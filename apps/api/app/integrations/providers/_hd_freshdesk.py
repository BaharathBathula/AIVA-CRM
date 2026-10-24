"""Freshdesk: tickets by requester or company, polling, ticket creation.

Auth: Basic ``{API key}:X`` against ``https://{domain}.freshdesk.com/api/v2``.
Freshdesk webhooks (an automation rule's "Trigger webhook" action) aren't
signed, so AIVA only takes the ticket id from them and reads the ticket back
from the API. An optional ``X-AIVA-Webhook-Secret`` header (added to the rule)
is checked when the workspace has generated one.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._hd_common import Ticket, check, clean_email, http, parse_time, same, utcnow
from app.models.integration_hub import IntegrationConnection

KEY = "freshdesk"
KIND = "ticket"
HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.freshdesk\.com$")
STATUS = {2: ("open", "Open"), 3: ("pending", "Pending"), 4: ("solved", "Resolved"), 5: ("closed", "Closed")}
PRIORITY = {1: "low", 2: "normal", 3: "high", 4: "urgent"}
PRIORITY_CODE = {"low": 1, "normal": 2, "high": 3, "urgent": 4}
TICKET_ID = re.compile(r"(?:/tickets/|^#?)(\d{1,12})(?:\D|$)")
SYNC_PAGE = 100
MAX_PAGE = 300  # Freshdesk stops paging list results here


def clean_domain(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/")[0]
    if text and "." not in text:
        text = f"{text}.freshdesk.com"
    if not HOST.match(text):
        raise IntegrationError("Enter your Freshdesk address, like acme.freshdesk.com.", 422)
    return text


def _creds(connection: IntegrationConnection) -> tuple[str, tuple[str, str]]:
    host = clean_domain((connection.config or {}).get("domain") or "")
    key = core.secret(connection, "api_key") or ""
    if not key:
        raise IntegrationError("Freshdesk isn't fully connected. Add the API key.", 409)
    return host, (key, "X")


async def _call(connection: IntegrationConnection, method: str, path: str, *, what: str = "", **kwargs: Any) -> Any:
    host, auth = _creds(connection)
    response = await http(method, f"https://{host}/api/v2{path}", auth=auth, headers={"Accept": "application/json"}, **kwargs)
    return check(response, KEY, connection, what=what)


def to_ticket(host: str, raw: dict[str, Any], statuses: dict[str, str] | None = None, *, email: str | None = None) -> Ticket:
    code = raw.get("status")
    status, label = STATUS.get(code, ("open", (statuses or {}).get(str(code)) or f"Status {code}"))
    requester = raw.get("requester") if isinstance(raw.get("requester"), dict) else {}
    ticket_id = str(raw.get("id"))
    return Ticket(
        provider=KEY,
        external_id=ticket_id,
        kind=KIND,
        number=f"#{ticket_id}",
        subject=str(raw.get("subject") or "")[:500],
        status=status,
        status_label=label,
        priority=PRIORITY.get(raw.get("priority")),
        requester_email=clean_email(requester.get("email")) or email,
        requester_name=requester.get("name"),
        url=f"https://{host}/a/tickets/{ticket_id}",
        created_at=parse_time(raw.get("created_at")),
        updated_at=parse_time(raw.get("updated_at")),
        description=(raw.get("description_text") or "")[:8000] or None,
        extra={"company_id": raw.get("company_id")},
    )


async def validate(values: dict[str, str]) -> dict[str, Any]:
    host = clean_domain(values.get("domain") or "")
    key = (values.get("api_key") or "").strip()
    response = await http("GET", f"https://{host}/api/v2/agents/me", auth=(key, "X"), headers={"Accept": "application/json"})
    if response.status_code == 404:
        raise IntegrationError(f"There's no Freshdesk account at {host}.", 400)
    agent = check(response, KEY) or {}
    contact = agent.get("contact") or {}
    statuses: dict[str, str] = {}
    try:
        fields = check(await http("GET", f"https://{host}/api/v2/ticket_fields", auth=(key, "X"), params={"type": "default_status"}), KEY)
        for item in fields if isinstance(fields, list) else []:
            for code, labels in (item.get("choices") or {}).items():
                statuses[str(code)] = (labels[0] if isinstance(labels, list) and labels else str(labels))[:60]
    except IntegrationError:
        pass
    return {
        "label": host,
        "external_id": host,
        "config": {"domain": host, "agent_name": contact.get("name"), "agent_email": contact.get("email"), "statuses": statuses},
    }


async def setup(db: AsyncSession, connection: IntegrationConnection) -> None:
    connection.config = {**(connection.config or {}), "webhook_status": "manual"}


async def teardown(db: AsyncSession, connection: IntegrationConnection) -> None:
    return None


async def check_connection(db: AsyncSession, connection: IntegrationConnection) -> str:
    agent = await _call(connection, "GET", "/agents/me")
    name = (agent.get("contact") or {}).get("name") or "the agent"
    return f"Freshdesk at {(connection.config or {}).get('domain')} answered; signed in as {name}."


def options(connection: IntegrationConnection) -> dict[str, Any]:
    return {}


def _statuses(connection: IntegrationConnection) -> dict[str, str]:
    return (connection.config or {}).get("statuses") or {}


async def _list(connection: IntegrationConnection, params: dict[str, Any], *, email: str | None = None) -> list[Ticket]:
    host, _ = _creds(connection)
    try:
        rows = await _call(connection, "GET", "/tickets", params=params)
    except IntegrationError as exc:
        # Older accounts refuse updated_since together with another filter.
        if exc.status_code == 400 and "updated_since" in params and len(params) > 2:
            rows = await _call(connection, "GET", "/tickets", params={k: v for k, v in params.items() if k != "updated_since"})
        else:
            raise
    return [to_ticket(host, raw, _statuses(connection), email=email) for raw in rows or [] if isinstance(raw, dict) and raw.get("id")]


async def by_emails(db: AsyncSession, connection: IntegrationConnection, emails: list[str]) -> list[Ticket]:
    since = (utcnow() - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found: dict[str, Ticket] = {}
    for email in emails[:5]:
        for ticket in await _list(
            connection,
            {"email": email, "updated_since": since, "order_by": "updated_at", "order_type": "desc", "per_page": 30, "include": "requester"},
            email=email,
        ):
            found[ticket.external_id] = ticket
    return list(found.values())


async def by_company(db: AsyncSession, connection: IntegrationConnection, name: str | None, domain: str | None) -> list[Ticket]:
    if not name or len(name.strip()) < 2:
        return []
    result = await _call(connection, "GET", "/companies/autocomplete", params={"name": name.strip()[:100]})
    companies = [c for c in (result or {}).get("companies") or [] if str(c.get("name") or "").casefold() == name.strip().casefold()]
    if len(companies) != 1:
        return []
    since = (utcnow() - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return await _list(
        connection,
        {"company_id": companies[0]["id"], "updated_since": since, "order_by": "updated_at", "order_type": "desc", "per_page": 30, "include": "requester"},
    )


async def fetch(db: AsyncSession, connection: IntegrationConnection, external_id: str, *, comments: bool = False) -> Ticket:
    if not (str(external_id).isascii() and str(external_id).isdigit()):
        raise IntegrationError("Freshdesk ticket numbers are digits, like 45.", 422)
    host, _ = _creds(connection)
    raw = await _call(connection, "GET", f"/tickets/{external_id}", params={"include": "requester"}, what=f"ticket #{external_id}")
    ticket = to_ticket(host, raw or {}, _statuses(connection))
    if comments:
        try:
            replies = await _call(connection, "GET", f"/tickets/{external_id}/conversations", params={"per_page": 100})
            ticket.comment_count = len(replies or []) + 1
        except IntegrationError:
            pass
    return ticket


def reference(text: str) -> str | None:
    match = TICKET_ID.search((text or "").strip())
    return match.group(1) if match else None


async def create(
    db: AsyncSession, connection: IntegrationConnection, *, subject: str, body: str, requester_email: str, requester_name: str | None,
    priority: str | None, public: bool, options: dict[str, Any],
) -> Ticket:
    host, _ = _creds(connection)
    payload: dict[str, Any] = {
        "subject": subject,
        "description": _html(body),
        "email": requester_email,
        "status": 2,
        "priority": PRIORITY_CODE.get(priority or "normal", 2),
        "tags": ["aiva"],
    }
    if requester_name:
        payload["name"] = requester_name[:255]
    raw = await _call(connection, "POST", "/tickets", json=payload, what="create tickets")
    if not (raw or {}).get("id"):
        raise IntegrationError("Freshdesk didn't return the new ticket.", 502)
    ticket = to_ticket(host, raw, _statuses(connection), email=requester_email)
    ticket.requester_name = requester_name
    ticket.comment_count = 1
    return ticket


def _html(text: str) -> str:
    from html import escape

    return "<br>".join(escape(line) for line in (text or "").splitlines()) or escape(text or "")


async def sync(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], pages: int = 2) -> tuple[list[Ticket], bool]:
    """Tickets updated since the last run (oldest first, paged)."""

    host, _ = _creds(connection)
    config = dict(connection.config or {})
    since = config.get("sync_since") or (utcnow() - timedelta(days=int(settings.get("backfill_days") or 30))).strftime("%Y-%m-%dT%H:%M:%SZ")
    page = int(config.get("sync_page") or 1)
    newest = config.get("sync_newest") or since
    tickets: list[Ticket] = []
    more = False
    for _ in range(pages):
        rows = await _call(
            connection, "GET", "/tickets",
            params={"updated_since": since, "order_by": "updated_at", "order_type": "asc", "per_page": SYNC_PAGE, "page": page, "include": "requester"},
        )
        batch = [to_ticket(host, raw, _statuses(connection)) for raw in rows or [] if isinstance(raw, dict) and raw.get("id")]
        tickets.extend(batch)
        for ticket in batch:
            if ticket.updated_at and ticket.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") > newest:
                newest = ticket.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        if len(batch) == SYNC_PAGE and page < MAX_PAGE:
            page += 1
            more = True
            continue
        # Caught up: next time start from the newest change seen.
        since, page, more = newest, 1, False
        break
    connection.config = {**(connection.config or {}), "sync_since": since, "sync_page": page, "sync_newest": newest}
    return tickets, more


def verify(connection: IntegrationConnection, request: Request, body: bytes) -> bool | None:
    secret = core.secret(connection, "webhook_secret")
    if not secret:
        return None
    return same(secret, request.headers.get("x-aiva-webhook-secret"))


def ids_from(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("ticket_id")
    if raw is None and isinstance(payload.get("freshdesk_webhook"), dict):
        raw = payload["freshdesk_webhook"].get("ticket_id")
    text = str(raw or "").strip().lstrip("#")
    return [text] if text.isascii() and text.isdigit() else []


def webhook_info(connection: IntegrationConnection) -> dict[str, Any]:
    return {"mode": "manual", "status": (connection.config or {}).get("webhook_status"), "signed": bool(core.secret(connection, "webhook_secret"))}
