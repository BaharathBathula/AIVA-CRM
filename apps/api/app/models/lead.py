from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import (
    OrganizationOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Lead(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "leads"

    __table_args__ = (
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="score_range",
        ),
    )

    first_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    last_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        index=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    company_name: Mapped[str | None] = mapped_column(
        String(250),
        nullable=True,
        index=True,
    )

    job_title: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="manual",
        server_default="manual",
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="new",
        server_default="new",
        index=True,
    )

    score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    converted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    converted_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    converted_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "contacts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    converted_opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
