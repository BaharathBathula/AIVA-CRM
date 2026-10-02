from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
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


class PipelineStage(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "pipeline_stages"

    __table_args__ = (
        UniqueConstraint(
            "pipeline_id",
            "name",
            name="uq_pipeline_stage_name",
        ),
        UniqueConstraint(
            "pipeline_id",
            "position",
            name="uq_pipeline_stage_position",
        ),
        CheckConstraint(
            "probability >= 0 AND probability <= 100",
            name="probability_range",
        ),
    )

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "pipelines.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    probability: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    category: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="open",
        server_default="open",
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    pipeline: Mapped["Pipeline"] = relationship(
        back_populates="stages",
    )
