"""Help desks: Zendesk, Freshdesk, Intercom, Help Scout and Jira Service Management.

Every desk works the same way in AIVA:

* **On records.** A contact's, lead's or account's tickets (or conversations) from
  every connected desk, looked up live by the person's email (cached briefly) and
  kept as ``IntegrationObject`` rows matched to the contact, lead and account.
* **In the background.** ``run_due`` reads what changed since the last run (a
  cursor per connection, a few pages per run, a time budget per run, Retry-After
  honoured), so tickets also show up on records nobody opened.
* **Webhooks.** Zendesk and Help Scout webhooks are registered by AIVA and signed;
  Intercom's and Jira's are signed with a secret the admin sets; Freshdesk's
  aren't signed. Whatever the desk, AIVA only takes ids from a webhook and reads
  the ticket back from the desk's API before changing anything.
* **Cases.** An AIVA case can be escalated to (or linked with) a ticket; the
  ticket's status changes are noted on the case, and optionally resolve it.
  Optionally, new tickets open AIVA cases (only tickets created after that was
  switched on, never tickets AIVA created itself).

The desk adapters live in ``_hd_<desk>.py``; ``_hd_common`` has the shared parts.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import timedelta
from types import ModuleType
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.catalog import INTEGRATIONS
from app.integrations.core import IntegrationError
from app.integrations.providers import _hd_common as common
from app.integrations.providers import _hd_freshdesk, _hd_helpscout, _hd_intercom, _hd_jira, _hd_zendesk
from app.integrations.providers._hd_common import KEYS, RateLimited, Ticket, name_of, settings_for, store, ticket_out, utcnow
from app.models.contact import Contact
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink

logger = logging.getLogger("aiva.integrations.helpdesk")

ADAPTERS: dict[str, ModuleType] = {
    "zendesk": _hd_zendesk,
    "freshdesk": _hd_freshdesk,
    "intercom": _hd_intercom,
    "help_scout": _hd_helpscout,
    "jira_service_management": _hd_jira,
}
FEATURE = "helpdesk_sync"
LIVE_SECONDS = 120  # a record's tickets are looked up live at most this often
REFRESH_SECONDS = 15  # ...and at most this often when someone presses Refresh
LINK_SECONDS = 60  # linked tickets on a case are re-read at most this often
SYNC_EVERY = timedelta(minutes=5)
RUN_BUDGET_SECONDS = 25.0
RUN_MAX_CONNECTIONS = 6
WEBHOOK_MAX_BYTES = 512 * 1024
WEBHOOK_MAX_IDS = 10
BOOL_SETTINGS = ("match_accounts_by_domain", "create_cases", "sync_case_status", "notify_customer")


def adapter(key: str) -> ModuleType:
    found = ADAPTERS.get(key)
    if found is None:
        raise IntegrationError("That isn't a help desk integration.", 404)
    return found


# ===========================================================================
# Hub hooks
# ===========================================================================


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    return await adapter(key).validate(values)


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    config = dict(connection.config or {})
    # A different desk account than before starts its sync from scratch.
    if config.get("sync_account") != connection.external_account_id:
        for name in ("sync_cursor", "sync_since", "sync_page", "sync_newest", "sync_after", "sync_token", "sync_started", "sync_more", "backoff_until"):
            config.pop(name, None)
        config["sync_account"] = connection.external_account_id
    connection.config = config
    await adapter(key).setup(db, connection)
    common.cache_drop(("hd-live", connection.organization_id))


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await adapter(key).teardown(db, connection)
    common.cache_drop(("hd-live", connection.organization_id))


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    common.cache_drop(("hd-live", organization_id))


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    return await adapter(key).check_connection(db, connection)


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    org = principal.organization_id
    current = await settings_for(db, org, key)
    cleaned: dict[str, Any] = {}
    for name in BOOL_SETTINGS:
        if name in config:
            cleaned[name] = bool(config.get(name))
    if cleaned.get("create_cases") and not current.get("create_cases"):
        # Only tickets created from now on open cases (no flood from the backlog).
        cleaned["create_cases_since"] = utcnow().isoformat()
    if "backfill_days" in config:
        try:
            days = int(config.get("backfill_days"))
        except (TypeError, ValueError) as exc:
            raise IntegrationError("Sync history is a number of days between 0 and 365.", 422) from exc
        if not 0 <= days <= 365:
            raise IntegrationError("Sync history is a number of days between 0 and 365.", 422)
        cleaned["backfill_days"] = days
    connection = await core.connection_for(db, org, key)
    if key == "intercom":
        if "create_leads" in config:
            cleaned["create_leads"] = bool(config.get("create_leads"))
        if "lead_owner_id" in config:
            from app.integrations.providers import lead_capture

            owner = config.get("lead_owner_id")
            valid = await lead_capture.valid_owner(db, org, owner) if owner else None
            if owner and valid is None:
                raise IntegrationError("Choose someone in this workspace to own new leads.", 422)
            cleaned["lead_owner_id"] = str(valid) if valid else None
    if key == "help_scout" and "default_mailbox_id" in config:
        wanted = str(config.get("default_mailbox_id") or "")
        known = {str(m.get("id")) for m in ((connection.config or {}).get("mailboxes") if connection else None) or []}
        if wanted and wanted not in known:
            raise IntegrationError("Pick one of the Help Scout mailboxes AIVA can see.", 422)
        cleaned["default_mailbox_id"] = wanted or None
    if key == "jira_service_management" and ("service_desk_id" in config or "request_type_id" in config):
        if connection is None:
            raise IntegrationError("Connect Jira Service Management first.", 409)
        desks = (connection.config or {}).get("service_desks") or []
        desk_id = str(config.get("service_desk_id") or (connection.config or {}).get("service_desk_id") or "")
        desk = next((d for d in desks if d.get("id") == desk_id), None)
        if desk is None:
            raise IntegrationError("Pick one of the service desks AIVA can see.", 422)
        types = (connection.config or {}).get("request_types") or []
        if desk_id != (connection.config or {}).get("service_desk_id"):
            types = await _hd_jira.request_types_for(connection, desk_id)
        type_id = str(config.get("request_type_id") or "")
        if type_id and type_id not in {t.get("id") for t in types}:
            type_id = ""
        cleaned.update(service_desk_id=desk_id, project_key=desk.get("project_key"), request_types=types,
                       request_type_id=type_id or (types[0]["id"] if types else None))
    return cleaned


def _setting_keys(key: str) -> tuple[str, ...]:
    extra = {
        "intercom": ("create_leads", "lead_owner_id"),
        "help_scout": ("default_mailbox_id",),
        "jira_service_management": ("service_desk_id", "request_type_id"),
    }.get(key, ())
    return (*BOOL_SETTINGS, "backfill_days", *extra)


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    mod = adapter(key)
    connection = await core.connection_for(db, org, key)
    settings = await settings_for(db, org, key)
    counts = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(IntegrationObject.status.in_(tuple(common.OPEN_STATUSES))),
                func.count().filter(or_(IntegrationObject.contact_id.is_not(None), IntegrationObject.lead_id.is_not(None), IntegrationObject.account_id.is_not(None))),
                func.count().filter(IntegrationObject.record_type == "case"),
            ).where(IntegrationObject.organization_id == org, IntegrationObject.provider == key)
        )
    ).one()
    out: dict[str, Any] = {
        "kind": mod.KIND,
        "settings": {name: settings.get(name) for name in _setting_keys(key)},
        "stats": {"total": counts[0], "open": counts[1], "matched": counts[2], "cases": counts[3]},
    }
    if connection is None:
        return out
    config = connection.config or {}
    out.update(
        {
            "webhook_url": core.webhook_url(key, connection.webhook_token),
            "webhook": {**mod.webhook_info(connection), "last_received_at": config.get("webhook_last_at")},
            "sync": {"last_sync_at": connection.last_sync_at, "catching_up": bool(config.get("sync_more")), "backoff_until": config.get("backoff_until")},
            "options": mod.options(connection),
            "account": {k: config.get(k) for k in ("subdomain", "domain", "site", "agent_name", "agent_email", "app_name", "region", "user_email", "display_name", "email")
                        if config.get(k)},
        }
    )
    if principal.can("admin.settings"):
        rows = (
            await db.execute(
                select(IntegrationObject)
                .where(IntegrationObject.organization_id == org, IntegrationObject.provider == key)
                .order_by(IntegrationObject.external_updated_at.desc().nulls_last())
                .limit(10)
            )
        ).scalars().all()
        out["recent"] = [ticket_out(r) for r in rows]
    return out


# ===========================================================================
# Inbound webhooks
# ===========================================================================


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    mod = adapter(key)
    if request.method != "POST":
        return JSONResponse({"ok": True, "message": f"AIVA receives {name_of(key)} updates here (POST)."})
    declared = request.headers.get("content-length") or ""
    if declared.isascii() and declared.isdigit() and int(declared) > WEBHOOK_MAX_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    body = await request.body()
    if len(body) > WEBHOOK_MAX_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    org = connection.organization_id
    verified = mod.verify(connection, request, body)
    if verified is False:
        core.log(db, org, key, "webhook_rejected", f"Rejected a {name_of(key)} webhook with a missing or wrong signature.", level="warning", connection=connection)
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": "expected JSON"}, status_code=400)
    ids = list(dict.fromkeys(mod.ids_from(payload)))[:WEBHOOK_MAX_IDS]
    lead_ids = list(dict.fromkeys(getattr(mod, "lead_ids_from", lambda _p: [])(payload)))[:WEBHOOK_MAX_IDS]
    connection.config = {**(connection.config or {}), "webhook_last_at": utcnow().isoformat(), "webhook_status": "active"}
    if not ids and not lead_ids:
        return JSONResponse({"ok": True, "ignored": "nothing to update"})
    if not common.webhook_allowed(connection.id):
        return JSONResponse({"error": "too many requests"}, status_code=429, headers={"Retry-After": "60"})
    settings = await settings_for(db, org, key)
    updated = 0
    for external_id in ids:
        try:
            ticket = await mod.fetch(db, connection, external_id)
        except RateLimited as exc:
            common.backoff(connection, exc.retry_after)
            break  # the next sync catches up
        except IntegrationError as exc:
            if exc.status_code != 404:
                core.log(db, org, key, "webhook_read_failed", f"A {name_of(key)} update arrived, but reading it back failed: {exc}", level="warning", connection=connection)
            continue
        if not _in_scope(connection, ticket):
            continue
        row, created = await store(db, connection, ticket, settings=settings)
        updated += 1
        if created and ticket.created_at and utcnow() - ticket.created_at < timedelta(days=1):
            noun = "conversation" if row.object_type == "conversation" else "ticket"
            common.timeline(db, row, f"New {name_of(key)} {noun} {ticket.number}: {ticket.subject}", None, direction="inbound")
    if lead_ids and settings.get("create_leads"):
        updated += await _leads_from_intercom(db, connection, lead_ids, settings)
    return JSONResponse({"ok": True, "updated": updated})


def _in_scope(connection: IntegrationConnection, ticket: Ticket) -> bool:
    if ticket.provider == "jira_service_management":
        project = (connection.config or {}).get("project_key")
        return not project or ticket.extra.get("project") in (None, project)
    return True


async def _leads_from_intercom(db: AsyncSession, connection: IntegrationConnection, contact_ids: list[str], settings: dict[str, Any]) -> int:
    from app.integrations.providers import lead_capture

    org = connection.organization_id
    try:
        await lead_capture.prepare(db, org, FEATURE)
    except IntegrationError as exc:
        core.log(db, org, "intercom", "lead_skipped", str(exc), level="warning", connection=connection)
        return 0
    owner = await lead_capture.valid_owner(db, org, settings.get("lead_owner_id")) if settings.get("lead_owner_id") else None
    made = 0
    for contact_id in contact_ids:
        try:
            person = await _hd_intercom.contact(connection, contact_id)
        except IntegrationError:
            continue
        email = common.clean_email(person.get("email"))
        if person.get("role") != "lead" or not email:
            continue
        location = person.get("location") if isinstance(person.get("location"), dict) else {}
        companies = ((person.get("companies") or {}).get("data") if isinstance(person.get("companies"), dict) else None) or []
        answers = [
            lead_capture.Answer("email", "Email", email),
            lead_capture.Answer("full_name", "Name", str(person.get("name") or "")),
            lead_capture.Answer("phone", "Phone", str(person.get("phone") or "")),
        ]
        if companies and isinstance(companies[0], dict) and companies[0].get("name"):
            answers.append(lead_capture.Answer("company_name", "Company", str(companies[0]["name"])))
        capture = lead_capture.Capture(
            provider="intercom", lead_source="Intercom", answers=[a for a in answers if a.value], owner_user_id=owner, on_duplicate="skip",
            attribution={"intercom_contact_url": f"{_hd_intercom._hosts(connection)[1]}/a/apps/{(connection.config or {}).get('app_id')}/users/{contact_id}",
                         "country": location.get("country")},
            external_key="intercom_contact_id", external_id=contact_id, origin="Intercom",
        )
        try:
            outcome = await lead_capture.save(db, org, capture)
        except IntegrationError as exc:
            core.log(db, org, "intercom", "lead_skipped", f"An Intercom lead wasn't imported: {exc}", level="warning", connection=connection)
            continue
        if outcome.result == "created" and outcome.lead is not None:
            made += 1
            core.log(db, org, "intercom", "lead_created", f"New lead {outcome.lead.first_name} {outcome.lead.last_name} from Intercom.".replace("  ", " "),
                     level="success", connection=connection)
    return made


# ===========================================================================
# Background sync
# ===========================================================================


async def sync_connection(db: AsyncSession, connection: IntegrationConnection, *, pages: int = 2) -> int:
    """Read one page or two of changes for one desk. The caller commits."""

    key = connection.provider
    mod = adapter(key)
    settings = await settings_for(db, connection.organization_id, key)
    try:
        tickets, more = await mod.sync(db, connection, settings, pages=pages)
    except RateLimited as exc:
        common.backoff(connection, exc.retry_after)
        connection.last_sync_at = utcnow()
        return 0
    except IntegrationError as exc:
        connection.last_sync_at = utcnow()
        if connection.last_error != str(exc):
            connection.last_error = str(exc)[:1000]
            core.log(db, connection.organization_id, key, "sync_failed", f"Couldn't read {name_of(key)} changes: {exc}", level="warning", connection=connection)
        return 0
    stored = 0
    for ticket in tickets:
        if _in_scope(connection, ticket):
            await store(db, connection, ticket, settings=settings)
            stored += 1
    connection.last_sync_at = utcnow()
    connection.config = {**(connection.config or {}), "sync_more": more}
    if connection.status == "error":
        connection.status, connection.last_error = "connected", None
    return stored


async def run_due(db: AsyncSession) -> int:
    from app.integrations.providers import lead_capture

    started = time.monotonic()
    now = utcnow()
    candidates = (
        await db.execute(
            select(IntegrationConnection.id, IntegrationConnection.provider, IntegrationConnection.organization_id)
            .where(
                IntegrationConnection.provider.in_(KEYS), IntegrationConnection.user_id.is_(None), IntegrationConnection.status != "needs_reauth",
                or_(
                    IntegrationConnection.last_sync_at.is_(None), IntegrationConnection.last_sync_at < now - SYNC_EVERY,
                    IntegrationConnection.config["sync_more"].astext == "true",
                ),
            )
            # Least recently looked at first (skipped desks included), so none waits forever.
            .order_by(core.scheduler_order())
            .limit(50)
        )
    ).all()
    handled = 0
    processed = 0
    plans: dict[uuid.UUID, bool] = {}
    examined: list[uuid.UUID] = []
    try:
        for connection_id, provider, org in candidates:
            if processed >= RUN_MAX_CONNECTIONS or time.monotonic() - started > RUN_BUDGET_SECONDS:
                break
            examined.append(connection_id)
            handled_here, ran = await _run_one(db, connection_id, provider, org, now, plans, lead_capture)
            handled += handled_here
            processed += ran
    finally:
        await db.rollback()
        await core.mark_seen_ids(db, examined)
    return handled


async def _run_one(db: AsyncSession, connection_id: uuid.UUID, provider: str, org: uuid.UUID, now: Any, plans: dict[uuid.UUID, bool], lead_capture: Any) -> tuple[int, int]:
    """One desk's turn. Returns (items handled, 1 if it ran)."""

    # Loaded one at a time: a failed desk rolls back without touching the others.
    connection = await db.get(IntegrationConnection, connection_id)
    if connection is None:
        return 0, 0
    config = connection.config or {}
    if connection.last_sync_at and now - connection.last_sync_at < SYNC_EVERY and not config.get("sync_more"):
        return 0, 0
    if common.backing_off(connection) or not await core.is_enabled(db, org, provider):
        return 0, 0
    if org not in plans:
        features, read_only = await lead_capture.plan_for(db, org)
        plans[org] = FEATURE in features and not read_only
    if not plans[org]:
        return 0, 0
    try:
        handled = await sync_connection(db, connection)
        await db.commit()
    except Exception:  # noqa: BLE001 - one desk mustn't stop the others
        logger.exception("Help desk sync failed for %s", provider)
        await db.rollback()
        await _mark_attempt(db, connection_id)
        handled = 0
    return handled, 1


async def _mark_attempt(db: AsyncSession, connection_id: uuid.UUID) -> None:
    """After an unexpected failure, wait the normal interval before trying that desk again."""

    try:
        connection = await db.get(IntegrationConnection, connection_id)
        if connection is not None:
            connection.last_sync_at = utcnow()
            await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()


# ===========================================================================
# Used by the routes (int_support.py)
# ===========================================================================


async def ready_connection(db: AsyncSession, organization_id: uuid.UUID, key: str) -> IntegrationConnection | None:
    if not await core.is_enabled(db, organization_id, key):
        return None
    connection = await core.connection_for(db, organization_id, key)
    if connection is None or connection.status == "needs_reauth":
        return None
    return connection


async def desks(db: AsyncSession, organization_id: uuid.UUID) -> list[dict[str, Any]]:
    toggles = await core.toggles(db, organization_id)
    rows = (
        await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == organization_id, IntegrationConnection.provider.in_(KEYS),
                                                             IntegrationConnection.user_id.is_(None)))
    ).scalars().all()
    connections = {r.provider: r for r in rows}
    out = []
    for key in KEYS:
        item = INTEGRATIONS[key]
        toggle = toggles.get(key)
        enabled = bool(toggle and toggle.enabled)
        connection = connections.get(key)
        ready = enabled and connection is not None and connection.status != "needs_reauth"
        config = dict(toggle.config or {}) if toggle else {}
        out.append(
            {
                "key": key,
                "name": item.name,
                "kind": ADAPTERS[key].KIND,
                "color": item.color,
                "monogram": item.monogram or item.name[:1],
                "enabled": enabled,
                "connected": connection is not None,
                "ready": ready,
                "status": connection.status if connection else None,
                "label": connection.account_label if connection else None,
                "options": ADAPTERS[key].options(connection) if ready and connection else {},
                "defaults": {
                    "notify_customer": bool(config.get("notify_customer", False)),
                    "mailbox_id": config.get("default_mailbox_id"),
                    "request_type_id": (connection.config or {}).get("request_type_id") if connection else None,
                },
                "can_notify": key in {"zendesk"},
                "has_priority": key in {"zendesk", "freshdesk", "intercom"},
            }
        )
    return out


async def record_emails(db: AsyncSession, record_type: str, record: Any) -> list[str]:
    """Emails to look tickets up by: the person's, or (for an account) its contacts'.
    Record visibility applies to the contact query."""

    if record_type in {"contact", "lead"}:
        email = common.clean_email(getattr(record, "email", None))
        return [email] if email else []
    rows = (
        await db.execute(
            select(Contact.email)
            .where(Contact.organization_id == record.organization_id, Contact.account_id == record.id, Contact.email.is_not(None), Contact.is_archived.is_(False))
            .order_by(Contact.is_primary.desc(), Contact.updated_at.desc())
            .limit(5)
        )
    ).scalars().all()
    return [e for e in dict.fromkeys(common.clean_email(r) for r in rows) if e]


async def record_tickets(db: AsyncSession, organization_id: uuid.UUID, record_type: str, record: Any, *, refresh: bool = False) -> dict[str, Any]:
    emails = await record_emails(db, record_type, record)
    errors: dict[str, str] = {}
    looked = False
    for key in KEYS:
        connection = await ready_connection(db, organization_id, key)
        if connection is None:
            continue
        cache_key = ("hd-live", organization_id, key, record_type, record.id)
        recent = common.cache_get(cache_key)
        if recent is not None and (not refresh or time.monotonic() - recent < REFRESH_SECONDS):
            continue
        settings = await settings_for(db, organization_id, key)
        mod = ADAPTERS[key]
        try:
            found: list[Ticket] = await mod.by_emails(db, connection, emails) if emails else []
            if record_type == "account":
                found += await mod.by_company(db, connection, getattr(record, "name", None), getattr(record, "domain", None))
        except RateLimited as exc:
            errors[key] = str(exc)
            common.cache_put(cache_key, time.monotonic(), exc.retry_after)
            continue
        except IntegrationError as exc:
            errors[key] = str(exc)
            common.cache_put(cache_key, time.monotonic(), 30)
            continue
        for ticket in found:
            if _in_scope(connection, ticket):
                row, _ = await store(db, connection, ticket, settings=settings, allow_new_case=False)
                _attach(row, record_type, record)
        connection.last_used_at = utcnow()
        common.cache_put(cache_key, time.monotonic(), LIVE_SECONDS)
        looked = True
    if looked:
        await db.commit()
    column = {"contact": IntegrationObject.contact_id, "lead": IntegrationObject.lead_id, "account": IntegrationObject.account_id}[record_type]
    condition = column == record.id
    if emails:
        condition = or_(condition, IntegrationObject.email.in_(emails))
    rows = (
        await db.execute(
            select(IntegrationObject)
            .where(IntegrationObject.organization_id == organization_id, IntegrationObject.provider.in_(KEYS), condition)
            .order_by(IntegrationObject.external_updated_at.desc().nulls_last())
            .limit(50)
        )
    ).scalars().all()
    return {"items": [ticket_out(r) for r in rows], "errors": errors, "emails": emails}


def _attach(row: IntegrationObject, record_type: str, record: Any) -> None:
    """A ticket found by this record's email belongs to it even when matching found nothing."""

    if record_type == "contact" and row.contact_id is None and row.lead_id is None:
        row.contact_id = record.id
        row.account_id = row.account_id or getattr(record, "account_id", None)
    elif record_type == "lead" and row.contact_id is None and row.lead_id is None:
        row.lead_id = record.id
    elif record_type == "account" and row.account_id is None:
        row.account_id = record.id


async def create_ticket(
    db: AsyncSession, principal: Any, *, key: str, record_type: str, record: Any, subject: str, body: str, priority: str | None,
    requester_email: str, requester_name: str | None, notify_customer: bool | None, options: dict[str, Any],
) -> IntegrationObject:
    org = principal.organization_id
    connection = await core.active_connection(db, org, key)
    settings = await settings_for(db, org, key)
    mod = adapter(key)
    public = bool(settings.get("notify_customer") if notify_customer is None else notify_customer)
    ticket = await mod.create(db, connection, subject=subject, body=body, requester_email=requester_email, requester_name=requester_name,
                              priority=priority, public=public, options=options)
    row, _ = await store(db, connection, ticket, settings=settings, created_from_aiva=True, allow_new_case=False)
    _attach(row, record_type, record)
    noun = "conversation" if row.object_type == "conversation" else "ticket"
    common.timeline(db, row, f"{name_of(key)} {noun} {ticket.number} opened: {subject}", principal.user_id)
    connection.last_used_at = utcnow()
    core.log(db, org, key, "ticket_created", f"{principal.full_name or 'Someone'} opened {name_of(key)} {noun} {ticket.number} from AIVA.", level="success",
             connection=connection, user_id=principal.user_id)
    common.cache_drop(("hd-live", org, key))
    return row


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def case_links(db: AsyncSession, organization_id: uuid.UUID, case_id: uuid.UUID) -> list[IntegrationRecordLink]:
    return list(
        (
            await db.execute(
                select(IntegrationRecordLink)
                .where(IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.record_type == "case",
                       IntegrationRecordLink.record_id == case_id, IntegrationRecordLink.provider.in_(KEYS), IntegrationRecordLink.external_type == "ticket")
                .order_by(IntegrationRecordLink.created_at)
            )
        ).scalars().all()
    )


def link_out(link: IntegrationRecordLink, row: IntegrationObject | None) -> dict[str, Any]:
    data = link.data or {}
    base = ticket_out(row) if row is not None else {
        "provider": link.provider, "provider_name": name_of(link.provider), "external_id": link.external_id, "number": data.get("number") or link.external_id,
        "subject": data.get("subject") or "", "status": data.get("status") or "open", "status_label": data.get("status_label") or "", "url": link.external_url,
        "comment_count": data.get("comment_count"), "updated_at": link.last_synced_at, "priority": None, "is_open": data.get("status") in common.OPEN_STATUSES,
    }
    return {**base, "link_id": str(link.id), "origin": data.get("origin") or "escalation", "linked_at": link.created_at, "last_synced_at": link.last_synced_at,
            "error": link.last_error}


async def case_tickets(db: AsyncSession, organization_id: uuid.UUID, case_id: uuid.UUID, *, refresh: bool = False) -> list[dict[str, Any]]:
    links = await case_links(db, organization_id, case_id)
    changed = False
    out = []
    for link in links:
        connection = await ready_connection(db, organization_id, link.provider)
        stale = link.last_synced_at is None or utcnow() - link.last_synced_at > timedelta(seconds=LINK_SECONDS)
        if connection is not None and (refresh or stale):
            try:
                ticket = await ADAPTERS[link.provider].fetch(db, connection, link.external_id, comments=True)
                await store(db, connection, ticket, allow_new_case=False)
                link.external_url = ticket.url or link.external_url
                link.last_error = None
            except IntegrationError as exc:
                link.last_error = str(exc)[:500]
            link.last_synced_at = utcnow()
            changed = True
        row = await common.find_object(db, organization_id, link.provider, ADAPTERS[link.provider].KIND, link.external_id)
        out.append(link_out(link, row))
    if changed:
        await db.commit()
    return out


async def _comment(db: AsyncSession, case: Any, body: str, user_id: uuid.UUID | None) -> None:
    from app.models.platform import CaseComment

    db.add(CaseComment(id=uuid.uuid4(), organization_id=case.organization_id, case_id=case.id, author_user_id=user_id, body=body[:5000], is_internal=True))


async def _link(db: AsyncSession, case: Any, row: IntegrationObject, origin: str) -> IntegrationRecordLink:
    link = IntegrationRecordLink(
        id=uuid.uuid4(), organization_id=case.organization_id, provider=row.provider, record_type="case", record_id=case.id, external_type="ticket",
        external_id=row.external_id, external_parent="", external_url=row.url, status="linked",
        data={"origin": origin, "number": (row.data or {}).get("number"), "subject": row.title, "status": row.status,
              "status_label": (row.data or {}).get("status_label"), "comment_count": (row.data or {}).get("comment_count")},
        last_synced_at=utcnow(),
    )
    db.add(link)
    row.record_type, row.record_id = "case", case.id
    if row.contact_id is None and case.contact_id:
        row.contact_id = case.contact_id
    if row.account_id is None and case.account_id:
        row.account_id = case.account_id
    return link


async def _ensure_unlinked(db: AsyncSession, case: Any, key: str, external_id: str | None = None) -> None:
    for link in await case_links(db, case.organization_id, case.id):
        if link.provider == key:
            raise IntegrationError(f"This case is already linked to {name_of(key)} {(link.data or {}).get('number') or link.external_id}. Unlink it first.", 409)
    if external_id:
        other = await common.case_link_for_ticket(db, case.organization_id, key, external_id)
        if other is not None:
            raise IntegrationError("That ticket is already linked to another AIVA case.", 409)


async def requester_for_case(db: AsyncSession, case: Any, given: str | None) -> tuple[str, str | None]:
    if given:
        email = common.clean_email(given)
        if not email:
            raise IntegrationError("That requester email doesn't look right.", 422)
        return email, None
    if case.contact_id:
        contact = await db.get(Contact, case.contact_id)
        if contact is not None and common.clean_email(contact.email):
            return common.clean_email(contact.email) or "", f"{contact.first_name or ''} {contact.last_name or ''}".strip() or None
    requester = (case.custom_fields or {}).get("_requester") or {}
    if common.clean_email(requester.get("email")):
        return common.clean_email(requester.get("email")) or "", requester.get("name")
    raise IntegrationError("This case has no contact with an email address. Enter the customer's email.", 422)


async def escalate(
    db: AsyncSession, principal: Any, case: Any, *, key: str, subject: str | None, body: str | None, priority: str | None, requester_email: str | None,
    notify_customer: bool | None, options: dict[str, Any],
) -> dict[str, Any]:
    org = principal.organization_id
    await _ensure_unlinked(db, case, key)
    connection = await core.active_connection(db, org, key)
    settings = await settings_for(db, org, key)
    email, name = await requester_for_case(db, case, requester_email)
    text = (body or "").strip() or "\n\n".join(p for p in ((case.description or "").strip()[:20000], f"Escalated from AIVA case CS-{case.number}.") if p)
    public = bool(settings.get("notify_customer") if notify_customer is None else notify_customer)
    ticket = await adapter(key).create(
        db, connection, subject=(subject or case.subject or f"Case CS-{case.number}")[:250], body=text, requester_email=email, requester_name=name,
        priority=priority or common.CASE_PRIORITY_TO_TICKET.get(case.priority, "normal"), public=public, options=options,
    )
    row, _ = await store(db, connection, ticket, settings=settings, created_from_aiva=True, allow_new_case=False)
    link = await _link(db, case, row, "escalation")
    noun = "conversation" if row.object_type == "conversation" else "ticket"
    await _comment(db, case, f"Escalated to {name_of(key)} {noun} {ticket.number} by {principal.full_name or 'a teammate'}." + (f" {ticket.url}" if ticket.url else ""),
                   principal.user_id)
    connection.last_used_at = utcnow()
    core.log(db, org, key, "case_escalated", f"Case CS-{case.number} escalated to {name_of(key)} {noun} {ticket.number}.", level="success",
             connection=connection, user_id=principal.user_id)
    await db.flush()
    return link_out(link, row)


async def link_existing(db: AsyncSession, principal: Any, case: Any, *, key: str, reference: str) -> dict[str, Any]:
    org = principal.organization_id
    mod = adapter(key)
    connection = await core.active_connection(db, org, key)
    ref = mod.reference(reference)
    if not ref:
        raise IntegrationError(f"Paste the {name_of(key)} ticket number or its link.", 422)
    try:
        ticket = await mod.fetch(db, connection, ref, comments=True)
    except IntegrationError as exc:
        finder = getattr(mod, "find_by_number", None)
        if exc.status_code != 404 or finder is None:
            raise
        found = await finder(db, connection, ref)
        if found is None:
            raise
        ticket = found
    if not _in_scope(connection, ticket):
        raise IntegrationError("That request belongs to a different Jira project than the one connected.", 422)
    await _ensure_unlinked(db, case, key, ticket.external_id)
    row, _ = await store(db, connection, ticket, allow_new_case=False)
    link = await _link(db, case, row, "linked")
    noun = "conversation" if row.object_type == "conversation" else "ticket"
    await _comment(db, case, f"Linked to {name_of(key)} {noun} {ticket.number} by {principal.full_name or 'a teammate'}.", principal.user_id)
    core.log(db, org, key, "case_linked", f"Case CS-{case.number} linked to {name_of(key)} {noun} {ticket.number}.", connection=connection, user_id=principal.user_id)
    await db.flush()
    return link_out(link, row)


async def unlink(db: AsyncSession, principal: Any, case: Any, link_id: uuid.UUID) -> None:
    link = next((l for l in await case_links(db, case.organization_id, case.id) if l.id == link_id), None)  # noqa: E741
    if link is None:
        raise IntegrationError("That link isn't on this case.", 404)
    row = await common.find_object(db, case.organization_id, link.provider, ADAPTERS[link.provider].KIND, link.external_id)
    if row is not None and row.record_type == "case" and row.record_id == case.id:
        row.record_type, row.record_id = None, None
    number = (link.data or {}).get("number") or link.external_id
    await db.delete(link)
    await _comment(db, case, f"Unlinked from {name_of(link.provider)} {number} by {principal.full_name or 'a teammate'}.", principal.user_id)

