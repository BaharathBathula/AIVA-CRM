import uuid

from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.db.session import get_db
from app.schemas.account import (
    AccountCreate,
    AccountDuplicateCheckResponse,
    AccountResponse,
    AccountUpdate,
)
from app.services.accounts import (
    archive_account,
    check_account_duplicates,
    create_account,
    get_account,
    list_accounts,
    list_child_accounts,
    reactivate_account,
    update_account,
)


router = APIRouter(
    prefix="/accounts",
    tags=["Accounts"],
)


@router.post(
    "",
    response_model=
    AccountResponse,
    status_code=
    status.HTTP_201_CREATED,
)
async def create_account_endpoint(
    payload: AccountCreate,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await create_account(
        db,
        organization_id,
        payload,
    )


@router.get(
    "",
    response_model=
    list[AccountResponse],
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
    include_archived:
        bool = Query(
            default=False
        ),
    parent_account_id:
        uuid.UUID | None = Query(
            default=None
        ),
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await list_accounts(
        db,
        organization_id,
        skip,
        limit,
        include_archived,
        parent_account_id,
    )


@router.get(
    "/duplicates/check",
    response_model=
    AccountDuplicateCheckResponse,
)
async def check_account_duplicates_endpoint(
    name: str = Query(
        min_length=1,
        max_length=250,
    ),
    domain: str | None = Query(
        default=None,
        max_length=255,
    ),
    exclude_account_id:
        uuid.UUID | None = Query(
            default=None
        ),
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await check_account_duplicates(
        db,
        organization_id,
        name,
        domain,
        exclude_account_id,
    )


@router.get(
    "/{account_id}/children",
    response_model=
    list[AccountResponse],
)
async def list_child_accounts_endpoint(
    account_id: uuid.UUID,
    include_archived:
        bool = Query(
            default=False
        ),
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await list_child_accounts(
        db,
        organization_id,
        account_id,
        include_archived,
    )


@router.post(
    "/{account_id}/archive",
    response_model=
    AccountResponse,
)
async def archive_account_endpoint(
    account_id: uuid.UUID,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await archive_account(
        db,
        organization_id,
        account_id,
    )


@router.post(
    "/{account_id}/reactivate",
    response_model=
    AccountResponse,
)
async def reactivate_account_endpoint(
    account_id: uuid.UUID,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await reactivate_account(
        db,
        organization_id,
        account_id,
    )


@router.get(
    "/{account_id}",
    response_model=
    AccountResponse,
)
async def get_account_endpoint(
    account_id: uuid.UUID,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await get_account(
        db,
        organization_id,
        account_id,
    )


@router.patch(
    "/{account_id}",
    response_model=
    AccountResponse,
)
async def update_account_endpoint(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await update_account(
        db,
        organization_id,
        account_id,
        payload,
    )