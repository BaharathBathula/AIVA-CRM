"""Integration endpoints: messaging.

* ``/messaging/...``: the SMS + WhatsApp inbox (conversations, threads, record
  messages, sending, opt-outs, attachments), WhatsApp templates and webhook
  set-up, Twilio numbers.
* ``/messaging/slack/...``: channel picker and sharing a record to Slack.

Inbound webhooks arrive through the hub's ``/public/integrations/{key}/webhook/{token}``
and are handled by the provider modules (``app/integrations/providers``).
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core, messaging
from app.integrations.core import IntegrationError
from app.integrations.providers import slack, twilio_sms, whatsapp
from app.models.account import Account
from app.models.contact import Contact
from app.models.integration_hub import ChannelMessage
from app.models.lead import Lead
from app.services import records
from app.services.automation import record_audit
from app.services.catalog import OBJECTS
from app.services.lookups import names_for

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

PROVIDERS = {"sms": twilio_sms, "whatsapp": whatsapp}
INLINE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "audio/ogg", "audio/mpeg", "audio/aac", "audio/amr", "audio/mp4", "audio/wav",
    "video/mp4", "video/3gpp", "video/webm",
}
RECORD_MODELS = {"contact": Contact, "lead": Lead, "account": Account}


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def _number(raw: str, cc: str) -> str:
    number = messaging.normalize_phone(raw, cc)
    if number is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That phone number doesn't look right. Include the country code.")
    return number


async def _record(db: AsyncSession, organization_id: uuid.UUID, record_type: str, record_id: uuid.UUID) -> Any:
    model = RECORD_MODELS.get(record_type)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Messages can be linked to contacts, leads and accounts.")
    # A select (not db.get) so record visibility rules apply.
    found = (await db.execute(select(model).where(model.id == record_id, model.organization_id == organization_id))).scalar_one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{record_type.title()} not found.")
    return found


def _links(record_type: str, record: Any) -> dict[str, uuid.UUID | None]:
    if record_type == "contact":
        return {"contact_id": record.id, "account_id": record.account_id}
    if record_type == "lead":
        return {"lead_id": record.id}
    return {"account_id": record.id}


async def _record_refs(db: AsyncSession, organization_id: uuid.UUID, rows: list[Any]) -> dict[tuple[str, str], dict[str, str]]:
    pairs = [link for link in (messaging.link_of(row) for row in rows) if link]
    return await records.resolve_many(db, organization_id, [(t, i) for t, i in pairs]) if pairs else {}


async def _channels_out(db: AsyncSession, organization_id: uuid.UUID) -> list[dict[str, Any]]:
    available = await messaging.available_channels(db, organization_id)
    out = []
    for channel, key in messaging.CHANNELS.items():
        connection = available.get(channel)
        item: dict[str, Any] = {
            "channel": channel,
            "key": key,
            "label": messaging.CHANNEL_LABEL[channel],
            "ready": connection is not None,
            "from": None,
        }
        if connection is not None:
            config = connection.config or {}
            item["from"] = config.get("business_number") if channel == "whatsapp" else config.get("from_number") or config.get("messaging_service_name")
            if channel == "whatsapp":
                item["templates"] = [
                    {k: t.get(k) for k in ("name", "language", "category", "body", "body_params", "header", "parameter_format", "footer")}
                    for t in config.get("templates") or []
                    if t.get("status") == "APPROVED"
                ]
        out.append(item)
    return out


async def _visible_numbers(db: AsyncSession, rows: list[Any]) -> set[str]:
    """Conversation numbers the person may see (those linked to records they can't see are hidden)."""

    contacts = {r.contact_id for r in rows if r.contact_id}
    leads = {r.lead_id for r in rows if r.lead_id}
    accounts = {r.account_id for r in rows if r.account_id}
    seen = await messaging.visible_ids(db, contacts, leads, accounts)
    allowed = set()
    for row in rows:
        link = messaging.link_of(row)
        if link is None or link[1] in seen:
            allowed.add(row.number)
    return allowed


# ===========================================================================
# Inbox
# ===========================================================================

messaging_read = [Depends(require_feature("messaging"))]


@router.get("/messaging/channels", dependencies=messaging_read)
async def channel_options(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Which channels can send right now, with WhatsApp's approved templates."""

    org = principal.organization_id
    return {
        "channels": await _channels_out(db, org),
        "default_country_code": await messaging.default_country_code(db, org),
        "can_send": principal.can("records.write"),
        "can_manage": principal.can("admin.settings"),
    }


@router.get("/messaging/conversations", dependencies=messaging_read)
async def conversations(
    search: str | None = Query(default=None, max_length=120),
    channel: Literal["sms", "whatsapp"] | None = None,
    unread: bool = False,
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    number = messaging.counterpart().label("number")
    filters = [ChannelMessage.organization_id == org]
    if channel:
        filters.append(ChannelMessage.channel == channel)
    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        conditions = [ChannelMessage.body.ilike(like)]
        digits = messaging.digits_of(term)
        if len(digits) >= 3:
            conditions.append(messaging.counterpart().like(f"%{digits}%"))
        name = func.concat_ws(" ", Contact.first_name, Contact.last_name)
        contact_ids = select(Contact.id).where(Contact.organization_id == org, name.ilike(like)).limit(200)
        lead_ids = select(Lead.id).where(Lead.organization_id == org, func.concat_ws(" ", Lead.first_name, Lead.last_name).ilike(like)).limit(200)
        account_ids = select(Account.id).where(Account.organization_id == org, Account.name.ilike(like)).limit(200)
        conditions += [ChannelMessage.contact_id.in_(contact_ids), ChannelMessage.lead_id.in_(lead_ids), ChannelMessage.account_id.in_(account_ids)]
        filters.append(or_(*conditions))
    unread_count = func.count().filter(messaging.unread_filter())
    grouped = (
        select(
            number,
            func.max(ChannelMessage.created_at).label("last_at"),
            unread_count.label("unread"),
            func.count().label("total"),
            func.array_agg(distinct(ChannelMessage.channel)).label("channels"),
        )
        .where(*filters)
        .group_by(number)
    )
    if unread:
        grouped = grouped.having(unread_count > 0)
    groups = (await db.execute(grouped.order_by(func.max(ChannelMessage.created_at).desc()).limit(limit + 1).offset(offset))).all()
    more = len(groups) > limit
    groups = groups[:limit]
    numbers = [g.number for g in groups if g.number]
    latest: dict[str, ChannelMessage] = {}
    linked: dict[str, Any] = {}
    if numbers:
        key = messaging.counterpart()
        rows = (
            await db.execute(
                select(ChannelMessage, key.label("number"))
                .where(ChannelMessage.organization_id == org, key.in_(numbers))
                .ext(distinct_on(key))
                .order_by(key, ChannelMessage.created_at.desc())
            )
        ).all()
        latest = {n: m for m, n in rows}
        link_rows = (
            await db.execute(
                select(key.label("number"), ChannelMessage.contact_id, ChannelMessage.lead_id, ChannelMessage.account_id)
                .where(
                    ChannelMessage.organization_id == org,
                    key.in_(numbers),
                    or_(ChannelMessage.contact_id.is_not(None), ChannelMessage.lead_id.is_not(None), ChannelMessage.account_id.is_not(None)),
                )
                .ext(distinct_on(key))
                .order_by(key, ChannelMessage.created_at.desc())
            )
        ).all()
        linked = {r.number: r for r in link_rows}
    allowed = await _visible_numbers(db, list(linked.values()))
    allowed |= {n for n in numbers if n not in linked}
    refs = await _record_refs(db, org, list(linked.values()))
    blocked = await messaging.opt_outs(db, org, numbers)
    items = []
    for group in groups:
        if group.number not in allowed:
            continue
        last = latest.get(group.number)
        link = messaging.link_of(linked[group.number]) if group.number in linked else None
        record = refs.get((link[0], str(link[1]))) if link else None
        items.append(
            {
                "number": group.number,
                "pretty": messaging.pretty(group.number),
                "last_at": group.last_at,
                "unread": int(group.unread or 0),
                "total": int(group.total or 0),
                "channels": sorted(c for c in group.channels or [] if c),
                "last": messaging.message_out(last) if last else None,
                "record": record,
                "opted_out": group.number in blocked,
            }
        )
    return {"items": items, "more": more, "unread_total": await messaging.unread_count(db, org)}


async def _thread_rows(db: AsyncSession, organization_id: uuid.UUID, number: str, before: str | None, limit: int) -> list[ChannelMessage]:
    query = select(ChannelMessage).where(ChannelMessage.organization_id == organization_id, messaging.counterpart() == number)
    if before:
        try:
            pivot = (await db.execute(select(ChannelMessage.created_at).where(ChannelMessage.id == uuid.UUID(before), ChannelMessage.organization_id == organization_id))).scalar_one_or_none()
        except ValueError:
            pivot = None
        if pivot is not None:
            query = query.where(ChannelMessage.created_at < pivot)
    rows = (await db.execute(query.order_by(ChannelMessage.created_at.desc()).limit(limit))).scalars().all()
    return list(reversed(rows))


async def _can_see(db: AsyncSession, link: tuple[str, uuid.UUID]) -> bool:
    record_type, record_id = link
    ids = {record_id}
    seen = await messaging.visible_ids(db, ids if record_type == "contact" else set(), ids if record_type == "lead" else set(), ids if record_type == "account" else set())
    return record_id in seen


async def _thread_meta(db: AsyncSession, principal: Principal, number: str) -> dict[str, Any]:
    org = principal.organization_id
    record = None
    link = messaging.link_of(await messaging.conversation_links(db, org, number))
    if link:
        if not await _can_see(db, link):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
        record = (await records.resolve_many(db, org, [link])).get((link[0], str(link[1])))
    else:
        # Not linked yet: suggest the record with this number (the person can link it).
        suggestion = messaging.link_of(await messaging.match_number(db, org, number))
        if suggestion and await _can_see(db, suggestion):
            found = (await records.resolve_many(db, org, [suggestion])).get((suggestion[0], str(suggestion[1])))
            record = {**found, "suggested": True} if found else None
    blocked = await messaging.opt_out_entry(db, org, number)
    last_whatsapp = await messaging.last_inbound_at(db, org, "whatsapp", number)
    window_until = last_whatsapp + messaging.WHATSAPP_WINDOW if last_whatsapp else None
    return {
        "number": number,
        "pretty": messaging.pretty(number),
        "record": record,
        "opted_out": {"reason": blocked.reason, "note": blocked.note, "at": blocked.created_at} if blocked else None,
        "whatsapp_window_until": window_until if window_until and window_until > messaging.utcnow() else None,
    }


@router.get("/messaging/thread", dependencies=messaging_read)
async def thread(
    number: str = Query(min_length=3, max_length=40),
    before: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=60, ge=1, le=200),
    mark_read: bool = True,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    target = _number(number, await messaging.default_country_code(db, org))
    meta = await _thread_meta(db, principal, target)
    rows = await _thread_rows(db, org, target, before, limit)
    names = await names_for(db, org, "user", [r.user_id for r in rows])
    if mark_read and not principal.read_only and any(r.direction == "inbound" and not r.is_read for r in rows):
        await messaging.mark_read(db, org, target)
        await db.commit()
    return {
        **meta,
        "messages": [messaging.message_out(r, names) for r in rows],
        "more": len(rows) == limit,
        "channels": await _channels_out(db, org),
    }


class NumberPayload(BaseModel):
    number: str = Field(min_length=3, max_length=40)


@router.post("/messaging/thread/read", dependencies=messaging_read)
async def thread_read(payload: NumberPayload, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    target = _number(payload.number, await messaging.default_country_code(db, org))
    count = await messaging.mark_read(db, org, target)
    await db.commit()
    return {"marked": count, "unread_total": await messaging.unread_count(db, org)}


class LinkPayload(BaseModel):
    number: str = Field(min_length=3, max_length=40)
    record_type: Literal["contact", "lead", "account"] | None = None
    record_id: uuid.UUID | None = None


@router.post("/messaging/thread/link", dependencies=messaging_read)
async def thread_link(payload: LinkPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    """Link every message with this number to a record (or unlink them)."""

    org = principal.organization_id
    target = _number(payload.number, await messaging.default_country_code(db, org))
    links: dict[str, uuid.UUID | None] = {"contact_id": None, "lead_id": None, "account_id": None}
    if payload.record_type and payload.record_id:
        found = await _record(db, org, payload.record_type, payload.record_id)
        links.update(_links(payload.record_type, found))
    rows = (await db.execute(select(ChannelMessage).where(ChannelMessage.organization_id == org, messaging.counterpart() == target))).scalars().all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There are no messages with that number.")
    for row in rows:
        row.contact_id, row.lead_id, row.account_id = links["contact_id"], links["lead_id"], links["account_id"]
    await db.commit()
    return await _thread_meta(db, principal, target)


class OptOutPayload(BaseModel):
    number: str = Field(min_length=3, max_length=40)
    opted_out: bool
    note: str | None = Field(default=None, max_length=300)


@router.post("/messaging/opt-out", dependencies=messaging_read)
async def opt_out(payload: OptOutPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    """Stop (or allow again) messages to a number. Adding is open to anyone who can
    edit records; taking a number off needs the privacy permission."""

    org = principal.organization_id
    target = _number(payload.number, await messaging.default_country_code(db, org))
    if payload.opted_out:
        changed = await messaging.set_opted_out(db, org, target, reason="manual", note=payload.note or f"Marked opted out by {principal.full_name}.", user_id=principal.user_id)
        action = "phone_opted_out"
    else:
        if not principal.can("data.privacy"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only people who handle privacy requests can take a number off the do-not-contact list.")
        entry = await messaging.opt_out_entry(db, org, target)
        if entry is not None and entry.reason == "removal_request":
            raise HTTPException(status.HTTP_409_CONFLICT, "This number belongs to a completed removal request, so it stays blocked.")
        changed = await messaging.clear_opt_out(db, org, target, only_reasons=None)
        action = "phone_opted_in"
    if changed:
        record_audit(db, organization_id=org, action=action, entity_type="integration", entity_label=messaging.pretty(target))
    await db.commit()
    entry = await messaging.opt_out_entry(db, org, target)
    return {"number": target, "opted_out": {"reason": entry.reason, "note": entry.note, "at": entry.created_at} if entry else None}


@router.get("/messaging/records/{record_type}/{record_id}", dependencies=messaging_read)
async def record_messages(
    record_type: Literal["contact", "lead", "account"],
    record_id: uuid.UUID,
    limit: int = Query(default=80, ge=1, le=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    """Messages with a contact, lead or account, and the numbers you can message."""

    org = principal.organization_id
    record = await _record(db, org, record_type, record_id)
    cc = await messaging.default_country_code(db, org)
    fields = [("mobile", "Mobile"), ("phone", "Phone")] if record_type == "contact" else [("phone", "Phone")]
    numbers: list[dict[str, Any]] = []
    for attr, label in fields:
        normalized = messaging.normalize_phone(getattr(record, attr, None), cc)
        if normalized and normalized not in {n["number"] for n in numbers}:
            numbers.append({"number": normalized, "pretty": messaging.pretty(normalized), "label": label})
    column = {"contact": ChannelMessage.contact_id, "lead": ChannelMessage.lead_id, "account": ChannelMessage.account_id}[record_type]
    condition = column == record_id
    if numbers:
        condition = or_(condition, and_(messaging.counterpart().in_([n["number"] for n in numbers]), ChannelMessage.contact_id.is_(None), ChannelMessage.lead_id.is_(None)))
    rows = (
        await db.execute(select(ChannelMessage).where(ChannelMessage.organization_id == org, condition).order_by(ChannelMessage.created_at.desc()).limit(limit))
    ).scalars().all()
    rows = list(reversed(rows))
    for row in rows:
        number = row.to_number if row.direction == "outbound" else row.from_number
        if number and number not in {n["number"] for n in numbers}:
            numbers.append({"number": number, "pretty": messaging.pretty(number), "label": "Messaged"})
    blocked = await messaging.opt_outs(db, org, [n["number"] for n in numbers])
    for item in numbers:
        entry = blocked.get(item["number"])
        item["opted_out"] = {"reason": entry.reason, "at": entry.created_at} if entry else None
        last = await messaging.last_inbound_at(db, org, "whatsapp", item["number"])
        until = last + messaging.WHATSAPP_WINDOW if last else None
        item["whatsapp_window_until"] = until if until and until > messaging.utcnow() else None
    unread = [r for r in rows if r.direction == "inbound" and not r.is_read]
    names = await names_for(db, org, "user", [r.user_id for r in rows])
    return {
        "numbers": numbers,
        "messages": [messaging.message_out(r, names) for r in rows],
        "unread": len(unread),
        "channels": await _channels_out(db, org),
        "can_send": principal.can("records.write"),
    }


class TemplatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    language: str = Field(min_length=2, max_length=20)
    params: dict[str, Any] = Field(default_factory=dict)


class SendPayload(BaseModel):
    channel: Literal["sms", "whatsapp"]
    to: str = Field(min_length=3, max_length=40)
    body: str | None = Field(default=None, max_length=4096)
    template: TemplatePayload | None = None
    record_type: Literal["contact", "lead", "account"] | None = None
    record_id: uuid.UUID | None = None


@router.post("/messaging/send", dependencies=messaging_read)
async def send_message(payload: SendPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    """Send a text or WhatsApp message. A message the provider refuses comes back
    with ``status: failed`` and the reason, and stays in the conversation."""

    org = principal.organization_id
    if principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support access can't send messages to customers.")
    links = None
    if payload.record_type and payload.record_id:
        found = await _record(db, org, payload.record_type, payload.record_id)
        links = _links(payload.record_type, found)
    try:
        message = await messaging.send(
            db,
            organization_id=org,
            user_id=principal.user_id,
            channel=payload.channel,
            to=payload.to,
            body=payload.body,
            template=payload.template.model_dump() if payload.template else None,
            links=links,
        )
    except IntegrationError as exc:
        raise _error(exc) from exc
    await db.commit()
    names = {str(principal.user_id): principal.full_name}
    out = messaging.message_out(message, names)
    return {"ok": message.status != "failed", "message": out, "error": message.error if message.status == "failed" else None}


@router.post("/messaging/messages/{message_id}/retry", dependencies=messaging_read)
async def retry(message_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    original = (await db.execute(select(ChannelMessage).where(ChannelMessage.id == message_id, ChannelMessage.organization_id == org))).scalar_one_or_none()
    if original is None or original.direction != "outbound" or original.status != "failed":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Only failed messages you sent can be retried.")
    if original.template_name:
        raise HTTPException(status.HTTP_409_CONFLICT, "Send the template again from the composer.")
    links = {"contact_id": original.contact_id, "lead_id": original.lead_id, "account_id": original.account_id}
    try:
        message = await messaging.send(db, organization_id=org, user_id=principal.user_id, channel=original.channel, to=original.to_number or "", body=original.body, links=links)
    except IntegrationError as exc:
        raise _error(exc) from exc
    await db.commit()
    return {"ok": message.status != "failed", "message": messaging.message_out(message, {str(principal.user_id): principal.full_name}), "error": message.error}


@router.get("/messaging/messages/{message_id}/media/{index}", dependencies=messaging_read)
async def media(message_id: uuid.UUID, index: int, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """An attachment from a received message, fetched from the provider with the workspace's credentials."""

    org = principal.organization_id
    row = (await db.execute(select(ChannelMessage).where(ChannelMessage.id == message_id, ChannelMessage.organization_id == org))).scalar_one_or_none()
    if row is None or not 0 <= index < len(row.media or []):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found.")
    link = messaging.link_of(row)
    if link and not await _can_see(db, link):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found.")
    item = row.media[index]
    if item.get("type") == "location":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Locations have no file.")
    try:
        connection = await core.active_connection(db, org, messaging.CHANNELS[row.channel])
        content, content_type = await PROVIDERS[row.channel].fetch_media(db, connection, item)
    except IntegrationError as exc:
        raise _error(exc) from exc
    # Only plain pictures, audio and video open in the browser (never SVG or HTML,
    # which could run script on AIVA's origin); everything else downloads.
    base_type = content_type.split(";")[0].strip().lower()
    inline = base_type in INLINE_TYPES
    filename = re.sub(r'[^\w. -]', "_", item.get("filename") or f"attachment-{index + 1}")[:120]
    return Response(
        content,
        media_type=base_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/messaging/unread", dependencies=messaging_read)
async def unread(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return {"unread": await messaging.unread_count(db, principal.organization_id)}


# ===========================================================================
# WhatsApp and Twilio set-up (admins)
# ===========================================================================


@router.post("/messaging/whatsapp/templates/sync", dependencies=messaging_read)
async def whatsapp_sync(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    try:
        connection = await core.active_connection(db, org, "whatsapp")
        count = await whatsapp.sync_templates(db, connection)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    core.log(db, org, "whatsapp", "templates_synced", f"Synced {count} message templates.", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"synced": count, "detail": await whatsapp.detail(db, "whatsapp", principal)}


@router.post("/messaging/whatsapp/webhook-setup", dependencies=messaging_read)
async def whatsapp_webhook(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Ask Meta to send this business account's webhooks to AIVA (Meta checks the URL first)."""

    org = principal.organization_id
    try:
        connection = await core.active_connection(db, org, "whatsapp")
        await whatsapp.use_webhook_override(db, connection)
    except IntegrationError as exc:
        core.log(db, org, "whatsapp", "webhook_setup_failed", str(exc), level="error", user_id=principal.user_id)
        await db.commit()
        raise _error(exc) from exc
    core.log(db, org, "whatsapp", "webhook_set", "Meta now sends this business account's webhooks to AIVA.", level="success", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"ok": True}


@router.get("/messaging/twilio/numbers", dependencies=messaging_read)
async def twilio_numbers(refresh: bool = False, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    try:
        connection = await core.active_connection(db, principal.organization_id, "twilio_sms")
        return {"items": await twilio_sms.list_numbers(db, connection, refresh=refresh)}
    except IntegrationError as exc:
        raise _error(exc) from exc


@router.post("/messaging/twilio/numbers/{number_sid}/use-aiva", dependencies=messaging_read)
async def twilio_point(number_sid: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Set the number's "A message comes in" webhook to AIVA."""

    org = principal.organization_id
    try:
        connection = await core.active_connection(db, org, "twilio_sms")
        row = await twilio_sms.point_number_here(db, connection, number_sid)
    except IntegrationError as exc:
        raise _error(exc) from exc
    core.log(db, org, "twilio_sms", "number_configured", f"{messaging.pretty(row.get('phone_number'))} now sends incoming texts to AIVA.", level="success", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"items": await twilio_sms.list_numbers(db, connection, refresh=True)}


# ===========================================================================
# Slack
# ===========================================================================

slack_feature = [Depends(require_feature("chat_integrations"))]


@router.get("/messaging/slack/channels", dependencies=slack_feature)
async def slack_channels(
    refresh: bool = False,
    include_channels: bool = True,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    """Channels AIVA can post to (for the channel picker and "Share to Slack").
    ``include_channels=false`` answers only whether sharing is possible (no Slack call)."""

    org = principal.organization_id
    connection = await core.connection_for(db, org, "slack")
    toggle = await core.toggle_row(db, org, "slack")
    config = dict(toggle.config or {}) if toggle else {}
    base = {
        "enabled": await core.is_enabled(db, org, "slack"),
        "mode": "bot"
        if slack.bot_token(connection)
        else "webhook"
        if (connection is not None and core.secret(connection, "webhook_url")) or slack.legacy_webhook(await slack.legacy_row(db, org))
        else None,
        "default_channel_id": config.get("default_channel_id"),
        "default_channel_name": config.get("default_channel_name"),
        "items": [],
        "ready": False,
    }
    if not base["enabled"]:
        return base
    base["ready"] = bool(base["mode"]) and (connection is None or connection.status != "needs_reauth")
    if base["mode"] == "bot" and connection is not None and include_channels:
        try:
            base["items"] = await slack.channels(db, connection, refresh=refresh and principal.can("admin.settings"))
        except IntegrationError as exc:
            await db.commit()
            raise _error(exc) from exc
    return base


class SharePayload(BaseModel):
    record_type: str = Field(min_length=2, max_length=40)
    record_id: uuid.UUID
    channel_id: str | None = Field(default=None, max_length=30)
    note: str | None = Field(default=None, max_length=1500)


def _share_fields(record_type: str, record: Any) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value not in (None, "", []):
            fields.append((label, str(value)[:200]))

    if record_type in {"contact", "lead"}:
        add("Email", getattr(record, "email", None))
        add("Phone", getattr(record, "phone", None) or getattr(record, "mobile", None))
        add("Title", getattr(record, "job_title", None))
        add("Company", getattr(record, "company_name", None))
        add("Status", getattr(record, "status", None))
    elif record_type == "account":
        add("Industry", getattr(record, "industry", None))
        add("Domain", getattr(record, "domain", None))
        add("Stage", getattr(record, "lifecycle_stage", None))
    elif record_type == "opportunity":
        amount = getattr(record, "amount", None)
        add("Amount", f"{float(amount):,.0f}" if amount is not None else None)
        add("Close date", getattr(record, "expected_close_date", None))
        add("Probability", f"{record.probability}%" if getattr(record, "probability", None) is not None else None)
    elif record_type == "case":
        add("Priority", getattr(record, "priority", None))
        add("Status", getattr(record, "status", None))
    elif record_type == "quote":
        total = getattr(record, "total", None)
        add("Total", f"{float(total):,.2f}" if total is not None else None)
        add("Status", getattr(record, "status", None))
    return fields


@router.post("/messaging/slack/share", dependencies=slack_feature)
async def slack_share(payload: SharePayload, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Post a record (with a note) to a Slack channel."""

    org = principal.organization_id
    entry = records.RECORD_TYPES.get(payload.record_type)
    if entry is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That kind of record can't be shared.")
    model, kind, label_of, link_of = entry
    record = (await db.execute(select(model).where(model.id == payload.record_id, model.organization_id == org))).scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} not found.")
    label = str(label_of(record))[:200]
    definition = OBJECTS.get(payload.record_type)
    try:
        result = await slack.share(
            db,
            org,
            channel_id=payload.channel_id,
            title=f"{kind}: {label}",
            text=(definition.singular if definition else kind) + f" shared by {principal.full_name}",
            link=link_of(record),
            fields=_share_fields(payload.record_type, record),
            note=payload.note,
            shared_by=principal.full_name,
        )
    except IntegrationError as exc:
        connection = await core.connection_for(db, org, "slack")
        core.log(db, org, "slack", "share_failed", f"Couldn't share {kind.lower()} “{label}”: {exc}", level="error", connection=connection, user_id=principal.user_id)
        await db.commit()
        raise _error(exc) from exc
    toggle = await core.toggle_row(db, org, "slack")
    config = dict(toggle.config or {}) if toggle else {}
    channel_id = result.get("channel_id")
    channel_name = (config.get("channel_names") or {}).get(channel_id or "") or (config.get("default_channel_name") if channel_id == config.get("default_channel_id") else None)
    connection = await core.connection_for(db, org, "slack")
    core.log(db, org, "slack", "shared", f"{principal.full_name} shared {kind.lower()} “{label}” to Slack.", level="success", connection=connection, user_id=principal.user_id,
             detail={"record_type": payload.record_type, "record_id": str(payload.record_id), "channel_id": result.get("channel_id")})
    await db.commit()
    return {"ok": True, "permalink": result.get("permalink"), "channel_id": result.get("channel_id"), "channel_name": channel_name}
