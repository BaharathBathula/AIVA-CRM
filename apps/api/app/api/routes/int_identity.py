"""Integration endpoints: identity (Microsoft Entra ID, Okta) and the SCIM 2.0 service.

Admins (``admin.settings``, plan feature ``sso``):

* ``/identity/policy`` — verified domains and "Require single sign-on" (whole workspace).
* ``/identity/domains`` — add a domain, check its DNS TXT record, remove it.
* ``/identity/{key}/sso`` — create or update the workspace's single sign-on connection
  from the integration (exact issuer for the tenant / Okta org), and check it.
* ``/identity/{key}/scim-token`` — create (shown once), rotate or revoke the SCIM token.
* ``/identity/{key}/mapping`` — default role, group → role/team, automatic teams.
* ``/identity/{key}/directory-groups`` — live groups from Microsoft Graph or the Okta API.
* ``/identity/{key}/provisioning`` — counts and the provisioning log.

Identity providers (bearer SCIM token; the token alone picks the workspace):
``/public/scim/v2/{ServiceProviderConfig,ResourceTypes,Schemas,Users,Groups}``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    Principal,
    client_ip,
    require_feature,
    require_permission,
)
from app.core.context import bind_session_context
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _idp_http as http
from app.integrations.providers import _idp_policy as policy
from app.integrations.providers import _idp_scim as scim
from app.integrations.providers import _idp_store as store
from app.integrations.providers import identity
from app.models.identity import UserIdentity
from app.models.membership import OrganizationMembership
from app.models.user import User
from app.services import oidc
from app.services.automation import record_audit

logger = logging.getLogger("aiva.integrations.identity")

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

ADMIN = Depends(require_permission("admin.settings"))
FEATURE = [Depends(require_feature(identity.FEATURE))]
NO_STORE = {"Cache-Control": "no-store"}


async def _manager(principal: Principal = Depends(require_permission("admin.settings"))) -> Principal:
    """Admins changing sign-in and provisioning: from the app only (not API keys), never AIVA support."""

    if principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AIVA support can't change single sign-on, provisioning or security settings.")
    if principal.api_key_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Manage single sign-on and provisioning in the AIVA app, not with an API key.")
    return principal


MANAGE = Depends(_manager)


def _key(key: str) -> str:
    if key not in identity.KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown identity provider.")
    return key


def _http(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


_CALLS: dict[str, deque] = defaultdict(deque)


def _throttle(key: str, limit: int, window: float = 60.0) -> None:
    now = time.monotonic()
    bucket = _CALLS[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many checks in a short time. Wait a minute and try again.")
    bucket.append(now)
    if len(_CALLS) > 10_000:
        _CALLS.clear()


# ===========================================================================
# Workspace policy: verified domains and "Require single sign-on"
# ===========================================================================


async def _covered_members(db: AsyncSession, organization_id: uuid.UUID, domains: list[str]) -> int:
    if not domains:
        return 0
    return (
        await db.execute(
            select(func.count())
            .select_from(OrganizationMembership)
            .join(User, User.id == OrganizationMembership.user_id)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.status == "active",
                OrganizationMembership.role != "owner",
                func.split_part(User.email, "@", 2).in_(domains),
            )
        )
    ).scalar_one()


async def _signed_in_with_sso(db: AsyncSession, user_id: uuid.UUID, connection_id: uuid.UUID) -> bool:
    return bool(
        (await db.execute(select(UserIdentity.id).where(UserIdentity.user_id == user_id, UserIdentity.provider == f"sso:{connection_id}").limit(1))).first()
    )


async def _policy_out(db: AsyncSession, principal: Principal) -> dict[str, Any]:
    org = principal.organization_id
    data = await policy.load(db, org)
    sso = await identity.sso_connection(db, org)
    routed = set(sso.domains or []) if sso is not None and sso.is_active else set()
    enforced = [d for d in data["verified"] if d in routed]
    problems: list[str] = []
    if not data["verified"]:
        problems.append("Verify at least one email domain.")
    if sso is None:
        problems.append("Set up single sign-on (step 1 on the Microsoft Entra ID or Okta page).")
    elif not sso.is_active:
        problems.append("Single sign-on is turned off.")
    elif data["verified"] and not enforced:
        problems.append("None of your verified domains are in your single sign-on domains yet.")
    my_domain = policy.email_domain(principal.email)
    return {
        "require_sso": data["require_sso"],
        "enforced": bool(data["require_sso"] and enforced),
        "enforced_domains": enforced,
        "changed_at": data.get("require_sso_changed_at"),
        "domains": [
            {
                "domain": d["domain"],
                "status": d.get("status") or "pending",
                "record_name": d["domain"],
                "alternate_record_name": f"_aiva-verification.{d['domain']}",
                "record_value": policy.record_value(d["token"]),
                "added_at": d.get("added_at"),
                "checked_at": d.get("checked_at"),
                "verified_at": d.get("verified_at"),
                "error": d.get("error"),
                "in_sso": d["domain"] in set(sso.domains or []) if sso is not None else False,
            }
            for d in data["domains"]
        ],
        "sso": (
            {"name": sso.name, "issuer": sso.issuer, "active": sso.is_active, "domains": sso.domains or []}
            if sso is not None
            else None
        ),
        "covered_members": await _covered_members(db, org, enforced),
        "problems": problems,
        "you": {
            "owner": principal.role == "owner",
            "covered": my_domain in enforced,
            "signed_in_with_sso": bool(sso and await _signed_in_with_sso(db, principal.user_id, sso.id)),
        },
    }


@router.get("/identity/policy", dependencies=FEATURE)
async def get_policy(principal: Principal = ADMIN, db: AsyncSession = Depends(get_db)):
    return await _policy_out(db, principal)


class PolicyPayload(BaseModel):
    require_sso: bool


@router.put("/identity/policy", dependencies=FEATURE)
async def set_policy(payload: PolicyPayload, request: Request, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    data = await policy.load(db, org)
    if payload.require_sso and not data["require_sso"]:
        sso = await identity.sso_connection(db, org)
        if not data["verified"]:
            raise HTTPException(status.HTTP_409_CONFLICT, "Verify at least one email domain first.")
        if sso is None or not sso.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "Set up single sign-on and turn it on first.")
        routed = [d for d in data["verified"] if d in (sso.domains or [])]
        if not routed:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Add {', '.join(data['verified'])} to your single sign-on domains first.")
        if principal.role != "owner" and policy.email_domain(principal.email) in routed and not await _signed_in_with_sso(db, principal.user_id, sso.id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Sign in once with single sign-on yourself (Test sign-in) before requiring it, so you can't be locked out. Owners can always use their password.",
            )
    if payload.require_sso != data["require_sso"]:
        data["require_sso"] = payload.require_sso
        data["require_sso_changed_at"] = store.utcnow().isoformat()
        data["require_sso_changed_by"] = str(principal.user_id)
        await policy.save(db, org, data, principal.user_id)
        record_audit(
            db, organization_id=org, action="require_sso_on" if payload.require_sso else "require_sso_off", entity_type="security",
            entity_label="Require single sign-on", changes={"require_sso": {"from": not payload.require_sso, "to": payload.require_sso}, "domains": data["verified"]},
            ip=client_ip(request),
        )
        await db.commit()
    return await _policy_out(db, principal)


class DomainPayload(BaseModel):
    domain: str = Field(min_length=3, max_length=253)


@router.post("/identity/domains", dependencies=FEATURE)
async def add_domain(payload: DomainPayload, request: Request, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    from app.services.workspace_settings import clean_domain

    try:
        domain = clean_domain(payload.domain)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if domain in oidc.PUBLIC_EMAIL_DOMAINS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{domain} is a public email provider; only your company's own domains can be verified.")
    org = principal.organization_id
    data = await policy.load(db, org)
    if not any(d["domain"] == domain for d in data["domains"]):
        if len(data["domains"]) >= policy.MAX_DOMAINS:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Verify at most {policy.MAX_DOMAINS} domains.")
        data["domains"].append({"domain": domain, "token": policy.new_token(), "status": "pending", "added_at": store.utcnow().isoformat(), "added_by": str(principal.user_id)})
        await policy.save(db, org, data, principal.user_id)
        record_audit(db, organization_id=org, action="domain_added", entity_type="security", entity_label=domain, changes={"domain": domain}, ip=client_ip(request))
        await db.commit()
    return await _policy_out(db, principal)


@router.post("/identity/domains/{domain}/verify", dependencies=FEATURE)
async def verify_domain(domain: str, request: Request, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    _throttle(f"dns:{org}", 20)
    data = await policy.load(db, org)
    entry = next((d for d in data["domains"] if d["domain"] == domain.strip().lower()), None)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Add the domain first.")
    ok, message = await policy.check_domain(entry["domain"], entry["token"])
    now = store.utcnow().isoformat()
    before = entry.get("status")
    entry["checked_at"] = now
    if ok:
        entry["status"] = "verified"
        entry["verified_at"] = entry.get("verified_at") if before == "verified" else now
        entry["error"] = None
    else:
        # A domain that was verified stays verified through a temporary DNS problem; re-checks only confirm.
        entry["status"] = "verified" if before == "verified" else "failed"
        entry["error"] = message
    await policy.save(db, org, data, principal.user_id)
    if entry["status"] != before:
        record_audit(db, organization_id=org, action="domain_verified" if ok else "domain_check_failed", entity_type="security",
                     entity_label=entry["domain"], changes={"status": {"from": before, "to": entry["status"]}}, ip=client_ip(request))
    await db.commit()
    out = await _policy_out(db, principal)
    out["check"] = {"domain": entry["domain"], "ok": ok, "message": message}
    return out


@router.delete("/identity/domains/{domain}", dependencies=FEATURE)
async def remove_domain(domain: str, request: Request, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    data = await policy.load(db, org)
    domain = domain.strip().lower()
    entry = next((d for d in data["domains"] if d["domain"] == domain), None)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That domain isn't on the list.")
    if data["require_sso"] and entry.get("status") == "verified" and [d for d in data["verified"] if d != domain] == []:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn off Require single sign-on before removing your last verified domain.")
    data["domains"] = [d for d in data["domains"] if d["domain"] != domain]
    await policy.save(db, org, data, principal.user_id)
    record_audit(db, organization_id=org, action="domain_removed", entity_type="security", entity_label=domain, changes={"domain": domain}, ip=client_ip(request))
    await db.commit()
    return await _policy_out(db, principal)


# ===========================================================================
# Single sign-on from the integration
# ===========================================================================


async def _connection(db: AsyncSession, principal: Principal, key: str):
    connection = await core.connection_for(db, principal.organization_id, key)
    if connection is None:
        what = "directory (tenant) ID" if key == "entra_id" else "Okta domain"
        raise HTTPException(status.HTTP_409_CONFLICT, f"Connect {store.name_of(key)} first: enter your {what}.")
    return connection


class SsoSetup(BaseModel):
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, max_length=2000)
    domains: list[str] = Field(min_length=1, max_length=20)
    default_role: str = Field(default="member", max_length=60)
    auto_provision: bool = False
    is_active: bool = True
    name: str | None = Field(default=None, max_length=120)
    authorization_server: str | None = Field(default=None, max_length=64)  # Okta only: "" (org) or e.g. "default"


@router.put("/identity/{key}/sso", dependencies=FEATURE)
async def setup_sso(key: str, payload: SsoSetup, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    """Create or update the workspace's single sign-on with this provider's exact values (reuses Settings › Single sign-on)."""

    from app.api.routes import oauth

    key = _key(key)
    connection = await _connection(db, principal, key)
    config = dict(connection.config or {})
    server = None
    if key == "okta":
        try:
            server = http.clean_server(payload.authorization_server)
        except IntegrationError as exc:
            raise _http(exc) from exc
    issuer = identity.issuer_for(key, config, server)
    if not issuer:
        raise HTTPException(status.HTTP_409_CONFLICT, "Save the connection details again; the tenant couldn't be read.")
    if key == "entra_id" and not http.GUID_RE.match(payload.client_id.strip()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use the Application (client) ID of the AIVA sign-in app registration (a GUID).")
    saved = await oauth.save_sso(
        oauth.SsoPayload(
            name=(payload.name or identity.DEFAULT_NAMES[key]).strip() or identity.DEFAULT_NAMES[key],
            issuer=issuer,
            client_id=payload.client_id.strip(),
            client_secret=(payload.client_secret or "").strip() or None,
            domains=payload.domains,
            default_role=payload.default_role,
            auto_provision=payload.auto_provision,
            is_active=payload.is_active,
        ),
        principal,
        db,
    )
    if key == "okta":
        connection.config = {**config, "authorization_server": server or ""}
    core.log(db, principal.organization_id, key, "sso_saved", f"Single sign-on saved ({issuer}, {', '.join(saved['domains'])}).",
             level="success", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"connection": saved, "issuer": issuer}


class SsoCheck(BaseModel):
    authorization_server: str | None = Field(default=None, max_length=64)


@router.post("/identity/{key}/sso/check", dependencies=FEATURE)
async def check_sso(key: str, payload: SsoCheck | None = None, principal: Principal = ADMIN, db: AsyncSession = Depends(get_db)):
    key = _key(key)
    _throttle(f"sso:{principal.organization_id}", 30)
    connection = await _connection(db, principal, key)
    try:
        server = http.clean_server(payload.authorization_server) if key == "okta" and payload else None
        issuer = identity.issuer_for(key, connection.config or {}, server)
        if not issuer:
            raise IntegrationError("Save the connection details first.", 409)
        doc = await http.discover(issuer)
    except IntegrationError as exc:
        raise _http(exc) from exc
    sso = await identity.sso_connection(db, principal.organization_id)
    scopes = doc.get("scopes_supported") or []
    return {
        "issuer": doc.get("issuer"),
        "authorization_endpoint": doc.get("authorization_endpoint"),
        "token_endpoint": doc.get("token_endpoint"),
        "scopes_ok": not scopes or all(s in scopes for s in ("openid", "email", "profile")),
        "saved": bool(sso and sso.issuer.rstrip("/") == str(doc.get("issuer") or "").rstrip("/")),
        "redirect_uri": oidc.redirect_uri("sso"),
    }


# ===========================================================================
# SCIM token, mapping, directory groups, provisioning log
# ===========================================================================


@router.post("/identity/{key}/scim-token", dependencies=FEATURE)
async def create_token(key: str, request: Request, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    """A new SCIM token (replaces the current one). The token is in this response only."""

    key = _key(key)
    if not await core.is_enabled(db, principal.organization_id, key):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Turn {store.name_of(key)} on first.")
    connection = await _connection(db, principal, key)
    rotated = store.token_meta(connection) is not None
    token = store.issue_token(connection, principal.user_id, principal.full_name or principal.email)
    message = "SCIM token replaced; the previous token stopped working." if rotated else "SCIM token created."
    core.log(db, principal.organization_id, key, "scim_token_rotated" if rotated else "scim_token_created", message, level="success", connection=connection, user_id=principal.user_id)
    record_audit(db, organization_id=principal.organization_id, action="scim_token_rotated" if rotated else "scim_token_created", entity_type="integration",
                 entity_label=store.name_of(key), changes={"hint": store.token_info(connection)["hint"]}, ip=client_ip(request))
    await db.commit()
    return JSONResponse({"token": token, "info": store.token_info(connection), "base_url": store.base_url()}, headers=NO_STORE)


@router.delete("/identity/{key}/scim-token", dependencies=FEATURE)
async def revoke_token(key: str, request: Request, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    key = _key(key)
    connection = await _connection(db, principal, key)
    if store.revoke_token(connection):
        core.log(db, principal.organization_id, key, "scim_token_revoked", "SCIM token revoked; provisioning stopped. Nobody was removed.", level="warning", connection=connection, user_id=principal.user_id)
        record_audit(db, organization_id=principal.organization_id, action="scim_token_revoked", entity_type="integration", entity_label=store.name_of(key), ip=client_ip(request))
    await db.commit()
    return {"info": None}


class MappingPayload(BaseModel):
    default_role: str | None = Field(default=None, max_length=60)
    group_mappings: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    auto_teams: bool | None = None
    apply: bool = True


@router.put("/identity/{key}/mapping", dependencies=FEATURE)
async def save_mapping(key: str, payload: MappingPayload, request: Request, principal: Principal = MANAGE, db: AsyncSession = Depends(get_db)):
    from app.models.integration_hub import IntegrationToggle

    key = _key(key)
    org = principal.organization_id
    raw = {k: v for k, v in payload.model_dump(exclude={"apply"}).items() if v is not None}
    try:
        cleaned = await store.clean_mapping(db, org, raw)
    except IntegrationError as exc:
        raise _http(exc) from exc
    toggle = await core.toggle_row(db, org, key)
    if toggle is None:
        toggle = IntegrationToggle(id=uuid.uuid4(), organization_id=org, provider=key, enabled=False)
        db.add(toggle)
    toggle.config = {**(toggle.config or {}), **cleaned}
    toggle.updated_by_user_id = principal.user_id
    await db.flush()
    connection = await core.connection_for(db, org, key)
    changed = 0
    if payload.apply:
        ctx = store.Ctx(org=org, key=key, connection=connection, settings=dict(toggle.config), ip=client_ip(request))
        changed = await store.recompute(db, ctx, None)
    core.log(db, org, key, "mapping_saved", "Group mapping saved" + (f"; roles or teams changed for {changed}." if changed else "."), connection=connection, user_id=principal.user_id)
    record_audit(db, organization_id=org, action="scim_mapping_saved", entity_type="integration", entity_label=store.name_of(key),
                 changes={"default_role": toggle.config.get("default_role"), "mappings": len(toggle.config.get("group_mappings") or []), "auto_teams": bool(toggle.config.get("auto_teams")), "changed": changed},
                 ip=client_ip(request))
    await db.commit()
    default_role, mappings, auto_teams = store.mapping_settings(toggle.config)
    return {"mapping": {"default_role": default_role, "group_mappings": mappings, "auto_teams": auto_teams}, "changed": changed}


@router.get("/identity/{key}/directory-groups", dependencies=FEATURE)
async def directory_groups(key: str, search: str | None = Query(default=None, max_length=100), principal: Principal = ADMIN, db: AsyncSession = Depends(get_db)):
    key = _key(key)
    _throttle(f"groups:{principal.organization_id}", 30)
    connection = await _connection(db, principal, key)
    try:
        items = await identity.directory_groups(db, key, connection, search)
    except IntegrationError as exc:
        raise _http(exc) from exc
    return {"items": items, "source": "Microsoft Graph" if key == "entra_id" else "Okta API"}


@router.get("/identity/{key}/provisioning", dependencies=FEATURE)
async def provisioning(key: str, limit: int = Query(default=100, ge=1, le=500), principal: Principal = ADMIN, db: AsyncSession = Depends(get_db)):
    key = _key(key)
    org = principal.organization_id
    return {"stats": await store.stats(db, org, key), "items": await store.provisioning_log(db, org, key, limit), "groups": await store.groups_overview(db, org, key)}


# ===========================================================================
# SCIM 2.0 service (Microsoft Entra ID and Okta)
# ===========================================================================

Handler = Callable[[store.Ctx], Awaitable[Response]]


async def _scim(request: Request, db: AsyncSession, writing: bool, handler: Handler) -> Response:
    ip = client_ip(request)
    try:
        connection = await store.authenticate(db, request.headers.get("authorization"), ip)
        settings = await store.check_access(db, connection, writing)
        bind_session_context(db, actor_id=None, organization_id=connection.organization_id, ip=ip, user_agent=request.headers.get("user-agent"))
        ctx = store.Ctx(org=connection.organization_id, key=connection.provider, connection=connection, settings=settings, ip=ip)
        response = await handler(ctx)
        await db.commit()
        return response
    except scim.ScimError as exc:
        await db.rollback()
        return scim.error_response(exc)
    except IntegrityError:
        await db.rollback()
        return scim.error_response(scim.ScimError(409, "That conflicts with an existing user or group.", "uniqueness"))
    except Exception:
        logger.exception("SCIM request failed")
        await db.rollback()
        return scim.error_response(scim.ScimError(500, "AIVA couldn't complete this request. Try again later."))


async def _body(request: Request) -> dict[str, Any]:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > scim.MAX_BODY_BYTES:
        raise scim.ScimError(413, "The request is too large.", "tooLarge")
    return scim.parse_body(await request.body())


def _docs(key: str) -> str:
    from app.integrations.catalog import INTEGRATIONS

    return INTEGRATIONS[key].docs_url


@public_router.get("/scim/v2/ServiceProviderConfig")
async def scim_config(request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        return scim.respond(scim.service_provider_config(store.base_url(), _docs(ctx.key)))

    return await _scim(request, db, False, run)


@public_router.get("/scim/v2/ResourceTypes")
async def scim_resource_types(request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        items = scim.resource_types(store.base_url())
        return scim.respond(scim.list_response(items, len(items), 1))

    return await _scim(request, db, False, run)


@public_router.get("/scim/v2/ResourceTypes/{type_id}")
async def scim_resource_type(type_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        found = next((t for t in scim.resource_types(store.base_url()) if t["id"].lower() == type_id.lower()), None)
        if found is None:
            raise scim.ScimError(404, f"Resource type {type_id[:60]} not found.")
        return scim.respond(found)

    return await _scim(request, db, False, run)


@public_router.get("/scim/v2/Schemas")
async def scim_schemas(request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        items = scim.schemas(store.base_url())
        return scim.respond(scim.list_response(items, len(items), 1))

    return await _scim(request, db, False, run)


@public_router.get("/scim/v2/Schemas/{schema_id}")
async def scim_schema(schema_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        found = next((s for s in scim.schemas(store.base_url()) if s["id"].lower() == schema_id.lower()), None)
        if found is None:
            raise scim.ScimError(404, f"Schema {schema_id[:120]} not found.")
        return scim.respond(found)

    return await _scim(request, db, False, run)


@public_router.get("/scim/v2/Users")
async def scim_list_users(
    request: Request,
    filter: str | None = Query(default=None),
    startIndex: str | None = Query(default=None),
    count: str | None = Query(default=None),
    attributes: str | None = Query(default=None),
    excludedAttributes: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    async def run(ctx: store.Ctx) -> Response:
        start, size = scim.paging(startIndex, count)
        body = await store.list_users(db, ctx, filter, start, size)
        body["Resources"] = [scim.project(r, attributes, excludedAttributes) for r in body["Resources"]]
        return scim.respond(body)

    return await _scim(request, db, False, run)


@public_router.post("/scim/v2/Users")
async def scim_create_user(request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        resource = await store.create_user(db, ctx, await _body(request))
        return scim.respond(resource, 201, resource["meta"]["location"])

    return await _scim(request, db, True, run)


@public_router.get("/scim/v2/Users/{user_id}")
async def scim_get_user(
    user_id: str,
    request: Request,
    attributes: str | None = Query(default=None),
    excludedAttributes: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    async def run(ctx: store.Ctx) -> Response:
        return scim.respond(scim.project(await store.get_user(db, ctx, user_id), attributes, excludedAttributes))

    return await _scim(request, db, False, run)


@public_router.put("/scim/v2/Users/{user_id}")
async def scim_replace_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        return scim.respond(await store.replace_user(db, ctx, user_id, await _body(request)))

    return await _scim(request, db, True, run)


@public_router.patch("/scim/v2/Users/{user_id}")
async def scim_patch_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        return scim.respond(await store.patch_user(db, ctx, user_id, await _body(request)))

    return await _scim(request, db, True, run)


@public_router.delete("/scim/v2/Users/{user_id}")
async def scim_delete_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        await store.delete_user(db, ctx, user_id)
        return scim.no_content()

    return await _scim(request, db, True, run)


@public_router.get("/scim/v2/Groups")
async def scim_list_groups(
    request: Request,
    filter: str | None = Query(default=None),
    startIndex: str | None = Query(default=None),
    count: str | None = Query(default=None),
    attributes: str | None = Query(default=None),
    excludedAttributes: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    async def run(ctx: store.Ctx) -> Response:
        start, size = scim.paging(startIndex, count)
        return scim.respond(await store.list_groups(db, ctx, filter, start, size, excludedAttributes, attributes))

    return await _scim(request, db, False, run)


@public_router.post("/scim/v2/Groups")
async def scim_create_group(request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        resource = await store.create_group(db, ctx, await _body(request))
        return scim.respond(resource, 201, resource["meta"]["location"])

    return await _scim(request, db, True, run)


@public_router.get("/scim/v2/Groups/{group_id}")
async def scim_get_group(
    group_id: str,
    request: Request,
    attributes: str | None = Query(default=None),
    excludedAttributes: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    async def run(ctx: store.Ctx) -> Response:
        return scim.respond(await store.get_group(db, ctx, group_id, excludedAttributes, attributes))

    return await _scim(request, db, False, run)


@public_router.put("/scim/v2/Groups/{group_id}")
async def scim_replace_group(group_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        return scim.respond(await store.replace_group(db, ctx, group_id, await _body(request)))

    return await _scim(request, db, True, run)


@public_router.patch("/scim/v2/Groups/{group_id}")
async def scim_patch_group(group_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        await store.patch_group(db, ctx, group_id, await _body(request))
        return scim.no_content()  # Entra expects 204; Okta accepts 200 or 204

    return await _scim(request, db, True, run)


@public_router.delete("/scim/v2/Groups/{group_id}")
async def scim_delete_group(group_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        await store.delete_group(db, ctx, group_id)
        return scim.no_content()

    return await _scim(request, db, True, run)


@public_router.post("/scim/v2/Bulk")
async def scim_bulk(request: Request, db: AsyncSession = Depends(get_db)):
    async def run(ctx: store.Ctx) -> Response:
        raise scim.ScimError(501, "Bulk operations aren't supported; send each change on its own.")

    return await _scim(request, db, False, run)
