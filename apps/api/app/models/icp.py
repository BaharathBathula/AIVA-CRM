"""Ideal customer profile: personas, buying-intent topics and signals."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_dict, _json_list, _user_fk, _when


class Persona(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "personas"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_personas_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="blue", server_default="blue")
    title_keywords: Mapped[list[str]] = _json_list()
    exclude_keywords: Mapped[list[str]] = _json_list()
    seniorities: Mapped[list[str]] = _json_list()
    departments: Mapped[list[str]] = _json_list()
    is_active: Mapped[bool] = _bool(True)
    position: Mapped[int] = _int()


class IntentTopic(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "intent_topics"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_intent_topics_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str]] = _json_list()
    url_patterns: Mapped[list[str]] = _json_list()
    weight: Mapped[int] = _int(1)
    is_active: Mapped[bool] = _bool(True)


class SignalRule(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "signal_rules"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # page_visit | intent_surge | email_engagement | form_submitted | deal_stalled | job_change | sequence_reply | persona_match
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    config: Mapped[dict] = _json_dict()
    notify_owner: Mapped[bool] = _bool(True)
    create_task: Mapped[bool] = _bool()
    is_active: Mapped[bool] = _bool(True)
    last_evaluated_at: Mapped[datetime | None] = _when()
    fire_count: Mapped[int] = _int()
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class SignalEvent(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "signal_events"
    __table_args__ = (UniqueConstraint("rule_id", "dedupe_key", name="uq_signal_events_dedupe"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signal_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(20), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    record_label: Mapped[str] = mapped_column(String(300), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # new | actioned | dismissed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", server_default="new", index=True)
    actioned_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    actioned_at: Mapped[datetime | None] = _when()


__all__ = ["Persona", "IntentTopic", "SignalRule", "SignalEvent"]
