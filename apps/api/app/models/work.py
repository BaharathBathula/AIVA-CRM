"""Services and appointments, and projects with milestones."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_list, _money, _user_fk, _when


def _fk(table: str, ondelete: str = "SET NULL", nullable: bool = True):
    return mapped_column(ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable, index=True)


# ---------------------------------------------------------------------------
# Services and appointments
# ---------------------------------------------------------------------------


class Service(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_services_org_name"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = _int(60)
    buffer_minutes: Mapped[int] = _int(0)
    price: Mapped[Decimal] = _money()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    # in_person | online | phone | customer_site
    location_type: Mapped[str] = mapped_column(String(20), nullable=False, default="online", server_default="online")
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # People who can deliver this service (user ids as strings).
    staff_user_ids: Mapped[list[str]] = _json_list()
    is_active: Mapped[bool] = _bool(True)


class Appointment(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_appointments_org_number"),
        Index("ix_appointments_org_start", "organization_id", "start_at"),
        Index("ix_appointments_staff_start", "staff_user_id", "start_at"),
    )

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    service_id: Mapped[uuid.UUID | None] = _fk("services")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # scheduled | completed | cancelled | no_show
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled", server_default="scheduled")
    staff_user_id: Mapped[uuid.UUID | None] = _user_fk()
    contact_id: Mapped[uuid.UUID | None] = _fk("contacts")
    account_id: Mapped[uuid.UUID | None] = _fk("accounts")
    lead_id: Mapped[uuid.UUID | None] = _fk("leads")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    price: Mapped[Decimal] = _money()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    reminder_sent_at: Mapped[datetime | None] = _when()
    completed_at: Mapped[datetime | None] = _when()


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class Project(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_projects_org_number"),)

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # planning | active | on_hold | completed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="planning", server_default="planning", index=True)
    # low | medium | high | critical
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium", server_default="medium")
    account_id: Mapped[uuid.UUID | None] = _fk("accounts")
    opportunity_id: Mapped[uuid.UUID | None] = _fk("opportunities")
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    member_user_ids: Mapped[list[str]] = _json_list()
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[Decimal | None] = _money(nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    completed_at: Mapped[datetime | None] = _when()


class ProjectMilestone(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "project_milestones"

    project_id: Mapped[uuid.UUID] = _fk("projects", "CASCADE", nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # open | completed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    position: Mapped[int] = _int(0)
    completed_at: Mapped[datetime | None] = _when()
