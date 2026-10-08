import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.api.dependencies.tenant import (
    get_current_organization_id,
)


ORGANIZATION_ID = uuid.uuid4()
OPPORTUNITY_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()

PAYLOAD = {
    "opportunity_ids": [str(OPPORTUNITY_ID)],
    "owner_user_id": str(OWNER_ID),
}


def test_bulk_owner_requires_authentication():
    async def fake_organization():
        return ORGANIZATION_ID

    app.dependency_overrides[
        get_current_organization_id
    ] = fake_organization

    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api/v1/opportunities/bulk/owner",
                json=PAYLOAD,
            )

        assert response.status_code == 401

    finally:
        app.dependency_overrides.clear()


def test_bulk_owner_rejects_invalid_token():
    async def fake_organization():
        return ORGANIZATION_ID

    app.dependency_overrides[
        get_current_organization_id
    ] = fake_organization

    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api/v1/opportunities/bulk/owner",
                headers={
                    "Authorization": "Bearer invalid-token",
                },
                json=PAYLOAD,
            )

        assert response.status_code == 401

    finally:
        app.dependency_overrides.clear()


def test_bulk_owner_requires_organization_header():
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/opportunities/bulk/owner",
            json=PAYLOAD,
        )

    assert response.status_code == 400
