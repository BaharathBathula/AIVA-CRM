import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.routes import tasks as task_routes

from tests.conftest import (
    TEST_ORGANIZATION_ID,
)


TASK_ID = uuid.UUID(
    "22222222-2222-2222-2222-222222222222"
)

USER_ID = uuid.UUID(
    "33333333-3333-3333-3333-333333333333"
)

ACCOUNT_ID = uuid.UUID(
    "44444444-4444-4444-4444-444444444444"
)


def make_task(
    **overrides,
):
    now = datetime.now(timezone.utc)

    values = {
        "id": TASK_ID,
        "organization_id": (
            TEST_ORGANIZATION_ID
        ),
        "account_id": None,
        "contact_id": None,
        "lead_id": None,
        "opportunity_id": None,
        "assigned_to_user_id": None,
        "created_by_user_id": None,
        "parent_task_id": None,
        "title": "Follow up with customer",
        "description": None,
        "task_type": "follow_up",
        "status": "open",
        "priority": "medium",
        "source": "manual",
        "start_at": None,
        "due_at": None,
        "reminder_at": None,
        "completed_at": None,
        "is_recurring": False,
        "recurrence_rule": None,
        "is_ai_generated": False,
        "external_id": None,
        "tags": [],
        "task_metadata": {},
        "created_at": now,
        "updated_at": now,
    }

    values.update(overrides)

    return SimpleNamespace(
        **values
    )


def test_create_task(
    client,
    monkeypatch,
):
    task = make_task()

    mock_create = AsyncMock(
        return_value=task
    )

    monkeypatch.setattr(
        task_routes,
        "create_task",
        mock_create,
    )

    response = client.post(
        "/api/v1/tasks",
        json={
            "title": (
                "Follow up with customer"
            ),
            "task_type": "follow_up",
        },
    )

    assert response.status_code == 201

    data = response.json()

    assert data["id"] == str(TASK_ID)

    assert (
        data["organization_id"]
        == str(TEST_ORGANIZATION_ID)
    )

    assert (
        data["title"]
        == "Follow up with customer"
    )

    assert data["status"] == "open"

    mock_create.assert_awaited_once()


def test_list_tasks_with_filters(
    client,
    monkeypatch,
):
    task = make_task(
        priority="urgent",
        account_id=ACCOUNT_ID,
    )

    mock_list = AsyncMock(
        return_value=[task]
    )

    monkeypatch.setattr(
        task_routes,
        "list_tasks",
        mock_list,
    )

    response = client.get(
        (
            "/api/v1/tasks"
            "?status=open"
            "&priority=urgent"
            "&is_overdue=true"
            f"&account_id={ACCOUNT_ID}"
        )
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1
    assert data[0]["priority"] == "urgent"

    mock_list.assert_awaited_once()

    call = mock_list.await_args

    assert (
        call.kwargs["organization_id"]
        == TEST_ORGANIZATION_ID
    )

    assert (
        call.kwargs["task_status"]
        == "open"
    )

    assert (
        call.kwargs["priority"]
        == "urgent"
    )

    assert (
        call.kwargs["is_overdue"]
        is True
    )

    assert (
        call.kwargs["account_id"]
        == ACCOUNT_ID
    )


def test_get_task(
    client,
    monkeypatch,
):
    task = make_task()

    mock_get = AsyncMock(
        return_value=task
    )

    monkeypatch.setattr(
        task_routes,
        "get_task",
        mock_get,
    )

    response = client.get(
        f"/api/v1/tasks/{TASK_ID}"
    )

    assert response.status_code == 200

    assert (
        response.json()["id"]
        == str(TASK_ID)
    )

    mock_get.assert_awaited_once()


def test_update_task(
    client,
    monkeypatch,
):
    task = make_task(
        title="Updated task",
        priority="high",
    )

    mock_update = AsyncMock(
        return_value=task
    )

    monkeypatch.setattr(
        task_routes,
        "update_task",
        mock_update,
    )

    response = client.patch(
        f"/api/v1/tasks/{TASK_ID}",
        json={
            "title": "Updated task",
            "priority": "high",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["title"] == "Updated task"
    assert data["priority"] == "high"

    mock_update.assert_awaited_once()


def test_update_task_status(
    client,
    monkeypatch,
):
    task = make_task(
        status="in_progress"
    )

    mock_status = AsyncMock(
        return_value=task
    )

    monkeypatch.setattr(
        task_routes,
        "update_task_status",
        mock_status,
    )

    response = client.patch(
        (
            f"/api/v1/tasks/{TASK_ID}"
            "/status"
        ),
        json={
            "status": "in_progress"
        },
    )

    assert response.status_code == 200

    assert (
        response.json()["status"]
        == "in_progress"
    )

    mock_status.assert_awaited_once()


def test_assign_task(
    client,
    monkeypatch,
):
    task = make_task(
        assigned_to_user_id=USER_ID
    )

    mock_assign = AsyncMock(
        return_value=task
    )

    monkeypatch.setattr(
        task_routes,
        "assign_task",
        mock_assign,
    )

    response = client.patch(
        (
            f"/api/v1/tasks/{TASK_ID}"
            "/assign"
        ),
        json={
            "assigned_to_user_id": (
                str(USER_ID)
            )
        },
    )

    assert response.status_code == 200

    assert (
        response.json()[
            "assigned_to_user_id"
        ]
        == str(USER_ID)
    )

    mock_assign.assert_awaited_once()


def test_complete_task(
    client,
    monkeypatch,
):
    completed_at = datetime.now(
        timezone.utc
    )

    task = make_task(
        status="completed",
        completed_at=completed_at,
    )

    mock_complete = AsyncMock(
        return_value=task
    )

    monkeypatch.setattr(
        task_routes,
        "complete_task",
        mock_complete,
    )

    response = client.post(
        (
            f"/api/v1/tasks/{TASK_ID}"
            "/complete"
        )
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "completed"

    assert (
        data["completed_at"]
        is not None
    )

    mock_complete.assert_awaited_once()


def test_reopen_task(
    client,
    monkeypatch,
):
    task = make_task(
        status="open",
        completed_at=None,
    )

    mock_reopen = AsyncMock(
        return_value=task
    )

    monkeypatch.setattr(
        task_routes,
        "reopen_task",
        mock_reopen,
    )

    response = client.post(
        (
            f"/api/v1/tasks/{TASK_ID}"
            "/reopen"
        )
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "open"
    assert data["completed_at"] is None

    mock_reopen.assert_awaited_once()


def test_delete_task(
    client,
    monkeypatch,
):
    mock_delete = AsyncMock(
        return_value=None
    )

    monkeypatch.setattr(
        task_routes,
        "delete_task",
        mock_delete,
    )

    response = client.delete(
        f"/api/v1/tasks/{TASK_ID}"
    )

    assert response.status_code == 204
    assert response.content == b""

    mock_delete.assert_awaited_once()


def test_reject_invalid_task_status(
    client,
):
    response = client.patch(
        (
            f"/api/v1/tasks/{TASK_ID}"
            "/status"
        ),
        json={
            "status": "random_status"
        },
    )

    assert response.status_code == 422


def test_reject_due_date_before_start(
    client,
):
    response = client.post(
        "/api/v1/tasks",
        json={
            "title": "Invalid dates",
            "start_at": (
                "2026-10-10T10:00:00Z"
            ),
            "due_at": (
                "2026-10-09T10:00:00Z"
            ),
        },
    )

    assert response.status_code == 422


def test_recurring_task_requires_rule(
    client,
):
    response = client.post(
        "/api/v1/tasks",
        json={
            "title": "Weekly review",
            "is_recurring": True,
        },
    )

    assert response.status_code == 422


def test_invalid_priority_rejected(
    client,
):
    response = client.post(
        "/api/v1/tasks",
        json={
            "title": "Priority test",
            "priority": "criticalish",
        },
    )

    assert response.status_code == 422
