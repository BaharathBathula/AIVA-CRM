import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.routes import (
    opportunities as opportunity_routes,
)

from tests.conftest import (
    TEST_ORGANIZATION_ID,
)


OPPORTUNITY_ID = uuid.UUID(
    "aaaaaaaa-1111-1111-1111-111111111111"
)

ACCOUNT_ID = uuid.UUID(
    "bbbbbbbb-2222-2222-2222-222222222222"
)

CONTACT_ID = uuid.UUID(
    "cccccccc-3333-3333-3333-333333333333"
)

PIPELINE_ID = uuid.UUID(
    "dddddddd-4444-4444-4444-444444444444"
)

STAGE_ID = uuid.UUID(
    "eeeeeeee-5555-5555-5555-555555555555"
)

OWNER_ID = uuid.UUID(
    "ffffffff-6666-6666-6666-666666666666"
)


def make_opportunity(
    **overrides,
):
    now = datetime.now(timezone.utc)

    values = {
        "id": OPPORTUNITY_ID,
        "organization_id": (
            TEST_ORGANIZATION_ID
        ),
        "name": "AIVA Enterprise Deal",
        "description": (
            "Enterprise CRM opportunity"
        ),
        "account_id": ACCOUNT_ID,
        "primary_contact_id": CONTACT_ID,
        "pipeline_id": PIPELINE_ID,
        "stage_id": STAGE_ID,
        "owner_user_id": OWNER_ID,
        "amount": Decimal("500000.00"),
        "currency": "USD",
        "probability": 60,
        "expected_close_date": date(
            2026,
            12,
            31,
        ),
        "closed_at": None,
        "opportunity_type": (
            "new_business"
        ),
        "lead_source": "referral",
        "priority": "high",
        "forecast_category": (
            "best_case"
        ),
        "next_step": (
            "Schedule executive demo"
        ),
        "loss_reason": None,
        "competitor": "Competitor CRM",
        "stage_entered_at": now,
        "created_at": now,
        "updated_at": now,
    }

    values.update(overrides)

    return SimpleNamespace(
        **values
    )


def test_create_opportunity(
    client,
    monkeypatch,
):
    opportunity = make_opportunity()

    mock_create = AsyncMock(
        return_value=opportunity
    )

    monkeypatch.setattr(
        opportunity_routes,
        "create_opportunity",
        mock_create,
    )

    response = client.post(
        "/api/v1/opportunities",
        json={
            "name": "AIVA Enterprise Deal",
            "account_id": str(
                ACCOUNT_ID
            ),
            "primary_contact_id": str(
                CONTACT_ID
            ),
            "pipeline_id": str(
                PIPELINE_ID
            ),
            "stage_id": str(
                STAGE_ID
            ),
            "owner_user_id": str(
                OWNER_ID
            ),
            "amount": "500000.00",
            "currency": "USD",
            "probability": 60,
            "expected_close_date": (
                "2026-12-31"
            ),
            "description": (
                "Enterprise CRM opportunity"
            ),
            "opportunity_type": (
                "new_business"
            ),
            "lead_source": "referral",
            "priority": "high",
            "forecast_category": (
                "best_case"
            ),
            "next_step": (
                "Schedule executive demo"
            ),
            "competitor": (
                "Competitor CRM"
            ),
        },
    )

    assert response.status_code == 201

    data = response.json()

    assert (
        data["id"]
        == str(OPPORTUNITY_ID)
    )

    assert (
        data["organization_id"]
        == str(TEST_ORGANIZATION_ID)
    )

    assert (
        data["name"]
        == "AIVA Enterprise Deal"
    )

    assert (
        data["opportunity_type"]
        == "new_business"
    )

    assert (
        data["lead_source"]
        == "referral"
    )

    assert (
        data["priority"]
        == "high"
    )

    assert (
        data["forecast_category"]
        == "best_case"
    )

    assert (
        data["next_step"]
        == "Schedule executive demo"
    )

    assert (
        data["competitor"]
        == "Competitor CRM"
    )

    assert (
        Decimal(str(
            data["weighted_amount"]
        ))
        == Decimal("300000.00")
    )

    assert (
        data["stage_entered_at"]
        is not None
    )

    mock_create.assert_awaited_once()

    call = mock_create.await_args

    assert (
        call.args[1]
        == TEST_ORGANIZATION_ID
    )

    payload = call.args[2]

    assert (
        payload.opportunity_type
        == "new_business"
    )

    assert (
        payload.priority
        == "high"
    )

    assert (
        payload.forecast_category
        == "best_case"
    )


def test_update_opportunity(
    client,
    monkeypatch,
):
    opportunity = make_opportunity(
        priority="critical",
        forecast_category="commit",
        probability=80,
        next_step="Send final proposal",
    )

    mock_update = AsyncMock(
        return_value=opportunity
    )

    monkeypatch.setattr(
        opportunity_routes,
        "update_opportunity",
        mock_update,
    )

    response = client.patch(
        (
            f"/api/v1/opportunities/"
            f"{OPPORTUNITY_ID}"
        ),
        json={
            "priority": "critical",
            "forecast_category": "commit",
            "probability": 80,
            "next_step": (
                "Send final proposal"
            ),
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert (
        data["priority"]
        == "critical"
    )

    assert (
        data["forecast_category"]
        == "commit"
    )

    assert (
        data["probability"]
        == 80
    )

    assert (
        Decimal(str(
            data["weighted_amount"]
        ))
        == Decimal("400000.00")
    )

    assert (
        data["next_step"]
        == "Send final proposal"
    )

    mock_update.assert_awaited_once()


def test_probability_validation(
    client,
):
    response = client.patch(
        (
            f"/api/v1/opportunities/"
            f"{OPPORTUNITY_ID}"
        ),
        json={
            "probability": 101,
        },
    )

    assert response.status_code == 422


def test_priority_validation(
    client,
):
    response = client.patch(
        (
            f"/api/v1/opportunities/"
            f"{OPPORTUNITY_ID}"
        ),
        json={
            "priority": "super_urgent",
        },
    )

    assert response.status_code == 422


def test_opportunity_type_validation(
    client,
):
    response = client.post(
        "/api/v1/opportunities",
        json={
            "name": "Invalid Opportunity",
            "account_id": str(
                ACCOUNT_ID
            ),
            "opportunity_type": (
                "something_invalid"
            ),
        },
    )

    assert response.status_code == 422


def test_forecast_category_validation(
    client,
):
    response = client.patch(
        (
            f"/api/v1/opportunities/"
            f"{OPPORTUNITY_ID}"
        ),
        json={
            "forecast_category": (
                "maybe"
            ),
        },
    )

    assert response.status_code == 422