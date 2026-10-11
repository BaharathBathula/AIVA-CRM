import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.pipelines import reactivate_pipeline_stage


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
            is_active=index != 2,
        )
        for index in range(1, 4)
    ]

    return organization_id, pipeline_id, pipeline, stages


def make_db(pipeline, stages):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()

    pipeline_result = MagicMock()
    pipeline_result.scalar_one_or_none.return_value = pipeline

    stages_result = MagicMock()
    stages_result.scalars.return_value.all.return_value = stages

    db.execute.side_effect = [
        pipeline_result,
        stages_result,
    ]

    return db


@pytest.mark.asyncio
async def test_reactivate_inactive_stage():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    target = stages[1]

    original = (
        target.id,
        target.name,
        target.position,
        target.probability,
        target.category,
    )

    result = await reactivate_pipeline_stage(
        db, org_id, pipeline_id, target.id
    )

    assert result.is_active is True

    assert (
        result.id,
        result.name,
        result.position,
        result.probability,
        result.category,
    ) == original

    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(target)
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_already_active_stage():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    with pytest.raises(HTTPException) as error:
        await reactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[0].id
        )

    assert error.value.status_code == 409
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_missing_stage():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    with pytest.raises(HTTPException) as error:
        await reactivate_pipeline_stage(
            db, org_id, pipeline_id, uuid.uuid4()
        )

    assert error.value.status_code == 404
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_pipeline", [True, False])
async def test_reject_unavailable_pipeline(missing_pipeline):
    org_id, pipeline_id, pipeline, stages = make_objects()

    if missing_pipeline:
        pipeline = None
    else:
        pipeline.is_active = False
        # Simulate the database query excluding inactive pipelines.
        pipeline = None

    db = make_db(pipeline, stages)

    with pytest.raises(HTTPException) as error:
        await reactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[1].id
        )

    assert error.value.status_code == 404
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_stage_outside_organization():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, [])

    with pytest.raises(HTTPException) as error:
        await reactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[1].id
        )

    assert error.value.status_code == 404
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_error_rolls_back():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    db.commit.side_effect = RuntimeError("Database failure")

    with pytest.raises(RuntimeError):
        await reactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[1].id
        )

    db.rollback.assert_awaited_once()

@pytest.mark.asyncio
async def test_reactivation_integrity_error_returns_conflict():
    from sqlalchemy.exc import IntegrityError

    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    db.commit.side_effect = IntegrityError(
        "UPDATE pipeline_stages",
        {},
        Exception("duplicate stage position"),
    )

    with pytest.raises(HTTPException) as error:
        await reactivate_pipeline_stage(
            db, org_id, pipeline_id, stages[1].id
        )

    assert error.value.status_code == 409
    db.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()
