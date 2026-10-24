"""Integrations hub API: catalog, on/off switches, connecting accounts, settings,
tests and the activity log. Provider-specific endpoints live in the
``int_*`` route modules; inbound webhooks and the OAuth callback are public.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_permission
from app.core.config import get_settings
from app.db.session import get_db
from app.integrations import core
from app.integrations.catalog import CATEGORIES, FAMILIES, INTEGRATIONS, IntegrationDef
from app.integrations.core import IntegrationError
from app.integrations.providers import call, hook
from app.models.integration_hub import IntegrationConnection, IntegrationLog, IntegrationToggle, WebhookSubscription
from app.services.automation import record_audit
from app.services.lookups import names_for

logger = logging.getLogger("aiva.integrations")

router = APIRouter(prefix="/integration-hub", tags=["Integrations"])
public_router = APIRouter(prefix="/public/integrations", tags=["Public"])


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def _item(key: str) -> IntegrationDef:
    item = INTEGRATIONS.get(key)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown integration.")
    return item


def connection_out(connection: IntegrationConnection | None, names: dict[str, str] | None = None) -> dict[str, Any] | None:
    if connection is None:
        return None
    return {
        "id": str(connection.id),
        "provider": connection.provider,
        "status": connection.status,
        "account_label": connection.account_label,
        "external_account_id": connection.external_account_id,
        "scopes": list(connection.scopes or []),
        "config": dict(connection.config or {}),
        "secrets": core.secrets_set(connection),
        "user_id": str(connection.user_id) if connection.user_id else None,
        "user_name": (names or {}).get(str(connection.user_id)) if connection.user_id else None,
        "webhook_url": core.webhook_url(connection.provider, connection.webhook_token),
        "last_sync_at": connection.last_sync_at,
        "last_used_at": connection.last_used_at,
        "last_error": connection.last_error,
        "connected_at": connection.created_at,
        "updated_at": connection.updated_at,
    }


def _status(item: IntegrationDef, available: bool, enabled: bool, connection: IntegrationConnection | None, extra_ready: bool = False) -> str:
    if not available:
        return "upgrade"
    if not enabled:
        return "off"
    if connection is not None:
        return connection.status
    if extra_ready:
        return "ready"
    return "not_connected"


async def _connections(db: AsyncSession, principal: Principal) -> tuple[dict[str, IntegrationConnection], dict[str, IntegrationConnection], dict[str, int]]:
    rows = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == principal.organization_id))).scalars().all()
    workspace = {r.provider: r for r in rows if r.user_id is None}
    mine = {r.provider: r for r in rows if r.user_id == principal.user_id}
    people: dict[str, int] = {}
    for r in rows:
        if r.user_id is not None and r.status == "connected":
            people[r.provider] = people.get(r.provider, 0) + 1
    return workspace, mine, people


def _relevant(item: IntegrationDef, workspace: dict[str, IntegrationConnection], mine: dict[str, IntegrationConnection]) -> IntegrationConnection | None:
    if item.depends_on:
        return mine.get(item.depends_on) if INTEGRATIONS[item.depends_on].personal else workspace.get(item.depends_on)
    if item.personal:
        return mine.get(item.key)
    return workspace.get(item.key)


async def _hook_counts(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await db.execute(
            select(WebhookSubscription.provider, func.count())
            .where(WebhookSubscription.organization_id == organization_id, WebhookSubscription.is_active.is_(True))
            .group_by(WebhookSubscription.provider)
        )
    ).all()
    return {provider: int(count) for provider, count in rows}


# ===========================================================================
# Catalog
# ===========================================================================


@router.get("/catalog")
async def catalog(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    toggles = await core.toggles(db, principal.organization_id)
    workspace, mine, people = await _connections(db, principal)
    hooks = await _hook_counts(db, principal.organization_id)
    items = []
    for item in INTEGRATIONS.values():
        toggle = toggles.get(item.key)
        enabled = bool(toggle and toggle.enabled) and (not item.depends_on or bool(toggles.get(item.depends_on) and toggles[item.depends_on].enabled))
        available = principal.has(item.feature)
        connection = _relevant(item, workspace, mine)
        state = _status(item, available, enabled, connection, extra_ready=item.auth == "api_key")
        if item.auth == "api_key" and enabled and available and hooks.get(item.key):
            state = "connected"
        items.append(
            {
                **item.to_dict(),
                "enabled": enabled,
                "switched_on": bool(toggle and toggle.enabled),
                "available": available,
                "status": state,
                "account_label": connection.account_label if connection else None,
                "people_connected": people.get(item.key, 0),
                "subscriptions": hooks.get(item.key, 0),
            }
        )
    return {"items": items, "categories": list(CATEGORIES), "can_manage": principal.can("admin.settings")}


# ===========================================================================
# One integration
# ===========================================================================


async def _logs(db: AsyncSession, principal: Principal, key: str | None, limit: int = 40, everyone: bool = True) -> list[dict[str, Any]]:
    query = select(IntegrationLog).where(IntegrationLog.organization_id == principal.organization_id)
    if key:
        query = query.where(IntegrationLog.provider == key)
    if not everyone:
        query = query.where((IntegrationLog.user_id == principal.user_id) | IntegrationLog.user_id.is_(None))
    rows = (await db.execute(query.order_by(IntegrationLog.created_at.desc()).limit(limit))).scalars().all()
    names = await names_for(db, principal.organization_id, "user", [r.user_id for r in rows])
    return [
        {
            "id": str(r.id), "provider": r.provider, "provider_name": INTEGRATIONS[r.provider].name if r.provider in INTEGRATIONS else r.provider,
            "level": r.level, "action": r.action, "message": r.message, "detail": r.detail or {}, "user": names.get(str(r.user_id)) if r.user_id else None,
            "at": r.created_at,
        }
        for r in rows
    ]


@router.get("/{key}")
async def integration_detail(key: str, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    item = _item(key)
    org = principal.organization_id
    toggles = await core.toggles(db, org)
    toggle = toggles.get(key)
    manage = principal.can("admin.settings")
    workspace, mine, _ = await _connections(db, principal)
    connection = _relevant(item, workspace, mine)
    enabled = bool(toggle and toggle.enabled) and (not item.depends_on or bool(toggles.get(item.depends_on) and toggles[item.depends_on].enabled))
    available = principal.has(item.feature)
    hooks = await _hook_counts(db, org)
    state = _status(item, available, enabled, connection, extra_ready=item.auth == "api_key")
    if item.auth == "api_key" and enabled and available and hooks.get(key):
        state = "connected"
    people: list[dict[str, Any]] = []
    if item.personal and manage:
        rows = (
            await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == org, IntegrationConnection.provider == key, IntegrationConnection.user_id.is_not(None)))
        ).scalars().all()
        names = await names_for(db, org, "user", [r.user_id for r in rows])
        people = [connection_out(r, names) for r in rows]
    oauth: dict[str, Any] | None = None
    if item.family in FAMILIES and item.auth in {"oauth", "credentials"} and item.key not in {"google_meet"}:
        family = FAMILIES[item.family]
        app = await core.oauth_app(db, org, family.key)
        app_row = toggles.get(f"app:{family.key}")
        oauth = {
            "family": family.key,
            "label": family.label,
            "configured": bool(app["client_id"]),
            "source": app["source"],
            "redirect_uri": core.redirect_uri(),
            "console_url": family.console_url,
            "workspace_client_id": (app_row.config or {}).get("client_id") if app_row and manage else None,
            "options": {k: v for k, v in (app_row.config or {}).items() if k != "client_id"} if app_row and manage else {},
        }
    try:
        extra = await call(key, "detail", db, key, principal, default={}) if enabled and available else {}
    except IntegrationError as exc:
        extra = {"error": str(exc)}
    except Exception:  # noqa: BLE001 - a provider hiccup mustn't break the page
        logger.exception("Integration detail hook failed for %s", key)
        extra = {"error": "Couldn't load live details from the provider right now."}
    return {
        "integration": item.to_dict(),
        "enabled": enabled,
        "switched_on": bool(toggle and toggle.enabled),
        "dependency_enabled": bool(not item.depends_on or (toggles.get(item.depends_on) and toggles[item.depends_on].enabled)),
        "available": available,
        "status": state,
        "can_manage": manage,
        "connection": connection_out(connection, {str(principal.user_id): principal.full_name}),
        "people": people,
        "workspace_config": dict(toggle.config or {}) if toggle else {},
        "workspace_secrets": core.secrets_set(toggle),
        "oauth": oauth,
        "app_webhook_url": core.app_webhook_url(key) if key == "facebook_lead_ads" else None,
        "subscriptions": hooks.get(key, 0),
        "extra": extra or {},
        "logs": await _logs(db, principal, key, 40, everyone=manage),
    }


class TogglePayload(BaseModel):
    enabled: bool


@router.put("/{key}/toggle")
async def set_toggle(key: str, payload: TogglePayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    item = _item(key)
    org = principal.organization_id
    if payload.enabled and not principal.has(item.feature):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{item.name} isn't included in your plan.")
    if payload.enabled and item.depends_on and not await core.is_enabled(db, org, item.depends_on):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Turn on {INTEGRATIONS[item.depends_on].name} first; {item.name} uses it.")
    toggle = await core.toggle_row(db, org, key)
    if toggle is None:
        toggle = IntegrationToggle(id=uuid.uuid4(), organization_id=org, provider=key, enabled=False)
        db.add(toggle)
    toggle.enabled = payload.enabled
    toggle.updated_by_user_id = principal.user_id
    if payload.enabled and item.auth == "webhook":
        existing = await core.connection_for(db, org, key)
        if existing is None:
            db.add(IntegrationConnection(id=uuid.uuid4(), organization_id=org, provider=key, status="connected", account_label="Webhook",
                                         webhook_token=core.new_webhook_token(), connected_by_user_id=principal.user_id))
    if not payload.enabled:
        # Things that depend on this one go off with it.
        for other in INTEGRATIONS.values():
            if other.depends_on == key:
                dependent = await core.toggle_row(db, org, other.key)
                if dependent is not None and dependent.enabled:
                    dependent.enabled = False
    await db.flush()
    try:
        await call(key, "on_toggle", db, key, org, payload.enabled)
    except IntegrationError as exc:
        core.log(db, org, key, "toggle_hook", str(exc), level="warning", user_id=principal.user_id)
    core.log(db, org, key, "switched_on" if payload.enabled else "switched_off", f"{item.name} turned {'on' if payload.enabled else 'off'}.", user_id=principal.user_id)
    record_audit(db, organization_id=org, action="integration_on" if payload.enabled else "integration_off", entity_type="integration", entity_label=item.name)
    await db.commit()
    return await integration_detail(key, principal, db)


class SettingsPayload(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


async def _clean(key: str, db: AsyncSession, principal: Principal, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    cleaner = hook(key, "clean_settings")
    if cleaner is None:
        return config
    try:
        return await cleaner(db, key, principal, config, personal)
    except IntegrationError as exc:
        raise _error(exc) from exc


@router.put("/{key}/settings")
async def workspace_settings(key: str, payload: SettingsPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Workspace-level options for an integration (default channel, events, lead owner...)."""

    item = _item(key)
    org = principal.organization_id
    toggle = await core.toggle_row(db, org, key)
    if toggle is None:
        toggle = IntegrationToggle(id=uuid.uuid4(), organization_id=org, provider=key, enabled=False)
        db.add(toggle)
    toggle.config = {**(toggle.config or {}), **await _clean(key, db, principal, payload.config, False)}
    toggle.updated_by_user_id = principal.user_id
    connection = await core.connection_for(db, org, key) if not item.personal else None
    if connection is not None:
        # Workspace connections read their options from the connection too.
        connection.config = {**(connection.config or {}), **{k: v for k, v in toggle.config.items()}}
    core.log(db, org, key, "settings_changed", "Settings updated.", user_id=principal.user_id)
    await db.commit()
    return await integration_detail(key, principal, db)


@router.put("/{key}/my-settings")
async def my_settings(key: str, payload: SettingsPayload, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Options for your own connection (which calendar to sync, whether to read the inbox...)."""

    item = _item(key)
    if not item.personal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.name} is set up for the whole workspace.")
    connection = await core.connection_for(db, principal.organization_id, key, principal.user_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connect your account first.")
    connection.config = {**(connection.config or {}), **await _clean(key, db, principal, payload.config, True)}
    core.log(db, principal.organization_id, key, "settings_changed", "Your settings were updated.", connection=connection, user_id=principal.user_id)
    await db.commit()
    return await integration_detail(key, principal, db)


class ConnectPayload(BaseModel):
    return_to: str | None = Field(default=None, max_length=300)


@router.post("/{key}/connect")
async def connect(key: str, payload: ConnectPayload, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Start signing in at the provider; returns the URL to send the browser to."""

    item = _item(key)
    if not principal.has(item.feature):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{item.name} isn't included in your plan.")
    if not item.personal and not principal.can("admin.settings") and key != "microsoft_teams":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only an admin can connect {item.name} for the workspace.")
    if principal.api_key_id is not None or principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Connect integrations while signed in as yourself.")
    try:
        url = await core.start_oauth(db, organization_id=principal.organization_id, user_id=principal.user_id, key=key, return_to=payload.return_to)
    except IntegrationError as exc:
        raise _error(exc) from exc
    return {"authorize_url": url}


class CredentialsPayload(BaseModel):
    values: dict[str, str | None] = Field(default_factory=dict)


@router.put("/{key}/credentials")
async def save_credentials(key: str, payload: CredentialsPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Connect with keys from the provider's console (WhatsApp, Twilio, a Teams or Slack webhook, a Calendly token)."""

    item = _item(key)
    org = principal.organization_id
    if not item.credential_fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.name} connects by signing in.")
    if not principal.has(item.feature):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{item.name} isn't included in your plan.")
    if not await core.is_enabled(db, org, key):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Turn {item.name} on first.")
    connection = await core.connection_for(db, org, key)
    values = {f.key: (payload.values.get(f.key) or "").strip() for f in item.credential_fields}
    # Blank secrets keep what's stored; required fields must end up with a value.
    for field_def in item.credential_fields:
        if not values[field_def.key] and field_def.secret and connection is not None and core.secret(connection, field_def.key):
            values[field_def.key] = core.secret(connection, field_def.key) or ""
        if not values[field_def.key] and field_def.required:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field_def.label} is required.")
    validator = hook(key, "validate_credentials")
    try:
        found = await validator(db, key, org, values) if validator else {}
    except IntegrationError as exc:
        raise _error(exc) from exc
    if connection is None:
        connection = IntegrationConnection(id=uuid.uuid4(), organization_id=org, provider=key, connected_by_user_id=principal.user_id)
        db.add(connection)
    for field_def in item.credential_fields:
        value = values[field_def.key]
        if field_def.secret:
            core.set_secret(connection, field_def.key, value or None)
        else:
            connection.config = {**(connection.config or {}), field_def.key: value or None}
    connection.config = {**(connection.config or {}), **(found.get("config") or {})}
    connection.account_label = (found.get("label") or connection.account_label or item.name)[:320]
    connection.external_account_id = (str(found.get("external_id") or connection.external_account_id or ""))[:300] or None
    connection.status = "connected"
    connection.last_error = None
    if not connection.webhook_token:
        connection.webhook_token = core.new_webhook_token()
    await db.flush()
    try:
        await call(key, "on_connected", db, key, connection)
    except IntegrationError as exc:
        connection.last_error = str(exc)
        core.log(db, org, key, "connect_followup", str(exc), level="warning", connection=connection, user_id=principal.user_id)
    core.log(db, org, key, "connected", f"Connected {connection.account_label}.", level="success", connection=connection, user_id=principal.user_id)
    record_audit(db, organization_id=org, action="integration_connected", entity_type="integration", entity_label=item.name)
    await db.commit()
    return await integration_detail(key, principal, db)


@router.delete("/{key}/connection")
async def disconnect(
    key: str,
    user_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    item = _item(key)
    org = principal.organization_id
    if item.personal:
        target = user_id or principal.user_id
        if target != principal.user_id and not principal.can("admin.settings"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can disconnect someone else's account.")
        connection = await core.connection_for(db, org, key, target)
    else:
        if not principal.can("admin.settings"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only an admin can disconnect {item.name}.")
        connection = await core.connection_for(db, org, key)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing is connected.")
    try:
        await call(key, "on_disconnect", db, key, connection)
    except IntegrationError as exc:
        core.log(db, org, key, "disconnect_cleanup", str(exc), level="warning", user_id=principal.user_id)
    except Exception:  # noqa: BLE001 - disconnecting must always work
        logger.exception("Disconnect hook failed for %s", key)
    label = connection.account_label or item.name
    await db.delete(connection)
    core.log(db, org, key, "disconnected", f"Disconnected {label}.", user_id=principal.user_id)
    record_audit(db, organization_id=org, action="integration_disconnected", entity_type="integration", entity_label=item.name)
    await db.commit()
    return await integration_detail(key, principal, db)


@router.post("/{key}/test")
async def test_integration(key: str, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    item = _item(key)
    org = principal.organization_id
    tester = hook(key, "test")
    if tester is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{item.name} has no live test.")
    try:
        connection = await core.active_connection(db, org, key, principal.user_id)
        message = await tester(db, key, connection, principal)
    except IntegrationError as exc:
        core.log(db, org, key, "test_failed", str(exc), level="error", user_id=principal.user_id)
        await db.commit()
        raise _error(exc) from exc
    connection.last_error = None
    if connection.status == "error":
        connection.status = "connected"
    core.log(db, org, key, "test_passed", message, level="success", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"ok": True, "message": message}


@router.get("/{key}/logs")
async def integration_logs(key: str, limit: int = Query(default=100, ge=1, le=500), principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    _item(key)
    return {"items": await _logs(db, principal, key, limit, everyone=principal.can("admin.settings"))}


@router.get("")
async def activity(
    provider: str | None = None,
    level: str | None = Query(default=None, pattern="^(info|success|warning|error)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Activity across every integration (admins see everyone's)."""

    items = await _logs(db, principal, provider, limit, everyone=principal.can("admin.settings"))
    if level:
        items = [i for i in items if i["level"] == level]
    return {"items": items, "providers": [{"key": k, "name": v.name} for k, v in INTEGRATIONS.items()]}


# ===========================================================================
# The workspace's own OAuth apps
# ===========================================================================


class AppPayload(BaseModel):
    client_id: str | None = Field(default=None, max_length=300)
    client_secret: str | None = Field(default=None, max_length=500)
    tenant: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9.\-]*$")
    environment: str | None = Field(default=None, pattern="^(demo|production)$")


@router.put("/apps/{family}")
async def set_app(family: str, payload: AppPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    if family not in FAMILIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider.")
    org = principal.organization_id
    key = f"app:{family}"
    row = await core.toggle_row(db, org, key)
    if row is None:
        row = IntegrationToggle(id=uuid.uuid4(), organization_id=org, provider=key, enabled=True)
        db.add(row)
    config = dict(row.config or {})
    if payload.client_id is not None:
        config["client_id"] = payload.client_id.strip() or None
    if payload.tenant is not None:
        config["tenant"] = payload.tenant.strip() or None
    if payload.environment is not None:
        config["environment"] = payload.environment
    row.config = {k: v for k, v in config.items() if v}
    if payload.client_secret:
        core.set_secret(row, "client_secret", payload.client_secret.strip())
    if not row.config.get("client_id"):
        core.set_secret(row, "client_secret", None)
    row.updated_by_user_id = principal.user_id
    record_audit(db, organization_id=org, action="integration_app_updated", entity_type="integration", entity_label=f"{FAMILIES[family].label} app")
    await db.commit()
    app = await core.oauth_app(db, org, family)
    return {"family": family, "configured": bool(app["client_id"]), "source": app["source"]}


@router.delete("/apps/{family}")
async def remove_app(family: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    if family not in FAMILIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider.")
    row = await core.toggle_row(db, principal.organization_id, f"app:{family}")
    if row is not None:
        await db.delete(row)
        await db.commit()
    app = await core.oauth_app(db, principal.organization_id, family)
    return {"family": family, "configured": bool(app["client_id"]), "source": app["source"]}


# ===========================================================================
# Public: OAuth callback and inbound webhooks
# ===========================================================================


def _back(path: str, **params: str) -> RedirectResponse:
    base = get_settings().app_base_url.rstrip("/")
    query = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(f"{base}{path}{'?' + query if query else ''}", status_code=status.HTTP_302_FOUND)


@public_router.get("/oauth/callback")
async def oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if not state:
        return _back("/integrations", error="The provider didn't send the sign-in back. Try connecting again.")
    if error or not code:
        from app.core.crypto import sha256
        from app.models.integration_hub import IntegrationOAuthState

        row = (await db.execute(select(IntegrationOAuthState).where(IntegrationOAuthState.state_hash == sha256(state)))).scalar_one_or_none()
        target = row.return_to if row and row.return_to else "/integrations"
        message = "You cancelled the sign-in." if error in {"access_denied", "user_cancelled_authorize"} else (error_description or error or "The sign-in didn't finish.")
        return _back(target, error=message[:300])
    db.sync_session.info["automation_disabled"] = True
    try:
        connection, row = await core.finish_oauth(db, state=state, code=code)
    except IntegrationError as exc:
        await db.commit()
        return _back("/integrations", error=str(exc)[:300])
    try:
        await call(connection.provider, "on_connected", db, connection.provider, connection)
    except IntegrationError as exc:
        connection.last_error = str(exc)
        core.log(db, connection.organization_id, connection.provider, "connect_followup", str(exc), level="warning", connection=connection)
    except Exception:  # noqa: BLE001 - the connection itself worked; report the follow-up problem
        logger.exception("on_connected failed for %s", connection.provider)
        connection.last_error = "Connected, but finishing the setup failed. Use Test to check."
    record_audit(db, organization_id=connection.organization_id, action="integration_connected", entity_type="integration",
                 entity_label=INTEGRATIONS[connection.provider].name, actor_user_id=row.user_id)
    await db.commit()
    return _back(row.return_to or f"/integrations/{connection.provider}", connected=connection.provider)


async def _dispatch_webhook(key: str, token: str, request: Request, db: AsyncSession) -> Response:
    if key not in INTEGRATIONS:
        return JSONResponse({"error": "unknown integration"}, status_code=404)
    handler = hook(key, "handle_webhook")
    if handler is None:
        return JSONResponse({"error": "not supported"}, status_code=404)
    db.sync_session.info["automation_disabled"] = False
    if token == "app":
        response = await handler(db, key, None, request)
        await db.commit()
        return response
    connection = (
        await db.execute(select(IntegrationConnection).where(IntegrationConnection.webhook_token == token, IntegrationConnection.provider == key))
    ).scalar_one_or_none()
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if not await core.is_enabled(db, connection.organization_id, key):
        # Answer politely so the sender doesn't retry forever, but do nothing.
        core.log(db, connection.organization_id, key, "webhook_ignored", "A webhook arrived while the integration was off; it was ignored.", level="warning", connection=connection)
        await db.commit()
        return JSONResponse({"ignored": "integration is off"}, status_code=200)
    response = await handler(db, key, connection, request)
    await db.commit()
    return response


@public_router.get("/{key}/webhook/{token}")
async def webhook_get(key: str, token: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _dispatch_webhook(key, token, request, db)


@public_router.post("/{key}/webhook/{token}")
async def webhook_post(key: str, token: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _dispatch_webhook(key, token, request, db)
