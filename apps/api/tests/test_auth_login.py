import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.auth import _choose_organization, _password_step


USER_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()
OTHER_ORG_ID = uuid.uuid4()


def mock_db(user):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: user)
    return db


def active_user(**overrides):
    values = {
        "id": USER_ID,
        "email": "owner@example.com",
        "password_hash": "stored-password-hash",
        "is_active": True,
        "failed_login_count": 0,
        "locked_until": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def membership_rows(*org_ids):
    return [(SimpleNamespace(role="owner"), SimpleNamespace(id=org_id)) for org_id in org_ids]


@pytest.mark.asyncio
async def test_password_step_accepts_valid_credentials():
    user = active_user()
    with patch("app.api.routes.auth.verify_password", return_value=True):
        result = await _password_step(mock_db(user), "Owner@Example.com ", "correct")
    assert result is user


@pytest.mark.asyncio
async def test_password_step_rejects_incorrect_password_and_counts_failure():
    user = active_user()
    db = mock_db(user)
    with patch("app.api.routes.auth.verify_password", return_value=False):
        with pytest.raises(HTTPException) as error:
            await _password_step(db, "owner@example.com", "wrong")
    assert error.value.status_code == 401
    assert user.failed_login_count == 1
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_password_step_locks_after_repeated_failures():
    user = active_user(failed_login_count=4)
    with patch("app.api.routes.auth.verify_password", return_value=False):
        with pytest.raises(HTTPException):
            await _password_step(mock_db(user), "owner@example.com", "wrong")
    assert user.locked_until is not None
    assert user.failed_login_count == 0


@pytest.mark.asyncio
async def test_password_step_rejects_locked_account():
    user = active_user(locked_until=datetime.now(timezone.utc) + timedelta(minutes=10))
    with pytest.raises(HTTPException) as error:
        await _password_step(mock_db(user), "owner@example.com", "correct")
    assert error.value.status_code == 423


@pytest.mark.asyncio
async def test_password_step_rejects_unknown_or_inactive_user():
    for user in (None, active_user(is_active=False), active_user(password_hash=None)):
        with pytest.raises(HTTPException) as error:
            await _password_step(mock_db(user), "owner@example.com", "correct")
        assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_choose_organization_defaults_to_first_membership():
    with patch("app.api.routes.auth.active_memberships", new=AsyncMock(return_value=membership_rows(ORG_ID, OTHER_ORG_ID))):
        assert await _choose_organization(AsyncMock(), active_user(), None) == ORG_ID


@pytest.mark.asyncio
async def test_choose_organization_honours_requested_membership():
    with patch("app.api.routes.auth.active_memberships", new=AsyncMock(return_value=membership_rows(ORG_ID, OTHER_ORG_ID))):
        assert await _choose_organization(AsyncMock(), active_user(), OTHER_ORG_ID) == OTHER_ORG_ID


@pytest.mark.asyncio
async def test_choose_organization_rejects_foreign_or_inactive_workspace():
    # Inactive organizations and deactivated memberships are excluded by active_memberships().
    with patch("app.api.routes.auth.active_memberships", new=AsyncMock(return_value=membership_rows(ORG_ID))):
        with pytest.raises(HTTPException) as error:
            await _choose_organization(AsyncMock(), active_user(), uuid.uuid4())
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_choose_organization_requires_some_membership():
    with patch("app.api.routes.auth.active_memberships", new=AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as error:
            await _choose_organization(AsyncMock(), active_user(), None)
    assert error.value.status_code == 403
