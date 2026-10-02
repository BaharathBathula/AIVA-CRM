import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.schemas.activity import ActivityCreate


async def get_activity_account(
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


async def get_activity_contact(
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


async def create_activity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: ActivityCreate,
) -> Activity:
    account_id = payload.account_id
    contact = None

    if account_id is not None:
        await get_activity_account(
            db,
            organization_id,
            account_id,
        )

    if payload.contact_id is not None:
        contact = await get_activity_contact(
            db,
            organization_id,
            payload.contact_id,
        )

        if (
            account_id is not None
            and contact.account_id is not None
            and contact.account_id != account_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Contact does not belong "
                    "to the supplied account."
                ),
            )

        if (
            account_id is None
            and contact.account_id is not None
        ):
            account_id = contact.account_id

    data = payload.model_dump()

    data["account_id"] = account_id

    activity = Activity(
        organization_id=organization_id,
        created_by_user_id=None,
        **data,
    )

    db.add(activity)

    await db.commit()
    await db.refresh(activity)

    return activity


async def list_account_activities(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[Activity]:
    await get_activity_account(
        db,
        organization_id,
        account_id,
    )

    result = await db.execute(
        select(Activity)
        .where(
            Activity.organization_id
            == organization_id,
            Activity.account_id
            == account_id,
        )
        .order_by(
            Activity.occurred_at.desc()
        )
        .offset(skip)
        .limit(limit)
    )

    return list(
        result.scalars().all()
    )


async def list_contact_activities(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[Activity]:
    await get_activity_contact(
        db,
        organization_id,
        contact_id,
    )

    result = await db.execute(
        select(Activity)
        .where(
            Activity.organization_id
            == organization_id,
            Activity.contact_id
            == contact_id,
        )
        .order_by(
            Activity.occurred_at.desc()
        )
        .offset(skip)
        .limit(limit)
    )

    return list(
        result.scalars().all()
    )
