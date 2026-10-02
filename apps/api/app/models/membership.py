from __future__ import annotations

import uuid

from sqlalchemy import (
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class OrganizationMembership(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "organization_memberships"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_membership_user",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="member",
        server_default="member",
    )

    organization: Mapped["Organization"] = relationship(
        back_populates="memberships",
    )

    user: Mapped["User"] = relationship(
        back_populates="memberships",
    )
