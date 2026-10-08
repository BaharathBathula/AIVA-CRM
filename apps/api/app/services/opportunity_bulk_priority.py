import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.opportunity import Opportunity


ALLOWED_PRIORITIES = {
    "low",
    "medium",
    "high",
    "critical",
}


async def bulk_change_opportunity_priority(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_ids: list[uuid.UUID],
    priority: str,
) -> dict:
    if priority not in ALLOWED_PRIORITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid opportunity priority.",
        )

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
        if opportunity.priority != priority:
            opportunity.priority = priority
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
        "priority": priority,
    }
