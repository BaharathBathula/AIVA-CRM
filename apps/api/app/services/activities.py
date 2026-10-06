from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.opportunity import Opportunity
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


async def get_activity_lead(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> Lead:
    result = await db.execute(
        select(Lead).where(
            Lead.id == lead_id,
            Lead.organization_id
            == organization_id,
        )
    )

    lead = result.scalar_one_or_none()

    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found.",
        )

    return lead


async def get_activity_opportunity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> Opportunity:
    result = await db.execute(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.organization_id
            == organization_id,
        )
    )

    opportunity = result.scalar_one_or_none()

    if opportunity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found.",
        )

    return opportunity


async def create_activity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: ActivityCreate,
) -> Activity:
    account_id = payload.account_id
    contact = None
    opportunity = None

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

    if payload.lead_id is not None:
        await get_activity_lead(
            db,
            organization_id,
            payload.lead_id,
        )

    if payload.opportunity_id is not None:
        opportunity = await get_activity_opportunity(
            db,
            organization_id,
            payload.opportunity_id,
        )

        if (
            account_id is not None
            and opportunity.account_id
            != account_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Opportunity does not belong "
                    "to the supplied account."
                ),
            )

        if account_id is None:
            account_id = opportunity.account_id

        if (
            contact is not None
            and contact.account_id is not None
            and contact.account_id
            != opportunity.account_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Contact and opportunity "
                    "belong to different accounts."
                ),
            )

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


async def add_automated_activity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    activity_type: str,
    subject: str,
    body: str | None = None,
    account_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    activity_metadata: dict | None = None,
) -> Activity:
    activity = Activity(
        organization_id=organization_id,
        account_id=account_id,
        contact_id=contact_id,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
        created_by_user_id=None,
        activity_type=activity_type,
        subject=subject,
        body=body,
        direction=None,
        occurred_at=(
            occurred_at
            or datetime.now(timezone.utc)
        ),
        external_id=None,
        activity_metadata=(
            activity_metadata or {}
        ),
    )

    db.add(activity)

    return activity


async def list_activities(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    activity_type: str | None = None,
    direction: str | None = None,
    account_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Activity]:
    query = select(Activity).where(
        Activity.organization_id
        == organization_id
    )

    if activity_type is not None:
        query = query.where(
            Activity.activity_type
            == activity_type
        )

    if direction is not None:
        query = query.where(
            Activity.direction
            == direction
        )

    if account_id is not None:
        query = query.where(
            Activity.account_id
            == account_id
        )

    if contact_id is not None:
        query = query.where(
            Activity.contact_id
            == contact_id
        )

    if lead_id is not None:
        query = query.where(
            Activity.lead_id
            == lead_id
        )

    if opportunity_id is not None:
        query = query.where(
            Activity.opportunity_id
            == opportunity_id
        )

    if occurred_from is not None:
        query = query.where(
            Activity.occurred_at
            >= occurred_from
        )

    if occurred_to is not None:
        query = query.where(
            Activity.occurred_at
            <= occurred_to
        )

    if search:
        normalized_search = (
            search.strip()
        )

        if normalized_search:
            pattern = (
                f"%{normalized_search}%"
            )

            query = query.where(
                or_(
                    Activity.subject.ilike(
                        pattern
                    ),
                    Activity.body.ilike(
                        pattern
                    ),
                )
            )

    query = (
        query
        .order_by(
            Activity.occurred_at.desc(),
            Activity.created_at.desc(),
        )
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(
        query
    )

    return list(
        result.scalars().all()
    )


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

    return await list_activities(
        db,
        organization_id,
        account_id=account_id,
        skip=skip,
        limit=limit,
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

    return await list_activities(
        db,
        organization_id,
        contact_id=contact_id,
        skip=skip,
        limit=limit,
    )


async def list_lead_activities(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[Activity]:
    await get_activity_lead(
        db,
        organization_id,
        lead_id,
    )

    return await list_activities(
        db,
        organization_id,
        lead_id=lead_id,
        skip=skip,
        limit=limit,
    )


async def list_opportunity_activities(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[Activity]:
    await get_activity_opportunity(
        db,
        organization_id,
        opportunity_id,
    )

    return await list_activities(
        db,
        organization_id,
        opportunity_id=opportunity_id,
        skip=skip,
        limit=limit,
    )
