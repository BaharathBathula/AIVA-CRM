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
from app.schemas.opportunity import (
    OpportunityCreate,
    OpportunityResponse,
    OpportunityUpdate,
)
from app.services.opportunities import (
    create_opportunity,
    get_opportunity,
    list_opportunities,
    update_opportunity,
)


router = APIRouter(
    prefix="/opportunities",
    tags=["Opportunities"],
)


@router.post(
    "",
    response_model=OpportunityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_opportunity_endpoint(
    payload: OpportunityCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await create_opportunity(
        db,
        organization_id,
        payload,
    )


@router.get(
    "",
    response_model=list[
        OpportunityResponse
    ],
)
async def list_opportunities_endpoint(
    account_id: uuid.UUID | None = Query(
        default=None,
    ),
    pipeline_id: uuid.UUID | None = Query(
        default=None,
    ),
    stage_id: uuid.UUID | None = Query(
        default=None,
    ),
    owner_user_id: uuid.UUID | None = Query(
        default=None,
    ),
    search: str | None = Query(
        default=None,
        max_length=200,
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
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await list_opportunities(
        db,
        organization_id,
        account_id,
        pipeline_id,
        stage_id,
        owner_user_id,
        search,
        skip,
        limit,
    )


@router.get(
    "/{opportunity_id}",
    response_model=OpportunityResponse,
)
async def get_opportunity_endpoint(
    opportunity_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await get_opportunity(
        db,
        organization_id,
        opportunity_id,
    )


@router.patch(
    "/{opportunity_id}",
    response_model=OpportunityResponse,
)
async def update_opportunity_endpoint(
    opportunity_id: uuid.UUID,
    payload: OpportunityUpdate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await update_opportunity(
        db,
        organization_id,
        opportunity_id,
        payload,
    )
