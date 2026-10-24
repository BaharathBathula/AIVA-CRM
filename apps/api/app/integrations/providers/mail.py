"""Gmail and Microsoft Outlook Mail: send and read the inbox through the provider API.

Connecting either integration (each person signs in with Google or Microsoft)
turns their AIVA mailbox (``MailboxConnection``) into an API mailbox: every
email AIVA sends for them (one-off emails, campaigns, sequence steps, meeting
invitations) goes out through the Gmail API or Microsoft Graph instead of SMTP
with an app password, and customer emails in the inbox are pulled in every few
minutes through the same pipeline as IMAP mailboxes (reply detection stops
sequences; mail from contacts lands on their timeline).

* Sending: Gmail ``users.messages.send`` with the raw RFC 822 message
  (base64url); Graph ``POST /me/sendMail`` with the MIME message (base64,
  ``Content-Type: text/plain``). Both keep AIVA's headers and attachments
  (unsubscribe headers, calendar invitations).
* Reading: Gmail ``users.history.list`` from the last history ID (falling back
  to ``messages.list`` for the first sync or when the history has expired), then
  ``messages.get?format=raw``; Graph ``mailFolders/inbox/messages/delta``.
* The switch: while the integration is off, nothing is sent or read through it
  (``mailer.sender_config`` falls back to the server's mail settings).
"""

from __future__ import annotations

import base64
import email.policy
import logging
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.editions import MailboxConnection
from app.models.email_message import EmailMessage as StoredEmail
from app.models.integration_hub import IntegrationConnection, IntegrationToggle
from app.models.user import User
from app.services import mailer

logger = logging.getLogger("aiva.integrations.mail")

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GRAPH = "https://graph.microsoft.com/v1.0/me"

KEYS = ("gmail", "outlook_mail")
TRANSPORT = {"gmail": "gmail_api", "outlook_mail": "graph"}
MAILBOX_PROVIDER = {"gmail": "gmail", "outlook_mail": "outlook"}
LABEL = {"gmail": "Gmail", "outlook_mail": "Outlook"}

SYNC_EVERY = timedelta(minutes=10)
FIRST_SYNC_DAYS = 3
MAX_MESSAGES = 100
MAX_PAGES = 5
JOB_BATCH = 10


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _key_for(mailbox: MailboxConnection) -> str:
    key = mailer.API_TRANSPORTS.get(mailbox.transport)
    if key is None:
        raise IntegrationError("This mailbox uses an app password, not the Gmail or Outlook integration.")
    return key


async def _link(db: AsyncSession, mailbox: MailboxConnection) -> IntegrationConnection:
    """The person's integration connection behind an API mailbox (checks the switch too)."""

    return await core.active_connection(db, mailbox.organization_id, _key_for(mailbox), mailbox.user_id)


async def _mailbox(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None) -> MailboxConnection | None:
    if user_id is None:
        return None
    return (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == organization_id, MailboxConnection.user_id == user_id))
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


async def send_message(db: AsyncSession, mailbox: MailboxConnection, message: EmailMessage) -> None:
    """Send an already-built message through the mailbox's provider API."""

    link = await _link(db, mailbox)
    key = _key_for(mailbox)
    try:
        if key == "gmail":
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            await core.request(db, link, "POST", f"{GMAIL}/messages/send", json={"raw": raw})
        else:
            mime = message.as_bytes(policy=email.policy.SMTP)
            await core.request(
                db, link, "POST", f"{GRAPH}/sendMail",
                content=base64.b64encode(mime).decode(), headers={"Content-Type": "text/plain"},
            )
    except IntegrationError as exc:
        mailbox.last_error = str(exc)[:1000]
        core.log(db, mailbox.organization_id, key, "send_failed", f"An email to {message.get('To')} wasn't sent: {exc}", level="error", connection=link)
        raise
    mailbox.last_error = None


async def check_mailbox(db: AsyncSession, mailbox: MailboxConnection) -> str:
    link = await _link(db, mailbox)
    return await test(db, _key_for(mailbox), link, None)


# ---------------------------------------------------------------------------
# Reading the inbox
# ---------------------------------------------------------------------------


def _parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _gmail_fetch(db: AsyncSession, link: IntegrationConnection) -> list[mailer.InboundMessage]:
    config = dict(link.config or {})
    history_id = config.get("gmail_history_id")
    ids: list[str] = []
    latest: str | None = None
    if history_id:
        try:
            page: str | None = None
            for _ in range(MAX_PAGES):
                params: dict[str, Any] = {"startHistoryId": history_id, "historyTypes": "messageAdded", "labelId": "INBOX", "maxResults": 100}
                if page:
                    params["pageToken"] = page
                data = (await core.request(db, link, "GET", f"{GMAIL}/history", params=params)).json()
                for entry in data.get("history") or []:
                    for added in entry.get("messagesAdded") or []:
                        message = added.get("message") or {}
                        labels = set(message.get("labelIds") or [])
                        if message.get("id") and "INBOX" in labels and not labels & {"SENT", "DRAFT", "SPAM"}:
                            ids.append(message["id"])
                latest = data.get("historyId") or latest
                page = data.get("nextPageToken")
                if not page:
                    break
        except IntegrationError as exc:
            if exc.status_code != 400:
                raise
            # The history ID is too old (Gmail keeps about a week): start over from recent mail.
            history_id = None
            ids = []
            link.last_error = None
    if not history_id:
        data = (
            await core.request(db, link, "GET", f"{GMAIL}/messages", params={"q": f"in:inbox newer_than:{FIRST_SYNC_DAYS}d", "maxResults": MAX_MESSAGES})
        ).json()
        ids = [m["id"] for m in data.get("messages") or [] if m.get("id")]
        profile = (await core.request(db, link, "GET", f"{GMAIL}/profile")).json()
        latest = profile.get("historyId")
    seen: set[str] = set()
    messages: list[mailer.InboundMessage] = []
    for message_id in ids:
        if message_id in seen or len(messages) >= MAX_MESSAGES:
            continue
        seen.add(message_id)
        data = (await core.request(db, link, "GET", f"{GMAIL}/messages/{message_id}", params={"format": "raw"})).json()
        raw = data.get("raw")
        if not raw:
            continue
        padded = raw + "=" * (-len(raw) % 4)
        messages.append(mailer.parse_message(base64.urlsafe_b64decode(padded), f"gmail-{message_id}"))
    if latest:
        link.config = {**(link.config or {}), "gmail_history_id": str(latest)}
    return messages


def _graph_message(item: dict[str, Any]) -> mailer.InboundMessage:
    sender = (item.get("from") or item.get("sender") or {}).get("emailAddress") or {}
    recipients = []
    for group in ("toRecipients", "ccRecipients"):
        for recipient in item.get(group) or []:
            address = ((recipient or {}).get("emailAddress") or {}).get("address")
            if address:
                recipients.append(address.lower())
    body = item.get("body") or {}
    text = body.get("content") if body.get("contentType", "text").lower() == "text" else None
    return mailer.InboundMessage(
        message_id=(item.get("internetMessageId") or "").strip() or f"graph-{item.get('id')}",
        subject=item.get("subject") or "(no subject)",
        from_address=(sender.get("address") or "").lower(),
        from_name=sender.get("name") or None,
        to_addresses=recipients,
        date=_parse_when(item.get("receivedDateTime")),
        text=(text or item.get("bodyPreview") or "")[:20000],
    )


async def _graph_fetch(db: AsyncSession, link: IntegrationConnection) -> list[mailer.InboundMessage]:
    config = dict(link.config or {})
    url: str | None = config.get("graph_next_link") or config.get("graph_delta_link")
    params: dict[str, Any] | None = None
    if not url:
        since = (utcnow() - timedelta(days=FIRST_SYNC_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{GRAPH}/mailFolders/inbox/messages/delta"
        params = {
            "$select": "internetMessageId,subject,from,sender,toRecipients,ccRecipients,receivedDateTime,body,bodyPreview,isDraft",
            "$filter": f"receivedDateTime ge {since}",
        }
    headers = {"Prefer": 'odata.maxpagesize=50, outlook.body-content-type="text"'}
    messages: list[mailer.InboundMessage] = []
    next_link: str | None = None
    delta_link: str | None = None
    try:
        for _ in range(MAX_PAGES):
            data = (await core.request(db, link, "GET", url, params=params, headers=headers)).json()
            params = None  # the links carry every parameter from here on
            for item in data.get("value") or []:
                if "@removed" in item or item.get("isDraft"):
                    continue
                messages.append(_graph_message(item))
            next_link = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")
            if delta_link or not next_link:
                break
            url = next_link
    except IntegrationError as exc:
        if exc.status_code != 400 or not (config.get("graph_delta_link") or config.get("graph_next_link")):
            raise
        # The delta token expired: start a fresh round next time.
        link.config = {**config, "graph_delta_link": None, "graph_next_link": None}
        link.last_error = None
        return []
    if delta_link:
        link.config = {**(link.config or {}), "graph_delta_link": delta_link, "graph_next_link": None}
    elif next_link:
        link.config = {**(link.config or {}), "graph_next_link": next_link}
    return messages[: MAX_MESSAGES * MAX_PAGES]


async def _sync(db: AsyncSession, link: IntegrationConnection, mailbox: MailboxConnection) -> dict[str, Any]:
    from app.services import mailbox_sync

    key = link.provider
    now = utcnow()
    try:
        messages = await (_gmail_fetch if key == "gmail" else _graph_fetch)(db, link)
        own = (mailbox.email_address or "").lower()
        messages = [m for m in messages if m.from_address and m.from_address != own]
        result = await mailbox_sync.import_messages(db, mailbox.organization_id, mailbox.email_address, mailbox.display_name, messages, source=key)
    except IntegrationError as exc:
        changed = mailbox.last_error != str(exc)
        mailbox.last_error = str(exc)[:1000]
        mailbox.last_sync_at = now
        link.last_sync_at = now
        if changed:
            core.log(db, mailbox.organization_id, key, "inbox_sync_failed", f"Couldn't read the inbox: {exc}", level="error", connection=link)
        raise
    mailbox.last_sync_at = now
    mailbox.last_error = None
    mailbox.status = "connected"
    link.last_sync_at = now
    link.last_error = None
    stats = dict((link.config or {}).get("sync_stats") or {})
    stats["imported_total"] = int(stats.get("imported_total", 0)) + result["imported"]
    stats["last_checked"] = result["checked"]
    stats["last_imported"] = result["imported"]
    link.config = {**(link.config or {}), "sync_stats": stats}
    if result["imported"]:
        noun = "email" if result["imported"] == 1 else "emails"
        core.log(db, mailbox.organization_id, key, "inbox_synced", f"Added {result['imported']} customer {noun} from {mailbox.email_address}.", level="success", connection=link)
    return result


async def sync_mailbox_now(db: AsyncSession, mailbox: MailboxConnection) -> dict[str, Any]:
    """Read new inbox messages right away (the "Sync now" buttons). Commits."""

    link = await _link(db, mailbox)
    try:
        result = await _sync(db, link, mailbox)
    except IntegrationError:
        await db.commit()
        raise
    await db.commit()
    return result


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    """Point the person's AIVA mailbox at this account, replacing an app-password mailbox."""

    if connection.user_id is None:
        return
    address = (connection.account_label or "").strip().lower()
    if "@" not in address:
        raise IntegrationError(f"Connected, but {LABEL[key]} didn't say which email address this is. Try connecting again.")
    user = await db.get(User, connection.user_id)
    mailbox = await _mailbox(db, connection.organization_id, connection.user_id)
    replaced: str | None = None
    if mailbox is None:
        mailbox = MailboxConnection(id=uuid.uuid4(), organization_id=connection.organization_id, user_id=connection.user_id, secret="")
        db.add(mailbox)
    elif mailbox.transport != TRANSPORT[key]:
        replaced = "an app-password mailbox" if mailbox.transport == "smtp" else ("Outlook Mail" if key == "gmail" else "Gmail")
    mailbox.provider = MAILBOX_PROVIDER[key]
    mailbox.transport = TRANSPORT[key]
    mailbox.email_address = address
    mailbox.display_name = (connection.config or {}).get("display_name") or mailbox.display_name or (connection.config or {}).get("name") or (user.full_name if user else None)
    mailbox.username = address
    mailbox.smtp_host = ""
    mailbox.smtp_port = 0
    mailbox.smtp_security = "none"
    mailbox.imap_host = None
    mailbox.imap_port = 0
    mailbox.secret = ""
    mailbox.status = "connected"
    mailbox.last_error = None
    sync_inbox = (connection.config or {}).get("sync_inbox", True) is not False
    mailbox.sync_enabled = sync_inbox
    # A fresh sign-in starts reading from recent mail again.
    connection.config = {
        **(connection.config or {}),
        "sync_inbox": sync_inbox,
        "gmail_history_id": None,
        "graph_delta_link": None,
        "graph_next_link": None,
    }
    connection.last_sync_at = None
    message = f"AIVA now sends as {address} through {LABEL[key]}."
    if replaced:
        message += f" This replaced {replaced}."
    core.log(db, connection.organization_id, key, "mailbox_ready", message, level="success", connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    mailbox = await _mailbox(db, connection.organization_id, connection.user_id)
    if mailbox is not None and mailbox.transport == TRANSPORT[key]:
        await db.delete(mailbox)
        core.log(db, connection.organization_id, key, "mailbox_removed", f"{mailbox.email_address} no longer sends through AIVA.", connection=connection)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    if key == "gmail":
        profile = (await core.request(db, connection, "GET", f"{GMAIL}/profile")).json()
        total = profile.get("messagesTotal")
        return f"Signed in to Gmail as {profile.get('emailAddress') or connection.account_label}" + (f"; {int(total):,} messages in the mailbox." if total is not None else ".")
    inbox = (await core.request(db, connection, "GET", f"{GRAPH}/mailFolders/inbox", params={"$select": "displayName,unreadItemCount,totalItemCount"})).json()
    unread = inbox.get("unreadItemCount")
    return f"Signed in to Outlook as {connection.account_label}" + (f"; {int(unread):,} unread in the inbox." if unread is not None else ".")


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, key, principal.user_id)
    mailbox = await _mailbox(db, org, principal.user_id)
    uses_this = mailbox is not None and mailbox.transport == TRANSPORT[key]
    other = None
    if mailbox is not None and not uses_this:
        other = {
            "email": mailbox.email_address,
            "kind": "app_password" if mailbox.transport == "smtp" else mailer.API_TRANSPORTS.get(mailbox.transport),
        }
    week = utcnow() - timedelta(days=7)
    imported_week = 0
    if uses_this:
        imported_week = (
            await db.execute(
                select(func.count()).select_from(StoredEmail).where(
                    StoredEmail.organization_id == org,
                    StoredEmail.direction == "inbound",
                    StoredEmail.occurred_at >= week,
                    StoredEmail.message_metadata["mailbox"].astext == mailbox.email_address,
                )
            )
        ).scalar_one()
    config = dict(connection.config or {}) if connection else {}
    return {
        "mailbox": {
            "email": mailbox.email_address,
            "display_name": mailbox.display_name,
            "status": mailbox.status,
            "last_error": mailbox.last_error,
            "last_sync_at": mailbox.last_sync_at,
            "sync_enabled": mailbox.sync_enabled,
        } if uses_this and mailbox else None,
        "other_mailbox": other,
        "sync_inbox": config.get("sync_inbox", True) is not False if connection else None,
        "display_name": (mailbox.display_name if uses_this and mailbox else None) or config.get("name"),
        "stats": {**(config.get("sync_stats") or {}), "imported_week": int(imported_week)},
        "sync_every_minutes": int(SYNC_EVERY.total_seconds() // 60),
        "server_mail": mailer.server_config() is not None,
    }


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if not personal:
        return {}
    cleaned: dict[str, Any] = {}
    if "sync_inbox" in config:
        cleaned["sync_inbox"] = bool(config["sync_inbox"])
    if "display_name" in config:
        name = str(config.get("display_name") or "").strip()
        if len(name) > 200:
            raise IntegrationError("The sender name can be at most 200 characters.", 422)
        cleaned["display_name"] = name or None
    mailbox = await _mailbox(db, principal.organization_id, principal.user_id)
    if mailbox is not None and mailbox.transport == TRANSPORT[key]:
        if "sync_inbox" in cleaned:
            mailbox.sync_enabled = cleaned["sync_inbox"]
        if "display_name" in cleaned:
            mailbox.display_name = cleaned["display_name"] or principal.full_name or None
    return cleaned


async def run_due(db: AsyncSession) -> int:
    """Read new inbox messages for every API mailbox that's due."""

    cutoff = utcnow() - SYNC_EVERY
    rows = (
        await db.execute(
            select(IntegrationConnection, MailboxConnection)
            .join(
                IntegrationToggle,
                and_(
                    IntegrationToggle.organization_id == IntegrationConnection.organization_id,
                    IntegrationToggle.provider == IntegrationConnection.provider,
                    IntegrationToggle.enabled.is_(True),
                ),
            )
            .join(
                MailboxConnection,
                and_(MailboxConnection.organization_id == IntegrationConnection.organization_id, MailboxConnection.user_id == IntegrationConnection.user_id),
            )
            .where(
                IntegrationConnection.provider.in_(KEYS),
                IntegrationConnection.status == "connected",
                or_(
                    and_(IntegrationConnection.provider == "gmail", MailboxConnection.transport == "gmail_api"),
                    and_(IntegrationConnection.provider == "outlook_mail", MailboxConnection.transport == "graph"),
                ),
                MailboxConnection.sync_enabled.is_(True),
                or_(IntegrationConnection.last_sync_at.is_(None), IntegrationConnection.last_sync_at <= cutoff),
            )
            .order_by(IntegrationConnection.last_sync_at.asc().nulls_first())
            .limit(JOB_BATCH)
        )
    ).all()
    handled = 0
    for link, mailbox in rows:
        try:
            await _sync(db, link, mailbox)
            handled += 1
        except IntegrationError as exc:
            logger.info("Inbox sync for %s failed: %s", mailbox.email_address, exc)
        await db.commit()
    return handled
