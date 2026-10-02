cat > apps/api/app/models/email_message.py <<'PY'
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import (
    OrganizationOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class EmailMessage(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "email_messages"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider",
            "external_message_id",
            name="uq_email_messages_org_provider_external",
        ),
        Index(
            "ix_email_messages_org_occurred_at",
            "organization_id",
            "occurred_at",
        ),
        Index(
            "ix_email_messages_thread_occurred_at",
            "thread_id",
            "occurred_at",
        ),
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "email_threads.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    external_message_id: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        index=True,
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

    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    direction: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    subject: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    from_address: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    from_name: Mapped[str | None] = mapped_column(
        String(250),
        nullable=True,
    )

    to_recipients: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    cc_recipients: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    bcc_recipients: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    reply_to: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )

    body_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    body_html: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    snippet: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    is_draft: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    has_attachments: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    attachments: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    internet_message_id: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        index=True,
    )

    in_reply_to: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    references: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    message_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
PY
