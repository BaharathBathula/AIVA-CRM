from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin


class OpportunityStageHistory(
    UUIDPrimaryKeyMixin,
    Base,
):
    __tablename__ = "opportunity_stage_history"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
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

    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "pipeline_stages.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "pipeline_stages.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    change_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )