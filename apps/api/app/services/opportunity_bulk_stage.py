import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.opportunity import Opportunity
from app.services.opportunities import (
    apply_stage_state,
    resolve_pipeline_and_stage,
)


async def bulk_change_opportunity_stage(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_ids: list[uuid.UUID],
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
) -> dict:
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

    pipeline, stage = await resolve_pipeline_and_stage(
        db=db,
        organization_id=organization_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
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

    changed_ids = []

    for opportunity in opportunities:
        if (
            opportunity.pipeline_id == pipeline.id
            and opportunity.stage_id == stage.id
        ):
            continue

        apply_stage_state(opportunity, stage)
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
        "pipeline_id": pipeline.id,
        "stage_id": stage.id,
    }
