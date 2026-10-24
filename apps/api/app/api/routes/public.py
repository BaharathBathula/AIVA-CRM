"""Public endpoints: pricing, web forms, unsubscribe, open tracking, calendar
feeds, the Google Ads lead webhook and the Stripe webhook.

None of these need a signed-in user. Each one authenticates in its own way
(an unguessable token, a webhook key or signature) where it matters.
"""

from __future__ import annotations

import base64
import hmac
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import client_ip
from app.core.config import get_settings
from app.core.crypto import decrypt, sha256
from app.core.plans import catalog
from app.db.session import get_db
from app.models.contact import Contact
from app.models.editions import CalendarFeed, CampaignRecipient, Integration, Subscription, WebForm
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.organization import Organization
from app.models.task import Task
from app.services import stripe_billing
from app.services.automation import load_automation_context
from app.services.billing import plan_state
from app.services.forms import public_form, submit

router = APIRouter(prefix="/public", tags=["Public"])

PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_RATE: dict[str, deque] = defaultdict(deque)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def rate_limited(key: str, limit: int, window: int = 60) -> bool:
    now = time.monotonic()
    bucket = _RATE[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    if len(_RATE) > 20000:
        _RATE.clear()
    return False


@router.get("/plans")
async def plans():
    return catalog()


# ---------------------------------------------------------------------------
# Web forms
# ---------------------------------------------------------------------------


async def _form(db: AsyncSession, slug: str) -> tuple[WebForm, Organization]:
    form = (await db.execute(select(WebForm).where(WebForm.slug == slug))).scalar_one_or_none()
    if form is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This form doesn't exist.")
    organization = await db.get(Organization, form.organization_id)
    return form, organization


@router.get("/forms/{slug}")
async def get_public_form(slug: str, db: AsyncSession = Depends(get_db)):
    form, organization = await _form(db, slug)
    if not form.is_active:
        raise HTTPException(status.HTTP_410_GONE, "This form isn't accepting responses.")
    return public_form(form, organization.name if organization else "")


class Submission(BaseModel):
    data: dict[str, Any]
    attribution: dict[str, Any] = {}


@router.post("/forms/{slug}/submit")
async def submit_public_form(slug: str, payload: Submission, request: Request, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request) or "unknown"
    if rate_limited(f"form:{ip}", 10):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many submissions. Wait a minute and try again.")
    form, _ = await _form(db, slug)
    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == form.organization_id))).scalar_one_or_none()
    # Run lead scoring, workflows and chat alerts for the new record like any other.
    await load_automation_context(db, form.organization_id, features=plan_state(subscription).features)
    result = await submit(db, form, payload.data, payload.attribution, ip=ip, user_agent=request.headers.get("user-agent"))
    await db.commit()
    return {"ok": True, **({"record_type": result.get("record_type")} if result.get("record_type") else {})}


# ---------------------------------------------------------------------------
# Unsubscribe and open tracking
# ---------------------------------------------------------------------------


async def _recipient(db: AsyncSession, token: str) -> CampaignRecipient:
    recipient = (await db.execute(select(CampaignRecipient).where(CampaignRecipient.token == token))).scalar_one_or_none()
    if recipient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link isn't valid.")
    return recipient


async def _sequence_message(db: AsyncSession, token: str):
    from app.models.outbound import SequenceEnrollment, SequenceMessage

    message = (await db.execute(select(SequenceMessage).where(SequenceMessage.token == token))).scalar_one_or_none()
    if message is None:
        return None, None
    return message, await db.get(SequenceEnrollment, message.enrollment_id)


@router.get("/unsubscribe/{token}")
async def unsubscribe_info(token: str, db: AsyncSession = Depends(get_db)):
    message, enrollment = await _sequence_message(db, token)
    if message is not None:
        organization = await db.get(Organization, message.organization_id)
        model = Lead if enrollment is not None and enrollment.record_type == "lead" else Contact
        record = await db.get(model, enrollment.record_id) if enrollment is not None else None
        return {
            "email": message.to_email,
            "organization_name": organization.name if organization else "",
            "unsubscribed": bool(record and record.email_opt_out),
        }
    recipient = await _recipient(db, token)
    organization = await db.get(Organization, recipient.organization_id)
    model = Lead if recipient.record_type == "lead" else Contact
    record = await db.get(model, recipient.record_id)
    return {
        "email": recipient.email,
        "organization_name": organization.name if organization else "",
        "unsubscribed": bool(record and record.email_opt_out),
    }


@router.post("/unsubscribe/{token}")
async def unsubscribe(token: str, db: AsyncSession = Depends(get_db)):
    db.sync_session.info["automation_disabled"] = True
    message, _enrollment = await _sequence_message(db, token)
    if message is not None:
        from app.services import outbound

        await outbound.unsubscribed(db, message)
        organization_id, email = message.organization_id, (message.to_email or "").lower()
    else:
        recipient = await _recipient(db, token)
        recipient.unsubscribed_at = recipient.unsubscribed_at or utcnow()
        organization_id, email = recipient.organization_id, recipient.email.lower()
    from app.models.governance import SuppressionEntry

    exists = (
        await db.execute(
            select(SuppressionEntry.id).where(SuppressionEntry.organization_id == organization_id, SuppressionEntry.kind == "email", SuppressionEntry.value == email)
        )
    ).first()
    if email and not exists:
        db.add(SuppressionEntry(id=uuid.uuid4(), organization_id=organization_id, kind="email", value=email, reason="unsubscribed"))
    # Opt the address out everywhere in this workspace, not just this record.
    for model in (Lead, Contact):
        for record in (
            await db.execute(select(model).where(model.organization_id == organization_id, model.email.ilike(email)))
        ).scalars():
            record.email_opt_out = True
    await db.commit()
    return {"unsubscribed": True, "email": email}


@router.get("/email/open/{token}.gif")
async def email_open(token: str, db: AsyncSession = Depends(get_db)):
    recipient = (await db.execute(select(CampaignRecipient).where(CampaignRecipient.token == token))).scalar_one_or_none()
    if recipient is not None:
        recipient.opened_at = recipient.opened_at or utcnow()
        recipient.open_count = (recipient.open_count or 0) + 1
        await db.commit()
    else:
        message, _enrollment = await _sequence_message(db, token)
        if message is not None:
            message.opened_at = message.opened_at or utcnow()
            message.open_count = (message.open_count or 0) + 1
            await db.commit()
    return Response(content=PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store, max-age=0"})


# ---------------------------------------------------------------------------
# Calendar feed
# ---------------------------------------------------------------------------


def _ics_text(value: str | None) -> str:
    text = (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")
    return text


def _fold(line: str) -> str:
    out = []
    while len(line.encode()) > 74:
        cut = 74
        while len(line[:cut].encode()) > 74:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@router.get("/calendar/{token}.ics")
async def calendar_feed(token: str, db: AsyncSession = Depends(get_db)):
    feed = (await db.execute(select(CalendarFeed).where(CalendarFeed.token_hash == sha256(token)))).scalar_one_or_none()
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This calendar link was reset or removed.")
    feed.last_accessed_at = utcnow()
    organization = await db.get(Organization, feed.organization_id)
    base = get_settings().app_base_url.rstrip("/")
    now = utcnow()
    tasks = (
        await db.execute(
            select(Task).where(
                Task.organization_id == feed.organization_id,
                Task.assigned_to_user_id == feed.user_id,
                Task.due_at.is_not(None),
                Task.due_at >= now - timedelta(days=30),
                Task.due_at <= now + timedelta(days=365),
                Task.status.in_(("open", "in_progress")),
            )
        )
    ).scalars().all()
    meetings = (
        await db.execute(
            select(Meeting).where(
                Meeting.organization_id == feed.organization_id,
                Meeting.organizer_user_id == feed.user_id,
                Meeting.start_at >= now - timedelta(days=30),
            )
        )
    ).scalars().all()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AIVA CRM//Calendar feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:AIVA · {_ics_text(organization.name if organization else 'CRM')}",
        "X-PUBLISHED-TTL:PT30M",
        "REFRESH-INTERVAL;VALUE=DURATION:PT30M",
    ]
    for task in tasks:
        start = task.start_at or task.due_at
        end = task.due_at if task.start_at and task.due_at > task.start_at else start + timedelta(minutes=30)
        lines += [
            "BEGIN:VEVENT",
            f"UID:task-{task.id}@aiva",
            f"DTSTAMP:{_stamp(task.updated_at or now)}",
            f"DTSTART:{_stamp(start)}",
            f"DTEND:{_stamp(end)}",
            f"SUMMARY:{_ics_text(('☎ ' if task.task_type == 'call' else '') + task.title)}",
            f"DESCRIPTION:{_ics_text((task.description or '') + chr(10) + chr(10) + f'Open in AIVA: {base}/tasks')}",
            f"URL:{base}/tasks",
            "BEGIN:VALARM" if task.reminder_at else "",
            "ACTION:DISPLAY" if task.reminder_at else "",
            f"DESCRIPTION:{_ics_text(task.title)}" if task.reminder_at else "",
            f"TRIGGER;VALUE=DATE-TIME:{_stamp(task.reminder_at)}" if task.reminder_at else "",
            "END:VALARM" if task.reminder_at else "",
            "END:VEVENT",
        ]
    for meeting in meetings:
        end = meeting.end_at or (meeting.start_at + timedelta(hours=1))
        lines += [
            "BEGIN:VEVENT",
            f"UID:meeting-{meeting.id}@aiva",
            f"DTSTAMP:{_stamp(meeting.updated_at or now)}",
            f"DTSTART:{_stamp(meeting.start_at)}",
            f"DTEND:{_stamp(end)}",
            f"SUMMARY:{_ics_text(meeting.title)}",
            f"DESCRIPTION:{_ics_text(meeting.description or '')}",
            f"LOCATION:{_ics_text(meeting.meeting_url or meeting.location or '')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    await db.commit()
    body = "\r\n".join(_fold(line) for line in lines if line) + "\r\n"
    return Response(content=body, media_type="text/calendar; charset=utf-8", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Google Ads lead form webhook
# ---------------------------------------------------------------------------

GOOGLE_COLUMNS = {
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


@router.post("/google-ads/{organization_slug}")
async def google_ads_lead(organization_slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    organization = (await db.execute(select(Organization).where(Organization.slug == organization_slug))).scalar_one_or_none()
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown workspace.")
    integration = (
        await db.execute(select(Integration).where(Integration.organization_id == organization.id, Integration.kind == "google_ads"))
    ).scalar_one_or_none()
    expected = decrypt((integration.config or {}).get("webhook_key")) if integration else None
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send a JSON object.")
    if not expected or not hmac.compare_digest(str(payload.get("google_key") or "").encode(), expected.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The Google Ads key doesn't match.")
    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == organization.id))).scalar_one_or_none()
    state = plan_state(subscription)
    if "google_ads" not in state.features or state.read_only:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Google Ads isn't included in this workspace's plan.")

    values: dict[str, str] = {}
    extra = []
    for column in payload.get("user_column_data") or []:
        key = GOOGLE_COLUMNS.get(str(column.get("column_id")))
        value = str(column.get("string_value") or "").strip()
        if key and value:
            values[key] = value
        elif value:
            extra.append(f"{column.get('column_name') or column.get('column_id')}: {value}")
    if "full_name" in values and not values.get("last_name"):
        parts = values["full_name"].split(" ", 1)
        values["first_name"] = values.get("first_name") or parts[0]
        values["last_name"] = parts[1] if len(parts) > 1 else parts[0]
    lead_id = str(payload.get("lead_id") or "")
    if lead_id:
        existing = (
            await db.execute(
                select(Lead).where(Lead.organization_id == organization.id, Lead.custom_fields["_attribution"]["google_lead_id"].astext == lead_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"ok": True, "duplicate": True}
    await load_automation_context(db, organization.id, features=state.features)
    lead = Lead(
        id=uuid.uuid4(),
        organization_id=organization.id,
        first_name=values.get("first_name", "")[:120],
        last_name=(values.get("last_name") or values.get("email", "Google lead").split("@")[0])[:120],
        email=(values.get("email") or "").lower() or None,
        phone=values.get("phone"),
        company_name=values.get("company_name"),
        job_title=values.get("job_title"),
        notes="\n".join(extra) or None,
        source="google_ads",
        status="new",
        score=0,
        owner_user_id=integration.connected_by_user_id,
        custom_fields={
            "_attribution": {
                "gclid": payload.get("gcl_id"),
                "google_lead_id": lead_id or None,
                "utm_campaign": str(payload.get("campaign_id") or "") or None,
                "google_campaign_id": str(payload.get("campaign_id") or "") or None,
                "form_id": str(payload.get("form_id") or "") or None,
                "test": bool(payload.get("is_test")),
            }
        },
    )
    db.add(lead)
    integration.last_used_at = utcnow()
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stripe isn't configured.")
    payload = await request.body()
    try:
        event = stripe_billing.verify_webhook(payload, request.headers.get("stripe-signature"), settings.stripe_webhook_secret)
    except stripe_billing.StripeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    result = await stripe_billing.handle_event(db, event)
    await db.commit()
    return {"received": True, "result": result}
