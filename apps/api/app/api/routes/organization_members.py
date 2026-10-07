import uuid

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.db.session import get_db
from app.models.membership import (
    OrganizationMembership,
)
from app.models.user import User
from app.schemas.organization_member import (
    OrganizationMemberResponse,
)


router = APIRouter(
    prefix="/organization-members",
    tags=["Organization Members"],
)


@router.get(
    "",
    response_model=
    list[OrganizationMemberResponse],
)
async def list_organization_members(
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    result = await db.execute(
        select(
            OrganizationMembership.user_id,
            User.full_name,
            User.email,
            OrganizationMembership.role,
            User.is_active,
        )
        .join(
            User,
            User.id
            ==
            OrganizationMembership.user_id,
        )
        .where(
            OrganizationMembership.organization_id
            ==
            organization_id,
            User.is_active.is_(True),
        )
        .order_by(
            User.full_name.asc()
        )
    )

    return [
        OrganizationMemberResponse(
            user_id=row.user_id,
            full_name=row.full_name,
            email=row.email,
            role=row.role,
            is_active=row.is_active,
        )
        for row in result.all()
    ]