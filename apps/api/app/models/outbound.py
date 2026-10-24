"""Outbound: sending domains, snippets and sales sequences.

A sequence is an ordered set of steps (automatic emails, manual emails,
calls, LinkedIn touches and generic tasks) with delays between them.
People (contacts or leads) are enrolled; the scheduler sends automatic
emails from the enrolling user's mailbox and creates tasks for manual
steps. An enrollment stops when the person replies, bounces, unsubscribes,
books a meeting or finishes the last step.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_dict, _user_fk, _when


class SendingDomain(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "sending_domains"
    __table_args__ = (UniqueConstraint("organization_id", "domain", name="uq_sending_domains_domain"),)

    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    # pending | verified | issues
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    dkim_selector: Mapped[str | None] = mapped_column(String(63), nullable=True)
    tracking_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checks: Mapped[dict] = _json_dict()
    last_checked_at: Mapped[datetime | None] = _when()
    is_default: Mapped[bool] = _bool()
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class Snippet(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "snippets"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    shortcut: Mapped[str | None] = mapped_column(String(40), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_shared: Mapped[bool] = _bool(True)
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    usage_count: Mapped[int] = _int()


class Sequence(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "sequences"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # draft | active | paused | archived
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft", index=True)
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    is_shared: Mapped[bool] = _bool(True)
    # schedule (days, start/end hour, timezone), stop rules, finished stage…
    settings: Mapped[dict] = _json_dict()
    archived_at: Mapped[datetime | None] = _when()


class SequenceStep(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "sequence_steps"

    sequence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = _int()
    # auto_email | manual_email | call | linkedin | task
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    delay_days: Mapped[int] = _int()
    delay_hours: Mapped[int] = _int()
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="medium", server_default="medium")
    thread_reply: Mapped[bool] = _bool(True)
    is_active: Mapped[bool] = _bool(True)


class SequenceEnrollment(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "sequence_enrollments"
    __table_args__ = (
        UniqueConstraint("sequence_id", "record_type", "record_id", name="uq_sequence_enrollments_record"),
        Index("ix_sequence_enrollments_due", "status", "next_run_at"),
    )

    sequence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(20), nullable=False)  # contact | lead
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sender_user_id: Mapped[uuid.UUID | None] = _user_fk()
    # active | paused | finished | replied | bounced | unsubscribed | meeting_booked | removed | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    current_position: Mapped[int] = _int()
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrolled_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    finished_at: Mapped[datetime | None] = _when()
    finished_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    thread_message_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    thread_subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SequenceMessage(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """One step carried out for one enrollment: an email sent or a task made."""

    __tablename__ = "sequence_messages"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sequence_enrollments.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sequence_steps.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    to_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # sent | failed | skipped | task_created
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sent_at: Mapped[datetime | None] = _when()
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    opened_at: Mapped[datetime | None] = _when()
    open_count: Mapped[int] = _int()
    clicked_at: Mapped[datetime | None] = _when()
    click_count: Mapped[int] = _int()
    replied_at: Mapped[datetime | None] = _when()
    bounced_at: Mapped[datetime | None] = _when()
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_id_header: Mapped[str | None] = mapped_column(String(300), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    sender_user_id: Mapped[uuid.UUID | None] = _user_fk()


__all__ = ["SendingDomain", "Snippet", "Sequence", "SequenceStep", "SequenceEnrollment", "SequenceMessage"]
