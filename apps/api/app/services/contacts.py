import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.schemas.contact import (
    ContactCreate,
    ContactUpdate,
)


async def validate_contact_account(
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Account does not exist "
                "in this organization."
            ),
        )

    return account


async def create_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: ContactCreate,
) -> Contact:
    if payload.account_id is not None:
        await validate_contact_account(
            db,
            organization_id,
            payload.account_id,
        )

    contact = Contact(
        organization_id=organization_id,
        **payload.model_dump(),
    )

    db.add(contact)

    await db.commit()
    await db.refresh(contact)

    return contact


async def list_contacts(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Contact]:
    statement = select(Contact).where(
        Contact.organization_id
        == organization_id
    )

    if account_id is not None:
        await validate_contact_account(
            db,
            organization_id,
            account_id,
        )

        statement = statement.where(
            Contact.account_id == account_id
        )

    statement = (
        statement
        .order_by(
            Contact.last_name.asc(),
            Contact.first_name.asc(),
        )
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(statement)

    return list(
        result.scalars().all()
    )


async def get_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
) -> Contact:
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.organization_id
            == organization_id,
        )
    )

    contact = result.scalar_one_or_none()

    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found.",
        )

    return contact


async def update_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    payload: ContactUpdate,
) -> Contact:
    contact = await get_contact(
        db,
        organization_id,
        contact_id,
    )

    updates = payload.model_dump(
        exclude_unset=True
    )

    if (
        "first_name" in updates
        and updates["first_name"] is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="First name cannot be null.",
        )

    if (
        "last_name" in updates
        and updates["last_name"] is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Last name cannot be null.",
        )

    if (
        "account_id" in updates
        and updates["account_id"] is not None
    ):
        await validate_contact_account(
            db,
            organization_id,
            updates["account_id"],
        )

    for field, value in updates.items():
        setattr(
            contact,
            field,
            value,
        )

    await db.commit()
    await db.refresh(contact)

    return contact
