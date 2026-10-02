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
from app.schemas.lead import (
    LeadCreate,
    LeadResponse,
    LeadStatus,
    LeadUpdate,
)
from app.services.leads import (
    create_lead,
    get_lead,
    list_leads,
    update_lead,
)


router = APIRouter(
    prefix="/leads",
    tags=["Leads"],
)


@router.post(
    "",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_lead_endpoint(
    payload: LeadCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await create_lead(
        db,
        organization_id,
        payload,
    )


@router.get(
    "",
    response_model=list[LeadResponse],
)
async def list_leads_endpoint(
    lead_status: LeadStatus | None = Query(
        default=None,
        alias="status",
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
    db: AsyncSession = Depends(get_db),
):
    return await list_leads(
        db,
        organization_id,
        lead_status,
        search,
        skip,
        limit,
    )


@router.get(
    "/{lead_id}",
    response_model=LeadResponse,
)
async def get_lead_endpoint(
    lead_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await get_lead(
        db,
        organization_id,
        lead_id,
    )


@router.patch(
    "/{lead_id}",
    response_model=LeadResponse,
)
async def update_lead_endpoint(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await update_lead(
        db,
        organization_id,
        lead_id,
        payload,
    )
