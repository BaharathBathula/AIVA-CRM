"""Shared plumbing for the help desk integrations (Zendesk, Freshdesk, Intercom,
Help Scout, Jira Service Management).

* ``Ticket``: one ticket or conversation in a common shape, whichever desk it
  came from. Desk adapters (``_hd_<desk>.py``) turn provider JSON into tickets.
* ``store``: saves a ticket as an ``IntegrationObject`` matched to the AIVA
  contact, lead and account it belongs to (by the requester's email, and the
  account by the email's company domain), mirrors status changes onto any AIVA
  case it's linked to, and can open an AIVA case for new tickets.
* ``http``: one HTTP helper so failures read well, 429s carry Retry-After and
  tests can stand in for every desk.

Ticket contents are customer data: they're stored for display on records, but
never written to logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.models.lead import Lead
from app.services.enrichment import FREE_EMAIL_DOMAINS, email_domain

logger = logging.getLogger("aiva.integrations.helpdesk")

KEYS = ("zendesk", "freshdesk", "intercom", "help_scout", "jira_service_management")
NAMES = {
    "zendesk": "Zendesk",
    "freshdesk": "Freshdesk",
    "intercom": "Intercom",
    "help_scout": "Help Scout",
    "jira_service_management": "Jira Service Management",
}
# Common statuses (the desk's own wording is kept in data.status_label).
STATUSES = ("new", "open", "pending", "on_hold", "solved", "closed")
OPEN_STATUSES = {"new", "open", "pending", "on_hold"}
DONE_STATUSES = {"solved", "closed"}
PRIORITIES = ("low", "normal", "high", "urgent")
CASE_PRIORITY = {"low": "low", "normal": "medium", "high": "high", "urgent": "urgent"}
CASE_PRIORITY_TO_TICKET = {"low": "low", "medium": "normal", "high": "high", "urgent": "urgent"}
TIMEOUT = 20.0

DEFAULT_SETTINGS: dict[str, Any] = {
    "match_accounts_by_domain": True,
    "create_cases": False,
    "sync_case_status": False,
    "notify_customer": False,
    "backfill_days": 30,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def name_of(key: str) -> str:
    return NAMES.get(key, key)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class RateLimited(IntegrationError):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message, 429)
        self.retry_after = retry_after


async def http(method: str, url: str, *, timeout: float = TIMEOUT, **kwargs: Any) -> httpx.Response:
    """Call a desk's API. Network failures become a friendly IntegrationError."""

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            return await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Network error calling %s: %r", url.split("?")[0], exc)
        raise IntegrationError("Couldn't reach the help desk. Try again in a moment.", 502) from exc


def retry_after(response: httpx.Response, default: int = 60) -> int:
    raw = response.headers.get("Retry-After") or response.headers.get("retry-after") or ""
    try:
        return max(1, min(int(float(raw)), 3600))
    except ValueError:
        return default


def check(response: httpx.Response, key: str, connection: IntegrationConnection | None = None, *, what: str = "") -> Any:
    """JSON body of a successful response, or an IntegrationError people can act on."""

    name = name_of(key)
    if response.status_code == 429:
        wait = retry_after(response)
        raise RateLimited(f"{name} asked AIVA to slow down. Try again in {wait} seconds.", wait)
    if response.status_code in (401, 403):
        message = (
            f"{name} didn't accept the saved credentials. Check them on the {name} integration page."
            if response.status_code == 401
            else f"{name} refused: the connected user doesn't have permission{f' to {what}' if what else ''}."
        )
        if connection is not None and response.status_code == 401:
            connection.status = "error"
            connection.last_error = message
        raise IntegrationError(message, 400)
    if response.status_code == 404:
        raise IntegrationError(f"{name} couldn't find {what or 'that'}.", 404)
    if response.status_code >= 400:
        detail = _provider_message(response)
        raise IntegrationError(f"{name} said: {detail}" if detail else f"{name} answered {response.status_code}.", 502 if response.status_code >= 500 else 400)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def _provider_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200].strip()
    if isinstance(data, dict):
        for key in ("description", "message", "error_description", "error", "errorMessage"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value[:300]
        errors = data.get("errors") or data.get("errorMessages") or data.get("details")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return str(first.get("message") or first.get("description") or first.get("code") or first)[:300]
            return str(first)[:300]
        if isinstance(errors, dict) and errors:
            return "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])[:300]
    return ""


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


def hmac_b64(secret: str, payload: bytes, digest: Any = hashlib.sha256) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), payload, digest).digest()).decode("ascii")


def hmac_hex(secret: str, payload: bytes, digest: Any = hashlib.sha256) -> str:
    return hmac.new(secret.encode("utf-8"), payload, digest).hexdigest()


def same(expected: str, given: str | None) -> bool:
    """Constant-time comparison of two signatures (as bytes, so odd input can't raise)."""

    if not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.strip().encode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


@dataclass
class Ticket:
    provider: str
    external_id: str
    kind: str = "ticket"  # ticket | conversation
    number: str = ""  # what people call it: "#4521", "SUP-12"
    subject: str = ""
    status: str = "open"  # one of STATUSES
    status_label: str = ""
    priority: str | None = None  # one of PRIORITIES
    requester_email: str | None = None
    requester_name: str | None = None
    organization: str | None = None
    url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    comment_count: int | None = None
    description: str | None = None  # the first message, for cases opened from tickets
    extra: dict[str, Any] = field(default_factory=dict)


def parse_time(value: Any) -> datetime | None:
    """ISO 8601 strings, Jira's "2026-01-02T03:04:05.000+0000" and Unix seconds."""

    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return parse_time(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":" and text[-4:].isdigit():
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


_EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def clean_email(value: Any) -> str | None:
    """A plain address, lower-cased; anything unusual (quotes, spaces, brackets) is
    refused so it can't change the meaning of a desk's search query."""

    text = str(value or "").strip().lower()
    if len(text) > 254 or not _EMAIL.match(text):
        return None
    return text


def ticket_out(row: IntegrationObject) -> dict[str, Any]:
    data = row.data or {}
    return {
        "id": str(row.id),
        "provider": row.provider,
        "provider_name": name_of(row.provider),
        "kind": row.object_type,
        "external_id": row.external_id,
        "number": data.get("number") or row.external_id,
        "subject": row.title or "(no subject)",
        "status": row.status or "open",
        "status_label": data.get("status_label") or (row.status or "open").replace("_", " "),
        "priority": row.priority,
        "requester_email": row.email,
        "requester_name": data.get("requester_name"),
        "url": row.url,
        "comment_count": data.get("comment_count"),
        "created_at": row.occurred_at,
        "updated_at": row.external_updated_at or row.occurred_at,
        "contact_id": str(row.contact_id) if row.contact_id else None,
        "lead_id": str(row.lead_id) if row.lead_id else None,
        "account_id": str(row.account_id) if row.account_id else None,
        "case_id": str(row.record_id) if row.record_type == "case" and row.record_id else None,
        "is_open": (row.status or "open") in OPEN_STATUSES,
    }


async def match(db: AsyncSession, organization_id: uuid.UUID, email: str | None, *, by_domain: bool = True) -> dict[str, uuid.UUID | None]:
    """The AIVA contact, lead and account a requester email belongs to."""

    out: dict[str, uuid.UUID | None] = {"contact_id": None, "lead_id": None, "account_id": None}
    if not email:
        return out
    contact = (
        await db.execute(
            select(Contact.id, Contact.account_id)
            .where(Contact.organization_id == organization_id, func.lower(Contact.email) == email, Contact.is_archived.is_(False))
            .order_by(Contact.created_at)
            .limit(1)
            .execution_options(skip_visibility=True)
        )
    ).first()
    if contact is not None:
        out["contact_id"], out["account_id"] = contact[0], contact[1]
    else:
        lead = (
            await db.execute(
                select(Lead.id)
                .where(Lead.organization_id == organization_id, func.lower(Lead.email) == email, Lead.converted_at.is_(None))
                .order_by(Lead.created_at.desc())
                .limit(1)
                .execution_options(skip_visibility=True)
            )
        ).scalar_one_or_none()
        out["lead_id"] = lead
    if out["account_id"] is None and by_domain:
        domain = email_domain(email)
        if domain and domain not in FREE_EMAIL_DOMAINS:
            accounts = (
                await db.execute(
                    select(Account.id)
                    .where(Account.organization_id == organization_id, func.lower(Account.domain) == domain, Account.is_archived.is_(False))
                    .limit(2)
                    .execution_options(skip_visibility=True)
                )
            ).scalars().all()
            if len(accounts) == 1:
                out["account_id"] = accounts[0]
    return out


async def find_object(db: AsyncSession, organization_id: uuid.UUID, key: str, kind: str, external_id: str) -> IntegrationObject | None:
    return (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == organization_id,
                IntegrationObject.provider == key,
                IntegrationObject.object_type == kind,
                IntegrationObject.external_parent == "",
                IntegrationObject.external_id == external_id,
            )
        )
    ).scalar_one_or_none()


async def settings_for(db: AsyncSession, organization_id: uuid.UUID, key: str) -> dict[str, Any]:
    return {**DEFAULT_SETTINGS, **{k: v for k, v in (await core.workspace_config(db, organization_id, key)).items() if v is not None}}


async def store(
    db: AsyncSession,
    connection: IntegrationConnection,
    ticket: Ticket,
    *,
    settings: dict[str, Any] | None = None,
    created_from_aiva: bool = False,
    allow_new_case: bool = True,
) -> tuple[IntegrationObject, bool]:
    """Save (or update) one ticket. Returns (row, created). Linked cases hear about
    status changes; with ``create_cases`` on, a new ticket opens an AIVA case."""

    org = connection.organization_id
    settings = settings if settings is not None else await settings_for(db, org, ticket.provider)
    row = await find_object(db, org, ticket.provider, ticket.kind, ticket.external_id)
    created = row is None
    previous_status = None if row is None else row.status
    email = clean_email(ticket.requester_email)
    if row is None:
        row = IntegrationObject(
            id=uuid.uuid4(),
            organization_id=org,
            provider=ticket.provider,
            object_type=ticket.kind,
            external_id=ticket.external_id[:200],
            external_parent="",
            data={},
        )
        db.add(row)
    data = dict(row.data or {})
    data.update(
        {
            "number": ticket.number or ticket.external_id,
            "status_label": (ticket.status_label or ticket.status)[:80],
            "requester_name": (ticket.requester_name or "")[:200] or data.get("requester_name"),
            "organization": (ticket.organization or "")[:200] or data.get("organization"),
        }
    )
    if ticket.comment_count is not None:
        data["comment_count"] = int(ticket.comment_count)
    if created_from_aiva:
        data["created_from_aiva"] = True
    data.update({k: v for k, v in ticket.extra.items() if k not in data or v is not None})
    row.data = data
    row.title = (ticket.subject or "")[:500] or None
    row.status = ticket.status if ticket.status in STATUSES else "open"
    row.priority = ticket.priority if ticket.priority in PRIORITIES else None
    row.url = (ticket.url or "")[:1000] or None
    row.occurred_at = ticket.created_at or row.occurred_at or utcnow()
    row.external_updated_at = ticket.updated_at or ticket.created_at or row.external_updated_at
    if email and (created or email != row.email or row.contact_id is None and row.lead_id is None):
        found = await match(db, org, email, by_domain=bool(settings.get("match_accounts_by_domain", True)))
        row.contact_id = found["contact_id"] or row.contact_id
        row.lead_id = found["lead_id"] if found["contact_id"] is None else None
        row.account_id = found["account_id"] or row.account_id
    row.email = email or row.email
    await db.flush()

    link = await case_link_for_ticket(db, org, ticket.provider, ticket.external_id)
    if link is not None:
        if row.record_type is None:
            row.record_type, row.record_id = "case", link.record_id
        if previous_status is not None and previous_status != row.status:
            await mirror_status(db, link, row, previous_status, settings)
        link.data = {**(link.data or {}), "status": row.status, "status_label": data["status_label"], "comment_count": data.get("comment_count")}
        link.last_synced_at = utcnow()
    elif created and allow_new_case and settings.get("create_cases") and not data.get("created_from_aiva") and row.status in OPEN_STATUSES:
        since = parse_time(settings.get("create_cases_since"))
        if since is None or (ticket.created_at is not None and ticket.created_at >= since):
            await open_case(db, connection, row, ticket)
    return row, created


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def case_link_for_ticket(db: AsyncSession, organization_id: uuid.UUID, key: str, external_id: str) -> IntegrationRecordLink | None:
    return (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id,
                IntegrationRecordLink.provider == key,
                IntegrationRecordLink.external_type == "ticket",
                IntegrationRecordLink.external_parent == "",
                IntegrationRecordLink.external_id == external_id,
                IntegrationRecordLink.record_type == "case",
            )
        )
    ).scalar_one_or_none()


def _label(row: IntegrationObject) -> str:
    number = str((row.data or {}).get("number") or row.external_id)
    noun = "conversation" if row.object_type == "conversation" else "ticket"
    return f"{name_of(row.provider)} {noun} {'#' + number if number.isdigit() else number}"


async def mirror_status(db: AsyncSession, link: IntegrationRecordLink, row: IntegrationObject, previous: str | None, settings: dict[str, Any]) -> None:
    """Note the ticket's new status on the case (internal comment); optionally resolve the case."""

    from app.models.platform import Case, CaseComment

    case = await db.get(Case, link.record_id)
    if case is None or case.organization_id != link.organization_id:
        return
    label = (row.data or {}).get("status_label") or row.status
    db.add(
        CaseComment(
            id=uuid.uuid4(),
            organization_id=case.organization_id,
            case_id=case.id,
            author_user_id=None,
            body=f"{_label(row)} is now {label}" + (f" (was {previous.replace('_', ' ')})." if previous else "."),
            is_internal=True,
        )
    )
    if settings.get("sync_case_status") and row.status in DONE_STATUSES and case.status in {"new", "open", "pending", "on_hold"}:
        case.status = "resolved"
        case.resolved_at = case.resolved_at or utcnow()
        db.add(
            CaseComment(
                id=uuid.uuid4(), organization_id=case.organization_id, case_id=case.id, author_user_id=None,
                body=f"Resolved because {_label(row)} was {label.lower()}.", is_internal=True,
            )
        )


async def open_case(db: AsyncSession, connection: IntegrationConnection, row: IntegrationObject, ticket: Ticket) -> None:
    """An AIVA case for a new ticket (when the workspace asked for it)."""

    from app.api.routes.service import _apply_sla, _policy_for  # noqa: PLC0415 - reuse the SLA rules
    from app.models.platform import Case

    org = connection.organization_id
    number = (await db.execute(select(func.coalesce(func.max(Case.number), 1000)).where(Case.organization_id == org))).scalar_one() + 1
    priority = CASE_PRIORITY.get(row.priority or "normal", "medium")
    now = utcnow()
    channel = {"intercom": "chat", "jira_service_management": "portal"}.get(row.provider, "email")
    description = "\n\n".join(
        part for part in (
            (ticket.description or "").strip()[:8000] or None,
            f"Opened from {_label(row)}" + (f": {row.url}" if row.url else "."),
        ) if part
    )
    case = Case(
        id=uuid.uuid4(),
        organization_id=org,
        number=number,
        subject=(row.title or f"{_label(row)}")[:250],
        description=description or None,
        status="new",
        priority=priority,
        channel=channel,
        contact_id=row.contact_id,
        account_id=row.account_id,
        created_by_user_id=None,
        linked_article_ids=[],
        tags=[name_of(row.provider).lower()],
        custom_fields={"_helpdesk": {"provider": row.provider, "id": row.external_id}, "_requester": {"name": ticket.requester_name, "email": row.email}},
    )
    case.created_at = now
    _apply_sla(case, await _policy_for(db, org, priority), now)
    db.add(case)
    await db.flush()
    db.add(
        IntegrationRecordLink(
            id=uuid.uuid4(), organization_id=org, provider=row.provider, record_type="case", record_id=case.id,
            external_type="ticket", external_id=row.external_id, external_parent="", external_url=row.url,
            status="linked", data={"origin": "ticket", "status": row.status, "status_label": (row.data or {}).get("status_label")},
            last_synced_at=now,
        )
    )
    row.record_type, row.record_id = "case", case.id
    core.log(db, org, row.provider, "case_opened", f"Opened case CS-{number} for {_label(row)}.", level="success", connection=connection)


def timeline(db: AsyncSession, row: IntegrationObject, subject: str, user_id: uuid.UUID | None, *, direction: str = "outbound") -> None:
    """A line on the contact / lead / account timeline (no ticket body)."""

    if not (row.contact_id or row.lead_id or row.account_id):
        return
    db.add(
        Activity(
            id=uuid.uuid4(),
            organization_id=row.organization_id,
            contact_id=row.contact_id,
            lead_id=row.lead_id,
            account_id=row.account_id,
            created_by_user_id=user_id,
            activity_type="system",
            subject=subject[:300],
            body=None,
            direction=direction,
            occurred_at=utcnow(),
            external_id=f"{row.provider}:{row.external_id}"[:500],
            activity_metadata={"integration": row.provider, "ticket_id": row.external_id, "url": row.url},
        )
    )


# ---------------------------------------------------------------------------
# Short-lived caches (live lookups for record pages, desk options)
# ---------------------------------------------------------------------------

_CACHE: dict[Any, tuple[float, Any]] = {}
_CACHE_LIMIT = 5000


def cache_get(key: Any) -> Any:
    found = _CACHE.get(key)
    if found and found[0] > time.monotonic():
        return found[1]
    _CACHE.pop(key, None)
    return None


def cache_put(key: Any, value: Any, seconds: float) -> None:
    if len(_CACHE) > _CACHE_LIMIT:
        now = time.monotonic()
        for stale in [k for k, (expires, _) in _CACHE.items() if expires <= now]:
            _CACHE.pop(stale, None)
        if len(_CACHE) > _CACHE_LIMIT:
            _CACHE.clear()
    _CACHE[key] = (time.monotonic() + seconds, value)


def cache_drop(prefix: Any) -> None:
    for key in [k for k in _CACHE if isinstance(k, tuple) and k[: len(prefix)] == prefix]:
        _CACHE.pop(key, None)


# Inbound webhooks that make AIVA call the desk back are rate limited per connection.
_WEBHOOK_HITS: dict[uuid.UUID, list[float]] = {}


def webhook_allowed(connection_id: uuid.UUID, per_minute: int = 240) -> bool:
    now = time.monotonic()
    hits = [t for t in _WEBHOOK_HITS.get(connection_id, []) if now - t < 60]
    if len(hits) >= per_minute:
        _WEBHOOK_HITS[connection_id] = hits
        return False
    hits.append(now)
    _WEBHOOK_HITS[connection_id] = hits
    if len(_WEBHOOK_HITS) > 5000:
        _WEBHOOK_HITS.clear()
    return True


def backoff(connection: IntegrationConnection, seconds: int) -> None:
    connection.config = {**(connection.config or {}), "backoff_until": (utcnow() + timedelta(seconds=seconds)).isoformat()}


def backing_off(connection: IntegrationConnection) -> bool:
    until = parse_time((connection.config or {}).get("backoff_until"))
    return bool(until and until > utcnow())
