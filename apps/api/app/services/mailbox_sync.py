"""Pull recent inbox messages from connected mailboxes into AIVA.

Only messages from people already in AIVA (a contact's email address) are
kept, so personal mail stays out of the CRM. Each one becomes an inbound
email on the contact's timeline, threaded by subject.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.editions import MailboxConnection
from app.models.email_message import EmailMessage
from app.models.email_thread import EmailThread
from app.services import mailer

logger = logging.getLogger("aiva.mailbox")

SYNC_EVERY = timedelta(minutes=15)
PROVIDER = "mailbox"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _thread_subject(subject: str) -> str:
    return re.sub(r"^((re|fw|fwd|aw)\s*:\s*)+", "", subject or "", flags=re.I).strip()[:480] or "(no subject)"


async def sync_connection(db: AsyncSession, connection: MailboxConnection, limit: int = 40) -> dict:
    if connection.transport != "smtp":
        # Gmail / Outlook Mail integration mailboxes read the inbox through the provider API.
        from app.integrations.core import IntegrationError
        from app.integrations.providers import mail as mail_api

        try:
            return await mail_api.sync_mailbox_now(db, connection)
        except IntegrationError as exc:
            raise mailer.MailError(str(exc)) from exc
    try:
        messages = await mailer.fetch_inbox(connection, limit=limit)
    except mailer.MailError as exc:
        connection.status = "error"
        connection.last_error = str(exc)
        connection.last_sync_at = utcnow()
        await db.commit()
        raise
    result = await import_messages(db, connection.organization_id, connection.email_address, connection.display_name, messages)
    connection.status = "connected"
    connection.last_error = None
    connection.last_sync_at = utcnow()
    await db.commit()
    return result


async def import_messages(
    db: AsyncSession,
    organization_id: uuid.UUID,
    mailbox_email: str,
    mailbox_name: str | None,
    messages: list[mailer.InboundMessage],
    source: str = "mailbox_sync",
) -> dict:
    """Run reply detection on each new message and add the ones from contacts to their timelines.

    Shared by IMAP mailboxes and the Gmail / Outlook Mail APIs; a message seen before
    (same Message-ID) is skipped, whichever way it arrived. Doesn't commit.
    """

    imported = 0
    skipped = 0
    previous = db.sync_session.info.get("automation_disabled", False)
    db.sync_session.info["automation_disabled"] = True
    try:
        for message in messages:
            exists = (
                await db.execute(
                    select(func.count()).select_from(EmailMessage).where(
                        EmailMessage.organization_id == organization_id,
                        EmailMessage.provider == PROVIDER,
                        EmailMessage.external_message_id == message.message_id[:500],
                    )
                )
            ).scalar_one()
            if exists:
                skipped += 1
                continue
            try:
                from app.services import outbound

                await outbound.handle_inbound(db, organization_id, message)
            except Exception:  # noqa: BLE001 - reply detection must not stop the sync
                logger.exception("Reply detection failed")
            contact = (
                await db.execute(
                    select(Contact).where(
                        Contact.organization_id == organization_id,
                        func.lower(Contact.email) == message.from_address,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if contact is None:
                skipped += 1
                continue
            subject = _thread_subject(message.subject)
            thread = (
                await db.execute(
                    select(EmailThread).where(
                        EmailThread.organization_id == organization_id,
                        EmailThread.contact_id == contact.id,
                        EmailThread.subject == subject,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            occurred = message.date or utcnow()
            if thread is None:
                thread = EmailThread(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    provider=PROVIDER,
                    external_thread_id=f"{contact.id}:{subject}"[:500],
                    subject=subject,
                    snippet=message.text[:250],
                    account_id=contact.account_id,
                    contact_id=contact.id,
                    participants=[
                        {"email": message.from_address, "name": message.from_name, "role": "from"},
                        {"email": mailbox_email, "name": mailbox_name, "role": "to"},
                    ],
                    last_message_at=occurred,
                )
                db.add(thread)
                await db.flush()
            else:
                thread.snippet = message.text[:250]
                if thread.last_message_at is None or occurred > thread.last_message_at:
                    thread.last_message_at = occurred
            db.add(
                EmailMessage(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    thread_id=thread.id,
                    provider=PROVIDER,
                    external_message_id=message.message_id[:500],
                    account_id=contact.account_id,
                    contact_id=contact.id,
                    direction="inbound",
                    subject=message.subject[:500],
                    from_address=message.from_address,
                    from_name=message.from_name,
                    to_recipients=[{"email": address} for address in message.to_addresses][:20],
                    cc_recipients=[],
                    bcc_recipients=[],
                    body_text=message.text,
                    snippet=message.text[:250],
                    occurred_at=occurred,
                    is_read=False,
                    is_draft=False,
                    has_attachments=False,
                    attachments=[],
                    references=[],
                    message_metadata={"source": source, "mailbox": mailbox_email},
                )
            )
            # Keep the next lookups in this batch aware of what was just added.
            await db.flush()
            imported += 1
    finally:
        db.sync_session.info["automation_disabled"] = previous
    return {"imported": imported, "skipped": skipped, "checked": len(messages)}


async def sync_due(db: AsyncSession) -> int:
    cutoff = utcnow() - SYNC_EVERY
    connections = (
        await db.execute(
            select(MailboxConnection).where(
                MailboxConnection.sync_enabled.is_(True),
                MailboxConnection.transport == "smtp",
                MailboxConnection.imap_host.is_not(None),
                MailboxConnection.imap_host != "",
                (MailboxConnection.last_sync_at.is_(None)) | (MailboxConnection.last_sync_at <= cutoff),
            ).limit(10)
        )
    ).scalars().all()
    done = 0
    for connection in connections:
        try:
            await sync_connection(db, connection)
            done += 1
        except mailer.MailError as exc:
            logger.info("Mailbox sync for %s failed: %s", connection.email_address, exc)
    return done
