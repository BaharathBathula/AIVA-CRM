import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.pipelines import reactivate_pipeline_stage


@pytest_asyncio.fixture(autouse=True)
async def isolate_postgres_connection_pool():
    await engine.dispose()
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_reactivation_postgres_transaction():
    import uuid

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
                    .limit(1)
                )

                pipeline = pipeline_result.scalar_one_or_none()

                if pipeline is None:
                    pytest.skip("No active pipeline exists.")

                existing_stages = await db.execute(
                    select(PipelineStage).where(
                        PipelineStage.pipeline_id == pipeline.id,
                        PipelineStage.organization_id
                        == pipeline.organization_id,
                    )
                )

                stages = list(existing_stages.scalars().all())

                next_position = max(
                    (stage.position for stage in stages),
                    default=0,
                ) + 1

                stage = PipelineStage(
                    id=uuid.uuid4(),
                    organization_id=pipeline.organization_id,
                    pipeline_id=pipeline.id,
                    name=f"P56 Test {uuid.uuid4().hex[:12]}",
                    position=next_position,
                    probability=25,
                    category="open",
                    is_active=False,
                )

                db.add(stage)
                await db.flush()

                original = (
                    stage.id,
                    stage.name,
                    stage.position,
                    stage.probability,
                    stage.category,
                )

                result = await reactivate_pipeline_stage(
                    db=db,
                    organization_id=pipeline.organization_id,
                    pipeline_id=pipeline.id,
                    stage_id=stage.id,
                )

                assert result.is_active is True

                assert (
                    result.id,
                    result.name,
                    result.position,
                    result.probability,
                    result.category,
                ) == original

                verification = await db.execute(
                    select(PipelineStage).where(
                        PipelineStage.id == stage.id
                    )
                )

                saved_stage = verification.scalar_one()

                assert saved_stage.is_active is True

        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_reject_already_active_stage_postgres():
    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                pipelines = await db.execute(
                    select(Pipeline)
                    .where(Pipeline.is_active.is_(True))
                    .order_by(Pipeline.id)
                )

                selected_pipeline = None
                selected_stage = None

                for pipeline in pipelines.scalars().all():
                    stages = await db.execute(
                        select(PipelineStage)
                        .where(
                            PipelineStage.pipeline_id == pipeline.id,
                            PipelineStage.organization_id
                            == pipeline.organization_id,
                            PipelineStage.is_active.is_(True),
                        )
                        .order_by(PipelineStage.id)
                    )

                    stage = stages.scalars().first()

                    if stage is not None:
                        selected_pipeline = pipeline
                        selected_stage = stage
                        break

                if selected_stage is None:
                    pytest.skip(
                        "No active stage available for integration test."
                    )

                with pytest.raises(HTTPException) as error:
                    await reactivate_pipeline_stage(
                        db=db,
                        organization_id=selected_pipeline.organization_id,
                        pipeline_id=selected_pipeline.id,
                        stage_id=selected_stage.id,
                    )

                assert error.value.status_code == 409

        finally:
            await transaction.rollback()
