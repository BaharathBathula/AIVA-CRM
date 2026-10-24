"""Help Scout (Mailbox API 2.0): conversations by customer, mailboxes, webhooks.

Auth: the workspace's own Help Scout app (App ID + secret) with the OAuth 2.0
client-credentials grant. The token (valid two days) is kept encrypted on the
connection and renewed when it's about to expire. AIVA registers its webhook via
the API with a secret; requests carry ``X-HelpScout-Signature`` =
base64(HMAC-SHA1(secret, body)).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._hd_common import Ticket, check, clean_email, hmac_b64, http, parse_time, same, utcnow
from app.models.integration_hub import IntegrationConnection

KEY = "help_scout"
KIND = "conversation"
API = "https://api.helpscout.net/v2"
TOKEN_URL = "https://api.helpscout.net/v2/oauth2/token"
STATUS = {"active": ("open", "Active"), "pending": ("pending", "Pending"), "closed": ("closed", "Closed"), "spam": ("closed", "Spam")}
EVENTS = ["convo.created", "convo.assigned", "convo.status", "convo.moved", "convo.customer.reply.created", "convo.agent.reply.created", "convo.note.created"]
CONVERSATION = re.compile(r"/conversation/(\d{1,15})")


async def _new_token(app_id: str, app_secret: str) -> tuple[str, int]:
    response = await http("POST", TOKEN_URL, data={"grant_type": "client_credentials", "client_id": app_id, "client_secret": app_secret},
                          headers={"Accept": "application/json"})
    if response.status_code in (400, 401):
        raise IntegrationError("Help Scout didn't accept the App ID and secret. Copy them again from Your profile → My apps.", 400)
    data = check(response, KEY) or {}
    token = data.get("access_token")
    if not token:
        raise IntegrationError("Help Scout didn't return an access token.", 502)
    return token, int(data.get("expires_in") or 7200)


async def _token(connection: IntegrationConnection, *, renew: bool = False) -> str:
    current = decrypt(connection.access_token) if connection.access_token else None
    if current and not renew and connection.token_expires_at and connection.token_expires_at - timedelta(minutes=10) > utcnow():
        return current
    app_id = (connection.config or {}).get("app_id") or ""
    app_secret = core.secret(connection, "app_secret") or ""
    if not app_id or not app_secret:
        raise IntegrationError("Help Scout isn't fully connected. Add the App ID and secret.", 409)
    token, expires_in = await _new_token(app_id, app_secret)
    connection.access_token = encrypt(token)
    connection.token_expires_at = utcnow() + timedelta(seconds=expires_in)
    return token


async def _raw(connection: IntegrationConnection, method: str, path: str, **kwargs: Any):
    token = await _token(connection)
    url = path if path.startswith("https://") else f"{API}{path}"
    response = await http(method, url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, **kwargs)
    if response.status_code == 401:
        token = await _token(connection, renew=True)
        response = await http(method, url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, **kwargs)
    return response


async def _call(connection: IntegrationConnection, method: str, path: str, *, what: str = "", **kwargs: Any) -> Any:
    return check(await _raw(connection, method, path, **kwargs), KEY, connection, what=what)


def to_ticket(raw: dict[str, Any], *, email: str | None = None) -> Ticket:
    status, label = STATUS.get(str(raw.get("status") or "active"), ("open", str(raw.get("status") or "Active").title()))
    customer = raw.get("primaryCustomer") if isinstance(raw.get("primaryCustomer"), dict) else {}
    conversation_id = str(raw.get("id"))
    number = raw.get("number")
    web = (((raw.get("_links") or {}).get("web") or {}).get("href")) if isinstance(raw.get("_links"), dict) else None
    name = " ".join(x for x in (customer.get("first"), customer.get("last")) if x) or None
    return Ticket(
        provider=KEY,
        external_id=conversation_id,
        kind=KIND,
        number=f"#{number}" if number else f"#{conversation_id}",
        subject=str(raw.get("subject") or "(no subject)")[:500],
        status=status,
        status_label=label,
        priority=None,
        requester_email=clean_email(customer.get("email")) or email,
        requester_name=name,
        url=web or f"https://secure.helpscout.net/conversation/{conversation_id}/{number or ''}".rstrip("/"),
        created_at=parse_time(raw.get("createdAt")),
        updated_at=parse_time(raw.get("userUpdatedAt") or raw.get("closedAt") or raw.get("createdAt")),
        comment_count=int(raw["threads"]) if isinstance(raw.get("threads"), int) else None,
        description=(raw.get("preview") or "")[:8000] or None,
        extra={"mailbox_id": raw.get("mailboxId")},
    )


def _embedded(payload: Any, name: str) -> list[dict[str, Any]]:
    items = ((payload or {}).get("_embedded") or {}).get(name) if isinstance(payload, dict) else None
    return [i for i in items or [] if isinstance(i, dict)]


async def validate(values: dict[str, str]) -> dict[str, Any]:
    app_id = (values.get("app_id") or "").strip()
    app_secret = (values.get("app_secret") or "").strip()
    if not app_id:
        raise IntegrationError("Enter the App ID of your Help Scout app.", 422)
    token, _ = await _new_token(app_id, app_secret)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    me = check(await http("GET", f"{API}/users/me", headers=headers), KEY) or {}
    mailboxes = _embedded(check(await http("GET", f"{API}/mailboxes", headers=headers), KEY), "mailboxes")
    if not mailboxes:
        raise IntegrationError("That Help Scout app can't see any mailboxes.", 400)
    listed = [{"id": str(m.get("id")), "name": m.get("name"), "email": m.get("email")} for m in mailboxes][:50]
    return {
        "label": f"Help Scout · {listed[0]['name']}" + (f" +{len(listed) - 1}" if len(listed) > 1 else ""),
        "external_id": str(me.get("id") or app_id),
        "config": {"app_id": app_id, "user_email": me.get("email"), "user_name": " ".join(x for x in (me.get("firstName"), me.get("lastName")) if x),
                   "mailboxes": listed},
    }


async def setup(db: AsyncSession, connection: IntegrationConnection) -> None:
    url = core.webhook_url(KEY, connection.webhook_token)
    if not url:
        return
    await teardown(db, connection)
    secret = secrets.token_urlsafe(30)[:40]
    try:
        response = await _raw(connection, "POST", "/webhooks", json={"url": url, "events": EVENTS, "secret": secret, "payloadVersion": "V2", "label": "AIVA CRM",
                                                                       "notification": False})
        check(response, KEY, connection, what="create webhooks (an account owner or admin app is needed)")
        webhook_id = response.headers.get("Resource-ID") or response.headers.get("resource-id") or ""
        core.set_secret(connection, "webhook_secret", secret)
        connection.config = {**(connection.config or {}), "webhook_id": webhook_id or None, "webhook_status": "active", "webhook_error": None}
        core.log(db, connection.organization_id, KEY, "webhook_created", "Registered AIVA's webhook with Help Scout, so conversation changes arrive within seconds.",
                 level="success", connection=connection)
    except IntegrationError as exc:
        connection.config = {**(connection.config or {}), "webhook_status": "failed", "webhook_error": str(exc)[:300]}
        core.log(db, connection.organization_id, KEY, "webhook_failed", f"Couldn't register the Help Scout webhook ({exc}). Conversations still sync every few minutes.",
                 level="warning", connection=connection)


async def teardown(db: AsyncSession, connection: IntegrationConnection) -> None:
    webhook_id = (connection.config or {}).get("webhook_id")
    if webhook_id and str(webhook_id).isdigit():
        try:
            await _call(connection, "DELETE", f"/webhooks/{quote(str(webhook_id))}")
        except IntegrationError:
            pass
    connection.config = {**(connection.config or {}), "webhook_id": None, "webhook_status": None}
    core.set_secret(connection, "webhook_secret", None)


async def check_connection(db: AsyncSession, connection: IntegrationConnection) -> str:
    me = await _call(connection, "GET", "/users/me")
    mailboxes = _embedded(await _call(connection, "GET", "/mailboxes"), "mailboxes")
    return f"Help Scout answered as {me.get('email') or 'your app'}; {len(mailboxes)} mailbox{'es' if len(mailboxes) != 1 else ''} visible."


def options(connection: IntegrationConnection) -> dict[str, Any]:
    return {"mailboxes": (connection.config or {}).get("mailboxes") or []}


async def by_emails(db: AsyncSession, connection: IntegrationConnection, emails: list[str]) -> list[Ticket]:
    found: dict[str, Ticket] = {}
    for email in emails[:5]:
        payload = await _call(connection, "GET", "/conversations",
                              params={"query": f'(email:"{email}")', "status": "all", "sortField": "modifiedAt", "sortOrder": "desc"})
        for raw in _embedded(payload, "conversations")[:25]:
            if raw.get("id"):
                ticket = to_ticket(raw, email=email)
                found[ticket.external_id] = ticket
    return list(found.values())


async def by_company(db: AsyncSession, connection: IntegrationConnection, name: str | None, domain: str | None) -> list[Ticket]:
    return []


async def fetch(db: AsyncSession, connection: IntegrationConnection, external_id: str, *, comments: bool = False) -> Ticket:
    if not (str(external_id).isascii() and str(external_id).isdigit()):
        raise IntegrationError("Help Scout conversation ids are digits.", 422)
    raw = await _call(connection, "GET", f"/conversations/{external_id}", what=f"conversation {external_id}")
    return to_ticket(raw or {})


def reference(text: str) -> str | None:
    text = (text or "").strip()
    match = CONVERSATION.search(text)
    if match:
        return match.group(1)
    digits = text.lstrip("#")
    return digits if digits.isascii() and digits.isdigit() and len(digits) <= 15 else None


async def find_by_number(db: AsyncSession, connection: IntegrationConnection, number: str) -> Ticket | None:
    payload = await _call(connection, "GET", "/conversations", params={"query": f"(number:{int(number)})", "status": "all"})
    rows = _embedded(payload, "conversations")
    return to_ticket(rows[0]) if rows else None


async def create(
    db: AsyncSession, connection: IntegrationConnection, *, subject: str, body: str, requester_email: str, requester_name: str | None,
    priority: str | None, public: bool, options: dict[str, Any],
) -> Ticket:
    mailbox_id = str(options.get("mailbox_id") or "")
    known = {str(m.get("id")) for m in (connection.config or {}).get("mailboxes") or []}
    if not mailbox_id or mailbox_id not in known:
        raise IntegrationError("Choose the Help Scout mailbox for the conversation.", 422)
    first, _, last = (requester_name or "").partition(" ")
    customer: dict[str, Any] = {"email": requester_email}
    if first:
        customer["firstName"] = first[:40]
    if last:
        customer["lastName"] = last[:40]
    response = await _raw(
        connection, "POST", "/conversations",
        json={"subject": subject, "customer": customer, "mailboxId": int(mailbox_id), "type": "email", "status": "active", "tags": ["aiva"],
              "threads": [{"type": "customer", "customer": {"email": requester_email}, "text": body}]},
    )
    check(response, KEY, connection, what="create conversations")
    conversation_id = response.headers.get("Resource-ID") or response.headers.get("resource-id") or ""
    if not conversation_id.isdigit():
        raise IntegrationError("Help Scout created the conversation but didn't say which one. Look for it in the mailbox.", 502)
    ticket = await fetch(db, connection, conversation_id)
    ticket.requester_email = ticket.requester_email or requester_email
    return ticket


async def sync(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], pages: int = 2) -> tuple[list[Ticket], bool]:
    config = dict(connection.config or {})
    since = config.get("sync_since") or (utcnow() - timedelta(days=int(settings.get("backfill_days") or 30))).strftime("%Y-%m-%dT%H:%M:%SZ")
    page = int(config.get("sync_page") or 1)
    newest = config.get("sync_newest") or since
    tickets: list[Ticket] = []
    more = False
    for _ in range(pages):
        payload = await _call(connection, "GET", "/conversations",
                              params={"status": "all", "modifiedSince": since, "sortField": "modifiedAt", "sortOrder": "asc", "page": page})
        for raw in _embedded(payload, "conversations"):
            if raw.get("id"):
                ticket = to_ticket(raw)
                tickets.append(ticket)
                if ticket.updated_at and ticket.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") > newest:
                    newest = ticket.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        info = (payload or {}).get("page") or {}
        if int(info.get("number") or page) < int(info.get("totalPages") or 0):
            page += 1
            more = True
            continue
        since, page, more = newest, 1, False
        break
    connection.config = {**(connection.config or {}), "sync_since": since, "sync_page": page, "sync_newest": newest}
    return tickets, more


def verify(connection: IntegrationConnection, request: Request, body: bytes) -> bool | None:
    secret = core.secret(connection, "webhook_secret")
    if not secret:
        return None
    return same(hmac_b64(secret, body, hashlib.sha1), request.headers.get("x-helpscout-signature"))


def ids_from(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    text = str(payload.get("id") or "").strip()
    return [text] if text.isascii() and text.isdigit() else []


def webhook_info(connection: IntegrationConnection) -> dict[str, Any]:
    config = connection.config or {}
    return {"mode": "automatic", "status": config.get("webhook_status"), "error": config.get("webhook_error"), "signed": bool(core.secret(connection, "webhook_secret"))}
