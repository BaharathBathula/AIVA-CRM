from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.db.base import Base
from app.db.mixins import (
    OrganizationOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Account(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(
        String(250),
        nullable=False,
        index=True,
    )

    domain: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    website: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    industry: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    lifecycle_stage: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="prospect",
        server_default="prospect",
        index=True,
    )

    employee_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    annual_revenue: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    billing_address_line1: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    billing_address_line2: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    billing_city: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    billing_state: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    billing_postal_code: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    billing_country: Mapped[str | None] = mapped_column(
        String(120),
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

    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="account",
    )

    activities: Mapped[list["Activity"]] = relationship(
        back_populates="account",
    )
