"""Meetings & conversations: recorded or transcribed calls and meetings,
keyword trackers, coaching scorecards and AI field prompts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_dict, _json_list, _user_fk


def _fk(table: str):
    return mapped_column(ForeignKey(f"{table}.id", ondelete="SET NULL"), nullable=True, index=True)


class Conversation(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # transcript_upload | audio_upload | manual | call
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="transcript_upload", server_default="transcript_upload")
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="external", server_default="external")  # external | internal
    meeting_id: Mapped[uuid.UUID | None] = _fk("meetings")
    opportunity_id: Mapped[uuid.UUID | None] = _fk("opportunities")
    account_id: Mapped[uuid.UUID | None] = _fk("accounts")
    contact_id: Mapped[uuid.UUID | None] = _fk("contacts")
    lead_id: Mapped[uuid.UUID | None] = _fk("leads")
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = _int()
    participants: Mapped[list[dict]] = _json_list()
    # [{"speaker": "Maya Chen", "start": 12.4, "end": 18.0, "text": "..."}]
    transcript: Mapped[list[dict]] = _json_list()
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    action_items: Mapped[list[str]] = _json_list()
    tracker_hits: Mapped[dict] = _json_dict()
    field_suggestions: Mapped[list[dict]] = _json_list()
    talk_time: Mapped[dict] = _json_dict()
    # processing | ready | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready", server_default="ready")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    recording_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recording_size: Mapped[int] = _int()
    recording: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationTracker(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "conversation_trackers"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    keywords: Mapped[list[str]] = _json_list()
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="blue", server_default="blue")
    is_active: Mapped[bool] = _bool(True)


class Scorecard(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "scorecards"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"id": "q1", "text": "Did the rep confirm budget?", "kind": "yes_no" | "rating", "weight": 1}]
    questions: Mapped[list[dict]] = _json_list()
    is_active: Mapped[bool] = _bool(True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class ScorecardReview(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "scorecard_reviews"

    scorecard_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scorecards.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_user_id: Mapped[uuid.UUID | None] = _user_fk()
    rep_user_id: Mapped[uuid.UUID | None] = _user_fk()
    answers: Mapped[dict] = _json_dict()
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class FieldPrompt(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "field_prompts"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)  # opportunity | account | contact
    field_key: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = _json_list()
    # suggest: someone approves; auto: written straight to the record
    apply_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="suggest", server_default="suggest")
    is_active: Mapped[bool] = _bool(True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


__all__ = ["Conversation", "ConversationTracker", "Scorecard", "ScorecardReview", "FieldPrompt"]
