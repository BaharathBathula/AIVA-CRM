"""Sign-in identities from Google, Microsoft, Apple and workspace single sign-on."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class UserIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An outside account a user signs in with.

    ``provider`` is ``google``, ``microsoft``, ``apple`` or ``sso:<connection id>``;
    ``subject`` is that provider's stable id for the person.
    """

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SsoConnection(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """A workspace's own identity provider (OpenID Connect): Okta, Entra ID, Google Workspace, …"""

    __tablename__ = "sso_connections"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_sso_connections_organization"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    client_id: Mapped[str] = mapped_column(String(300), nullable=False)
    client_secret: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    domains: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    default_role: Mapped[str] = mapped_column(String(60), nullable=False, default="member", server_default="member")
    auto_provision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
