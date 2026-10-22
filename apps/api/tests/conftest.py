import os
import uuid

# Must be set before the app (and its cached settings) is imported.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-at-least-32-characters")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.dependencies.auth import Principal, get_current_principal  # noqa: E402
from app.api.dependencies.tenant import get_current_organization_id  # noqa: E402
from app.core.permissions import ALL_PERMISSIONS  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402


TEST_ORGANIZATION_ID = uuid.UUID(
    "11111111-1111-1111-1111-111111111111"
)

TEST_USER_ID = uuid.UUID(
    "99999999-9999-9999-9999-999999999999"
)


async def override_organization_id():
    return TEST_ORGANIZATION_ID


async def override_db():
    yield object()


async def override_principal():
    return Principal(
        user_id=TEST_USER_ID,
        organization_id=TEST_ORGANIZATION_ID,
        role="owner",
        permissions=ALL_PERMISSIONS,
        email="owner@example.com",
        full_name="Test Owner",
    )


@pytest.fixture
def client():
    """Route-level tests with services mocked and an owner signed in."""

    app.dependency_overrides[
        get_current_organization_id
    ] = override_organization_id

    app.dependency_overrides[
        get_db
    ] = override_db

    app.dependency_overrides[
        get_current_principal
    ] = override_principal

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
