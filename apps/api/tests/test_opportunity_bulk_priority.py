import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.opportunity_bulk_priority import (
    bulk_change_opportunity_priority,
)


def uid():
    return uuid.uuid4()


def make_opportunity(priority="medium"):
    return SimpleNamespace(
        id=uid(),
        priority=priority,
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
async def test_valid_bulk_priority_update():
    opportunities = [
        make_opportunity("low"),
        make_opportunity("medium"),
    ]
    db = make_db(opportunities)

    result = await bulk_change_opportunity_priority(
        db=db,
        organization_id=uid(),
        opportunity_ids=[item.id for item in opportunities],
        priority="high",
    )

    assert result["updated_count"] == 2
    assert result["requested_count"] == 2
    assert set(result["updated_ids"]) == {
        item.id for item in opportunities
    }
    assert result["priority"] == "high"
    assert all(
        item.priority == "high"
        for item in opportunities
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_priority_rejected():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_priority(
            db, uid(), [uid()], "urgent"
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_selection_rejected():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_priority(
            db, uid(), [], "high"
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_more_than_100_rejected():
    db = make_db()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_priority(
            db,
            uid(),
            [uid() for _ in range(101)],
            "high",
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_ids_rejected():
    db = make_db()
    opportunity_id = uid()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_priority(
            db,
            uid(),
            [opportunity_id, opportunity_id],
            "high",
        )

    assert exc.value.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_or_cross_tenant_record_rejected():
    db = make_db([])

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_priority(
            db, uid(), [uid()], "high"
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_priority_unchanged():
    opportunity = make_opportunity("high")
    db = make_db([opportunity])

    result = await bulk_change_opportunity_priority(
        db,
        uid(),
        [opportunity.id],
        "high",
    )

    assert result["updated_count"] == 0
    assert result["updated_ids"] == []
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_failure_rolls_back():
    opportunity = make_opportunity("low")
    db = make_db([opportunity])
    db.commit.side_effect = RuntimeError(
        "Simulated database failure"
    )

    with pytest.raises(RuntimeError):
        await bulk_change_opportunity_priority(
            db,
            uid(),
            [opportunity.id],
            "critical",
        )

    db.rollback.assert_awaited_once()
