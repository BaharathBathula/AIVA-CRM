"""Google Ads: lead-form leads, offline click conversions and campaign performance.

* **Account.** After signing in with Google (``adwords`` scope), AIVA lists the Ads
  accounts the person can reach, including client accounts under a manager account
  (those are called with ``login-customer-id``). An admin picks one. Every call also
  needs a developer token: the workspace's own, or the server's.
* **Lead forms.** Google posts lead-form submissions to the integration's webhook URL
  with a ``google_key`` that AIVA generates; it is checked in constant time. Leads go
  through the shared lead-capture path (mapping, duplicates, owner, workflows) and
  keep their click ID, campaign, ad group, creative and form IDs.
* **Offline conversions.** CRM moments (lead qualified, deal won, a deal reaching a
  stage...) are mapped to "Import from clicks" conversion actions. They are queued in
  the record's own transaction (see ``_conversions``) and uploaded by the scheduler
  with ``uploadClickConversions`` (partial failure on), at most once per moment and
  record (``orderId``). Click IDs come from the lead, deal or contact, or from the
  person's website visits.
* **Performance.** Daily clicks, impressions, cost and conversions per campaign for
  the last 30 days, shown next to the leads and revenue AIVA attributes to each
  campaign. Click IDs on leads are resolved to their campaign with ``click_view``.

Scheduler work is bounded per run (workspaces, rows and a time budget) and backs off
when Google asks it to.
"""

from __future__ import annotations

import hmac
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _conversions as conversions
from app.integrations.providers import lead_capture
from app.integrations.providers._ads_api import (
    API_VERSION,
    AdsBusy,
    call,
    clean_customer_id,
    developer_token,
    pretty_customer_id,
    search,
)
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationToggle

logger = logging.getLogger("aiva.integrations.google_ads")

KEY = "google_ads"
FEATURE = "google_ads"

RUN_BUDGET_SECONDS = 40.0
WORKSPACES_PER_RUN = 25
UPLOAD_LIMIT = 500  # pending conversions looked at per workspace per run
UPLOAD_CHUNK = 200  # conversions per request (Google allows 2000)
PERFORMANCE_EVERY = timedelta(hours=6)
CLICKS_EVERY = timedelta(hours=1)
ACTIONS_MAX_AGE = timedelta(hours=24)
PERFORMANCE_DAYS = 30
MAX_ACCOUNTS = 200
MAX_BODY = 64 * 1024
EXPIRE_PENDING_AFTER = timedelta(days=30)

CONSENT = ("unspecified", "granted", "denied")
VALUE_MODES = ("none", "record", "fixed")

# Google lead-form column IDs → AIVA lead fields (anything else goes to the notes).
LEAD_FORM_COLUMNS: dict[str, str] = {
    "FULL_NAME": "full_name",
    "FIRST_NAME": "first_name",
    "LAST_NAME": "last_name",
    "EMAIL": "email",
    "WORK_EMAIL": "email",
    "PHONE_NUMBER": "phone",
    "WORK_PHONE": "phone",
    "COMPANY_NAME": "company_name",
    "JOB_TITLE": "job_title",
}

RETRYABLE = {
    "TOO_RECENT_EVENT", "TOO_RECENT_CLICK", "EVENT_NOT_FOUND", "CLICK_NOT_FOUND", "TOO_RECENT_CONVERSION_ACTION",
    "CONCURRENT_MODIFICATION", "INTERNAL_ERROR", "TRANSIENT_ERROR", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED",
}
DUPLICATES = {"CLICK_CONVERSION_ALREADY_EXISTS", "DUPLICATE_ORDER_ID", "ORDER_ID_ALREADY_IN_USE"}
ERROR_TEXT = {
    "EXPIRED_EVENT": "The click is older than the conversion action's click-through window.",
    "EXPIRED_CLICK": "The click is older than the conversion action's click-through window.",
    "CONVERSION_PRECEDES_EVENT": "The moment happened before the ad click.",
    "CONVERSION_PRECEDES_CLICK": "The moment happened before the ad click.",
    "UNPARSEABLE_GCLID": "Google couldn't read the click ID.",
    "EVENT_NOT_FOUND": "Google doesn't know this click yet (clicks can take a few hours to appear).",
    "CLICK_NOT_FOUND": "Google doesn't know this click yet (clicks can take a few hours to appear).",
    "UNAUTHORIZED_CUSTOMER": "The click belongs to a different Google Ads account.",
    "INVALID_CONVERSION_ACTION_TYPE": "That conversion action doesn't accept imported clicks.",
    "CONVERSION_TRACKING_NOT_ENABLED_AT_IMPRESSION_TIME": "Conversion tracking wasn't on when the ad was shown.",
    "TOO_RECENT_CONVERSION_ACTION": "The conversion action was created less than 6 hours ago; AIVA will try again.",
}

_rate: dict[str, list[float]] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def settings_of(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    lead_form = config.get("lead_form") if isinstance(config.get("lead_form"), dict) else {}
    return {
        "customer_id": clean_customer_id(config.get("customer_id")),
        "login_customer_id": clean_customer_id(config.get("login_customer_id")),
        "customer_name": config.get("customer_name"),
        "currency": (config.get("currency") or "USD")[:3],
        "time_zone": config.get("time_zone") or "UTC",
        "conversions": {k: v for k, v in (config.get("conversions") or {}).items() if isinstance(v, dict)},
        "consent": config.get("consent") if config.get("consent") in CONSENT else "unspecified",
        "sync_performance": config.get("sync_performance", True) is not False,
        "lead_form": {
            "lead_source": (lead_form.get("lead_source") or "google_ads")[:80],
            "owner_user_id": lead_form.get("owner_user_id"),
            "on_duplicate": lead_form.get("on_duplicate") if lead_form.get("on_duplicate") in lead_capture.DUPLICATE_MODES else "update",
            "tags": lead_capture.clean_tags(lead_form.get("tags")),
        },
    }


async def _toggle(db: AsyncSession, organization_id: uuid.UUID) -> IntegrationToggle | None:
    return await core.toggle_row(db, organization_id, KEY)


async def _stages(db: AsyncSession, organization_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.models.pipeline import Pipeline
    from app.models.pipeline_stage import PipelineStage

    rows = (
        await db.execute(
            select(PipelineStage.id, PipelineStage.name, PipelineStage.category, Pipeline.name)
            .join(Pipeline, Pipeline.id == PipelineStage.pipeline_id)
            .where(PipelineStage.organization_id == organization_id)
            .order_by(Pipeline.name, PipelineStage.position)
        )
    ).all()
    return [{"id": str(sid), "name": name, "category": category, "pipeline": pipeline} for sid, name, category, pipeline in rows]


def moments_catalog(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [{"key": k, "label": v, "valued": k in conversions.VALUED} for k, v in conversions.MOMENTS.items()]
    items += [
        {"key": f"{conversions.STAGE_PREFIX}{s['id']}", "label": f"Deal reaches {s['name']}" + (f" ({s['pipeline']})" if s.get("pipeline") else ""), "valued": True}
        for s in stages
    ]
    return items


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    org = principal.organization_id
    toggle = await _toggle(db, org)
    current = settings_of(toggle.config if toggle else {})
    connection = await core.connection_for(db, org, KEY)
    out: dict[str, Any] = {}

    if "customer_id" in config:
        wanted = clean_customer_id(config.get("customer_id"))
        if config.get("customer_id") and not wanted:
            raise IntegrationError("A Google Ads customer ID has 10 digits, like 123-456-7890.", 422)
        if wanted:
            accounts = {a["id"]: a for a in ((connection.config or {}).get("accounts") or [])} if connection else {}
            account = accounts.get(wanted)
            if account is None:
                raise IntegrationError("Pick one of the Google Ads accounts the connected Google user can reach (refresh the list if it's new).", 422)
            out.update(
                customer_id=wanted,
                login_customer_id=account.get("login_customer_id"),
                customer_name=account.get("name"),
                currency=account.get("currency") or "USD",
                time_zone=account.get("time_zone") or "UTC",
            )
        else:
            out.update(customer_id=None, login_customer_id=None, customer_name=None)
        if wanted != current["customer_id"]:
            # Conversion actions belong to one account.
            out["conversions"] = {}
            if connection is not None:
                connection.config = {k: v for k, v in (connection.config or {}).items() if k not in {"conversion_actions", "conversion_actions_at", "perf_synced_at"}}

    customer_id = out.get("customer_id", current["customer_id"]) if "customer_id" in out else current["customer_id"]
    if "conversions" in config and "conversions" not in out:
        raw = config.get("conversions")
        if not isinstance(raw, dict):
            raise IntegrationError("Conversions should map CRM moments to conversion actions.", 422)
        if raw and not customer_id:
            raise IntegrationError("Pick the Google Ads account first.", 422)
        stage_ids = {s["id"] for s in await _stages(db, org)}
        known = {a.get("resource_name") for a in ((connection.config or {}).get("conversion_actions") or [])} if connection else set()
        cleaned: dict[str, Any] = {}
        for moment, rule in list(raw.items())[:60]:
            if not conversions.valid_moment(str(moment), stage_ids):
                raise IntegrationError(f"Unknown CRM moment: {moment}.", 422)
            if not isinstance(rule, dict) or not rule.get("conversion_action"):
                continue
            action = str(rule.get("conversion_action"))
            if not re.fullmatch(rf"customers/{customer_id}/conversionActions/\d+", action) or (known and action not in known):
                raise IntegrationError("Pick a conversion action from the list (one that imports clicks).", 422)
            mode = rule.get("value") if rule.get("value") in VALUE_MODES else "record"
            entry: dict[str, Any] = {"conversion_action": action, "value": mode}
            if mode == "fixed":
                try:
                    fixed = float(rule.get("fixed_value"))
                except (TypeError, ValueError) as exc:
                    raise IntegrationError("Enter a number for the fixed conversion value.", 422) from exc
                if fixed < 0 or fixed > 1e9:
                    raise IntegrationError("The fixed conversion value must be between 0 and 1,000,000,000.", 422)
                entry["fixed_value"] = round(fixed, 2)
                currency = str(rule.get("currency") or "").upper()
                if currency and not re.fullmatch(r"[A-Z]{3}", currency):
                    raise IntegrationError("Currency is a three-letter code like USD.", 422)
                if currency:
                    entry["currency"] = currency
            cleaned[str(moment)] = entry
        out["conversions"] = cleaned

    if "consent" in config:
        if config.get("consent") not in CONSENT:
            raise IntegrationError("Choose how to report ad user data consent.", 422)
        out["consent"] = config["consent"]
    if "sync_performance" in config:
        out["sync_performance"] = bool(config.get("sync_performance"))
    if "lead_form" in config:
        raw = config.get("lead_form") or {}
        if not isinstance(raw, dict):
            raise IntegrationError("Lead form settings are invalid.", 422)
        mode = raw.get("on_duplicate") or "update"
        if mode not in lead_capture.DUPLICATE_MODES:
            raise IntegrationError("Choose what happens when the same email comes in again.", 422)
        owner = await lead_capture.valid_owner(db, org, raw.get("owner_user_id"))
        out["lead_form"] = {
            "lead_source": (str(raw.get("lead_source") or "google_ads").strip() or "google_ads")[:80],
            "owner_user_id": str(owner) if owner else None,
            "on_duplicate": mode,
            "tags": lead_capture.clean_tags(raw.get("tags")),
        }
    conversions.refresh_rules(org)
    return out


# ---------------------------------------------------------------------------
# Accounts and conversion actions
# ---------------------------------------------------------------------------


async def discover_accounts(db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
    """Accounts the signed-in user can reach, including clients under manager accounts."""

    body = await call(db, connection, "GET", "customers:listAccessibleCustomers")
    ids = [name.split("/")[-1] for name in body.get("resourceNames") or []][:50]
    found: dict[str, dict[str, Any]] = {}
    managers: list[dict[str, Any]] = []
    for customer_id in ids:
        try:
            rows = await search(
                db, connection, customer_id,
                "SELECT customer.id, customer.descriptive_name, customer.manager, customer.currency_code, customer.time_zone, customer.test_account FROM customer LIMIT 1",
            )
        except AdsBusy:
            raise
        except IntegrationError as exc:
            logger.info("Skipping Google Ads account %s: %s", customer_id, exc)
            continue
        info = (rows[0] if rows else {}).get("customer") or {}
        entry = {
            "id": str(info.get("id") or customer_id),
            "name": info.get("descriptiveName") or f"Account {pretty_customer_id(customer_id)}",
            "currency": info.get("currencyCode"),
            "time_zone": info.get("timeZone"),
            "test": bool(info.get("testAccount")),
            "manager": bool(info.get("manager")),
            "login_customer_id": None,
            "via": None,
        }
        if entry["manager"]:
            managers.append(entry)
        else:
            found[entry["id"]] = entry
    for manager in managers:
        if len(found) >= MAX_ACCOUNTS:
            break
        try:
            clients = await search(
                db, connection, manager["id"],
                "SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager, customer_client.currency_code, "
                "customer_client.time_zone, customer_client.status, customer_client.level FROM customer_client "
                "WHERE customer_client.level <= 2 AND customer_client.manager = FALSE AND customer_client.status = 'ENABLED'",
                login_customer_id=manager["id"],
                max_rows=MAX_ACCOUNTS,
            )
        except AdsBusy:
            raise
        except IntegrationError as exc:
            logger.info("Couldn't list clients of manager %s: %s", manager["id"], exc)
            continue
        for row in clients:
            client = row.get("customerClient") or {}
            client_id = str(client.get("id") or "")
            if not clean_customer_id(client_id) or client_id in found:
                continue
            found[client_id] = {
                "id": client_id,
                "name": client.get("descriptiveName") or f"Account {pretty_customer_id(client_id)}",
                "currency": client.get("currencyCode"),
                "time_zone": client.get("timeZone"),
                "test": manager.get("test", False),
                "manager": False,
                "login_customer_id": manager["id"],
                "via": manager["name"],
            }
    accounts = sorted(found.values(), key=lambda a: (a["name"] or "").lower())[:MAX_ACCOUNTS]
    connection.config = {**(connection.config or {}), "accounts": accounts, "accounts_at": utcnow().isoformat()}
    return accounts


async def conversion_actions(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], *, refresh: bool = False) -> list[dict[str, Any]]:
    cached = (connection.config or {}).get("conversion_actions")
    stamp = (connection.config or {}).get("conversion_actions_at")
    fresh = False
    if stamp:
        try:
            fresh = utcnow() - datetime.fromisoformat(stamp) < ACTIONS_MAX_AGE
        except ValueError:
            fresh = False
    if cached is not None and fresh and not refresh:
        return list(cached)
    if not settings["customer_id"]:
        return []
    rows = await search(
        db, connection, settings["customer_id"],
        "SELECT conversion_action.resource_name, conversion_action.id, conversion_action.name, conversion_action.type, "
        "conversion_action.category, conversion_action.status FROM conversion_action "
        "WHERE conversion_action.status = 'ENABLED' AND conversion_action.type = 'UPLOAD_CLICKS'",
        login_customer_id=settings["login_customer_id"],
        max_rows=500,
    )
    actions = sorted(
        (
            {
                "resource_name": (r.get("conversionAction") or {}).get("resourceName"),
                "id": str((r.get("conversionAction") or {}).get("id") or ""),
                "name": (r.get("conversionAction") or {}).get("name"),
                "category": (r.get("conversionAction") or {}).get("category"),
            }
            for r in rows
        ),
        key=lambda a: (a["name"] or "").lower(),
    )
    actions = [a for a in actions if a["resource_name"]]
    connection.config = {**(connection.config or {}), "conversion_actions": actions, "conversion_actions_at": utcnow().isoformat()}
    return actions


def _ensure_lead_form(connection: IntegrationConnection) -> None:
    if not connection.webhook_token:
        connection.webhook_token = core.new_webhook_token()
    if not core.secret(connection, "lead_form_key"):
        core.set_secret(connection, "lead_form_key", lead_form_key())


def lead_form_key() -> str:
    import secrets

    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    _ensure_lead_form(connection)
    toggle = await _toggle(db, connection.organization_id)
    token, _ = await developer_token(db, connection.organization_id)
    if not token:
        connection.last_error = "Add a Google Ads developer token to finish setting up."
        return
    accounts = await discover_accounts(db, connection)
    settings = settings_of(toggle.config if toggle else {})
    if toggle is not None and not settings["customer_id"] and len(accounts) == 1:
        account = accounts[0]
        toggle.config = {
            **(toggle.config or {}),
            "customer_id": account["id"], "login_customer_id": account.get("login_customer_id"), "customer_name": account.get("name"),
            "currency": account.get("currency") or "USD", "time_zone": account.get("time_zone") or "UTC",
        }
        settings = settings_of(toggle.config)
    if settings["customer_id"]:
        await conversion_actions(db, connection, settings, refresh=True)
    if not accounts:
        connection.last_error = "That Google user can't reach any Google Ads account."
    conversions.refresh_rules(connection.organization_id)


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    conversions.refresh_rules(organization_id)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    conversions.refresh_rules(connection.organization_id)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    body = await call(db, connection, "GET", "customers:listAccessibleCustomers")
    count = len(body.get("resourceNames") or [])
    toggle = await _toggle(db, connection.organization_id)
    settings = settings_of(toggle.config if toggle else {})
    if not settings["customer_id"]:
        return f"Google Ads answered: this Google user can reach {count} account{'s' if count != 1 else ''}. Pick the one to use."
    actions = await conversion_actions(db, connection, settings, refresh=True)
    return (
        f"Google Ads answered: {settings['customer_name'] or pretty_customer_id(settings['customer_id'])} "
        f"({pretty_customer_id(settings['customer_id'])}) has {len(actions)} conversion action{'s' if len(actions) != 1 else ''} that import clicks."
    )


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    manage = principal.can("admin.settings")
    toggle = await _toggle(db, org)
    settings = settings_of(toggle.config if toggle else {})
    connection = await core.connection_for(db, org, KEY)
    token, source = await developer_token(db, org)
    stages = await _stages(db, org)
    out: dict[str, Any] = {
        "api_version": API_VERSION,
        "developer_token": {"set": bool(token), "source": source, "workspace": bool(toggle and core.secret(toggle, "developer_token"))},
        "settings": settings,
        "moments": moments_catalog(stages),
        "consent_options": list(CONSENT),
    }
    if connection is None:
        return out
    config = connection.config or {}
    out.update(
        accounts=config.get("accounts") or [],
        accounts_at=config.get("accounts_at"),
        conversion_actions=config.get("conversion_actions") or [],
        conversion_actions_at=config.get("conversion_actions_at"),
        performance_synced_at=config.get("perf_synced_at"),
        lead_form_columns=[{"id": k, "target": v} for k, v in LEAD_FORM_COLUMNS.items()],
    )
    if manage:
        out["webhook_url"] = core.webhook_url(KEY, connection.webhook_token)
        out["lead_form_key"] = core.secret(connection, "lead_form_key")
    since = utcnow() - timedelta(days=30)
    counts = dict(
        (
            await db.execute(
                select(IntegrationObject.status, func.count())
                .where(
                    IntegrationObject.organization_id == org, IntegrationObject.provider == KEY,
                    IntegrationObject.object_type == "conversion", IntegrationObject.occurred_at >= since,
                )
                .group_by(IntegrationObject.status)
            )
        ).all()
    )
    out["stats"] = {s: int(counts.get(s, 0)) for s in ("pending", "sent", "skipped", "failed", "duplicate")}
    if manage:
        rows = (
            await db.execute(
                select(IntegrationObject)
                .where(IntegrationObject.organization_id == org, IntegrationObject.provider == KEY, IntegrationObject.object_type == "conversion")
                .order_by(IntegrationObject.occurred_at.desc())
                .limit(20)
            )
        ).scalars().all()
        out["recent"] = [conversions.object_out(r) for r in rows]
    return out


# ---------------------------------------------------------------------------
# Lead-form webhook
# ---------------------------------------------------------------------------


def _rate_limited(token: str, limit: int = 120, window: float = 60.0) -> bool:
    now = time.monotonic()
    hits = [t for t in _rate.get(token, []) if now - t < window]
    hits.append(now)
    _rate[token] = hits[-limit - 5:]
    if len(_rate) > 5000:
        _rate.clear()
    return len(hits) > limit


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method == "GET":
        return JSONResponse({"ok": True, "message": "Google Ads lead forms can post leads to this address."})
    if _rate_limited(connection.webhook_token or str(connection.id)):
        return JSONResponse({"error": "Too many requests."}, status_code=429)
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"error": "Too large."}, status_code=413)
    try:
        import json

        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": "Send JSON."}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Send a JSON object."}, status_code=400)
    expected = core.secret(connection, "lead_form_key") or ""
    given = str(payload.get("google_key") or "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        core.log(db, connection.organization_id, KEY, "lead_refused", "A lead-form submission was refused: its key didn't match the one on the Google Ads page in AIVA.", level="warning", connection=connection)
        return JSONResponse({"error": "The key doesn't match."}, status_code=403)
    org = connection.organization_id
    try:
        await lead_capture.prepare(db, org, feature=FEATURE)
    except IntegrationError as exc:
        core.log(db, org, KEY, "lead_refused", f"A lead-form submission was refused: {exc}", level="warning", connection=connection)
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

    toggle = await _toggle(db, org)
    settings = settings_of(toggle.config if toggle else {})
    answers: list[lead_capture.Answer] = []
    mapping: dict[str, str] = {}
    for column in payload.get("user_column_data") or []:
        if not isinstance(column, dict):
            continue
        column_id = str(column.get("column_id") or column.get("column_name") or "").strip()[:100]
        value = str(column.get("string_value") or "").strip()[:2000]
        if not column_id or not value:
            continue
        answers.append(lead_capture.Answer(key=column_id, label=str(column.get("column_name") or column_id)[:200], value=value))
        if column_id in LEAD_FORM_COLUMNS:
            mapping[column_id] = LEAD_FORM_COLUMNS[column_id]
        elif not lead_capture.auto_target(answers[-1]):
            mapping[column_id] = "notes"

    def text(value: Any) -> str | None:
        found = str(value or "").strip()
        return found[:100] if found and found.lower() != "none" else None

    is_test = bool(payload.get("is_test"))
    lead_form = settings["lead_form"]
    attribution = {
        "gclid": conversions.clean_click_id(payload.get("gcl_id")),
        "google_campaign_id": text(payload.get("campaign_id")),
        "google_ad_group_id": text(payload.get("adgroup_id")),
        "google_creative_id": text(payload.get("creative_id")),
        "google_asset_group_id": text(payload.get("asset_group_id")),
        "google_form_id": text(payload.get("form_id")),
        "utm_source": "google",
        "utm_medium": "lead_form",
        "google_lead_test": True if is_test else None,
    }
    owner = None
    if lead_form.get("owner_user_id"):
        try:
            owner = await lead_capture.valid_owner(db, org, lead_form["owner_user_id"])
        except IntegrationError:
            owner = None
    capture = lead_capture.Capture(
        provider=KEY,
        lead_source=lead_form["lead_source"],
        answers=answers,
        mapping=mapping,
        owner_user_id=owner or connection.connected_by_user_id,
        on_duplicate=lead_form["on_duplicate"],
        tags=[*lead_form["tags"], *(["test"] if is_test else [])],
        attribution=attribution,
        external_key="google_lead_id",
        external_id=text(payload.get("lead_id")),
        origin="Google Ads lead form",
    )
    try:
        outcome = await lead_capture.save(db, org, capture)
    except IntegrationError as exc:
        core.log(db, org, KEY, "lead_refused", f"A lead-form submission couldn't become a lead: {exc}", level="warning", connection=connection)
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
    connection.last_used_at = utcnow()
    lead = outcome.lead
    name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "someone"
    messages = {
        "created": f"New lead {name} from a Google Ads lead form{' (test)' if is_test else ''}.",
        "updated": f"{name} filled in a Google Ads lead form again; their lead was updated.",
        "skipped": f"{name} filled in a Google Ads lead form again; skipped (already a lead).",
        "duplicate": "Google sent the same lead-form submission again; it was ignored.",
    }
    core.log(
        db, org, KEY, f"lead_{outcome.result}", messages.get(outcome.result, "Lead form received."),
        level="success" if outcome.result in {"created", "updated"} else "info", connection=connection,
        detail={"lead_id": str(lead.id) if lead else None, "test": is_test},
    )
    return JSONResponse({}, status_code=200)


# ---------------------------------------------------------------------------
# Offline conversion upload
# ---------------------------------------------------------------------------


def _ads_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def _errors_by_index(body: dict[str, Any]) -> dict[int, tuple[str, str]]:
    found: dict[int, tuple[str, str]] = {}
    failure = body.get("partialFailureError") or {}
    for detail_item in failure.get("details") or []:
        for error in (detail_item or {}).get("errors") or []:
            index = None
            for element in ((error.get("location") or {}).get("fieldPathElements") or []):
                if element.get("fieldName") == "conversions" and element.get("index") is not None:
                    try:
                        index = int(element["index"])
                    except (TypeError, ValueError):
                        index = None
                    break
            if index is None:
                continue
            codes = error.get("errorCode") or {}
            code = str(next(iter(codes.values()), "UNKNOWN"))
            found.setdefault(index, (code, str(error.get("message") or code)))
    return found


async def upload_due(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], deadline: float) -> int:
    org = connection.organization_id
    rows = await conversions.due(db, org, conversions.ADS, UPLOAD_LIMIT)
    if not rows:
        return 0
    if not settings["customer_id"]:
        for row in rows:
            conversions.postpone(row, 3600, "Waiting for a Google Ads account to be picked.")
        return 0
    now = utcnow()
    batch: list[tuple[IntegrationObject, dict[str, Any]]] = []
    seen: set[str] = set()
    handled = 0
    for row in rows:
        if time.monotonic() > deadline:
            break
        data = row.data or {}
        moment = data.get("moment") or ""
        rule = settings["conversions"].get(moment)
        if not rule or not rule.get("conversion_action"):
            conversions.finish(row, "skipped", "This moment isn't sent to Google Ads any more.", attempt=False)
            handled += 1
            continue
        occurred = row.occurred_at or now
        if now - occurred > conversions.CLICK_WINDOW:
            conversions.finish(row, "failed", "Older than 90 days, the longest click window Google Ads allows.", attempt=False)
            handled += 1
            continue
        dedupe = data.get("dedupe") or ""
        if dedupe in seen or await conversions.already_sent(db, row):
            conversions.finish(row, "duplicate", "Already sent for this record.", attempt=False)
            handled += 1
            continue
        ids = await conversions.identifiers(db, org, data.get("record_type") or "", data.get("record_id") or "")
        click_key = next((k for k in conversions.CLICK_KEYS if ids.get(k)), None)
        if click_key is None:
            conversions.finish(
                row, "skipped",
                "No Google click ID for this person (they didn't come from a Google ad, or the click ID wasn't kept).",
                attempt=False,
            )
            handled += 1
            continue
        item: dict[str, Any] = {
            click_key: ids[click_key],
            "conversionAction": rule["conversion_action"],
            "conversionDateTime": _ads_time(occurred),
            "orderId": conversions.order_id(dedupe),
        }
        if rule.get("value") == "fixed" and rule.get("fixed_value") is not None:
            item["conversionValue"] = float(rule["fixed_value"])
            item["currencyCode"] = rule.get("currency") or settings["currency"]
        elif rule.get("value", "record") == "record" and row.amount is not None:
            item["conversionValue"] = float(row.amount)
            item["currencyCode"] = (row.currency or settings["currency"] or "USD").upper()
        if settings["consent"] != "unspecified":
            item["consent"] = {"adUserData": settings["consent"].upper()}
        seen.add(dedupe)
        row.data = {**data, "identifier": click_key}
        batch.append((row, item))

    for start in range(0, len(batch), UPLOAD_CHUNK):
        chunk = batch[start:start + UPLOAD_CHUNK]
        if not chunk:
            break
        if time.monotonic() > deadline:
            break
        try:
            body = await call(
                db, connection, "POST", f"customers/{settings['customer_id']}:uploadClickConversions",
                json={"conversions": [item for _, item in chunk], "partialFailure": True},
                login_customer_id=settings["login_customer_id"],
            )
        except AdsBusy as exc:
            for row, _ in batch[start:]:
                conversions.postpone(row, exc.retry_after, str(exc))
            raise
        except IntegrationError as exc:
            for row, _ in chunk:
                conversions.retry_later(row, str(exc))
            core.log(db, org, KEY, "upload_failed", f"Couldn't upload {len(chunk)} conversion{'s' if len(chunk) != 1 else ''}: {exc}", level="error", connection=connection)
            handled += len(chunk)
            continue
        errors = _errors_by_index(body)
        sent = 0
        for index, (row, _) in enumerate(chunk):
            if index in errors:
                code, message = errors[index]
                text = ERROR_TEXT.get(code) or message
                if code in DUPLICATES:
                    conversions.finish(row, "duplicate", "Google Ads already has this conversion.")
                elif code in RETRYABLE:
                    conversions.retry_later(row, text)
                else:
                    conversions.finish(row, "failed", f"{text} ({code})")
            else:
                conversions.finish(row, "sent", "Uploaded to Google Ads.")
                sent += 1
        handled += len(chunk)
        if sent:
            core.log(db, org, KEY, "conversions_uploaded", f"Uploaded {sent} offline conversion{'s' if sent != 1 else ''} to Google Ads.", level="success", connection=connection)
        failed = len(chunk) - sent
        if failed:
            core.log(db, org, KEY, "conversions_rejected", f"Google Ads didn't accept {failed} conversion{'s' if failed != 1 else ''}; see the upload log.", level="warning", connection=connection)
    return handled


# ---------------------------------------------------------------------------
# Click IDs → campaign, and campaign performance
# ---------------------------------------------------------------------------


def _local_day(moment: datetime, time_zone: str) -> date:
    try:
        zone = ZoneInfo(time_zone or "UTC")
    except Exception:  # noqa: BLE001 - unknown zone names fall back to UTC
        zone = timezone.utc
    return moment.astimezone(zone).date()


def _due(stamp: Any, every: timedelta) -> bool:
    if not stamp:
        return True
    try:
        return utcnow() - datetime.fromisoformat(str(stamp)) >= every
    except ValueError:
        return True


async def resolve_clicks(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], deadline: float) -> int:
    """Add campaign, ad group and keyword to recent leads that only have a GCLID."""

    from app.models.lead import Lead

    if not settings["customer_id"] or not _due((connection.config or {}).get("clicks_checked_at"), CLICKS_EVERY):
        return 0
    connection.config = {**(connection.config or {}), "clicks_checked_at": utcnow().isoformat()}
    since = utcnow() - timedelta(days=89)
    attribution = Lead.custom_fields["_attribution"]
    leads = (
        await db.execute(
            select(Lead)
            .where(
                Lead.organization_id == connection.organization_id,
                Lead.created_at >= since,
                attribution["gclid"].astext.is_not(None),
                attribution["google_campaign_id"].astext.is_(None),
                attribution["google_click_checked"].astext.is_(None),
            )
            .order_by(Lead.created_at.desc())
            .limit(150)
            .execution_options(skip_visibility=True)
        )
    ).scalars().all()
    by_day: dict[date, list[Any]] = defaultdict(list)
    for lead in leads:
        by_day[_local_day(lead.created_at, settings["time_zone"])].append(lead)
    handled = 0
    for day, group in list(by_day.items())[:5]:
        if time.monotonic() > deadline:
            break
        clicks = {conversions.clean_click_id(((l.custom_fields or {}).get("_attribution") or {}).get("gclid")): l for l in group}
        clicks.pop(None, None)
        if not clicks:
            continue
        quoted = ", ".join(f"'{gclid}'" for gclid in list(clicks)[:100])
        query = (
            "SELECT click_view.gclid, campaign.id, campaign.name, ad_group.id, ad_group.name, click_view.keyword_info.text "
            f"FROM click_view WHERE segments.date = '{day.isoformat()}' AND click_view.gclid IN ({quoted})"
        )
        try:
            rows = await search(db, connection, settings["customer_id"], query, login_customer_id=settings["login_customer_id"], max_rows=500)
        except AdsBusy:
            raise
        except IntegrationError as exc:
            logger.info("click_view lookup failed for %s: %s", day, exc)
            rows = []
        info = {}
        for row in rows:
            gclid = (row.get("clickView") or {}).get("gclid")
            if gclid:
                info[gclid] = row
        stamp = utcnow().isoformat()
        for gclid, lead in clicks.items():
            fields = dict(lead.custom_fields or {})
            current = dict(fields.get("_attribution") or {})
            row = info.get(gclid)
            if row:
                campaign = row.get("campaign") or {}
                group_info = row.get("adGroup") or {}
                keyword = ((row.get("clickView") or {}).get("keywordInfo") or {}).get("text")
                current.update(
                    google_campaign_id=str(campaign.get("id") or "") or None,
                    google_campaign=campaign.get("name"),
                    google_ad_group_id=str(group_info.get("id") or "") or current.get("google_ad_group_id"),
                    google_ad_group=group_info.get("name"),
                    google_keyword=keyword,
                )
                if not current.get("utm_campaign") and campaign.get("name"):
                    current["utm_campaign"] = campaign.get("name")
            current["google_click_checked"] = stamp
            fields["_attribution"] = {k: v for k, v in current.items() if v not in (None, "")}
            lead.custom_fields = fields
            handled += 1
    return handled


async def sync_performance(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], deadline: float, *, force: bool = False) -> int:
    if not settings["customer_id"] or not settings["sync_performance"]:
        return 0
    if not force and not _due((connection.config or {}).get("perf_synced_at"), PERFORMANCE_EVERY):
        return 0
    if time.monotonic() > deadline:
        return 0
    rows = await search(
        db, connection, settings["customer_id"],
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, segments.date, "
        "metrics.clicks, metrics.impressions, metrics.cost_micros, metrics.conversions, metrics.conversions_value "
        f"FROM campaign WHERE segments.date DURING LAST_{PERFORMANCE_DAYS}_DAYS AND metrics.impressions > 0",
        login_customer_id=settings["login_customer_id"],
        max_rows=20000,
    )
    org = connection.organization_id
    parent = settings["customer_id"]
    start = utcnow() - timedelta(days=PERFORMANCE_DAYS + 2)
    existing = {
        o.external_id: o
        for o in (
            await db.execute(
                select(IntegrationObject).where(
                    IntegrationObject.organization_id == org, IntegrationObject.provider == KEY,
                    IntegrationObject.object_type == "ad_campaign", IntegrationObject.external_parent == parent,
                    IntegrationObject.occurred_at >= start,
                )
            )
        ).scalars()
    }
    handled = 0
    for row in rows:
        campaign = row.get("campaign") or {}
        metrics = row.get("metrics") or {}
        day = (row.get("segments") or {}).get("date")
        campaign_id = str(campaign.get("id") or "")
        if not campaign_id or not day:
            continue
        try:
            when = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        external_id = f"{campaign_id}:{day}"
        cost = Decimal(int(metrics.get("costMicros") or 0)) / Decimal(1_000_000)
        data = {
            "campaign_id": campaign_id,
            "campaign_name": campaign.get("name"),
            "status": campaign.get("status"),
            "channel": campaign.get("advertisingChannelType"),
            "date": day,
            "clicks": int(metrics.get("clicks") or 0),
            "impressions": int(metrics.get("impressions") or 0),
            "conversions": float(metrics.get("conversions") or 0),
            "conversions_value": float(metrics.get("conversionsValue") or 0),
        }
        target = existing.get(external_id)
        if target is None:
            target = IntegrationObject(
                id=uuid.uuid4(), organization_id=org, provider=KEY, object_type="ad_campaign", external_id=external_id, external_parent=parent,
            )
            db.add(target)
            existing[external_id] = target
        target.title = (campaign.get("name") or f"Campaign {campaign_id}")[:500]
        target.status = (campaign.get("status") or "")[:60] or None
        target.amount = cost.quantize(Decimal("0.01"))
        target.currency = settings["currency"]
        target.occurred_at = when
        target.external_updated_at = utcnow()
        target.data = data
        handled += 1
    connection.config = {**(connection.config or {}), "perf_synced_at": utcnow().isoformat()}
    connection.last_sync_at = utcnow()
    return handled


async def performance_report(db: AsyncSession, organization_id: uuid.UUID, days: int) -> dict[str, Any]:
    """Ads spend per campaign next to the leads and won revenue AIVA attributes to it."""

    from app.models.lead import Lead
    from app.models.opportunity import Opportunity
    from app.models.pipeline_stage import PipelineStage

    toggle = await _toggle(db, organization_id)
    settings = settings_of(toggle.config if toggle else {})
    since = utcnow() - timedelta(days=days)
    rows = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == organization_id, IntegrationObject.provider == KEY,
                IntegrationObject.object_type == "ad_campaign", IntegrationObject.occurred_at >= since,
                IntegrationObject.external_parent == (settings["customer_id"] or ""),
            )
        )
    ).scalars().all()
    campaigns: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = row.data or {}
        cid = data.get("campaign_id") or row.external_id.split(":")[0]
        entry = campaigns.setdefault(
            cid, {"campaign_id": cid, "name": data.get("campaign_name") or row.title, "status": data.get("status"), "cost": 0.0, "clicks": 0, "impressions": 0, "ads_conversions": 0.0, "leads": 0, "won_deals": 0, "won_revenue": 0.0}
        )
        entry["cost"] += float(row.amount or 0)
        entry["clicks"] += int(data.get("clicks") or 0)
        entry["impressions"] += int(data.get("impressions") or 0)
        entry["ads_conversions"] += float(data.get("conversions") or 0)

    campaign_attr = Lead.custom_fields["_attribution"]["google_campaign_id"].astext
    leads = (
        await db.execute(
            select(Lead.id, campaign_attr, Lead.converted_opportunity_id)
            .where(Lead.organization_id == organization_id, Lead.created_at >= since, campaign_attr.is_not(None))
            .limit(20000)
        )
    ).all()
    won_stages = set(
        (await db.execute(select(PipelineStage.id).where(PipelineStage.organization_id == organization_id, PipelineStage.category == "won"))).scalars()
    )
    deal_ids = [deal for _, _, deal in leads if deal]
    deals = {}
    if deal_ids:
        deals = {
            d.id: d
            for d in (await db.execute(select(Opportunity).where(Opportunity.organization_id == organization_id, Opportunity.id.in_(deal_ids[:20000])))).scalars()
        }
    unmatched = {"leads": 0, "won_deals": 0, "won_revenue": 0.0}
    for _, cid, deal_id in leads:
        entry = campaigns.get(str(cid))
        target = entry if entry is not None else unmatched
        target["leads"] += 1
        deal = deals.get(deal_id) if deal_id else None
        if deal is not None and deal.stage_id in won_stages:
            target["won_deals"] += 1
            target["won_revenue"] += float(deal.amount or 0)
    items = sorted(campaigns.values(), key=lambda c: (-c["cost"], c["name"] or ""))
    for item in items:
        item["cost"] = round(item["cost"], 2)
        item["won_revenue"] = round(item["won_revenue"], 2)
        item["ads_conversions"] = round(item["ads_conversions"], 2)
        item["cost_per_lead"] = round(item["cost"] / item["leads"], 2) if item["leads"] else None
        item["roas"] = round(item["won_revenue"] / item["cost"], 2) if item["cost"] else None
    totals = {
        "cost": round(sum(i["cost"] for i in items), 2),
        "clicks": sum(i["clicks"] for i in items),
        "impressions": sum(i["impressions"] for i in items),
        "ads_conversions": round(sum(i["ads_conversions"] for i in items), 2),
        "leads": sum(i["leads"] for i in items),
        "won_deals": sum(i["won_deals"] for i in items),
        "won_revenue": round(sum(i["won_revenue"] for i in items), 2),
    }
    totals["roas"] = round(totals["won_revenue"] / totals["cost"], 2) if totals["cost"] else None
    return {"days": days, "currency": settings["currency"], "campaigns": items, "totals": totals, "unmatched": unmatched}


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

_last_housekeeping = 0.0


async def _housekeeping(db: AsyncSession) -> int:
    """Give up on conversions that waited too long (Google Ads was off or never set up)."""

    global _last_housekeeping
    if time.monotonic() - _last_housekeeping < 3600:
        return 0
    _last_housekeeping = time.monotonic()
    cutoff = utcnow() - EXPIRE_PENDING_AFTER
    rows = (
        await db.execute(
            select(IntegrationObject)
            .where(
                IntegrationObject.provider.in_((conversions.ADS, conversions.GA4)),
                IntegrationObject.object_type.in_(("conversion", "ga4_event")),
                IntegrationObject.status == "pending",
                IntegrationObject.occurred_at < cutoff,
            )
            .limit(1000)
        )
    ).scalars().all()
    for row in rows:
        conversions.finish(row, "failed", "Not sent within 30 days (the integration was off or not set up).", attempt=False)
    return len(rows)


async def run_workspace(db: AsyncSession, organization_id: uuid.UUID, deadline: float, *, force_performance: bool = False) -> int:
    toggle = await _toggle(db, organization_id)
    if toggle is None or not toggle.enabled:
        return 0
    connection = await core.connection_for(db, organization_id, KEY)
    if connection is None or connection.status == "needs_reauth":
        return 0
    features, read_only = await lead_capture.plan_for(db, organization_id)
    if FEATURE not in features or read_only:
        return 0
    settings = settings_of(toggle.config)
    token, _ = await developer_token(db, organization_id)
    if not token:
        return 0
    handled = 0
    try:
        handled += await upload_due(db, connection, settings, deadline)
        await db.commit()
        handled += await resolve_clicks(db, connection, settings, deadline)
        await db.commit()
        handled += await sync_performance(db, connection, settings, deadline, force=force_performance)
        if settings["customer_id"] and _due((connection.config or {}).get("conversion_actions_at"), ACTIONS_MAX_AGE):
            await conversion_actions(db, connection, settings, refresh=True)
        await db.commit()
    except AdsBusy as exc:
        connection.config = {**(connection.config or {}), "busy_until": (utcnow() + timedelta(seconds=exc.retry_after)).isoformat()}
        await db.commit()
    except IntegrationError as exc:
        connection.last_error = str(exc)[:1000]
        await db.commit()
    return handled


async def run_due(db: AsyncSession) -> int:
    deadline = time.monotonic() + RUN_BUDGET_SECONDS
    previous = db.sync_session.info.get("automation_disabled")
    db.sync_session.info["automation_disabled"] = True
    handled = 0
    try:
        handled += await _housekeeping(db)
        await db.commit()
        rows = (
            await db.execute(
                select(IntegrationConnection.id, IntegrationConnection.organization_id)
                .join(IntegrationToggle, (IntegrationToggle.organization_id == IntegrationConnection.organization_id) & (IntegrationToggle.provider == KEY))
                .where(IntegrationConnection.provider == KEY, IntegrationConnection.user_id.is_(None), IntegrationToggle.enabled.is_(True))
                # Least recently looked at first (skipped ones included), so no workspace waits forever.
                .order_by(core.scheduler_order())
                .limit(WORKSPACES_PER_RUN * 3)
            )
        ).all()
        processed = 0
        examined: list[uuid.UUID] = []
        try:
            for connection_id, org in rows:
                if processed >= WORKSPACES_PER_RUN or time.monotonic() > deadline:
                    break
                examined.append(connection_id)
                connection = await db.get(IntegrationConnection, connection_id)
                busy = (connection.config or {}).get("busy_until") if connection else None
                if busy and not _due(busy, timedelta(0)):
                    continue
                try:
                    handled += await run_workspace(db, org, deadline)
                except Exception:  # noqa: BLE001 - one workspace mustn't stop the rest
                    logger.exception("Google Ads run failed for %s", org)
                    await db.rollback()
                processed += 1
        finally:
            await db.rollback()
            await core.mark_seen_ids(db, examined)
    finally:
        db.sync_session.info["automation_disabled"] = previous
    return handled
