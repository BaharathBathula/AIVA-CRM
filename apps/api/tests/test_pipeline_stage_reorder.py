import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.schemas.pipeline import PipelineStageReorder
from app.services.pipelines import reorder_pipeline_stages


def make_objects():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()

    pipeline = Pipeline(
        id=pipeline_id,
        organization_id=org_id,
        name="Test Pipeline",
        is_active=True,
        is_default=False,
    )

    stages = [
        PipelineStage(
            id=uuid.uuid4(),
            organization_id=org_id,
            pipeline_id=pipeline_id,
            name=f"Stage {i}",
            position=i,
            probability=20,
            category="open",
            is_active=True,
        )
        for i in range(1, 4)
    ]

    return org_id, pipeline_id, pipeline, stages


def make_db(pipeline, stages):
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    pipeline_result = MagicMock()
    pipeline_result.scalar_one_or_none.return_value = pipeline

    stage_result = MagicMock()
    stage_result.scalars.return_value.all.return_value = stages

    db.execute.side_effect = [
        pipeline_result,
        stage_result,
    ]

    return db


def test_schema_accepts_valid_order():
    ids = [uuid.uuid4(), uuid.uuid4()]

    payload = PipelineStageReorder(stage_ids=ids)

    assert payload.stage_ids == ids


@pytest.mark.parametrize(
    "ids",
    [
        [],
        [uuid.uuid4()] * 2,
    ],
)
def test_schema_rejects_invalid_order(ids):
    with pytest.raises(ValidationError):
        PipelineStageReorder(stage_ids=ids)


@pytest.mark.asyncio
async def test_reorder_success():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    desired = [stages[2].id, stages[0].id, stages[1].id]

    result = await reorder_pipeline_stages(
        db, org_id, pipeline_id, desired
    )

    assert [stage.id for stage in result] == desired
    assert [stage.position for stage in result] == [1, 2, 3]
    assert db.flush.await_count == 2
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_ids_rejected():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    with pytest.raises(HTTPException) as exc:
        await reorder_pipeline_stages(
            db, org_id, pipeline_id,
            [stages[0].id, stages[0].id],
        )

    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_stage_rejected():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    with pytest.raises(HTTPException) as exc:
        await reorder_pipeline_stages(
            db, org_id, pipeline_id,
            [stages[0].id, stages[1].id],
        )

    assert exc.value.status_code == 422
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_stage_rejected():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)

    with pytest.raises(HTTPException) as exc:
        await reorder_pipeline_stages(
            db, org_id, pipeline_id,
            [stages[0].id, stages[1].id, uuid.uuid4()],
        )

    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_or_other_tenant_pipeline_rejected():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(None, stages)

    with pytest.raises(HTTPException) as exc:
        await reorder_pipeline_stages(
            db, org_id, pipeline_id,
            [stage.id for stage in stages],
        )

    assert exc.value.status_code == 404
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_inactive_stage_excluded_from_request():
    org_id, pipeline_id, pipeline, stages = make_objects()
    stages[2].is_active = False
    db = make_db(pipeline, stages)

    desired = [stages[1].id, stages[0].id]

    result = await reorder_pipeline_stages(
        db, org_id, pipeline_id, desired
    )

    assert [stage.id for stage in result] == desired
    assert [stage.position for stage in result] == [1, 2]
    assert stages[2].position == 3
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_integrity_error_rolls_back():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)
    db.flush.side_effect = IntegrityError(
        "UPDATE pipeline_stages",
        {},
        Exception("duplicate position"),
    )

    with pytest.raises(IntegrityError):
        await reorder_pipeline_stages(
            db, org_id, pipeline_id,
            [stage.id for stage in reversed(stages)],
        )

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_error_rolls_back():
    org_id, pipeline_id, pipeline, stages = make_objects()
    db = make_db(pipeline, stages)
    db.commit.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        await reorder_pipeline_stages(
            db, org_id, pipeline_id,
            [stage.id for stage in reversed(stages)],
        )

    db.rollback.assert_awaited_once()
