"""platform expansion: identity, security, sales, service, analytics, automation

Revision ID: 0011_platform_expansion
Revises: 0010_opps_enterprise
Create Date: 2026-10-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011_platform_expansion"
down_revision: Union[str, Sequence[str], None] = "0010_opps_enterprise"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def _base_columns(org_owned: bool = True) -> list[sa.Column]:
    columns = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]
    if org_owned:
        columns.append(
            sa.Column(
                "organization_id",
                sa.Uuid(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            )
        )
    return columns


def _user_fk(name: str, nullable: bool = True, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey("users.id", ondelete=ondelete), nullable=nullable, index=True)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Existing identity tables
    # ------------------------------------------------------------------
    op.add_column("users", sa.Column("job_title", sa.String(150), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("timezone", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("mfa_secret", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))

    op.add_column("organizations", sa.Column("settings", JSONB, server_default="{}", nullable=False))

    op.create_table(
        "teams",
        *_base_columns(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _user_fk("manager_user_id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),
    )

    op.add_column(
        "organization_memberships",
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
    )
    op.add_column(
        "organization_memberships",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index(
        "ix_organization_memberships_team_id", "organization_memberships", ["team_id"]
    )
    op.add_column(
        "organization_memberships",
        sa.Column(
            "invited_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )

    op.create_table(
        "roles",
        *_base_columns(),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permissions", JSONB, server_default="[]", nullable=False),
        sa.UniqueConstraint("organization_id", "key", name="uq_roles_org_key"),
    )

    op.create_table(
        "auth_tokens",
        *_base_columns(org_owned=False),
        _user_fk("user_id", nullable=False, ondelete="CASCADE"),
        sa.Column(
            "organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True, index=True,
        ),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _user_fk("created_by_user_id"),
    )

    op.create_table(
        "user_sessions",
        *_base_columns(org_owned=False),
        _user_fk("user_id", nullable=False, ondelete="CASCADE"),
        sa.Column(
            "organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_logs",
        *_base_columns(),
        _user_fk("actor_user_id"),
        sa.Column("action", sa.String(40), nullable=False, index=True),
        sa.Column("entity_type", sa.String(50), nullable=False, index=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("entity_label", sa.String(300), nullable=True),
        sa.Column("changes", JSONB, server_default="{}", nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
    )
    op.create_index("ix_audit_logs_org_created", "audit_logs", ["organization_id", "created_at"])

    op.create_table(
        "notifications",
        *_base_columns(),
        _user_fk("user_id", nullable=False, ondelete="CASCADE"),
        sa.Column("kind", sa.String(40), server_default="info", nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(300), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "is_read"])

    # ------------------------------------------------------------------
    # Sales
    # ------------------------------------------------------------------
    op.create_table(
        "products",
        *_base_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sku", sa.String(80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("family", sa.String(80), nullable=True),
        sa.Column("unit_price", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("billing_frequency", sa.String(20), server_default="one_time", nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.UniqueConstraint("organization_id", "sku", name="uq_products_org_sku"),
    )

    op.create_table(
        "quotes",
        *_base_columns(),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column(
            "opportunity_id", sa.Uuid(), sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
            nullable=True, index=True,
        ),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True),
        _user_fk("owner_user_id"),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("subtotal", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("discount_total", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("tax_total", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("total", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "number", name="uq_quotes_org_number"),
    )

    op.create_table(
        "quote_line_items",
        *_base_columns(),
        sa.Column("quote_id", sa.Uuid(), sa.ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 2), server_default="1", nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("discount_percent", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("tax_percent", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), server_default="0", nullable=False),
    )

    op.create_table(
        "lead_scoring_rules",
        *_base_columns(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("field", sa.String(60), nullable=False),
        sa.Column("operator", sa.String(20), nullable=False),
        sa.Column("value", sa.String(200), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
    )

    op.create_table(
        "forecast_quotas",
        *_base_columns(),
        _user_fk("user_id", nullable=False, ondelete="CASCADE"),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", "period", name="uq_forecast_quotas_user_period"),
    )

    # ------------------------------------------------------------------
    # Service desk
    # ------------------------------------------------------------------
    op.create_table(
        "sla_policies",
        *_base_columns(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("first_response_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_sla_policies_org_name"),
    )

    op.create_table(
        "cases",
        *_base_columns(),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(250), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="new", nullable=False),
        sa.Column("priority", sa.String(20), server_default="medium", nullable=False),
        sa.Column("channel", sa.String(20), server_default="email", nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True),
        _user_fk("assigned_to_user_id"),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("sla_policy_id", sa.Uuid(), sa.ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True),
        _user_fk("created_by_user_id"),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("satisfaction_rating", sa.SmallInteger(), nullable=True),
        sa.Column("tags", JSONB, server_default="[]", nullable=False),
        sa.Column("linked_article_ids", JSONB, server_default="[]", nullable=False),
        sa.UniqueConstraint("organization_id", "number", name="uq_cases_org_number"),
        sa.CheckConstraint(
            "satisfaction_rating IS NULL OR (satisfaction_rating BETWEEN 1 AND 5)", name="ck_cases_csat_range"
        ),
    )
    op.create_index("ix_cases_org_status", "cases", ["organization_id", "status"])

    op.create_table(
        "case_comments",
        *_base_columns(),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True),
        _user_fk("author_user_id"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), server_default="false", nullable=False),
    )

    op.create_table(
        "kb_articles",
        *_base_columns(),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("slug", sa.String(260), nullable=False),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("tags", JSONB, server_default="[]", nullable=False),
        _user_fk("author_user_id"),
        sa.Column("view_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("helpful_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("not_helpful_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "slug", name="uq_kb_articles_org_slug"),
    )

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    op.create_table(
        "reports",
        *_base_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("folder", sa.String(80), nullable=True),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("columns", JSONB, server_default="[]", nullable=False),
        sa.Column("filters", JSONB, server_default="[]", nullable=False),
        sa.Column("group_by", sa.String(60), nullable=True),
        sa.Column("date_granularity", sa.String(10), nullable=True),
        sa.Column("aggregate", sa.String(10), server_default="count", nullable=False),
        sa.Column("aggregate_field", sa.String(60), nullable=True),
        sa.Column("chart_type", sa.String(20), server_default="table", nullable=False),
        sa.Column("sort_field", sa.String(60), nullable=True),
        sa.Column("sort_direction", sa.String(4), server_default="desc", nullable=False),
        sa.Column("row_limit", sa.Integer(), server_default="200", nullable=False),
        _user_fk("owner_user_id"),
        sa.Column("is_shared", sa.Boolean(), server_default="true", nullable=False),
    )

    op.create_table(
        "dashboards",
        *_base_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("widgets", JSONB, server_default="[]", nullable=False),
        _user_fk("owner_user_id"),
        sa.Column("is_shared", sa.Boolean(), server_default="true", nullable=False),
    )

    # ------------------------------------------------------------------
    # Automation
    # ------------------------------------------------------------------
    op.create_table(
        "workflow_rules",
        *_base_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("object_type", sa.String(40), nullable=False, index=True),
        sa.Column("trigger_event", sa.String(30), nullable=False),
        sa.Column("trigger_field", sa.String(60), nullable=True),
        sa.Column("conditions", JSONB, server_default="[]", nullable=False),
        sa.Column("actions", JSONB, server_default="[]", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        _user_fk("created_by_user_id"),
    )

    op.create_table(
        "workflow_runs",
        *_base_columns(),
        sa.Column(
            "rule_id", sa.Uuid(), sa.ForeignKey("workflow_rules.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("entity_label", sa.String(300), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    for table in (
        "workflow_runs",
        "workflow_rules",
        "dashboards",
        "reports",
        "kb_articles",
        "case_comments",
        "cases",
        "sla_policies",
        "forecast_quotas",
        "lead_scoring_rules",
        "quote_line_items",
        "quotes",
        "products",
        "notifications",
        "audit_logs",
        "user_sessions",
        "auth_tokens",
        "roles",
    ):
        op.drop_table(table)

    op.drop_column("organization_memberships", "invited_by_user_id")
    op.drop_index("ix_organization_memberships_team_id", table_name="organization_memberships")
    op.drop_column("organization_memberships", "team_id")
    op.drop_column("organization_memberships", "status")
    op.drop_table("teams")
    op.drop_column("organizations", "settings")
    for column in (
        "locked_until",
        "failed_login_count",
        "last_login_at",
        "mfa_enabled",
        "mfa_secret",
        "timezone",
        "phone",
        "job_title",
    ):
        op.drop_column("users", column)
