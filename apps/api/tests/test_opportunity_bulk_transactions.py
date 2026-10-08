import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.opportunity_bulk import (
    bulk_change_opportunity_owner,
)


ORG_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()
OPPORTUNITY_1 = uuid.uuid4()
OPPORTUNITY_2 = uuid.uuid4()


def mock_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = value
    return result


def mock_db(owner_exists=True, opportunities=None):
    db = AsyncMock()

    if opportunities is None:
        opportunities = [
            SimpleNamespace(
                id=OPPORTUNITY_1,
                owner_user_id=None,
            ),
            SimpleNamespace(
                id=OPPORTUNITY_2,
                owner_user_id=None,
            ),
        ]

    db.execute.side_effect = [
        mock_result(OWNER_ID if owner_exists else None),
        mock_result(opportunities),
    ]

    return db


@pytest.mark.asyncio
async def test_bulk_owner_updates_all_selected():
    db = mock_db()

    result = await bulk_change_opportunity_owner(
        db,
        ORG_ID,
        [OPPORTUNITY_1, OPPORTUNITY_2],
        OWNER_ID,
    )

    assert result["updated_count"] == 2
    assert result["requested_count"] == 2
    assert set(result["updated_ids"]) == {
        OPPORTUNITY_1,
        OPPORTUNITY_2,
    }
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_bulk_owner_rejects_duplicates():
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_owner(
            db,
            ORG_ID,
            [OPPORTUNITY_1, OPPORTUNITY_1],
            OWNER_ID,
        )

    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_owner_rejects_invalid_owner():
    db = mock_db(owner_exists=False)

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_owner(
            db,
            ORG_ID,
            [OPPORTUNITY_1],
            OWNER_ID,
        )

    assert exc.value.status_code == 400
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_owner_rejects_missing_or_foreign_records():
    db = mock_db(
        opportunities=[
            SimpleNamespace(
                id=OPPORTUNITY_1,
                owner_user_id=None,
            )
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await bulk_change_opportunity_owner(
            db,
            ORG_ID,
            [OPPORTUNITY_1, OPPORTUNITY_2],
            OWNER_ID,
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_owner_rolls_back_on_commit_failure():
    db = mock_db()
    db.commit.side_effect = RuntimeError(
        "Simulated database failure"
    )

    with pytest.raises(RuntimeError):
        await bulk_change_opportunity_owner(
            db,
            ORG_ID,
            [OPPORTUNITY_1, OPPORTUNITY_2],
            OWNER_ID,
        )

    db.rollback.assert_awaited_once()
