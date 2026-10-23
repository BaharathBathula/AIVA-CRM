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
    try:
        messages = await mailer.fetch_inbox(connection, limit=limit)
    except mailer.MailError as exc:
        connection.status = "error"
        connection.last_error = str(exc)
        connection.last_sync_at = utcnow()
        await db.commit()
        raise
    imported = 0
    skipped = 0
    db.sync_session.info["automation_disabled"] = True
    try:
        for message in messages:
            exists = (
                await db.execute(
                    select(func.count()).select_from(EmailMessage).where(
                        EmailMessage.organization_id == connection.organization_id,
                        EmailMessage.provider == PROVIDER,
                        EmailMessage.external_message_id == message.message_id[:500],
                    )
                )
            ).scalar_one()
            if exists:
                skipped += 1
                continue
            contact = (
                await db.execute(
                    select(Contact).where(
                        Contact.organization_id == connection.organization_id,
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
                        EmailThread.organization_id == connection.organization_id,
                        EmailThread.contact_id == contact.id,
                        EmailThread.subject == subject,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            occurred = message.date or utcnow()
            if thread is None:
                thread = EmailThread(
                    id=uuid.uuid4(),
                    organization_id=connection.organization_id,
                    provider=PROVIDER,
                    external_thread_id=f"{contact.id}:{subject}"[:500],
                    subject=subject,
                    snippet=message.text[:250],
                    account_id=contact.account_id,
                    contact_id=contact.id,
                    participants=[
                        {"email": message.from_address, "name": message.from_name, "role": "from"},
                        {"email": connection.email_address, "name": connection.display_name, "role": "to"},
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
                    organization_id=connection.organization_id,
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
                    message_metadata={"source": "mailbox_sync", "mailbox": connection.email_address},
                )
            )
            imported += 1
        connection.status = "connected"
        connection.last_error = None
        connection.last_sync_at = utcnow()
        await db.commit()
    finally:
        db.sync_session.info["automation_disabled"] = False
    return {"imported": imported, "skipped": skipped, "checked": len(messages)}


async def sync_due(db: AsyncSession) -> int:
    cutoff = utcnow() - SYNC_EVERY
    connections = (
        await db.execute(
            select(MailboxConnection).where(
                MailboxConnection.sync_enabled.is_(True),
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
