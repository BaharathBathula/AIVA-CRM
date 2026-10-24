"""Territories: which accounts and leads belong to whom.

A territory is a set of rules ("country is USA and state is TX, OK or LA",
"employees between 500 and 5,000", "industry is Healthcare") plus the
people who work it. New accounts and leads that match a territory are
tagged with it and, when the territory uses round-robin assignment and the
record has no owner yet, handed to the next member in turn. Territories are
checked in priority order; the first match wins.
"""

from __future__ import annotations

import itertools
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

FIELDS: dict[str, dict[str, dict[str, str]]] = {
    "account": {
        "country": {"label": "Country", "attr": "billing_country", "type": "text"},
        "state": {"label": "State / region", "attr": "billing_state", "type": "text"},
        "city": {"label": "City", "attr": "billing_city", "type": "text"},
        "postal_code": {"label": "Postal code", "attr": "billing_postal_code", "type": "text"},
        "industry": {"label": "Industry", "attr": "industry", "type": "text"},
        "employee_count": {"label": "Employees", "attr": "employee_count", "type": "number"},
        "annual_revenue": {"label": "Annual revenue", "attr": "annual_revenue", "type": "number"},
        "lifecycle_stage": {"label": "Account stage", "attr": "lifecycle_stage", "type": "text"},
        "domain": {"label": "Website domain", "attr": "domain", "type": "text"},
    },
    "lead": {
        "source": {"label": "Lead source", "attr": "source", "type": "text"},
        "company_name": {"label": "Company", "attr": "company_name", "type": "text"},
        "job_title": {"label": "Job title", "attr": "job_title", "type": "text"},
        "email_domain": {"label": "Email domain", "attr": "email", "type": "email_domain"},
    },
}
OPERATORS = {
    "in": "is any of",
    "not_in": "is none of",
    "contains": "contains any of",
    "starts_with": "starts with any of",
    "between": "is between",
    "gte": "is at least",
    "lte": "is at most",
    "is_empty": "is empty",
}


def _value(obj: Any, object_type: str, field: str) -> Any:
    if field.startswith("cf."):
        return (getattr(obj, "custom_fields", None) or {}).get(field[3:])
    spec = FIELDS.get(object_type, {}).get(field)
    if spec is None:
        return None
    raw = getattr(obj, spec["attr"], None)
    if spec["type"] == "email_domain":
        return (raw or "").split("@")[-1].lower() if raw and "@" in raw else None
    return raw


def _number(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError):
        return None


def rule_matches(obj: Any, object_type: str, rule: dict[str, Any]) -> bool:
    field, operator = rule.get("field", ""), rule.get("operator", "in")
    values = [str(v).strip().lower() for v in (rule.get("values") or []) if str(v).strip()]
    actual = _value(obj, object_type, field)
    text = str(actual).strip().lower() if actual not in (None, "") else ""
    if operator == "is_empty":
        return not text
    if operator in {"between", "gte", "lte"}:
        number = _number(actual)
        if number is None:
            return False
        bounds = [_number(v) for v in (rule.get("values") or [])]
        low = bounds[0] if bounds else None
        high = bounds[1] if len(bounds) > 1 else None
        if operator == "gte":
            return low is not None and number >= low
        if operator == "lte":
            return low is not None and number <= low
        return (low is None or number >= low) and (high is None or number <= high)
    if not text:
        return operator == "not_in"
    if operator == "in":
        return text in values
    if operator == "not_in":
        return text not in values
    if operator == "contains":
        return any(v in text for v in values)
    if operator == "starts_with":
        return any(text.startswith(v) for v in values)
    return False


def territory_matches(obj: Any, object_type: str, territory: dict[str, Any]) -> bool:
    if object_type not in (territory.get("applies_to") or ["account", "lead"]):
        return False
    rules = [r for r in territory.get("rules") or [] if r.get("field")]
    if not rules:
        return False
    results = (rule_matches(obj, object_type, rule) for rule in rules)
    return all(results) if territory.get("match", "all") == "all" else any(results)


def find_territory(obj: Any, object_type: str, territories: list[dict[str, Any]]) -> dict[str, Any] | None:
    for territory in sorted(territories, key=lambda t: (t.get("priority", 100), t.get("name", ""))):
        if territory.get("is_active", True) and territory_matches(obj, object_type, territory):
            return territory
    return None


_ROTATION: dict[str, itertools.cycle] = {}
_ROTATION_MEMBERS: dict[str, tuple[str, ...]] = {}


def next_member(territory: dict[str, Any]) -> uuid.UUID | None:
    """Round-robin through the territory's members (kept per server process)."""

    members = tuple(str(m) for m in territory.get("member_user_ids") or [])
    if not members:
        return None
    key = str(territory["id"])
    if _ROTATION_MEMBERS.get(key) != members:
        _ROTATION[key] = itertools.cycle(members)
        _ROTATION_MEMBERS[key] = members
    return uuid.UUID(next(_ROTATION[key]))


def as_context(territory: Any) -> dict[str, Any]:
    return {
        "id": str(territory.id),
        "name": territory.name,
        "match": territory.match,
        "rules": list(territory.rules or []),
        "member_user_ids": [str(m) for m in territory.member_user_ids or []],
        "assignment": territory.assignment,
        "applies_to": list(territory.applies_to or ["account", "lead"]),
        "auto_assign": territory.auto_assign,
        "priority": territory.priority,
        "is_active": territory.is_active,
    }
