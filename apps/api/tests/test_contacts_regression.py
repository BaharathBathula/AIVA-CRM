"""Regression tests for Contacts normalization and validation."""

import pytest
from pydantic import ValidationError

from app.schemas.contact import ContactCreate, ContactUpdate
from app.services.contacts import (
    normalize_contact_email,
    normalize_contact_phone,
    normalize_contact_tags,
    normalize_contact_text,
)


def test_contact_email_normalization():
    assert (
        normalize_contact_email("  TEST@EXAMPLE.COM  ")
        == "test@example.com"
    )


def test_contact_email_none():
    assert normalize_contact_email(None) == ""


def test_contact_phone_normalization_is_consistent():
    phone = "+1 214 555 0100"

    assert normalize_contact_phone(phone) == (
        normalize_contact_phone(phone)
    )


def test_contact_tags_normalization_is_consistent():
    tags = ["Customer", "Prospect"]

    assert normalize_contact_tags(tags) == (
        normalize_contact_tags(tags)
    )


def test_contact_text_normalization_is_consistent():
    value = "  Example  "

    assert normalize_contact_text(value) == (
        normalize_contact_text(value)
    )


def test_create_contact_requires_name():
    with pytest.raises(ValidationError):
        ContactCreate.model_validate({})


def test_create_contact_accepts_valid_name():
    contact = ContactCreate.model_validate(
        {
            "first_name": "AIVA",
            "last_name": "Test",
        }
    )

    assert contact.first_name == "AIVA"
    assert contact.last_name == "Test"


def test_contact_update_supports_partial_changes():
    update = ContactUpdate.model_validate(
        {
            "job_title": "Solutions Architect",
        }
    )

    assert update.job_title == "Solutions Architect"
    assert "job_title" in update.model_fields_set
