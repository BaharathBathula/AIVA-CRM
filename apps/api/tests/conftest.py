import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.db.session import get_db
from app.main import app


TEST_ORGANIZATION_ID = uuid.UUID(
    "11111111-1111-1111-1111-111111111111"
)


async def override_organization_id():
    return TEST_ORGANIZATION_ID


async def override_db():
    yield object()


@pytest.fixture
def client():
    app.dependency_overrides[
        get_current_organization_id
    ] = override_organization_id

    app.dependency_overrides[
        get_db
    ] = override_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
