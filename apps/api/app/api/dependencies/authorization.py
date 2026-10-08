from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.core.config import get_settings
from app.core.security import (
    AuthenticationError,
    decode_access_token,
)
from app.db.session import get_db
from app.models.membership import OrganizationMembership
from app.models.user import User


bearer_scheme = HTTPBearer(auto_error=False)

BULK_UPDATE_ROLES = frozenset({
    "owner",
    "admin",
})


@dataclass(frozen=True)
class AuthorizedOrganizationUser:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


async def require_bulk_update_permission(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
) -> AuthorizedOrganizationUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()

    try:
        user_id = decode_access_token(
            credentials.credentials,
            settings.jwt_secret_key,
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.is_active.is_(True),
        )
    )

    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not active or does not exist.",
        )

    result = await db.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id
            == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )

    membership = result.scalar_one_or_none()

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to this organization.",
        )

    if membership.role not in BULK_UPDATE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permission for bulk updates.",
        )

    return AuthorizedOrganizationUser(
        user_id=user_id,
        organization_id=organization_id,
        role=membership.role,
    )
