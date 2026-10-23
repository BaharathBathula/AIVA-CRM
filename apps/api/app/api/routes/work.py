"""Services and appointments, and projects with milestones and tasks."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
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
from app.models.organization import Organization
from app.models.platform import Notification
from app.models.task import Task
from app.models.user import User
from app.models.work import Appointment, Project, ProjectMilestone, Service
from app.services import mailer
from app.services.automation import record_audit
from app.services.lookups import names_for

logger = logging.getLogger("aiva.work")

router = APIRouter(tags=["Services and projects"])

CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id(value: Any) -> str | None:
    return str(value) if value else None


async def _owned(db: AsyncSession, model: type, record_id: Any, organization_id: uuid.UUID, label: str):
    if record_id is None:
        return None
    record = await db.get(model, record_id)
    if record is None or record.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found.")
    return record


async def _active_member_ids(db: AsyncSession, organization_id: uuid.UUID) -> set[str]:
    rows = await db.execute(
        select(OrganizationMembership.user_id).where(
            OrganizationMembership.organization_id == organization_id, OrganizationMembership.status == "active"
        )
    )
    return {str(r) for r in rows.scalars()}


async def _check_members(db: AsyncSession, organization_id: uuid.UUID, user_ids: list[Any]) -> list[str]:
    ids = [str(u) for u in user_ids if u]
    if not ids:
        return []
    active = await _active_member_ids(db, organization_id)
    unknown = [u for u in ids if u not in active]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Everyone you pick must be an active member of this workspace.")
    return list(dict.fromkeys(ids))


async def _check_links(db: AsyncSession, organization_id: uuid.UUID, data: dict[str, Any]) -> None:
    for key, model, label in (
        ("account_id", Account, "account"),
        ("contact_id", Contact, "contact"),
        ("lead_id", Lead, "lead"),
        ("opportunity_id", Opportunity, "deal"),
        ("service_id", Service, "service"),
    ):
        if data.get(key) is None:
            continue
        record = await db.get(model, data[key])
        if record is None or record.organization_id != organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The selected {label} isn't in this workspace.")


# ===========================================================================
# Services
# ===========================================================================


class ServicePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    duration_minutes: int = Field(default=60, ge=5, le=1440)
    buffer_minutes: int = Field(default=0, ge=0, le=240)
    price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    location_type: str = Field(default="online", pattern="^(in_person|online|phone|customer_site)$")
    location: str | None = Field(default=None, max_length=300)
    staff_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    is_active: bool = True


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    location_type: str | None = Field(default=None, pattern="^(in_person|online|phone|customer_site)$")
    location: str | None = Field(default=None, max_length=300)
    staff_user_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    is_active: bool | None = None


async def service_out(db: AsyncSession, service: Service, upcoming: int | None = None) -> dict[str, Any]:
    staff = await names_for(db, service.organization_id, "user", service.staff_user_ids or [])
    return {
        "id": str(service.id),
        "name": service.name,
        "category": service.category,
        "description": service.description,
        "duration_minutes": service.duration_minutes,
        "buffer_minutes": service.buffer_minutes,
        "price": float(service.price),
        "currency": service.currency,
        "location_type": service.location_type,
        "location": service.location,
        "staff": [{"id": uid, "name": staff.get(uid, "Former member")} for uid in service.staff_user_ids or []],
        "staff_user_ids": service.staff_user_ids or [],
        "is_active": service.is_active,
        "upcoming_appointments": upcoming,
        "created_at": service.created_at,
        "updated_at": service.updated_at,
    }


async def _unique_service(db: AsyncSession, organization_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> None:
    query = select(Service.id).where(Service.organization_id == organization_id, func.lower(Service.name) == name.lower())
    if exclude:
        query = query.where(Service.id != exclude)
    if (await db.execute(query)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"A service called “{name}” already exists.")


@router.get("/services", dependencies=[Depends(require_feature("services"))])
async def list_services(
    active: bool | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Service).where(Service.organization_id == principal.organization_id)
    if active is not None:
        query = query.where(Service.is_active.is_(active))
    services = (await db.execute(query.order_by(Service.category.nulls_last(), Service.name))).scalars().all()
    counts = dict(
        (
            await db.execute(
                select(Appointment.service_id, func.count())
                .where(Appointment.organization_id == principal.organization_id, Appointment.status == "scheduled", Appointment.start_at >= utcnow())
                .group_by(Appointment.service_id)
            )
        ).all()
    )
    return {
        "items": [await service_out(db, s, counts.get(s.id, 0)) for s in services],
        "can_manage": principal.can("service.services"),
    }


@router.post("/services", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("services"))])
async def create_service(
    payload: ServicePayload,
    principal: Principal = Depends(require_permission("service.services")),
    db: AsyncSession = Depends(get_db),
):
    name = payload.name.strip()
    await _unique_service(db, principal.organization_id, name)
    staff = await _check_members(db, principal.organization_id, payload.staff_user_ids)
    service = Service(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        **{**payload.model_dump(exclude={"staff_user_ids"}), "name": name, "currency": payload.currency.upper(), "price": money(payload.price)},
        staff_user_ids=staff,
    )
    db.add(service)
    await db.commit()
    return await service_out(db, service, 0)


@router.patch("/services/{service_id}", dependencies=[Depends(require_feature("services"))])
async def update_service(
    service_id: uuid.UUID,
    payload: ServiceUpdate,
    principal: Principal = Depends(require_permission("service.services")),
    db: AsyncSession = Depends(get_db),
):
    service = await _owned(db, Service, service_id, principal.organization_id, "Service")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name"):
        changes["name"] = changes["name"].strip()
        await _unique_service(db, principal.organization_id, changes["name"], exclude=service.id)
    if changes.get("staff_user_ids") is not None:
        changes["staff_user_ids"] = await _check_members(db, principal.organization_id, changes["staff_user_ids"])
    for key, value in changes.items():
        if value is None and key in {"name", "duration_minutes", "buffer_minutes", "price", "currency", "location_type", "staff_user_ids", "is_active"}:
            continue
        if key == "currency":
            value = value.upper()
        if key == "price":
            value = money(value)
        setattr(service, key, value)
    await db.commit()
    return await service_out(db, service)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("services"))])
async def delete_service(
    service_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.services")),
    db: AsyncSession = Depends(get_db),
):
    service = await _owned(db, Service, service_id, principal.organization_id, "Service")
    used = (await db.execute(select(func.count()).select_from(Appointment).where(Appointment.service_id == service.id))).scalar_one()
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, "This service has appointments. Turn it off instead so it can't be booked.")
    await db.delete(service)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Appointments
# ===========================================================================


async def _org_zone(db: AsyncSession, organization_id: uuid.UUID) -> ZoneInfo:
    organization = await db.get(Organization, organization_id)
    name = ((organization.settings or {}).get("timezone") if organization else None) or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


async def _busy(db: AsyncSession, organization_id: uuid.UUID, user_id: str, start: datetime, end: datetime, exclude: uuid.UUID | None = None) -> list[dict]:
    """Appointments and meetings that overlap [start, end) for a person."""

    staff = uuid.UUID(user_id)
    appointments = (
        await db.execute(
            select(Appointment, Service.buffer_minutes)
            .outerjoin(Service, Service.id == Appointment.service_id)
            .where(
                Appointment.organization_id == organization_id,
                Appointment.staff_user_id == staff,
                Appointment.status == "scheduled",
                Appointment.start_at < end + timedelta(hours=4),
                Appointment.end_at > start - timedelta(hours=4),
                *( [Appointment.id != exclude] if exclude else [] ),
            )
        )
    ).all()
    found = []
    for appointment, buffer in appointments:
        pad = timedelta(minutes=buffer or 0)
        if appointment.start_at - pad < end and appointment.end_at + pad > start:
            found.append({"kind": "appointment", "id": str(appointment.id), "title": appointment.title, "start_at": appointment.start_at, "end_at": appointment.end_at})
    meetings = (
        await db.execute(
            select(Meeting).where(
                Meeting.organization_id == organization_id,
                Meeting.status == "scheduled",
                Meeting.start_at < end,
                Meeting.end_at > start,
            )
        )
    ).scalars().all()
    for meeting in meetings:
        people = {str(meeting.organizer_user_id)} | {a.get("id") for a in meeting.attendees or [] if a.get("type") == "user" and a.get("response") != "declined"}
        if user_id in people:
            found.append({"kind": "meeting", "id": str(meeting.id), "title": meeting.title, "start_at": meeting.start_at, "end_at": meeting.end_at})
    return found


@router.get("/services/{service_id}/availability", dependencies=[Depends(require_feature("services"))])
async def availability(
    service_id: uuid.UUID,
    day: date = Query(alias="date"),
    staff_user_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    """Free start times on a day (business hours 9:00–17:00 in the workspace's time zone, Monday to Friday)."""

    org = principal.organization_id
    service = await _owned(db, Service, service_id, org, "Service")
    zone = await _org_zone(db, org)
    staff_ids = [str(staff_user_id)] if staff_user_id else list(service.staff_user_ids or [])
    if not staff_ids:
        staff_ids = [str(principal.user_id)]
    names = await names_for(db, org, "user", staff_ids)
    slots: list[dict] = []
    if day.weekday() < 5:
        opening = datetime.combine(day, time(9), tzinfo=zone)
        closing = datetime.combine(day, time(17), tzinfo=zone)
        length = timedelta(minutes=service.duration_minutes)
        pad = timedelta(minutes=service.buffer_minutes)
        now = utcnow()
        for staff in staff_ids:
            busy = await _busy(db, org, staff, opening - pad, closing + pad)
            cursor = opening
            while cursor + length <= closing:
                end = cursor + length
                clash = any(b["start_at"] - pad < end and b["end_at"] + pad > cursor for b in busy)
                if not clash and cursor > now:
                    slots.append({"start_at": cursor, "end_at": end, "staff_user_id": staff, "staff_name": names.get(staff)})
                cursor += timedelta(minutes=30)
    return {"date": day, "timezone": str(zone), "slots": slots}


class AppointmentPayload(BaseModel):
    service_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    staff_user_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    start_at: datetime
    end_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)
    price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=10000)
    notify_customer: bool = True
    allow_overlap: bool = False


class AppointmentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    staff_user_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)
    price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=10000)
    notify_customer: bool = True
    allow_overlap: bool = False


class AppointmentStatus(BaseModel):
    status: str = Field(pattern="^(completed|cancelled|no_show)$")
    outcome: str | None = Field(default=None, max_length=10000)
    cancel_reason: str | None = Field(default=None, max_length=300)
    notify_customer: bool = True


async def appointment_out(db: AsyncSession, appointment: Appointment) -> dict[str, Any]:
    org = appointment.organization_id
    users = await names_for(db, org, "user", [appointment.staff_user_id])
    contacts = await names_for(db, org, "contact", [appointment.contact_id])
    accounts = await names_for(db, org, "account", [appointment.account_id])
    service = await db.get(Service, appointment.service_id) if appointment.service_id else None
    lead_name = None
    if appointment.lead_id:
        lead = await db.get(Lead, appointment.lead_id)
        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else None
    return {
        "id": str(appointment.id),
        "number": appointment.number,
        "display_number": f"APT-{appointment.number}",
        "title": appointment.title,
        "status": appointment.status,
        "service_id": _id(appointment.service_id),
        "service_name": service.name if service else None,
        "staff_user_id": _id(appointment.staff_user_id),
        "staff_name": users.get(str(appointment.staff_user_id)),
        "contact_id": _id(appointment.contact_id),
        "contact_name": contacts.get(str(appointment.contact_id)),
        "account_id": _id(appointment.account_id),
        "account_name": accounts.get(str(appointment.account_id)),
        "lead_id": _id(appointment.lead_id),
        "lead_name": lead_name,
        "start_at": appointment.start_at,
        "end_at": appointment.end_at,
        "location": appointment.location,
        "price": float(appointment.price),
        "currency": appointment.currency,
        "notes": appointment.notes,
        "outcome": appointment.outcome,
        "cancel_reason": appointment.cancel_reason,
        "completed_at": appointment.completed_at,
        "created_at": appointment.created_at,
        "updated_at": appointment.updated_at,
    }


async def _customer(db: AsyncSession, appointment: Appointment) -> tuple[str | None, str | None]:
    if appointment.contact_id:
        contact = await db.get(Contact, appointment.contact_id)
        if contact and contact.email:
            return contact.email, f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    if appointment.lead_id:
        lead = await db.get(Lead, appointment.lead_id)
        if lead and lead.email:
            return lead.email, f"{lead.first_name or ''} {lead.last_name or ''}".strip()
    return None, None


async def _email_customer(db: AsyncSession, appointment: Appointment, kind: str) -> bool:
    email, name = await _customer(db, appointment)
    if not email:
        return False
    config = await mailer.sender_config(db, appointment.organization_id, appointment.staff_user_id)
    if config is None:
        return False
    zone = await _org_zone(db, appointment.organization_id)
    when = appointment.start_at.astimezone(zone).strftime("%A %B %d at %H:%M %Z")
    staff = await db.get(User, appointment.staff_user_id) if appointment.staff_user_id else None
    subject = {
        "booked": f"Confirmed: {appointment.title}",
        "rescheduled": f"Rescheduled: {appointment.title}",
        "cancelled": f"Cancelled: {appointment.title}",
    }[kind]
    sentence = {
        "booked": f"your appointment “{appointment.title}” is booked for {when}.",
        "rescheduled": f"your appointment “{appointment.title}” has moved to {when}.",
        "cancelled": f"your appointment “{appointment.title}” on {when} has been cancelled.",
    }[kind]
    lines = [f"Hi {(name or '').split(' ')[0] or 'there'},", "", sentence[0].upper() + sentence[1:], ""]
    if appointment.location and kind != "cancelled":
        lines += [f"Where: {appointment.location}", ""]
    if staff:
        lines.append(f"— {staff.full_name}")
    try:
        await mailer.send(config, to=email, to_name=name, subject=subject, text="\n".join(lines), reply_to=staff.email if staff else None)
        return True
    except mailer.MailError as exc:
        logger.warning("Appointment email to %s failed: %s", email, exc)
        return False


async def _validate_slot(db: AsyncSession, appointment: Appointment, allow_overlap: bool) -> None:
    if appointment.end_at <= appointment.start_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The appointment must end after it starts.")
    if appointment.staff_user_id is None or allow_overlap:
        return
    busy = await _busy(db, appointment.organization_id, str(appointment.staff_user_id), appointment.start_at, appointment.end_at, exclude=appointment.id)
    if busy:
        first = busy[0]
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{(await names_for(db, appointment.organization_id, 'user', [appointment.staff_user_id])).get(str(appointment.staff_user_id), 'They')} "
            f"is already booked: “{first['title']}” at {first['start_at'].astimezone(timezone.utc):%H:%M} UTC. Pick another time or book anyway.",
        )


@router.get("/appointments", dependencies=[Depends(require_feature("services"))])
async def list_appointments(
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(scheduled|completed|cancelled|no_show)$"),
    staff_user_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(Appointment).where(Appointment.organization_id == org)
    if start:
        query = query.where(Appointment.end_at >= start)
    if end:
        query = query.where(Appointment.start_at < end)
    if status_filter:
        query = query.where(Appointment.status == status_filter)
    if staff_user_id:
        query = query.where(Appointment.staff_user_id == staff_user_id)
    if contact_id:
        query = query.where(Appointment.contact_id == contact_id)
    if account_id:
        query = query.where(Appointment.account_id == account_id)
    if not principal.can("records.view_all"):
        query = query.where(or_(Appointment.staff_user_id == principal.user_id, Appointment.created_by_user_id == principal.user_id))
    items = (await db.execute(query.order_by(Appointment.start_at).limit(1000))).scalars().all()
    since = utcnow() - timedelta(days=30)
    recent = (
        await db.execute(
            select(Appointment.status, Appointment.price).where(Appointment.organization_id == org, Appointment.start_at >= since, Appointment.start_at <= utcnow())
        )
    ).all()
    finished = [s for s, _ in recent if s in {"completed", "no_show"}]
    zone = await _org_zone(db, org)
    today = datetime.now(zone).date()
    day_start = datetime.combine(today, time(0), tzinfo=zone)
    upcoming = (
        await db.execute(
            select(Appointment.start_at).where(Appointment.organization_id == org, Appointment.status == "scheduled", Appointment.start_at >= day_start)
        )
    ).scalars().all()
    return {
        "items": [await appointment_out(db, a) for a in items],
        "summary": {
            "today": len([s for s in upcoming if s < day_start + timedelta(days=1)]),
            "next_7_days": len([s for s in upcoming if s < day_start + timedelta(days=7)]),
            "completed_30d": len([s for s in finished if s == "completed"]),
            "revenue_30d": float(sum(money(p) for s, p in recent if s == "completed")),
            "no_show_rate": round(len([s for s in finished if s == "no_show"]) / len(finished) * 100) if finished else 0,
        },
        "timezone": str(zone),
    }


async def _next_appointment_number(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return int((await db.execute(select(func.coalesce(func.max(Appointment.number), 1000)).where(Appointment.organization_id == organization_id))).scalar_one()) + 1


@router.post("/appointments", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("services"))])
async def create_appointment(
    payload: AppointmentPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    data = payload.model_dump(exclude={"notify_customer", "allow_overlap"})
    await _check_links(db, org, data)
    service = await db.get(Service, payload.service_id) if payload.service_id else None
    if service is not None and not service.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"“{service.name}” is turned off and can't be booked.")
    staff_id = payload.staff_user_id or (uuid.UUID(service.staff_user_ids[0]) if service and service.staff_user_ids else principal.user_id)
    await _check_members(db, org, [staff_id])
    if service is not None and service.staff_user_ids and str(staff_id) not in service.staff_user_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, f"That person doesn't deliver “{service.name}”. Add them to the service first.")
    if not (payload.contact_id or payload.lead_id or payload.account_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose who the appointment is with (a contact, lead or account).")
    title = (payload.title or "").strip() or (service.name if service else "")
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give the appointment a title or pick a service.")
    account_id = payload.account_id
    if payload.contact_id and not account_id:
        contact = await db.get(Contact, payload.contact_id)
        account_id = contact.account_id
    appointment = Appointment(
        id=uuid.uuid4(),
        organization_id=org,
        number=await _next_appointment_number(db, org),
        service_id=payload.service_id,
        title=title,
        status="scheduled",
        staff_user_id=staff_id,
        contact_id=payload.contact_id,
        account_id=account_id,
        lead_id=payload.lead_id,
        start_at=payload.start_at,
        end_at=payload.end_at or payload.start_at + timedelta(minutes=service.duration_minutes if service else 60),
        location=payload.location if payload.location is not None else (service.location if service else None),
        price=money(payload.price if payload.price is not None else (service.price if service else 0)),
        currency=service.currency if service else "USD",
        notes=payload.notes,
        created_by_user_id=principal.user_id,
    )
    await _validate_slot(db, appointment, payload.allow_overlap)
    db.add(appointment)
    if staff_id != principal.user_id:
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=org,
                user_id=staff_id,
                kind="appointment",
                title=f"New appointment: {title}"[:200],
                body=f"{appointment.start_at.astimezone(timezone.utc):%a %b %d, %H:%M} UTC",
                link=f"/appointments?open={appointment.id}",
                is_read=False,
            )
        )
    emailed = await _email_customer(db, appointment, "booked") if payload.notify_customer else False
    await db.commit()
    data = await appointment_out(db, appointment)
    data["customer_emailed"] = emailed
    return data


@router.get("/appointments/{appointment_id}", dependencies=[Depends(require_feature("services"))])
async def get_appointment(
    appointment_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    return await appointment_out(db, await _owned(db, Appointment, appointment_id, principal.organization_id, "Appointment"))


@router.patch("/appointments/{appointment_id}", dependencies=[Depends(require_feature("services"))])
async def update_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentUpdate,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    appointment = await _owned(db, Appointment, appointment_id, org, "Appointment")
    changes = payload.model_dump(exclude_unset=True, exclude={"notify_customer", "allow_overlap"})
    if appointment.status != "scheduled" and set(changes) - {"notes"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This appointment is {appointment.status.replace('_', ' ')}. Only notes can change.")
    await _check_links(db, org, changes)
    if changes.get("staff_user_id"):
        await _check_members(db, org, [changes["staff_user_id"]])
    moved = False
    for key, value in changes.items():
        if value is None and key in {"title", "start_at", "staff_user_id"}:
            continue
        if key in {"start_at", "end_at"} and getattr(appointment, key) != value:
            moved = True
        if key == "start_at" and "end_at" not in changes:
            appointment.end_at = value + (appointment.end_at - appointment.start_at)
        if key == "price":
            value = money(value)
        setattr(appointment, key, value)
    if moved or "staff_user_id" in changes:
        await _validate_slot(db, appointment, payload.allow_overlap)
        appointment.reminder_sent_at = None
    emailed = await _email_customer(db, appointment, "rescheduled") if moved and payload.notify_customer else False
    await db.commit()
    data = await appointment_out(db, appointment)
    data["customer_emailed"] = emailed
    return data


@router.post("/appointments/{appointment_id}/status", dependencies=[Depends(require_feature("services"))])
async def change_appointment_status(
    appointment_id: uuid.UUID,
    payload: AppointmentStatus,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    appointment = await _owned(db, Appointment, appointment_id, principal.organization_id, "Appointment")
    if appointment.status != "scheduled":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This appointment is already {appointment.status.replace('_', ' ')}.")
    if payload.status in {"completed", "no_show"} and appointment.start_at > utcnow():
        raise HTTPException(status.HTTP_409_CONFLICT, "This appointment hasn't started yet.")
    appointment.status = payload.status
    emailed = False
    if payload.status == "completed":
        appointment.outcome = payload.outcome
        appointment.completed_at = utcnow()
        if appointment.contact_id or appointment.account_id or appointment.lead_id:
            db.add(
                Activity(
                    id=uuid.uuid4(),
                    organization_id=appointment.organization_id,
                    account_id=appointment.account_id,
                    contact_id=appointment.contact_id,
                    lead_id=appointment.lead_id,
                    created_by_user_id=principal.user_id,
                    activity_type="meeting",
                    subject=f"Appointment: {appointment.title}"[:300],
                    body=payload.outcome,
                    occurred_at=appointment.start_at,
                    activity_metadata={"appointment_id": str(appointment.id)},
                )
            )
    elif payload.status == "cancelled":
        appointment.cancel_reason = payload.cancel_reason
        if payload.notify_customer and appointment.start_at > utcnow():
            emailed = await _email_customer(db, appointment, "cancelled")
    else:
        appointment.outcome = payload.outcome
    record_audit(
        db,
        organization_id=appointment.organization_id,
        action="status",
        entity_type="appointment",
        entity_id=appointment.id,
        entity_label=f"APT-{appointment.number}",
        changes={"status": payload.status},
    )
    await db.commit()
    data = await appointment_out(db, appointment)
    data["customer_emailed"] = emailed
    return data


@router.delete("/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("services"))])
async def delete_appointment(
    appointment_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.delete")),
    db: AsyncSession = Depends(get_db),
):
    appointment = await _owned(db, Appointment, appointment_id, principal.organization_id, "Appointment")
    await db.delete(appointment)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def send_appointment_reminders(db: AsyncSession) -> int:
    """Remind staff an hour before each appointment (called by the scheduler)."""

    now = utcnow()
    rows = (
        await db.execute(
            select(Appointment).where(
                Appointment.status == "scheduled",
                Appointment.reminder_sent_at.is_(None),
                Appointment.start_at > now,
                Appointment.start_at <= now + timedelta(hours=1),
                Appointment.staff_user_id.is_not(None),
            ).limit(200)
        )
    ).scalars().all()
    for appointment in rows:
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=appointment.organization_id,
                user_id=appointment.staff_user_id,
                kind="reminder",
                title=f"Coming up: {appointment.title}"[:200],
                body=f"Starts at {appointment.start_at.astimezone(timezone.utc):%H:%M} UTC.",
                link=f"/appointments?open={appointment.id}",
                is_read=False,
            )
        )
        appointment.reminder_sent_at = now
    if rows:
        await db.commit()
    return len(rows)


# ===========================================================================
# Projects
# ===========================================================================

PROJECT_STATUSES = ("planning", "active", "on_hold", "completed", "cancelled")


class MilestoneInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    due_date: date | None = None


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    status: str = Field(default="planning", pattern="^(planning|active|on_hold|completed|cancelled)$")
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    account_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    member_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    start_date: date | None = None
    due_date: date | None = None
    budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    milestones: list[MilestoneInput] = Field(default_factory=list, max_length=50)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, pattern="^(planning|active|on_hold|completed|cancelled)$")
    priority: str | None = Field(default=None, pattern="^(low|medium|high|critical)$")
    account_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    member_user_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    start_date: date | None = None
    due_date: date | None = None
    budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class MilestoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: date | None = None
    status: str | None = Field(default=None, pattern="^(open|completed)$")
    position: int | None = Field(default=None, ge=0, le=1000)


class ProjectTaskPayload(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    assigned_to_user_id: uuid.UUID | None = None
    due_at: datetime | None = None
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    milestone_id: uuid.UUID | None = None


class ProjectTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    assigned_to_user_id: uuid.UUID | None = None
    due_at: datetime | None = None
    priority: str | None = Field(default=None, pattern="^(low|medium|high|urgent)$")
    milestone_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern="^(open|in_progress|completed|cancelled)$")


def _check_dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The due date must be on or after the start date.")


async def _project_stats(db: AsyncSession, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not project_ids:
        return {}
    rows = (
        await db.execute(
            select(Task.project_id, Task.status, func.count()).where(Task.project_id.in_(project_ids)).group_by(Task.project_id, Task.status)
        )
    ).all()
    overdue = dict(
        (
            await db.execute(
                select(Task.project_id, func.count())
                .where(Task.project_id.in_(project_ids), Task.status.in_(("open", "in_progress")), Task.due_at < utcnow())
                .group_by(Task.project_id)
            )
        ).all()
    )
    milestones = (
        await db.execute(
            select(ProjectMilestone.project_id, ProjectMilestone.status, func.count())
            .where(ProjectMilestone.project_id.in_(project_ids))
            .group_by(ProjectMilestone.project_id, ProjectMilestone.status)
        )
    ).all()
    stats = {pid: {"tasks_total": 0, "tasks_done": 0, "tasks_overdue": overdue.get(pid, 0), "milestones_total": 0, "milestones_done": 0} for pid in project_ids}
    for pid, task_status, count in rows:
        if task_status == "cancelled":
            continue
        stats[pid]["tasks_total"] += count
        if task_status == "completed":
            stats[pid]["tasks_done"] += count
    for pid, milestone_status, count in milestones:
        stats[pid]["milestones_total"] += count
        if milestone_status == "completed":
            stats[pid]["milestones_done"] += count
    for entry in stats.values():
        entry["progress"] = round(entry["tasks_done"] / entry["tasks_total"] * 100) if entry["tasks_total"] else 0
    return stats


async def project_out(db: AsyncSession, project: Project, stats: dict | None = None) -> dict[str, Any]:
    org = project.organization_id
    users = await names_for(db, org, "user", [project.owner_user_id, *project.member_user_ids])
    accounts = await names_for(db, org, "account", [project.account_id])
    opps = await names_for(db, org, "opportunity", [project.opportunity_id])
    late = project.status not in {"completed", "cancelled"} and project.due_date is not None and project.due_date < date.today()
    data = {
        "id": str(project.id),
        "number": project.number,
        "display_number": f"PRJ-{project.number}",
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "priority": project.priority,
        "is_late": late,
        "account_id": _id(project.account_id),
        "account_name": accounts.get(str(project.account_id)),
        "opportunity_id": _id(project.opportunity_id),
        "opportunity_name": opps.get(str(project.opportunity_id)),
        "owner_user_id": _id(project.owner_user_id),
        "owner_name": users.get(str(project.owner_user_id)),
        "members": [{"id": uid, "name": users.get(uid, "Former member")} for uid in project.member_user_ids or []],
        "member_user_ids": project.member_user_ids or [],
        "start_date": project.start_date,
        "due_date": project.due_date,
        "budget": float(project.budget) if project.budget is not None else None,
        "currency": project.currency,
        "completed_at": project.completed_at,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
    data.update(stats or (await _project_stats(db, [project.id])).get(project.id, {}))
    return data


async def _next_project_number(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return int((await db.execute(select(func.coalesce(func.max(Project.number), 100)).where(Project.organization_id == organization_id))).scalar_one()) + 1


@router.get("/projects", dependencies=[Depends(require_feature("projects"))])
async def list_projects(
    status_filter: str | None = Query(default=None, alias="status"),
    account_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, max_length=200),
    mine: bool = False,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Project).where(Project.organization_id == principal.organization_id)
    if status_filter == "open":
        query = query.where(Project.status.in_(("planning", "active", "on_hold")))
    elif status_filter in PROJECT_STATUSES:
        query = query.where(Project.status == status_filter)
    if account_id:
        query = query.where(Project.account_id == account_id)
    if opportunity_id:
        query = query.where(Project.opportunity_id == opportunity_id)
    if search:
        term = search.strip()
        conditions = [Project.name.ilike(f"%{term}%")]
        number = term.lower().removeprefix("prj-")
        if number.isdigit():
            conditions.append(Project.number == int(number))
        query = query.where(or_(*conditions))
    projects = (await db.execute(query.order_by(Project.number.desc()).limit(500))).scalars().all()
    if mine:
        me = str(principal.user_id)
        projects = [p for p in projects if str(p.owner_user_id) == me or me in (p.member_user_ids or [])]
    stats = await _project_stats(db, [p.id for p in projects])
    every = (await db.execute(select(Project.status, Project.due_date).where(Project.organization_id == principal.organization_id))).all()
    today = date.today()
    return {
        "items": [await project_out(db, p, stats.get(p.id)) for p in projects],
        "summary": {
            "active": len([1 for s, _ in every if s == "active"]),
            "planning": len([1 for s, _ in every if s == "planning"]),
            "late": len([1 for s, d in every if s not in {"completed", "cancelled"} and d and d < today]),
            "completed": len([1 for s, _ in every if s == "completed"]),
        },
        "can_manage": principal.can("projects.manage"),
    }


async def _create_project(db: AsyncSession, principal: Principal, payload: ProjectPayload) -> Project:
    org = principal.organization_id
    data = payload.model_dump(exclude={"milestones", "member_user_ids"})
    await _check_links(db, org, data)
    _check_dates(payload.start_date, payload.due_date)
    owner = payload.owner_user_id or principal.user_id
    await _check_members(db, org, [owner])
    members = await _check_members(db, org, payload.member_user_ids)
    if data.get("opportunity_id") and not data.get("account_id"):
        data["account_id"] = (await db.get(Opportunity, data["opportunity_id"])).account_id
    project = Project(
        id=uuid.uuid4(),
        organization_id=org,
        number=await _next_project_number(db, org),
        **{**data, "name": data["name"].strip(), "owner_user_id": owner, "currency": data["currency"].upper(), "budget": money(data["budget"]) if data["budget"] is not None else None},
        member_user_ids=members,
        completed_at=utcnow() if payload.status == "completed" else None,
    )
    db.add(project)
    await db.flush()
    for position, milestone in enumerate(payload.milestones):
        db.add(
            ProjectMilestone(
                id=uuid.uuid4(), organization_id=org, project_id=project.id, name=milestone.name.strip(), due_date=milestone.due_date, position=position
            )
        )
    for user_id in {str(owner), *members} - {str(principal.user_id)}:
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=org,
                user_id=uuid.UUID(user_id),
                kind="project",
                title=f"Added to project: {project.name}"[:200],
                body=None,
                link=f"/projects/{project.id}",
                is_read=False,
            )
        )
    return project


@router.post("/projects", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("projects"))])
async def create_project(
    payload: ProjectPayload,
    principal: Principal = Depends(require_permission("projects.manage")),
    db: AsyncSession = Depends(get_db),
):
    project = await _create_project(db, principal, payload)
    await db.commit()
    return await project_out(db, project)


@router.post("/projects/from-opportunity/{opportunity_id}", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("projects"))])
async def project_from_opportunity(
    opportunity_id: uuid.UUID,
    principal: Principal = Depends(require_permission("projects.manage")),
    db: AsyncSession = Depends(get_db),
):
    opportunity = await _owned(db, Opportunity, opportunity_id, principal.organization_id, "Deal")
    existing = (
        await db.execute(select(Project).where(Project.opportunity_id == opportunity.id, Project.status != "cancelled"))
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This deal already has a project: PRJ-{existing.number}.")
    owner = opportunity.owner_user_id if opportunity.owner_user_id and str(opportunity.owner_user_id) in await _active_member_ids(db, principal.organization_id) else principal.user_id
    payload = ProjectPayload(
        name=f"{opportunity.name} — delivery"[:200],
        description=opportunity.description,
        status="planning",
        account_id=opportunity.account_id,
        opportunity_id=opportunity.id,
        owner_user_id=owner,
        start_date=date.today(),
        due_date=date.today() + timedelta(days=60),
        budget=opportunity.amount,
        currency=opportunity.currency or "USD",
        milestones=[MilestoneInput(name="Kickoff", due_date=date.today() + timedelta(days=7)), MilestoneInput(name="Go-live", due_date=date.today() + timedelta(days=60))],
    )
    project = await _create_project(db, principal, payload)
    await db.commit()
    return await project_out(db, project)


async def _project(db: AsyncSession, principal: Principal, project_id: uuid.UUID) -> Project:
    return await _owned(db, Project, project_id, principal.organization_id, "Project")


async def project_task_out(db: AsyncSession, task: Task) -> dict[str, Any]:
    users = await names_for(db, task.organization_id, "user", [task.assigned_to_user_id])
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "assigned_to_user_id": _id(task.assigned_to_user_id),
        "assignee_name": users.get(str(task.assigned_to_user_id)),
        "due_at": task.due_at,
        "completed_at": task.completed_at,
        "milestone_id": _id(task.project_milestone_id),
        "is_overdue": task.status in {"open", "in_progress"} and task.due_at is not None and task.due_at < utcnow(),
        "created_at": task.created_at,
    }


@router.get("/projects/{project_id}", dependencies=[Depends(require_feature("projects"))])
async def get_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    data = await project_out(db, project)
    milestones = (
        await db.execute(select(ProjectMilestone).where(ProjectMilestone.project_id == project.id).order_by(ProjectMilestone.position, ProjectMilestone.due_date))
    ).scalars().all()
    tasks = (
        await db.execute(select(Task).where(Task.project_id == project.id).order_by(Task.status == "completed", Task.due_at.nulls_last(), Task.created_at))
    ).scalars().all()
    task_rows = [await project_task_out(db, t) for t in tasks]
    data["milestones"] = [
        {
            "id": str(m.id),
            "name": m.name,
            "due_date": m.due_date,
            "status": m.status,
            "position": m.position,
            "completed_at": m.completed_at,
            "is_late": m.status == "open" and m.due_date is not None and m.due_date < date.today(),
            "tasks_total": len([t for t in task_rows if t["milestone_id"] == str(m.id) and t["status"] != "cancelled"]),
            "tasks_done": len([t for t in task_rows if t["milestone_id"] == str(m.id) and t["status"] == "completed"]),
        }
        for m in milestones
    ]
    data["tasks"] = task_rows
    me = str(principal.user_id)
    data["can_manage"] = principal.can("projects.manage")
    data["can_work"] = principal.can("records.write") and (principal.can("projects.manage") or me == data["owner_user_id"] or me in data["member_user_ids"])
    return data


@router.patch("/projects/{project_id}", dependencies=[Depends(require_feature("projects"))])
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    principal: Principal = Depends(require_permission("projects.manage")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    project = await _project(db, principal, project_id)
    changes = payload.model_dump(exclude_unset=True)
    await _check_links(db, org, changes)
    if changes.get("owner_user_id"):
        await _check_members(db, org, [changes["owner_user_id"]])
    if changes.get("member_user_ids") is not None:
        before = set(project.member_user_ids or [])
        changes["member_user_ids"] = await _check_members(db, org, changes["member_user_ids"])
        for user_id in set(changes["member_user_ids"]) - before - {str(principal.user_id)}:
            db.add(
                Notification(
                    id=uuid.uuid4(), organization_id=org, user_id=uuid.UUID(user_id), kind="project",
                    title=f"Added to project: {project.name}"[:200], body=None, link=f"/projects/{project.id}", is_read=False,
                )
            )
    previous = project.status
    for key, value in changes.items():
        if value is None and key in {"name", "status", "priority", "owner_user_id", "member_user_ids", "currency"}:
            continue
        if key == "currency":
            value = value.upper()
        if key == "budget" and value is not None:
            value = money(value)
        setattr(project, key, value.strip() if key == "name" else value)
    _check_dates(project.start_date, project.due_date)
    if project.status == "completed" and previous != "completed":
        project.completed_at = utcnow()
    elif project.status != "completed":
        project.completed_at = None
    await db.commit()
    return await project_out(db, project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("projects"))])
async def delete_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_permission("projects.manage", "records.delete")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    record_audit(db, organization_id=project.organization_id, action="delete", entity_type="project", entity_id=project.id, entity_label=f"PRJ-{project.number} {project.name}")
    await db.delete(project)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/milestones", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("projects"))])
async def add_milestone(
    project_id: uuid.UUID,
    payload: MilestoneInput,
    principal: Principal = Depends(require_permission("projects.manage")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    position = (await db.execute(select(func.coalesce(func.max(ProjectMilestone.position), -1)).where(ProjectMilestone.project_id == project.id))).scalar_one() + 1
    milestone = ProjectMilestone(
        id=uuid.uuid4(), organization_id=project.organization_id, project_id=project.id, name=payload.name.strip(), due_date=payload.due_date, position=position
    )
    db.add(milestone)
    await db.commit()
    return {"id": str(milestone.id), "name": milestone.name, "due_date": milestone.due_date, "status": milestone.status, "position": milestone.position}


async def _milestone(db: AsyncSession, project: Project, milestone_id: uuid.UUID) -> ProjectMilestone:
    milestone = await db.get(ProjectMilestone, milestone_id)
    if milestone is None or milestone.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Milestone not found.")
    return milestone


@router.patch("/projects/{project_id}/milestones/{milestone_id}", dependencies=[Depends(require_feature("projects"))])
async def update_milestone(
    project_id: uuid.UUID,
    milestone_id: uuid.UUID,
    payload: MilestoneUpdate,
    principal: Principal = Depends(require_permission("projects.manage")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    milestone = await _milestone(db, project, milestone_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None and key in {"name", "status", "position"}:
            continue
        setattr(milestone, key, value.strip() if key == "name" else value)
    milestone.completed_at = utcnow() if milestone.status == "completed" and milestone.completed_at is None else (milestone.completed_at if milestone.status == "completed" else None)
    await db.commit()
    return {"id": str(milestone.id), "name": milestone.name, "due_date": milestone.due_date, "status": milestone.status, "position": milestone.position}


@router.delete("/projects/{project_id}/milestones/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("projects"))])
async def delete_milestone(
    project_id: uuid.UUID,
    milestone_id: uuid.UUID,
    principal: Principal = Depends(require_permission("projects.manage")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    milestone = await _milestone(db, project, milestone_id)
    await db.delete(milestone)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _ensure_worker(principal: Principal, project: Project) -> None:
    me = str(principal.user_id)
    if not (principal.can("projects.manage") or me == str(project.owner_user_id) or me in (project.member_user_ids or [])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the project's team can change its tasks.")


async def _check_task_refs(db: AsyncSession, project: Project, assigned: Any, milestone_id: Any) -> None:
    if assigned:
        await _check_members(db, project.organization_id, [assigned])
    if milestone_id:
        await _milestone(db, project, milestone_id)


def _assignment_notice(db: AsyncSession, project: Project, task: Task, actor: uuid.UUID) -> None:
    if task.assigned_to_user_id and task.assigned_to_user_id != actor:
        db.add(
            Notification(
                id=uuid.uuid4(), organization_id=project.organization_id, user_id=task.assigned_to_user_id, kind="task",
                title=f"Project task: {task.title}"[:200], body=f"In {project.name}.", link=f"/projects/{project.id}", is_read=False,
            )
        )


@router.post("/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("projects"))])
async def add_project_task(
    project_id: uuid.UUID,
    payload: ProjectTaskPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    _ensure_worker(principal, project)
    if project.status in {"completed", "cancelled"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This project is {project.status}. Reopen it to add tasks.")
    await _check_task_refs(db, project, payload.assigned_to_user_id, payload.milestone_id)
    task = Task(
        id=uuid.uuid4(),
        organization_id=project.organization_id,
        project_id=project.id,
        project_milestone_id=payload.milestone_id,
        account_id=project.account_id,
        opportunity_id=project.opportunity_id,
        assigned_to_user_id=payload.assigned_to_user_id or principal.user_id,
        created_by_user_id=principal.user_id,
        title=payload.title.strip(),
        description=payload.description,
        task_type="general",
        status="open",
        priority=payload.priority,
        source="project",
        due_at=payload.due_at,
        tags=[],
        task_metadata={},
    )
    db.add(task)
    _assignment_notice(db, project, task, principal.user_id)
    await db.commit()
    return await project_task_out(db, task)


@router.patch("/projects/{project_id}/tasks/{task_id}", dependencies=[Depends(require_feature("projects"))])
async def update_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: ProjectTaskUpdate,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    _ensure_worker(principal, project)
    task = await db.get(Task, task_id)
    if task is None or task.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found.")
    changes = payload.model_dump(exclude_unset=True)
    await _check_task_refs(db, project, changes.get("assigned_to_user_id"), changes.get("milestone_id"))
    reassigned = "assigned_to_user_id" in changes and changes["assigned_to_user_id"] != task.assigned_to_user_id
    for key, value in changes.items():
        if value is None and key in {"title", "priority", "status"}:
            continue
        if key == "milestone_id":
            task.project_milestone_id = value
            continue
        setattr(task, key, value.strip() if key == "title" else value)
    if task.status == "completed" and task.completed_at is None:
        task.completed_at = utcnow()
    elif task.status != "completed":
        task.completed_at = None
    if reassigned:
        _assignment_notice(db, project, task, principal.user_id)
    await db.commit()
    return await project_task_out(db, task)


@router.delete("/projects/{project_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("projects"))])
async def delete_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    project = await _project(db, principal, project_id)
    _ensure_worker(principal, project)
    task = await db.get(Task, task_id)
    if task is None or task.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found.")
    await db.delete(task)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
