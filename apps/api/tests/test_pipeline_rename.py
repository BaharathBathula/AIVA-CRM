import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.pipeline import Pipeline
from app.schemas.pipeline import PipelineRename
from app.services.pipelines import rename_pipeline


def mock_db():
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    return db


def existing_pipeline(organization_id, pipeline_id):
    return Pipeline(
        id=pipeline_id,
        organization_id=organization_id,
        name="Original Sales",
        is_default=False,
        is_active=True,
    )


def query_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_rename_schema_trims_whitespace():
    payload = PipelineRename(name="  Enterprise Sales  ")
    assert payload.name == "Enterprise Sales"


@pytest.mark.parametrize("name", ["", "   ", "X" * 151])
def test_rename_schema_rejects_invalid_names(name):
    with pytest.raises(ValueError):
        PipelineRename(name=name)


@pytest.mark.asyncio
async def test_rename_pipeline_success():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    pipeline = existing_pipeline(org_id, pipeline_id)
    db = mock_db()

    db.execute.side_effect = [
        query_result(pipeline),
        query_result(None),
    ]

    updated = await rename_pipeline(
        db, org_id, pipeline_id, "  Enterprise Sales  "
    )

    assert updated is pipeline
    assert pipeline.name == "Enterprise Sales"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(pipeline)
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_same_name_is_noop():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    pipeline = existing_pipeline(org_id, pipeline_id)
    db = mock_db()

    db.execute.return_value = query_result(pipeline)

    result = await rename_pipeline(
        db, org_id, pipeline_id, "Original Sales"
    )

    assert result is pipeline
    db.execute.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_duplicate_rejected():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    pipeline = existing_pipeline(org_id, pipeline_id)
    db = mock_db()

    db.execute.side_effect = [
        query_result(pipeline),
        query_result(uuid.uuid4()),
    ]

    with pytest.raises(HTTPException) as exc:
        await rename_pipeline(
            db, org_id, pipeline_id, "Existing Pipeline"
        )

    assert exc.value.status_code == 409
    assert pipeline.name == "Original Sales"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_other_tenant_returns_404():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    db = mock_db()

    db.execute.return_value = query_result(None)

    with pytest.raises(HTTPException) as exc:
        await rename_pipeline(
            db, org_id, pipeline_id, "Renamed"
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_integrity_error_rolls_back():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    pipeline = existing_pipeline(org_id, pipeline_id)
    db = mock_db()

    db.execute.side_effect = [
        query_result(pipeline),
        query_result(None),
    ]

    db.commit.side_effect = IntegrityError(
        statement="UPDATE pipelines",
        params={},
        orig=Exception("duplicate"),
    )

    with pytest.raises(HTTPException) as exc:
        await rename_pipeline(
            db, org_id, pipeline_id, "Duplicate"
        )

    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_unexpected_error_rolls_back():
    org_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    pipeline = existing_pipeline(org_id, pipeline_id)
    db = mock_db()

    db.execute.side_effect = [
        query_result(pipeline),
        query_result(None),
    ]

    db.commit.side_effect = RuntimeError("database failure")

    with pytest.raises(RuntimeError):
        await rename_pipeline(
            db, org_id, pipeline_id, "New Name"
        )

    db.rollback.assert_awaited_once()
