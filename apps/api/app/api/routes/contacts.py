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
from app.schemas.contact import (
    ContactCreate,
    ContactResponse,
    ContactUpdate,
)
from app.services.contacts import (
    create_contact,
    get_contact,
    list_contacts,
    update_contact,
)


router = APIRouter(
    prefix="/contacts",
    tags=["Contacts"],
)


@router.post(
    "",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact_endpoint(
    payload: ContactCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await create_contact(
        db,
        organization_id,
        payload,
    )


@router.get(
    "",
    response_model=list[ContactResponse],
)
async def list_contacts_endpoint(
    account_id: uuid.UUID | None = None,
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
    return await list_contacts(
        db,
        organization_id,
        account_id,
        skip,
        limit,
    )


@router.get(
    "/{contact_id}",
    response_model=ContactResponse,
)
async def get_contact_endpoint(
    contact_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await get_contact(
        db,
        organization_id,
        contact_id,
    )


@router.patch(
    "/{contact_id}",
    response_model=ContactResponse,
)
async def update_contact_endpoint(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await update_contact(
        db,
        organization_id,
        contact_id,
        payload,
    )
