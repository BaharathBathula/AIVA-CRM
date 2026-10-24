"""CRM moments sent to Google Ads (offline click conversions) and Google Analytics 4
(Measurement Protocol events).

How it works:

* The automation flush hook calls :func:`queue` for every record change. When the
  change is a moment a workspace sends (a lead qualified, a deal won...), a pending
  ``IntegrationObject`` is added *in the same transaction*, so a save that rolls back
  never sends anything. It only reads attributes already loaded and never queries.
* Each provider's ``run_due`` picks pending rows up, works out the person's click IDs
  and Google Analytics client ID (:func:`identifiers`), sends them, and records the
  outcome on the row (``sent``, ``failed``, ``skipped`` or ``duplicate``) with retries.
* Every moment of a record is sent once: the dedupe key is ``moment:record``, also
  used to build the Google Ads order ID and the GA4 ``transaction_id``.

The automation rule cache holds which moments each integration wants (see
:func:`context_entries`), so a workspace that uses neither pays one small query when
its rules are reloaded and nothing per save.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationToggle

logger = logging.getLogger("aiva.integrations.conversions")

ADS = "google_ads"
GA4 = "google_analytics"
OBJECT_TYPE = {ADS: "conversion", GA4: "ga4_event"}
FEATURE = {ADS: "google_ads", GA4: "bi_connectors"}

# The moments people can send, in the order the settings show them.
MOMENTS: dict[str, str] = {
    "lead_created": "New lead",
    "lead_qualified": "Lead qualified",
    "lead_disqualified": "Lead disqualified",
    "lead_converted": "Lead converted",
    "deal_created": "New deal",
    "deal_won": "Deal won",
    "deal_lost": "Deal lost",
    "quote_accepted": "Quote accepted",
}
STAGE_PREFIX = "stage:"
# Moments that carry money (the deal amount or quote total).
VALUED = {"deal_won", "deal_lost", "deal_created", "quote_accepted"}

CLICK_KEYS = ("gclid", "gbraid", "wbraid")
CLICK_ID = re.compile(r"^[A-Za-z0-9_\-.]{8,200}$")
GA_CLIENT_ID = re.compile(r"^\d{1,20}\.\d{1,20}$")
CLICK_WINDOW = timedelta(days=90)  # Google Ads' longest click-through window

BACKOFF = (60, 5 * 60, 30 * 60, 2 * 3600, 6 * 3600, 12 * 3600, 24 * 3600)
MAX_ATTEMPTS = len(BACKOFF) + 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def moment_label(key: str, stages: dict[str, dict[str, Any]] | None = None) -> str:
    if key.startswith(STAGE_PREFIX):
        stage = (stages or {}).get(key[len(STAGE_PREFIX):]) or {}
        return f"Deal reaches {stage.get('name') or 'a stage'}"
    return MOMENTS.get(key, key)


def valid_moment(key: str, stage_ids: set[str]) -> bool:
    if key in MOMENTS:
        return True
    return key.startswith(STAGE_PREFIX) and key[len(STAGE_PREFIX):] in stage_ids


def order_id(dedupe: str) -> str:
    """A stable, opaque ID per moment of a record (Google Ads order ID, GA4 transaction_id)."""

    return "aiva_" + hashlib.sha256(dedupe.encode()).hexdigest()[:40]


# ---------------------------------------------------------------------------
# Rule cache (loaded with the workspace's automation rules)
# ---------------------------------------------------------------------------


async def context_entries(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, list[str]]:
    """Which moments each integration sends, for the automation rule cache."""

    rows = (
        await db.execute(
            select(IntegrationToggle).where(
                IntegrationToggle.organization_id == organization_id,
                IntegrationToggle.provider.in_((ADS, GA4)),
                IntegrationToggle.enabled.is_(True),
            )
        )
    ).scalars().all()
    out: dict[str, list[str]] = {}
    for row in rows:
        config = row.config or {}
        if row.provider == ADS:
            if not config.get("customer_id"):
                continue
            moments = [m for m, rule in (config.get("conversions") or {}).items() if isinstance(rule, dict) and rule.get("conversion_action")]
        else:
            from app.integrations.providers.analytics import ga_events

            moments = [m for m, rule in ga_events(config).items() if rule.get("enabled")]
        if moments:
            out[row.provider] = moments
    if GA4 in out:
        connected = (
            await db.execute(
                select(IntegrationConnection.id).where(
                    IntegrationConnection.organization_id == organization_id, IntegrationConnection.provider == GA4, IntegrationConnection.user_id.is_(None)
                )
            )
        ).first()
        if connected is None:
            out.pop(GA4)
    return out


def refresh_rules(organization_id: uuid.UUID) -> None:
    from app.services.automation import invalidate_automation_cache

    invalidate_automation_cache(organization_id)


# ---------------------------------------------------------------------------
# Queueing (inside the flush hook: no queries, never raises)
# ---------------------------------------------------------------------------


def moments_for(kind: str, entity_type: str, obj: Any, changes: dict[str, Any], context: dict[str, Any]) -> list[str]:
    def became(field: str, *values: Any) -> bool:
        change = changes.get(field)
        return bool(change) and change.get("to") in values and change.get("from") not in values

    out: list[str] = []
    if entity_type == "lead":
        if kind == "created":
            out.append("lead_created")
            if getattr(obj, "status", None) == "qualified":
                out.append("lead_qualified")
        elif kind == "updated":
            if became("status", "qualified"):
                out.append("lead_qualified")
            if became("status", "unqualified"):
                out.append("lead_disqualified")
            converted = changes.get("converted_at")
            if converted and converted.get("from") is None and converted.get("to"):
                out.append("lead_converted")
    elif entity_type == "opportunity":
        if kind == "created":
            out.append("deal_created")
        if (kind == "created" or "stage_id" in changes) and getattr(obj, "stage_id", None):
            stage_id = str(obj.stage_id)
            out.append(f"{STAGE_PREFIX}{stage_id}")
            category = ((context.get("stages") or {}).get(stage_id) or {}).get("category")
            if category == "won":
                out.append("deal_won")
            elif category == "lost":
                out.append("deal_lost")
    elif entity_type == "quote" and kind == "updated" and became("status", "accepted"):
        out.append("quote_accepted")
    return out


def _value(entity_type: str, obj: Any, moment: str) -> tuple[float | None, str | None]:
    if moment not in VALUED and not moment.startswith(STAGE_PREFIX):
        return None, None
    if entity_type == "opportunity":
        amount = getattr(obj, "amount", None)
        return (float(amount) if amount is not None else None), (getattr(obj, "currency", None) or None)
    if entity_type == "quote":
        total = getattr(obj, "total", None)
        return (float(total) if total is not None else None), (getattr(obj, "currency", None) or None)
    return None, None


def _label(entity_type: str, obj: Any) -> str:
    if entity_type == "lead":
        return f"{getattr(obj, 'first_name', '') or ''} {getattr(obj, 'last_name', '') or ''}".strip() or "Lead"
    return str(getattr(obj, "name", None) or entity_type.title())[:200]


def queue(session: Any, kind: str, entity_type: str, obj: Any, changes: dict[str, Any], context: dict[str, Any], enabled: Callable[[str], bool]) -> None:
    """Add pending conversions for the moments this change produces. Never raises."""

    try:
        wanted = context.get("conversions") or {}
        organization_id = getattr(obj, "organization_id", None)
        if not wanted or kind == "deleted" or organization_id is None or entity_type not in {"lead", "opportunity", "quote"}:
            return
        moments = moments_for(kind, entity_type, obj, changes, context)
        if not moments:
            return
        now = utcnow()
        for provider, keys in wanted.items():
            if provider not in OBJECT_TYPE or not enabled(FEATURE[provider]):
                continue
            for moment in moments:
                if moment not in keys:
                    continue
                value, currency = _value(entity_type, obj, moment)
                record_id = str(obj.id)
                # Only the generic record_type/record_id link: the record (and its account or
                # contact) may be inserted in this same flush, and foreign keys without an ORM
                # relationship don't order inserts.
                session.add(
                    IntegrationObject(
                        id=uuid.uuid4(),
                        organization_id=organization_id,
                        provider=provider,
                        object_type=OBJECT_TYPE[provider],
                        external_id=uuid.uuid4().hex,
                        external_parent="",
                        title=f"{moment_label(moment, context.get('stages'))} · {_label(entity_type, obj)}"[:500],
                        status="pending",
                        amount=value,
                        currency=(currency or None) and str(currency)[:3],
                        occurred_at=now,
                        record_type=entity_type,
                        record_id=obj.id,
                        data={
                            "moment": moment,
                            "record_type": entity_type,
                            "record_id": record_id,
                            "dedupe": f"{moment}:{record_id}",
                            "attempts": 0,
                            "next_at": iso(now),
                        },
                    )
                )
    except Exception:  # noqa: BLE001 - conversions must never block a save
        logger.exception("Couldn't queue conversions for a %s", entity_type)


# ---------------------------------------------------------------------------
# Sending helpers (scheduler)
# ---------------------------------------------------------------------------


async def due(db: AsyncSession, organization_id: uuid.UUID, provider: str, limit: int) -> list[IntegrationObject]:
    """Pending rows whose next attempt is due, oldest first, locked for this run."""

    return list(
        (
            await db.execute(
                select(IntegrationObject)
                .where(
                    IntegrationObject.organization_id == organization_id,
                    IntegrationObject.provider == provider,
                    IntegrationObject.object_type == OBJECT_TYPE[provider],
                    IntegrationObject.status == "pending",
                    IntegrationObject.data["next_at"].astext <= iso(utcnow()),
                )
                .order_by(IntegrationObject.occurred_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )


async def already_sent(db: AsyncSession, row: IntegrationObject) -> bool:
    dedupe = (row.data or {}).get("dedupe")
    if not dedupe:
        return False
    found = (
        await db.execute(
            select(IntegrationObject.id).where(
                IntegrationObject.organization_id == row.organization_id,
                IntegrationObject.provider == row.provider,
                IntegrationObject.object_type == row.object_type,
                IntegrationObject.status.in_(("sent", "sending")),
                IntegrationObject.data["dedupe"].astext == dedupe,
                IntegrationObject.id != row.id,
            ).limit(1)
        )
    ).first()
    return found is not None


def finish(row: IntegrationObject, status: str, message: str | None = None, *, attempt: bool = True, **extra: Any) -> None:
    """Final outcome: sent, failed, skipped (nothing to send it with) or duplicate."""

    data = dict(row.data or {})
    if attempt:
        data["attempts"] = int(data.get("attempts") or 0) + 1
    data["message"] = (message or "")[:500] or None
    data.update(extra)
    data["finished_at"] = iso(utcnow())
    row.status = status
    row.external_updated_at = utcnow()
    row.data = data


def retry_later(row: IntegrationObject, message: str, *, delay_seconds: int | None = None) -> None:
    """Try again later, or give up after the last attempt."""

    data = dict(row.data or {})
    attempts = int(data.get("attempts") or 0) + 1
    data["attempts"] = attempts
    data["message"] = message[:500]
    row.external_updated_at = utcnow()
    if attempts >= MAX_ATTEMPTS:
        row.status = "failed"
        data["finished_at"] = iso(utcnow())
    else:
        wait = delay_seconds if delay_seconds is not None else BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
        data["next_at"] = iso(utcnow() + timedelta(seconds=max(30, wait)))
    row.data = data


def postpone(row: IntegrationObject, seconds: int, message: str | None = None) -> None:
    """Not an attempt (quota, a switched-off integration): just come back later."""

    data = dict(row.data or {})
    data["next_at"] = iso(utcnow() + timedelta(seconds=max(30, seconds)))
    if message:
        data["message"] = message[:500]
    row.data = data


def retry_now(row: IntegrationObject) -> None:
    data = dict(row.data or {})
    data["next_at"] = iso(utcnow())
    data["attempts"] = 0
    data.pop("message", None)
    data.pop("finished_at", None)
    row.status = "pending"
    row.data = data


def object_out(row: IntegrationObject) -> dict[str, Any]:
    data = row.data or {}
    record_type = data.get("record_type") or row.record_type
    record_id = data.get("record_id") or (str(row.record_id) if row.record_id else None)
    href = None
    if record_id and record_type in {"lead", "opportunity", "quote", "contact"}:
        href = {"lead": "/leads/", "opportunity": "/opportunities/", "quote": "/quotes/", "contact": "/contacts/"}[record_type] + record_id
    return {
        "id": str(row.id),
        "title": row.title,
        "moment": data.get("moment"),
        "status": row.status,
        "value": float(row.amount) if row.amount is not None else None,
        "currency": row.currency,
        "attempts": int(data.get("attempts") or 0),
        "message": data.get("message"),
        "identifier": data.get("identifier"),
        "next_at": data.get("next_at") if row.status == "pending" else None,
        "occurred_at": row.occurred_at,
        "finished_at": data.get("finished_at"),
        "record_type": record_type,
        "record_id": record_id,
        "href": href,
    }


# ---------------------------------------------------------------------------
# Who the moment is about: click IDs and the Google Analytics client ID
# ---------------------------------------------------------------------------


def _attribution(record: Any) -> dict[str, Any]:
    fields = getattr(record, "custom_fields", None) or {}
    value = fields.get("_attribution") if isinstance(fields, dict) else None
    return value if isinstance(value, dict) else {}


def clean_click_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if CLICK_ID.match(text) else None


def clean_ga_client_id(value: Any) -> str | None:
    """Accepts a client ID (``123.456``) or a whole ``_ga`` cookie (``GA1.1.123.456``)."""

    text = str(value or "").strip()
    if text.upper().startswith("GA1.") or text.upper().startswith("GS1."):
        parts = text.split(".")
        text = ".".join(parts[-2:]) if len(parts) >= 4 else ""
    return text if GA_CLIENT_ID.match(text) else None


async def identifiers(db: AsyncSession, organization_id: uuid.UUID, record_type: str, record_id: str) -> dict[str, Any]:
    """Click IDs (gclid, gbraid, wbraid), the GA client ID, when the click happened (or
    the earliest time we know of), and the lead source, for the person behind a record:
    the record itself, the lead it came from, the deal's contact, and finally the
    website visits of someone with the same email address."""

    from app.models.contact import Contact
    from app.models.lead import Lead
    from app.models.opportunity import Opportunity

    found: dict[str, Any] = {}
    emails: list[str] = []
    times: list[datetime] = []

    def absorb(record: Any) -> None:
        if record is None:
            return
        attribution = _attribution(record)
        for key in CLICK_KEYS:
            value = clean_click_id(attribution.get(key))
            if value and not any(found.get(k) for k in CLICK_KEYS):
                found[key] = value
        ga = clean_ga_client_id(attribution.get("ga_client_id"))
        if ga and not found.get("ga_client_id"):
            found["ga_client_id"] = ga
        if getattr(record, "created_at", None):
            times.append(record.created_at)
        email = getattr(record, "email", None)
        if email:
            emails.append(email.lower())
        source = getattr(record, "source", None) or getattr(record, "lead_source", None)
        if source and not found.get("lead_source"):
            found["lead_source"] = str(source)[:100]
        if type(record).__name__ in {"Lead", "Contact"} and not found.get("person_id"):
            found["person_id"] = str(record.id)  # an opaque AIVA ID, never PII

    try:
        rid = uuid.UUID(str(record_id))
    except ValueError:
        return found

    async def origin_lead(**where: Any) -> Any:
        column, value = next(iter(where.items()))
        return (
            await db.execute(
                select(Lead).where(Lead.organization_id == organization_id, getattr(Lead, column) == value).order_by(Lead.created_at.asc()).limit(1)
            )
        ).scalar_one_or_none()

    async def contact_chain(contact_id: uuid.UUID | None) -> None:
        if contact_id is None:
            return
        contact = await db.get(Contact, contact_id)
        if contact is not None and contact.organization_id == organization_id:
            absorb(contact)
            absorb(await origin_lead(converted_contact_id=contact.id))

    async def deal_chain(opportunity_id: uuid.UUID | None) -> None:
        if opportunity_id is None:
            return
        deal = await db.get(Opportunity, opportunity_id)
        if deal is None or deal.organization_id != organization_id:
            return
        absorb(deal)
        absorb(await origin_lead(converted_opportunity_id=deal.id))
        await contact_chain(deal.primary_contact_id)

    if record_type == "lead":
        lead = await db.get(Lead, rid)
        if lead is not None and lead.organization_id == organization_id:
            absorb(lead)
    elif record_type == "contact":
        await contact_chain(rid)
    elif record_type == "opportunity":
        await deal_chain(rid)
    elif record_type == "quote":
        from app.models.platform import Quote

        quote = await db.get(Quote, rid)
        if quote is not None and quote.organization_id == organization_id:
            await deal_chain(quote.opportunity_id)
            await contact_chain(quote.contact_id)

    has_click = any(found.get(k) for k in CLICK_KEYS)
    if emails and (not has_click or not found.get("ga_client_id")):
        await _from_website(db, organization_id, sorted(set(emails))[:5], found, has_click)
    if times and "first_seen_at" not in found:
        found["first_seen_at"] = min(times)
    return found


async def _from_website(db: AsyncSession, organization_id: uuid.UUID, emails: list[str], found: dict[str, Any], has_click: bool) -> None:
    from app.models.engagement import WebSession, WebVisitor

    visitors = (
        await db.execute(
            select(WebVisitor).where(WebVisitor.organization_id == organization_id, WebVisitor.email.in_(emails)).order_by(WebVisitor.last_seen_at.desc()).limit(5)
        )
    ).scalars().all()
    if not visitors:
        return
    if not found.get("ga_client_id"):
        for visitor in visitors:
            ga = clean_ga_client_id((visitor.first_source or {}).get("ga_client_id"))
            if ga:
                found["ga_client_id"] = ga
                break
    if has_click:
        return
    since = utcnow() - CLICK_WINDOW
    sessions = (
        await db.execute(
            select(WebSession.landing_url, WebSession.started_at)
            .where(WebSession.visitor_id.in_([v.id for v in visitors]), WebSession.started_at >= since, WebSession.landing_url.is_not(None))
            .order_by(WebSession.started_at.desc())
            .limit(25)
        )
    ).all()
    for landing_url, started_at in sessions:
        query = parse_qs(urlsplit(landing_url or "").query)
        for key in CLICK_KEYS:
            value = clean_click_id((query.get(key) or [None])[0])
            if value:
                found[key] = value
                found["click_at"] = started_at
                return
