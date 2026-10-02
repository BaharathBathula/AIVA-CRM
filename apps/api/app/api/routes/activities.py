import uuid

from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.db.session import get_db
from app.schemas.activity import (
    ActivityCreate,
    ActivityResponse,
)
from app.services.activities import (
    create_activity,
    list_account_activities,
    list_contact_activities,
)


router = APIRouter(
    tags=["Activities"],
)


@router.post(
    "/activities",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_activity_endpoint(
    payload: ActivityCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await create_activity(
        db,
        organization_id,
        payload,
    )


@router.get(
    "/accounts/{account_id}/activities",
    response_model=list[ActivityResponse],
)
async def account_activity_timeline(
    account_id: uuid.UUID,
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_account_activities(
        db,
        organization_id,
        account_id,
        skip,
        limit,
    )


@router.get(
    "/contacts/{contact_id}/activities",
    response_model=list[ActivityResponse],
)
async def contact_activity_timeline(
    contact_id: uuid.UUID,
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_contact_activities(
        db,
        organization_id,
        contact_id,
        skip,
        limit,
    )
