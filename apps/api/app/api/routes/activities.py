import uuid
from datetime import datetime

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
    ActivityDirection,
    ActivityResponse,
    ActivityType,
)
from app.services.activities import (
    create_activity,
    list_account_activities,
    list_activities,
    list_contact_activities,
    list_lead_activities,
    list_opportunity_activities,
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
    "/activities",
    response_model=list[ActivityResponse],
)
async def activity_feed(
    activity_type: ActivityType | None = Query(
        default=None
    ),
    direction: ActivityDirection | None = Query(
        default=None
    ),
    account_id: uuid.UUID | None = Query(
        default=None
    ),
    contact_id: uuid.UUID | None = Query(
        default=None
    ),
    lead_id: uuid.UUID | None = Query(
        default=None
    ),
    opportunity_id: uuid.UUID | None = Query(
        default=None
    ),
    occurred_from: datetime | None = Query(
        default=None
    ),
    occurred_to: datetime | None = Query(
        default=None
    ),
    search: str | None = Query(
        default=None,
        max_length=300,
    ),
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
    return await list_activities(
        db,
        organization_id,
        activity_type=activity_type,
        direction=direction,
        account_id=account_id,
        contact_id=contact_id,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        search=search,
        skip=skip,
        limit=limit,
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


@router.get(
    "/leads/{lead_id}/activities",
    response_model=list[ActivityResponse],
)
async def lead_activity_timeline(
    lead_id: uuid.UUID,
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
    return await list_lead_activities(
        db,
        organization_id,
        lead_id,
        skip,
        limit,
    )


@router.get(
    "/opportunities/{opportunity_id}/activities",
    response_model=list[ActivityResponse],
)
async def opportunity_activity_timeline(
    opportunity_id: uuid.UUID,
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
    return await list_opportunity_activities(
        db,
        organization_id,
        opportunity_id,
        skip,
        limit,
    )
