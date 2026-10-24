"""Workspace administration: settings namespaces, add-ons, credits, AI word
metering, data and removal requests, territories, support access, MCP call
log, global picklists, goals and the do-not-contact (suppression) list.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_dict, _json_list, _user_fk, _when


class WorkspaceSetting(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """One settings namespace (e.g. ``security``, ``dialer``) for a workspace.

    Kept out of ``organizations.settings`` because that column is readable by
    every member; ``secrets`` holds encrypted values that are never returned.
    """

    __tablename__ = "workspace_settings"
    __table_args__ = (UniqueConstraint("organization_id", "namespace", name="uq_workspace_settings_namespace"),)

    namespace: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[dict] = _json_dict()
    secrets: Mapped[dict] = _json_dict()
    updated_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class OrgAddon(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "org_addons"
    __table_args__ = (UniqueConstraint("organization_id", "addon_key", name="uq_org_addons_key"),)

    addon_key: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[int] = _int(1)
    # active | cancelled (stays on until renews_at) | ended
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime | None] = _when()
    renews_at: Mapped[datetime | None] = _when()
    cancelled_at: Mapped[datetime | None] = _when()
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="demo", server_default="demo")
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    purchased_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class CreditTransaction(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "credit_transactions"
    __table_args__ = (Index("ix_credit_transactions_org_period", "organization_id", "period"),)

    user_id: Mapped[uuid.UUID | None] = _user_fk()
    # usage (negative) | refund | adjustment
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    record_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    record_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM


class AiUsageEvent(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (Index("ix_ai_usage_events_org_period", "organization_id", "period"),)

    user_id: Mapped[uuid.UUID | None] = _user_fk()
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    words: Mapped[int] = _int()
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="rules", server_default="rules")
    period: Mapped[str] = mapped_column(String(7), nullable=False)


class DataRequest(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """Someone asked for missing contact data (email, phone…) on a record."""

    __tablename__ = "data_requests"

    record_type: Mapped[str] = mapped_column(String(20), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    record_label: Mapped[str] = mapped_column(String(300), nullable=False)
    fields: Mapped[list[str]] = _json_list()
    # open | fulfilled | not_found | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    assigned_to_user_id: Mapped[uuid.UUID | None] = _user_fk()
    fulfilled_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    fulfilled_at: Mapped[datetime | None] = _when()
    result: Mapped[dict] = _json_dict()
    credits_charged: Mapped[int] = _int()
    due_at: Mapped[datetime | None] = _when()


class Territory(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "territories"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_territories_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="blue", server_default="blue")
    match: Mapped[str] = mapped_column(String(5), nullable=False, default="all", server_default="all")
    # [{"field": "country", "operator": "in", "values": ["USA", "Canada"]}]
    rules: Mapped[list[dict]] = _json_list()
    member_user_ids: Mapped[list[str]] = _json_list()
    # round_robin: new records go to members in turn; territory_only: tag only.
    assignment: Mapped[str] = mapped_column(String(20), nullable=False, default="round_robin", server_default="round_robin")
    applies_to: Mapped[list[str]] = _json_list()
    auto_assign: Mapped[bool] = _bool(True)
    priority: Mapped[int] = _int(100)
    is_active: Mapped[bool] = _bool(True)


class SupportAccessGrant(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "support_access_grants"

    granted_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    # read_only | full
    access_level: Mapped[str] = mapped_column(String(20), nullable=False, default="read_only", server_default="read_only")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = _when()
    revoked_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    last_used_at: Mapped[datetime | None] = _when()
    use_count: Mapped[int] = _int()


class McpCall(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "mcp_calls"

    user_id: Mapped[uuid.UUID | None] = _user_fk()
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(60), nullable=False)
    tool: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = _int()
    client_name: Mapped[str | None] = mapped_column(String(120), nullable=True)


class GlobalPicklist(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "global_picklists"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_global_picklists_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"value": "Gold", "color": "amber", "is_active": true}]
    values: Mapped[list[dict]] = _json_list()


class Goal(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "goals"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    # week | month | quarter | year
    period: Mapped[str] = mapped_column(String(20), nullable=False, default="month", server_default="month")
    target: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # workspace | team | user
    assignee_type: Mapped[str] = mapped_column(String(20), nullable=False, default="user", server_default="user")
    assignee_ids: Mapped[list[str]] = _json_list()
    is_active: Mapped[bool] = _bool(True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class RemovalRequest(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """A person asked to have their personal data erased (GDPR / CCPA)."""

    __tablename__ = "removal_requests"

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    regulation: Mapped[str] = mapped_column(String(20), nullable=False, default="gdpr", server_default="gdpr")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="email", server_default="email")
    # pending | completed | rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict] = _json_dict()
    completed_at: Mapped[datetime | None] = _when()
    completed_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class SuppressionEntry(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """Never email (or re-import) this address or domain."""

    __tablename__ = "suppression_entries"
    __table_args__ = (UniqueConstraint("organization_id", "kind", "value", name="uq_suppression_entries_value"),)

    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # email | domain
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    # unsubscribed | bounced | removal_request | manual | complaint
    reason: Mapped[str] = mapped_column(String(30), nullable=False, default="manual", server_default="manual")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


__all__ = [
    "WorkspaceSetting",
    "OrgAddon",
    "CreditTransaction",
    "AiUsageEvent",
    "DataRequest",
    "Territory",
    "SupportAccessGrant",
    "McpCall",
    "GlobalPicklist",
    "Goal",
    "RemovalRequest",
    "SuppressionEntry",
]
