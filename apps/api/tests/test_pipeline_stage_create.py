import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.schemas.pipeline import PipelineStageCreate
from app.services.pipelines import create_pipeline_stage


def make_db(*query_values):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()

    results = []

    for value in query_values:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)

    db.execute.side_effect = results
    return db


def make_pipeline(org_id, pipeline_id):
    return Pipeline(
        id=pipeline_id,
        organization_id=org_id,
        name="Test Sales",
        is_default=False,
        is_active=True,
    )


def test_stage_schema_normalizes_name():
    stage = PipelineStageCreate(
        name="  Technical Review  ",
        probability=45,
        category="open",
    )
    assert stage.name == "Technical Review"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "X" * 121},
        {"name": "Test", "probability": -1},
        {"name": "Test", "probability": 101},
        {"name": "Test", "category": "invalid"},
    ],
)
def test_stage_schema_rejects_invalid_input(payload):
    with pytest.raises(ValidationError):
        PipelineStageCreate(**payload)


@pytest.mark.asyncio
async def test_create_stage_success():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    pipeline = make_pipeline(org_id, pipeline_id)

    db = make_db(pipeline, None, 7)

    stage = await create_pipeline_stage(
        db,
        org_id,
        pipeline_id,
        "  Technical Review  ",
        45,
        "open",
    )

    assert isinstance(stage, PipelineStage)
    assert stage.name == "Technical Review"
    assert stage.position == 8
    assert stage.probability == 45
    assert stage.category == "open"
    assert stage.organization_id == org_id
    assert stage.pipeline_id == pipeline_id

    db.add.assert_called_once_with(stage)
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(stage)


@pytest.mark.asyncio
async def test_first_stage_gets_position_one():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()

    db = make_db(
        make_pipeline(org_id, pipeline_id),
        None,
        None,
    )

    stage = await create_pipeline_stage(
        db, org_id, pipeline_id, "First Stage"
    )

    assert stage.position == 1


@pytest.mark.asyncio
async def test_duplicate_stage_rejected():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()

    db = make_db(
        make_pipeline(org_id, pipeline_id),
        uuid.uuid4(),
    )

    with pytest.raises(HTTPException) as exc:
        await create_pipeline_stage(
            db, org_id, pipeline_id, "Existing Stage"
        )

    assert exc.value.status_code == 409
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_or_other_tenant_pipeline_rejected():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()

    db = make_db(None)

    with pytest.raises(HTTPException) as exc:
        await create_pipeline_stage(
            db, org_id, pipeline_id, "New Stage"
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_integrity_error_rolls_back():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()

    db = make_db(
        make_pipeline(org_id, pipeline_id),
        None,
        7,
    )

    db.commit.side_effect = IntegrityError(
        statement="INSERT INTO pipeline_stages",
        params={},
        orig=Exception("unique constraint"),
    )

    with pytest.raises(HTTPException) as exc:
        await create_pipeline_stage(
            db, org_id, pipeline_id, "New Stage"
        )

    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_error_rolls_back():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()

    db = make_db(
        make_pipeline(org_id, pipeline_id),
        None,
        7,
    )

    db.commit.side_effect = RuntimeError("database failure")

    with pytest.raises(RuntimeError):
        await create_pipeline_stage(
            db, org_id, pipeline_id, "New Stage"
        )

    db.rollback.assert_awaited_once()
