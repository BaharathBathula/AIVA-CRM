import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.schemas.lead import (
    LeadCreate,
    LeadUpdate,
)


async def validate_lead_owner(
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

    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Lead owner must belong "
                "to the organization."
            ),
        )


async def create_lead(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: LeadCreate,
) -> Lead:
    if payload.owner_user_id is not None:
        await validate_lead_owner(
            db,
            organization_id,
            payload.owner_user_id,
        )

    lead = Lead(
        organization_id=organization_id,
        **payload.model_dump(),
    )

    db.add(lead)

    await db.commit()
    await db.refresh(lead)

    return lead


async def list_leads(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_status: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Lead]:
    statement = select(Lead).where(
        Lead.organization_id
        == organization_id
    )

    if lead_status:
        statement = statement.where(
            Lead.status == lead_status
        )

    if search:
        search_term = (
            f"%{search.strip()}%"
        )

        statement = statement.where(
            or_(
                Lead.first_name.ilike(
                    search_term
                ),
                Lead.last_name.ilike(
                    search_term
                ),
                Lead.email.ilike(
                    search_term
                ),
                Lead.company_name.ilike(
                    search_term
                ),
            )
        )

    statement = (
        statement
        .order_by(
            Lead.created_at.desc()
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


async def get_lead(
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


async def update_lead(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: LeadUpdate,
) -> Lead:
    lead = await get_lead(
        db,
        organization_id,
        lead_id,
    )

    if lead.status == "converted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Converted leads cannot "
                "be edited."
            ),
        )

    updates = payload.model_dump(
        exclude_unset=True
    )

    for field in [
        "first_name",
        "last_name",
    ]:
        if (
            field in updates
            and updates[field] is None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{field.replace('_', ' ').title()} "
                    "cannot be null."
                ),
            )

    owner_user_id = updates.get(
        "owner_user_id"
    )

    if owner_user_id is not None:
        await validate_lead_owner(
            db,
            organization_id,
            owner_user_id,
        )

    for field, value in updates.items():
        setattr(
            lead,
            field,
            value,
        )

    await db.commit()
    await db.refresh(lead)

    return lead
