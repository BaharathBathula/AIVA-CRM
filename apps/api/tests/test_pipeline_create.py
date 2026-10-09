import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.pipeline import PipelineCreate
from app.services.pipelines import (
    PIPELINE_DEFAULT_STAGES,
    create_pipeline,
)


def test_pipeline_name_is_trimmed():
    payload = PipelineCreate(name="  Enterprise Sales  ")
    assert payload.name == "Enterprise Sales"


@pytest.mark.parametrize("name", [
    "",
    "   ",
    "x" * 151,
])
def test_invalid_pipeline_name(name):
    with pytest.raises(ValidationError):
        PipelineCreate(name=name)


def test_default_stage_template():
    assert len(PIPELINE_DEFAULT_STAGES) == 7

    names = [
        stage[0]
        for stage in PIPELINE_DEFAULT_STAGES
    ]

    assert names == [
        "Qualification",
        "Discovery",
        "Demo",
        "Proposal",
        "Negotiation",
        "Closed Won",
        "Closed Lost",
    ]

    assert [
        stage[1]
        for stage in PIPELINE_DEFAULT_STAGES
    ] == list(range(1, 8))


@pytest.mark.asyncio
async def test_create_pipeline_and_stages():
    from app.models.pipeline import Pipeline
    from app.models.pipeline_stage import PipelineStage

    organization_id = uuid.uuid4()

    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    created = await create_pipeline(
        db,
        organization_id,
        "Enterprise Sales",
    )

    assert isinstance(created, Pipeline)
    assert created.organization_id == organization_id
    assert created.name == "Enterprise Sales"
    assert created.is_default is False
    assert created.is_active is True

    added_objects = [
        call.args[0]
        for call in db.add.call_args_list
    ]

    assert len(added_objects) == 8

    pipelines = [
        obj for obj in added_objects
        if isinstance(obj, Pipeline)
    ]

    stages = [
        obj for obj in added_objects
        if isinstance(obj, PipelineStage)
    ]

    assert len(pipelines) == 1
    assert len(stages) == 7

    assert [
        stage.name for stage in stages
    ] == [
        "Qualification",
        "Discovery",
        "Demo",
        "Proposal",
        "Negotiation",
        "Closed Won",
        "Closed Lost",
    ]

    assert all(
        stage.organization_id == organization_id
        for stage in stages
    )

    assert all(
        stage.pipeline_id == created.id
        for stage in stages
    )

    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(created)
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_pipeline_rejected():
    db = MagicMock()
    db.execute = AsyncMock()
    db.rollback = AsyncMock()

    db.execute.return_value.scalar_one_or_none.return_value = (
        uuid.uuid4()
    )

    with pytest.raises(HTTPException) as exc:
        await create_pipeline(
            db,
            uuid.uuid4(),
            "Existing Pipeline",
        )

    assert exc.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_creation_failure_rolls_back():
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock(
        side_effect=RuntimeError("Database failure")
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    with pytest.raises(RuntimeError):
        await create_pipeline(
            db,
            uuid.uuid4(),
            "Test Pipeline",
        )

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
