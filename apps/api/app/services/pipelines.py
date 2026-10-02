import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage


async def list_pipelines(
    db: AsyncSession,
    organization_id: uuid.UUID,
    include_inactive: bool = False,
) -> list[Pipeline]:
    statement = select(
        Pipeline
    ).where(
        Pipeline.organization_id
        == organization_id
    )

    if not include_inactive:
        statement = statement.where(
            Pipeline.is_active.is_(True)
        )

    statement = statement.order_by(
        Pipeline.is_default.desc(),
        Pipeline.name.asc(),
    )

    result = await db.execute(
        statement
    )

    return list(
        result.scalars().all()
    )


async def get_pipeline(
    db: AsyncSession,
    organization_id: uuid.UUID,
    pipeline_id: uuid.UUID,
) -> Pipeline:
    result = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id,
            Pipeline.organization_id
            == organization_id,
        )
    )

    pipeline = (
        result.scalar_one_or_none()
    )

    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline not found.",
        )

    return pipeline


async def list_pipeline_stages(
    db: AsyncSession,
    organization_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    include_inactive: bool = False,
) -> list[PipelineStage]:
    await get_pipeline(
        db,
        organization_id,
        pipeline_id,
    )

    statement = select(
        PipelineStage
    ).where(
        PipelineStage.organization_id
        == organization_id,
        PipelineStage.pipeline_id
        == pipeline_id,
    )

    if not include_inactive:
        statement = statement.where(
            PipelineStage.is_active.is_(
                True
            )
        )

    statement = statement.order_by(
        PipelineStage.position.asc()
    )

    result = await db.execute(
        statement
    )

    return list(
        result.scalars().all()
    )


async def get_pipeline_stage(
    db: AsyncSession,
    organization_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
) -> PipelineStage:
    result = await db.execute(
        select(PipelineStage).where(
            PipelineStage.id == stage_id,
            PipelineStage.pipeline_id
            == pipeline_id,
            PipelineStage.organization_id
            == organization_id,
        )
    )

    stage = (
        result.scalar_one_or_none()
    )

    if stage is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline stage not found.",
        )

    return stage
