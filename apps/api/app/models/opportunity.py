from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.db.base import Base
from app.db.mixins import (
    OrganizationOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Opportunity(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "opportunities"

    __table_args__ = (
        CheckConstraint(
            "probability >= 0 AND probability <= 100",
            name="probability_range",
        ),
    )

    name: Mapped[str] = mapped_column(
        String(250),
        nullable=False,
        index=True,
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    primary_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "contacts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "pipelines.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    stage_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "pipeline_stages.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        server_default="USD",
    )

    probability: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    expected_close_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
