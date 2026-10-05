import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.routes import (
    activities as activity_routes,
)

from tests.conftest import (
    TEST_ORGANIZATION_ID,
)


ACTIVITY_ID = uuid.UUID(
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
)

ACCOUNT_ID = uuid.UUID(
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
)

CONTACT_ID = uuid.UUID(
    "cccccccc-cccc-cccc-cccc-cccccccccccc"
)

LEAD_ID = uuid.UUID(
    "dddddddd-dddd-dddd-dddd-dddddddddddd"
)

OPPORTUNITY_ID = uuid.UUID(
    "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
)


def make_activity(
    **overrides,
):
    now = datetime.now(
        timezone.utc
    )

    values = {
        "id": ACTIVITY_ID,
        "organization_id": (
            TEST_ORGANIZATION_ID
        ),
        "account_id": ACCOUNT_ID,
        "contact_id": CONTACT_ID,
        "lead_id": None,
        "opportunity_id": None,
        "created_by_user_id": None,
        "activity_type": "call",
        "subject": "Customer follow-up",
        "body": "Discussed renewal options.",
        "direction": "outbound",
        "occurred_at": now,
        "external_id": None,
        "activity_metadata": {},
        "created_at": now,
        "updated_at": now,
    }

    values.update(
        overrides
    )

    return SimpleNamespace(
        **values
    )


def test_create_activity(
    client,
    monkeypatch,
):
    activity = make_activity(
        lead_id=LEAD_ID,
        opportunity_id=OPPORTUNITY_ID,
    )

    mock_create = AsyncMock(
        return_value=activity
    )

    monkeypatch.setattr(
        activity_routes,
        "create_activity",
        mock_create,
    )

    response = client.post(
        "/api/v1/activities",
        json={
            "account_id": str(
                ACCOUNT_ID
            ),
            "contact_id": str(
                CONTACT_ID
            ),
            "lead_id": str(
                LEAD_ID
            ),
            "opportunity_id": str(
                OPPORTUNITY_ID
            ),
            "activity_type": "call",
            "subject": (
                "Customer follow-up"
            ),
            "body": (
                "Discussed renewal options."
            ),
            "direction": "outbound",
        },
    )

    assert (
        response.status_code
        == 201
    )

    data = response.json()

    assert (
        data["id"]
        == str(ACTIVITY_ID)
    )

    assert (
        data["organization_id"]
        == str(
            TEST_ORGANIZATION_ID
        )
    )

    assert (
        data["account_id"]
        == str(ACCOUNT_ID)
    )

    assert (
        data["contact_id"]
        == str(CONTACT_ID)
    )

    assert (
        data["lead_id"]
        == str(LEAD_ID)
    )

    assert (
        data["opportunity_id"]
        == str(OPPORTUNITY_ID)
    )

    assert (
        data["activity_type"]
        == "call"
    )

    assert (
        data["direction"]
        == "outbound"
    )

    mock_create.assert_awaited_once()


def test_list_activities_with_filters(
    client,
    monkeypatch,
):
    activity = make_activity()

    mock_list = AsyncMock(
        return_value=[
            activity
        ]
    )

    monkeypatch.setattr(
        activity_routes,
        "list_activities",
        mock_list,
    )

    response = client.get(
        (
            "/api/v1/activities"
            "?activity_type=call"
            "&direction=outbound"
            f"&account_id={ACCOUNT_ID}"
            f"&contact_id={CONTACT_ID}"
            "&search=renewal"
            "&skip=5"
            "&limit=25"
        )
    )

    assert (
        response.status_code
        == 200
    )

    data = response.json()

    assert len(data) == 1

    assert (
        data[0]["activity_type"]
        == "call"
    )

    mock_list.assert_awaited_once()

    call = (
        mock_list.await_args
    )

    assert (
        call.args[1]
        == TEST_ORGANIZATION_ID
    )

    assert (
        call.kwargs[
            "activity_type"
        ]
        == "call"
    )

    assert (
        call.kwargs[
            "direction"
        ]
        == "outbound"
    )

    assert (
        call.kwargs[
            "account_id"
        ]
        == ACCOUNT_ID
    )

    assert (
        call.kwargs[
            "contact_id"
        ]
        == CONTACT_ID
    )

    assert (
        call.kwargs[
            "search"
        ]
        == "renewal"
    )

    assert (
        call.kwargs["skip"]
        == 5
    )

    assert (
        call.kwargs["limit"]
        == 25
    )


def test_account_activity_timeline(
    client,
    monkeypatch,
):
    activity = make_activity()

    mock_list = AsyncMock(
        return_value=[
            activity
        ]
    )

    monkeypatch.setattr(
        activity_routes,
        "list_account_activities",
        mock_list,
    )

    response = client.get(
        (
            f"/api/v1/accounts/"
            f"{ACCOUNT_ID}/activities"
        )
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.json()[0][
            "account_id"
        ]
        == str(ACCOUNT_ID)
    )

    mock_list.assert_awaited_once()


def test_contact_activity_timeline(
    client,
    monkeypatch,
):
    activity = make_activity()

    mock_list = AsyncMock(
        return_value=[
            activity
        ]
    )

    monkeypatch.setattr(
        activity_routes,
        "list_contact_activities",
        mock_list,
    )

    response = client.get(
        (
            f"/api/v1/contacts/"
            f"{CONTACT_ID}/activities"
        )
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.json()[0][
            "contact_id"
        ]
        == str(CONTACT_ID)
    )

    mock_list.assert_awaited_once()


def test_lead_activity_timeline(
    client,
    monkeypatch,
):
    activity = make_activity(
        account_id=None,
        contact_id=None,
        lead_id=LEAD_ID,
    )

    mock_list = AsyncMock(
        return_value=[
            activity
        ]
    )

    monkeypatch.setattr(
        activity_routes,
        "list_lead_activities",
        mock_list,
    )

    response = client.get(
        (
            f"/api/v1/leads/"
            f"{LEAD_ID}/activities"
        )
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.json()[0][
            "lead_id"
        ]
        == str(LEAD_ID)
    )

    mock_list.assert_awaited_once()


def test_opportunity_activity_timeline(
    client,
    monkeypatch,
):
    activity = make_activity(
        opportunity_id=(
            OPPORTUNITY_ID
        )
    )

    mock_list = AsyncMock(
        return_value=[
            activity
        ]
    )

    monkeypatch.setattr(
        activity_routes,
        (
            "list_opportunity_"
            "activities"
        ),
        mock_list,
    )

    response = client.get(
        (
            "/api/v1/opportunities/"
            f"{OPPORTUNITY_ID}"
            "/activities"
        )
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.json()[0][
            "opportunity_id"
        ]
        == str(
            OPPORTUNITY_ID
        )
    )

    mock_list.assert_awaited_once()


def test_activity_type_validation(
    client,
):
    response = client.get(
        (
            "/api/v1/activities"
            "?activity_type="
            "not_a_real_type"
        )
    )

    assert (
        response.status_code
        == 422
    )


def test_activity_direction_validation(
    client,
):
    response = client.get(
        (
            "/api/v1/activities"
            "?direction=sideways"
        )
    )

    assert (
        response.status_code
        == 422
    )
