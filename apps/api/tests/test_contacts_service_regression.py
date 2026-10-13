"""Contacts service regression tests with mocked database sessions."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.schemas.contact import ContactCreate
from app.services import contacts as service


def run(coroutine):
    return asyncio.run(coroutine)


def contact(**overrides):
    data = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "account_id": uuid.uuid4(),
        "first_name": "AIVA",
        "last_name": "Tester",
        "email": "test@example.com",
        "phone": None,
        "mobile": None,
        "is_primary": False,
        "is_archived": False,
        "archived_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def session_with_contacts(records):
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    result = MagicMock()
    result.scalars.return_value.all.return_value = records
    db.execute.return_value = result

    return db


def test_duplicate_email_high_confidence():
    org = uuid.uuid4()
    existing = contact(organization_id=org)
    db = session_with_contacts([existing])

    matches = run(
        service.check_contact_duplicates(
            db,
            org,
            email=" TEST@EXAMPLE.COM ",
        )
    )

    assert len(matches) == 1
    assert matches[0]["contact"] is existing
    assert matches[0]["confidence"] == "high"
    assert "Same email address" in matches[0]["reasons"]


def test_duplicate_check_excludes_self():
    org = uuid.uuid4()
    existing = contact(organization_id=org)
    db = session_with_contacts([])

    matches = run(
        service.check_contact_duplicates(
            db,
            org,
            email=existing.email,
            exclude_contact_id=existing.id,
        )
    )

    assert matches == []

    statement = db.execute.call_args.args[0]
    compiled = statement.compile()

    assert existing.id in compiled.params.values()



def test_duplicate_check_scopes_organization():
    org = uuid.uuid4()
    db = session_with_contacts([])

    run(
        service.check_contact_duplicates(
            db,
            org,
            email="test@example.com",
        )
    )

    statement = db.execute.call_args.args[0]
    compiled = statement.compile()

    assert org in compiled.params.values()


def test_primary_reassignment_clears_existing():
    org = uuid.uuid4()
    account_id = uuid.uuid4()
    previous = contact(
        organization_id=org,
        account_id=account_id,
        is_primary=True,
    )
    db = session_with_contacts([previous])

    run(
        service.clear_other_primary_contacts(
            db,
            org,
            account_id,
        )
    )

    assert previous.is_primary is False


def test_primary_contact_requires_account():
    db = session_with_contacts([])
    org = uuid.uuid4()

    payload = ContactCreate.model_validate(
        {
            "first_name": "AIVA",
            "last_name": "Tester",
            "is_primary": True,
        }
    )

    with pytest.raises(HTTPException) as error:
        run(service.create_contact(db, org, payload))

    assert error.value.status_code == 400
    db.commit.assert_not_awaited()


def test_archive_clears_primary_designation():
    org = uuid.uuid4()
    existing = contact(
        organization_id=org,
        is_primary=True,
    )
    db = session_with_contacts([])

    with patch.object(
        service,
        "get_contact",
        new_callable=AsyncMock,
        return_value=existing,
    ):
        result = run(
            service.archive_contact(
                db,
                org,
                existing.id,
            )
        )

    assert result.is_archived is True
    assert result.archived_at is not None
    assert result.is_primary is False
    db.commit.assert_awaited_once()


def test_reactivation_does_not_restore_primary():
    org = uuid.uuid4()
    existing = contact(
        organization_id=org,
        is_archived=True,
        is_primary=False,
    )
    db = session_with_contacts([])

    with patch.object(
        service,
        "get_contact",
        new_callable=AsyncMock,
        return_value=existing,
    ):
        result = run(
            service.reactivate_contact(
                db,
                org,
                existing.id,
            )
        )

    assert result.is_archived is False
    assert result.archived_at is None
    assert result.is_primary is False
    db.commit.assert_awaited_once()
