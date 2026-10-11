import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.pipelines import reactivate_pipeline_stage


@pytest_asyncio.fixture(autouse=True)
async def isolate_pool():
    await engine.dispose()
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_reactivation_waits_for_pipeline_lock():
    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as setup:
                pipeline_result = await setup.execute(
                    select(Pipeline)
                    .where(Pipeline.is_active.is_(True))
                    .order_by(Pipeline.id)
                    .limit(1)
                )

                pipeline = pipeline_result.scalar_one_or_none()

                if pipeline is None:
                    pytest.skip("No active pipeline available.")

                stages_result = await setup.execute(
                    select(PipelineStage).where(
                        PipelineStage.pipeline_id == pipeline.id
                    )
                )

                existing = list(stages_result.scalars().all())

                stage = PipelineStage(
                    id=uuid.uuid4(),
                    organization_id=pipeline.organization_id,
                    pipeline_id=pipeline.id,
                    name=f"P56 Lock {uuid.uuid4().hex[:12]}",
                    position=max(
                        (item.position for item in existing),
                        default=0,
                    ) + 1,
                    probability=25,
                    category="open",
                    is_active=False,
                )

                setup.add(stage)
                await setup.flush()

                stage_id = stage.id
                pipeline_id = pipeline.id
                organization_id = pipeline.organization_id

                # Hold the parent pipeline row lock.
                await setup.execute(
                    select(Pipeline)
                    .where(Pipeline.id == pipeline_id)
                    .with_for_update()
                )

                # A separate session must not acquire the same lock.
                async with AsyncSession(engine) as competing:
                    await competing.execute(
                        text("SET LOCAL lock_timeout = '300ms'")
                    )

                    with pytest.raises(DBAPIError) as error:
                        await reactivate_pipeline_stage(
                            db=competing,
                            organization_id=organization_id,
                            pipeline_id=pipeline_id,
                            stage_id=stage_id,
                        )

                    assert getattr(
                        error.value.orig, "sqlstate", None
                    ) == "55P03"

                assert stage.is_active is False

        finally:
            await transaction.rollback()



@pytest.mark.asyncio
async def test_duplicate_concurrent_reactivation():
    """Only one concurrent request may reactivate a stage."""
    import asyncio
    from fastapi import HTTPException

    # This test uses a committed fixture so both sessions
    # can observe the same stage. Cleanup is guaranteed.
    stage_id = uuid.uuid4()
    created = False
    pipeline_id = None
    organization_id = None

    async with AsyncSession(engine) as setup:
        result = await setup.execute(
            select(Pipeline)
            .where(Pipeline.is_active.is_(True))
            .order_by(Pipeline.id)
            .limit(1)
        )

        pipeline = result.scalar_one_or_none()

        if pipeline is None:
            pytest.skip("No active pipeline available.")

        pipeline_id = pipeline.id
        organization_id = pipeline.organization_id

        existing = await setup.execute(
            select(PipelineStage.position).where(
                PipelineStage.pipeline_id == pipeline_id
            )
        )

        next_position = max(
            existing.scalars().all(),
            default=0,
        ) + 1

        stage = PipelineStage(
            id=stage_id,
            organization_id=organization_id,
            pipeline_id=pipeline_id,
            name=f"P56 Concurrent {uuid.uuid4().hex[:12]}",
            position=next_position,
            probability=25,
            category="open",
            is_active=False,
        )

        setup.add(stage)
        await setup.commit()
        created = True

    try:
        async def reactivate():
            async with AsyncSession(engine) as db:
                try:
                    result = await reactivate_pipeline_stage(
                        db=db,
                        organization_id=organization_id,
                        pipeline_id=pipeline_id,
                        stage_id=stage_id,
                    )
                    return ("success", result.id)
                except HTTPException as error:
                    return ("error", error.status_code)

        results = await asyncio.wait_for(
            asyncio.gather(
                reactivate(),
                reactivate(),
            ),
            timeout=10,
        )

        successes = [
            item for item in results
            if item[0] == "success"
        ]

        conflicts = [
            item for item in results
            if item == ("error", 409)
        ]

        assert len(successes) == 1
        assert len(conflicts) == 1

        async with AsyncSession(engine) as verification:
            result = await verification.execute(
                select(PipelineStage).where(
                    PipelineStage.id == stage_id
                )
            )

            saved_stage = result.scalar_one()
            assert saved_stage.is_active is True

    finally:
        if created:
            async with AsyncSession(engine) as cleanup:
                stage_result = await cleanup.execute(
                    select(PipelineStage).where(
                        PipelineStage.id == stage_id
                    )
                )

                stage = stage_result.scalar_one_or_none()

                if stage is not None:
                    await cleanup.delete(stage)
                    await cleanup.commit()
