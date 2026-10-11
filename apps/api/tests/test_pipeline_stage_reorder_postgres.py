import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.pipelines import reorder_pipeline_stages


@pytest.mark.asyncio
async def test_reorder_stages_postgres_transaction():
    pipeline_id = None
    original_positions = {}

    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                result = await db.execute(
                    select(Pipeline)
                    .where(Pipeline.is_active.is_(True))
                    .order_by(Pipeline.id)
                )

                pipelines = result.scalars().all()
                selected = None
                stages = []

                for pipeline in pipelines:
                    stage_result = await db.execute(
                        select(PipelineStage)
                        .where(
                            PipelineStage.pipeline_id == pipeline.id,
                            PipelineStage.organization_id
                            == pipeline.organization_id,
                            PipelineStage.is_active.is_(True),
                        )
                        .order_by(PipelineStage.position.asc())
                    )

                    candidates = list(stage_result.scalars().all())

                    if len(candidates) >= 2:
                        selected = pipeline
                        stages = candidates
                        break

                if selected is None:
                    pytest.skip(
                        "No active pipeline with two stages available."
                    )

                pipeline_id = selected.id

                original_positions = {
                    stage.id: stage.position
                    for stage in stages
                }

                original_metadata = {
                    stage.id: (
                        stage.name,
                        stage.probability,
                        stage.category,
                    )
                    for stage in stages
                }

                desired_ids = [
                    stage.id for stage in reversed(stages)
                ]

                updated = await reorder_pipeline_stages(
                    db=db,
                    organization_id=selected.organization_id,
                    pipeline_id=pipeline_id,
                    stage_ids=desired_ids,
                )

                assert [stage.id for stage in updated] == desired_ids

                updated_positions = [
                    stage.position for stage in updated
                ]

                assert len(updated_positions) == len(
                    set(updated_positions)
                )

                assert updated_positions == sorted(
                    updated_positions
                )

                for stage in updated:
                    assert (
                        stage.name,
                        stage.probability,
                        stage.category,
                    ) == original_metadata[stage.id]

                assert any(
                    stage.position != original_positions[stage.id]
                    for stage in updated
                )

                print(
                    "\nPASS: PostgreSQL accepted stage reorder"
                )
                print(
                    "PASS: Unique positions preserved"
                )
                print(
                    "PASS: Stage metadata unchanged"
                )

        finally:
            await transaction.rollback()

    async with AsyncSession(engine) as verification_db:
        result = await verification_db.execute(
            select(PipelineStage)
            .where(
                PipelineStage.pipeline_id == pipeline_id,
                PipelineStage.id.in_(
                    list(original_positions)
                ),
            )
        )

        restored = list(result.scalars().all())

        assert len(restored) == len(original_positions)

        for stage in restored:
            assert (
                stage.position
                == original_positions[stage.id]
            )

    print("PASS: Original positions restored after rollback")
