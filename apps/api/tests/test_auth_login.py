import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.auth import LoginRequest, login


USER_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()


def mock_db(user, role):
    db = AsyncMock()

    user_result = SimpleNamespace(
        scalar_one_or_none=lambda: user
    )

    membership_result = SimpleNamespace(
        scalar_one_or_none=lambda: role
    )

    db.execute.side_effect = [
        user_result,
        membership_result,
    ]

    return db


def active_user():
    return SimpleNamespace(
        id=USER_ID,
        email="owner@example.com",
        password_hash="stored-password-hash",
        is_active=True,
    )


def login_payload(password="correct-password"):
    return LoginRequest(
        email="owner@example.com",
        password=password,
        organization_id=ORG_ID,
    )


@pytest.mark.asyncio
async def test_login_valid_credentials():
    db = mock_db(active_user(), "owner")

    with (
        patch(
            "app.api.routes.auth.verify_password",
            return_value=True,
        ),
        patch(
            "app.api.routes.auth.create_access_token",
            return_value="test-jwt-token",
        ),
    ):
        result = await login(login_payload(), db)

    assert result.access_token == "test-jwt-token"
    assert result.token_type == "bearer"
    assert result.user_id == USER_ID
    assert result.organization_id == ORG_ID
    assert result.role == "owner"
    assert result.expires_in > 0


@pytest.mark.asyncio
async def test_login_rejects_incorrect_password():
    db = mock_db(active_user(), "owner")

    with patch(
        "app.api.routes.auth.verify_password",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as error:
            await login(
                login_payload("wrong-password"),
                db,
            )

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_inactive_user():
    db = mock_db(None, None)

    with pytest.raises(HTTPException) as error:
        await login(login_payload(), db)

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_missing_membership():
    db = mock_db(active_user(), None)

    with patch(
        "app.api.routes.auth.verify_password",
        return_value=True,
    ):
        with pytest.raises(HTTPException) as error:
            await login(login_payload(), db)

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_inactive_organization():
    # The membership query excludes inactive organizations.
    db = mock_db(active_user(), None)

    with patch(
        "app.api.routes.auth.verify_password",
        return_value=True,
    ):
        with pytest.raises(HTTPException) as error:
            await login(login_payload(), db)

    assert error.value.status_code == 401
