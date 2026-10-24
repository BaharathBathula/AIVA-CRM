"""Integration endpoints: advertising and analytics (Google Ads, Google Analytics 4, Power BI, Looker Studio).

* ``/ads/google/...``: Google Ads accounts, developer token, conversion actions,
  the lead-form key, campaign performance next to AIVA revenue, and the conversion
  upload log (admins).
* ``/ga4/...``: validate or send a test event, and the GA4 event log (admins).
* ``/analytics-feed/...``: the read-only feed Power BI and Looker Studio read with an
  AIVA API key (people who can export records), plus the Power Query function and
  the Looker Studio connector to download.

Lead-form submissions arrive through the hub's webhook URL (``handle_webhook``).
"""

from __future__ import annotations

import io
import time
import uuid
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _bi_feed as feed
from app.integrations.providers import _conversions as conversions
from app.integrations.providers import analytics, google_ads
from app.integrations.providers._ads_api import developer_token
from app.models.integration_hub import IntegrationObject
from app.services.automation import record_audit

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

ADS = [Depends(require_feature("google_ads"))]
BI = [Depends(require_feature("bi_connectors"))]


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _ads_connection(db: AsyncSession, principal: Principal):
    try:
        return await core.active_connection(db, principal.organization_id, google_ads.KEY)
    except IntegrationError as exc:
        raise _error(exc) from exc


# ===========================================================================
# Google Ads
# ===========================================================================


@router.post("/ads/google/accounts/refresh", dependencies=ADS)
async def refresh_accounts(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """List the Google Ads accounts the connected Google user can reach again."""

    connection = await _ads_connection(db, principal)
    try:
        accounts = await google_ads.discover_accounts(db, connection)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    connection.last_error = None
    core.log(db, principal.organization_id, google_ads.KEY, "accounts_refreshed", f"Found {len(accounts)} Google Ads account{'s' if len(accounts) != 1 else ''}.", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"accounts": accounts}


@router.get("/ads/google/conversion-actions", dependencies=ADS)
async def list_conversion_actions(
    refresh: bool = False, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)
):
    connection = await _ads_connection(db, principal)
    toggle = await core.toggle_row(db, principal.organization_id, google_ads.KEY)
    settings = google_ads.settings_of(toggle.config if toggle else {})
    if not settings["customer_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Pick the Google Ads account first.")
    try:
        actions = await google_ads.conversion_actions(db, connection, settings, refresh=refresh)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    await db.commit()
    return {"items": actions, "refreshed_at": (connection.config or {}).get("conversion_actions_at")}


class DeveloperTokenPayload(BaseModel):
    token: str | None = Field(default=None, max_length=200)


@router.put("/ads/google/developer-token", dependencies=ADS)
async def set_developer_token(payload: DeveloperTokenPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """The workspace's own developer token (Google Ads → Tools → API Center). Blank removes it."""

    org = principal.organization_id
    toggle = await core.toggle_row(db, org, google_ads.KEY)
    if toggle is None or not toggle.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn Google Ads on first.")
    token = (payload.token or "").strip()
    if token and not all(ch.isalnum() or ch in "-_" for ch in token):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A developer token is letters, digits, dashes and underscores.")
    core.set_secret(toggle, "developer_token", token or None)
    toggle.updated_by_user_id = principal.user_id
    record_audit(db, organization_id=org, action="integration_secret_updated", entity_type="integration", entity_label="Google Ads developer token")
    core.log(db, org, google_ads.KEY, "developer_token", "Developer token saved." if token else "Developer token removed.", user_id=principal.user_id)
    await db.flush()
    connection = await core.connection_for(db, org, google_ads.KEY)
    found = None
    if token and connection is not None and not (connection.config or {}).get("accounts"):
        try:
            found = len(await google_ads.discover_accounts(db, connection))
            connection.last_error = None
        except IntegrationError as exc:
            connection.last_error = str(exc)[:1000]
    await db.commit()
    value, source = await developer_token(db, org)
    return {"set": bool(value), "source": source, "accounts_found": found}


@router.post("/ads/google/lead-form-key", dependencies=ADS)
async def rotate_lead_form_key(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """A new key for lead forms; forms keep failing until it's pasted into them."""

    connection = await _ads_connection(db, principal)
    key = google_ads.lead_form_key()
    core.set_secret(connection, "lead_form_key", key)
    if not connection.webhook_token:
        connection.webhook_token = core.new_webhook_token()
    record_audit(db, organization_id=principal.organization_id, action="integration_secret_updated", entity_type="integration", entity_label="Google Ads lead form key")
    core.log(db, principal.organization_id, google_ads.KEY, "lead_form_key", "Lead-form key replaced. Update it in each Google Ads lead form.", level="warning", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"key": key, "webhook_url": core.webhook_url(google_ads.KEY, connection.webhook_token)}


@router.get("/ads/google/performance", dependencies=ADS)
async def ads_performance(
    days: int = Query(default=30, ge=1, le=30), principal: Principal = Depends(require_permission("analytics.reports")), db: AsyncSession = Depends(get_db)
):
    """Spend, clicks and conversions per campaign next to the leads and won revenue in AIVA."""

    if not await core.is_enabled(db, principal.organization_id, google_ads.KEY):
        raise HTTPException(status.HTTP_409_CONFLICT, "Google Ads is turned off for this workspace.")
    report = await google_ads.performance_report(db, principal.organization_id, days)
    connection = await core.connection_for(db, principal.organization_id, google_ads.KEY)
    report["synced_at"] = (connection.config or {}).get("perf_synced_at") if connection else None
    return report


@router.post("/ads/google/sync", dependencies=ADS)
async def sync_now(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Upload due conversions and refresh campaign performance now."""

    connection = await _ads_connection(db, principal)
    connection.config = {k: v for k, v in (connection.config or {}).items() if k not in {"busy_until", "clicks_checked_at"}}
    db.sync_session.info["automation_disabled"] = True
    handled = await google_ads.run_workspace(db, principal.organization_id, time.monotonic() + 45, force_performance=True)
    connection = await core.connection_for(db, principal.organization_id, google_ads.KEY)
    error = connection.last_error if connection else None
    core.log(db, principal.organization_id, google_ads.KEY, "synced", "Synced with Google Ads." if not error else f"Sync finished with a problem: {error}", level="info" if not error else "warning", user_id=principal.user_id)
    await db.commit()
    return {"handled": handled, "error": error}


def _status_filter(value: str | None) -> str | None:
    if value and value not in {"pending", "sent", "skipped", "failed", "duplicate"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown status.")
    return value


async def _objects(db: AsyncSession, organization_id: uuid.UUID, provider: str, object_type: str, status_value: str | None, limit: int) -> list[dict[str, Any]]:
    query = select(IntegrationObject).where(
        IntegrationObject.organization_id == organization_id, IntegrationObject.provider == provider, IntegrationObject.object_type == object_type
    )
    if status_value:
        query = query.where(IntegrationObject.status == status_value)
    rows = (await db.execute(query.order_by(IntegrationObject.occurred_at.desc()).limit(limit))).scalars().all()
    return [conversions.object_out(r) for r in rows]


async def _retry(db: AsyncSession, principal: Principal, provider: str, object_type: str, object_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.id == object_id, IntegrationObject.organization_id == principal.organization_id,
                IntegrationObject.provider == provider, IntegrationObject.object_type == object_type,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    if row.status in {"sent", "duplicate"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "It was already sent.")
    conversions.retry_now(row)
    core.log(db, principal.organization_id, provider, "retry", f"Queued again: {row.title}.", user_id=principal.user_id)
    await db.commit()
    return conversions.object_out(row)


@router.get("/ads/google/conversions", dependencies=ADS)
async def ads_conversions(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await _objects(db, principal.organization_id, google_ads.KEY, "conversion", _status_filter(status_filter), limit)}


@router.post("/ads/google/conversions/{object_id}/retry", dependencies=ADS)
async def retry_conversion(object_id: uuid.UUID, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    return await _retry(db, principal, google_ads.KEY, "conversion", object_id)


# ===========================================================================
# Google Analytics 4
# ===========================================================================


class TestEventPayload(BaseModel):
    moment: str = Field(default="lead_created", max_length=80)
    send: bool = False


@router.post("/ga4/test-event", dependencies=BI)
async def ga4_test_event(payload: TestEventPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Validate a sample event with Google (and optionally send it for real)."""

    org = principal.organization_id
    try:
        connection = await core.active_connection(db, org, analytics.GA4)
    except IntegrationError as exc:
        raise _error(exc) from exc
    toggle = await core.toggle_row(db, org, analytics.GA4)
    settings = analytics.ga_settings(toggle.config if toggle else {})
    if payload.moment not in settings["events"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick one of the CRM moments.")
    body = analytics.sample_payload(org, settings, payload.moment)
    try:
        messages = await analytics.validate(connection, settings, body)
        sent = False
        if payload.send and not messages:
            measurement_id, secret = await analytics._credentials(connection)
            response = await analytics.http("POST", analytics._endpoint(settings), params={"measurement_id": measurement_id, "api_secret": secret}, json=body)
            sent = response.status_code < 300
    except IntegrationError as exc:
        raise _error(exc) from exc
    core.log(
        db, org, analytics.GA4, "test_event",
        ("Sample event sent to Google Analytics." if sent else "Sample event validated by Google Analytics.") if not messages else f"Google Analytics found {len(messages)} problem{'s' if len(messages) != 1 else ''} with a sample event.",
        level="success" if not messages else "warning", connection=connection, user_id=principal.user_id,
    )
    await db.commit()
    return {"valid": not messages, "messages": messages, "sent": sent, "payload": body}


@router.get("/ga4/events", dependencies=BI)
async def ga4_events(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await _objects(db, principal.organization_id, analytics.GA4, "ga4_event", _status_filter(status_filter), limit)}


@router.post("/ga4/events/{object_id}/retry", dependencies=BI)
async def ga4_retry(object_id: uuid.UUID, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    return await _retry(db, principal, analytics.GA4, "ga4_event", object_id)


# ===========================================================================
# Analytics feed (Power BI, Looker Studio)
# ===========================================================================

FEED_REQUESTS_PER_MINUTE = 120
_feed_hits: dict[str, list[float]] = {}
_feed_logged: dict[str, float] = {}


def _limited(identity: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _feed_hits.get(identity, []) if now - t < 60]
    hits.append(now)
    _feed_hits[identity] = hits[-FEED_REQUESTS_PER_MINUTE - 5:]
    if len(_feed_hits) > 10000:
        _feed_hits.clear()
    return len(hits) > FEED_REQUESTS_PER_MINUTE


async def feed_principal(request: Request, principal: Principal = Depends(require_permission("records.export")), db: AsyncSession = Depends(get_db)) -> Principal:
    if not principal.has("bi_connectors"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Analytics and BI connectors aren't included in this workspace's plan.")
    org = principal.organization_id
    if not (await core.is_enabled(db, org, analytics.POWER_BI) or await core.is_enabled(db, org, analytics.LOOKER)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn on Power BI or Looker Studio in Integrations to use the analytics feed.")
    identity = f"key:{principal.api_key_id}" if principal.api_key_id else f"user:{principal.user_id}"
    if _limited(identity):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests to the analytics feed. Wait a minute.", headers={"Retry-After": "60"})
    return principal


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _audit_feed(db: AsyncSession, request: Request, principal: Principal, dataset: str, rows: int) -> None:
    """HIPAA workspaces log every feed read; everyone sees hourly usage in the activity log."""

    from app.models.organization import Organization
    from app.models.platform import AuditLog
    from app.api.dependencies.auth import client_ip

    organization = await db.get(Organization, principal.organization_id)
    hipaa = bool((organization.settings or {}).get("hipaa_mode")) and principal.has("hipaa") if organization else False
    if hipaa:
        db.add(
            AuditLog(
                id=uuid.uuid4(), organization_id=principal.organization_id, actor_user_id=principal.user_id, action="export",
                entity_type=dataset[:50], entity_label=f"Analytics feed: {dataset}", changes={"rows": rows, "api_key": bool(principal.api_key_id)},
                ip_address=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300] or None,
            )
        )
    identity = f"{principal.api_key_id or principal.user_id}"
    now = time.monotonic()
    logged = False
    if now - _feed_logged.get(identity, 0) > 3600:
        _feed_logged[identity] = now
        logged = True
        agent = request.headers.get("user-agent") or ""
        provider = analytics.LOOKER if "Google-Apps-Script" in agent else analytics.POWER_BI
        if not await core.is_enabled(db, principal.organization_id, provider):
            provider = analytics.LOOKER if provider == analytics.POWER_BI else analytics.POWER_BI
        core.log(
            db, principal.organization_id, provider, "feed_read",
            f"The analytics feed was read{' with an API key' if principal.api_key_id else ''} ({dataset}).", user_id=principal.user_id,
        )
    if hipaa or logged:
        await db.commit()


@router.get("/analytics-feed", dependencies=BI)
async def feed_index(response: Response, principal: Principal = Depends(feed_principal), db: AsyncSession = Depends(get_db)):
    _no_store(response)
    base = analytics.feed_base()
    return {
        "datasets": [
            {
                "key": d.key, "label": d.label, "description": d.description,
                "url": f"{base}/api/aiva/analytics-feed/{d.key}", "schema_url": f"{base}/api/aiva/analytics-feed/{d.key}/schema",
            }
            for d in feed.DATASETS.values()
        ],
        "page_size": feed.MAX_LIMIT,
        "paging": "Follow next_cursor (or next) until it is null. Add updated_since=<ISO time> for rows changed since then.",
    }


@router.get("/analytics-feed/connectors/power-query.m", dependencies=BI)
async def power_query(principal: Principal = Depends(feed_principal)):
    text = feed.power_query_function(analytics.feed_base()) + "\n\n" + feed.power_query_examples() + "\n"
    return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="AivaFeed.pq"', "Cache-Control": "no-store"})


@router.get("/analytics-feed/connectors/looker-studio.zip", dependencies=BI)
async def looker_connector(principal: Principal = Depends(feed_principal)):
    base = analytics.feed_base()
    files = feed.looker_connector_files(base, f"{base}/icon-192.png")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"aiva-looker-studio-connector/{name}", content)
    return Response(
        buffer.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="aiva-looker-studio-connector.zip"', "Cache-Control": "no-store"},
    )


@router.get("/analytics-feed/{dataset}/schema", dependencies=BI)
async def feed_schema(dataset: str, response: Response, principal: Principal = Depends(feed_principal), db: AsyncSession = Depends(get_db)):
    _no_store(response)
    try:
        return await feed.schema(db, principal.organization_id, dataset)
    except IntegrationError as exc:
        raise _error(exc) from exc


@router.get("/analytics-feed/{dataset}", dependencies=BI)
async def feed_rows(
    dataset: str,
    request: Request,
    response: Response,
    cursor: str | None = Query(default=None, max_length=500),
    updated_since: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=feed.DEFAULT_LIMIT, ge=1, le=feed.MAX_LIMIT),
    principal: Principal = Depends(feed_principal),
    db: AsyncSession = Depends(get_db),
):
    _no_store(response)
    try:
        since = feed.parse_since(updated_since)
        result = await feed.page(db, principal.organization_id, dataset, cursor=cursor, updated_since=since, limit=limit)
    except IntegrationError as exc:
        raise _error(exc) from exc
    if result["next_cursor"]:
        params = {"cursor": result["next_cursor"], "limit": str(limit)}
        if updated_since:
            params["updated_since"] = updated_since
        from urllib.parse import urlencode

        result["next"] = f"{analytics.feed_base()}/api/aiva/analytics-feed/{dataset}?{urlencode(params)}"
    else:
        result["next"] = None
    result["generated_at"] = core.utcnow().isoformat().replace("+00:00", "Z")
    await _audit_feed(db, request, principal, dataset, result["count"])
    return result
