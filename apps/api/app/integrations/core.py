"""Shared plumbing for every integration.

* On/off: ``is_enabled`` is checked by every action, sync job and inbound
  webhook. Switching an integration off keeps its connections (so switching it
  back on resumes) but nothing is sent, synced or accepted while it's off.
* Connecting: ``start_oauth`` / ``finish_oauth`` run the OAuth 2.0
  authorization-code flow with PKCE where the provider supports it. The state
  and PKCE verifier are kept server-side and are single use.
* Calling providers: ``request`` adds the access token, refreshes it when it
  has expired (or the provider says it has), and turns failures into
  ``IntegrationError`` with a message people can act on.
* Everything notable goes to the integration's activity log (``log``).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt, sha256
from app.integrations.catalog import FAMILIES, INTEGRATIONS, IntegrationDef, OAuthFamily
from app.models.integration_hub import IntegrationConnection, IntegrationLog, IntegrationOAuthState, IntegrationToggle

logger = logging.getLogger("aiva.integrations")

STATE_MINUTES = 15
REFRESH_MARGIN = timedelta(minutes=2)


class IntegrationError(Exception):
    """A problem to show to the person, phrased for them."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def definition(key: str) -> IntegrationDef:
    found = INTEGRATIONS.get(key)
    if found is None:
        raise IntegrationError("Unknown integration.", 404)
    return found


# ---------------------------------------------------------------------------
# On/off
# ---------------------------------------------------------------------------


async def toggle_row(db: AsyncSession, organization_id: uuid.UUID, key: str) -> IntegrationToggle | None:
    return (
        await db.execute(select(IntegrationToggle).where(IntegrationToggle.organization_id == organization_id, IntegrationToggle.provider == key))
    ).scalar_one_or_none()


async def toggles(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, IntegrationToggle]:
    rows = (await db.execute(select(IntegrationToggle).where(IntegrationToggle.organization_id == organization_id))).scalars()
    return {row.provider: row for row in rows}


async def is_enabled(db: AsyncSession, organization_id: uuid.UUID, key: str) -> bool:
    row = await toggle_row(db, organization_id, key)
    if row is None or not row.enabled:
        return False
    item = INTEGRATIONS.get(key)
    if item is not None and item.depends_on:
        return await is_enabled(db, organization_id, item.depends_on)
    return True


async def require_enabled(db: AsyncSession, organization_id: uuid.UUID, key: str) -> None:
    if not await is_enabled(db, organization_id, key):
        raise IntegrationError(f"{definition(key).name} is turned off for this workspace. An admin can turn it on in Integrations.", 409)


async def workspace_config(db: AsyncSession, organization_id: uuid.UUID, key: str) -> dict[str, Any]:
    row = await toggle_row(db, organization_id, key)
    return dict(row.config or {}) if row else {}


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


async def connection_for(db: AsyncSession, organization_id: uuid.UUID, key: str, user_id: uuid.UUID | None = None) -> IntegrationConnection | None:
    query = select(IntegrationConnection).where(IntegrationConnection.organization_id == organization_id, IntegrationConnection.provider == key)
    query = query.where(IntegrationConnection.user_id == user_id) if user_id else query.where(IntegrationConnection.user_id.is_(None))
    return (await db.execute(query)).scalar_one_or_none()


async def active_connection(db: AsyncSession, organization_id: uuid.UUID, key: str, user_id: uuid.UUID | None = None) -> IntegrationConnection:
    """The connection to use for an action, or a clear error saying what's missing."""

    await require_enabled(db, organization_id, key)
    item = definition(key)
    lookup_key = item.depends_on or key
    connection = await connection_for(db, organization_id, lookup_key, user_id if INTEGRATIONS[lookup_key].personal else None)
    if connection is None:
        who = "Connect your account" if INTEGRATIONS[lookup_key].personal else "Ask an admin to connect it"
        raise IntegrationError(f"{INTEGRATIONS[lookup_key].name} isn't connected. {who} in Integrations.", 409)
    if connection.status == "needs_reauth":
        raise IntegrationError(f"{INTEGRATIONS[lookup_key].name} needs to be reconnected (the sign-in expired or was revoked).", 409)
    return connection


def secret(connection: IntegrationConnection | IntegrationToggle, name: str) -> str | None:
    value = (connection.secrets or {}).get(name)
    return decrypt(value) if value else None


def set_secret(connection: IntegrationConnection | IntegrationToggle, name: str, value: str | None) -> None:
    values = dict(connection.secrets or {})
    if value:
        values[name] = encrypt(value)
    else:
        values.pop(name, None)
    connection.secrets = values


def secrets_set(connection: IntegrationConnection | IntegrationToggle | None) -> dict[str, bool]:
    return {name: True for name in (connection.secrets or {})} if connection else {}


def new_webhook_token() -> str:
    return secrets.token_urlsafe(32)[:43]


def webhook_url(key: str, token: str | None) -> str | None:
    if not token:
        return None
    return f"{get_settings().app_base_url.rstrip('/')}/api/aiva/public/integrations/{key}/webhook/{token}"


def app_webhook_url(key: str) -> str:
    """For providers that send every workspace's events to one URL (Meta lead ads)."""

    return f"{get_settings().app_base_url.rstrip('/')}/api/aiva/public/integrations/{key}/webhook/app"


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


def log(
    db: AsyncSession,
    organization_id: uuid.UUID,
    key: str,
    action: str,
    message: str,
    *,
    level: str = "info",
    connection: IntegrationConnection | None = None,
    user_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        IntegrationLog(
            id=uuid.uuid4(),
            organization_id=organization_id,
            provider=key,
            connection_id=connection.id if connection is not None else None,
            user_id=user_id or (connection.user_id if connection is not None else None),
            level=level,
            action=action[:60],
            message=message[:4000],
            detail=detail or {},
        )
    )


# ---------------------------------------------------------------------------
# OAuth apps (server settings, or the workspace's own app)
# ---------------------------------------------------------------------------


def _family_settings(family: OAuthFamily) -> tuple[str, str]:
    settings = get_settings()
    return getattr(settings, family.settings_client_id, "") or "", getattr(settings, family.settings_client_secret, "") or ""


async def oauth_app(db: AsyncSession, organization_id: uuid.UUID, family_key: str) -> dict[str, Any]:
    """Client credentials for a provider family: the workspace's own app wins over the server's."""

    family = FAMILIES[family_key]
    row = await toggle_row(db, organization_id, f"app:{family_key}")
    own_id = (row.config or {}).get("client_id") if row else None
    own_secret = secret(row, "client_secret") if row else None
    if own_id and own_secret:
        return {"client_id": own_id, "client_secret": own_secret, "source": "workspace", "options": dict(row.config or {})}
    client_id, client_secret = _family_settings(family)
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret, "source": "server", "options": dict(row.config or {}) if row else {}}
    return {"client_id": None, "client_secret": None, "source": None, "options": dict(row.config or {}) if row else {}}


def redirect_uri() -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/api/aiva/public/integrations/oauth/callback"


def _endpoint(url: str, family_key: str, options: dict[str, Any]) -> str:
    settings = get_settings()
    tenant = options.get("tenant") or settings.microsoft_tenant_id or "common"
    environment = options.get("environment") or settings.docusign_environment or "demo"
    docusign_host = "account.docusign.com" if environment == "production" else "account-d.docusign.com"
    return url.replace("{tenant}", tenant).replace("{docusign_host}", docusign_host)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def start_oauth(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    key: str,
    return_to: str | None = None,
) -> str:
    item = definition(key)
    if item.family is None or item.auth not in {"oauth", "credentials"} or item.family not in FAMILIES:
        raise IntegrationError(f"{item.name} doesn't connect by signing in.")
    await require_enabled(db, organization_id, key)
    family = FAMILIES[item.family]
    app = await oauth_app(db, organization_id, family.key)
    if not app["client_id"]:
        raise IntegrationError(
            f"{family.label} sign-in isn't set up on this server yet. An admin can add your own {family.label} app's client ID and secret on this page.",
            409,
        )
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce() if family.pkce else (None, None)
    db.add(
        IntegrationOAuthState(
            id=uuid.uuid4(),
            organization_id=organization_id,
            state_hash=sha256(state),
            provider=key,
            user_id=user_id,
            personal=item.personal or key == "microsoft_teams",
            code_verifier=encrypt(verifier) if verifier else None,
            return_to=(return_to if return_to and return_to.startswith("/") and not return_to.startswith("//") else f"/integrations/{key}")[:500],
            expires_at=utcnow() + timedelta(minutes=STATE_MINUTES),
        )
    )
    await db.commit()
    params: dict[str, str] = {
        "client_id": app["client_id"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "state": state,
        **family.extra_params,
    }
    if item.scopes:
        params["scope"] = family.scope_separator.join(item.scopes)
    if challenge:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    if family.key == "microsoft":
        params["response_mode"] = "query"
    return f"{_endpoint(family.authorize_url, family.key, app['options'])}?{urlencode(params)}"


async def _token_request(family: OAuthFamily, app: dict[str, Any], data: dict[str, str]) -> dict[str, Any]:
    url = _endpoint(family.token_url, family.key, app["options"])
    headers = {"Accept": "application/json"}
    auth = None
    if family.basic_auth:
        auth = (app["client_id"], app["client_secret"])
    else:
        data = {**data, "client_id": app["client_id"], "client_secret": app["client_secret"]}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, data=data, headers=headers, auth=auth)
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Couldn't reach {family.label} ({exc.__class__.__name__}). Try again.", 502) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or payload.get("ok") is False or "error" in payload and not payload.get("access_token"):
        reason = payload.get("error_description") or payload.get("error") or payload.get("message") or response.text[:200]
        if isinstance(reason, dict):
            reason = reason.get("message") or str(reason)
        raise IntegrationError(f"{family.label} refused the sign-in: {reason}", 400)
    return payload


async def finish_oauth(db: AsyncSession, *, state: str, code: str) -> tuple[IntegrationConnection, IntegrationOAuthState]:
    row = (await db.execute(select(IntegrationOAuthState).where(IntegrationOAuthState.state_hash == sha256(state)))).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        raise IntegrationError("That sign-in link has expired. Start connecting again.", 400)
    row.used_at = utcnow()
    item = definition(row.provider)
    family = FAMILIES[item.family or ""]
    app = await oauth_app(db, row.organization_id, family.key)
    if not app["client_id"]:
        raise IntegrationError(f"{family.label} sign-in isn't set up.", 409)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()}
    if row.code_verifier:
        data["code_verifier"] = decrypt(row.code_verifier) or ""
    tokens = await _token_request(family, app, data)

    from app.integrations import identity

    who = await identity.identify(family.key, item.key, tokens, app)
    user_id = row.user_id if row.personal else None
    connection = await connection_for(db, row.organization_id, item.key, user_id)
    if connection is None:
        connection = IntegrationConnection(id=uuid.uuid4(), organization_id=row.organization_id, provider=item.key, user_id=user_id)
        db.add(connection)
    connection.status = "connected"
    connection.last_error = None
    connection.account_label = (who.get("label") or "")[:320] or None
    connection.external_account_id = (str(who.get("external_id") or ""))[:300] or None
    connection.access_token = encrypt(who.get("access_token") or tokens.get("access_token"))
    refresh = who.get("refresh_token") or tokens.get("refresh_token")
    if refresh:
        connection.refresh_token = encrypt(refresh)
    expires_in = who.get("expires_in", tokens.get("expires_in"))
    connection.token_expires_at = utcnow() + timedelta(seconds=int(expires_in)) if expires_in else None
    granted = tokens.get("scope")
    connection.scopes = (granted.replace(",", " ").split() if isinstance(granted, str) else list(item.scopes))[:60]
    connection.config = {**(connection.config or {}), **(who.get("config") or {})}
    for name, value in (who.get("secrets") or {}).items():
        set_secret(connection, name, value)
    connection.connected_by_user_id = row.user_id
    await db.flush()
    log(db, row.organization_id, item.key, "connected", f"Connected {connection.account_label or item.name}.", level="success", connection=connection, user_id=row.user_id)
    return connection, row


async def refresh_access_token(db: AsyncSession, connection: IntegrationConnection) -> str:
    item = definition(connection.provider)
    family = FAMILIES.get(item.family or "")
    refresh = decrypt(connection.refresh_token) if connection.refresh_token else None
    if family is None or not refresh:
        connection.status = "needs_reauth"
        raise IntegrationError(f"{item.name} needs to be reconnected.", 409)
    app = await oauth_app(db, connection.organization_id, family.key)
    if not app["client_id"]:
        raise IntegrationError(f"{family.label} sign-in isn't set up on this server.", 409)
    try:
        tokens = await _token_request(family, app, {"grant_type": "refresh_token", "refresh_token": refresh})
    except IntegrationError as exc:
        if exc.status_code == 400:
            connection.status = "needs_reauth"
            connection.last_error = str(exc)
            log(db, connection.organization_id, connection.provider, "token_refresh_failed", str(exc), level="error", connection=connection)
            await db.commit()
            raise IntegrationError(f"{item.name} needs to be reconnected (the sign-in expired or was revoked).", 409) from exc
        raise
    connection.access_token = encrypt(tokens["access_token"])
    if tokens.get("refresh_token"):
        connection.refresh_token = encrypt(tokens["refresh_token"])
    connection.token_expires_at = utcnow() + timedelta(seconds=int(tokens["expires_in"])) if tokens.get("expires_in") else None
    connection.status = "connected"
    return tokens["access_token"]


async def access_token(db: AsyncSession, connection: IntegrationConnection) -> str:
    token = decrypt(connection.access_token) if connection.access_token else None
    if token and (connection.token_expires_at is None or connection.token_expires_at - REFRESH_MARGIN > utcnow()):
        return token
    return await refresh_access_token(db, connection)


def _provider_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)[:300]
        for key in ("error_description", "message", "error_summary", "error", "detail"):
            if data.get(key):
                return str(data[key])[:300]
    return str(data)[:300]


async def request(
    db: AsyncSession,
    connection: IntegrationConnection,
    method: str,
    url: str,
    *,
    token: str | None = None,
    retry_auth: bool = True,
    timeout: float = 25,
    **kwargs: Any,
) -> httpx.Response:
    """Call a provider API as this connection. Raises IntegrationError on failure."""

    bearer = token or await access_token(db, connection)
    headers = {"Authorization": f"Bearer {bearer}", **(kwargs.pop("headers", None) or {})}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Couldn't reach {definition(connection.provider).name} ({exc.__class__.__name__}).", 502) from exc
    if response.status_code == 401 and retry_auth and token is None and connection.refresh_token:
        await refresh_access_token(db, connection)
        return await request(db, connection, method, url, retry_auth=False, timeout=timeout, headers=headers | {"Authorization": f"Bearer {decrypt(connection.access_token)}"}, **kwargs)
    if response.status_code >= 400:
        message = _provider_message(response)
        if response.status_code == 401:
            connection.status = "needs_reauth"
        connection.last_error = f"{response.status_code}: {message}"[:1000]
        raise IntegrationError(f"{definition(connection.provider).name} said: {message}", 502 if response.status_code >= 500 else 400)
    connection.last_used_at = utcnow()
    return response
