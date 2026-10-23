"""Social publishing and website visit tracking."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_dict, _json_list, _user_fk, _when

# ---------------------------------------------------------------------------
# Social
# ---------------------------------------------------------------------------


class SocialChannel(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "social_channels"
    __table_args__ = (UniqueConstraint("organization_id", "network", "external_id", name="uq_social_channels_account"),)

    # linkedin | x | facebook | instagram
    network: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Page id, organization/person id or user id on the network.
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    token_expires_at: Mapped[datetime | None] = _when()
    # connected | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="connected", server_default="connected")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = _when()
    connected_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class SocialPost(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "social_posts"
    __table_args__ = (Index("ix_social_posts_org_status_scheduled", "organization_id", "status", "scheduled_at"),)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    channel_ids: Mapped[list[str]] = _json_list()
    # draft | scheduled | publishing | published | partial | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    scheduled_at: Mapped[datetime | None] = _when()
    published_at: Mapped[datetime | None] = _when()
    # {channel_id: {"status", "external_id", "url", "error", "at"}}
    results: Mapped[dict] = _json_dict()
    campaign: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


# ---------------------------------------------------------------------------
# Website visits
# ---------------------------------------------------------------------------


class TrackingSite(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "tracking_sites"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    site_key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = _bool(True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    last_hit_at: Mapped[datetime | None] = _when()


class WebVisitor(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "web_visitors"
    __table_args__ = (
        UniqueConstraint("site_id", "visitor_key", name="uq_web_visitors_key"),
        Index("ix_web_visitors_org_last_seen", "organization_id", "last_seen_at"),
        Index("ix_web_visitors_org_email", "organization_id", "email"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tracking_sites.id", ondelete="CASCADE"), nullable=False, index=True)
    visitor_key: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visit_count: Mapped[int] = _int(0)
    page_view_count: Mapped[int] = _int(0)
    total_seconds: Mapped[int] = _int(0)
    first_referrer: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    first_source: Mapped[dict] = _json_dict()
    last_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    device: Mapped[str | None] = mapped_column(String(20), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(40), nullable=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    identified_at: Mapped[datetime | None] = _when()


class WebSession(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "web_sessions"
    __table_args__ = (UniqueConstraint("visitor_id", "session_key", name="uq_web_sessions_key"),)

    visitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("web_visitors.id", ondelete="CASCADE"), nullable=False, index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tracking_sites.id", ondelete="CASCADE"), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    page_count: Mapped[int] = _int(0)
    duration_seconds: Mapped[int] = _int(0)
    referrer: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(160), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(160), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(160), nullable=True)
    landing_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    exit_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class WebPageView(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "web_page_views"
    __table_args__ = (Index("ix_web_page_views_visitor_viewed", "visitor_id", "viewed_at"),)

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("web_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    visitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("web_visitors.id", ondelete="CASCADE"), nullable=False)
    view_key: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seconds: Mapped[int] = _int(0)


__all__ = ["SocialChannel", "SocialPost", "TrackingSite", "WebVisitor", "WebSession", "WebPageView"]
