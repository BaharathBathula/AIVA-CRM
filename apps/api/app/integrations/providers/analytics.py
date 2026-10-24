"""Google Analytics 4 (Measurement Protocol), Power BI and Looker Studio.

**Google Analytics 4.** CRM moments (new lead, lead qualified, deal won...) are sent
as GA4 events (``generate_lead``, ``qualify_lead``, ``close_convert_lead``...) with
their value and currency, so they can be marked as key events and reported by channel
and campaign. Each event carries the visitor's GA client ID, read from the ``_ga``
cookie by AIVA's tracking script or kept on the lead by AIVA forms. When none is known
AIVA can use a stable stand-in ID for the person: the event still counts, but GA
can't tie it to the visit's channel. No personal data (names, emails, phone numbers)
is ever sent. Events are queued with the record change and sent by the scheduler with
retries (see ``_conversions``).

**Power BI and Looker Studio.** Both read the read-only analytics feed
(``/analytics-feed``, see ``_bi_feed``) with an AIVA API key. Power BI gets a
ready-made Power Query function; Looker Studio gets a community connector to deploy
with Apps Script.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _conversions as conversions
from app.integrations.providers import _bi_feed as feed
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationToggle

logger = logging.getLogger("aiva.integrations.analytics")

GA4 = "google_analytics"
POWER_BI = "power_bi"
LOOKER = "looker_studio"
BI_KEYS = (POWER_BI, LOOKER)

MEASUREMENT_ID = re.compile(r"^G-[A-Z0-9]{4,20}$")
EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
RESERVED_PREFIXES = ("_", "firebase_", "ga_", "google_", "gtag.")
RESERVED_NAMES = {"ad_activeview", "ad_click", "ad_exposure", "ad_query", "ad_reward", "adunit_exposure", "app_background", "app_clear_data",
                  "app_exception", "app_remove", "app_store_refund", "app_store_subscription_cancel", "app_store_subscription_convert",
                  "app_store_subscription_renew", "app_update", "app_upgrade", "dynamic_link_app_open", "dynamic_link_app_update",
                  "dynamic_link_first_open", "error", "first_open", "first_visit", "in_app_purchase", "notification_dismiss",
                  "notification_foreground", "notification_open", "notification_receive", "os_update", "session_start", "session_start_with_rollout",
                  "user_engagement"}

# Google's recommended lead-generation events; others can be renamed or added.
DEFAULT_EVENTS: dict[str, dict[str, Any]] = {
    "lead_created": {"enabled": True, "event": "generate_lead"},
    "lead_qualified": {"enabled": True, "event": "qualify_lead"},
    "lead_disqualified": {"enabled": False, "event": "disqualify_lead"},
    "lead_converted": {"enabled": False, "event": "working_lead"},
    "deal_created": {"enabled": False, "event": "deal_created"},
    "deal_won": {"enabled": True, "event": "close_convert_lead"},
    "deal_lost": {"enabled": False, "event": "close_unconvert_lead"},
    "quote_accepted": {"enabled": False, "event": "purchase"},
}
CONSENT = ("unspecified", "granted", "denied")
BACKDATE_LIMIT = timedelta(hours=71)  # GA4 accepts events up to 72 hours old
RUN_BUDGET_SECONDS = 30.0
WORKSPACES_PER_RUN = 25
EVENTS_PER_WORKSPACE = 200


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# GA4 settings
# ---------------------------------------------------------------------------


def ga_events(config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The moment → event mapping: defaults, overridden by the workspace's choices."""

    chosen = (config or {}).get("events") if isinstance((config or {}).get("events"), dict) else {}
    out = {k: dict(v) for k, v in DEFAULT_EVENTS.items()}
    for moment, rule in chosen.items():
        if isinstance(rule, dict):
            out[moment] = {"enabled": bool(rule.get("enabled")), "event": str(rule.get("event") or out.get(moment, {}).get("event") or moment)[:40]}
    return out


def ga_settings(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    return {
        "events": ga_events(config),
        "send_user_id": bool(config.get("send_user_id", False)),
        "pseudo_client_id": config.get("pseudo_client_id", True) is not False,
        "eu_endpoint": bool(config.get("eu_endpoint", False)),
        "consent": config.get("consent") if config.get("consent") in CONSENT else "unspecified",
        "non_personalized_ads": bool(config.get("non_personalized_ads", False)),
    }


def valid_event_name(name: str) -> bool:
    lowered = name.lower()
    return bool(EVENT_NAME.match(name)) and not lowered.startswith(RESERVED_PREFIXES) and lowered not in RESERVED_NAMES


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if key in BI_KEYS:
        return {}
    out: dict[str, Any] = {}
    if "events" in config:
        raw = config.get("events")
        if not isinstance(raw, dict):
            raise IntegrationError("Events should map CRM moments to GA4 event names.", 422)
        from app.integrations.providers.google_ads import _stages

        stage_ids = {s["id"] for s in await _stages(db, principal.organization_id)}
        cleaned: dict[str, Any] = {}
        for moment, rule in list(raw.items())[:60]:
            if not conversions.valid_moment(str(moment), stage_ids):
                raise IntegrationError(f"Unknown CRM moment: {moment}.", 422)
            if not isinstance(rule, dict):
                continue
            name = str(rule.get("event") or "").strip()
            if not valid_event_name(name):
                raise IntegrationError(
                    f"“{name or '(blank)'}” can't be a GA4 event name: use up to 40 letters, digits and underscores, starting with a letter, and not a reserved name.",
                    422,
                )
            cleaned[str(moment)] = {"enabled": bool(rule.get("enabled")), "event": name}
        out["events"] = cleaned
    for flag in ("send_user_id", "pseudo_client_id", "eu_endpoint", "non_personalized_ads"):
        if flag in config:
            out[flag] = bool(config.get(flag))
    if "consent" in config:
        if config.get("consent") not in CONSENT:
            raise IntegrationError("Choose how to report consent.", 422)
        out["consent"] = config["consent"]
    conversions.refresh_rules(principal.organization_id)
    return out


# ---------------------------------------------------------------------------
# GA4 Measurement Protocol
# ---------------------------------------------------------------------------


async def http(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """The single network call (tests replace this). Never logs the URL: it holds the API secret."""

    try:
        async with httpx.AsyncClient(timeout=kwargs.pop("timeout", 15)) as client:
            return await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Network error calling Google Analytics: %s", exc.__class__.__name__)
        raise IntegrationError("Couldn't reach Google Analytics. Try again in a moment.", 502) from exc


def _endpoint(settings: dict[str, Any], *, debug: bool = False) -> str:
    host = "region1.google-analytics.com" if settings["eu_endpoint"] else "www.google-analytics.com"
    return f"https://{host}/{'debug/' if debug else ''}mp/collect"


def pseudo_client_id(organization_id: uuid.UUID, person: str) -> str:
    """A stable stand-in client ID for someone whose GA client ID isn't known."""

    digest = hashlib.sha256(f"aiva-ga:{organization_id}:{person}".encode()).hexdigest()
    return f"{int(digest[:8], 16) % 2_000_000_000 + 100_000_000}.{int(digest[8:16], 16) % 1_000_000_000 + 1_000_000_000}"


def build_payload(
    organization_id: uuid.UUID,
    *,
    event_name: str,
    moment: str,
    record_type: str,
    ids: dict[str, Any],
    settings: dict[str, Any],
    occurred_at: datetime,
    value: float | None,
    currency: str | None,
    dedupe: str,
) -> tuple[dict[str, Any] | None, str]:
    """The Measurement Protocol body, and which client ID it used ("ga" or "pseudo").

    Returns (None, reason) when the event can't be sent."""

    client_id = conversions.clean_ga_client_id(ids.get("ga_client_id"))
    used = "ga"
    if not client_id:
        if not settings["pseudo_client_id"]:
            return None, "No Google Analytics client ID for this person (their visit wasn't seen by AIVA's tracking script or forms)."
        client_id = pseudo_client_id(organization_id, str(ids.get("person_id") or dedupe.split(":", 1)[-1]))
        used = "pseudo"
    params: dict[str, Any] = {"crm_record_type": record_type, "engagement_time_msec": 1}
    if value is not None:
        params["value"] = round(float(value), 2)
        params["currency"] = (currency or "USD").upper()[:3]
    if value is not None or event_name in {"purchase", "close_convert_lead"}:
        params["transaction_id"] = conversions.order_id(dedupe)
    if ids.get("lead_source") and event_name == "generate_lead":
        params["lead_source"] = str(ids["lead_source"])[:100]
    earliest = utcnow() - BACKDATE_LIMIT
    moment_time = max(occurred_at, earliest)
    body: dict[str, Any] = {
        "client_id": client_id,
        "timestamp_micros": int(moment_time.timestamp() * 1_000_000),
        "non_personalized_ads": settings["non_personalized_ads"],
        "events": [{"name": event_name, "params": params}],
    }
    if settings["send_user_id"] and ids.get("person_id"):
        body["user_id"] = str(ids["person_id"])
    if settings["consent"] != "unspecified":
        state = settings["consent"].upper()
        body["consent"] = {"ad_user_data": state, "ad_personalization": state}
    return body, used


async def _credentials(connection: IntegrationConnection) -> tuple[str, str]:
    measurement_id = str((connection.config or {}).get("measurement_id") or "")
    secret = core.secret(connection, "api_secret") or ""
    if not MEASUREMENT_ID.match(measurement_id) or not secret:
        raise IntegrationError("Add the GA4 measurement ID and Measurement Protocol API secret first.", 409)
    return measurement_id, secret


async def validate(connection: IntegrationConnection, settings: dict[str, Any], body: dict[str, Any]) -> list[dict[str, Any]]:
    """Google's validation messages for a payload (empty when it's valid)."""

    measurement_id, secret = await _credentials(connection)
    response = await http("POST", _endpoint(settings, debug=True), params={"measurement_id": measurement_id, "api_secret": secret}, json=body)
    if response.status_code >= 400:
        raise IntegrationError(f"Google Analytics answered {response.status_code}.", 502)
    try:
        data = response.json()
    except ValueError:
        data = {}
    return [
        {"field": m.get("fieldPath"), "description": m.get("description"), "code": m.get("validationCode")}
        for m in (data.get("validationMessages") or [])
        if isinstance(m, dict)
    ]


def sample_payload(organization_id: uuid.UUID, settings: dict[str, Any], moment: str = "lead_created") -> dict[str, Any]:
    rule = settings["events"].get(moment) or {"event": "generate_lead"}
    body, _ = build_payload(
        organization_id, event_name=rule["event"], moment=moment, record_type="lead",
        ids={"ga_client_id": "1234567890.1700000000", "lead_source": "aiva_test"}, settings={**settings, "send_user_id": False},
        occurred_at=utcnow(), value=100.0 if moment in conversions.VALUED else None, currency="USD", dedupe=f"{moment}:test",
    )
    return body or {}


async def send_due(db: AsyncSession, toggle: IntegrationToggle, connection: IntegrationConnection, deadline: float) -> int:
    org = connection.organization_id
    rows = await conversions.due(db, org, conversions.GA4, EVENTS_PER_WORKSPACE)
    if not rows:
        return 0
    settings = ga_settings(toggle.config)
    measurement_id, secret = await _credentials(connection)
    handled = 0
    sent = 0
    seen: set[str] = set()
    for row in rows:
        if time.monotonic() > deadline:
            break
        data = row.data or {}
        moment = data.get("moment") or ""
        rule = settings["events"].get(moment)
        handled += 1
        if not rule or not rule.get("enabled"):
            conversions.finish(row, "skipped", "This moment isn't sent to Google Analytics any more.", attempt=False)
            continue
        dedupe = data.get("dedupe") or ""
        if dedupe in seen or await conversions.already_sent(db, row):
            conversions.finish(row, "duplicate", "Already sent for this record.", attempt=False)
            continue
        ids = await conversions.identifiers(db, org, data.get("record_type") or "", data.get("record_id") or "")
        body, used = build_payload(
            org, event_name=rule["event"], moment=moment, record_type=data.get("record_type") or "", ids=ids, settings=settings,
            occurred_at=row.occurred_at or utcnow(), value=float(row.amount) if row.amount is not None else None, currency=row.currency, dedupe=dedupe,
        )
        if body is None:
            conversions.finish(row, "skipped", used, attempt=False)
            continue
        try:
            response = await http("POST", _endpoint(settings), params={"measurement_id": measurement_id, "api_secret": secret}, json=body)
        except IntegrationError as exc:
            conversions.retry_later(row, str(exc))
            continue
        seen.add(dedupe)
        if response.status_code < 300:
            conversions.finish(
                row, "sent",
                f"Sent as {rule['event']}." + (" (stand-in client ID: no GA visit was linked to this person)" if used == "pseudo" else ""),
                identifier="ga_client_id" if used == "ga" else "pseudo_client_id",
            )
            sent += 1
        elif response.status_code == 429 or response.status_code >= 500:
            conversions.retry_later(row, f"Google Analytics answered {response.status_code}; AIVA will try again.")
        else:
            conversions.finish(row, "failed", f"Google Analytics refused the event ({response.status_code}). Check the measurement ID and API secret.")
    if sent:
        connection.last_used_at = utcnow()
        core.log(db, org, GA4, "events_sent", f"Sent {sent} CRM event{'s' if sent != 1 else ''} to Google Analytics.", level="success", connection=connection)
    return handled


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    if key != GA4:
        return {}
    measurement_id = (values.get("measurement_id") or "").strip().upper()
    secret = (values.get("api_secret") or "").strip()
    if not MEASUREMENT_ID.match(measurement_id):
        raise IntegrationError("The measurement ID looks like G-XXXXXXXXXX (GA4 → Admin → Data streams → your web stream).", 422)
    if len(secret) < 10:
        raise IntegrationError("Paste the Measurement Protocol API secret (Data stream → Measurement Protocol API secrets → Create).", 422)
    toggle = await core.toggle_row(db, organization_id, GA4)
    settings = ga_settings(toggle.config if toggle else {})
    probe = IntegrationConnection(organization_id=organization_id, provider=GA4, config={"measurement_id": measurement_id})
    core.set_secret(probe, "api_secret", secret)
    messages = await validate(probe, settings, sample_payload(organization_id, settings))
    if messages:
        raise IntegrationError("Google Analytics didn't accept a sample event: " + "; ".join(m["description"] or m["code"] or "" for m in messages)[:400], 422)
    values["measurement_id"] = measurement_id
    return {"label": f"GA4 {measurement_id}", "external_id": measurement_id, "config": {"measurement_id": measurement_id}}


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    conversions.refresh_rules(organization_id)


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    conversions.refresh_rules(connection.organization_id)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    conversions.refresh_rules(connection.organization_id)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    if key != GA4:
        raise IntegrationError("Open the feed from Power BI or Looker Studio to test it.", 400)
    toggle = await core.toggle_row(db, connection.organization_id, GA4)
    settings = ga_settings(toggle.config if toggle else {})
    messages = await validate(connection, settings, sample_payload(connection.organization_id, settings))
    if messages:
        raise IntegrationError("Google Analytics found problems: " + "; ".join(m["description"] or m["code"] or "" for m in messages)[:400], 422)
    return f"Google Analytics validated a sample {settings['events'].get('lead_created', {}).get('event', 'generate_lead')} event for {(connection.config or {}).get('measurement_id')}."


def feed_base() -> str:
    return get_settings().app_base_url.rstrip("/")


def feed_info(key: str) -> dict[str, Any]:
    base = feed_base()
    info: dict[str, Any] = {
        "base_url": base,
        "feed_url": f"{base}/api/aiva/analytics-feed",
        "api_keys_url": "/settings/api-keys",
        "datasets": [{"key": d.key, "label": d.label, "description": d.description, "columns": len(d.columns)} for d in feed.DATASETS.values()],
        "page_size": feed.MAX_LIMIT,
    }
    if key == POWER_BI:
        info["power_query"] = feed.power_query_function(base)
        info["power_query_examples"] = feed.power_query_examples()
    else:
        info["connector_download"] = "/analytics-feed/connectors/looker-studio.zip"
    return info


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    if key in BI_KEYS:
        from app.models.organization import Organization

        organization = await db.get(Organization, org)
        hipaa = bool((organization.settings or {}).get("hipaa_mode")) and principal.has("hipaa") if organization else False
        return {**feed_info(key), "hipaa": hipaa, "can_read": principal.can("records.export")}
    toggle = await core.toggle_row(db, org, GA4)
    settings = ga_settings(toggle.config if toggle else {})
    connection = await core.connection_for(db, org, GA4)
    from app.integrations.providers.google_ads import _stages, moments_catalog

    out: dict[str, Any] = {
        "settings": settings,
        "moments": moments_catalog(await _stages(db, org)),
        "consent_options": list(CONSENT),
        "measurement_id": (connection.config or {}).get("measurement_id") if connection else None,
    }
    since = utcnow() - timedelta(days=30)
    counts = dict(
        (
            await db.execute(
                select(IntegrationObject.status, func.count())
                .where(IntegrationObject.organization_id == org, IntegrationObject.provider == GA4, IntegrationObject.object_type == "ga4_event", IntegrationObject.occurred_at >= since)
                .group_by(IntegrationObject.status)
            )
        ).all()
    )
    out["stats"] = {s: int(counts.get(s, 0)) for s in ("pending", "sent", "skipped", "failed", "duplicate")}
    if principal.can("admin.settings"):
        rows = (
            await db.execute(
                select(IntegrationObject)
                .where(IntegrationObject.organization_id == org, IntegrationObject.provider == GA4, IntegrationObject.object_type == "ga4_event")
                .order_by(IntegrationObject.occurred_at.desc())
                .limit(20)
            )
        ).scalars().all()
        out["recent"] = [conversions.object_out(r) for r in rows]
    return out


async def run_due(db: AsyncSession) -> int:
    deadline = time.monotonic() + RUN_BUDGET_SECONDS
    previous = db.sync_session.info.get("automation_disabled")
    db.sync_session.info["automation_disabled"] = True
    handled = 0
    try:
        from app.integrations.providers.lead_capture import plan_for

        rows = (
            await db.execute(
                select(IntegrationConnection.id, IntegrationConnection.organization_id)
                .join(IntegrationToggle, (IntegrationToggle.organization_id == IntegrationConnection.organization_id) & (IntegrationToggle.provider == GA4))
                .where(IntegrationConnection.provider == GA4, IntegrationConnection.user_id.is_(None), IntegrationToggle.enabled.is_(True))
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
                toggle = await core.toggle_row(db, org, GA4)
                if connection is None or toggle is None:
                    continue
                features, read_only = await plan_for(db, org)
                if conversions.FEATURE[GA4] not in features or read_only:
                    continue
                processed += 1
                try:
                    handled += await send_due(db, toggle, connection, deadline)
                    await db.commit()
                except IntegrationError as exc:
                    connection.last_error = str(exc)[:1000]
                    await db.commit()
                except Exception:  # noqa: BLE001 - one workspace mustn't stop the rest
                    logger.exception("GA4 run failed for %s", org)
                    await db.rollback()
        finally:
            await db.rollback()
            await core.mark_seen_ids(db, examined)
    finally:
        db.sync_session.info["automation_disabled"] = previous
    return handled
