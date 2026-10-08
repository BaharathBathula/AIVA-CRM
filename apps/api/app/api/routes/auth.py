from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.user import User


router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)
    organization_id: uuid.UUID


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials or organization access.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    result = await db.execute(
        select(User).where(
            User.email == payload.email.strip().lower(),
            User.is_active.is_(True),
        )
    )

    user = result.scalar_one_or_none()

    if (
        user is None
        or not user.password_hash
        or not verify_password(
            payload.password,
            user.password_hash,
        )
    ):
        raise invalid_credentials

    result = await db.execute(
        select(OrganizationMembership.role)
        .join(
            Organization,
            Organization.id
            == OrganizationMembership.organization_id,
        )
        .where(
            OrganizationMembership.organization_id
            == payload.organization_id,
            OrganizationMembership.user_id == user.id,
            Organization.is_active.is_(True),
        )
    )

    role = result.scalar_one_or_none()

    if role is None:
        raise invalid_credentials

    settings = get_settings()

    token = create_access_token(
        user_id=user.id,
        secret_key=settings.jwt_secret_key,
        expires_minutes=settings.jwt_access_token_expire_minutes,
    )

    return LoginResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user_id=user.id,
        organization_id=payload.organization_id,
        role=role,
    )
