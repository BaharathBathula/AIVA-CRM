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


# AIVA_P32_CREATE_PIPELINE
from sqlalchemy.exc import IntegrityError

PIPELINE_DEFAULT_STAGES = (
    ("Qualification", 1, 10, "open"),
    ("Discovery", 2, 25, "open"),
    ("Demo", 3, 40, "open"),
    ("Proposal", 4, 60, "open"),
    ("Negotiation", 5, 80, "open"),
    ("Closed Won", 6, 100, "won"),
    ("Closed Lost", 7, 0, "lost"),
)


async def create_pipeline(
    db: AsyncSession,
    organization_id: uuid.UUID,
    name: str,
) -> Pipeline:
    normalized_name = name.strip()

    if not normalized_name or len(normalized_name) > 150:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pipeline name must contain 1 to 150 characters.",
        )

    existing = await db.execute(
        select(Pipeline.id).where(
            Pipeline.organization_id == organization_id,
            Pipeline.name == normalized_name,
        )
    )

    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pipeline with this name already exists.",
        )

    pipeline = Pipeline(
        organization_id=organization_id,
        name=normalized_name,
        is_default=False,
        is_active=True,
    )

    try:
        db.add(pipeline)
        await db.flush()

        for stage_name, position, probability, category in PIPELINE_DEFAULT_STAGES:
            db.add(
                PipelineStage(
                    organization_id=organization_id,
                    pipeline_id=pipeline.id,
                    name=stage_name,
                    position=position,
                    probability=probability,
                    category=category,
                    is_active=True,
                )
            )

        await db.commit()
        await db.refresh(pipeline)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pipeline with this name already exists.",
        ) from exc

    except Exception:
        await db.rollback()
        raise

    return pipeline
