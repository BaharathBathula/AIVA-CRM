"""Sign in with Google, Microsoft, Apple or a workspace's single sign-on."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, client_ip, get_current_principal, require_feature, require_permission
from app.core.crypto import encrypt
from app.core.permissions import SYSTEM_ROLES
from app.db.session import get_db
from app.models.identity import SsoConnection, UserIdentity
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.platform import Role
from app.models.user import User
from app.services import oidc
from app.services.audit_helpers import audit_auth_event
from app.services.auth import active_memberships, issue_session, mfa_challenge, utcnow
from app.services.automation import record_audit
from app.services.billing import get_subscription, plan_state, start_trial
from app.services.workspace import provision_workspace, unique_slug

router = APIRouter(tags=["Sign-in providers"])

SOCIAL = ("google", "microsoft", "apple")


def _safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") and "\\" not in value else "/"


# ===========================================================================
# Starting a sign-in
# ===========================================================================


@router.get("/auth/providers")
async def providers():
    """Which sign-in buttons work on this server."""
    return {**oidc.configured(), "sso": True}


class DiscoverRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


async def _connection_for_email(db: AsyncSession, email: str) -> SsoConnection:
    if "@" not in email:
        raise oidc.OAuthError("Enter your work email address.")
    domain = oidc.email_domain(email)
    rows = (await db.execute(select(SsoConnection).where(SsoConnection.is_active.is_(True)))).scalars().all()
    connection = next((c for c in rows if domain in (c.domains or [])), None)
    if connection is None:
        raise oidc.OAuthError(f"{domain} doesn't use single sign-on with AIVA. Sign in with your email and password, Google or Microsoft.")
    subscription = await get_subscription(db, connection.organization_id)
    state = plan_state(subscription)
    if "sso" not in state.features or state.read_only:
        raise oidc.OAuthError("Single sign-on isn't available for this workspace's plan. Sign in with your email and password.")
    return connection


@router.post("/auth/sso/discover")
async def discover_sso(payload: DiscoverRequest, db: AsyncSession = Depends(get_db)):
    try:
        connection = await _connection_for_email(db, payload.email.strip().lower())
    except oidc.OAuthError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    organization = await db.get(Organization, connection.organization_id)
    return {"organization_name": organization.name if organization else connection.name, "provider_host": urlparse(connection.issuer).netloc}


class StartRequest(BaseModel):
    provider: str = Field(pattern="^(google|microsoft|apple|sso)$")
    intent: str = Field(default="login", pattern="^(login|signup)$")
    next: str | None = Field(default=None, max_length=500)
    email: str | None = Field(default=None, max_length=320)


async def _start(db: AsyncSession, provider_path: str, intent: str, next_path: str, email: str | None, link_user_id: uuid.UUID | None):
    try:
        connection = None
        key = provider_path
        if provider_path == "sso":
            if not email:
                raise oidc.OAuthError("Enter your work email to find your organization's sign-in.")
            connection = await _connection_for_email(db, email.strip().lower())
            key = f"sso:{connection.id}"
        p = await oidc.provider(key, connection)
        values, token = oidc.new_state(key, intent, _safe_next(next_path), link_user_id=link_user_id)
        url = oidc.authorization_url(p, values, login_hint=email.strip().lower() if email else None)
    except oidc.OAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {
        "authorization_url": url,
        "state_token": token,
        # Apple posts the result back from its own site, which needs a SameSite=None cookie.
        "cookie_same_site": "none" if provider_path == "apple" else "lax",
    }


@router.post("/auth/oauth/start")
async def start(payload: StartRequest, db: AsyncSession = Depends(get_db)):
    return await _start(db, payload.provider, payload.intent, payload.next or "/", payload.email, None)


class LinkRequest(BaseModel):
    provider: str = Field(pattern="^(google|microsoft|apple)$")


@router.post("/auth/oauth/link")
async def start_link(payload: LinkRequest, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    if principal.api_key_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Connect sign-in methods from the app.")
    return await _start(db, payload.provider, "link", "/settings/security", None, principal.user_id)


# ===========================================================================
# Finishing a sign-in
# ===========================================================================


class CallbackRequest(BaseModel):
    provider: str = Field(pattern="^(google|microsoft|apple|sso)$")
    code: str | None = Field(default=None, max_length=4000)
    state: str | None = Field(default=None, max_length=200)
    state_token: str | None = Field(default=None, max_length=4000)
    user: str | None = Field(default=None, max_length=4000)  # Apple sends the name once, as JSON
    error: str | None = Field(default=None, max_length=200)


def _failure(message: str, intent: str = "login") -> dict[str, Any]:
    return {"status": "error", "message": message, "intent": intent}


async def _identity(db: AsyncSession, provider_key: str, subject: str) -> UserIdentity | None:
    return (
        await db.execute(select(UserIdentity).where(UserIdentity.provider == provider_key, UserIdentity.subject == subject))
    ).scalar_one_or_none()


def _remember_identity(db: AsyncSession, existing: UserIdentity | None, user: User, provider_key: str, profile: oidc.Profile) -> None:
    if existing is None:
        db.add(UserIdentity(id=uuid.uuid4(), user_id=user.id, provider=provider_key, subject=profile.subject, email=profile.email, last_login_at=utcnow()))
    else:
        existing.last_login_at = utcnow()
        if profile.email:
            existing.email = profile.email


async def _activate_invites(db: AsyncSession, user: User) -> None:
    """A verified email proves the person owns it, so pending invitations are accepted."""
    rows = (
        await db.execute(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user.id, OrganizationMembership.status == "invited")
        )
    ).scalars().all()
    for membership in rows:
        membership.status = "active"
    user.is_active = True


async def _create_account(db: AsyncSession, profile: oidc.Profile, request: Request) -> tuple[User, uuid.UUID]:
    email = profile.email or ""
    name = (profile.name or email.split("@")[0].replace(".", " ").title()).strip()[:200]
    user = User(id=uuid.uuid4(), email=email, full_name=name or email, password_hash=None, is_active=True)
    workspace = oidc.workspace_name(email, profile.name)
    organization = Organization(id=uuid.uuid4(), name=workspace, slug=await unique_slug(db, workspace), is_active=True, settings={})
    db.add_all([user, organization])
    await db.flush()
    db.add(OrganizationMembership(id=uuid.uuid4(), organization_id=organization.id, user_id=user.id, role="owner", status="active"))
    await provision_workspace(db, organization, user.id)
    db.add(start_trial(organization.id, email))
    await db.flush()
    audit_auth_event(db, organization.id, user, "signup", request)
    return user, organization.id


async def _member(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership | None:
    return (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id, OrganizationMembership.user_id == user_id
            )
        )
    ).scalar_one_or_none()


async def _sso_user(db: AsyncSession, connection: SsoConnection, provider_key: str, profile: oidc.Profile, request: Request) -> User:
    """Workspace single sign-on only ever opens that workspace."""
    if not profile.email or oidc.email_domain(profile.email) not in (connection.domains or []):
        raise oidc.OAuthError("Your organization's sign-in returned an email outside its domains. Ask your administrator.")
    identity = await _identity(db, provider_key, profile.subject)
    user = await db.get(User, identity.user_id) if identity else None
    if user is None:
        user = (await db.execute(select(User).where(User.email == profile.email))).scalar_one_or_none()
        if user is not None:
            membership = await _member(db, connection.organization_id, user.id)
            if membership is None:
                raise oidc.OAuthError(
                    f"{profile.email} already has an AIVA account outside this workspace. Ask your administrator to invite it, then sign in again."
                )
            if user.is_platform_staff:
                raise oidc.OAuthError("AIVA staff accounts can't be linked to a workspace's single sign-on.")
            others = [org for m, org in await active_memberships(db, user.id) if org.id != connection.organization_id]
            if others:
                # An account other workspaces trust is only linked to this workspace's identity
                # provider when the workspace has proven it owns the email domain (DNS); whoever
                # owns the domain already controls that mailbox. The session still only opens
                # this workspace.
                from app.integrations.providers import _idp_policy

                domain = oidc.email_domain(profile.email)
                if domain not in (await _idp_policy.load(db, connection.organization_id))["verified"]:
                    raise oidc.OAuthError(
                        f"This account also belongs to other workspaces, so it can only use single sign-on once your administrator "
                        f"verifies @{domain} (Single sign-on → Verified domains). Until then sign in with your password, Google or Microsoft."
                    )
        elif connection.auto_provision:
            user = User(id=uuid.uuid4(), email=profile.email, full_name=(profile.name or profile.email)[:200], password_hash=None, is_active=True)
            db.add(user)
            await db.flush()
            db.add(
                OrganizationMembership(
                    id=uuid.uuid4(), organization_id=connection.organization_id, user_id=user.id, role=connection.default_role, status="active"
                )
            )
            await db.flush()
            record_audit(
                db,
                organization_id=connection.organization_id,
                action="create",
                entity_type="user",
                entity_id=user.id,
                entity_label=user.full_name,
                changes={"source": "single sign-on", "role": connection.default_role},
                actor_user_id=None,
            )
        else:
            raise oidc.OAuthError(f"You don't have access to this workspace yet. Ask your administrator to invite {profile.email}.")
    membership = await _member(db, connection.organization_id, user.id)
    if membership is None:
        raise oidc.OAuthError("You don't have access to this workspace yet. Ask your administrator to invite you.")
    if membership.status == "invited":
        membership.status = "active"
    if membership.status != "active":
        raise oidc.OAuthError("Your access to this workspace is turned off. Ask your administrator.")
    _remember_identity(db, identity, user, provider_key, profile)
    return user


async def _social_user(db: AsyncSession, provider_key: str, profile: oidc.Profile, request: Request) -> tuple[User, uuid.UUID | None]:
    identity = await _identity(db, provider_key, profile.subject)
    if identity is not None:
        user = await db.get(User, identity.user_id)
        if user is not None:
            if profile.email_verified:
                await _activate_invites(db, user)
            _remember_identity(db, identity, user, provider_key, profile)
            return user, None
    label = oidc.LABELS.get(provider_key, provider_key)
    if not profile.email:
        raise oidc.OAuthError(f"{label.title()} didn't share an email address. Sign in with your email and password instead.")
    if not profile.email_verified:
        raise oidc.OAuthError(
            f"{label.title()} hasn't confirmed {profile.email}. Sign in with your password, then connect {label.title()} in Settings › Password and security."
        )
    user = (await db.execute(select(User).where(User.email == profile.email))).scalar_one_or_none()
    if user is None:
        user, organization_id = await _create_account(db, profile, request)
        _remember_identity(db, None, user, provider_key, profile)
        return user, organization_id
    await _activate_invites(db, user)
    _remember_identity(db, None, user, provider_key, profile)
    return user, None


@router.post("/auth/oauth/callback")
async def callback(payload: CallbackRequest, request: Request, db: AsyncSession = Depends(get_db)):
    intent = "login"
    try:
        if payload.error:
            raise oidc.OAuthError("Sign-in was cancelled." if payload.error == "access_denied" else f"Sign-in didn't finish ({payload.error}).")
        claims = oidc.read_state(payload.state_token, payload.state, payload.provider)
        intent = str(claims.get("intent") or "login")
        provider_key = str(claims["provider"])
        connection = None
        if provider_key.startswith("sso:"):
            connection = await db.get(SsoConnection, uuid.UUID(provider_key[4:]))
            if connection is None or not connection.is_active:
                raise oidc.OAuthError("Your organization turned off single sign-on. Sign in another way.")
        p = await oidc.provider(provider_key, connection)
        if not payload.code:
            raise oidc.OAuthError("Sign-in didn't finish. Try again.")
        tokens = await oidc.exchange_code(p, payload.code, str(claims["verifier"]))
        id_claims = await oidc.verify_id_token(p, tokens["id_token"], str(claims["nonce"]))
        profile = oidc.profile(p, id_claims, payload.user)

        if intent == "link":
            user = await db.get(User, uuid.UUID(str(claims["link"])))
            if user is None or not user.is_active:
                raise oidc.OAuthError("Sign in again, then connect the account.")
            existing = await _identity(db, provider_key, profile.subject)
            if existing is not None and existing.user_id != user.id:
                raise oidc.OAuthError(f"That {p.label} account is already connected to another AIVA user.")
            _remember_identity(db, existing, user, provider_key, profile)
            for _, organization in await active_memberships(db, user.id):
                audit_auth_event(db, organization.id, user, "identity_connected", request, {"provider": provider_key})
            await db.commit()
            return {"status": "linked", "provider": p.path}

        if connection is not None:
            user = await _sso_user(db, connection, provider_key, profile, request)
            organization_id, bound = connection.organization_id, True
        else:
            user, organization_id = await _social_user(db, provider_key, profile, request)
            bound = False

        if not user.is_active:
            raise oidc.OAuthError("This account is deactivated. Ask a workspace administrator.")
        if user.locked_until and user.locked_until > utcnow():
            raise oidc.OAuthError("This account is locked after too many failed sign-ins. Try again later or reset your password.")
        if organization_id is None:
            memberships = await active_memberships(db, user.id)
            if not memberships:
                raise oidc.OAuthError("Your account isn't part of an active workspace. Ask an administrator to invite you.")
            # Workspaces that require single sign-on for this person can't be opened this way;
            # the session then stays in the workspace it opens.
            from app.integrations.providers._idp_policy import blocked_message, sso_required

            required = await sso_required(db, user, memberships)
            allowed = [org.id for _, org in memberships if org.id not in required]
            if not allowed:
                raise oidc.OAuthError(blocked_message(user, list(required.values())))
            organization_id, bound = allowed[0], bool(required)

        await db.flush()
        next_path = _safe_next(str(claims.get("next") or "/"))
        if user.mfa_enabled and user.mfa_secret:
            await db.commit()
            return {"status": "mfa", "next": next_path, **mfa_challenge(user, organization_id if connection is not None else None)}

        session = await issue_session(
            db, user, organization_id, ip=client_ip(request), user_agent=request.headers.get("user-agent"), bound=bound,
            via_sso=connection is not None,
        )
        audit_auth_event(db, organization_id, user, "login", request, {"method": "sso" if connection is not None else p.key})
        await db.commit()
        return {"status": "session", "next": next_path, "session": session}
    except oidc.OAuthError as exc:
        await db.rollback()
        return _failure(str(exc), intent)


# ===========================================================================
# Connected sign-in methods (Settings › Password and security)
# ===========================================================================


@router.get("/auth/identities")
async def list_identities(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, principal.user_id)
    rows = (
        await db.execute(select(UserIdentity).where(UserIdentity.user_id == principal.user_id).order_by(UserIdentity.created_at))
    ).scalars().all()
    connections = {}
    for row in rows:
        if row.provider.startswith("sso:"):
            connection = await db.get(SsoConnection, uuid.UUID(row.provider[4:]))
            connections[row.provider] = connection.name if connection else "Single sign-on"
    return {
        "has_password": bool(user and user.password_hash),
        "available": oidc.configured(),
        "items": [
            {
                "id": str(row.id),
                "provider": "sso" if row.provider.startswith("sso:") else row.provider,
                "label": connections.get(row.provider) or oidc.LABELS.get(row.provider, row.provider).title(),
                "email": row.email,
                "created_at": row.created_at,
                "last_login_at": row.last_login_at,
            }
            for row in rows
        ],
    }


@router.delete("/auth/identities/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_identity(identity_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    identity = await db.get(UserIdentity, identity_id)
    if identity is None or identity.user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sign-in method not found.")
    user = await db.get(User, principal.user_id)
    others = (
        await db.execute(select(func.count()).select_from(UserIdentity).where(UserIdentity.user_id == user.id, UserIdentity.id != identity.id))
    ).scalar_one()
    if not user.password_hash and not others:
        raise HTTPException(status.HTTP_409_CONFLICT, "Set a password first (Forgot password on the sign-in page), so you can still sign in.")
    await db.delete(identity)
    audit_auth_event(db, principal.organization_id, user, "identity_disconnected", request, {"provider": identity.provider})
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Workspace single sign-on (Settings › Single sign-on)
# ===========================================================================


def connection_out(connection: SsoConnection | None) -> dict[str, Any] | None:
    if connection is None:
        return None
    return {
        "id": str(connection.id),
        "name": connection.name,
        "issuer": connection.issuer,
        "client_id": connection.client_id,
        "has_secret": bool(connection.client_secret),
        "domains": connection.domains or [],
        "default_role": connection.default_role,
        "auto_provision": connection.auto_provision,
        "is_active": connection.is_active,
        "updated_at": connection.updated_at,
    }


async def _org_connection(db: AsyncSession, organization_id: uuid.UUID) -> SsoConnection | None:
    return (await db.execute(select(SsoConnection).where(SsoConnection.organization_id == organization_id))).scalar_one_or_none()


@router.get("/sso")
async def get_sso(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    roles = [{"key": k, "name": r.name} for k, r in SYSTEM_ROLES.items() if k != "owner"]
    roles += [
        {"key": r.key, "name": r.name}
        for r in (await db.execute(select(Role).where(Role.organization_id == principal.organization_id).order_by(Role.name))).scalars()
    ]
    return {
        "connection": connection_out(await _org_connection(db, principal.organization_id)),
        "redirect_uri": oidc.redirect_uri("sso"),
        "available": principal.has("sso"),
        "roles": roles,
    }


class SsoPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, max_length=2000)
    domains: list[str] = Field(min_length=1, max_length=20)
    default_role: str = Field(default="member", max_length=60)
    auto_provision: bool = True
    is_active: bool = True


@router.put("/sso", dependencies=[Depends(require_feature("sso"))])
async def save_sso(payload: SsoPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    try:
        issuer = oidc.check_issuer_url(payload.issuer)
        doc = await oidc.discover(issuer)
    except oidc.OAuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    domains = []
    for raw in payload.domains:
        domain = raw.strip().lower().lstrip("@")
        if not domain or "." not in domain or " " in domain:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{raw}' isn't a domain. Use the part after @, such as acme.com.")
        if domain in oidc.PUBLIC_EMAIL_DOMAINS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{domain} is a public email provider and can't use single sign-on.")
        if domain not in domains:
            domains.append(domain)
    # Domains proved through DNS (Integrations → Entra ID / Okta → Domains) win over plain claims:
    # a domain verified by another workspace can't be claimed here, and a domain this workspace
    # has verified is taken over from a workspace that only claimed it.
    from app.integrations.providers import _idp_policy
    from app.models.governance import WorkspaceSetting

    verified_by: dict[uuid.UUID, set[str]] = {}
    for org_id, data in (
        await db.execute(select(WorkspaceSetting.organization_id, WorkspaceSetting.data).where(WorkspaceSetting.namespace == _idp_policy.NAMESPACE))
    ).all():
        verified_by[org_id] = set(_idp_policy.normalize(data)["verified"])
    mine_verified = verified_by.get(principal.organization_id, set())
    verified_elsewhere = {d for org_id, found in verified_by.items() if org_id != principal.organization_id for d in found}
    blocked = next((d for d in domains if d in verified_elsewhere and d not in mine_verified), None)
    if blocked:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{blocked} is verified by another AIVA workspace, so it can't sign in here.")
    others = (await db.execute(select(SsoConnection).where(SsoConnection.organization_id != principal.organization_id))).scalars().all()
    for other in others:
        for d in other.domains or []:
            if d not in domains:
                continue
            if d not in mine_verified:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{d} already signs in to another AIVA workspace. Verify the domain (Integrations → Microsoft Entra ID or Okta → Domains) to claim it.",
                )
            if d in verified_by.get(other.organization_id, set()):
                # Both workspaces proved the domain: neither takes it from the other silently.
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{d} is verified by another AIVA workspace too and signs in there. Remove it from that workspace's single sign-on first.",
                )
    for other in others:
        moved = [d for d in (other.domains or []) if d in domains]
        if moved:
            other.domains = [d for d in (other.domains or []) if d not in moved]
            record_audit(
                db, organization_id=other.organization_id, action="sso_domain_reclaimed", entity_type="sso_connection", entity_id=other.id,
                entity_label=other.name, changes={"domains_removed": moved, "verified_by_another_workspace": True},
            )
    valid_roles = set(SYSTEM_ROLES) - {"owner"}
    valid_roles |= {r for r in (await db.execute(select(Role.key).where(Role.organization_id == principal.organization_id))).scalars()}
    if payload.default_role not in valid_roles:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose the role new people get.")

    connection = await _org_connection(db, principal.organization_id)
    if connection is None:
        if not payload.client_secret:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the client secret from your identity provider.")
        connection = SsoConnection(id=uuid.uuid4(), organization_id=principal.organization_id, client_secret="")
        db.add(connection)
    connection.name = payload.name.strip()
    connection.issuer = str(doc.get("issuer") or issuer).rstrip("/")
    connection.client_id = payload.client_id.strip()
    if payload.client_secret:
        connection.client_secret = encrypt(payload.client_secret.strip()) or ""
    connection.domains = domains
    connection.default_role = payload.default_role
    connection.auto_provision = payload.auto_provision
    connection.is_active = payload.is_active
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="update",
        entity_type="sso",
        entity_id=connection.id,
        entity_label="Single sign-on",
        changes={"issuer": connection.issuer, "domains": domains, "active": payload.is_active},
    )
    await db.commit()
    return connection_out(connection)


@router.delete("/sso", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sso(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    connection = await _org_connection(db, principal.organization_id)
    if connection is not None:
        await db.delete(connection)
        record_audit(db, organization_id=principal.organization_id, action="delete", entity_type="sso", entity_label="Single sign-on")
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SsoTest(BaseModel):
    issuer: str = Field(min_length=8, max_length=500)


@router.post("/sso/test", dependencies=[Depends(require_feature("sso"))])
async def test_sso(payload: SsoTest, principal: Principal = Depends(require_permission("admin.settings"))):
    try:
        doc = await oidc.discover(oidc.check_issuer_url(payload.issuer))
    except oidc.OAuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {
        "issuer": doc.get("issuer"),
        "authorization_endpoint": doc.get("authorization_endpoint"),
        "token_endpoint": doc.get("token_endpoint"),
        "scopes": doc.get("scopes_supported"),
    }
