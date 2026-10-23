"""Models for plans and billing and the Starter, Pro and Enterprise features.

Grouped by area: billing; email and campaigns; calendar; forms; data
import; customization (custom modules, fields, buttons, stage rules,
approvals); integrations and API keys; AI; inventory and invoices;
portals; journeys; teams; account based marketing.
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


def _user_fk(nullable: bool = True, ondelete: str = "SET NULL"):
    return mapped_column(ForeignKey("users.id", ondelete=ondelete), nullable=nullable, index=True)


def _json_list():
    return mapped_column(JSONB, nullable=False, default=list, server_default="[]")


def _json_dict():
    return mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


def _bool(default: bool = False):
    return mapped_column(Boolean, nullable=False, default=default, server_default="true" if default else "false")


def _int(default: int = 0):
    return mapped_column(Integer, nullable=False, default=default, server_default=str(default))


def _money(nullable: bool = False):
    if nullable:
        return mapped_column(Numeric(14, 2), nullable=True)
    return mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")


def _when():
    return mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


class Subscription(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_subscriptions_org"),)

    plan: Mapped[str] = mapped_column(String(20), nullable=False, default="trial", server_default="trial")
    # trialing | active | past_due | canceled | expired
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="trialing", server_default="trialing")
    trial_ends_at: Mapped[datetime | None] = _when()
    current_period_start: Mapped[datetime | None] = _when()
    current_period_end: Mapped[datetime | None] = _when()
    seats: Mapped[int] = _int(1)
    cancel_at_period_end: Mapped[bool] = _bool()
    billing_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # demo (no charge) | stripe | sandbox (inherits the parent's plan)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="demo", server_default="demo")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(120), nullable=True)


class BillingInvoice(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_billing_invoices_number"),)

    number: Mapped[str] = mapped_column(String(30), nullable=False)
    plan: Mapped[str] = mapped_column(String(20), nullable=False)
    seats: Mapped[int] = _int(1)
    unit_price: Mapped[Decimal] = _money()
    amount: Mapped[Decimal] = _money()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    # paid | open | void
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="paid", server_default="paid")
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    period_start: Mapped[datetime | None] = _when()
    period_end: Mapped[datetime | None] = _when()
    issued_at: Mapped[datetime | None] = _when()
    paid_at: Mapped[datetime | None] = _when()
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="demo", server_default="demo")
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)


# ---------------------------------------------------------------------------
# Email: templates, campaigns, mailboxes
# ---------------------------------------------------------------------------


class EmailTemplate(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "email_templates"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    is_shared: Mapped[bool] = _bool(True)
    usage_count: Mapped[int] = _int()


class EmailCampaign(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "email_campaigns"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    # {"object_type": "lead"|"contact", "filters": [...], "abm_tier": "tier_1"|None}
    audience: Mapped[dict] = _json_dict()
    # draft | scheduled | sending | sent | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    scheduled_at: Mapped[datetime | None] = _when()
    started_at: Mapped[datetime | None] = _when()
    completed_at: Mapped[datetime | None] = _when()
    from_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    stats: Mapped[dict] = _json_dict()


class CampaignRecipient(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "email_campaign_recipients"
    __table_args__ = (Index("ix_campaign_recipients_campaign_status", "campaign_id", "status"),)

    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("email_campaigns.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Journeys also send through this table.
    journey_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    record_type: Mapped[str] = mapped_column(String(20), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # queued | sent | failed | skipped
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    # smtp | outbox
    delivery: Mapped[str | None] = mapped_column(String(20), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = _when()
    opened_at: Mapped[datetime | None] = _when()
    open_count: Mapped[int] = _int()
    unsubscribed_at: Mapped[datetime | None] = _when()


class MailboxConnection(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "mailbox_connections"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_mailbox_connections_user"),)

    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # gmail | outlook | smtp
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    smtp_host: Mapped[str] = mapped_column(String(200), nullable=False)
    smtp_port: Mapped[int] = _int(587)
    smtp_security: Mapped[str] = mapped_column(String(10), nullable=False, default="starttls", server_default="starttls")
    imap_host: Mapped[str | None] = mapped_column(String(200), nullable=True)
    imap_port: Mapped[int] = _int(993)
    username: Mapped[str] = mapped_column(String(320), nullable=False)
    secret: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted app password
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="connected", server_default="connected")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = _when()
    sync_enabled: Mapped[bool] = _bool(True)


# ---------------------------------------------------------------------------
# Calendar feed
# ---------------------------------------------------------------------------


class CalendarFeed(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "calendar_feeds"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_calendar_feeds_user"),)

    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    last_accessed_at: Mapped[datetime | None] = _when()


# ---------------------------------------------------------------------------
# Web forms
# ---------------------------------------------------------------------------


class WebForm(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "web_forms"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    target: Mapped[str] = mapped_column(String(20), nullable=False, default="lead", server_default="lead")  # lead | case
    fields: Mapped[list[dict]] = _json_list()
    settings: Mapped[dict] = _json_dict()
    is_active: Mapped[bool] = _bool(True)
    submission_count: Mapped[int] = _int()
    last_submission_at: Mapped[datetime | None] = _when()
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class WebFormSubmission(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "web_form_submissions"

    form_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("web_forms.id", ondelete="CASCADE"), nullable=False, index=True)
    data: Mapped[dict] = _json_dict()
    attribution: Mapped[dict] = _json_dict()
    record_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    record_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


# ---------------------------------------------------------------------------
# Data import
# ---------------------------------------------------------------------------


class ImportJob(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "import_jobs"

    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", server_default="completed")
    total_rows: Mapped[int] = _int()
    created_count: Mapped[int] = _int()
    updated_count: Mapped[int] = _int()
    skipped_count: Mapped[int] = _int()
    error_count: Mapped[int] = _int()
    errors: Mapped[list[dict]] = _json_list()
    mapping: Mapped[dict] = _json_dict()
    options: Mapped[dict] = _json_dict()
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


# ---------------------------------------------------------------------------
# Customization
# ---------------------------------------------------------------------------


class CustomModule(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "custom_modules"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_custom_modules_key"),)

    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)  # plural, e.g. "Vehicles"
    singular: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str] = mapped_column(String(40), nullable=False, default="box", server_default="box")
    is_active: Mapped[bool] = _bool(True)


class CustomField(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "custom_fields"
    __table_args__ = (UniqueConstraint("organization_id", "object_type", "key", name="uq_custom_fields_key"),)

    # lead | contact | account | opportunity | case | <custom module key>
    object_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), nullable=False)
    required: Mapped[bool] = _bool()
    options: Mapped[list[str]] = _json_list()
    help_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    position: Mapped[int] = _int()
    step: Mapped[int] = _int(1)
    is_encrypted: Mapped[bool] = _bool()
    is_active: Mapped[bool] = _bool(True)
    # lookup target, formula expression, default value, currency …
    config: Mapped[dict] = _json_dict()


class CustomRecord(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "custom_records"
    __table_args__ = (Index("ix_custom_records_module_created", "module_id", "created_at"),)

    module_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("custom_modules.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    data: Mapped[dict] = _json_dict()
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class CustomButton(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "custom_buttons"

    object_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # open_url | webhook | function
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = _json_dict()
    is_active: Mapped[bool] = _bool(True)
    position: Mapped[int] = _int()
    run_count: Mapped[int] = _int()
    last_run_at: Mapped[datetime | None] = _when()


class StageRule(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """Process management: what a deal needs to enter or leave a stage."""

    __tablename__ = "stage_rules"
    __table_args__ = (UniqueConstraint("stage_id", name="uq_stage_rules_stage"),)

    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_stages.id", ondelete="CASCADE"), nullable=False)
    required_fields: Mapped[list[str]] = _json_list()
    checklist: Mapped[list[str]] = _json_list()
    exit_requires_checklist: Mapped[bool] = _bool()
    guidance: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalRule(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "approval_rules"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)  # opportunity | quote
    # stage_change (opportunity moves into target_stage_id) | quote_send
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    target_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_stages.id", ondelete="CASCADE"), nullable=True
    )
    conditions: Mapped[list[dict]] = _json_list()
    # user | role | manager (the requester's manager in the reporting hierarchy)
    approver_type: Mapped[str] = mapped_column(String(20), nullable=False, default="role", server_default="role")
    approver_value: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lock_record: Mapped[bool] = _bool(True)
    is_active: Mapped[bool] = _bool(True)


class ApprovalRequest(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approval_requests_org_status", "organization_id", "status"),)

    rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_rules.id", ondelete="SET NULL"), nullable=True)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    record_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requested_change: Mapped[dict] = _json_dict()
    # pending | approved | rejected | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    requested_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    approver_user_id: Mapped[uuid.UUID | None] = _user_fk()
    approver_role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    decided_at: Mapped[datetime | None] = _when()
    request_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Integrations and API keys
# ---------------------------------------------------------------------------


class Integration(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("organization_id", "kind", name="uq_integrations_kind"),)

    # slack | teams | zoom | google_ads | quickbooks
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    config: Mapped[dict] = _json_dict()  # secrets inside are encrypted
    events: Mapped[list[str]] = _json_list()
    is_active: Mapped[bool] = _bool(True)
    connected_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    last_used_at: Mapped[datetime | None] = _when()
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class OutboundMessage(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """Queued chat alert or webhook call, delivered by the background scheduler."""

    __tablename__ = "outbound_messages"
    __table_args__ = (Index("ix_outbound_messages_status", "status", "deliver_after"),)

    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # slack | teams
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = _json_dict()
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = _int()
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deliver_after: Mapped[datetime | None] = _when()
    sent_at: Mapped[datetime | None] = _when()


class ApiKey(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="read_write", server_default="read_write")
    created_by_user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    last_used_at: Mapped[datetime | None] = _when()
    expires_at: Mapped[datetime | None] = _when()
    revoked_at: Mapped[datetime | None] = _when()


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------


class AiSuggestion(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "ai_suggestions"
    __table_args__ = (Index("ix_ai_suggestions_org_status", "organization_id", "status"),)

    agent: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(20), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    record_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[dict] = _json_dict()
    confidence: Mapped[int] = _int(50)
    # open | accepted | dismissed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="rules", server_default="rules")
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    resolved_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    resolved_at: Mapped[datetime | None] = _when()
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


# ---------------------------------------------------------------------------
# Inventory and invoices
# ---------------------------------------------------------------------------


class StockMovement(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "stock_movements"

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    # receive | adjust | sale | return
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_after: Mapped[Decimal] = _money()
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class SalesInvoice(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "sales_invoices"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_sales_invoices_number"),)

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    # draft | sent | paid | void
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    subtotal: Mapped[Decimal] = _money()
    discount_total: Mapped[Decimal] = _money()
    tax_total: Mapped[Decimal] = _money()
    total: Mapped[Decimal] = _money()
    amount_paid: Mapped[Decimal] = _money()
    paid_at: Mapped[datetime | None] = _when()
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_items: Mapped[list[dict]] = _json_list()
    exported_at: Mapped[datetime | None] = _when()


# ---------------------------------------------------------------------------
# Portals
# ---------------------------------------------------------------------------


class Portal(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "portals"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    audience: Mapped[str] = mapped_column(String(20), nullable=False, default="customer", server_default="customer")
    settings: Mapped[dict] = _json_dict()
    is_active: Mapped[bool] = _bool(True)


class PortalUser(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "portal_users"
    __table_args__ = (UniqueConstraint("portal_id", "email", name="uq_portal_users_email"),)

    portal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portals.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    last_login_at: Mapped[datetime | None] = _when()


class PortalToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "portal_tokens"

    portal_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portal_users.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # login | session
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = _when()


# ---------------------------------------------------------------------------
# Customer journeys
# ---------------------------------------------------------------------------


class Journey(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "journeys"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)  # lead | contact
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="created", server_default="created")
    conditions: Mapped[list[dict]] = _json_list()
    steps: Mapped[list[dict]] = _json_list()
    # draft | active | paused
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class JourneyEnrollment(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "journey_enrollments"
    __table_args__ = (
        UniqueConstraint("journey_id", "record_id", name="uq_journey_enrollments_record"),
        Index("ix_journey_enrollments_due", "status", "next_run_at"),
    )

    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(20), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    record_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # active | completed | exited | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    current_step: Mapped[int] = _int()
    next_run_at: Mapped[datetime | None] = _when()
    completed_at: Mapped[datetime | None] = _when()
    history: Mapped[list[dict]] = _json_list()


# ---------------------------------------------------------------------------
# Teams (multi-team) and account based marketing
# ---------------------------------------------------------------------------


class TeamMember(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_members_user"),)

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member", server_default="member")


class TargetAccount(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "target_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "account_id", name="uq_target_accounts_account"),)

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="tier_2", server_default="tier_2")
    goal: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
