from __future__ import annotations

from sqlalchemy import (
    Boolean,
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


class Pipeline(
    UUIDPrimaryKeyMixin,
    OrganizationOwnedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "pipelines"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "name",
            name="uq_pipeline_org_name",
        ),
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    stages: Mapped[list["PipelineStage"]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStage.position",
    )
