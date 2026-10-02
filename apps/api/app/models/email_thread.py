from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class EmailThread(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "email_threads"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider",
            "external_thread_id",
            name=(
                "uq_email_threads_org_provider_external"
            ),
        ),
        Index(
            "ix_email_threads_org_last_message_at",
            "organization_id",
            "last_message_at",
        ),
    )

    provider: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    external_thread_id: Mapped[
        str | None
    ] = mapped_column(
        String(500),
        nullable=True,
        index=True,
    )

    subject: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    snippet: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    account_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    contact_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        ForeignKey(
            "contacts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    opportunity_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    participants: Mapped[
        list[dict]
    ] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    last_message_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
