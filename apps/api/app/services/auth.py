"""Authentication workflows: sessions, sign-up, password reset, invitations, MFA."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.permissions import SYSTEM_ROLES
from app.core.security import (
    create_access_token,
    create_mfa_challenge_token,
    generate_opaque_token,
    hash_token,
)
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.platform import AuthToken, Role, UserSession
from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def active_memberships(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[OrganizationMembership, Organization]]:
    result = await db.execute(
        select(OrganizationMembership, Organization)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "active",
            Organization.is_active.is_(True),
        )
        .order_by(Organization.name)
    )
    return [(membership, organization) for membership, organization in result.all()]


async def role_name(db: AsyncSession, organization_id: uuid.UUID, role_key: str) -> str:
    if role_key in SYSTEM_ROLES:
        return SYSTEM_ROLES[role_key].name
    result = await db.execute(select(Role.name).where(Role.organization_id == organization_id, Role.key == role_key))
    return result.scalar_one_or_none() or role_key.replace("_", " ").title()


def organizations_payload(rows: list[tuple[OrganizationMembership, Organization]]) -> list[dict]:
    return [
        {"id": str(org.id), "name": org.name, "slug": org.slug, "role": membership.role}
        for membership, org in rows
    ]


def access_minutes(organization, default: int) -> int:
    """Access-token lifetime: the workspace's security policy, within safe bounds."""
    try:
        minutes = int(((organization.settings if organization else None) or {}).get("session_timeout_minutes") or default)
    except (TypeError, ValueError):
        minutes = default
    return max(5, min(240, minutes))


async def issue_session(
    db: AsyncSession,
    user: User,
    organization_id: uuid.UUID,
    *,
    ip: str | None,
    user_agent: str | None,
) -> dict:
    """Create a refresh session and return tokens plus workspace context."""

    settings = get_settings()
    refresh_token = generate_opaque_token()
    session = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        organization_id=organization_id,
        refresh_token_hash=hash_token(refresh_token),
        user_agent=(user_agent or "")[:300] or None,
        ip_address=ip,
        last_used_at=utcnow(),
        expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(session)

    user.last_login_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None

    memberships = await active_memberships(db, user.id)
    role = next((m.role for m, org in memberships if org.id == organization_id), "member")
    minutes = access_minutes(
        next((org for _, org in memberships if org.id == organization_id), None), settings.jwt_access_token_expire_minutes
    )

    return {
        "access_token": create_access_token(
            user_id=user.id,
            secret_key=settings.jwt_secret_key,
            expires_minutes=minutes,
        ),
        "token_type": "bearer",
        "expires_in": minutes * 60,
        "refresh_token": refresh_token,
        "session_id": str(session.id),
        "user_id": str(user.id),
        "organization_id": str(organization_id),
        "role": role,
        "mfa_required": False,
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name},
        "organizations": organizations_payload(memberships),
    }


def mfa_challenge(user: User) -> dict:
    settings = get_settings()
    return {
        "mfa_required": True,
        "mfa_token": create_mfa_challenge_token(user.id, settings.jwt_secret_key),
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name},
    }


async def rotate_refresh_session(db: AsyncSession, refresh_token: str, organization_id: uuid.UUID | None) -> dict:
    settings = get_settings()
    result = await db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.refresh_token_hash == hash_token(refresh_token))
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session ended. Sign in again.")
    session, user = row
    if session.revoked_at is not None or session.expires_at <= utcnow() or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session ended. Sign in again.")

    memberships = await active_memberships(db, user.id)
    allowed = {org.id for _, org in memberships}
    target = organization_id or session.organization_id
    if target not in allowed:
        if not memberships:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You no longer have access to any workspace.")
        target = memberships[0][1].id

    new_refresh = generate_opaque_token()
    session.refresh_token_hash = hash_token(new_refresh)
    session.last_used_at = utcnow()
    session.organization_id = target
    role = next(m.role for m, org in memberships if org.id == target)
    minutes = access_minutes(next((org for _, org in memberships if org.id == target), None), settings.jwt_access_token_expire_minutes)

    return {
        "access_token": create_access_token(
            user_id=user.id,
            secret_key=settings.jwt_secret_key,
            expires_minutes=minutes,
        ),
        "token_type": "bearer",
        "expires_in": minutes * 60,
        "refresh_token": new_refresh,
        "session_id": str(session.id),
        "user_id": str(user.id),
        "organization_id": str(target),
        "role": role,
        "mfa_required": False,
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name},
        "organizations": organizations_payload(memberships),
    }


async def revoke_refresh_session(db: AsyncSession, refresh_token: str) -> UserSession | None:
    result = await db.execute(select(UserSession).where(UserSession.refresh_token_hash == hash_token(refresh_token)))
    session = result.scalar_one_or_none()
    if session is not None and session.revoked_at is None:
        session.revoked_at = utcnow()
    return session


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID, except_session_id: uuid.UUID | None = None) -> int:
    statement = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if except_session_id is not None:
        statement = statement.where(UserSession.id != except_session_id)
    result = await db.execute(statement)
    return result.rowcount or 0


async def create_one_time_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    purpose: str,
    hours: int,
    organization_id: uuid.UUID | None = None,
    created_by_user_id: uuid.UUID | None = None,
) -> str:
    # A new link replaces any earlier unused link for the same purpose.
    await db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user_id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    token = generate_opaque_token()
    db.add(
        AuthToken(
            id=uuid.uuid4(),
            user_id=user_id,
            organization_id=organization_id,
            purpose=purpose,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=hours),
            created_by_user_id=created_by_user_id,
        )
    )
    return token


async def consume_token(db: AsyncSession, token: str, purpose: str, *, peek: bool = False) -> AuthToken:
    result = await db.execute(
        select(AuthToken).where(AuthToken.token_hash == hash_token(token), AuthToken.purpose == purpose)
    )
    record = result.scalar_one_or_none()
    if record is None or record.used_at is not None or record.expires_at <= utcnow():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This link is invalid or has expired. Request a new one.",
        )
    if not peek:
        record.used_at = utcnow()
    return record


def link(path: str, token: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}{path}?token={token}"
