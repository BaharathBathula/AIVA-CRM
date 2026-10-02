from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class Activity(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "activities"

    __table_args__ = (
        Index(
            "ix_activities_organization_occurred_at",
            "organization_id",
            "occurred_at",
        ),
    )

    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "contacts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    activity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    subject: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    direction: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    external_id: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    activity_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    account: Mapped["Account | None"] = relationship(
        back_populates="activities",
    )

    contact: Mapped["Contact | None"] = relationship(
        back_populates="activities",
    )
