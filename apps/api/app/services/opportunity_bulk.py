from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.user import User


async def bulk_change_opportunity_owner(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_ids: list[uuid.UUID],
    owner_user_id: uuid.UUID,
) -> dict:
    """
    Atomically change the owner of selected opportunities.

    All selected opportunities must belong to the same
    organization. Any validation failure prevents updates.
    """

    if not opportunity_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select at least one opportunity.",
        )

    if len(opportunity_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Maximum 100 opportunities per request.",
        )

    if len(set(opportunity_ids)) != len(opportunity_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Duplicate opportunity IDs are not allowed.",
        )

    owner_result = await db.execute(
        select(User.id)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == User.id,
        )
        .where(
            User.id == owner_user_id,
            User.is_active.is_(True),
            OrganizationMembership.organization_id
            == organization_id,
        )
    )

    if owner_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "New owner must be an active member "
                "of the organization."
            ),
        )

    result = await db.execute(
        select(Opportunity)
        .where(
            Opportunity.organization_id == organization_id,
            Opportunity.id.in_(opportunity_ids),
        )
        .order_by(Opportunity.id)
        .with_for_update()
    )

    opportunities = result.scalars().all()

    if len(opportunities) != len(opportunity_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "One or more opportunities were not found "
                "in this organization."
            ),
        )

    changed_ids: list[uuid.UUID] = []

    for opportunity in opportunities:
        if opportunity.owner_user_id != owner_user_id:
            opportunity.owner_user_id = owner_user_id
            changed_ids.append(opportunity.id)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "updated_count": len(changed_ids),
        "updated_ids": changed_ids,
        "requested_count": len(opportunity_ids),
        "owner_user_id": owner_user_id,
    }
