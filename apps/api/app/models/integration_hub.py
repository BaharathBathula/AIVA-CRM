"""Integrations hub: on/off switches, connected accounts, activity, outgoing
webhooks (Zapier, Make, n8n), cloud-file links, e-signature envelopes and
SMS / WhatsApp messages.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_dict, _json_list, _user_fk, _when


class IntegrationToggle(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """The workspace's on/off switch for one integration, plus workspace-level options.

    ``config`` holds non-secret options; ``secrets`` holds encrypted values such as a
    workspace's own OAuth app credentials. Neither is ever returned with secrets in it.
    """

    __tablename__ = "integration_toggles"
    __table_args__ = (UniqueConstraint("organization_id", "provider", name="uq_integration_toggles_provider"),)

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = _bool(False)
    config: Mapped[dict] = _json_dict()
    secrets: Mapped[dict] = _json_dict()
    updated_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class IntegrationConnection(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """A connected account. Workspace integrations have one (``user_id`` empty);
    personal ones (mailboxes, calendars, Zoom) have one per person."""

    __tablename__ = "integration_connections"
    __table_args__ = (
        Index(
            "uq_integration_connections_workspace", "organization_id", "provider",
            unique=True, postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_integration_connections_user", "organization_id", "provider", "user_id",
            unique=True, postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = _user_fk(ondelete="CASCADE")
    # connected | error | needs_reauth
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="connected", server_default="connected")
    account_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    external_account_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    scopes: Mapped[list[str]] = _json_list()
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    token_expires_at: Mapped[datetime | None] = _when()
    secrets: Mapped[dict] = _json_dict()  # encrypted values (API keys, signing keys)
    config: Mapped[dict] = _json_dict()  # non-secret settings (channel, calendar, sync options)
    # Random path segment for this connection's inbound webhook URL.
    webhook_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    last_sync_at: Mapped[datetime | None] = _when()
    last_used_at: Mapped[datetime | None] = _when()
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class IntegrationOAuthState(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """A pending "connect your account" round trip (PKCE verifier kept server-side)."""

    __tablename__ = "integration_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[uuid.UUID | None] = _user_fk(ondelete="CASCADE")
    personal: Mapped[bool] = _bool(False)
    code_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    return_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = _when()


class IntegrationLog(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "integration_logs"
    __table_args__ = (Index("ix_integration_logs_org_provider_created", "organization_id", "provider", "created_at"),)

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("integration_connections.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = _user_fk()
    # info | success | warning | error
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="info", server_default="info")
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    detail: Mapped[dict] = _json_dict()


class WebhookSubscription(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """An outgoing webhook for Zapier, Make, n8n (or any URL) on one event."""

    __tablename__ = "webhook_subscriptions"
    __table_args__ = (Index("ix_webhook_subscriptions_org_event", "organization_id", "event"),)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # zapier | make | n8n | custom
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted signing secret
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # manual (pasted URL) | rest_hook (subscribed by the platform through the API)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual", server_default="manual")
    is_active: Mapped[bool] = _bool(True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    last_delivery_at: Mapped[datetime | None] = _when()
    last_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consecutive_failures: Mapped[int] = _int()


class WebhookDelivery(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_status_next", "status", "next_attempt_at"),)

    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = _json_dict()
    # pending | delivered | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = _int()
    next_attempt_at: Mapped[datetime | None] = _when()
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = _when()


class ExternalFileLink(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """A Google Drive, OneDrive or Dropbox file attached to a record."""

    __tablename__ = "external_file_links"
    __table_args__ = (
        Index("ix_external_file_links_record", "organization_id", "record_type", "record_id"),
        UniqueConstraint("organization_id", "provider", "file_id", "record_type", "record_id", name="uq_external_file_links_file_record"),
    )

    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    file_id: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    web_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_modified_at: Mapped[datetime | None] = _when()
    record_type: Mapped[str] = mapped_column(String(30), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    added_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class SignatureRequest(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """A DocuSign envelope sent from AIVA."""

    __tablename__ = "signature_requests"
    __table_args__ = (Index("ix_signature_requests_record", "organization_id", "record_type", "record_id"),)

    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="docusign", server_default="docusign")
    envelope_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # created | sent | delivered | completed | declined | voided | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created", server_default="created")
    record_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    record_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True)
    document_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recipients: Mapped[list[dict]] = _json_list()
    sent_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    sent_at: Mapped[datetime | None] = _when()
    completed_at: Mapped[datetime | None] = _when()
    last_checked_at: Mapped[datetime | None] = _when()
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChannelMessage(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    """An SMS (Twilio) or WhatsApp message, sent from AIVA or received."""

    __tablename__ = "channel_messages"
    __table_args__ = (
        Index("ix_channel_messages_org_channel_created", "organization_id", "channel", "created_at"),
        Index("ix_channel_messages_external", "channel", "external_id"),
    )

    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # sms | whatsapp
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # inbound | outbound
    from_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    media: Mapped[list[dict]] = _json_list()
    template_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # queued | sent | delivered | read | failed | received
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = _user_fk()
    is_read: Mapped[bool] = _bool(False)
