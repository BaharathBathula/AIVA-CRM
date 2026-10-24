"""SMS (Twilio) and WhatsApp messages: phone numbers, matching messages to CRM
records, opt-outs, sending and the inbox queries the Messages page uses.

The provider modules (``providers/twilio_sms.py`` and ``providers/whatsapp.py``)
talk to the provider; everything channel-neutral lives here.

* Numbers are stored in E.164 (``+12145550100``). A conversation is every
  message to or from one number, across both channels.
* Opt-outs ("STOP") go on the workspace's do-not-contact list as
  ``kind="phone"`` entries, so Data management shows them next to blocked
  emails, and nothing is sent to that number on either channel.
* Messages linked to a contact, lead or account also land on the record's
  activity timeline.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.governance import SuppressionEntry
from app.models.integration_hub import ChannelMessage, IntegrationConnection
from app.models.lead import Lead
from app.models.platform import Notification

logger = logging.getLogger("aiva.integrations.messaging")

CHANNELS: dict[str, str] = {"sms": "twilio_sms", "whatsapp": "whatsapp"}
CHANNEL_LABEL = {"sms": "SMS", "whatsapp": "WhatsApp"}
RECORD_TYPES = ("contact", "lead", "account")

# Carrier-standard keywords (Twilio's defaults); compared after removing punctuation.
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"}
START_WORDS = {"START", "UNSTOP", "YES"}
HELP_WORDS = {"HELP", "INFO"}

STATUS_RANK = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}
WHATSAPP_WINDOW = timedelta(hours=24)
MAX_BODY = {"sms": 1600, "whatsapp": 4096}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# HTTP (one place, so tests can stand in for the providers)
# ---------------------------------------------------------------------------


async def http(method: str, url: str, *, timeout: float = 25, **kwargs: Any) -> httpx.Response:
    """Call a provider API. Network failures become a friendly IntegrationError."""

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        host = (urlsplit(url).hostname or "").lower()
        vendor = "Twilio" if host.endswith("twilio.com") else "WhatsApp (Meta)" if host.endswith(("facebook.com", "fbcdn.net", "fbsbx.com")) else "the provider"
        logger.warning("Network error calling %s: %r", host, exc)
        raise IntegrationError(f"Couldn't reach {vendor}. Check the server's internet access and try again in a moment.", 502) from exc


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

_EXTENSION = re.compile(r"(?i)\s*(?:ext\.?|extension|x|#)\s*\d{1,6}\s*$")


def normalize_phone(raw: str | None, default_country_code: str | None = "1") -> str | None:
    """E.164 (``+12145550100``) or None when the number can't be made sense of.

    Numbers without a country code get the workspace's default one; a leading
    trunk ``0`` (``07700 900123``) is dropped first.
    """

    if not raw:
        return None
    text = str(raw).strip()
    for prefix in ("whatsapp:", "tel:", "sms:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = _EXTENSION.sub("", text)
    international = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        international = True
        digits = digits[2:]
    if not digits:
        return None
    if not international:
        cc = re.sub(r"\D", "", default_country_code or "")
        if not cc:
            return None
        if cc == "1":
            if len(digits) == 10:
                digits = "1" + digits
            elif not (len(digits) == 11 and digits.startswith("1")):
                return None
        elif digits.startswith("0"):
            digits = cc + digits.lstrip("0")
        elif not (digits.startswith(cc) and len(digits) >= 10):
            digits = cc + digits
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        return None
    return f"+{digits}"


def digits_of(number: str | None) -> str:
    return re.sub(r"\D", "", number or "")


def pretty(number: str | None) -> str:
    """+12145550100 → +1 214 555 0100 (NANP); other numbers are left as they are."""

    if not number:
        return ""
    d = digits_of(number)
    if len(d) == 11 and d.startswith("1"):
        return f"+1 {d[1:4]} {d[4:7]} {d[7:]}"
    return number


def keyword(body: str | None) -> str | None:
    word = re.sub(r"[^A-Za-z]", "", (body or "").strip()).upper()
    if word in STOP_WORDS:
        return "stop"
    if word in START_WORDS:
        return "start"
    if word in HELP_WORDS:
        return "help"
    return None


async def default_country_code(db: AsyncSession, organization_id: uuid.UUID, channel: str | None = None) -> str:
    keys = [CHANNELS[channel]] if channel in CHANNELS else []
    keys += [k for k in CHANNELS.values() if k not in keys]
    for key in keys:
        value = (await core.workspace_config(db, organization_id, key)).get("default_country_code")
        if value:
            return str(value)
    return "1"


def counterpart():
    """The other party's number for a message (whoever isn't the business)."""

    return case((ChannelMessage.direction == "outbound", ChannelMessage.to_number), else_=ChannelMessage.from_number)


# ---------------------------------------------------------------------------
# Matching numbers to records
# ---------------------------------------------------------------------------


def _norm(column):
    return func.regexp_replace(func.coalesce(column, ""), r"\D", "", "g")


def _score(stored: str, wanted: str) -> int:
    if not stored or len(stored) < 7:
        return 0
    if stored == wanted:
        return 3
    if wanted.endswith(stored):  # stored without the country code
        return 2
    if stored.endswith(wanted):
        return 1
    return 0


async def match_number(db: AsyncSession, organization_id: uuid.UUID, number: str) -> dict[str, uuid.UUID | None]:
    """The contact (and its account), else the lead, else the account with this number."""

    wanted = digits_of(number)
    if len(wanted) < 7:
        return {}
    tail = wanted[-7:]
    contacts = (
        await db.execute(
            select(Contact.id, Contact.account_id, Contact.is_archived, _norm(Contact.phone), _norm(Contact.mobile))
            .where(Contact.organization_id == organization_id, or_(func.right(_norm(Contact.phone), 7) == tail, func.right(_norm(Contact.mobile), 7) == tail))
            .limit(25)
        )
    ).all()
    best = max(
        ((max(_score(p, wanted), _score(m, wanted)) - (1 if archived else 0) * 0.5, cid, aid) for cid, aid, archived, p, m in contacts),
        default=None,
        key=lambda row: row[0],
    )
    if best and best[0] > 0:
        return {"contact_id": best[1], "account_id": best[2]}
    leads = (
        await db.execute(
            select(Lead.id, Lead.converted_at, _norm(Lead.phone))
            .where(Lead.organization_id == organization_id, func.right(_norm(Lead.phone), 7) == tail)
            .limit(25)
        )
    ).all()
    best_lead = max(((_score(p, wanted) - (0.5 if converted else 0), lid) for lid, converted, p in leads), default=None, key=lambda row: row[0])
    if best_lead and best_lead[0] > 0:
        return {"lead_id": best_lead[1]}
    accounts = (
        await db.execute(
            select(Account.id, _norm(Account.phone)).where(Account.organization_id == organization_id, func.right(_norm(Account.phone), 7) == tail).limit(25)
        )
    ).all()
    best_account = max(((_score(p, wanted), aid) for aid, p in accounts), default=None, key=lambda row: row[0])
    if best_account and best_account[0] > 0:
        return {"account_id": best_account[1]}
    return {}


async def conversation_links(db: AsyncSession, organization_id: uuid.UUID, number: str) -> dict[str, uuid.UUID | None]:
    """The record this conversation is linked to (someone may have linked it by hand)."""

    row = (
        await db.execute(
            select(ChannelMessage.contact_id, ChannelMessage.lead_id, ChannelMessage.account_id)
            .where(
                ChannelMessage.organization_id == organization_id,
                counterpart() == number,
                or_(ChannelMessage.contact_id.is_not(None), ChannelMessage.lead_id.is_not(None), ChannelMessage.account_id.is_not(None)),
            )
            .order_by(ChannelMessage.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return {}
    return {k: v for k, v in zip(("contact_id", "lead_id", "account_id"), row, strict=True) if v}


async def links_for(db: AsyncSession, organization_id: uuid.UUID, number: str) -> dict[str, uuid.UUID | None]:
    return await conversation_links(db, organization_id, number) or await match_number(db, organization_id, number)


# ---------------------------------------------------------------------------
# Opt-outs (the do-not-contact list)
# ---------------------------------------------------------------------------


async def opt_out_entry(db: AsyncSession, organization_id: uuid.UUID, number: str) -> SuppressionEntry | None:
    return (
        await db.execute(
            select(SuppressionEntry).where(SuppressionEntry.organization_id == organization_id, SuppressionEntry.kind == "phone", SuppressionEntry.value == number)
        )
    ).scalar_one_or_none()


async def opt_outs(db: AsyncSession, organization_id: uuid.UUID, numbers: list[str]) -> dict[str, SuppressionEntry]:
    if not numbers:
        return {}
    rows = (
        await db.execute(
            select(SuppressionEntry).where(
                SuppressionEntry.organization_id == organization_id, SuppressionEntry.kind == "phone", SuppressionEntry.value.in_(numbers)
            )
        )
    ).scalars()
    return {row.value: row for row in rows}


async def set_opted_out(
    db: AsyncSession,
    organization_id: uuid.UUID,
    number: str,
    *,
    reason: str = "unsubscribed",
    note: str | None = None,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Add the number to the do-not-contact list. Returns True when it wasn't there."""

    if await opt_out_entry(db, organization_id, number):
        return False
    db.add(SuppressionEntry(id=uuid.uuid4(), organization_id=organization_id, kind="phone", value=number, reason=reason, note=(note or "")[:300] or None, created_by_user_id=user_id))
    await db.flush()
    return True


async def clear_opt_out(db: AsyncSession, organization_id: uuid.UUID, number: str, *, only_reasons: tuple[str, ...] | None = ("unsubscribed",)) -> bool:
    """Remove a phone opt-out. By default only ones the person made themselves (STOP)."""

    entry = await opt_out_entry(db, organization_id, number)
    if entry is None:
        return False
    if entry.reason == "removal_request":
        return False
    if only_reasons is not None and entry.reason not in only_reasons:
        return False
    await db.delete(entry)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# Recording messages
# ---------------------------------------------------------------------------


async def find_by_external(db: AsyncSession, organization_id: uuid.UUID | None, channel: str, external_id: str) -> ChannelMessage | None:
    query = select(ChannelMessage).where(ChannelMessage.channel == channel, ChannelMessage.external_id == external_id)
    if organization_id is not None:
        query = query.where(ChannelMessage.organization_id == organization_id)
    return (await db.execute(query.limit(1))).scalars().first()


def _activity(message: ChannelMessage, subject: str) -> Activity | None:
    if not (message.contact_id or message.lead_id or message.account_id):
        return None
    return Activity(
        id=uuid.uuid4(),
        organization_id=message.organization_id,
        contact_id=message.contact_id,
        lead_id=message.lead_id,
        account_id=message.account_id,
        created_by_user_id=message.user_id,
        activity_type="sms",
        subject=subject[:300],
        body=message.body or None,
        direction=message.direction,
        occurred_at=utcnow(),
        external_id=(message.external_id or "")[:500] or None,
        activity_metadata={"channel": message.channel, "channel_message_id": str(message.id), "number": message.to_number if message.direction == "outbound" else message.from_number},
    )


async def _owner_for(db: AsyncSession, message: ChannelMessage, number: str) -> uuid.UUID | None:
    for model, record_id in ((Contact, message.contact_id), (Lead, message.lead_id), (Account, message.account_id)):
        if record_id:
            owner = (await db.execute(select(model.owner_user_id).where(model.id == record_id))).scalar_one_or_none()
            if owner:
                return owner
    return (
        await db.execute(
            select(ChannelMessage.user_id)
            .where(
                ChannelMessage.organization_id == message.organization_id,
                ChannelMessage.direction == "outbound",
                ChannelMessage.to_number == number,
                ChannelMessage.user_id.is_not(None),
            )
            .order_by(ChannelMessage.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _display_name(db: AsyncSession, message: ChannelMessage) -> str | None:
    if message.contact_id:
        row = (await db.execute(select(Contact.first_name, Contact.last_name).where(Contact.id == message.contact_id))).first()
        if row:
            return " ".join(p for p in row if p) or None
    if message.lead_id:
        row = (await db.execute(select(Lead.first_name, Lead.last_name).where(Lead.id == message.lead_id))).first()
        if row:
            return " ".join(p for p in row if p) or None
    if message.account_id:
        return (await db.execute(select(Account.name).where(Account.id == message.account_id))).scalar_one_or_none()
    return None


async def record_inbound(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    channel: str,
    from_number: str,
    to_number: str | None,
    body: str,
    media: list[dict[str, Any]] | None = None,
    external_id: str | None = None,
    profile_name: str | None = None,
    sent_at: datetime | None = None,
) -> ChannelMessage | None:
    """Save a received message once (providers retry), link it and tell the owner."""

    if external_id and await find_by_external(db, organization_id, channel, external_id):
        return None
    links = await links_for(db, organization_id, from_number)
    message = ChannelMessage(
        id=uuid.uuid4(),
        organization_id=organization_id,
        channel=channel,
        direction="inbound",
        from_number=from_number,
        to_number=to_number,
        body=body or "",
        media=media or [],
        status="received",
        external_id=external_id,
        is_read=False,
        contact_id=links.get("contact_id"),
        lead_id=links.get("lead_id"),
        account_id=links.get("account_id"),
    )
    if sent_at is not None:
        message.created_at = sent_at
    db.add(message)
    await db.flush()
    name = await _display_name(db, message) or profile_name or pretty(from_number)
    activity = _activity(message, f"{CHANNEL_LABEL[channel]} from {name}")
    if activity is not None:
        db.add(activity)
    settings = await core.workspace_config(db, organization_id, CHANNELS[channel])
    if settings.get("notify_owner", True):
        recipient = await _owner_for(db, message, from_number)
        if recipient:
            preview = (body or ("[attachment]" if media else "")).strip()
            db.add(
                Notification(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    user_id=recipient,
                    kind="message",
                    title=f"New {CHANNEL_LABEL[channel]} message from {name}"[:200],
                    body=preview[:500] or None,
                    link=f"/integrations/messages?number={from_number.replace('+', '%2B')}",
                    is_read=False,
                )
            )
    return message


async def apply_status(
    db: AsyncSession,
    organization_id: uuid.UUID,
    channel: str,
    external_id: str,
    status: str,
    error: str | None = None,
) -> ChannelMessage | None:
    """Move a sent message forward (queued → sent → delivered → read) or mark it failed."""

    message = await find_by_external(db, organization_id, channel, external_id)
    if message is None or message.direction != "outbound":
        return None
    current = message.status
    if status == "failed":
        if current in {"delivered", "read"}:
            return message
        message.status = "failed"
        message.error = (error or message.error or "The provider couldn't deliver this message.")[:2000]
        return message
    if status not in STATUS_RANK or (current == "failed" and status == "queued"):
        return message  # unknown, or a late "queued" arriving after the failure
    if STATUS_RANK[status] > STATUS_RANK.get(current, -1):
        message.status = status
        message.error = None
    return message


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


async def available_channels(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, IntegrationConnection]:
    """Channels that are on and connected, by channel ("sms", "whatsapp")."""

    found: dict[str, IntegrationConnection] = {}
    for channel, key in CHANNELS.items():
        if not await core.is_enabled(db, organization_id, key):
            continue
        connection = await core.connection_for(db, organization_id, key)
        if connection is not None and connection.status in {"connected", "error"}:
            found[channel] = connection
    return found


async def last_inbound_at(db: AsyncSession, organization_id: uuid.UUID, channel: str, number: str) -> datetime | None:
    return (
        await db.execute(
            select(func.max(ChannelMessage.created_at)).where(
                ChannelMessage.organization_id == organization_id,
                ChannelMessage.channel == channel,
                ChannelMessage.direction == "inbound",
                ChannelMessage.from_number == number,
            )
        )
    ).scalar_one_or_none()


async def send(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    channel: str,
    to: str,
    body: str | None = None,
    template: dict[str, Any] | None = None,
    links: dict[str, uuid.UUID | None] | None = None,
) -> ChannelMessage:
    """Send an SMS or WhatsApp message and keep it. Provider refusals are saved on the
    message (status ``failed`` with the reason) rather than raised, so the person sees
    what happened in the conversation; problems with the request itself raise."""

    from app.integrations.providers import twilio_sms, whatsapp

    if channel not in CHANNELS:
        raise IntegrationError("Choose SMS or WhatsApp.", 422)
    key = CHANNELS[channel]
    connection = await core.active_connection(db, organization_id, key)
    cc = await default_country_code(db, organization_id, channel)
    number = normalize_phone(to, cc)
    if number is None:
        raise IntegrationError("That phone number doesn't look right. Include the country code, like +44 7700 900123.", 422)
    blocked = await opt_out_entry(db, organization_id, number)
    if blocked is not None:
        why = "replied STOP" if blocked.reason == "unsubscribed" else "is on the do-not-contact list"
        raise IntegrationError(f"{pretty(number)} {why}, so AIVA won't message it. They can text START to opt back in.", 409)
    text = (body or "").strip()
    if template is None and not text:
        raise IntegrationError("Write a message first.", 422)
    if len(text) > MAX_BODY[channel]:
        raise IntegrationError(f"That message is too long for {CHANNEL_LABEL[channel]} ({len(text)} of {MAX_BODY[channel]} characters).", 422)
    if channel == "sms" and template is not None:
        raise IntegrationError("Templates are for WhatsApp; SMS sends plain text.", 422)
    if channel == "whatsapp" and template is not None:
        whatsapp.build_template(connection, template)  # a missing value or unapproved template is the sender's to fix
    if channel == "whatsapp" and template is None:
        last = await last_inbound_at(db, organization_id, "whatsapp", number)
        if last is None or utcnow() - last > WHATSAPP_WINDOW:
            raise IntegrationError(
                "WhatsApp only allows free-form messages within 24 hours of the customer's last message. Send an approved template instead.", 409
            )
    if links is None:
        links = await links_for(db, organization_id, number)
    message = ChannelMessage(
        id=uuid.uuid4(),
        organization_id=organization_id,
        channel=channel,
        direction="outbound",
        from_number=None,
        to_number=number,
        body=text,
        media=[],
        status="queued",
        user_id=user_id,
        is_read=True,
        contact_id=links.get("contact_id"),
        lead_id=links.get("lead_id"),
        account_id=links.get("account_id"),
    )
    db.add(message)
    await db.flush()
    provider = whatsapp if channel == "whatsapp" else twilio_sms
    try:
        await provider.send(db, connection, message, template)
    except IntegrationError as exc:
        message.status = "failed"
        message.error = str(exc)[:2000]
        core.log(db, organization_id, key, "send_failed", f"Message to {pretty(number)} failed: {exc}", level="error", connection=connection, user_id=user_id)
        if getattr(exc, "opted_out", False):
            await set_opted_out(db, organization_id, number, reason="unsubscribed", note=f"The carrier reports this number opted out ({CHANNEL_LABEL[channel]}).")
        return message
    connection.last_used_at = utcnow()
    if connection.status == "error":
        connection.status = "connected"
        connection.last_error = None
    activity = _activity(message, f"{CHANNEL_LABEL[channel]} to {await _display_name(db, message) or pretty(number)}")
    if activity is not None:
        db.add(activity)
    return message


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def message_out(message: ChannelMessage, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "channel": message.channel,
        "direction": message.direction,
        "number": message.to_number if message.direction == "outbound" else message.from_number,
        "from_number": message.from_number,
        "to_number": message.to_number,
        "body": message.body,
        "media": [
            {k: v for k, v in item.items() if k in {"type", "mime_type", "caption", "filename", "name", "latitude", "longitude", "address"}} | {"index": index}
            for index, item in enumerate(message.media or [])
        ],
        "template_name": message.template_name,
        "status": message.status,
        "error": message.error,
        "is_read": message.is_read,
        "user_id": str(message.user_id) if message.user_id else None,
        "user_name": (names or {}).get(str(message.user_id)) if message.user_id else None,
        "contact_id": str(message.contact_id) if message.contact_id else None,
        "lead_id": str(message.lead_id) if message.lead_id else None,
        "account_id": str(message.account_id) if message.account_id else None,
        "created_at": message.created_at,
    }


def link_of(row: Any) -> tuple[str, uuid.UUID] | None:
    """The record a message (or a dict of ``*_id`` links) belongs to: contact, else lead, else account."""

    for record_type, attr in (("contact", "contact_id"), ("lead", "lead_id"), ("account", "account_id")):
        value = row.get(attr) if isinstance(row, dict) else getattr(row, attr, None)
        if value:
            return record_type, value
    return None


async def visible_ids(db: AsyncSession, contact_ids: set, lead_ids: set, account_ids: set) -> set[uuid.UUID]:
    """Which of these records the person may see (record visibility applies to these queries)."""

    seen: set[uuid.UUID] = set()
    for model, ids in ((Contact, contact_ids), (Lead, lead_ids), (Account, account_ids)):
        if ids:
            seen |= set((await db.execute(select(model.id).where(model.id.in_(list(ids))))).scalars())
    return seen


async def stats(db: AsyncSession, organization_id: uuid.UUID, channel: str, days: int = 7) -> dict[str, int]:
    since = utcnow() - timedelta(days=days)
    rows = (
        await db.execute(
            select(ChannelMessage.direction, ChannelMessage.status, func.count())
            .where(ChannelMessage.organization_id == organization_id, ChannelMessage.channel == channel, ChannelMessage.created_at >= since)
            .group_by(ChannelMessage.direction, ChannelMessage.status)
        )
    ).all()
    out = {"sent": 0, "received": 0, "failed": 0, "delivered": 0}
    for direction, status, count in rows:
        if direction == "inbound":
            out["received"] += count
        else:
            out["sent"] += count
            if status == "failed":
                out["failed"] += count
            if status in {"delivered", "read"}:
                out["delivered"] += count
    return out


async def mark_read(db: AsyncSession, organization_id: uuid.UUID, number: str) -> int:
    result = await db.execute(
        update(ChannelMessage)
        .where(
            ChannelMessage.organization_id == organization_id,
            ChannelMessage.direction == "inbound",
            ChannelMessage.from_number == number,
            ChannelMessage.is_read.is_(False),
        )
        .values(is_read=True)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def unread_count(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).where(
                    ChannelMessage.organization_id == organization_id, ChannelMessage.direction == "inbound", ChannelMessage.is_read.is_(False)
                )
            )
        ).scalar_one()
    )


def unread_filter():
    return and_(ChannelMessage.direction == "inbound", ChannelMessage.is_read.is_(False))
