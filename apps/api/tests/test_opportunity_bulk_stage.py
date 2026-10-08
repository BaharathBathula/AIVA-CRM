import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.opportunity_bulk_stage import (
    bulk_change_opportunity_stage,
)


def uid():
    return uuid.uuid4()


def make_db(opportunities=None):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    result = MagicMock()
    result.scalars.return_value.all.return_value = (
        opportunities if opportunities is not None else []
    )
    db.execute.return_value = result
    return db


def make_opportunity(org_id, pipeline_id, stage_id):
    return SimpleNamespace(
        id=uid(),
        organization_id=org_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        probability=10,
        closed_at=None,
    )


@pytest.mark.asyncio
async def test_valid_bulk_stage_update():
    org_id = uid()
    old_pipeline = uid()
    old_stage = uid()
    new_pipeline = uid()
    new_stage = uid()

    opportunities = [
        make_opportunity(org_id, old_pipeline, old_stage)
        for _ in range(2)
    ]

    db = make_db(opportunities)

    pipeline = SimpleNamespace(id=new_pipeline)
    stage = SimpleNamespace(
        id=new_stage,
        pipeline_id=new_pipeline,
        probability=75,
        category="open",
    )

    with patch(
        "app.services.opportunity_bulk_stage.resolve_pipeline_and_stage",
        new_callable=AsyncMock,
        return_value=(pipeline, stage),
    ):
        result = await bulk_change_opportunity_stage(
            db=db,
            organization_id=org_id,
            opportunity_ids=[item.id for item in opportunities],
            pipeline_id=new_pipeline,
            stage_id=new_stage,
        )

    assert result["updated_count"] == 2
    assert result["requested_count"] == 2

    for opportunity in opportunities:
        assert opportunity.pipeline_id == new_pipeline
        assert opportunity.stage_id == new_stage
        assert opportunity.probability == 75

    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_selection_rejected():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_stage(
            db, uid(), [], uid(), uid()
        )

    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_more_than_100_rejected():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_stage(
            db,
            uid(),
            [uid() for _ in range(101)],
            uid(),
            uid(),
        )

    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_selection_rejected():
    db = make_db()
    opportunity_id = uid()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_stage(
            db,
            uid(),
            [opportunity_id, opportunity_id],
            uid(),
            uid(),
        )

    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_stage_rejected():
    db = make_db()

    with patch(
        "app.services.opportunity_bulk_stage.resolve_pipeline_and_stage",
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=409,
            detail="Stage does not belong to pipeline.",
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await bulk_change_opportunity_stage(
                db, uid(), [uid()], uid(), uid()
            )

    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_or_cross_tenant_opportunity_rejected():
    org_id = uid()
    db = make_db([])

    pipeline_id = uid()
    stage_id = uid()

    with patch(
        "app.services.opportunity_bulk_stage.resolve_pipeline_and_stage",
        new_callable=AsyncMock,
        return_value=(
            SimpleNamespace(id=pipeline_id),
            SimpleNamespace(
                id=stage_id,
                pipeline_id=pipeline_id,
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await bulk_change_opportunity_stage(
                db,
                org_id,
                [uid()],
                pipeline_id,
                stage_id,
            )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_already_in_stage():
    org_id = uid()
    pipeline_id = uid()
    stage_id = uid()

    opportunity = make_opportunity(
        org_id, pipeline_id, stage_id
    )
    db = make_db([opportunity])

    with patch(
        "app.services.opportunity_bulk_stage.resolve_pipeline_and_stage",
        new_callable=AsyncMock,
        return_value=(
            SimpleNamespace(id=pipeline_id),
            SimpleNamespace(
                id=stage_id,
                pipeline_id=pipeline_id,
            ),
        ),
    ):
        result = await bulk_change_opportunity_stage(
            db,
            org_id,
            [opportunity.id],
            pipeline_id,
            stage_id,
        )

    assert result["updated_count"] == 0
    assert result["updated_ids"] == []
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_failure_triggers_rollback():
    org_id = uid()
    pipeline_id = uid()
    stage_id = uid()

    opportunity = make_opportunity(
        org_id, uid(), uid()
    )
    db = make_db([opportunity])
    db.commit.side_effect = RuntimeError("Database failure")

    with patch(
        "app.services.opportunity_bulk_stage.resolve_pipeline_and_stage",
        new_callable=AsyncMock,
        return_value=(
            SimpleNamespace(id=pipeline_id),
            SimpleNamespace(
                id=stage_id,
                pipeline_id=pipeline_id,
                probability=50,
                category="open",
            ),
        ),
    ):
        with pytest.raises(RuntimeError):
            await bulk_change_opportunity_stage(
                db,
                org_id,
                [opportunity.id],
                pipeline_id,
                stage_id,
            )

    db.rollback.assert_awaited_once()
