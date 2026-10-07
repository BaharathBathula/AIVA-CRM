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
    ContactDuplicateCheckResponse,
    ContactDuplicateMatch,
    ContactResponse,
    ContactUpdate,
)
from app.services.contacts import (
    archive_contact,
    reactivate_contact,
    check_contact_duplicates,
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
    status_code=
    status.HTTP_201_CREATED,
)
async def create_contact_endpoint(
    payload: ContactCreate,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await create_contact(
        db,
        organization_id,
        payload,
    )


@router.get(
    "",
    response_model=
    list[ContactResponse],
)
async def list_contacts_endpoint(
    account_id:
        uuid.UUID | None = None,
    is_active:
        bool | None = None,
    is_primary:
        bool | None = None,
    include_archived:
        bool = Query(
            default=False,
        ),
    segment:
        str | None = Query(
            default=None,
            max_length=100,
        ),
    tag:
        str | None = Query(
            default=None,
            max_length=100,
        ),
    search:
        str | None = Query(
            default=None,
            max_length=250,
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
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await list_contacts(
        db,
        organization_id,
        account_id,
        is_active,
        is_primary,
        include_archived,
        segment,
        tag,
        search,
        skip,
        limit,
    )


@router.get(
    "/duplicates/check",
    response_model=
    ContactDuplicateCheckResponse,
)
async def check_contact_duplicates_endpoint(
    first_name: str | None = Query(
        default=None,
        max_length=120,
    ),
    last_name: str | None = Query(
        default=None,
        max_length=120,
    ),
    email: str | None = Query(
        default=None,
        max_length=320,
    ),
    phone: str | None = Query(
        default=None,
        max_length=50,
    ),
    mobile: str | None = Query(
        default=None,
        max_length=50,
    ),
    account_id: uuid.UUID | None = Query(
        default=None,
    ),
    exclude_contact_id: uuid.UUID | None = Query(
        default=None,
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
    matches = (
        await check_contact_duplicates(
            db=db,
            organization_id=
                organization_id,
            first_name=
                first_name,
            last_name=
                last_name,
            email=
                email,
            phone=
                phone,
            mobile=
                mobile,
            account_id=
                account_id,
            exclude_contact_id=
                exclude_contact_id,
        )
    )

    return ContactDuplicateCheckResponse(
        has_duplicates=
            bool(matches),

        matches=[
            ContactDuplicateMatch(
                contact=
                    match[
                        "contact"
                    ],
                confidence=
                    match[
                        "confidence"
                    ],
                reasons=
                    match[
                        "reasons"
                    ],
            )
            for match
            in matches
        ],
    )


@router.post(
    "/{contact_id}/archive",
    response_model=
    ContactResponse,
)
async def archive_contact_endpoint(
    contact_id: uuid.UUID,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await archive_contact(
        db,
        organization_id,
        contact_id,
    )


@router.post(
    "/{contact_id}/reactivate",
    response_model=
    ContactResponse,
)
async def reactivate_contact_endpoint(
    contact_id: uuid.UUID,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await reactivate_contact(
        db,
        organization_id,
        contact_id,
    )


@router.get(
    "/{contact_id}",
    response_model=
    ContactResponse,
)
async def get_contact_endpoint(
    contact_id: uuid.UUID,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await get_contact(
        db,
        organization_id,
        contact_id,
    )


@router.patch(
    "/{contact_id}",
    response_model=
    ContactResponse,
)
async def update_contact_endpoint(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    organization_id:
        uuid.UUID = Depends(
            get_current_organization_id
        ),
    db:
        AsyncSession = Depends(
            get_db
        ),
):
    return await update_contact(
        db,
        organization_id,
        contact_id,
        payload,
    )