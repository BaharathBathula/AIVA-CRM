"""Integrations: mailboxes, Slack, Teams, Zoom, calendar feeds, API keys and HIPAA mode."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import API_KEY_PREFIX, Principal, get_current_principal, require_feature, require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt, new_token, sha256
from app.db.session import get_db
from app.models.editions import ApiKey, CalendarFeed, Integration, MailboxConnection
from app.models.organization import Organization
from app.models.user import User
from app.services import chat, mailer
from app.services.automation import CHAT_EVENTS, invalidate_automation_cache, record_audit
from app.services.lookups import names_for
from app.services.mailbox_sync import sync_connection

router = APIRouter(tags=["Integrations"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Overview
# ===========================================================================


@router.get("/integrations")
async def integrations_overview(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    rows = {i.kind: i for i in (await db.execute(select(Integration).where(Integration.organization_id == org))).scalars()}
    mailbox = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org, MailboxConnection.user_id == principal.user_id))
    ).scalar_one_or_none()
    feed = (
        await db.execute(select(CalendarFeed).where(CalendarFeed.organization_id == org, CalendarFeed.user_id == principal.user_id))
    ).scalar_one_or_none()
    settings = get_settings()
    zoom_links = ((rows.get("zoom").config if rows.get("zoom") else {}) or {}).get("personal_links") or {}

    def chat_out(kind: str) -> dict[str, Any]:
        integration = rows.get(kind)
        return {
            "connected": bool(integration and (integration.config or {}).get("webhook_url")),
            "is_active": bool(integration and integration.is_active),
            "events": list(integration.events or []) if integration else ["deal_won", "case_urgent"],
            "last_used_at": integration.last_used_at if integration else None,
            "last_error": integration.last_error if integration else None,
        }

    return {
        "can_manage": principal.can("admin.settings"),
        "mailbox": mailbox_out(mailbox),
        "mailbox_presets": {k: {key: v for key, v in preset.items()} for k, preset in mailer.PROVIDER_PRESETS.items()},
        "server_mail": bool(settings.smtp_host and settings.smtp_from_email),
        "slack": chat_out("slack"),
        "teams": chat_out("teams"),
        "chat_events": [{"key": k, "label": v} for k, v in CHAT_EVENTS.items()],
        "zoom": {"personal_link": zoom_links.get(str(principal.user_id))},
        "calendar": {"url": calendar_url(decrypt(feed.token_encrypted)) if feed else None, "last_accessed_at": feed.last_accessed_at if feed else None},
        "google_ads": {"connected": "google_ads" in rows},
        "quickbooks": {"available": principal.has("quickbooks")},
        "stripe": {"configured": bool(settings.stripe_secret_key)},
        "ai": {"configured": bool(settings.anthropic_api_key), "model": settings.ai_model if settings.anthropic_api_key else None},
        "features": {
            "mailbox_sync": principal.has("mailbox_sync"),
            "chat_integrations": principal.has("chat_integrations"),
            "calendar_sync": principal.has("calendar_sync"),
            "google_ads": principal.has("google_ads"),
            "api_access": principal.has("api_access"),
        },
    }


# ===========================================================================
# Mailbox (Gmail, Outlook, SMTP)
# ===========================================================================


def mailbox_out(connection: MailboxConnection | None) -> dict[str, Any] | None:
    if connection is None:
        return None
    return {
        "id": str(connection.id),
        "provider": connection.provider,
        "email_address": connection.email_address,
        "display_name": connection.display_name,
        "smtp_host": connection.smtp_host,
        "smtp_port": connection.smtp_port,
        "smtp_security": connection.smtp_security,
        "imap_host": connection.imap_host,
        "imap_port": connection.imap_port,
        "username": connection.username,
        "status": connection.status,
        "last_error": connection.last_error,
        "last_sync_at": connection.last_sync_at,
        "sync_enabled": connection.sync_enabled,
    }


class MailboxPayload(BaseModel):
    provider: str = Field(pattern="^(gmail|outlook|smtp)$")
    email_address: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=1, max_length=500)
    smtp_host: str | None = Field(default=None, max_length=200)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: str | None = Field(default=None, pattern="^(starttls|ssl|none)$")
    imap_host: str | None = Field(default=None, max_length=200)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    sync_enabled: bool = True
    skip_check: bool = False


@router.put("/integrations/mailbox", dependencies=[Depends(require_feature("mailbox_sync"))])
async def connect_mailbox(
    payload: MailboxPayload,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    preset = mailer.PROVIDER_PRESETS[payload.provider]
    email_address = payload.email_address.strip().lower()
    values = {
        "provider": payload.provider,
        "email_address": email_address,
        "display_name": payload.display_name or principal.full_name,
        "username": (payload.username or email_address).strip(),
        "smtp_host": (payload.smtp_host or preset["smtp_host"]).strip(),
        "smtp_port": payload.smtp_port or preset["smtp_port"],
        "smtp_security": payload.smtp_security or preset["smtp_security"],
        "imap_host": (payload.imap_host or preset["imap_host"] or "").strip() or None,
        "imap_port": payload.imap_port or preset["imap_port"],
        "sync_enabled": payload.sync_enabled,
    }
    if not values["smtp_host"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter your SMTP server.")
    config = mailer.SmtpConfig(
        values["smtp_host"], values["smtp_port"], values["smtp_security"], values["username"], payload.password, email_address, values["display_name"], "mailbox"
    )
    if not payload.skip_check:
        try:
            await mailer.verify_smtp(config)
        except mailer.MailError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    connection = (
        await db.execute(
            select(MailboxConnection).where(
                MailboxConnection.organization_id == principal.organization_id, MailboxConnection.user_id == principal.user_id
            )
        )
    ).scalar_one_or_none()
    if connection is None:
        connection = MailboxConnection(id=uuid.uuid4(), organization_id=principal.organization_id, user_id=principal.user_id, secret="")
        db.add(connection)
    for key, value in values.items():
        setattr(connection, key, value)
    connection.secret = encrypt(payload.password)
    connection.status = "connected"
    connection.last_error = None
    record_audit(db, organization_id=principal.organization_id, action="connect", entity_type="mailbox", entity_label=email_address)
    await db.commit()
    return mailbox_out(connection)


async def _mailbox(db: AsyncSession, principal: Principal) -> MailboxConnection:
    connection = (
        await db.execute(
            select(MailboxConnection).where(
                MailboxConnection.organization_id == principal.organization_id, MailboxConnection.user_id == principal.user_id
            )
        )
    ).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connect a mailbox first.")
    return connection


@router.delete("/integrations/mailbox", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_mailbox(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    connection = await _mailbox(db, principal)
    await db.delete(connection)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/integrations/mailbox/test")
async def test_mailbox(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    connection = await _mailbox(db, principal)
    try:
        await mailer.send(
            mailer.mailbox_config(connection),
            to=connection.email_address,
            subject="AIVA is connected to your mailbox",
            text="This test message was sent by AIVA CRM through your mailbox. Emails you send from AIVA will now come from this address.",
        )
    except mailer.MailError as exc:
        connection.status = "error"
        connection.last_error = str(exc)
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"message": f"Test email sent to {connection.email_address}."}


@router.post("/integrations/mailbox/sync", dependencies=[Depends(require_feature("mailbox_sync"))])
async def sync_mailbox(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    connection = await _mailbox(db, principal)
    try:
        return await sync_connection(db, connection)
    except mailer.MailError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ===========================================================================
# Slack and Teams
# ===========================================================================


class ChatPayload(BaseModel):
    webhook_url: str | None = Field(default=None, max_length=1000)
    events: list[str] = Field(default_factory=list)
    is_active: bool = True


async def _integration(db: AsyncSession, organization_id: uuid.UUID, kind: str) -> Integration | None:
    return (
        await db.execute(select(Integration).where(Integration.organization_id == organization_id, Integration.kind == kind))
    ).scalar_one_or_none()


@router.put("/integrations/chat/{kind}", dependencies=[Depends(require_feature("chat_integrations"))])
async def save_chat(
    kind: str,
    payload: ChatPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    if kind not in {"slack", "teams"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown app.")
    events = [event for event in payload.events if event in CHAT_EVENTS]
    integration = await _integration(db, principal.organization_id, kind)
    if integration is None:
        if not payload.webhook_url:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Paste the incoming webhook URL.")
        integration = Integration(
            id=uuid.uuid4(), organization_id=principal.organization_id, kind=kind, config={}, events=[], connected_by_user_id=principal.user_id
        )
        db.add(integration)
    if payload.webhook_url:
        url = payload.webhook_url.strip()
        allowed = ("https://hooks.slack.com/",) if kind == "slack" else ("https://",)
        if not url.startswith(allowed):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Slack webhook URLs start with https://hooks.slack.com/." if kind == "slack" else "Paste the https:// webhook URL from Teams.",
            )
        integration.config = {**(integration.config or {}), "webhook_url": encrypt(url)}
    integration.events = events
    integration.is_active = payload.is_active
    integration.last_error = None
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return {"connected": True, "events": events, "is_active": integration.is_active}


@router.post("/integrations/chat/{kind}/test", dependencies=[Depends(require_feature("chat_integrations"))])
async def test_chat(
    kind: str,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    integration = await _integration(db, principal.organization_id, kind)
    url = chat.webhook_url(integration) if integration else None
    if not url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connect the app first.")
    try:
        await chat.post(kind, url, "AIVA is connected", f"{principal.full_name} connected AIVA CRM. Alerts will appear here.", "/")
    except Exception as exc:  # noqa: BLE001
        integration.last_error = str(exc)[:500]
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The message didn't go through: {exc}") from exc
    integration.last_used_at = utcnow()
    integration.last_error = None
    await db.commit()
    return {"message": "Test message sent."}


@router.delete("/integrations/chat/{kind}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_chat(
    kind: str,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    integration = await _integration(db, principal.organization_id, kind)
    if integration is not None:
        await db.delete(integration)
        await db.commit()
        invalidate_automation_cache(principal.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ZoomPayload(BaseModel):
    personal_link: str | None = Field(default=None, max_length=500)


@router.put("/integrations/zoom", dependencies=[Depends(require_feature("chat_integrations"))])
async def save_zoom(
    payload: ZoomPayload,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    link = (payload.personal_link or "").strip()
    if link and not (link.startswith("https://") and ("zoom.us/" in link or "zoom.com/" in link)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Paste your Zoom personal meeting link (https://…zoom.us/j/…).")
    integration = await _integration(db, principal.organization_id, "zoom")
    if integration is None:
        integration = Integration(
            id=uuid.uuid4(), organization_id=principal.organization_id, kind="zoom", config={}, events=[], connected_by_user_id=principal.user_id
        )
        db.add(integration)
    links = dict((integration.config or {}).get("personal_links") or {})
    if link:
        links[str(principal.user_id)] = link
    else:
        links.pop(str(principal.user_id), None)
    integration.config = {**(integration.config or {}), "personal_links": links}
    await db.commit()
    return {"personal_link": link or None}


# ===========================================================================
# Calendar feed (iCal)
# ===========================================================================


def calendar_url(token: str | None) -> str | None:
    if not token:
        return None
    return f"{get_settings().app_base_url.rstrip('/')}/api/aiva/public/calendar/{token}.ics"


@router.post("/integrations/calendar", dependencies=[Depends(require_feature("calendar_sync"))])
async def create_calendar_feed(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    feed = (
        await db.execute(select(CalendarFeed).where(CalendarFeed.organization_id == principal.organization_id, CalendarFeed.user_id == principal.user_id))
    ).scalar_one_or_none()
    token = new_token(nbytes=24)
    if feed is None:
        feed = CalendarFeed(id=uuid.uuid4(), organization_id=principal.organization_id, user_id=principal.user_id, token_hash="", token_encrypted="")
        db.add(feed)
    feed.token_hash = sha256(token)
    feed.token_encrypted = encrypt(token)
    feed.last_accessed_at = None
    await db.commit()
    return {"url": calendar_url(token)}


@router.delete("/integrations/calendar", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar_feed(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    feed = (
        await db.execute(select(CalendarFeed).where(CalendarFeed.organization_id == principal.organization_id, CalendarFeed.user_id == principal.user_id))
    ).scalar_one_or_none()
    if feed is not None:
        await db.delete(feed)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# API keys
# ===========================================================================


class ApiKeyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scope: str = Field(default="read_write", pattern="^(read|read_write)$")
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


def api_key_out(key: ApiKey, owners: dict[str, str]) -> dict[str, Any]:
    now = utcnow()
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.prefix,
        "scope": key.scope,
        "created_by": owners.get(str(key.created_by_user_id)),
        "created_by_user_id": str(key.created_by_user_id),
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
        "expires_at": key.expires_at,
        "revoked_at": key.revoked_at,
        "status": "revoked" if key.revoked_at else "expired" if key.expires_at and key.expires_at <= now else "active",
    }


@router.get("/api-keys", dependencies=[Depends(require_feature("api_access"))])
async def list_api_keys(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    query = select(ApiKey).where(ApiKey.organization_id == principal.organization_id)
    if not principal.can("admin.settings"):
        query = query.where(ApiKey.created_by_user_id == principal.user_id)
    keys = (await db.execute(query.order_by(ApiKey.created_at.desc()))).scalars().all()
    owners = await names_for(db, principal.organization_id, "user", [k.created_by_user_id for k in keys])
    return {
        "items": [api_key_out(k, owners) for k in keys],
        "organization_id": str(principal.organization_id),
        "base_url": f"{get_settings().app_base_url.rstrip('/')}/api/v1",
        "docs_url": "/docs",
    }


@router.post("/api-keys", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("api_access"))])
async def create_api_key(
    payload: ApiKeyPayload,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    if principal.api_key_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API keys can't create other API keys.")
    raw = new_token(API_KEY_PREFIX, 30)
    key = ApiKey(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        name=payload.name.strip(),
        prefix=raw[:14],
        key_hash=sha256(raw),
        scope=payload.scope,
        created_by_user_id=principal.user_id,
        expires_at=utcnow() + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None,
    )
    db.add(key)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="api_key", entity_id=key.id, entity_label=key.name)
    await db.commit()
    return {"key": raw, **api_key_out(key, {str(principal.user_id): principal.full_name})}


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    key = await db.get(ApiKey, key_id)
    if key is None or key.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found.")
    if key.created_by_user_id != principal.user_id and not principal.can("admin.settings"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the key's creator or an administrator can revoke it.")
    key.revoked_at = utcnow()
    record_audit(db, organization_id=principal.organization_id, action="revoke", entity_type="api_key", entity_id=key.id, entity_label=key.name)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Compliance (HIPAA mode)
# ===========================================================================


class CompliancePayload(BaseModel):
    hipaa_mode: bool


@router.get("/compliance")
async def compliance(
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    from app.models.membership import OrganizationMembership

    rows = (
        await db.execute(
            select(User.full_name, User.email, User.mfa_enabled)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == principal.organization_id, OrganizationMembership.status == "active")
            .order_by(User.full_name)
        )
    ).all()
    settings = organization.settings or {}
    return {
        "available": principal.has("hipaa"),
        "hipaa_mode": bool(settings.get("hipaa_mode")),
        "session_timeout_minutes": settings.get("session_timeout_minutes", 30),
        "encryption_available": principal.has("field_encryption"),
        "members": [{"name": name, "email": email, "mfa_enabled": mfa} for name, email, mfa in rows],
        "members_without_mfa": len([1 for _, _, mfa in rows if not mfa]),
    }


@router.put("/compliance", dependencies=[Depends(require_feature("hipaa"))])
async def set_compliance(
    payload: CompliancePayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    user = await db.get(User, principal.user_id)
    if payload.hipaa_mode and not user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn on two-step verification for your own account first, so you don't lock yourself out.")
    settings = dict(organization.settings or {})
    settings["hipaa_mode"] = payload.hipaa_mode
    if payload.hipaa_mode:
        settings["session_timeout_minutes"] = min(int(settings.get("session_timeout_minutes") or 30), 15)
    organization.settings = settings
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="update",
        entity_type="organization",
        entity_id=organization.id,
        entity_label="HIPAA mode",
        changes={"hipaa_mode": {"from": not payload.hipaa_mode, "to": payload.hipaa_mode}},
    )
    await db.commit()
    return await compliance(principal, db)
