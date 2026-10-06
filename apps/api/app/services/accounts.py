import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.membership import OrganizationMembership
from app.schemas.account import (
    AccountCreate,
    AccountDuplicateCheckResponse,
    AccountDuplicateMatch,
    AccountResponse,
    AccountUpdate,
)


def normalize_account_name(
    value: str,
) -> str:
    return " ".join(
        value
        .strip()
        .lower()
        .split()
    )


def normalize_domain(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized = (
        value
        .strip()
        .lower()
    )

    if not normalized:
        return None

    for prefix in (
        "https://",
        "http://",
    ):
        if normalized.startswith(
            prefix
        ):
            normalized = normalized[
                len(prefix):
            ]

    if normalized.startswith(
        "www."
    ):
        normalized = normalized[4:]

    normalized = normalized.split(
        "/",
        1,
    )[0]

    normalized = normalized.rstrip(
        "."
    )

    return (
        normalized
        or None
    )


async def validate_account_owner(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    result = await db.execute(
        select(
            OrganizationMembership.id
        ).where(
            OrganizationMembership.organization_id
            == organization_id,
            OrganizationMembership.user_id
            == user_id,
        )
    )

    if (
        result.scalar_one_or_none()
        is None
    ):
        raise HTTPException(
            status_code=
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "Account owner must belong "
                "to the organization."
            ),
        )


async def get_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Account:
    result = await db.execute(
        select(Account).where(
            Account.id
            == account_id,
            Account.organization_id
            == organization_id,
        )
    )

    account = (
        result.scalar_one_or_none()
    )

    if account is None:
        raise HTTPException(
            status_code=
            status.HTTP_404_NOT_FOUND,
            detail="Account not found.",
        )

    return account


async def validate_parent_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    parent_account_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
) -> Account:
    if (
        account_id is not None
        and
        parent_account_id
        == account_id
    ):
        raise HTTPException(
            status_code=
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "An account cannot be "
                "its own parent."
            ),
        )

    parent = await get_account(
        db,
        organization_id,
        parent_account_id,
    )

    if parent.is_archived:
        raise HTTPException(
            status_code=
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "An archived account cannot "
                "be assigned as a parent "
                "account."
            ),
        )

    if account_id is None:
        return parent

    current: Account | None = parent

    visited: set[
        uuid.UUID
    ] = set()

    while current is not None:
        if (
            current.id
            == account_id
        ):
            raise HTTPException(
                status_code=
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Account hierarchy "
                    "cycle detected."
                ),
            )

        if (
            current.id
            in visited
        ):
            raise HTTPException(
                status_code=
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Existing account "
                    "hierarchy contains "
                    "a cycle."
                ),
            )

        visited.add(
            current.id
        )

        if (
            current.parent_account_id
            is None
        ):
            break

        current = await get_account(
            db,
            organization_id,
            current.parent_account_id,
        )

    return parent


async def check_account_duplicates(
    db: AsyncSession,
    organization_id: uuid.UUID,
    name: str,
    domain: str | None = None,
    exclude_account_id:
        uuid.UUID | None = None,
) -> AccountDuplicateCheckResponse:
    normalized_name = (
        normalize_account_name(
            name
        )
    )

    normalized_domain = (
        normalize_domain(
            domain
        )
    )

    result = await db.execute(
        select(Account)
        .where(
            Account.organization_id
            == organization_id
        )
        .order_by(
            Account.name.asc()
        )
    )

    accounts = list(
        result.scalars().all()
    )

    matches: list[
        AccountDuplicateMatch
    ] = []


    for account in accounts:
        if (
            exclude_account_id
            is not None
            and
            account.id
            == exclude_account_id
        ):
            continue


        match_reasons: list[str] = []


        existing_name = (
            normalize_account_name(
                account.name
            )
        )

        existing_domain = (
            normalize_domain(
                account.domain
            )
        )


        if (
            existing_name
            == normalized_name
        ):
            match_reasons.append(
                "same_name"
            )


        if (
            normalized_domain
            is not None
            and
            existing_domain
            is not None
            and
            existing_domain
            == normalized_domain
        ):
            match_reasons.append(
                "same_domain"
            )


        if not match_reasons:
            continue


        confidence = (
            "high"
            if (
                "same_domain"
                in match_reasons
                or
                len(
                    match_reasons
                )
                > 1
            )
            else
            "medium"
        )


        matches.append(
            AccountDuplicateMatch(
                account=
                AccountResponse
                .model_validate(
                    account
                ),

                match_reasons=
                match_reasons,

                confidence=
                confidence,
            )
        )


    matches.sort(
        key=lambda item: (
            0
            if item.confidence
            == "high"
            else 1,

            item.account.name
            .lower(),
        )
    )


    return (
        AccountDuplicateCheckResponse(
            has_duplicates=
            len(matches)
            > 0,

            matches=
            matches,
        )
    )


async def create_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: AccountCreate,
) -> Account:
    if (
        payload.owner_user_id
        is not None
    ):
        await validate_account_owner(
            db,
            organization_id,
            payload.owner_user_id,
        )

    if (
        payload.parent_account_id
        is not None
    ):
        await validate_parent_account(
            db,
            organization_id,
            payload.parent_account_id,
        )

    account = Account(
        organization_id=
        organization_id,

        **payload.model_dump(),
    )

    db.add(account)

    await db.commit()
    await db.refresh(account)

    return account


async def list_accounts(
    db: AsyncSession,
    organization_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
    include_archived: bool = False,
    parent_account_id:
        uuid.UUID | None = None,
) -> list[Account]:
    statement = (
        select(Account)
        .where(
            Account.organization_id
            == organization_id
        )
    )

    if not include_archived:
        statement = (
            statement.where(
                Account.is_archived
                .is_(False)
            )
        )

    if (
        parent_account_id
        is not None
    ):
        statement = (
            statement.where(
                Account.parent_account_id
                == parent_account_id
            )
        )

    statement = (
        statement
        .order_by(
            Account.name.asc()
        )
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(
        statement
    )

    return list(
        result.scalars().all()
    )


async def list_child_accounts(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    include_archived: bool = False,
) -> list[Account]:
    await get_account(
        db,
        organization_id,
        account_id,
    )

    statement = (
        select(Account)
        .where(
            Account.organization_id
            == organization_id,

            Account.parent_account_id
            == account_id,
        )
    )

    if not include_archived:
        statement = (
            statement.where(
                Account.is_archived
                .is_(False)
            )
        )

    statement = (
        statement.order_by(
            Account.name.asc()
        )
    )

    result = await db.execute(
        statement
    )

    return list(
        result.scalars().all()
    )


async def update_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    payload: AccountUpdate,
) -> Account:
    account = await get_account(
        db,
        organization_id,
        account_id,
    )

    updates = payload.model_dump(
        exclude_unset=True
    )

    if (
        "name" in updates
        and
        updates["name"]
        is None
    ):
        raise HTTPException(
            status_code=
            status.HTTP_422_UNPROCESSABLE_ENTITY,

            detail=(
                "Account name cannot "
                "be null."
            ),
        )


    if (
        "owner_user_id"
        in updates
    ):
        owner_user_id = (
            updates[
                "owner_user_id"
            ]
        )

        if (
            owner_user_id
            is not None
        ):
            await validate_account_owner(
                db,
                organization_id,
                owner_user_id,
            )


    if (
        "parent_account_id"
        in updates
    ):
        parent_account_id = (
            updates[
                "parent_account_id"
            ]
        )

        if (
            parent_account_id
            is not None
        ):
            await validate_parent_account(
                db,
                organization_id,
                parent_account_id,
                account_id,
            )


    for (
        field,
        value,
    ) in updates.items():
        setattr(
            account,
            field,
            value,
        )


    await db.commit()
    await db.refresh(account)

    return account


async def archive_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Account:
    account = await get_account(
        db,
        organization_id,
        account_id,
    )

    if account.is_archived:
        return account

    account.is_archived = True

    account.archived_at = (
        datetime.now(
            timezone.utc
        )
    )

    await db.commit()
    await db.refresh(account)

    return account


async def reactivate_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Account:
    account = await get_account(
        db,
        organization_id,
        account_id,
    )

    if not account.is_archived:
        return account

    account.is_archived = False
    account.archived_at = None

    await db.commit()
    await db.refresh(account)

    return account