"""Facebook (and Instagram) Lead Ads: lead-form submissions become AIVA leads.

* Connecting: an admin signs in with Facebook (workspace OAuth, long-lived user
  token). AIVA lists the pages they manage and keeps each page's token
  (encrypted; page tokens made from a long-lived user token don't expire).
* Choosing pages: subscribing a page (``POST /{page}/subscribed_apps
  ?subscribed_fields=leadgen``) makes Meta send its new leads to AIVA's one
  app-wide webhook, ``/public/integrations/facebook_lead_ads/webhook/app``.
* Receiving: Meta's ``GET`` verification is answered for the server's app (token
  from ``FACEBOOK_WEBHOOK_VERIFY_TOKEN`` or derived from the app secret) and for
  any workspace that uses its own Meta app (a per-workspace token). ``POST``
  bodies must carry a valid ``X-Hub-Signature-256`` made with the app secret of
  the workspace that subscribed the page; each ``leadgen`` change is routed by
  page id, the lead is fetched with the page token and mapped to a lead.
* Safety net: every 15 minutes the job asks each subscribed form for leads
  created since the last check, so a missed webhook never loses a lead. Leads
  are imported once (by Facebook lead id).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets as pysecrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import sha256
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import lead_capture
from app.integrations.providers.lead_capture import Answer, Capture
from app.models.integration_hub import IntegrationConnection, IntegrationToggle

logger = logging.getLogger("aiva.integrations.facebook")

KEY = "facebook_lead_ads"
GRAPH = "https://graph.facebook.com/v21.0"
LEAD_FIELDS = "id,created_time,field_data,form_id,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,platform,is_organic"
LEAD_FIELDS_BASIC = "id,created_time,field_data,form_id,ad_id,campaign_id,platform,is_organic"
FORM_FIELDS = "id,name,status,locale,created_time,questions{key,label,type}"
BACKFILL_EVERY = timedelta(minutes=15)
BACKFILL_FIRST_WINDOW = timedelta(hours=24)
MAX_BODY = 1024 * 1024
DEFAULT_SOURCE = "facebook_ads"
STANDARD_LABELS = {
    "full_name": "Full name", "first_name": "First name", "last_name": "Last name", "email": "Email", "work_email": "Work email",
    "phone_number": "Phone number", "work_phone_number": "Work phone", "company_name": "Company name", "job_title": "Job title",
    "street_address": "Street address", "city": "City", "state": "State", "province": "Province", "country": "Country",
    "post_code": "Post code", "zip_code": "ZIP code", "date_of_birth": "Date of birth", "gender": "Gender",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Verify tokens and signatures
# ===========================================================================


def server_verify_token() -> str | None:
    configured = os.getenv("FACEBOOK_WEBHOOK_VERIFY_TOKEN", "").strip()
    if configured:
        return configured
    secret = get_settings().facebook_app_secret
    if not secret:
        return None
    return hmac.new(secret.encode(), b"aiva-facebook-leadgen-verify", hashlib.sha256).hexdigest()[:40]


async def ensure_verify_token(db: AsyncSession, organization_id: uuid.UUID) -> str | None:
    """The verify token for a workspace that uses its own Meta app."""

    toggle = await core.toggle_row(db, organization_id, KEY)
    if toggle is None:
        return None
    token = core.secret(toggle, "verify_token")
    if not token:
        token = "aiva_" + pysecrets.token_urlsafe(24)
        core.set_secret(toggle, "verify_token", token)
        toggle.config = {**(toggle.config or {}), "verify_token_hash": sha256(token)}
    return token


def signature_ok(app_secret: str | None, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1].strip())


# ===========================================================================
# Pages and forms
# ===========================================================================


def _pages(connection: IntegrationConnection) -> list[dict[str, Any]]:
    return list((connection.config or {}).get("pages") or [])


def page_token(connection: IntegrationConnection, page_id: str) -> str | None:
    return core.secret(connection, f"page:{page_id}")


def _set_config(connection: IntegrationConnection, **values: Any) -> None:
    connection.config = {**(connection.config or {}), **values}


async def refresh_pages(db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
    """Read the pages the person manages and keep their tokens."""

    url: str | None = f"{GRAPH}/me/accounts"
    params: dict[str, Any] | None = {"fields": "id,name,category,access_token,tasks", "limit": 100}
    found: list[dict[str, Any]] = []
    for _ in range(10):
        if not url:
            break
        response = await core.request(db, connection, "GET", url, params=params)
        payload = response.json()
        found.extend(payload.get("data") or [])
        url = (payload.get("paging") or {}).get("next")
        params = None
    previous = {p["id"]: p for p in _pages(connection)}
    pages = []
    keep = {}
    for item in found:
        page_id = str(item.get("id") or "")
        if not page_id:
            continue
        tasks = list(item.get("tasks") or [])
        if item.get("access_token"):
            keep[f"page:{page_id}"] = item["access_token"]
        old = previous.get(page_id, {})
        pages.append(
            {
                "id": page_id,
                "name": item.get("name") or page_id,
                "category": item.get("category"),
                "tasks": tasks,
                "can_leads": not tasks or bool({"ADVERTISE", "MANAGE"} & set(tasks)),
                "subscribed": bool(old.get("subscribed")),
                "subscribed_at": old.get("subscribed_at"),
                "error": None,
            }
        )
    secrets_now = {k: v for k, v in (connection.secrets or {}).items() if not k.startswith("page:")}
    connection.secrets = secrets_now
    for name, token in keep.items():
        core.set_secret(connection, name, token)
    # Pages the person can no longer manage stop syncing.
    current = {p["id"] for p in pages}
    for page_id, old in previous.items():
        if old.get("subscribed") and page_id not in current:
            pages.append({**old, "subscribed": False, "error": "You no longer manage this page, so its leads can't be read."})
    _set_config(connection, pages=pages, subscribed_page_ids=[p["id"] for p in pages if p.get("subscribed")], pages_synced_at=utcnow().isoformat())
    return pages


async def set_page_subscription(db: AsyncSession, connection: IntegrationConnection, page_id: str, subscribed: bool, user_id: uuid.UUID | None) -> dict[str, Any]:
    pages = _pages(connection)
    page = next((p for p in pages if p["id"] == page_id), None)
    if page is None:
        raise IntegrationError("That page isn't one you manage. Refresh the list of pages.", 404)
    token = page_token(connection, page_id)
    if subscribed and not token:
        raise IntegrationError("AIVA doesn't have access to that page any more. Refresh the pages or reconnect Facebook.", 409)
    if token:
        try:
            if subscribed:
                await core.request(db, connection, "POST", f"{GRAPH}/{page_id}/subscribed_apps", token=token, data={"subscribed_fields": "leadgen"})
            else:
                await core.request(db, connection, "DELETE", f"{GRAPH}/{page_id}/subscribed_apps", token=token)
        except IntegrationError:
            if subscribed:
                raise
            # Unsubscribing a page we can't reach any more: stop syncing it anyway.
    page["subscribed"] = subscribed
    page["subscribed_at"] = utcnow().isoformat() if subscribed else None
    page["error"] = None
    _set_config(connection, pages=pages, subscribed_page_ids=[p["id"] for p in pages if p.get("subscribed")])
    if subscribed:
        backfill = dict((connection.config or {}).get("backfill") or {})
        backfill.setdefault(page_id, (utcnow() - BACKFILL_FIRST_WINDOW).isoformat())
        _set_config(connection, backfill=backfill)
    core.log(db, connection.organization_id, KEY, "page_subscribed" if subscribed else "page_unsubscribed",
             f"{'Syncing' if subscribed else 'Stopped syncing'} leads from {page['name']}.", level="success" if subscribed else "info",
             connection=connection, user_id=user_id)
    return page


async def load_forms(db: AsyncSession, connection: IntegrationConnection, page_id: str) -> list[dict[str, Any]]:
    token = page_token(connection, page_id)
    if not token:
        raise IntegrationError("AIVA doesn't have access to that page. Refresh the pages.", 409)
    forms: list[dict[str, Any]] = []
    url: str | None = f"{GRAPH}/{page_id}/leadgen_forms"
    params: dict[str, Any] | None = {"fields": FORM_FIELDS, "limit": 100}
    for _ in range(10):
        if not url:
            break
        response = await core.request(db, connection, "GET", url, token=token, params=params)
        payload = response.json()
        for item in payload.get("data") or []:
            forms.append(
                {
                    "id": str(item.get("id")),
                    "name": item.get("name") or str(item.get("id")),
                    "status": item.get("status"),
                    "questions": [
                        {"key": q.get("key"), "label": q.get("label") or STANDARD_LABELS.get(q.get("key") or "", q.get("key")), "type": q.get("type")}
                        for q in ((item.get("questions") or []) if isinstance(item.get("questions"), list) else [])
                        if q.get("key")
                    ],
                }
            )
        url = (payload.get("paging") or {}).get("next")
        params = None
    cache = dict((connection.config or {}).get("lead_forms") or {})
    cache[page_id] = forms[:200]
    _set_config(connection, lead_forms=cache)
    return forms


def _form_info(connection: IntegrationConnection, form_id: str) -> dict[str, Any] | None:
    for forms in ((connection.config or {}).get("lead_forms") or {}).values():
        for form in forms:
            if form.get("id") == form_id:
                return form
    return None


def _page_name(connection: IntegrationConnection, page_id: str) -> str | None:
    return next((p.get("name") for p in _pages(connection) if p.get("id") == page_id), None)


# ===========================================================================
# Leads
# ===========================================================================


async def fetch_lead(db: AsyncSession, connection: IntegrationConnection, leadgen_id: str, token: str) -> dict[str, Any]:
    try:
        response = await core.request(db, connection, "GET", f"{GRAPH}/{leadgen_id}", token=token, params={"fields": LEAD_FIELDS})
    except IntegrationError:
        # Ad and campaign names need extra ad-account access; retry with the basics.
        response = await core.request(db, connection, "GET", f"{GRAPH}/{leadgen_id}", token=token, params={"fields": LEAD_FIELDS_BASIC})
    return response.json()


def answers_from(lead: dict[str, Any], form: dict[str, Any] | None) -> list[Answer]:
    labels = {q.get("key"): q.get("label") for q in (form or {}).get("questions") or []}
    answers = []
    for item in lead.get("field_data") or []:
        name = str(item.get("name") or "")
        if not name:
            continue
        values = item.get("values") or []
        value = ", ".join(str(v) for v in values if v not in (None, ""))
        label = labels.get(name) or STANDARD_LABELS.get(name) or name.replace("_", " ").capitalize()
        answers.append(Answer(key=name, label=str(label)[:200], value=value[:5000]))
    return answers


async def import_lead(db: AsyncSession, connection: IntegrationConnection, page_id: str, lead: dict[str, Any], *, via: str) -> str:
    """Create or update the AIVA lead for one Facebook lead. Returns the outcome."""

    org = connection.organization_id
    leadgen_id = str(lead.get("id") or "")
    form_id = str(lead.get("form_id") or "")
    form = _form_info(connection, form_id)
    config = await core.workspace_config(db, org, KEY)
    base = config.get("defaults") or {}
    settings = (config.get("forms") or {}).get(form_id) or {}
    form_name = (form or {}).get("name") or (f"Form {form_id}" if form_id else "Lead form")
    if settings.get("enabled") is False:
        core.log(db, org, KEY, "lead_ignored", f"Ignored a lead from {form_name} (that form is switched off).", connection=connection, detail={"form_id": form_id})
        return "ignored"
    owner = settings.get("owner_user_id") or base.get("owner_user_id")
    page_name = _page_name(connection, page_id)
    capture = Capture(
        provider=KEY,
        lead_source=settings.get("lead_source") or base.get("lead_source") or DEFAULT_SOURCE,
        answers=answers_from(lead, form),
        mapping=dict(settings.get("mapping") or {}),
        owner_user_id=uuid.UUID(owner) if owner else connection.connected_by_user_id,
        on_duplicate=base.get("on_duplicate") if base.get("on_duplicate") in lead_capture.DUPLICATE_MODES else "update",
        tags=lead_capture.clean_tags([*(base.get("tags") or []), *(settings.get("tags") or [])]),
        attribution={
            "integration": KEY,
            "page": page_name,
            "page_id": page_id,
            "form": form_name,
            "form_id": form_id,
            "ad_id": lead.get("ad_id"),
            "ad_name": lead.get("ad_name"),
            "adset_name": lead.get("adset_name"),
            "campaign_id": lead.get("campaign_id"),
            "campaign_name": lead.get("campaign_name"),
            "platform": lead.get("platform"),
            "is_organic": lead.get("is_organic"),
            "utm_source": "instagram" if str(lead.get("platform") or "").lower() in {"ig", "instagram"} else "facebook",
            "utm_medium": "organic" if lead.get("is_organic") else "paid_social",
            "utm_campaign": lead.get("campaign_name"),
            "lead_created_at": lead.get("created_time"),
        },
        external_key="facebook_lead_id",
        external_id=leadgen_id or None,
        origin=f"Facebook lead form {form_name}",
    )
    await lead_capture.prepare(db, org)
    outcome = await lead_capture.save(db, org, capture)
    record = outcome.lead
    label = f"{record.first_name} {record.last_name}".strip() if record is not None else "a lead"
    messages = {
        "created": ("lead_created", "success", f"New lead {label} from {form_name}" + (f" ({page_name})" if page_name else "") + "."),
        "updated": ("lead_updated", "info", f"{form_name}: {label} was already a lead, so the lead was updated."),
        "skipped": ("lead_skipped", "info", f"{form_name}: skipped a duplicate of {label}."),
        "duplicate": ("lead_duplicate", "info", f"{form_name}: lead {leadgen_id} was already imported."),
    }
    if outcome.result != "duplicate" or via == "webhook":
        action, level, message = messages[outcome.result]
        core.log(db, org, KEY, action, message, level=level, connection=connection,
                 detail={"form_id": form_id, "page_id": page_id, "lead_id": str(record.id) if record else None, "via": via, "result": outcome.result})
    connection.last_used_at = utcnow()
    return outcome.result


# ===========================================================================
# Webhook
# ===========================================================================


async def _verify_get(db: AsyncSession, request: Request) -> Response:
    params = request.query_params
    mode, token, challenge = params.get("hub.mode"), params.get("hub.verify_token") or "", params.get("hub.challenge") or ""
    if mode != "subscribe" or not token or not challenge or len(challenge) > 200 or not challenge.replace("-", "").replace("_", "").isalnum():
        return JSONResponse({"error": "bad verification request"}, status_code=400)
    server = server_verify_token()
    if server and hmac.compare_digest(server.encode(), token.encode()):
        return PlainTextResponse(challenge)
    match = (
        await db.execute(
            select(IntegrationToggle.id).where(IntegrationToggle.provider == KEY, IntegrationToggle.config["verify_token_hash"].astext == sha256(token)).limit(1)
        )
    ).scalar_one_or_none()
    if match is not None:
        return PlainTextResponse(challenge)
    return JSONResponse({"error": "verify token doesn't match"}, status_code=403)


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if request.method == "GET":
        return await _verify_get(db, request)
    if connection is not None:
        # Per-workspace URLs aren't used for Meta; everything comes to /webhook/app.
        return JSONResponse({"error": "use the app webhook URL"}, status_code=404)
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"error": "too large"}, status_code=413)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return JSONResponse({"ok": True, "ignored": "not a page event"})
    changes: list[tuple[str, dict[str, Any]]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") == "leadgen" and isinstance(change.get("value"), dict):
                page_id = str(change["value"].get("page_id") or entry.get("id") or "")
                if page_id:
                    changes.append((page_id, change["value"]))
    if not changes:
        return JSONResponse({"ok": True, "ignored": "no lead events"})

    page_ids = sorted({page_id for page_id, _ in changes})
    candidates = (
        await db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == KEY,
                or_(*(IntegrationConnection.config["subscribed_page_ids"].contains([page_id]) for page_id in page_ids)),
            )
        )
    ).scalars().all()
    if not candidates:
        return JSONResponse({"ok": True, "ignored": "no workspace syncs these pages"})

    signature = request.headers.get("x-hub-signature-256")
    verified: list[IntegrationConnection] = []
    for candidate in candidates:
        app = await core.oauth_app(db, candidate.organization_id, "facebook")
        if signature_ok(app.get("client_secret"), body, signature):
            verified.append(candidate)
    if not verified:
        logger.warning("Facebook lead webhook with a bad signature for pages %s", page_ids)
        for candidate in candidates:
            core.log(db, candidate.organization_id, KEY, "webhook_rejected", "A lead notification was refused: its signature didn't match the Meta app's secret.",
                     level="warning", connection=candidate)
        return JSONResponse({"error": "signature doesn't match"}, status_code=401)

    results: dict[str, int] = {}
    for candidate in verified:
        if not await core.is_enabled(db, candidate.organization_id, KEY):
            core.log(db, candidate.organization_id, KEY, "webhook_ignored", "A lead arrived while Facebook Lead Ads was off; it was ignored.", level="warning", connection=candidate)
            continue
        subscribed = set((candidate.config or {}).get("subscribed_page_ids") or [])
        for page_id, value in changes:
            if page_id not in subscribed:
                continue
            leadgen_id = str(value.get("leadgen_id") or "")
            token = page_token(candidate, page_id)
            if not leadgen_id or not token:
                continue
            try:
                lead = await fetch_lead(db, candidate, leadgen_id, token)
                if value.get("form_id") and not lead.get("form_id"):
                    lead["form_id"] = value.get("form_id")
                outcome = await import_lead(db, candidate, page_id, lead, via="webhook")
                await db.commit()
                results[outcome] = results.get(outcome, 0) + 1
            except IntegrationError as exc:
                await db.rollback()
                await db.refresh(candidate)
                core.log(db, candidate.organization_id, KEY, "lead_failed", f"Couldn't import lead {leadgen_id}: {exc} AIVA will try again shortly.",
                         level="error", connection=candidate, detail={"leadgen_id": leadgen_id, "page_id": page_id})
                await db.commit()
                results["failed"] = results.get("failed", 0) + 1
    # Always 200 for a verified delivery: missed leads are picked up by the backfill job.
    return JSONResponse({"ok": True, **results})


# ===========================================================================
# Backfill job
# ===========================================================================


async def backfill(db: AsyncSession, connection: IntegrationConnection, *, force: bool = False) -> int:
    """Import leads created since the last check on every subscribed page's forms."""

    imported = 0
    now = utcnow()
    marks = dict((connection.config or {}).get("backfill") or {})
    for page in _pages(connection):
        if not page.get("subscribed"):
            continue
        page_id = page["id"]
        token = page_token(connection, page_id)
        if not token:
            continue
        since_text = marks.get(page_id)
        try:
            since = datetime.fromisoformat(since_text) if since_text else now - BACKFILL_FIRST_WINDOW
        except ValueError:
            since = now - BACKFILL_FIRST_WINDOW
        forms = await load_forms(db, connection, page_id)
        for form in forms:
            if form.get("status") not in (None, "ACTIVE"):
                continue
            filtering = json.dumps([{"field": "time_created", "operator": "GREATER_THAN", "value": int(since.timestamp()) - 60}])
            url: str | None = f"{GRAPH}/{form['id']}/leads"
            params: dict[str, Any] | None = {"fields": LEAD_FIELDS_BASIC, "filtering": filtering, "limit": 100}
            for _ in range(20):
                if not url:
                    break
                response = await core.request(db, connection, "GET", url, token=token, params=params)
                payload = response.json()
                for lead in payload.get("data") or []:
                    lead.setdefault("form_id", form["id"])
                    if await import_lead(db, connection, page_id, lead, via="backfill") == "created":
                        imported += 1
                url = (payload.get("paging") or {}).get("next")
                params = None
        marks[page_id] = now.isoformat()
        _set_config(connection, backfill=dict(marks))
        await db.commit()  # keep each page's progress even if a later page fails
    _set_config(connection, backfill=marks, last_backfill_at=now.isoformat())
    connection.last_sync_at = now
    if imported:
        core.log(db, connection.organization_id, KEY, "backfill", f"Caught up on {imported} lead{'s' if imported != 1 else ''} that hadn't arrived by webhook.",
                 level="success", connection=connection)
    return imported


async def run_due(db: AsyncSession) -> int:
    info = db.sync_session.info
    saved = {name: info.get(name) for name in ("automation_disabled", "automation", "features")}
    handled = 0
    try:
        rows = (
            await db.execute(select(IntegrationConnection).where(IntegrationConnection.provider == KEY, IntegrationConnection.status == "connected"))
        ).scalars().all()
        now = utcnow()
        due = [
            (row.id, row.organization_id)
            for row in rows
            if (row.config or {}).get("subscribed_page_ids") and not (row.last_sync_at and now - row.last_sync_at < BACKFILL_EVERY)
        ]
        for connection_id, organization_id in due:
            if not await core.is_enabled(db, organization_id, KEY):
                continue
            connection = await db.get(IntegrationConnection, connection_id)
            if connection is None:
                continue
            try:
                handled += await backfill(db, connection)
                await db.commit()
            except IntegrationError as exc:
                await db.rollback()
                fresh = await db.get(IntegrationConnection, connection_id, populate_existing=True)
                if fresh is not None:
                    fresh.last_error = str(exc)[:1000]
                    fresh.last_sync_at = now
                    core.log(db, fresh.organization_id, KEY, "backfill_failed", f"Couldn't check for missed leads: {exc}", level="error", connection=fresh)
                    await db.commit()
    finally:
        for name, value in saved.items():
            if value is None:
                info.pop(name, None)
            else:
                info[name] = value
    return handled


# ===========================================================================
# Hub hooks
# ===========================================================================


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    if enabled:
        await ensure_verify_token(db, organization_id)


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await ensure_verify_token(db, connection.organization_id)
    pages = await refresh_pages(db, connection)
    resubscribed = 0
    for page in pages:
        if page.get("subscribed") and page_token(connection, page["id"]):
            try:
                await set_page_subscription(db, connection, page["id"], True, connection.connected_by_user_id)
                resubscribed += 1
            except IntegrationError as exc:
                page["error"] = str(exc)
    core.log(db, connection.organization_id, KEY, "pages_loaded",
             f"Found {len(pages)} page{'s' if len(pages) != 1 else ''}. Choose which ones send leads to AIVA.", connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    for page in _pages(connection):
        token = page_token(connection, page["id"])
        if page.get("subscribed") and token:
            try:
                await core.request(db, connection, "DELETE", f"{GRAPH}/{page['id']}/subscribed_apps", token=token)
            except IntegrationError:
                continue


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    pages = await refresh_pages(db, connection)
    subscribed = [p for p in pages if p.get("subscribed")]
    if not pages:
        return "Connected, but this Facebook account doesn't manage any pages."
    return f"Connected. {len(pages)} page{'s' if len(pages) != 1 else ''} found; {len(subscribed)} sending leads to AIVA."


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    from app.services.custom_fields import fields_for

    org = principal.organization_id
    custom_keys = {f.key for f in await fields_for(db, org, "lead")}
    cleaned: dict[str, Any] = {}
    if "defaults" in config:
        raw = config.get("defaults") or {}
        mode = raw.get("on_duplicate") or "update"
        if mode not in lead_capture.DUPLICATE_MODES:
            raise IntegrationError("Choose what happens to duplicates: update, skip or create.", 422)
        owner = await lead_capture.valid_owner(db, org, raw.get("owner_user_id"))
        cleaned["defaults"] = {
            "lead_source": (str(raw.get("lead_source") or DEFAULT_SOURCE).strip()[:80]) or DEFAULT_SOURCE,
            "owner_user_id": str(owner) if owner else None,
            "on_duplicate": mode,
            "tags": lead_capture.clean_tags(raw.get("tags")),
        }
    if "forms" in config:
        forms_in = config.get("forms") or {}
        if not isinstance(forms_in, dict):
            raise IntegrationError("Form settings must be an object.", 422)
        forms: dict[str, Any] = {}
        for form_id, raw in list(forms_in.items())[:300]:
            if not isinstance(raw, dict) or not str(form_id).isdigit():
                continue
            owner = await lead_capture.valid_owner(db, org, raw.get("owner_user_id"))
            forms[str(form_id)] = {
                "enabled": raw.get("enabled", True) is not False,
                "lead_source": (str(raw.get("lead_source") or "").strip()[:80]) or None,
                "owner_user_id": str(owner) if owner else None,
                "tags": lead_capture.clean_tags(raw.get("tags")),
                "mapping": lead_capture.clean_mapping(raw.get("mapping"), custom_keys),
            }
        cleaned["forms"] = forms
    return cleaned


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    from app.services.custom_fields import fields_for

    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    app = await core.oauth_app(db, org, "facebook")
    config = await core.workspace_config(db, org, KEY)
    manage = principal.can("admin.settings")
    verify_token = None
    if manage and app.get("source") == "workspace":
        verify_token = await ensure_verify_token(db, org)
        await db.commit()
    pages = _pages(connection) if connection else []
    lead_forms = (connection.config or {}).get("lead_forms") or {} if connection else {}
    return {
        "callback_url": core.app_webhook_url(KEY),
        "app_source": app.get("source"),
        "verify_token": verify_token,
        "pages": pages,
        "lead_forms": lead_forms,
        "defaults": {
            "lead_source": (config.get("defaults") or {}).get("lead_source") or DEFAULT_SOURCE,
            "owner_user_id": (config.get("defaults") or {}).get("owner_user_id"),
            "on_duplicate": (config.get("defaults") or {}).get("on_duplicate") or "update",
            "tags": (config.get("defaults") or {}).get("tags") or [],
        },
        "forms": config.get("forms") or {},
        "targets": [{"key": k, "label": v} for k, v in lead_capture.TARGETS.items()],
        "custom_fields": [{"key": f"cf.{f.key}", "label": f.label} for f in await fields_for(db, org, "lead")],
        "last_backfill_at": (connection.config or {}).get("last_backfill_at") if connection else None,
        "pages_synced_at": (connection.config or {}).get("pages_synced_at") if connection else None,
        "testing_tool_url": "https://developers.facebook.com/tools/lead-ads-testing",
    }
