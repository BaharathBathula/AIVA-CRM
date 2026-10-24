"""Microsoft Entra ID and Okta: single sign-on set-up and SCIM 2.0 provisioning.

* Credentials: Entra needs the directory (tenant) ID; an app registration (client
  ID + secret with Microsoft Graph ``Group.Read.All``) is optional and only used to
  list groups for mapping. Okta needs the Okta domain; an API token is optional,
  for the same reason. Both are checked against the provider when saved.
* Single sign-on reuses the workspace's OpenID Connect connection
  (Settings › Single sign-on): the integration fills in the exact issuer and
  redirect URI (routes in ``app/api/routes/int_identity.py``).
* SCIM 2.0 lives at ``{APP_BASE_URL}/api/aiva/public/scim/v2`` and is shared by
  both providers; the token decides the workspace and the provider
  (``_idp_store``). Switching the integration off pauses provisioning (requests
  are refused); nobody is removed. Disconnecting deletes the token.
* Group mapping (default role, group → role/team, automatic teams) is kept in the
  integration's workspace settings and applied whenever group membership changes.
* Verified domains and "Require single sign-on" are workspace-wide (``_idp_policy``).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _idp_http as http
from app.integrations.providers import _idp_policy as policy
from app.integrations.providers import _idp_store as store
from app.models.identity import SsoConnection
from app.services import oidc

KEYS = store.KEYS
FEATURE = "sso"
DEFAULT_NAMES = {"entra_id": "Microsoft Entra ID", "okta": "Okta"}


# ---------------------------------------------------------------------------
# Helpers shared with the routes
# ---------------------------------------------------------------------------


def issuer_for(key: str, config: dict[str, Any], server: str | None = None) -> str | None:
    """The exact OpenID Connect issuer AIVA expects for this provider."""

    if key == "entra_id":
        tenant = str(config.get("tenant_id") or "")
        return http.entra_issuer(tenant) if http.GUID_RE.match(tenant) else None
    domain = str(config.get("okta_domain") or "")
    if not domain:
        return None
    return http.okta_issuer(domain, server if server is not None else (config.get("authorization_server") or None))


async def sso_connection(db: AsyncSession, organization_id: uuid.UUID) -> SsoConnection | None:
    return (await db.execute(select(SsoConnection).where(SsoConnection.organization_id == organization_id))).scalar_one_or_none()


def test_sign_in_url(key: str, email: str | None = None) -> str:
    query = f"?intent=login&next=/integrations/{key}"
    return f"/api/session/oauth/sso/start{query}" + (f"&email={email}" if email else "")


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    if key == "entra_id":
        tenant = http.clean_tenant(values.get("tenant_id") or "")
        guid, _ = await http.entra_tenant(tenant)
        client_id = (values.get("client_id") or "").strip()
        secret = (values.get("client_secret") or "").strip()
        if bool(client_id) != bool(secret):
            raise IntegrationError("Enter both the application (client) ID and a client secret, or leave both empty.", 422)
        if client_id and not http.GUID_RE.match(client_id):
            raise IntegrationError("The application (client) ID looks like 00000000-0000-0000-0000-000000000000.", 422)
        if client_id:
            await http.graph_token(guid, client_id, secret)
        label = tenant if tenant != guid else f"Tenant {guid[:8]}…"
        return {"label": label, "external_id": guid, "config": {"tenant_id": guid, "tenant_name": tenant if tenant != guid else None, "directory_api": bool(client_id)}}
    domain = http.clean_okta_domain(values.get("okta_domain") or "")
    await http.discover(http.okta_issuer(domain))
    token = (values.get("api_token") or "").strip()
    if token:
        await http.okta_groups(domain, token, limit=1)
    return {"label": domain, "external_id": domain, "config": {"okta_domain": domain, "directory_api": bool(token)}}


async def on_connected(db: AsyncSession, key: str, connection) -> None:
    core.log(
        db, connection.organization_id, key, "setup_next",
        "Next: set up single sign-on with the values on this page, then create a SCIM token for provisioning.",
        connection=connection,
    )


async def on_disconnect(db: AsyncSession, key: str, connection) -> None:
    if store.token_meta(connection) is not None:
        core.log(
            db, connection.organization_id, key, "scim_stopped",
            "Provisioning stopped: the SCIM token no longer works. People, roles and teams in AIVA stay as they are.",
            level="warning",
        )


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    connection = await core.connection_for(db, organization_id, key)
    if connection is not None and store.token_meta(connection) is not None:
        core.log(
            db, organization_id, key, "scim_resumed" if enabled else "scim_paused",
            "Provisioning resumed." if enabled else f"Provisioning paused: {store.name_of(key)} requests are refused until it's turned on again. Nobody was removed.",
            level="info" if enabled else "warning", connection=connection,
        )


async def test(db: AsyncSession, key: str, connection, principal) -> str:
    config = connection.config or {}
    issuer = issuer_for(key, config)
    if not issuer:
        raise IntegrationError("Save the connection details first.")
    await http.discover(issuer)
    parts = [f"Sign-on discovery works ({issuer})."]
    if config.get("directory_api"):
        groups = await directory_groups(db, key, connection, limit=5)
        parts.append(f"Directory API works ({len(groups)}{'+' if len(groups) >= 5 else ''} groups visible).")
    info = store.token_info(connection)
    if info is None:
        parts.append("No SCIM token yet.")
    elif info["last_used_at"]:
        parts.append(f"SCIM last used {info['last_used_at'][:16].replace('T', ' ')} UTC.")
    else:
        parts.append("SCIM token created; not used yet.")
    return " ".join(parts)


async def directory_groups(db: AsyncSession, key: str, connection, search: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    config = connection.config or {}
    if key == "entra_id":
        client_id = str(config.get("client_id") or "")
        secret = core.secret(connection, "client_secret")
        if not client_id or not secret:
            raise IntegrationError("Add an app registration (application ID and secret) to list Entra groups, or map the groups provisioning pushes.", 409)
        return await http.graph_groups(str(config.get("tenant_id")), client_id, secret, search, limit)
    token = core.secret(connection, "api_token")
    if not token:
        raise IntegrationError("Add an Okta API token to list Okta groups, or map the groups provisioning pushes.", 409)
    return await http.okta_groups(str(config.get("okta_domain")), token, search, limit)


async def clean_settings(db: AsyncSession, key: str, principal, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if personal:
        raise IntegrationError("These settings are for the whole workspace.")
    if getattr(principal, "api_key_id", None) is not None or getattr(principal, "support_grant_id", None) is not None:
        raise IntegrationError("Change provisioning settings in the AIVA app while signed in as yourself.", 403)
    return await store.clean_mapping(db, principal.organization_id, config)


async def detail(db: AsyncSession, key: str, principal) -> dict[str, Any]:
    org = principal.organization_id
    manage = principal.can("admin.settings")
    connection = await core.connection_for(db, org, key)
    config = (connection.config or {}) if connection is not None else {}
    settings = await core.workspace_config(db, org, key)
    issuer = issuer_for(key, config)
    sso = await sso_connection(db, org)
    sso_matches = bool(sso and issuer and sso.issuer.rstrip("/") == issuer.rstrip("/"))
    default_role, mappings, auto_teams = store.mapping_settings(settings)
    out: dict[str, Any] = {
        "connected": connection is not None,
        "scim": {"base_url": store.base_url(), "has_token": store.token_meta(connection) is not None},
        "sso": {
            "redirect_uri": oidc.redirect_uri("sso"),
            "issuer": issuer,
            "configured": sso is not None,
            "active": bool(sso and sso.is_active),
            "matches": sso_matches,
            "login_url": f"{get_settings().app_base_url.rstrip('/')}/login",
        },
        "stats": await store.stats(db, org, key),
    }
    if not manage:
        return out
    policy_data = await policy.load(db, org)
    out["scim"]["token"] = store.token_info(connection)
    out["sso"].update(
        {
            "connection": (
                {
                    "name": sso.name, "issuer": sso.issuer, "client_id": sso.client_id, "has_secret": bool(sso.client_secret),
                    "domains": sso.domains or [], "default_role": sso.default_role, "auto_provision": sso.auto_provision,
                    "is_active": sso.is_active, "updated_at": sso.updated_at.isoformat() if sso.updated_at else None,
                }
                if sso is not None
                else None
            ),
            "authorization_server": config.get("authorization_server") or "",
            "test_url": test_sign_in_url(key),
            "default_name": DEFAULT_NAMES[key],
        }
    )
    roles = await store.valid_roles(db, org)
    out.update(
        {
            "mapping": {"default_role": default_role, "group_mappings": mappings, "auto_teams": auto_teams},
            "roles": [{"key": k, "name": v} for k, v in roles.items()],
            "teams": [{"id": k, "name": v} for k, v in (await store.teams(db, org)).items()],
            "groups": await store.groups_overview(db, org, key),
            "directory_api": bool(config.get("directory_api")),
            "provisioning": await store.provisioning_log(db, org, key, 25),
            "policy": {"require_sso": policy_data["require_sso"], "verified": policy_data["verified"]},
        }
    )
    return out
