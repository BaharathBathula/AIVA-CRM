"""Unit tests for plans, expressions, custom fields, enrichment, merge fields and predictions."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.crypto import decrypt, encrypt, is_encrypted, mask
from app.core.plans import FEATURES, PLANS, required_plan
from app.models.account import Account
from app.models.editions import CustomField, Subscription
from app.models.lead import Lead
from app.services.billing import add_month, plan_state
from app.services.custom_fields import FieldValueError, apply_values, coerce, present
from app.services.enrichment import enrich, normalize_phone, seniority
from app.services.expressions import ExpressionError, evaluate, names_in, parse
from app.services.merge import render
from app.services.predictions import Model, predict


def test_plans_are_nested_and_priced():
    assert [PLANS[k].price for k in ("starter", "pro", "enterprise")] == [Decimal("19"), Decimal("29"), Decimal("59")]
    assert PLANS["starter"].features < PLANS["pro"].features < PLANS["enterprise"].features
    assert PLANS["trial"].features == PLANS["enterprise"].features
    assert required_plan("cpq").key == "pro" and required_plan("sandbox").key == "enterprise"
    assert len({f.key for f in FEATURES}) == len(FEATURES)


def test_plan_state_trial_expiry_and_cancellation():
    now = datetime.now(timezone.utc)
    trial = Subscription(plan="trial", status="trialing", trial_ends_at=now + timedelta(days=2, hours=1), cancel_at_period_end=False)
    state = plan_state(trial, now)
    assert state.days_left == 3 and not state.read_only
    trial.trial_ends_at = now - timedelta(minutes=1)
    assert plan_state(trial, now).read_only
    paid = Subscription(plan="pro", status="active", cancel_at_period_end=True, current_period_end=now - timedelta(days=1))
    assert plan_state(paid, now).status == "canceled"
    assert "cpq" in plan_state(paid, now).features and "sandbox" not in plan_state(paid, now).features


def test_add_month_clamps_day():
    assert add_month(datetime(2026, 1, 31, tzinfo=timezone.utc)).date() == date(2026, 2, 28)
    assert add_month(datetime(2026, 12, 15, tzinfo=timezone.utc)).date() == date(2027, 1, 15)


@pytest.mark.parametrize(
    ("expression", "names", "expected"),
    [
        ("amount * 0.1", {"amount": 250}, 25.0),
        ("ROUND(premium / 12, 2)", {"premium": 1000}, 83.33),
        ('IF(priority == "high", "Call today", "Email")', {"priority": "HIGH"}, "Call today"),
        ("CONCAT(first_name, ' ', last_name)", {"first_name": "Ana", "last_name": "Lopez"}, "Ana Lopez"),
        ("DAYS_BETWEEN('2026-01-01', '2026-01-31')", {}, 30),
        ("amount > 100 and stage != 'lost'", {"amount": "150", "stage": "open"}, True),
        ("missing + 1", {}, None),
        ("10 / 0", {}, None),
        ("COALESCE(nickname, first_name)", {"first_name": "Bo"}, "Bo"),
    ],
)
def test_expressions(expression, names, expected):
    assert evaluate(expression, names) == expected


@pytest.mark.parametrize("bad", ["__import__('os')", "open('x')", "a.b", "[1,2]", "lambda: 1", "x[0]", "f(x=1)"])
def test_expressions_reject_unsafe_code(bad):
    with pytest.raises(ExpressionError):
        parse(bad)


def test_names_in_ignores_functions():
    assert names_in("ROUND(amount * rate, 2)") == {"amount", "rate"}


def _field(key, kind, **extra):
    return CustomField(key=key, label=key.replace("_", " ").title(), field_type=kind, required=extra.pop("required", False), options=extra.pop("options", []), is_encrypted=extra.pop("is_encrypted", False), config=extra.pop("config", {}))


def test_custom_field_coercion():
    assert coerce(_field("n", "currency"), "$1,250.50") == 1250.5
    assert coerce(_field("d", "date"), "2026-03-04T10:00:00") == "2026-03-04"
    assert coerce(_field("c", "checkbox"), "yes") is True
    assert coerce(_field("u", "url"), "example.com") == "https://example.com"
    assert coerce(_field("m", "multiselect", options=["A", "B"]), "A; B") == ["A", "B"]
    with pytest.raises(FieldValueError):
        coerce(_field("p", "picklist", options=["A"]), "Z")
    with pytest.raises(FieldValueError):
        coerce(_field("e", "email"), "nope")


def test_apply_values_encrypts_requires_and_numbers():
    fields = [
        _field("vin", "text", required=True),
        _field("ssn", "text", is_encrypted=True),
        _field("number", "auto_number", config={"prefix": "A-", "start": 7}),
        _field("total", "formula", config={"expression": "price * 2"}),
        _field("price", "number"),
    ]
    with pytest.raises(FieldValueError):
        apply_values(fields, {}, {}, creating=True)
    data = apply_values(fields, {}, {"vin": "X1", "ssn": "123456789", "price": "4"}, creating=True)
    assert is_encrypted(data["ssn"]) and data["number"] == "A-7" and fields[2].config["next"] == 8
    shown = present(fields, data, can_view_encrypted=False)
    assert shown["ssn"] == "••••••89" and shown["total"] == 8.0
    assert present(fields, data, can_view_encrypted=True)["ssn"] == "123456789"
    unchanged = apply_values(fields, data, {"ssn": "••••••89"}, creating=False)
    assert unchanged["ssn"] == data["ssn"]


def test_encryption_round_trip():
    secret = encrypt("hello")
    assert secret.startswith("enc:v1:") and decrypt(secret) == "hello" and encrypt(secret) == secret
    assert mask("abcdef") == "••••••ef"


def test_enrichment_rules():
    assert normalize_phone("(214) 555-0142") == ("+1 214-555-0142", "United States / Canada")
    assert normalize_phone("+44 20 7946 0958")[1] == "United Kingdom"
    assert seniority("Chief Revenue Officer") == "Owner / C-level"
    assert seniority("Account Executive") == "Individual contributor"
    lead = Lead(first_name="Ana", last_name="Lopez", email="ana@lopez-roofing.com", phone="2145550100", job_title="VP Sales", custom_fields={})
    result = enrich(lead)
    assert lead.company_name == "Lopez Roofing" and result["insights"]["department"] == "Sales"
    personal = Lead(first_name="Bo", last_name="Chen", email="bo@gmail.com", custom_fields={})
    enrich(personal)
    assert personal.company_name is None and personal.custom_fields["_enrichment"]["email_type"] == "Personal"
    account = Account(name="Northgate Dental Group", domain="northgate.example", custom_fields={})
    enrich(account)
    assert account.industry == "Healthcare" and account.website == "https://northgate.example"


def test_merge_fields_with_fallbacks():
    context = {"first_name": "", "company": "Acme", "cf": {"fleet": 9}}
    assert render("Hi {{first_name|there}} at {{ company }} ({{cf.fleet}} vans)", context) == "Hi there at Acme (9 vans)"


def test_prediction_factors_move_probability():
    stage = uuid.uuid4()
    model = Model(stage_rates={stage: 0.4}, org_win_rate=0.3, owner_rates={}, median_cycle_days=40, median_won_amount=10000, closed_count=30)
    now = datetime.now(timezone.utc)
    healthy = SimpleNamespace(stage_id=stage, probability=40, expected_close_date=date.today() + timedelta(days=10), created_at=now - timedelta(days=20), owner_user_id=None, amount=Decimal("8000"), next_step="Demo")
    stale = SimpleNamespace(stage_id=stage, probability=40, expected_close_date=date.today() - timedelta(days=30), created_at=now - timedelta(days=200), owner_user_id=None, amount=Decimal("90000"), next_step=None)
    good = predict(model, healthy, now - timedelta(days=2))
    bad = predict(model, stale, None)
    assert good.probability > 40 > bad.probability
    assert bad.label == "at_risk" and any(f["impact"] == "negative" for f in bad.factors)
