import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.membership import OrganizationMembership
from app.schemas.account import (
    AccountCreate,
    AccountUpdate,
)


async def validate_account_owner(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    result = await db.execute(
        select(OrganizationMembership.id).where(
            OrganizationMembership.organization_id
            == organization_id,
            OrganizationMembership.user_id
            == user_id,
        )
    )

    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Account owner must belong "
                "to the organization."
            ),
        )


async def create_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: AccountCreate,
) -> Account:
    if payload.owner_user_id:
        await validate_account_owner(
            db,
            organization_id,
            payload.owner_user_id,
        )

    account = Account(
        organization_id=organization_id,
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
) -> list[Account]:
    result = await db.execute(
        select(Account)
        .where(
            Account.organization_id
            == organization_id
        )
        .order_by(Account.name.asc())
        .offset(skip)
        .limit(limit)
    )

    return list(
        result.scalars().all()
    )


async def get_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Account:
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.organization_id
            == organization_id,
        )
    )

    account = result.scalar_one_or_none()

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found.",
        )

    return account


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
        and updates["name"] is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Account name cannot be null.",
        )

    owner_user_id = updates.get(
        "owner_user_id"
    )

    if owner_user_id is not None:
        await validate_account_owner(
            db,
            organization_id,
            owner_user_id,
        )

    for field, value in updates.items():
        setattr(
            account,
            field,
            value,
        )

    await db.commit()
    await db.refresh(account)

    return account
