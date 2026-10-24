"""Meetings: schedule, invite, reschedule, cancel and complete.

Meetings live in the existing ``meetings`` table (provider "aiva"). Invites go
out by email with a calendar (.ics) attachment when mail is set up; teammates
also get an in-app notification. Completing a meeting logs it on the related
records' timelines and can turn action items into follow-up tasks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.platform import Notification
from app.models.task import Task
from app.models.user import User
from app.services import mailer
from app.services.automation import record_audit
from app.services.lookups import names_for

logger = logging.getLogger("aiva.meetings")

router = APIRouter(tags=["Meetings"], dependencies=[Depends(require_feature("tasks_meetings_calls"))])

PROVIDER = "aiva"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id(value: Any) -> str | None:
    return str(value) if value else None


# ---------------------------------------------------------------------------
# Calendar file
# ---------------------------------------------------------------------------


def _ics_text(value: str | None) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


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


def meeting_ics(meeting: Meeting, organizer_name: str, organizer_email: str, method: str = "REQUEST") -> bytes:
    end = meeting.end_at or (meeting.start_at + timedelta(hours=1))
    sequence = int((meeting.meeting_metadata or {}).get("sequence", 0))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AIVA CRM//Meetings//EN",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:meeting-{meeting.id}@aiva",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_stamp(utcnow())}",
    ]
    if meeting.is_all_day:
        lines += [f"DTSTART;VALUE=DATE:{meeting.start_at:%Y%m%d}", f"DTEND;VALUE=DATE:{(end if end.date() > meeting.start_at.date() else meeting.start_at + timedelta(days=1)):%Y%m%d}"]
    else:
        lines += [f"DTSTART:{_stamp(meeting.start_at)}", f"DTEND:{_stamp(end)}"]
    lines += [
        f"SUMMARY:{_ics_text(meeting.title)}",
        f"DESCRIPTION:{_ics_text((meeting.description or '') + (chr(10) + 'Join: ' + meeting.meeting_url if meeting.meeting_url else ''))}",
        f"LOCATION:{_ics_text(meeting.meeting_url or meeting.location or '')}",
        f"ORGANIZER;CN={_ics_text(organizer_name)}:mailto:{organizer_email}",
        f"STATUS:{'CANCELLED' if method == 'CANCEL' or meeting.status == 'cancelled' else 'CONFIRMED'}",
    ]
    for attendee in meeting.attendees or []:
        if attendee.get("email"):
            lines.append(f"ATTENDEE;CN={_ics_text(attendee.get('name') or attendee['email'])};ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{attendee['email']}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AttendeePayload(BaseModel):
    type: str = Field(pattern="^(user|contact|lead|email)$")
    id: uuid.UUID | None = None
    email: str | None = Field(default=None, max_length=320)
    name: str | None = Field(default=None, max_length=200)


def _check_url(value: str | None) -> str | None:
    value = (value or "").strip()
    if value and not value.lower().startswith(("https://", "http://")):
        raise ValueError("Meeting links must start with https://")
    return value or None


class MeetingPayload(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    location: str | None = Field(default=None, max_length=500)
    meeting_url: str | None = Field(default=None, max_length=1000)
    is_online: bool = False
    is_all_day: bool = False
    start_at: datetime
    end_at: datetime
    timezone: str | None = Field(default=None, max_length=100)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    organizer_user_id: uuid.UUID | None = None
    attendees: list[AttendeePayload] = Field(default_factory=list, max_length=100)
    reminder_minutes: int | None = Field(default=15, ge=0, le=10080)
    send_invites: bool = True

    @field_validator("meeting_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _check_url(value)


class MeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    location: str | None = Field(default=None, max_length=500)
    meeting_url: str | None = Field(default=None, max_length=1000)
    is_online: bool | None = None
    is_all_day: bool | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=100)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    organizer_user_id: uuid.UUID | None = None
    attendees: list[AttendeePayload] | None = Field(default=None, max_length=100)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)
    notes: str | None = Field(default=None, max_length=20000)
    send_updates: bool = True

    @field_validator("meeting_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _check_url(value)


class CompletePayload(BaseModel):
    notes: str | None = Field(default=None, max_length=20000)
    summary: str | None = Field(default=None, max_length=5000)
    action_items: list[str] = Field(default_factory=list, max_length=50)
    create_tasks: bool = True
    task_due_in_days: int = Field(default=2, ge=0, le=90)


class CancelPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    notify: bool = True


class RespondPayload(BaseModel):
    response: str = Field(pattern="^(accepted|declined|tentative)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _meeting(db: AsyncSession, principal: Principal, meeting_id: uuid.UUID) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or meeting.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found.")
    return meeting


async def _check_links(db: AsyncSession, organization_id: uuid.UUID, data: dict[str, Any]) -> None:
    for key, model, label in (
        ("account_id", Account, "account"),
        ("contact_id", Contact, "contact"),
        ("lead_id", Lead, "lead"),
        ("opportunity_id", Opportunity, "deal"),
    ):
        if data.get(key) is None:
            continue
        record = await db.get(model, data[key])
        if record is None or record.organization_id != organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The selected {label} isn't in this workspace.")
    if data.get("organizer_user_id"):
        await _member(db, organization_id, data["organizer_user_id"])


async def _member(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID) -> User:
    member = (
        await db.execute(
            select(User)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == organization_id, User.id == user_id, OrganizationMembership.status == "active")
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That person isn't an active member of this workspace.")
    return member


async def _resolve_attendees(db: AsyncSession, organization_id: uuid.UUID, items: list[AttendeePayload], previous: list[dict]) -> list[dict]:
    responses = {(a.get("type"), a.get("id") or a.get("email")): a.get("response") for a in previous or []}
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if item.type == "email":
            email = (item.email or "").strip().lower()
            if not email or "@" not in email:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a valid email for each guest.")
            entry = {"type": "email", "id": None, "email": email, "name": item.name or email}
        else:
            if item.id is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a person for each attendee.")
            if item.type == "user":
                user = await _member(db, organization_id, item.id)
                entry = {"type": "user", "id": str(user.id), "email": user.email, "name": user.full_name}
            else:
                model = Contact if item.type == "contact" else Lead
                record = await db.get(model, item.id)
                if record is None or record.organization_id != organization_id:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The selected {item.type} isn't in this workspace.")
                name = f"{record.first_name or ''} {record.last_name or ''}".strip() or record.email
                entry = {"type": item.type, "id": str(record.id), "email": (record.email or None), "name": name}
        key = entry["email"] or f"{entry['type']}:{entry['id']}"
        if key in seen:
            continue
        seen.add(key)
        entry["response"] = responses.get((entry["type"], entry["id"] or entry["email"])) or "needs_action"
        out.append(entry)
    return out


async def _conflicts(db: AsyncSession, meeting: Meeting) -> list[dict]:
    """Other scheduled meetings that overlap for the organizer or internal attendees."""

    users = {str(meeting.organizer_user_id)} if meeting.organizer_user_id else set()
    users |= {a["id"] for a in meeting.attendees or [] if a.get("type") == "user" and a.get("id")}
    if not users:
        return []
    end = meeting.end_at or meeting.start_at + timedelta(hours=1)
    rows = (
        await db.execute(
            select(Meeting).where(
                Meeting.organization_id == meeting.organization_id,
                Meeting.id != meeting.id,
                Meeting.status == "scheduled",
                Meeting.start_at < end,
                Meeting.end_at > meeting.start_at,
            )
        )
    ).scalars().all()
    found = []
    for other in rows:
        others = {str(other.organizer_user_id)} | {a["id"] for a in other.attendees or [] if a.get("type") == "user" and a.get("id")}
        if users & others:
            found.append({"id": str(other.id), "title": other.title, "start_at": other.start_at, "end_at": other.end_at})
    return found


async def meeting_out(db: AsyncSession, meeting: Meeting, principal: Principal | None = None) -> dict[str, Any]:
    org = meeting.organization_id
    accounts = await names_for(db, org, "account", [meeting.account_id])
    contacts = await names_for(db, org, "contact", [meeting.contact_id])
    opps = await names_for(db, org, "opportunity", [meeting.opportunity_id])
    users = await names_for(db, org, "user", [meeting.organizer_user_id])
    lead_name = None
    if meeting.lead_id:
        lead = await db.get(Lead, meeting.lead_id)
        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else None
    metadata = meeting.meeting_metadata or {}
    me = str(principal.user_id) if principal else None
    my_response = next((a.get("response") for a in meeting.attendees or [] if a.get("type") == "user" and a.get("id") == me), None)
    return {
        "id": str(meeting.id),
        "title": meeting.title,
        "description": meeting.description,
        "location": meeting.location,
        "meeting_url": meeting.meeting_url,
        "is_online": meeting.is_online,
        "is_all_day": meeting.is_all_day,
        "start_at": meeting.start_at,
        "end_at": meeting.end_at,
        "timezone": meeting.timezone,
        "status": meeting.status,
        "provider": meeting.provider,
        "account_id": _id(meeting.account_id),
        "account_name": accounts.get(str(meeting.account_id)),
        "contact_id": _id(meeting.contact_id),
        "contact_name": contacts.get(str(meeting.contact_id)),
        "lead_id": _id(meeting.lead_id),
        "lead_name": lead_name,
        "opportunity_id": _id(meeting.opportunity_id),
        "opportunity_name": opps.get(str(meeting.opportunity_id)),
        "organizer_user_id": _id(meeting.organizer_user_id),
        "organizer_name": users.get(str(meeting.organizer_user_id)),
        "attendees": meeting.attendees or [],
        "notes": meeting.notes,
        "summary": meeting.summary,
        "action_items": meeting.action_items or [],
        "reminder_minutes": metadata.get("reminder_minutes"),
        "cancel_reason": metadata.get("cancel_reason"),
        "completed_at": metadata.get("completed_at"),
        "invites_sent": metadata.get("invites_sent", 0),
        "my_response": my_response,
        "can_edit": principal is not None and principal.can("records.write") and (_manages_others(principal) or str(meeting.organizer_user_id) == me),
        "created_at": meeting.created_at,
        "updated_at": meeting.updated_at,
    }


async def _organizer(db: AsyncSession, meeting: Meeting) -> tuple[str, str]:
    user = await db.get(User, meeting.organizer_user_id) if meeting.organizer_user_id else None
    return (user.full_name, user.email) if user else ("AIVA", "noreply@aiva.local")


async def _notify(db: AsyncSession, meeting: Meeting, actor: uuid.UUID, kind: str, method: str = "REQUEST", only_new: set[str] | None = None) -> int:
    """In-app notice to teammates and email (with .ics) to everyone with an address. Returns emails sent."""

    organizer_name, organizer_email = await _organizer(db, meeting)
    when = meeting.start_at.astimezone(timezone.utc).strftime("%a %b %d, %H:%M UTC")
    titles = {
        "invite": (f"Invitation: {meeting.title}", f"{organizer_name} invited you to “{meeting.title}” on {when}."),
        "update": (f"Updated: {meeting.title}", f"“{meeting.title}” changed. It's now on {when}."),
        "cancel": (f"Cancelled: {meeting.title}", f"“{meeting.title}” on {when} was cancelled."),
    }
    subject, body = titles[kind]
    for attendee in meeting.attendees or []:
        if attendee.get("type") == "user" and attendee.get("id") and attendee["id"] != str(actor):
            if only_new is not None and attendee["id"] not in only_new:
                continue
            db.add(
                Notification(
                    id=uuid.uuid4(),
                    organization_id=meeting.organization_id,
                    user_id=uuid.UUID(attendee["id"]),
                    kind="meeting",
                    title=subject[:200],
                    body=body[:500],
                    link=f"/meetings?open={meeting.id}",
                    is_read=False,
                )
            )
    config = await mailer.sender_config(db, meeting.organization_id, meeting.organizer_user_id)
    if config is None:
        return 0
    invite = meeting_ics(meeting, organizer_name, organizer_email, method)
    sent = 0
    for attendee in meeting.attendees or []:
        email = attendee.get("email")
        if not email or email == organizer_email:
            continue
        key = attendee.get("id") or email
        if only_new is not None and key not in only_new:
            continue
        lines = [f"Hi {(attendee.get('name') or '').split(' ')[0] or 'there'},", "", body, ""]
        if meeting.meeting_url and kind != "cancel":
            lines += [f"Join: {meeting.meeting_url}", ""]
        if meeting.location and kind != "cancel":
            lines += [f"Where: {meeting.location}", ""]
        if meeting.description and kind != "cancel":
            lines += [meeting.description, ""]
        consent = (meeting.meeting_metadata or {}).get("consent_notice")
        if consent and kind != "cancel" and attendee.get("type") != "user":
            lines += [consent, ""]
        lines.append(f"— {organizer_name}")
        try:
            await mailer.send(
                config,
                to=email,
                to_name=attendee.get("name"),
                subject=subject,
                text="\n".join(lines),
                reply_to=organizer_email,
                attachments=[("invite.ics", f"text/calendar; method={method}", invite)],
            )
            sent += 1
        except mailer.MailError as exc:
            logger.warning("Meeting invite to %s failed: %s", email, exc)
    return sent


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/meetings")
async def list_meetings(
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(scheduled|completed|cancelled)$"),
    mine: bool = False,
    record_type: str | None = Query(default=None, pattern="^(account|contact|lead|opportunity)$"),
    record_id: uuid.UUID | None = None,
    limit: int = Query(default=500, ge=1, le=1000),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Meeting).where(Meeting.organization_id == principal.organization_id)
    if start is not None:
        query = query.where(or_(Meeting.end_at >= start, and_(Meeting.end_at.is_(None), Meeting.start_at >= start)))
    if end is not None:
        query = query.where(Meeting.start_at < end)
    if status_filter:
        query = query.where(Meeting.status == status_filter)
    if record_type and record_id:
        column = {"account": Meeting.account_id, "contact": Meeting.contact_id, "lead": Meeting.lead_id, "opportunity": Meeting.opportunity_id}[record_type]
        query = query.where(column == record_id)
    meetings = (await db.execute(query.order_by(Meeting.start_at).limit(limit))).scalars().all()
    if mine or not principal.can("records.view_all"):
        me = str(principal.user_id)
        meetings = [
            m for m in meetings
            if str(m.organizer_user_id) == me or any(a.get("type") == "user" and a.get("id") == me for a in m.attendees or [])
        ]
    return {"items": [await meeting_out(db, m, principal) for m in meetings]}


@router.post("/meetings", status_code=status.HTTP_201_CREATED)
async def create_meeting(
    payload: MeetingPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    if payload.end_at <= payload.start_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The meeting must end after it starts.")
    if payload.end_at - payload.start_at > timedelta(days=14):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A meeting can't be longer than 14 days.")
    data = payload.model_dump(exclude={"attendees", "reminder_minutes", "send_invites"})
    await _check_links(db, org, data)
    organizer = data.get("organizer_user_id") or principal.user_id
    meeting = Meeting(
        id=uuid.uuid4(),
        organization_id=org,
        provider=PROVIDER,
        external_event_id=None,
        **{**data, "title": data["title"].strip(), "organizer_user_id": organizer},
        status="scheduled",
        attendees=await _resolve_attendees(db, org, payload.attendees, []),
        action_items=[],
        meeting_metadata={"reminder_minutes": payload.reminder_minutes, "sequence": 0},
    )
    if meeting.contact_id and not meeting.account_id:
        contact = await db.get(Contact, meeting.contact_id)
        meeting.account_id = contact.account_id if contact else None
    from app.services import outbound, workspace_settings

    recording = await workspace_settings.get(db, org, "recording")
    external = [a for a in meeting.attendees or [] if a.get("type") != "user"]
    if (
        principal.has("conversation_intelligence")
        and recording.add_consent_to_invites
        and (recording.record_mode == "all" or (recording.record_mode == "external" and external))
    ):
        meeting.meeting_metadata = {**meeting.meeting_metadata, "consent_notice": recording.consent_notice}
    db.add(meeting)
    await db.flush()
    conflicts = await _conflicts(db, meeting)
    await outbound.meeting_booked(db, org, {a.get("email") for a in external if a.get("email")})
    sent = await _notify(db, meeting, principal.user_id, "invite") if payload.send_invites else 0
    meeting.meeting_metadata = {**meeting.meeting_metadata, "invites_sent": sent}
    record_audit(db, organization_id=org, action="create", entity_type="meeting", entity_id=meeting.id, entity_label=meeting.title)
    await db.commit()
    data = await meeting_out(db, meeting, principal)
    data["conflicts"] = conflicts
    return data


@router.get("/meetings/{meeting_id}")
async def get_meeting(
    meeting_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    meeting = await _meeting(db, principal, meeting_id)
    data = await meeting_out(db, meeting, principal)
    data["conflicts"] = await _conflicts(db, meeting) if meeting.status == "scheduled" else []
    return data


def _manages_others(principal: Principal) -> bool:
    """Managers and admins (who can see and delete anyone's records) may change any meeting."""

    return principal.can("records.view_all") and principal.can("records.delete")


def _ensure_editor(principal: Principal, meeting: Meeting) -> None:
    if not (_manages_others(principal) or meeting.organizer_user_id == principal.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the organizer or a manager can change this meeting.")


@router.patch("/meetings/{meeting_id}")
async def update_meeting(
    meeting_id: uuid.UUID,
    payload: MeetingUpdate,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    meeting = await _meeting(db, principal, meeting_id)
    _ensure_editor(principal, meeting)
    changes = payload.model_dump(exclude_unset=True, exclude={"send_updates", "attendees", "reminder_minutes"})
    if meeting.status == "cancelled" and set(changes) - {"notes"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "This meeting was cancelled. Schedule a new one instead.")
    await _check_links(db, org, changes)
    timing_changed = False
    for key, value in changes.items():
        if key in {"title", "is_online", "is_all_day", "start_at", "organizer_user_id"} and value is None:
            continue
        if key in {"start_at", "end_at", "location", "meeting_url"} and getattr(meeting, key) != value:
            timing_changed = True
        setattr(meeting, key, value.strip() if key == "title" else value)
    if meeting.end_at is None or meeting.end_at <= meeting.start_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The meeting must end after it starts.")
    metadata = dict(meeting.meeting_metadata or {})
    if "reminder_minutes" in payload.model_fields_set:
        metadata["reminder_minutes"] = payload.reminder_minutes
        metadata.pop("reminded_at", None)
    if timing_changed:
        metadata.pop("reminded_at", None)
    new_people: set[str] = set()
    if payload.attendees is not None:
        before = {a.get("id") or a.get("email") for a in meeting.attendees or []}
        meeting.attendees = await _resolve_attendees(db, org, payload.attendees, meeting.attendees or [])
        new_people = {a.get("id") or a.get("email") for a in meeting.attendees} - before
    sent = 0
    if meeting.status == "scheduled" and payload.send_updates:
        if timing_changed:
            metadata["sequence"] = int(metadata.get("sequence", 0)) + 1
            meeting.meeting_metadata = metadata
            sent = await _notify(db, meeting, principal.user_id, "update")
        elif new_people:
            meeting.meeting_metadata = metadata
            sent = await _notify(db, meeting, principal.user_id, "invite", only_new=new_people)
    metadata["invites_sent"] = int(metadata.get("invites_sent", 0)) + sent
    meeting.meeting_metadata = metadata
    await db.commit()
    data = await meeting_out(db, meeting, principal)
    data["conflicts"] = await _conflicts(db, meeting) if meeting.status == "scheduled" else []
    return data


@router.post("/meetings/{meeting_id}/complete")
async def complete_meeting(
    meeting_id: uuid.UUID,
    payload: CompletePayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    meeting = await _meeting(db, principal, meeting_id)
    _ensure_editor(principal, meeting)
    if meeting.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This meeting was cancelled.")
    if meeting.status == "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This meeting is already marked as held.")
    items = [item.strip() for item in payload.action_items if item.strip()]
    meeting.status = "completed"
    meeting.notes = payload.notes if payload.notes is not None else meeting.notes
    meeting.summary = payload.summary
    meeting.action_items = items
    metadata = dict(meeting.meeting_metadata or {})
    metadata["completed_at"] = utcnow().isoformat()
    meeting.meeting_metadata = metadata
    if meeting.account_id or meeting.contact_id or meeting.lead_id or meeting.opportunity_id:
        body = "\n\n".join(filter(None, [payload.summary, payload.notes, ("Action items:\n" + "\n".join(f"• {i}" for i in items)) if items else None]))
        db.add(
            Activity(
                id=uuid.uuid4(),
                organization_id=meeting.organization_id,
                account_id=meeting.account_id,
                contact_id=meeting.contact_id,
                lead_id=meeting.lead_id,
                opportunity_id=meeting.opportunity_id,
                created_by_user_id=principal.user_id,
                activity_type="meeting",
                subject=f"Meeting: {meeting.title}"[:300],
                body=body or None,
                occurred_at=meeting.start_at,
                activity_metadata={"meeting_id": str(meeting.id), "attendees": len(meeting.attendees or [])},
            )
        )
    created = 0
    if payload.create_tasks:
        due = utcnow() + timedelta(days=payload.task_due_in_days)
        for item in items:
            db.add(
                Task(
                    id=uuid.uuid4(),
                    organization_id=meeting.organization_id,
                    account_id=meeting.account_id,
                    contact_id=meeting.contact_id,
                    lead_id=meeting.lead_id,
                    opportunity_id=meeting.opportunity_id,
                    assigned_to_user_id=meeting.organizer_user_id or principal.user_id,
                    created_by_user_id=principal.user_id,
                    title=item[:300],
                    description=f"Action item from “{meeting.title}”.",
                    task_type="follow_up",
                    status="open",
                    priority="medium",
                    source="meeting",
                    due_at=due,
                    tags=[],
                    task_metadata={"meeting_id": str(meeting.id)},
                )
            )
            created += 1
    await db.commit()
    data = await meeting_out(db, meeting, principal)
    data["tasks_created"] = created
    return data


@router.post("/meetings/{meeting_id}/cancel")
async def cancel_meeting(
    meeting_id: uuid.UUID,
    payload: CancelPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    meeting = await _meeting(db, principal, meeting_id)
    _ensure_editor(principal, meeting)
    if meeting.status != "scheduled":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This meeting is already {meeting.status}.")
    meeting.status = "cancelled"
    metadata = dict(meeting.meeting_metadata or {})
    metadata["cancel_reason"] = payload.reason
    metadata["sequence"] = int(metadata.get("sequence", 0)) + 1
    meeting.meeting_metadata = metadata
    if payload.notify:
        await _notify(db, meeting, principal.user_id, "cancel", method="CANCEL")
    record_audit(db, organization_id=meeting.organization_id, action="cancel", entity_type="meeting", entity_id=meeting.id, entity_label=meeting.title, changes={"reason": payload.reason})
    await db.commit()
    return await meeting_out(db, meeting, principal)


@router.post("/meetings/{meeting_id}/respond")
async def respond_to_meeting(
    meeting_id: uuid.UUID,
    payload: RespondPayload,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    meeting = await _meeting(db, principal, meeting_id)
    me = str(principal.user_id)
    attendees = [dict(a) for a in meeting.attendees or []]
    mine = next((a for a in attendees if a.get("type") == "user" and a.get("id") == me), None)
    if mine is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You're not on the guest list for this meeting.")
    mine["response"] = payload.response
    meeting.attendees = attendees
    if meeting.organizer_user_id and meeting.organizer_user_id != principal.user_id:
        verb = {"accepted": "accepted", "declined": "declined", "tentative": "might attend"}[payload.response]
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=meeting.organization_id,
                user_id=meeting.organizer_user_id,
                kind="meeting",
                title=f"{principal.full_name or 'A teammate'} {verb}: {meeting.title}"[:200],
                body=None,
                link=f"/meetings?open={meeting.id}",
                is_read=False,
            )
        )
    await db.commit()
    return await meeting_out(db, meeting, principal)


@router.get("/meetings/{meeting_id}/invite.ics")
async def download_invite(
    meeting_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    meeting = await _meeting(db, principal, meeting_id)
    name, email = await _organizer(db, meeting)
    return Response(
        content=meeting_ics(meeting, name, email, "CANCEL" if meeting.status == "cancelled" else "REQUEST"),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="meeting.ics"'},
    )


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(
    meeting_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.delete")),
    db: AsyncSession = Depends(get_db),
):
    meeting = await _meeting(db, principal, meeting_id)
    _ensure_editor(principal, meeting)
    record_audit(db, organization_id=meeting.organization_id, action="delete", entity_type="meeting", entity_id=meeting.id, entity_label=meeting.title)
    await db.delete(meeting)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Reminders (called by the background scheduler)
# ---------------------------------------------------------------------------


async def send_meeting_reminders(db: AsyncSession) -> int:
    now = utcnow()
    meetings = (
        await db.execute(
            select(Meeting).where(
                Meeting.status == "scheduled",
                Meeting.start_at > now,
                Meeting.start_at <= now + timedelta(days=7),
                Meeting.meeting_metadata["reminded_at"].astext.is_(None),
            ).limit(200)
        )
    ).scalars().all()
    count = 0
    for meeting in meetings:
        minutes = (meeting.meeting_metadata or {}).get("reminder_minutes")
        if minutes is None or meeting.start_at - timedelta(minutes=int(minutes)) > now:
            continue
        people = {meeting.organizer_user_id} if meeting.organizer_user_id else set()
        people |= {uuid.UUID(a["id"]) for a in meeting.attendees or [] if a.get("type") == "user" and a.get("id") and a.get("response") != "declined"}
        when = meeting.start_at.astimezone(timezone.utc).strftime("%H:%M UTC")
        for user_id in people:
            db.add(
                Notification(
                    id=uuid.uuid4(),
                    organization_id=meeting.organization_id,
                    user_id=user_id,
                    kind="reminder",
                    title=f"Starting soon: {meeting.title}"[:200],
                    body=f"Starts at {when}." + (f" Join: {meeting.meeting_url}" if meeting.meeting_url else ""),
                    link=f"/meetings?open={meeting.id}",
                    is_read=False,
                )
            )
        meeting.meeting_metadata = {**(meeting.meeting_metadata or {}), "reminded_at": now.isoformat()}
        count += 1
    if count:
        await db.commit()
    return count

