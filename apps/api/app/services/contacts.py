import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
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
            Account.organization_id == organization_id,
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

    if account.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Contacts cannot be assigned "
                "to an archived account."
            ),
        )

    return account


async def clear_other_primary_contacts(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    exclude_contact_id: uuid.UUID | None = None,
) -> None:
    statement = select(Contact).where(
        Contact.organization_id == organization_id,
        Contact.account_id == account_id,
        Contact.is_primary.is_(True),
    )

    if exclude_contact_id is not None:
        statement = statement.where(
            Contact.id != exclude_contact_id
        )

    result = await db.execute(
        statement
    )

    for contact in result.scalars().all():
        contact.is_primary = False


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

    if (
        payload.is_primary
        and
        payload.account_id is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A primary contact must "
                "belong to an account."
            ),
        )

    if (
        payload.is_primary
        and
        payload.account_id is not None
    ):
        await clear_other_primary_contacts(
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
    is_active: bool | None = None,
    is_primary: bool | None = None,
    search: str | None = None,
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
            Contact.account_id
            == account_id
        )

    if is_active is not None:
        statement = statement.where(
            Contact.is_active.is_(
                is_active
            )
        )

    if is_primary is not None:
        statement = statement.where(
            Contact.is_primary.is_(
                is_primary
            )
        )

    if search:
        normalized_search = (
            search.strip().lower()
        )

        if normalized_search:
            pattern = (
                f"%{normalized_search}%"
            )

            statement = statement.where(
                or_(
                    func.lower(
                        Contact.first_name
                    ).like(pattern),
                    func.lower(
                        Contact.last_name
                    ).like(pattern),
                    func.lower(
                        Contact.email
                    ).like(pattern),
                    func.lower(
                        Contact.job_title
                    ).like(pattern),
                    func.lower(
                        Contact.department
                    ).like(pattern),
                )
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

    result = await db.execute(
        statement
    )

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

    contact = (
        result.scalar_one_or_none()
    )

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
        and
        updates["first_name"] is None
    ):
        raise HTTPException(
            status_code=
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "First name cannot be null."
            ),
        )

    if (
        "last_name" in updates
        and
        updates["last_name"] is None
    ):
        raise HTTPException(
            status_code=
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Last name cannot be null."
            ),
        )

    if (
        "account_id" in updates
        and
        updates["account_id"] is not None
    ):
        await validate_contact_account(
            db,
            organization_id,
            updates["account_id"],
        )

    if (
        "account_id" in updates
        and
        updates["account_id"] is None
        and
        contact.is_primary
        and
        "is_primary" not in updates
    ):
        updates["is_primary"] = False

    final_account_id = (
        updates["account_id"]
        if "account_id" in updates
        else contact.account_id
    )

    final_is_primary = (
        updates["is_primary"]
        if "is_primary" in updates
        else contact.is_primary
    )

    final_is_active = (
        updates["is_active"]
        if "is_active" in updates
        else contact.is_active
    )

    if not final_is_active:
        updates["is_primary"] = False
        final_is_primary = False

    if (
        final_is_primary
        and
        final_account_id is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A primary contact must "
                "belong to an account."
            ),
        )

    if (
        final_is_primary
        and
        final_account_id is not None
    ):
        await clear_other_primary_contacts(
            db,
            organization_id,
            final_account_id,
            exclude_contact_id=contact.id,
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