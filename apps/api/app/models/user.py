from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class User(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
        index=True,
    )

    full_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    password_hash: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    job_title: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    timezone: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Multi-factor authentication (TOTP)
    mfa_secret: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # Sign-in tracking and lockout
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    memberships: Mapped[list["OrganizationMembership"]] = relationship(
        back_populates="user",
        foreign_keys="OrganizationMembership.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
