from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, client_ip, get_current_principal
from app.core.config import get_settings
from app.core.permissions import SYSTEM_ROLES
from app.core.security import (
    AuthenticationError,
    decode_mfa_challenge,
    hash_password,
    password_policy_error,
    verify_password,
)
from app.core import totp
from app.db.session import get_db
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.platform import UserSession
from app.models.user import User
from app.services.audit_helpers import audit_auth_event
from app.services.auth import (
    active_memberships,
    consume_token,
    create_one_time_token,
    issue_session,
    link,
    mfa_challenge,
    revoke_all_sessions,
    revoke_refresh_session,
    role_name,
    rotate_refresh_session,
    utcnow,
)
from app.services.billing import get_subscription, plan_state, start_trial
from app.services.workspace import provision_workspace, unique_slug

router = APIRouter(prefix="/auth", tags=["Authentication"])

INVALID = "That email and password don't match. Try again or reset your password."


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=500)
    organization_id: uuid.UUID | None = None


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=10)
    organization_id: uuid.UUID | None = None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)
    organization_id: uuid.UUID | None = None


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class SignupRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=1, max_length=200)


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=1, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=50)
    timezone: str | None = Field(default=None, max_length=64)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1, max_length=200)


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class MfaDisableRequest(BaseModel):
    password: str


# ---------------------------------------------------------------------------
# Sign in
# ---------------------------------------------------------------------------


async def _password_step(db: AsyncSession, email: str, password: str, request: Request | None = None) -> User:
    settings = get_settings()
    result = await db.execute(select(User).where(User.email == email.strip().lower()))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active or not user.password_hash:
        # Spend similar time as a real check so response timing doesn't reveal accounts.
        verify_password(password, "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$0000000000000000000000")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID, headers={"WWW-Authenticate": "Bearer"})

    if user.locked_until and user.locked_until > utcnow():
        minutes = max(1, int((user.locked_until - utcnow()).total_seconds() // 60) + 1)
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"Too many failed sign-in attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''} or reset your password.",
        )

    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        attempts = user.failed_login_count
        locked = attempts >= settings.max_failed_logins
        if locked:
            user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
        if request is not None:
            # Failed sign-ins show up in every workspace's audit log the user belongs to.
            for _membership, organization in await active_memberships(db, user.id):
                audit_auth_event(
                    db,
                    organization.id,
                    user,
                    "account_locked" if locked else "login_failed",
                    request,
                    {"attempts": attempts},
                )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID, headers={"WWW-Authenticate": "Bearer"})

    return user


async def _workspace_password_rule(db: AsyncSession, user_id: uuid.UUID, password: str, extra: uuid.UUID | None = None) -> str | None:
    """The longest minimum password length among the person's workspaces (Settings → Security)."""

    from app.models.governance import WorkspaceSetting

    org_ids = {org.id for _, org in await active_memberships(db, user_id)}
    if extra:
        org_ids.add(extra)
    if not org_ids:
        return None
    rows = (
        await db.execute(
            select(WorkspaceSetting.data).where(WorkspaceSetting.organization_id.in_(org_ids), WorkspaceSetting.namespace == "security")
        )
    ).scalars().all()
    required = max([int((row or {}).get("password_min_length") or 10) for row in rows] or [10])
    if len(password or "") < required:
        return f"Your workspace requires passwords of at least {required} characters."
    return None


async def _choose_organization(db: AsyncSession, user: User, requested: uuid.UUID | None) -> uuid.UUID:
    organization_id, _bound = await _session_organization(db, user, requested)
    return organization_id


async def _session_organization(
    db: AsyncSession, user: User, requested: uuid.UUID | None, *, sso_organization: uuid.UUID | None = None
) -> tuple[uuid.UUID, bool]:
    """The workspace to open, and whether the session must stay in it.

    Workspaces that require single sign-on for this person (Integrations → Microsoft
    Entra ID / Okta → Require single sign-on) can't be opened with a password; the
    session is then bound to the workspace chosen so it can't switch into them.
    ``sso_organization`` is the workspace whose single sign-on the person just used.
    """

    memberships = await active_memberships(db, user.id)
    if not memberships:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your account isn't part of an active workspace. Ask an administrator to invite you.",
        )
    if sso_organization is not None:
        if sso_organization not in {org.id for _, org in memberships}:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID, headers={"WWW-Authenticate": "Bearer"})
        return sso_organization, True
    from app.integrations.providers._idp_policy import blocked_message, sso_required

    required = await sso_required(db, user, memberships)
    allowed = [org.id for _, org in memberships if org.id not in required]
    if not allowed or (requested is not None and requested in required):
        raise HTTPException(status.HTTP_403_FORBIDDEN, blocked_message(user, list(required.values())))
    if requested is None:
        return allowed[0], bool(required)
    if requested not in allowed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID, headers={"WWW-Authenticate": "Bearer"})
    return requested, bool(required)


@router.post("/login")
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = await _password_step(db, payload.email, payload.password, request)
    try:
        organization_id, bound = await _session_organization(db, user, payload.organization_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            for _membership, organization in await active_memberships(db, user.id):
                audit_auth_event(db, organization.id, user, "login_blocked", request, {"method": "password", "reason": "single sign-on required"})
            await db.commit()
        raise

    if user.mfa_enabled and user.mfa_secret:
        await db.commit()
        return mfa_challenge(user)

    session = await issue_session(
        db, user, organization_id, ip=client_ip(request), user_agent=request.headers.get("user-agent"), bound=bound
    )
    audit_auth_event(db, organization_id, user, "login", request, {"method": "password"})
    await db.commit()
    return session


@router.post("/mfa/verify")
async def verify_mfa(payload: MfaVerifyRequest, request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    try:
        user_id, bound_organization = decode_mfa_challenge(payload.mfa_token, settings.jwt_secret_key)
    except AuthenticationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active or not user.mfa_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again.")

    if not totp.verify_code(user.mfa_secret or "", payload.code):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't work. Enter the current 6-digit code.")

    organization_id, bound = await _session_organization(db, user, payload.organization_id, sso_organization=bound_organization)
    session = await issue_session(
        db,
        user,
        organization_id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        bound=bound,
        via_sso=bound_organization is not None,
    )
    audit_auth_event(db, organization_id, user, "login", request, {"method": "sso+mfa" if bound_organization else "password+mfa"})
    await db.commit()
    return session


@router.post("/refresh")
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    session = await rotate_refresh_session(db, payload.refresh_token, payload.organization_id)
    await db.commit()
    return session


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if payload.refresh_token:
        session = await revoke_refresh_session(db, payload.refresh_token)
        if session is not None and session.organization_id is not None:
            user = await db.get(User, session.user_id)
            if user is not None:
                audit_auth_event(db, session.organization_id, user, "logout", request)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Self-service sign-up
# ---------------------------------------------------------------------------


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, request: Request, db: AsyncSession = Depends(get_db)):
    email = payload.email.strip().lower()
    problem = password_policy_error(payload.password, email)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)

    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account with this email already exists. Sign in or reset your password.",
        )

    user = User(
        id=uuid.uuid4(),
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    organization = Organization(
        id=uuid.uuid4(),
        name=payload.company_name.strip(),
        slug=await unique_slug(db, payload.company_name),
        is_active=True,
        settings={},
    )
    db.add_all([user, organization])
    await db.flush()
    db.add(
        OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization.id,
            user_id=user.id,
            role="owner",
            status="active",
        )
    )
    await provision_workspace(db, organization, user.id)
    db.add(start_trial(organization.id, email))
    await db.flush()

    session = await issue_session(
        db, user, organization.id, ip=client_ip(request), user_agent=request.headers.get("user-agent")
    )
    audit_auth_event(db, organization.id, user, "signup", request)
    await db.commit()
    return session


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    message = {"message": "If an account exists for that email, a reset link is on its way. It expires in 1 hour."}
    user = (await db.execute(select(User).where(User.email == payload.email.strip().lower()))).scalar_one_or_none()
    if user is None or not user.is_active:
        return message

    token = await create_one_time_token(db, user_id=user.id, purpose="password_reset", hours=1)
    await db.commit()

    # No email provider is configured in development, so return the link there.
    if settings.expose_dev_links:
        message["reset_url"] = link("/reset-password", token)
    return message


@router.post("/password/reset")
async def reset_password(payload: ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    record = await consume_token(db, payload.token, "password_reset", peek=True)
    user = await db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired.")

    problem = password_policy_error(payload.password, user.email) or await _workspace_password_rule(db, user.id, payload.password)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)

    await consume_token(db, payload.token, "password_reset")
    user.password_hash = hash_password(payload.password)
    user.failed_login_count = 0
    user.locked_until = None
    await revoke_all_sessions(db, user.id)
    for _membership, organization in await active_memberships(db, user.id):
        audit_auth_event(db, organization.id, user, "password_reset", request)
    await db.commit()
    return {"message": "Your password is updated. Sign in with your new password."}


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.get("/invitations/{token}")
async def get_invitation(token: str, db: AsyncSession = Depends(get_db)):
    record = await consume_token(db, token, "invite", peek=True)
    user = await db.get(User, record.user_id)
    organization = await db.get(Organization, record.organization_id) if record.organization_id else None
    inviter = await db.get(User, record.created_by_user_id) if record.created_by_user_id else None
    if user is None or organization is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is no longer valid.")
    return {
        "email": user.email,
        "full_name": user.full_name,
        "organization_name": organization.name,
        "invited_by": inviter.full_name if inviter else None,
        "has_password": bool(user.password_hash),
    }


@router.post("/invitations/accept")
async def accept_invitation(payload: AcceptInviteRequest, request: Request, db: AsyncSession = Depends(get_db)):
    record = await consume_token(db, payload.token, "invite", peek=True)
    user = await db.get(User, record.user_id)
    if user is None or record.organization_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is no longer valid.")

    invited = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == record.organization_id,
                OrganizationMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    from app.integrations.providers._idp_policy import sso_required_for

    if invited is not None and await sso_required_for(db, user, record.organization_id, invited.role):
        # The workspace requires single sign-on for this domain: signing in through it accepts the invitation.
        organization = await db.get(Organization, record.organization_id)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{organization.name if organization else 'This workspace'} uses single sign-on for your email domain. On the sign-in page "
            "choose “Log In with your Organization” and enter your work email; your invitation is accepted when you sign in.",
        )

    if user.password_hash:
        if not verify_password(payload.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Enter the password for your existing AIVA account.")
    else:
        problem = password_policy_error(payload.password, user.email) or await _workspace_password_rule(
            db, user.id, payload.password, extra=record.organization_id
        )
        if problem:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
        user.password_hash = hash_password(payload.password)

    if payload.full_name:
        user.full_name = payload.full_name.strip()
    user.is_active = True

    membership = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == record.organization_id,
                OrganizationMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation was withdrawn.")
    membership.status = "active"

    await consume_token(db, payload.token, "invite")
    await db.flush()
    session = await issue_session(
        db, user, record.organization_id, ip=client_ip(request), user_agent=request.headers.get("user-agent")
    )
    audit_auth_event(db, record.organization_id, user, "invite_accepted", request)
    await db.commit()
    return session


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


@router.get("/me")
async def me(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, principal.user_id)
    organization = await db.get(Organization, principal.organization_id)
    memberships = await active_memberships(db, principal.user_id)
    subscription = await get_subscription(db, principal.organization_id)
    state = plan_state(subscription)
    org_settings = organization.settings or {}
    from app.api.dependencies.auth import workspace_security
    from app.services.metering import active_addons

    security = await workspace_security(db, principal.organization_id)
    hipaa = bool(org_settings.get("hipaa_mode")) and "hipaa" in state.features
    return {
        "subscription": {
            **state.to_dict(),
            "features": sorted(principal.features),
            "addons": [a.addon_key for a in await active_addons(db, principal.organization_id)],
            "provider": subscription.provider if subscription else "demo",
        },
        "is_sandbox": bool(org_settings.get("sandbox_of")),
        "hipaa_mode": hipaa,
        "mfa_enforced": hipaa or bool(security.get("require_mfa")),
        "support_access": principal.support_grant_id is not None,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "job_title": user.job_title,
            "phone": user.phone,
            "timezone": user.timezone,
            "mfa_enabled": user.mfa_enabled,
            "has_password": bool(user.password_hash),
            "last_login_at": user.last_login_at,
        },
        "organization": {
            "id": str(organization.id),
            "name": organization.name,
            "slug": organization.slug,
            "settings": organization.settings or {},
        },
        "role": principal.role,
        "role_name": await role_name(db, principal.organization_id, principal.role),
        "team_id": str(principal.team_id) if principal.team_id else None,
        "permissions": sorted(principal.permissions),
        "organizations": [
            {"id": str(org.id), "name": org.name, "slug": org.slug, "role": m.role} for m, org in memberships
        ],
    }


@router.patch("/me")
async def update_me(
    payload: ProfileUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, principal.user_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, key, value.strip() if isinstance(value, str) else value)
    await db.commit()
    return {"message": "Profile updated."}


@router.post("/password/change")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, principal.user_id)
    if not verify_password(payload.current_password, user.password_hash or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is incorrect.")
    problem = password_policy_error(payload.new_password, user.email) or await _workspace_password_rule(db, user.id, payload.new_password)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    user.password_hash = hash_password(payload.new_password)
    keep = _parse_uuid(x_session_id)
    signed_out = await revoke_all_sessions(db, user.id, except_session_id=keep)
    audit_auth_event(db, principal.organization_id, user, "password_changed", request)
    await db.commit()
    return {"message": "Password changed.", "other_sessions_signed_out": signed_out}


# ---------------------------------------------------------------------------
# Multi-factor authentication
# ---------------------------------------------------------------------------


@router.post("/mfa/setup")
async def mfa_setup(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, principal.user_id)
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-step verification is already on.")
    secret = totp.generate_secret()
    user.mfa_secret = secret
    await db.commit()
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, user.email)}


@router.post("/mfa/enable")
async def mfa_enable(
    payload: MfaCodeRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, principal.user_id)
    if not user.mfa_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start setup first.")
    if not totp.verify_code(user.mfa_secret, payload.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code didn't match. Check the time on your phone and try again.")
    user.mfa_enabled = True
    audit_auth_event(db, principal.organization_id, user, "mfa_enabled", request)
    await db.commit()
    return {"message": "Two-step verification is on."}


@router.post("/mfa/disable")
async def mfa_disable(
    payload: MfaDisableRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, principal.user_id)
    if not verify_password(payload.password, user.password_hash or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your password is incorrect.")
    user.mfa_enabled = False
    user.mfa_secret = None
    audit_auth_event(db, principal.organization_id, user, "mfa_disabled", request)
    await db.commit()
    return {"message": "Two-step verification is off."}


# ---------------------------------------------------------------------------
# Sessions (signed-in devices)
# ---------------------------------------------------------------------------


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


@router.get("/sessions")
async def list_sessions(
    principal: Principal = Depends(get_current_principal),
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    db: AsyncSession = Depends(get_db),
):
    current = _parse_uuid(x_session_id)
    result = await db.execute(
        select(UserSession)
        .where(
            UserSession.user_id == principal.user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .order_by(UserSession.last_used_at.desc().nulls_last())
    )
    return [
        {
            "id": str(session.id),
            "user_agent": session.user_agent,
            "ip_address": session.ip_address,
            "created_at": session.created_at,
            "last_used_at": session.last_used_at,
            "expires_at": session.expires_at,
            "current": session.id == current,
        }
        for session in result.scalars()
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(UserSession, session_id)
    if session is None or session.user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    session.revoked_at = utcnow()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(
    principal: Principal = Depends(get_current_principal),
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    db: AsyncSession = Depends(get_db),
):
    count = await revoke_all_sessions(db, principal.user_id, except_session_id=_parse_uuid(x_session_id))
    await db.commit()
    return {"signed_out": count}


@router.get("/roles/system")
async def system_roles():
    return [{"key": role.key, "name": role.name} for role in SYSTEM_ROLES.values()]
