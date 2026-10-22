"""Authentication and permission dependencies.

Every CRM route depends on :func:`get_current_principal`, which verifies the
bearer token, confirms an active membership in the organization named by the
``X-Organization-ID`` header, resolves the member's role permissions, and
binds the acting user to the database session for audit and automation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import get_current_organization_id
from app.core.config import get_settings
from app.core.context import bind_session_context
from app.core.permissions import SYSTEM_ROLES
from app.core.security import AuthenticationError, decode_access_token
from app.db.session import get_db
from app.models.membership import OrganizationMembership
from app.models.platform import Role
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    email: str = ""
    full_name: str = ""
    team_id: uuid.UUID | None = None

    def can(self, permission: str) -> bool:
        return permission in self.permissions


async def resolve_role_permissions(
    db: AsyncSession,
    organization_id: uuid.UUID,
    role_key: str,
) -> frozenset[str]:
    system = SYSTEM_ROLES.get(role_key)
    if system is not None:
        return system.permissions

    result = await db.execute(
        select(Role.permissions).where(
            Role.organization_id == organization_id,
            Role.key == role_key,
        )
    )
    permissions = result.scalar_one_or_none()
    return frozenset(permissions or [])


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    organization_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    if credentials is None:
        raise _unauthorized("Sign in to continue.")

    settings = get_settings()
    try:
        user_id = decode_access_token(credentials.credentials, settings.jwt_secret_key)
    except AuthenticationError as exc:
        raise _unauthorized("Your session expired. Sign in again.") from exc

    result = await db.execute(
        select(User, OrganizationMembership)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == User.id,
        )
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            OrganizationMembership.organization_id == organization_id,
        )
    )
    row = result.first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this workspace.",
        )

    user, membership = row
    if membership.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your access to this workspace is deactivated.",
        )

    permissions = await resolve_role_permissions(db, organization_id, membership.role)

    bind_session_context(
        db,
        actor_id=user.id,
        organization_id=organization_id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    # Workflow and lead scoring rules are evaluated inside flush events,
    # which cannot run queries, so load them for this organization now.
    from app.services.automation import load_automation_context

    await load_automation_context(db, organization_id)

    return Principal(
        user_id=user.id,
        organization_id=organization_id,
        role=membership.role,
        permissions=permissions,
        email=user.email,
        full_name=user.full_name,
        team_id=membership.team_id,
    )


def require_permission(*required: str):
    """Dependency factory: the member must hold every listed permission."""

    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        missing = [permission for permission in required if not principal.can(permission)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role doesn't allow this action. Ask an administrator for access.",
            )
        return principal

    return dependency


async def enforce_record_permissions(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Router-level guard for the core CRM record APIs."""

    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        needed = "records.read"
    elif method == "DELETE":
        needed = "records.delete"
    else:
        needed = "records.write"

    if not principal.can(needed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role doesn't allow this action. Ask an administrator for access.",
        )
    return principal
