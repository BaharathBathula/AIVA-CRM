import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.opportunity import Opportunity


ALLOWED_FORECAST_CATEGORIES = {
    "pipeline",
    "best_case",
    "commit",
    "closed",
    "omitted",
}


async def bulk_change_opportunity_forecast(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_ids: list[uuid.UUID],
    forecast_category: str,
) -> dict:
    if forecast_category not in ALLOWED_FORECAST_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid forecast category.",
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
        if opportunity.forecast_category != forecast_category:
            opportunity.forecast_category = forecast_category
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
        "forecast_category": forecast_category,
    }
