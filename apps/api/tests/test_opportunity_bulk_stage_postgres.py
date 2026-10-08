import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.opportunity import Opportunity
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.opportunity_bulk_stage import (
    bulk_change_opportunity_stage,
)


@pytest.mark.asyncio
async def test_bulk_stage_change_with_transaction_rollback():
    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                result = await db.execute(
                    select(Opportunity)
                    .order_by(Opportunity.id)
                    .limit(1)
                )
                opportunity = result.scalar_one_or_none()

                if opportunity is None:
                    pytest.skip(
                        "No opportunities available for integration test."
                    )

                stages_result = await db.execute(
                    select(PipelineStage)
                    .join(
                        Pipeline,
                        Pipeline.id == PipelineStage.pipeline_id,
                    )
                    .where(
                        PipelineStage.organization_id
                        == opportunity.organization_id,
                        PipelineStage.is_active.is_(True),
                        Pipeline.is_active.is_(True),
                        PipelineStage.id != opportunity.stage_id,
                    )
                    .order_by(PipelineStage.id)
                    .limit(1)
                )
                target_stage = stages_result.scalar_one_or_none()

                if target_stage is None:
                    pytest.skip(
                        "No alternative active stage available."
                    )

                original_stage_id = opportunity.stage_id

                response = await bulk_change_opportunity_stage(
                    db=db,
                    organization_id=opportunity.organization_id,
                    opportunity_ids=[opportunity.id],
                    pipeline_id=target_stage.pipeline_id,
                    stage_id=target_stage.id,
                )

                assert response["updated_count"] == 1

                await db.refresh(opportunity)

                assert opportunity.stage_id == target_stage.id
                assert opportunity.pipeline_id == target_stage.pipeline_id
                assert opportunity.probability == target_stage.probability
                assert opportunity.stage_id != original_stage_id

                print(
                    "\nPASS: Real PostgreSQL bulk stage update"
                )
                print(
                    "PASS: Pipeline and probability synchronized"
                )
        finally:
            await transaction.rollback()

    print("PASS: Test transaction rolled back")
