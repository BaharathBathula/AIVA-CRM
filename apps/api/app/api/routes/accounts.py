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
from app.schemas.account import (
    AccountCreate,
    AccountResponse,
    AccountUpdate,
)
from app.services.accounts import (
    create_account,
    get_account,
    list_accounts,
    update_account,
)


router = APIRouter(
    prefix="/accounts",
    tags=["Accounts"],
)


@router.post(
    "",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_account_endpoint(
    payload: AccountCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await create_account(
        db,
        organization_id,
        payload,
    )


@router.get(
    "",
    response_model=list[AccountResponse],
)
async def list_accounts_endpoint(
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
    return await list_accounts(
        db,
        organization_id,
        skip,
        limit,
    )


@router.get(
    "/{account_id}",
    response_model=AccountResponse,
)
async def get_account_endpoint(
    account_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await get_account(
        db,
        organization_id,
        account_id,
    )


@router.patch(
    "/{account_id}",
    response_model=AccountResponse,
)
async def update_account_endpoint(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await update_account(
        db,
        organization_id,
        account_id,
        payload,
    )
