"""Sending and receiving email.

Mail goes out through, in order of preference:

1. the sender's connected mailbox (Gmail, Outlook or any SMTP server, using
   an app password),
2. the server-wide SMTP settings (``SMTP_HOST`` …), or
3. the outbox: nothing is delivered; the message is stored so it can be
   reviewed in AIVA. Every caller reports which one was used.

Connected mailboxes can also pull recent inbox messages over IMAP and
attach them to matching contacts and leads.
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.models.editions import MailboxConnection

logger = logging.getLogger("aiva.mailer")

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "label": "Gmail / Google Workspace",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "help": "Turn on 2-Step Verification for the Google account, then create an app password at myaccount.google.com/apppasswords.",
    },
    "outlook": {
        "label": "Outlook / Microsoft 365",
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "help": "Use an app password from account.microsoft.com/security. Your Microsoft 365 admin may need to allow SMTP AUTH for the mailbox.",
    },
    "smtp": {
        "label": "Other email (SMTP / IMAP)",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "",
        "imap_port": 993,
        "help": "Use the SMTP and IMAP settings from your email provider.",
    },
}


class MailError(Exception):
    pass


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str
    username: str
    password: str
    from_email: str
    from_name: str | None
    source: str  # mailbox | server


def server_config() -> SmtpConfig | None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from_email:
        return None
    return SmtpConfig(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_security,
        settings.smtp_username,
        settings.smtp_password,
        settings.smtp_from_email,
        settings.smtp_from_name,
        "server",
    )


def mailbox_config(connection: MailboxConnection) -> SmtpConfig:
    return SmtpConfig(
        connection.smtp_host,
        connection.smtp_port,
        connection.smtp_security,
        connection.username,
        decrypt(connection.secret) or "",
        connection.email_address,
        connection.display_name,
        "mailbox",
    )


async def sender_config(db: AsyncSession, organization_id, user_id) -> SmtpConfig | None:
    if user_id is not None:
        connection = (
            await db.execute(
                select(MailboxConnection).where(
                    MailboxConnection.organization_id == organization_id,
                    MailboxConnection.user_id == user_id,
                    MailboxConnection.status == "connected",
                )
            )
        ).scalar_one_or_none()
        if connection is not None:
            return mailbox_config(connection)
    return server_config()


def _smtp_send(config: SmtpConfig, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if config.security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=20)
    try:
        server.ehlo()
        if config.security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        if config.username:
            server.login(config.username, config.password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def build_message(
    config: SmtpConfig,
    *,
    to: str,
    to_name: str | None,
    subject: str,
    text: str,
    html_body: str | None,
    reply_to: str | None = None,
    from_name: str | None = None,
    headers: dict[str, str] | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email.utils.formataddr((from_name or config.from_name or "", config.from_email))
    message["To"] = email.utils.formataddr((to_name or "", to))
    message["Date"] = email.utils.formatdate(localtime=True)
    if "Message-ID" not in (headers or {}):
        message["Message-ID"] = email.utils.make_msgid(domain=config.from_email.split("@")[-1])
    if reply_to:
        message["Reply-To"] = reply_to
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content(text or "")
    if html_body:
        message.add_alternative(html_body, subtype="html")
    for filename, content_type, payload in attachments or []:
        # e.g. "text/calendar; method=REQUEST" for meeting invitations.
        kind, _, rest = content_type.partition(";")
        maintype, _, subtype = kind.strip().partition("/")
        extra = {}
        if "method=" in rest:
            extra = {"params": {"method": rest.split("method=", 1)[1].strip()}}
        message.add_attachment(payload, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename, **extra)
    return message


async def send(
    config: SmtpConfig | None,
    *,
    to: str,
    to_name: str | None = None,
    subject: str,
    text: str,
    html_body: str | None = None,
    reply_to: str | None = None,
    from_name: str | None = None,
    headers: dict[str, str] | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> str:
    """Send one message. Returns "smtp" when delivered, "outbox" when there's no mail server."""

    if config is None:
        return "outbox"
    message = build_message(
        config, to=to, to_name=to_name, subject=subject, text=text, html_body=html_body,
        reply_to=reply_to, from_name=from_name, headers=headers, attachments=attachments,
    )
    try:
        await asyncio.to_thread(_smtp_send, config, message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(str(exc) or exc.__class__.__name__) from exc
    return "smtp"


def _smtp_check(config: SmtpConfig) -> None:
    context = ssl.create_default_context()
    if config.security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=15, context=context)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=15)
    try:
        server.ehlo()
        if config.security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        server.login(config.username, config.password)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


async def verify_smtp(config: SmtpConfig) -> None:
    try:
        await asyncio.to_thread(_smtp_check, config)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("The mail server rejected the username or app password.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Couldn't reach {config.host}:{config.port} ({exc or exc.__class__.__name__}).") from exc


# ---------------------------------------------------------------------------
# IMAP
# ---------------------------------------------------------------------------


@dataclass
class InboundMessage:
    message_id: str
    subject: str
    from_address: str
    from_name: str | None
    to_addresses: list[str]
    date: Any
    text: str


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _plain_text(message: email.message.Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition") or ""):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = message.get_payload(decode=True) or b""
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace")


def _imap_fetch(connection_values: dict[str, Any], limit: int) -> list[InboundMessage]:
    client = imaplib.IMAP4_SSL(connection_values["host"], connection_values["port"], timeout=20)
    try:
        client.login(connection_values["username"], connection_values["password"])
        client.select("INBOX", readonly=True)
        status, data = client.search(None, "ALL")
        if status != "OK":
            return []
        ids = data[0].split()[-limit:]
        messages: list[InboundMessage] = []
        for message_id in reversed(ids):
            status, parts = client.fetch(message_id, "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            parsed = email.message_from_bytes(parts[0][1])
            name, address = email.utils.parseaddr(parsed.get("From", ""))
            recipients = [addr.lower() for _, addr in email.utils.getaddresses(parsed.get_all("To", []) + parsed.get_all("Cc", []))]
            try:
                sent = email.utils.parsedate_to_datetime(parsed.get("Date"))
            except (TypeError, ValueError):
                sent = None
            messages.append(
                InboundMessage(
                    message_id=(parsed.get("Message-ID") or "").strip() or f"imap-{message_id.decode()}",
                    subject=_decode(parsed.get("Subject")) or "(no subject)",
                    from_address=address.lower(),
                    from_name=_decode(name) or None,
                    to_addresses=recipients,
                    date=sent,
                    text=_plain_text(parsed)[:20000],
                )
            )
        return messages
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


async def fetch_inbox(connection: MailboxConnection, limit: int = 40) -> list[InboundMessage]:
    if not connection.imap_host:
        raise MailError("Add an IMAP server to pull in replies.")
    values = {
        "host": connection.imap_host,
        "port": connection.imap_port,
        "username": connection.username,
        "password": decrypt(connection.secret) or "",
    }
    try:
        return await asyncio.to_thread(_imap_fetch, values, limit)
    except imaplib.IMAP4.error as exc:
        raise MailError("The mail server rejected the IMAP sign-in. Check the app password.") from exc
    except OSError as exc:
        raise MailError(f"Couldn't reach {connection.imap_host}:{connection.imap_port} ({exc}).") from exc
