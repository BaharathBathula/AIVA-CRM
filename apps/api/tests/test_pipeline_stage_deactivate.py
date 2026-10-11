import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.pipelines import deactivate_pipeline_stage


def make_objects():
    organization_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()

    pipeline = Pipeline(
        id=pipeline_id,
        organization_id=organization_id,
        name="Test Pipeline",
        is_active=True,
        is_default=False,
    )

    stages = [
        PipelineStage(
            id=uuid.uuid4(),
            organization_id=organization_id,
            pipeline_id=pipeline_id,
            name=f"Stage {index}",
            position=index,
            probability=20,
            category="open",
            is_active=True,
        )
        for index in range(1, 4)
    ]

    return organization_id, pipeline_id, pipeline, stages


def make_db(pipeline, stages, opportunity_count=0):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()

    pipeline_result = MagicMock()
    pipeline_result.scalar_one_or_none.return_value = pipeline

    stages_result = MagicMock()
    stages_result.scalars.return_value.all.return_value = stages

    usage_result = MagicMock()
    usage_result.scalar_one.return_value = opportunity_count

    db.execute.side_effect = [
        pipeline_result,
        stages_result,
        usage_result,
    ]

    return db


@pytest.mark.asyncio
async def test_deactivate_unused_stage():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    result = await deactivate_pipeline_stage(
        db, org_id, pipeline_id, stages[0].id
    )

    assert result.id == stages[0].id
    assert result.is_active is False
    assert stages[1].is_active is True
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(stages[0])
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_last_active_stage():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages[:1])

    with pytest.raises(HTTPException) as exc:
        await deactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[0].id
        )

    assert exc.value.status_code == 409
    assert stages[0].is_active is True
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("opportunity_count", [1, 5])
async def test_reject_stage_with_opportunities(opportunity_count):
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages, opportunity_count)

    with pytest.raises(HTTPException) as exc:
        await deactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[0].id
        )

    assert exc.value.status_code == 409
    assert str(opportunity_count) in str(exc.value.detail)
    assert stages[0].is_active is True
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_missing_or_other_tenant_pipeline():
    org_id, pipeline_id, _, stages = make_objects()
    db = make_db(None, stages)

    with pytest.raises(HTTPException) as exc:
        await deactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[0].id
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_missing_or_inactive_stage():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages[1:])

    with pytest.raises(HTTPException) as exc:
        await deactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[0].id
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_error_rolls_back():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)
    db.commit.side_effect = RuntimeError("Database unavailable")

    with pytest.raises(RuntimeError):
        await deactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[0].id
        )

    db.rollback.assert_awaited_once()
