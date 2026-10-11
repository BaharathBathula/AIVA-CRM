import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.schemas.pipeline import PipelineStageUpdate
from app.services.pipelines import update_pipeline_stage


def make_db(*values):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()

    results = []
    for value in values:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)

    db.execute.side_effect = results
    return db


def make_objects():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    stage_id = uuid.uuid4()

    pipeline = Pipeline(
        id=pipeline_id,
        organization_id=org_id,
        name="Enterprise Sales",
        is_active=True,
        is_default=False,
    )

    stage = PipelineStage(
        id=stage_id,
        organization_id=org_id,
        pipeline_id=pipeline_id,
        name="Discovery",
        position=2,
        probability=25,
        category="open",
        is_active=True,
    )

    return org_id, pipeline_id, stage_id, pipeline, stage


def test_update_schema_accepts_partial_payload():
    payload = PipelineStageUpdate(
        name="  Technical Review  "
    )
    assert payload.model_dump(exclude_unset=True) == {
        "name": "Technical Review"
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "X" * 121},
        {"probability": -1},
        {"probability": 101},
        {"probability": True},
        {"category": "invalid"},
        {"name": None},
        {"probability": None},
        {"category": None},
    ],
)
def test_update_schema_rejects_invalid_values(payload):
    with pytest.raises(ValidationError):
        PipelineStageUpdate(**payload)


@pytest.mark.asyncio
async def test_update_stage_all_fields():
    org, pipeline_id, stage_id, pipeline, stage = make_objects()
    db = make_db(pipeline, stage, None)

    result = await update_pipeline_stage(
        db,
        org,
        pipeline_id,
        stage_id,
        {
            "name": "Technical Review",
            "probability": 45,
            "category": "open",
        },
    )

    assert result.name == "Technical Review"
    assert result.probability == 45
    assert result.category == "open"
    assert result.position == 2
    assert result.id == stage_id

    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(stage)


@pytest.mark.asyncio
async def test_update_stage_probability_only():
    org, pipeline_id, stage_id, pipeline, stage = make_objects()
    db = make_db(pipeline, stage)

    result = await update_pipeline_stage(
        db, org, pipeline_id, stage_id,
        {"probability": 60},
    )

    assert result.name == "Discovery"
    assert result.probability == 60
    assert result.position == 2


@pytest.mark.asyncio
async def test_duplicate_stage_name_rejected():
    org, pipeline_id, stage_id, pipeline, stage = make_objects()
    db = make_db(pipeline, stage, uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await update_pipeline_stage(
            db, org, pipeline_id, stage_id,
            {"name": "Proposal"},
        )

    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_pipeline_rejected():
    org, pipeline_id, stage_id, _, _ = make_objects()
    db = make_db(None)

    with pytest.raises(HTTPException) as exc:
        await update_pipeline_stage(
            db, org, pipeline_id, stage_id,
            {"name": "New Name"},
        )

    assert exc.value.status_code == 404
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_stage_rejected():
    org, pipeline_id, stage_id, pipeline, _ = make_objects()
    db = make_db(pipeline, None)

    with pytest.raises(HTTPException) as exc:
        await update_pipeline_stage(
            db, org, pipeline_id, stage_id,
            {"name": "New Name"},
        )

    assert exc.value.status_code == 404
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_changes_rejected():
    org, pipeline_id, stage_id, _, _ = make_objects()
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await update_pipeline_stage(
            db, org, pipeline_id, stage_id, {},
        )

    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_integrity_error_rolls_back():
    org, pipeline_id, stage_id, pipeline, stage = make_objects()
    db = make_db(pipeline, stage, None)

    db.commit.side_effect = IntegrityError(
        statement="UPDATE pipeline_stages",
        params={},
        orig=Exception("unique constraint"),
    )

    with pytest.raises(HTTPException) as exc:
        await update_pipeline_stage(
            db, org, pipeline_id, stage_id,
            {"name": "Technical Review"},
        )

    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_error_rolls_back():
    org, pipeline_id, stage_id, pipeline, stage = make_objects()
    db = make_db(pipeline, stage)

    db.commit.side_effect = RuntimeError("database error")

    with pytest.raises(RuntimeError):
        await update_pipeline_stage(
            db, org, pipeline_id, stage_id,
            {"probability": 70},
        )

    db.rollback.assert_awaited_once()
