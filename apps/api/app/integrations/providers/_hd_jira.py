"""Jira Service Management (Cloud): requests by customer, escalations, webhooks.

Auth: Basic ``{email}:{API token}`` against ``https://{site}.atlassian.net``.
Searches use the current JQL endpoint (``POST /rest/api/3/search/jql``) and
requests are raised on the customer's behalf through the Service Management API.
Jira's admin webhooks can carry a secret (``X-Hub-Signature: sha256=<hex>``);
either way AIVA only takes the issue id from a webhook and reads the issue back.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._hd_common import Ticket, cache_get, cache_put, check, clean_email, hmac_hex, http, parse_time, same, utcnow
from app.models.integration_hub import IntegrationConnection

KEY = "jira_service_management"
KIND = "ticket"
SITE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net$")
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9_]{0,19}-\d{1,9})\b")
PRIORITY = {"highest": "urgent", "blocker": "urgent", "critical": "urgent", "high": "high", "major": "high", "medium": "normal",
            "low": "low", "lowest": "low", "minor": "low", "trivial": "low"}
FIELDS = ["summary", "status", "priority", "created", "updated", "reporter", "project", "description"]


def clean_site(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/")[0]
    if text and "." not in text:
        text = f"{text}.atlassian.net"
    if not SITE.match(text):
        raise IntegrationError("Enter your Atlassian site, like acme.atlassian.net.", 422)
    return text


def _creds(connection: IntegrationConnection) -> tuple[str, tuple[str, str]]:
    config = connection.config or {}
    site = clean_site(config.get("site") or "")
    email = config.get("email") or ""
    token = core.secret(connection, "api_token") or ""
    if not email or not token:
        raise IntegrationError("Jira Service Management isn't fully connected. Add the email and API token.", 409)
    return site, (email, token)


async def _call(connection: IntegrationConnection, method: str, path: str, *, what: str = "", **kwargs: Any) -> Any:
    site, auth = _creds(connection)
    response = await http(method, f"https://{site}{path}", auth=auth, headers={"Accept": "application/json"}, **kwargs)
    return check(response, KEY, connection, what=what)


def adf_text(node: Any, limit: int = 8000) -> str:
    """Plain text from an Atlassian Document Format value (or a plain string)."""

    if isinstance(node, str):
        return node[:limit]
    parts: list[str] = []

    def walk(item: Any) -> None:
        if len("".join(parts)) > limit:
            return
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            for child in item.get("content") or []:
                walk(child)
            if item.get("type") in {"paragraph", "heading", "listItem"}:
                parts.append("\n")
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(node)
    return "".join(parts).strip()[:limit]


def _status(raw: dict[str, Any]) -> tuple[str, str]:
    name = str(raw.get("name") or "Open")
    category = str(((raw.get("statusCategory") or {}).get("key")) or "")
    lower = name.lower()
    if category == "done":
        return ("closed" if "close" in lower or "cancel" in lower else "solved"), name
    if category == "new":
        return "new", name
    if "wait" in lower or "pending" in lower:
        return "pending", name
    if "hold" in lower:
        return "on_hold", name
    return "open", name


def to_ticket(site: str, raw: dict[str, Any], *, email: str | None = None) -> Ticket:
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    status, label = _status(fields.get("status") or {})
    reporter = fields.get("reporter") if isinstance(fields.get("reporter"), dict) else {}
    priority = PRIORITY.get(str((fields.get("priority") or {}).get("name") or "").lower())
    key = str(raw.get("key") or raw.get("id"))
    comments = fields.get("comment") if isinstance(fields.get("comment"), dict) else None
    return Ticket(
        provider=KEY,
        external_id=str(raw.get("id")),
        kind=KIND,
        number=key,
        subject=str(fields.get("summary") or "")[:500],
        status=status,
        status_label=label,
        priority=priority,
        requester_email=clean_email(reporter.get("emailAddress")) or email,
        requester_name=reporter.get("displayName"),
        url=f"https://{site}/browse/{key}",
        created_at=parse_time(fields.get("created")),
        updated_at=parse_time(fields.get("updated")),
        comment_count=int(comments.get("total")) + 1 if comments and isinstance(comments.get("total"), int) else None,
        description=adf_text(fields.get("description")) or None,
        extra={"key": key, "project": ((fields.get("project") or {}).get("key"))},
    )


async def _desks(site: str, auth: tuple[str, str]) -> list[dict[str, Any]]:
    data = check(await http("GET", f"https://{site}/rest/servicedeskapi/servicedesk", auth=auth, params={"limit": 50}, headers={"Accept": "application/json"}), KEY)
    return [{"id": str(d.get("id")), "name": d.get("projectName"), "project_key": d.get("projectKey")} for d in (data or {}).get("values") or [] if d.get("id")]


async def _request_types(site: str, auth: tuple[str, str], desk_id: str) -> list[dict[str, Any]]:
    if not desk_id.isdigit():
        return []
    data = check(
        await http("GET", f"https://{site}/rest/servicedeskapi/servicedesk/{desk_id}/requesttype", auth=auth, params={"limit": 100}, headers={"Accept": "application/json"}),
        KEY,
    )
    return [{"id": str(t.get("id")), "name": t.get("name")} for t in (data or {}).get("values") or [] if t.get("id")][:100]


async def validate(values: dict[str, str]) -> dict[str, Any]:
    site = clean_site(values.get("site") or "")
    email = clean_email(values.get("email"))
    token = (values.get("api_token") or "").strip()
    if not email:
        raise IntegrationError("Enter the email of the Atlassian account the API token belongs to.", 422)
    auth = (email, token)
    response = await http("GET", f"https://{site}/rest/api/3/myself", auth=auth, headers={"Accept": "application/json"})
    if response.status_code == 404:
        raise IntegrationError(f"There's no Jira site at {site}.", 400)
    me = check(response, KEY) or {}
    if not me.get("accountId"):
        raise IntegrationError("Jira didn't accept that email and API token.", 400)
    desks = await _desks(site, auth)
    if not desks:
        raise IntegrationError("That account can't see any service desks. Use an agent or admin of your Jira Service Management project.", 400)
    wanted = (values.get("service_desk_id") or "").strip()
    if wanted and wanted not in {d["id"] for d in desks}:
        raise IntegrationError(f"There's no service desk with ID {wanted} that this account can see.", 422)
    desk = next((d for d in desks if d["id"] == wanted), desks[0] if len(desks) == 1 or not wanted else None) or desks[0]
    types = await _request_types(site, auth, desk["id"])
    return {
        "label": f"{site} · {desk['name']}",
        "external_id": site,
        "config": {
            "site": site, "email": email, "account_id": me.get("accountId"), "display_name": me.get("displayName"),
            "service_desks": desks[:50], "service_desk_id": desk["id"], "project_key": desk["project_key"],
            "request_types": types, "request_type_id": types[0]["id"] if types else None,
        },
    }


async def request_types_for(connection: IntegrationConnection, desk_id: str) -> list[dict[str, Any]]:
    site, auth = _creds(connection)
    return await _request_types(site, auth, desk_id)


async def refresh_options(connection: IntegrationConnection) -> dict[str, Any]:
    site, auth = _creds(connection)
    desks = await _desks(site, auth)
    desk_id = (connection.config or {}).get("service_desk_id") or (desks[0]["id"] if desks else "")
    return {"service_desks": desks[:50], "request_types": await _request_types(site, auth, str(desk_id)) if desk_id else []}


async def setup(db: AsyncSession, connection: IntegrationConnection) -> None:
    connection.config = {**(connection.config or {}), "webhook_status": "manual"}


async def teardown(db: AsyncSession, connection: IntegrationConnection) -> None:
    return None


async def check_connection(db: AsyncSession, connection: IntegrationConnection) -> str:
    me = await _call(connection, "GET", "/rest/api/3/myself")
    desk = next((d for d in (connection.config or {}).get("service_desks") or [] if d.get("id") == (connection.config or {}).get("service_desk_id")), {})
    return f"Jira answered as {me.get('displayName')}; escalations go to {desk.get('name') or 'your service desk'}."


def options(connection: IntegrationConnection) -> dict[str, Any]:
    config = connection.config or {}
    return {"service_desks": config.get("service_desks") or [], "service_desk_id": config.get("service_desk_id"),
            "request_types": config.get("request_types") or [], "request_type_id": config.get("request_type_id")}


async def account_id_for(connection: IntegrationConnection, email: str) -> str | None:
    cached = cache_get(("jira-user", connection.id, email))
    if cached is not None:
        return cached or None
    users = await _call(connection, "GET", "/rest/api/3/user/search", params={"query": email, "maxResults": 5})
    people = [u for u in users or [] if isinstance(u, dict) and u.get("accountId")]
    exact = [u for u in people if clean_email(u.get("emailAddress")) == email]
    found = (exact or (people if len(people) == 1 else []) or [{}])[0].get("accountId")
    cache_put(("jira-user", connection.id, email), found or "", 600)
    return found


async def _search(connection: IntegrationConnection, jql: str, *, limit: int = 25, token: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    body: dict[str, Any] = {"jql": jql, "fields": FIELDS, "maxResults": limit}
    if token:
        body["nextPageToken"] = token
    data = await _call(connection, "POST", "/rest/api/3/search/jql", json=body)
    return [i for i in (data or {}).get("issues") or [] if isinstance(i, dict) and i.get("id")], (data or {}).get("nextPageToken")


def _project_clause(connection: IntegrationConnection) -> str:
    key = (connection.config or {}).get("project_key")
    return f'project = "{key}" AND ' if key and re.fullmatch(r"[A-Z][A-Z0-9_]{0,19}", str(key)) else ""


async def by_emails(db: AsyncSession, connection: IntegrationConnection, emails: list[str]) -> list[Ticket]:
    site, _ = _creds(connection)
    found: dict[str, Ticket] = {}
    for email in emails[:5]:
        account_id = await account_id_for(connection, email)
        if not account_id or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", account_id):
            continue
        issues, _ = await _search(connection, f'{_project_clause(connection)}reporter = "{account_id}" ORDER BY updated DESC')
        for raw in issues:
            ticket = to_ticket(site, raw, email=email)
            found[ticket.external_id] = ticket
    return list(found.values())


async def by_company(db: AsyncSession, connection: IntegrationConnection, name: str | None, domain: str | None) -> list[Ticket]:
    return []


async def fetch(db: AsyncSession, connection: IntegrationConnection, external_id: str, *, comments: bool = False) -> Ticket:
    ref = str(external_id)
    if not (ref.isascii() and ref.isdigit()) and not ISSUE_KEY.fullmatch(ref):
        raise IntegrationError("Jira requests look like SUP-12.", 422)
    site, _ = _creds(connection)
    fields = ",".join(FIELDS + (["comment"] if comments else []))
    raw = await _call(connection, "GET", f"/rest/api/3/issue/{quote(ref)}", params={"fields": fields}, what=f"request {ref}")
    return to_ticket(site, raw or {})


def reference(text: str) -> str | None:
    text = (text or "").strip()
    match = ISSUE_KEY.search(text.upper() if "/" not in text else text)
    if match:
        return match.group(1)
    return text if text.isascii() and text.isdigit() and len(text) <= 12 else None


async def _customer_id(connection: IntegrationConnection, email: str, name: str | None, desk_id: str) -> str:
    account_id = await account_id_for(connection, email)
    if not account_id:
        created = await _call(connection, "POST", "/rest/servicedeskapi/customer", json={"email": email, "displayName": (name or email)[:255]},
                              what="add customers (a service desk admin's token is needed)")
        account_id = (created or {}).get("accountId")
        if not account_id:
            raise IntegrationError("Jira didn't return the new customer.", 502)
        cache_put(("jira-user", connection.id, email), account_id, 600)
    try:
        await _call(connection, "POST", f"/rest/servicedeskapi/servicedesk/{desk_id}/customer", json={"accountIds": [account_id]})
    except IntegrationError:
        pass  # already a customer, or the desk is open to everyone
    return account_id


async def create(
    db: AsyncSession, connection: IntegrationConnection, *, subject: str, body: str, requester_email: str, requester_name: str | None,
    priority: str | None, public: bool, options: dict[str, Any],
) -> Ticket:
    config = connection.config or {}
    desk_id = str(options.get("service_desk_id") or config.get("service_desk_id") or "")
    request_type_id = str(options.get("request_type_id") or config.get("request_type_id") or "")
    if not desk_id.isdigit() or desk_id not in {d.get("id") for d in config.get("service_desks") or []}:
        raise IntegrationError("Choose the service desk on the Jira Service Management page first.", 422)
    if not request_type_id.isdigit():
        raise IntegrationError("Choose the request type for escalations on the Jira Service Management page.", 422)
    account_id = await _customer_id(connection, requester_email, requester_name, desk_id)
    created = await _call(
        connection, "POST", "/rest/servicedeskapi/request",
        json={"serviceDeskId": desk_id, "requestTypeId": request_type_id, "raiseOnBehalfOf": account_id,
              "requestFieldValues": {"summary": subject[:255], "description": body}},
        what="raise requests",
    )
    issue_id = str((created or {}).get("issueId") or "")
    if not issue_id:
        raise IntegrationError("Jira didn't return the new request.", 502)
    ticket = await fetch(db, connection, issue_id, comments=True)
    ticket.requester_email = ticket.requester_email or requester_email
    ticket.requester_name = ticket.requester_name or requester_name
    return ticket


async def sync(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], pages: int = 2) -> tuple[list[Ticket], bool]:
    """Requests updated since the last run. JQL relative times ("-90m") avoid the
    Jira user's time zone; a margin covers clock drift."""

    site, _ = _creds(connection)
    config = dict(connection.config or {})
    started = config.get("sync_started") or utcnow().isoformat()
    since = parse_time(config.get("sync_since")) or (utcnow() - timedelta(days=int(settings.get("backfill_days") or 30)))
    minutes = int((utcnow() - since).total_seconds() // 60) + 5
    token = config.get("sync_token")
    tickets: list[Ticket] = []
    more = False
    for _ in range(pages):
        issues, token = await _search(connection, f"{_project_clause(connection)}updated >= -{minutes}m ORDER BY updated ASC", limit=50, token=token)
        tickets.extend(to_ticket(site, raw) for raw in issues)
        if not token:
            more = False
            break
        more = True
    if more:
        connection.config = {**(connection.config or {}), "sync_token": token, "sync_started": started}
    else:
        connection.config = {**(connection.config or {}), "sync_token": None, "sync_started": None, "sync_since": started}
    return tickets, more


def verify(connection: IntegrationConnection, request: Request, body: bytes) -> bool | None:
    secret = core.secret(connection, "webhook_secret")
    given = (request.headers.get("x-hub-signature") or "").strip()
    if not secret:
        return None
    if not given:
        return False
    if not given.startswith("sha256="):
        return False
    return same(hmac_hex(secret, body, hashlib.sha256), given[7:])


def ids_from(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
    text = str(issue.get("id") or "").strip()
    return [text] if text.isascii() and text.isdigit() else []


def webhook_info(connection: IntegrationConnection) -> dict[str, Any]:
    return {"mode": "manual", "status": (connection.config or {}).get("webhook_status"), "signed": bool(core.secret(connection, "webhook_secret"))}
