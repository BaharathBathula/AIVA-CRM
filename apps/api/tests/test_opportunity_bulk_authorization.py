import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.dependencies.authorization import (
    require_bulk_update_permission,
)


ORG_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def mock_db(user, membership):
    db = AsyncMock()

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user

    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = membership

    db.execute.side_effect = [
        user_result,
        membership_result,
    ]

    return db


def credentials():
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="test-token",
    )


@pytest.mark.asyncio
async def test_owner_can_bulk_update():
    db = mock_db(
        SimpleNamespace(id=USER_ID, is_active=True),
        SimpleNamespace(role="owner"),
    )

    with patch(
        "app.api.dependencies.authorization.decode_access_token",
        return_value=USER_ID,
    ), patch(
        "app.api.dependencies.authorization.get_settings",
        return_value=SimpleNamespace(
            jwt_secret_key="test-secret-key-with-at-least-32-characters"
        ),
    ):
        result = await require_bulk_update_permission(
            credentials=credentials(),
            organization_id=ORG_ID,
            db=db,
        )

    assert result.user_id == USER_ID
    assert result.organization_id == ORG_ID
    assert result.role == "owner"


@pytest.mark.asyncio
async def test_admin_can_bulk_update():
    db = mock_db(
        SimpleNamespace(id=USER_ID, is_active=True),
        SimpleNamespace(role="admin"),
    )

    with patch(
        "app.api.dependencies.authorization.decode_access_token",
        return_value=USER_ID,
    ), patch(
        "app.api.dependencies.authorization.get_settings",
        return_value=SimpleNamespace(
            jwt_secret_key="test-secret-key-with-at-least-32-characters"
        ),
    ):
        result = await require_bulk_update_permission(
            credentials=credentials(),
            organization_id=ORG_ID,
            db=db,
        )

    assert result.role == "admin"


@pytest.mark.asyncio
async def test_member_cannot_bulk_update():
    db = mock_db(
        SimpleNamespace(id=USER_ID, is_active=True),
        SimpleNamespace(role="member"),
    )

    with patch(
        "app.api.dependencies.authorization.decode_access_token",
        return_value=USER_ID,
    ), patch(
        "app.api.dependencies.authorization.get_settings",
        return_value=SimpleNamespace(
            jwt_secret_key="test-secret-key-with-at-least-32-characters"
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_bulk_update_permission(
                credentials=credentials(),
                organization_id=ORG_ID,
                db=db,
            )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_member_cannot_bulk_update():
    db = mock_db(
        SimpleNamespace(id=USER_ID, is_active=True),
        None,
    )

    with patch(
        "app.api.dependencies.authorization.decode_access_token",
        return_value=USER_ID,
    ), patch(
        "app.api.dependencies.authorization.get_settings",
        return_value=SimpleNamespace(
            jwt_secret_key="test-secret-key-with-at-least-32-characters"
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_bulk_update_permission(
                credentials=credentials(),
                organization_id=ORG_ID,
                db=db,
            )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_inactive_user_cannot_bulk_update():
    db = mock_db(None, None)

    with patch(
        "app.api.dependencies.authorization.decode_access_token",
        return_value=USER_ID,
    ), patch(
        "app.api.dependencies.authorization.get_settings",
        return_value=SimpleNamespace(
            jwt_secret_key="test-secret-key-with-at-least-32-characters"
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_bulk_update_permission(
                credentials=credentials(),
                organization_id=ORG_ID,
                db=db,
            )

    assert exc.value.status_code == 401
