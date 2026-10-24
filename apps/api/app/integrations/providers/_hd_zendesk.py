"""Zendesk Support: tickets by requester, incremental export, webhook + trigger.

Auth: Basic ``{agent email}/token:{API token}`` against ``https://{subdomain}.zendesk.com``.
On connect AIVA creates a webhook (with a signing secret) and a trigger that calls
it when a ticket is created or updated. Requests are verified with
``X-Zendesk-Webhook-Signature`` = base64(HMAC-SHA256(secret, timestamp + body)).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._hd_common import Ticket, check, clean_email, hmac_b64, http, parse_time, same, utcnow
from app.models.integration_hub import IntegrationConnection

KEY = "zendesk"
KIND = "ticket"
SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
STATUS = {"new": "new", "open": "open", "pending": "pending", "hold": "on_hold", "solved": "solved", "closed": "closed"}
LABEL = {"new": "New", "open": "Open", "pending": "Pending", "hold": "On-hold", "solved": "Solved", "closed": "Closed"}
PRIORITY = {"low": "low", "normal": "normal", "high": "high", "urgent": "urgent"}
TICKET_ID = re.compile(r"(?:/tickets/|^#?)(\d{1,12})(?:\D|$)")
SIGNATURE_TOLERANCE = 600  # seconds


def clean_subdomain(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/")[0]
    if text.endswith(".zendesk.com"):
        text = text[: -len(".zendesk.com")]
    if not SUBDOMAIN.match(text):
        raise IntegrationError("Enter your Zendesk subdomain: the part before .zendesk.com, like acme.", 422)
    return text


def _base(config: dict[str, Any]) -> str:
    return f"https://{clean_subdomain(config.get('subdomain') or '')}.zendesk.com"


def _auth(email: str, token: str) -> tuple[str, str]:
    return (f"{email}/token", token)


def _creds(connection: IntegrationConnection) -> tuple[str, tuple[str, str]]:
    config = connection.config or {}
    email = config.get("email") or ""
    token = core.secret(connection, "api_token") or ""
    if not email or not token:
        raise IntegrationError("Zendesk isn't fully connected. Add the agent email and API token.", 409)
    return _base(config), _auth(email, token)


async def _call(connection: IntegrationConnection, method: str, path: str, *, what: str = "", **kwargs: Any) -> Any:
    base, auth = _creds(connection)
    response = await http(method, f"{base}{path}", auth=auth, headers={"Accept": "application/json"}, **kwargs)
    return check(response, KEY, connection, what=what)


def to_ticket(base: str, raw: dict[str, Any], users: dict[Any, dict[str, Any]], *, email: str | None = None) -> Ticket:
    requester = users.get(raw.get("requester_id")) or {}
    status = str(raw.get("status") or "open")
    ticket_id = str(raw.get("id"))
    return Ticket(
        provider=KEY,
        external_id=ticket_id,
        kind=KIND,
        number=f"#{ticket_id}",
        subject=str(raw.get("subject") or raw.get("raw_subject") or "")[:500],
        status=STATUS.get(status, "open"),
        status_label=LABEL.get(status, status.title()),
        priority=PRIORITY.get(str(raw.get("priority") or "")),
        requester_email=clean_email(requester.get("email")) or email,
        requester_name=requester.get("name"),
        url=f"{base}/agent/tickets/{ticket_id}",
        created_at=parse_time(raw.get("created_at")),
        updated_at=parse_time(raw.get("updated_at")),
        description=(raw.get("description") or "")[:8000] or None,
        extra={"via": ((raw.get("via") or {}).get("channel"))},
    )


def _users(payload: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    return {u.get("id"): u for u in payload.get("users") or [] if isinstance(u, dict)}


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------


async def validate(values: dict[str, str]) -> dict[str, Any]:
    subdomain = clean_subdomain(values.get("subdomain") or "")
    email = clean_email(values.get("email"))
    token = (values.get("api_token") or "").strip()
    if not email:
        raise IntegrationError("Enter the email address of the Zendesk agent the API token belongs to.", 422)
    base = f"https://{subdomain}.zendesk.com"
    response = await http("GET", f"{base}/api/v2/users/me.json", auth=_auth(email, token), headers={"Accept": "application/json"})
    if response.status_code == 404:
        raise IntegrationError(f"There's no Zendesk account at {subdomain}.zendesk.com.", 400)
    user = (check(response, KEY) or {}).get("user") or {}
    if not user.get("id") or user.get("role") not in {"agent", "admin"}:
        raise IntegrationError("Zendesk didn't accept that email and API token (check that API token access is on, and that the email is an agent's).", 400)
    return {
        "label": f"{subdomain}.zendesk.com",
        "external_id": subdomain,
        "config": {"subdomain": subdomain, "email": email, "agent_name": user.get("name"), "agent_role": user.get("role")},
    }


def _trigger_body() -> str:
    # Only the ticket id is sent; AIVA reads the ticket back from the API.
    return json.dumps({"ticket_id": "{{ticket.id}}", "updated_at": "{{ticket.updated_at_with_timestamp}}", "source": "aiva-trigger"})


async def setup(db: AsyncSession, connection: IntegrationConnection) -> None:
    """Create the webhook (with its signing secret) and the trigger that calls it."""

    url = core.webhook_url(KEY, connection.webhook_token)
    if not url:
        return
    await teardown(db, connection)
    try:
        created = await _call(
            connection, "POST", "/api/v2/webhooks", what="create webhooks (an admin's API token is needed)",
            json={"webhook": {"name": "AIVA CRM", "status": "active", "endpoint": url, "http_method": "POST", "request_format": "json",
                              "subscriptions": ["conditional_ticket_events"]}},
        )
        webhook_id = str(((created or {}).get("webhook") or {}).get("id") or "")
        if not webhook_id:
            raise IntegrationError("Zendesk didn't return the new webhook.", 502)
        secret = ((await _call(connection, "GET", f"/api/v2/webhooks/{quote(webhook_id)}/signing_secret")).get("signing_secret") or {}).get("secret")
        if secret:
            core.set_secret(connection, "webhook_secret", secret)
        trigger = await _call(
            connection, "POST", "/api/v2/triggers", what="create triggers",
            json={
                "trigger": {
                    "title": "Notify AIVA CRM of ticket changes",
                    "description": "Created by AIVA CRM so ticket changes show in AIVA. Removed when Zendesk is disconnected in AIVA.",
                    "active": True,
                    "conditions": {"all": [], "any": [{"field": "update_type", "operator": "is", "value": "Create"}, {"field": "update_type", "operator": "is", "value": "Change"}]},
                    "actions": [{"field": "notification_webhook", "value": [webhook_id, _trigger_body()]}],
                }
            },
        )
        trigger_id = str(((trigger or {}).get("trigger") or {}).get("id") or "")
        connection.config = {**(connection.config or {}), "webhook_id": webhook_id, "trigger_id": trigger_id or None, "webhook_status": "active",
                             "webhook_error": None}
        core.log(db, connection.organization_id, KEY, "webhook_created", "Created the AIVA webhook and trigger in Zendesk, so ticket changes arrive within seconds.",
                 level="success", connection=connection)
    except IntegrationError as exc:
        connection.config = {**(connection.config or {}), "webhook_status": "failed", "webhook_error": str(exc)[:300]}
        core.log(db, connection.organization_id, KEY, "webhook_failed",
                 f"Couldn't create the Zendesk webhook ({exc}). Tickets still sync every few minutes.", level="warning", connection=connection)


async def teardown(db: AsyncSession, connection: IntegrationConnection) -> None:
    config = connection.config or {}
    for kind, ident in (("triggers", config.get("trigger_id")), ("webhooks", config.get("webhook_id"))):
        if not ident:
            continue
        try:
            await _call(connection, "DELETE", f"/api/v2/{kind}/{quote(str(ident))}")
        except IntegrationError:
            pass
    connection.config = {**config, "webhook_id": None, "trigger_id": None, "webhook_status": None}
    core.set_secret(connection, "webhook_secret", None)


async def check_connection(db: AsyncSession, connection: IntegrationConnection) -> str:
    user = (await _call(connection, "GET", "/api/v2/users/me.json")).get("user") or {}
    if not user.get("id"):
        raise IntegrationError("Zendesk didn't accept the saved API token.", 400)
    return f"Signed in to {(connection.config or {}).get('subdomain')}.zendesk.com as {user.get('name')} ({user.get('role')})."


def options(connection: IntegrationConnection) -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def by_emails(db: AsyncSession, connection: IntegrationConnection, emails: list[str]) -> list[Ticket]:
    base, _ = _creds(connection)
    found: dict[str, Ticket] = {}
    for email in emails[:5]:
        payload = await _call(
            connection, "GET", "/api/v2/search.json",
            params={"query": f"type:ticket requester:{email}", "sort_by": "updated_at", "sort_order": "desc", "per_page": 25, "include": "tickets(users)"},
        )
        users = _users(payload)
        for raw in payload.get("results") or []:
            if isinstance(raw, dict) and raw.get("result_type", "ticket") == "ticket" and raw.get("id"):
                ticket = to_ticket(base, raw, users, email=email)
                found[ticket.external_id] = ticket
    return list(found.values())


async def by_company(db: AsyncSession, connection: IntegrationConnection, name: str | None, domain: str | None) -> list[Ticket]:
    return []


async def fetch(db: AsyncSession, connection: IntegrationConnection, external_id: str, *, comments: bool = False) -> Ticket:
    if not (str(external_id).isascii() and str(external_id).isdigit()):
        raise IntegrationError("Zendesk ticket numbers are digits, like 4521.", 422)
    base, _ = _creds(connection)
    payload = await _call(connection, "GET", f"/api/v2/tickets/{external_id}.json", params={"include": "users"}, what=f"ticket #{external_id}")
    ticket = to_ticket(base, payload.get("ticket") or {}, _users(payload))
    if comments:
        try:
            count = await _call(connection, "GET", f"/api/v2/tickets/{external_id}/comments/count.json")
            ticket.comment_count = int(((count or {}).get("count") or {}).get("value") or 0)
        except (IntegrationError, TypeError, ValueError):
            pass
    return ticket


def reference(text: str) -> str | None:
    match = TICKET_ID.search((text or "").strip())
    return match.group(1) if match else None


async def create(
    db: AsyncSession, connection: IntegrationConnection, *, subject: str, body: str, requester_email: str, requester_name: str | None,
    priority: str | None, public: bool, options: dict[str, Any],
) -> Ticket:
    base, _ = _creds(connection)
    ticket: dict[str, Any] = {
        "subject": subject,
        "comment": {"body": body, "public": bool(public)},
        "requester": {"email": requester_email, **({"name": requester_name} if requester_name else {})},
        "tags": ["aiva"],
    }
    if priority in PRIORITY:
        ticket["priority"] = priority
    payload = await _call(connection, "POST", "/api/v2/tickets.json", json={"ticket": ticket}, what="create tickets")
    raw = payload.get("ticket") or {}
    if not raw.get("id"):
        raise IntegrationError("Zendesk didn't return the new ticket.", 502)
    created = to_ticket(base, raw, {}, email=requester_email)
    created.requester_name = requester_name
    created.comment_count = 1
    return created


async def sync(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], pages: int = 2) -> tuple[list[Ticket], bool]:
    """Incremental cursor export (tickets changed since the last run, with requesters)."""

    base, _ = _creds(connection)
    config = dict(connection.config or {})
    tickets: list[Ticket] = []
    more = False
    for _ in range(pages):
        params: dict[str, Any] = {"include": "users"}
        if config.get("sync_cursor"):
            params["cursor"] = config["sync_cursor"]
        else:
            days = int(settings.get("backfill_days") or 30)
            params["start_time"] = int((utcnow().timestamp()) - max(days, 0) * 86400 - 60)
        payload = await _call(connection, "GET", "/api/v2/incremental/tickets/cursor.json", params=params)
        users = _users(payload)
        for raw in payload.get("tickets") or []:
            if isinstance(raw, dict) and raw.get("id") and raw.get("status") != "deleted":
                tickets.append(to_ticket(base, raw, users))
        if payload.get("after_cursor"):
            config["sync_cursor"] = payload["after_cursor"]
        more = not payload.get("end_of_stream", True)
        if not more:
            break
    connection.config = {**(connection.config or {}), "sync_cursor": config.get("sync_cursor")}
    return tickets, more


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def verify(connection: IntegrationConnection, request: Request, body: bytes) -> bool | None:
    """True/False when a signing secret is stored; None when there's nothing to check against."""

    secret = core.secret(connection, "webhook_secret")
    if not secret:
        return None
    signature = request.headers.get("x-zendesk-webhook-signature")
    stamp = request.headers.get("x-zendesk-webhook-signature-timestamp") or ""
    if not signature or not stamp:
        return False
    when = parse_time(stamp)
    if when is not None and abs((utcnow() - when).total_seconds()) > SIGNATURE_TOLERANCE:
        return False
    return same(hmac_b64(secret, stamp.encode("utf-8") + body), signature)


def ids_from(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("ticket_id")
    if raw is None and isinstance(payload.get("ticket"), dict):
        raw = payload["ticket"].get("id")
    if raw is None and isinstance(payload.get("detail"), dict):
        raw = payload["detail"].get("id")
    text = str(raw or "").strip()
    return [text] if text.isascii() and text.isdigit() else []


def webhook_info(connection: IntegrationConnection) -> dict[str, Any]:
    config = connection.config or {}
    return {"mode": "automatic", "status": config.get("webhook_status"), "error": config.get("webhook_error"), "signed": bool(core.secret(connection, "webhook_secret"))}

