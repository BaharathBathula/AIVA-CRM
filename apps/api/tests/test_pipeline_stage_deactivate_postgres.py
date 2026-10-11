import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine

@pytest_asyncio.fixture(autouse=True)
async def isolate_postgres_connection_pool():
    await engine.dispose()
    yield
    await engine.dispose()

from app.models.opportunity import Opportunity
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.pipelines import deactivate_pipeline_stage


@pytest.mark.asyncio
async def test_deactivate_stage_postgres_transaction():
    stage_id = None
    original_active = None

    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                pipeline_result = await db.execute(
                    select(Pipeline)
                    .where(Pipeline.is_active.is_(True))
                    .order_by(Pipeline.id)
                )

                selected = None
                selected_stage = None

                for pipeline in pipeline_result.scalars().all():
                    stage_result = await db.execute(
                        select(PipelineStage)
                        .where(
                            PipelineStage.pipeline_id == pipeline.id,
                            PipelineStage.organization_id
                            == pipeline.organization_id,
                            PipelineStage.is_active.is_(True),
                        )
                        .order_by(PipelineStage.position)
                    )

                    stages = list(stage_result.scalars().all())

                    if len(stages) < 2:
                        continue

                    for stage in stages:
                        count_result = await db.execute(
                            select(func.count(Opportunity.id)).where(
                                Opportunity.organization_id
                                == pipeline.organization_id,
                                Opportunity.pipeline_id == pipeline.id,
                                Opportunity.stage_id == stage.id,
                            )
                        )

                        if count_result.scalar_one() == 0:
                            selected = pipeline
                            selected_stage = stage
                            break

                    if selected_stage is not None:
                        break

                if selected_stage is None:
                    pytest.skip(
                        "No unused stage in an active pipeline "
                        "with at least two active stages."
                    )

                stage_id = selected_stage.id
                original_active = selected_stage.is_active

                updated = await deactivate_pipeline_stage(
                    db=db,
                    organization_id=selected.organization_id,
                    pipeline_id=selected.id,
                    stage_id=stage_id,
                )

                assert updated.is_active is False

                await db.refresh(updated)

                assert updated.is_active is False

                print(
                    "PASS: PostgreSQL accepted stage deactivation"
                )

        finally:
            await transaction.rollback()

    async with AsyncSession(engine) as verification_db:
        result = await verification_db.execute(
            select(PipelineStage).where(
                PipelineStage.id == stage_id
            )
        )

        restored = result.scalar_one()

        assert restored.is_active == original_active

    print("PASS: Original stage restored after rollback")


@pytest.mark.asyncio
async def test_reject_occupied_stage_postgres():
    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                pipeline_result = await db.execute(
                    select(Pipeline)
                    .where(Pipeline.is_active.is_(True))
                    .order_by(Pipeline.id)
                )

                selected = None
                selected_stage = None

                for pipeline in pipeline_result.scalars().all():
                    stage_result = await db.execute(
                        select(PipelineStage).where(
                            PipelineStage.pipeline_id == pipeline.id,
                            PipelineStage.organization_id
                            == pipeline.organization_id,
                            PipelineStage.is_active.is_(True),
                        )
                    )

                    stages = list(stage_result.scalars().all())

                    if len(stages) < 2:
                        continue

                    for stage in stages:
                        count_result = await db.execute(
                            select(func.count(Opportunity.id)).where(
                                Opportunity.organization_id
                                == pipeline.organization_id,
                                Opportunity.pipeline_id == pipeline.id,
                                Opportunity.stage_id == stage.id,
                            )
                        )

                        if count_result.scalar_one() > 0:
                            selected = pipeline
                            selected_stage = stage
                            break

                    if selected_stage is not None:
                        break

                if selected_stage is None:
                    pytest.skip(
                        "No occupied stage available for testing."
                    )

                with pytest.raises(HTTPException) as exc:
                    await deactivate_pipeline_stage(
                        db=db,
                        organization_id=selected.organization_id,
                        pipeline_id=selected.id,
                        stage_id=selected_stage.id,
                    )

                assert exc.value.status_code == 409

                print(
                    "PASS: PostgreSQL rejected occupied stage"
                )

        finally:
            await transaction.rollback()
