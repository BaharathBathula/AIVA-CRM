import uuid
from typing import Annotated

from fastapi import (
    Depends,
    Header,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import sha256
from app.db.session import get_db
from app.models.editions import ApiKey
from app.models.organization import Organization


async def get_current_organization_id(
    x_organization_id: Annotated[
        str | None,
        Header(alias="X-Organization-ID"),
    ] = None,
    authorization: Annotated[
        str | None,
        Header(alias="Authorization"),
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    if not x_organization_id and authorization and authorization.startswith("Bearer aiva_sk_"):
        # An API key already belongs to one workspace, so the header is optional.
        key_org = (
            await db.execute(select(ApiKey.organization_id).where(ApiKey.key_hash == sha256(authorization[7:].strip())))
        ).scalar_one_or_none()
        if key_org is not None:
            x_organization_id = str(key_org)
    if not x_organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Organization-ID header is required.",
        )

    try:
        organization_id = uuid.UUID(
            x_organization_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid organization ID.",
        ) from exc

    result = await db.execute(
        select(Organization.id).where(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
    )

    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )

    return organization_id
