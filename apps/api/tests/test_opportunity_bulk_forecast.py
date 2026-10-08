import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.opportunity_bulk_forecast import (
    bulk_change_opportunity_forecast,
)


def uid():
    return uuid.uuid4()


def make_opportunity(category="pipeline"):
    return SimpleNamespace(
        id=uid(),
        forecast_category=category,
    )


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category",
    ["pipeline", "best_case", "commit", "closed", "omitted"],
)
async def test_all_forecast_categories(category):
    opportunity = make_opportunity("pipeline")
    db = make_db([opportunity])

    result = await bulk_change_opportunity_forecast(
        db=db,
        organization_id=uid(),
        opportunity_ids=[opportunity.id],
        forecast_category=category,
    )

    assert opportunity.forecast_category == category
    assert result["forecast_category"] == category
    assert result["requested_count"] == 1
    assert result["updated_count"] == (
        0 if category == "pipeline" else 1
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_multiple_opportunities():
    opportunities = [
        make_opportunity("pipeline"),
        make_opportunity("best_case"),
    ]
    db = make_db(opportunities)

    result = await bulk_change_opportunity_forecast(
        db,
        uid(),
        [item.id for item in opportunities],
        "commit",
    )

    assert result["updated_count"] == 2
    assert set(result["updated_ids"]) == {
        item.id for item in opportunities
    }
    assert all(
        item.forecast_category == "commit"
        for item in opportunities
    )


@pytest.mark.asyncio
async def test_invalid_forecast_category():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_forecast(
            db, uid(), [uid()], "unknown"
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_selection():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_forecast(
            db, uid(), [], "commit"
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_more_than_100_opportunities():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_forecast(
            db,
            uid(),
            [uid() for _ in range(101)],
            "commit",
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_opportunity_ids():
    db = make_db()
    opportunity_id = uid()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_forecast(
            db,
            uid(),
            [opportunity_id, opportunity_id],
            "commit",
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_or_cross_tenant_opportunity():
    db = make_db([])

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_forecast(
            db, uid(), [uid()], "commit"
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_forecast():
    opportunity = make_opportunity("commit")
    db = make_db([opportunity])

    result = await bulk_change_opportunity_forecast(
        db, uid(), [opportunity.id], "commit"
    )

    assert result["updated_count"] == 0
    assert result["updated_ids"] == []
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_failure_rolls_back():
    opportunity = make_opportunity("pipeline")
    db = make_db([opportunity])
    db.commit.side_effect = RuntimeError(
        "Simulated database failure"
    )

    with pytest.raises(RuntimeError):
        await bulk_change_opportunity_forecast(
            db,
            uid(),
            [opportunity.id],
            "closed",
        )

    db.rollback.assert_awaited_once()
