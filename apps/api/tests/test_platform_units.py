"""Pure unit tests for platform building blocks (no database)."""

import time
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.routes.sales import period_bounds, period_for, recalculate, shift_period
from app.core import totp
from app.core.permissions import ALL_PERMISSIONS, SYSTEM_ROLES, normalize_permissions
from app.core.security import password_policy_error
from app.services.automation import evaluate_condition, render_template, score_lead
from app.services.catalog import OBJECTS
from app.services.reporting import period_range


def test_totp_round_trip_and_window():
    secret = totp.generate_secret()
    assert totp.verify_code(secret, totp.current_code(secret))
    assert totp.verify_code(secret, totp.current_code(secret, at=time.time() - 30))
    assert not totp.verify_code(secret, totp.current_code(secret, at=time.time() - 300))
    assert not totp.verify_code(secret, "12345")
    assert totp.provisioning_uri(secret, "a@b.com").startswith("otpauth://totp/")


def test_totp_matches_rfc6238_reference_vector():
    # RFC 6238 SHA-1 test secret "12345678901234567890" at T=59 -> 94287082 (8 digits); 6-digit -> 287082
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.current_code(secret, at=59) == "287082"


def test_system_roles_are_consistent():
    assert SYSTEM_ROLES["owner"].permissions == ALL_PERMISSIONS
    assert "admin.users" not in SYSTEM_ROLES["manager"].permissions
    assert SYSTEM_ROLES["viewer"].permissions <= {"records.read", "records.view_all", "sales.forecast_view", "service.cases"}
    assert normalize_permissions(["records.read", "made.up", "records.read"]) == ["records.read"]


@pytest.mark.parametrize(
    "password,ok",
    [("short", False), ("alllowercaseletters", False), ("Lowercase123", True), ("Aiva#2026demo", True)],
)
def test_password_policy(password, ok):
    assert (password_policy_error(password, "jane@example.com") is None) is ok


def test_password_policy_rejects_email_name():
    assert password_policy_error("JaneDoe#2026", "janedoe@example.com") is not None


def lead(**values):
    base = dict(first_name="Ada", last_name="Lovelace", email=None, phone=None, company_name=None,
                job_title=None, source="website", status="new", score=0, owner_user_id=None)
    base.update(values)
    return SimpleNamespace(**base)


def test_conditions_cover_operators():
    record = lead(job_title="VP Sales", email="ada@x.io", score=55)
    assert evaluate_condition(record, {"field": "job_title", "operator": "contains", "value": "vp"})
    assert evaluate_condition(record, {"field": "source", "operator": "equals", "value": "Website"})
    assert evaluate_condition(record, {"field": "status", "operator": "in", "value": "new, contacted"})
    assert evaluate_condition(record, {"field": "score", "operator": "greater_than", "value": "50"})
    assert not evaluate_condition(record, {"field": "score", "operator": "less_than", "value": "50"})
    assert evaluate_condition(record, {"field": "phone", "operator": "is_empty"})
    assert evaluate_condition(record, {"field": "email", "operator": "is_not_empty"})
    assert not evaluate_condition(record, {"field": "missing_field", "operator": "equals", "value": "x"})


def test_lead_score_is_capped_and_explained():
    rules = [
        {"name": "Exec", "field": "job_title", "operator": "contains", "value": "chief", "points": 60},
        {"name": "Referral", "field": "source", "operator": "equals", "value": "referral", "points": 60},
        {"name": "No phone", "field": "phone", "operator": "is_empty", "value": None, "points": -5},
    ]
    score, breakdown = score_lead(lead(job_title="Chief Revenue Officer", source="referral"), rules)
    assert score == 100
    assert [b["rule"] for b in breakdown] == ["Exec", "Referral", "No phone"]
    assert score_lead(lead(), rules)[0] == 0


def test_template_rendering():
    record = SimpleNamespace(name="Big deal", amount=Decimal("125000"))
    assert render_template("{{name}} worth ${{amount}}", record, OBJECTS["opportunity"]) == "Big deal worth $125,000.00"
    assert render_template("{{record}}", record, OBJECTS["opportunity"]) == "Big deal"


def test_fiscal_periods():
    assert period_bounds("2026-Q4", 1) == (date(2026, 10, 1), date(2026, 12, 31))
    # Fiscal year starting in July: Q1 is Jul–Sep.
    assert period_bounds("2026-Q1", 7) == (date(2026, 7, 1), date(2026, 9, 30))
    assert period_bounds("2026-Q3", 7) == (date(2027, 1, 1), date(2027, 3, 31))
    assert period_for(date(2027, 2, 14), 7) == "2026-Q3"
    assert period_for(date(2026, 10, 21), 1) == "2026-Q4"
    assert shift_period("2026-Q4", 1) == "2027-Q1"
    assert shift_period("2026-Q1", -1) == "2025-Q4"


def test_relative_report_periods():
    start, end = period_range("this_quarter", date(2026, 10, 21))
    assert (start, end) == (date(2026, 10, 1), date(2027, 1, 1))
    assert period_range("last_month", date(2026, 1, 15)) == (date(2025, 12, 1), date(2026, 1, 1))
    assert period_range("last_7_days", date(2026, 10, 21)) == (date(2026, 10, 15), date(2026, 10, 22))


def test_quote_math():
    items = [
        SimpleNamespace(quantity=Decimal("10"), unit_price=Decimal("100"), discount_percent=Decimal("10"), tax_percent=Decimal("8.25"), line_total=None),
        SimpleNamespace(quantity=Decimal("1"), unit_price=Decimal("999.99"), discount_percent=Decimal("0"), tax_percent=Decimal("0"), line_total=None),
    ]
    quote = SimpleNamespace(line_items=items, subtotal=None, discount_total=None, tax_total=None, total=None)
    recalculate(quote)
    assert quote.subtotal == Decimal("1999.99")
    assert quote.discount_total == Decimal("100.00")
    assert quote.tax_total == Decimal("74.25")
    assert quote.total == Decimal("1974.24")
    assert items[0].line_total == Decimal("900.00")


def test_catalog_fields_exist_on_models():
    for definition in OBJECTS.values():
        for field in definition.fields:
            if field.virtual:
                continue
            assert hasattr(definition.model, field.key), f"{definition.key}.{field.key}"
        if definition.owner_field:
            assert hasattr(definition.model, definition.owner_field)
        assert definition.link(SimpleNamespace(id=uuid.uuid4())).startswith("/")
