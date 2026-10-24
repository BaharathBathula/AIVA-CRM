"""Intercom: conversations with a contact, new leads, webhooks.

Auth: an access token from the workspace's Intercom app (Developer Hub), sent
as a bearer token to the region's API host with a pinned ``Intercom-Version``.
Webhooks are configured in the Developer Hub and signed with the app's client
secret: ``X-Hub-Signature: sha1=<hex HMAC-SHA1 of the body>``. Without the
secret AIVA only takes ids from webhooks and reads them back from the API.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._hd_common import Ticket, cache_get, cache_put, check, clean_email, hmac_hex, http, parse_time, same, utcnow
from app.models.integration_hub import IntegrationConnection

KEY = "intercom"
KIND = "conversation"
VERSION = "2.13"
REGIONS = {
    "us": ("https://api.intercom.io", "https://app.intercom.com"),
    "eu": ("https://api.eu.intercom.io", "https://app.eu.intercom.com"),
    "au": ("https://api.au.intercom.io", "https://app.au.intercom.com"),
}
STATE = {"open": ("open", "Open"), "snoozed": ("pending", "Snoozed"), "closed": ("closed", "Closed")}
CONVERSATION_ID = re.compile(r"(?:/conversation/|^#?)(\d{1,24})(?:\D|$)")
CONVERSATION_TOPICS = ("conversation.",)
LEAD_TOPICS = {"contact.lead.created", "contact.user.created"}


def clean_region(raw: Any) -> str:
    text = str(raw or "us").strip().lower()
    if text not in REGIONS:
        raise IntegrationError("The Intercom region is us, eu or au.", 422)
    return text


def _hosts(connection: IntegrationConnection) -> tuple[str, str]:
    return REGIONS[clean_region((connection.config or {}).get("region"))]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json", "Intercom-Version": VERSION}


def _token(connection: IntegrationConnection) -> str:
    token = core.secret(connection, "access_token") or ""
    if not token:
        raise IntegrationError("Intercom isn't fully connected. Add the access token.", 409)
    return token


async def _call(connection: IntegrationConnection, method: str, path: str, *, what: str = "", **kwargs: Any) -> Any:
    api, _ = _hosts(connection)
    response = await http(method, f"{api}{path}", headers=_headers(_token(connection)), **kwargs)
    return check(response, KEY, connection, what=what)


def _url(connection: IntegrationConnection, conversation_id: str) -> str:
    _, app = _hosts(connection)
    app_id = (connection.config or {}).get("app_id") or ""
    return f"{app}/a/apps/{app_id}/inbox/inbox/conversation/{conversation_id}"


def to_ticket(connection: IntegrationConnection, raw: dict[str, Any], *, email: str | None = None) -> Ticket:
    state, label = STATE.get(str(raw.get("state") or ("open" if raw.get("open") else "closed")), ("open", "Open"))
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    author = source.get("author") if isinstance(source.get("author"), dict) else {}
    from_contact = author.get("type") in {"user", "lead", "contact"}
    conversation_id = str(raw.get("id"))
    parts = raw.get("conversation_parts") if isinstance(raw.get("conversation_parts"), dict) else {}
    body = re.sub(r"<[^>]+>", " ", str(source.get("body") or "")).strip()
    contacts = ((raw.get("contacts") or {}).get("contacts") if isinstance(raw.get("contacts"), dict) else None) or []
    return Ticket(
        provider=KEY,
        external_id=conversation_id,
        kind=KIND,
        number=f"#{conversation_id}",
        subject=(raw.get("title") or re.sub(r"<[^>]+>", " ", str(source.get("subject") or "")).strip() or body[:120] or "Conversation")[:500],
        status=state,
        status_label=label,
        priority="high" if raw.get("priority") == "priority" else "normal",
        requester_email=(clean_email(author.get("email")) if from_contact else None) or email,
        requester_name=author.get("name") if from_contact else None,
        url=_url(connection, conversation_id),
        created_at=parse_time(raw.get("created_at")),
        updated_at=parse_time(raw.get("updated_at")),
        comment_count=int(parts.get("total_count")) + 1 if isinstance(parts.get("total_count"), int) else None,
        description=body[:8000] or None,
        extra={"contact_ids": [str(c.get("id")) for c in contacts if isinstance(c, dict) and c.get("id")][:5]},
    )


async def validate(values: dict[str, str]) -> dict[str, Any]:
    region = clean_region(values.get("region") or "us")
    token = (values.get("access_token") or "").strip()
    api, _ = REGIONS[region]
    response = await http("GET", f"{api}/me", headers=_headers(token))
    me = check(response, KEY) or {}
    if me.get("type") != "admin":
        raise IntegrationError("Intercom didn't accept that access token. Copy it from your app's Authentication page in the Developer Hub.", 400)
    app = me.get("app") or {}
    if app.get("region") and str(app["region"]).lower() in REGIONS and str(app["region"]).lower() != region:
        raise IntegrationError(f"This Intercom workspace is hosted in the {str(app['region']).upper()} region. Set the region to {str(app['region']).lower()}.", 422)
    return {
        "label": app.get("name") or "Intercom",
        "external_id": app.get("id_code"),
        "config": {"region": region, "app_id": app.get("id_code"), "app_name": app.get("name"), "admin_id": str(me.get("id") or ""), "admin_email": me.get("email")},
    }


async def setup(db: AsyncSession, connection: IntegrationConnection) -> None:
    connection.config = {**(connection.config or {}), "webhook_status": "manual"}


async def teardown(db: AsyncSession, connection: IntegrationConnection) -> None:
    return None


async def check_connection(db: AsyncSession, connection: IntegrationConnection) -> str:
    me = await _call(connection, "GET", "/me")
    app = me.get("app") or {}
    return f"Intercom workspace “{app.get('name') or 'Intercom'}” answered; signed in as {me.get('name') or me.get('email')}."


def options(connection: IntegrationConnection) -> dict[str, Any]:
    return {}


async def _contact_ids(connection: IntegrationConnection, email: str) -> list[str]:
    result = await _call(connection, "POST", "/contacts/search", json={"query": {"field": "email", "operator": "=", "value": email}, "pagination": {"per_page": 10}})
    return [str(c.get("id")) for c in (result or {}).get("data") or [] if isinstance(c, dict) and c.get("id")][:10]


async def contact(connection: IntegrationConnection, contact_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", contact_id or ""):
        raise IntegrationError("That isn't an Intercom contact.", 422)
    cached = cache_get(("intercom-contact", connection.id, contact_id))
    if cached is not None:
        return cached
    found = await _call(connection, "GET", f"/contacts/{contact_id}", what="that contact")
    cache_put(("intercom-contact", connection.id, contact_id), found or {}, 600)
    return found or {}


async def by_emails(db: AsyncSession, connection: IntegrationConnection, emails: list[str]) -> list[Ticket]:
    found: dict[str, Ticket] = {}
    for email in emails[:5]:
        filters: list[dict[str, Any]] = [{"field": "source.author.email", "operator": "=", "value": email}]
        filters += [{"field": "contact_ids", "operator": "=", "value": cid} for cid in await _contact_ids(connection, email)]
        result = await _call(connection, "POST", "/conversations/search", json={"query": {"operator": "OR", "value": filters[:15]}, "pagination": {"per_page": 25}})
        for raw in (result or {}).get("conversations") or []:
            if isinstance(raw, dict) and raw.get("id"):
                ticket = to_ticket(connection, raw, email=email)
                found[ticket.external_id] = ticket
    return list(found.values())


async def by_company(db: AsyncSession, connection: IntegrationConnection, name: str | None, domain: str | None) -> list[Ticket]:
    return []


async def _fill_requester(connection: IntegrationConnection, ticket: Ticket) -> None:
    if ticket.requester_email:
        return
    for contact_id in ticket.extra.get("contact_ids") or []:
        try:
            person = await contact(connection, contact_id)
        except IntegrationError:
            continue
        email = clean_email(person.get("email"))
        if email:
            ticket.requester_email, ticket.requester_name = email, person.get("name")
            return


async def fetch(db: AsyncSession, connection: IntegrationConnection, external_id: str, *, comments: bool = False) -> Ticket:
    if not (str(external_id).isascii() and str(external_id).isdigit()):
        raise IntegrationError("Intercom conversation ids are digits.", 422)
    raw = await _call(connection, "GET", f"/conversations/{external_id}", params={"display_as": "plaintext"}, what=f"conversation {external_id}")
    ticket = to_ticket(connection, raw or {})
    await _fill_requester(connection, ticket)
    return ticket


def reference(text: str) -> str | None:
    match = CONVERSATION_ID.search((text or "").strip())
    return match.group(1) if match else None


async def create(
    db: AsyncSession, connection: IntegrationConnection, *, subject: str, body: str, requester_email: str, requester_name: str | None,
    priority: str | None, public: bool, options: dict[str, Any],
) -> Ticket:
    """An email from the connected admin to the person, which starts a conversation."""

    admin_id = (connection.config or {}).get("admin_id")
    if not admin_id:
        raise IntegrationError("Reconnect Intercom so AIVA knows which admin sends messages.", 409)
    ids = await _contact_ids(connection, requester_email)
    if ids:
        person = await contact(connection, ids[0])
    else:
        person = await _call(connection, "POST", "/contacts", json={"role": "lead", "email": requester_email, **({"name": requester_name} if requester_name else {})},
                             what="create contacts")
    role = "user" if person.get("role") == "user" else "lead"
    message = await _call(
        connection, "POST", "/messages",
        json={"message_type": "email", "subject": subject, "body": body, "template": "plain", "from": {"type": "admin", "id": admin_id},
              "to": {"type": role, "id": person.get("id")}, "create_conversation_without_contact_reply": True},
        what="send messages",
    )
    conversation_id = str((message or {}).get("conversation_id") or "")
    if not conversation_id:
        raise IntegrationError("Intercom sent the message but didn't say which conversation it started. Look for it in the Intercom inbox.", 502)
    try:
        ticket = await fetch(db, connection, conversation_id)
    except IntegrationError:
        ticket = Ticket(provider=KEY, external_id=conversation_id, kind=KIND, number=f"#{conversation_id}", subject=subject, status="open", status_label="Open",
                        url=_url(connection, conversation_id), created_at=utcnow(), updated_at=utcnow())
    ticket.requester_email = ticket.requester_email or requester_email
    ticket.requester_name = ticket.requester_name or requester_name
    if priority in {"high", "urgent"}:
        try:
            await _call(connection, "PUT", f"/conversations/{conversation_id}", json={"priority": "priority"})
            ticket.priority = "high"
        except IntegrationError:
            pass
    return ticket


async def sync(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], pages: int = 2) -> tuple[list[Ticket], bool]:
    """Conversations updated since the last run. The lower bound only moves once
    every page for it has been read, so nothing is skipped whatever the order."""

    config = dict(connection.config or {})
    since = int(config.get("sync_since") or (utcnow().timestamp() - int(settings.get("backfill_days") or 30) * 86400))
    cursor = config.get("sync_after")
    newest = int(config.get("sync_newest") or since)
    tickets: list[Ticket] = []
    more = False
    lookups = 0
    for _ in range(pages):
        pagination: dict[str, Any] = {"per_page": 50}
        if cursor:
            pagination["starting_after"] = cursor
        result = await _call(connection, "POST", "/conversations/search", json={"query": {"field": "updated_at", "operator": ">", "value": since}, "pagination": pagination})
        for raw in (result or {}).get("conversations") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            ticket = to_ticket(connection, raw)
            if not ticket.requester_email and lookups < 10:
                lookups += 1
                await _fill_requester(connection, ticket)
            tickets.append(ticket)
            stamp = raw.get("updated_at")
            if isinstance(stamp, int) and stamp > newest:
                newest = stamp
        nxt = (((result or {}).get("pages") or {}).get("next") or {})
        cursor = nxt.get("starting_after") if isinstance(nxt, dict) else None
        if not cursor:
            since, newest, more = newest, newest, False
            break
        more = True
    connection.config = {**(connection.config or {}), "sync_since": since, "sync_after": cursor, "sync_newest": newest}
    return tickets, more


def verify(connection: IntegrationConnection, request: Request, body: bytes) -> bool | None:
    secret = core.secret(connection, "client_secret")
    if not secret:
        return None
    given = (request.headers.get("x-hub-signature") or "").strip()
    if not given.startswith("sha1="):
        return False
    return same(hmac_hex(secret, body, hashlib.sha1), given[5:])


def topic_of(payload: Any) -> str:
    return str(payload.get("topic") or "") if isinstance(payload, dict) else ""


def ids_from(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not topic_of(payload).startswith(CONVERSATION_TOPICS):
        return []
    item = ((payload.get("data") or {}).get("item") or {}) if isinstance(payload.get("data"), dict) else {}
    text = str(item.get("id") or "").strip()
    return [text] if text.isascii() and text.isdigit() else []


def lead_ids_from(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or topic_of(payload) not in LEAD_TOPICS:
        return []
    item = ((payload.get("data") or {}).get("item") or {}) if isinstance(payload.get("data"), dict) else {}
    text = str(item.get("id") or "").strip()
    return [text] if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text) else []


def webhook_info(connection: IntegrationConnection) -> dict[str, Any]:
    return {"mode": "manual", "status": (connection.config or {}).get("webhook_status"), "signed": bool(core.secret(connection, "client_secret"))}
