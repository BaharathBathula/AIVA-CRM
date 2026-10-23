"""Platform models: identity, security, sales, service, analytics and automation.

These extend the CRM foundation with the capabilities expected of an
enterprise CRM: custom roles and teams, refresh sessions and one-time
tokens, an audit trail, products and quotes, lead scoring, quotas, a
service desk with SLAs, a knowledge base, reports and dashboards,
workflow automation and in-app notifications.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import (
    OrganizationOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


def _user_fk(nullable: bool = True, ondelete: str = "SET NULL"):
    return mapped_column(ForeignKey("users.id", ondelete=ondelete), nullable=nullable, index=True)


# ---------------------------------------------------------------------------
# Identity & security
# ---------------------------------------------------------------------------


class Role(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """Custom role. Built-in roles live in ``app.core.permissions``."""

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_roles_org_key"),)

    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class Team(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_user_id: Mapped[uuid.UUID | None] = _user_fk()
    # Teams can nest (Enterprise: multi-team management).
    parent_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )


class AuthToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-use tokens for password reset and invitations (stored hashed)."""

    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A signed-in device. Holds the hashed, rotating refresh token."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sessions started through a workspace's single sign-on stay in that workspace.
    bound_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )


class AuditLog(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_org_created", "organization_id", "created_at"),)

    actor_user_id: Mapped[uuid.UUID | None] = _user_fk()
    action: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    entity_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Notification(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "is_read"),)

    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="info", server_default="info")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


class Product(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("organization_id", "sku", name="uq_products_org_sku"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    family: Mapped[str | None] = mapped_column(String(80), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    billing_frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, default="one_time", server_default="one_time"
    )
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Inventory (Pro).
    track_inventory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    stock_on_hand: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)


class Quote(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "quotes"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_quotes_org_number"),)

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    discount_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    tax_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    line_items: Mapped[list["QuoteLineItem"]] = relationship(
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteLineItem.position",
        lazy="selectin",
    )


class QuoteLineItem(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "quote_line_items"

    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=1, server_default="1")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")

    quote: Mapped[Quote] = relationship(back_populates="line_items")


class LeadScoringRule(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "lead_scoring_rules"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    field: Mapped[str] = mapped_column(String(60), nullable=False)
    operator: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class ForecastQuota(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "forecast_quotas"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "period", name="uq_forecast_quotas_user_period"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")


# ---------------------------------------------------------------------------
# Service desk
# ---------------------------------------------------------------------------


class SlaPolicy(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "sla_policies"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_sla_policies_org_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    first_response_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class Case(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_cases_org_number"),
        Index("ix_cases_org_status", "organization_id", "status"),
    )

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", server_default="new")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium", server_default="medium")
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email", server_default="email")
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = _user_fk()
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    satisfaction_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    linked_article_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    custom_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class CaseComment(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "case_comments"

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id: Mapped[uuid.UUID | None] = _user_fk()
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class KnowledgeArticle(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "kb_articles"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_kb_articles_org_slug"),)

    title: Mapped[str] = mapped_column(String(250), nullable=False)
    slug: Mapped[str] = mapped_column(String(260), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    author_user_id: Mapped[uuid.UUID | None] = _user_fk()
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    not_helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class Report(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "reports"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder: Mapped[str | None] = mapped_column(String(80), nullable=True)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    filters: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    group_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    date_granularity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    aggregate: Mapped[str] = mapped_column(String(10), nullable=False, default="count", server_default="count")
    aggregate_field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    chart_type: Mapped[str] = mapped_column(String(20), nullable=False, default="table", server_default="table")
    sort_field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    sort_direction: Mapped[str] = mapped_column(String(4), nullable=False, default="desc", server_default="desc")
    row_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=200, server_default="200")
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class Dashboard(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "dashboards"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    widgets: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


# ---------------------------------------------------------------------------
# Automation
# ---------------------------------------------------------------------------


class WorkflowRule(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "workflow_rules"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    trigger_event: Mapped[str] = mapped_column(String(30), nullable=False)
    trigger_field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    conditions: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    actions: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class WorkflowRun(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    entity_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
