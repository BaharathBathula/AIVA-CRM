"""Custom fields: types, validation, encryption, formulas and presentation.

Fields can be added to leads, contacts, accounts, opportunities and cases
(values live in each record's ``custom_fields`` JSON column) and to custom
modules (values live in ``custom_records.data``).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt, is_encrypted, mask
from app.models.account import Account
from app.models.contact import Contact
from app.models.editions import CustomField, CustomModule, CustomRecord
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.platform import Case
from app.models.user import User
from app.services.expressions import ExpressionError, evaluate, parse

BASIC_TYPES = ("text", "textarea", "number", "date", "checkbox", "picklist")
EXTENDED_TYPES = ("currency", "percent", "datetime", "email", "phone", "url", "multiselect", "lookup", "formula", "auto_number")
FIELD_TYPES = BASIC_TYPES + EXTENDED_TYPES

TYPE_LABELS = {
    "text": "Single line text",
    "textarea": "Long text",
    "number": "Number",
    "date": "Date",
    "checkbox": "Checkbox",
    "picklist": "Picklist",
    "currency": "Currency",
    "percent": "Percent",
    "datetime": "Date and time",
    "email": "Email",
    "phone": "Phone",
    "url": "Website (URL)",
    "multiselect": "Multi-select picklist",
    "lookup": "Lookup (link to a record)",
    "formula": "Formula",
    "auto_number": "Auto-number",
}

STANDARD_OBJECTS: dict[str, type] = {
    "lead": Lead,
    "contact": Contact,
    "account": Account,
    "opportunity": Opportunity,
    "case": Case,
}
STANDARD_LABELS = {"lead": "Leads", "contact": "Contacts", "account": "Accounts", "opportunity": "Opportunities", "case": "Cases"}

LOOKUP_TARGETS = ("account", "contact", "lead", "opportunity", "user")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://[^\s]+$", re.I)
KEY_RE = re.compile(r"[^a-z0-9]+")


def make_key(label: str) -> str:
    key = KEY_RE.sub("_", label.lower()).strip("_")[:50] or "field"
    return key if key[0].isalpha() else f"f_{key}"


class FieldValueError(ValueError):
    pass


def _number(value: Any, label: str) -> float:
    try:
        return float(Decimal(str(value).replace(",", "").replace("$", "").replace("%", "").strip()))
    except (InvalidOperation, ValueError) as exc:
        raise FieldValueError(f"{label} must be a number.") from exc


def coerce(field: CustomField, raw: Any) -> Any:
    """Validate a submitted value and return what is stored (before encryption)."""

    label = field.label
    kind = field.field_type
    if raw is None or raw == "" or raw == []:
        return None
    if kind in {"text", "textarea"}:
        text = str(raw).strip()
        limit = 255 if kind == "text" else 10000
        if len(text) > limit:
            raise FieldValueError(f"{label} can be at most {limit} characters.")
        return text
    if kind in {"number", "currency", "percent"}:
        return _number(raw, label)
    if kind == "checkbox":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"true", "1", "yes", "y", "on", "checked"}
    if kind == "date":
        try:
            return date.fromisoformat(str(raw)[:10]).isoformat()
        except ValueError as exc:
            raise FieldValueError(f"{label} must be a date (YYYY-MM-DD).") from exc
    if kind == "datetime":
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).isoformat()
        except ValueError as exc:
            raise FieldValueError(f"{label} must be a date and time.") from exc
    if kind == "picklist":
        value = str(raw).strip()
        if field.options and value not in field.options:
            raise FieldValueError(f"Choose one of the {label.lower()} options.")
        return value
    if kind == "multiselect":
        values = raw if isinstance(raw, list) else [part.strip() for part in str(raw).split(";") if part.strip()]
        bad = [v for v in values if field.options and v not in field.options]
        if bad:
            raise FieldValueError(f"'{bad[0]}' isn't a {label.lower()} option.")
        return [str(v) for v in values]
    if kind == "email":
        value = str(raw).strip().lower()
        if not EMAIL_RE.match(value):
            raise FieldValueError(f"{label} must be an email address.")
        return value
    if kind == "phone":
        value = str(raw).strip()
        if len(re.sub(r"\D", "", value)) < 7:
            raise FieldValueError(f"{label} must be a phone number.")
        return value
    if kind == "url":
        value = str(raw).strip()
        if not URL_RE.match(value):
            value = f"https://{value}" if "." in value and " " not in value else value
        if not URL_RE.match(value):
            raise FieldValueError(f"{label} must be a web address.")
        return value
    if kind == "lookup":
        try:
            return str(uuid.UUID(str(raw)))
        except ValueError as exc:
            raise FieldValueError(f"Choose a record for {label}.") from exc
    if kind in {"formula", "auto_number"}:
        return None  # computed, never submitted
    raise FieldValueError(f"Unknown field type for {label}.")


async def fields_for(db: AsyncSession, organization_id: uuid.UUID, object_type: str, *, include_inactive: bool = False) -> list[CustomField]:
    query = select(CustomField).where(CustomField.organization_id == organization_id, CustomField.object_type == object_type)
    if not include_inactive:
        query = query.where(CustomField.is_active.is_(True))
    return list((await db.execute(query.order_by(CustomField.step, CustomField.position, CustomField.label))).scalars().all())


def apply_values(
    fields: list[CustomField],
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
    *,
    creating: bool,
) -> dict[str, Any]:
    """Merge submitted values into stored ones. Raises FieldValueError with a friendly message."""

    data = dict(current or {})
    by_key = {field.key: field for field in fields}
    for key, raw in incoming.items():
        field = by_key.get(key)
        if field is None or field.field_type in {"formula", "auto_number"}:
            continue
        if field.is_encrypted and isinstance(raw, str) and raw.startswith("••••"):
            continue  # masked value sent back unchanged
        value = coerce(field, raw)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = encrypt(str(value)) if field.is_encrypted else value
    missing = [
        field.label
        for field in fields
        if field.required
        and field.field_type not in {"formula", "auto_number", "checkbox"}
        and (creating or field.key in incoming)
        and data.get(field.key) in (None, "", [])
    ]
    if missing:
        raise FieldValueError(f"{missing[0]} is required.")
    # Numbers are handed out only once the values are known to be valid.
    for field in fields:
        if field.field_type == "auto_number" and creating and field.key not in data:
            config = dict(field.config or {})
            next_number = int(config.get("next") or config.get("start") or 1)
            prefix = config.get("prefix") or ""
            data[field.key] = f"{prefix}{next_number}"
            config["next"] = next_number + 1
            field.config = config
    return data


def _plain(field: CustomField, value: Any, can_view_encrypted: bool) -> Any:
    if is_encrypted(value):
        return decrypt(value) if can_view_encrypted else mask(decrypt(value))
    return value


def present(
    fields: list[CustomField],
    data: dict[str, Any] | None,
    *,
    record: Any = None,
    can_view_encrypted: bool = True,
) -> dict[str, Any]:
    """Values for display: decrypted or masked, with formulas computed."""

    stored = dict(data or {})
    values: dict[str, Any] = {}
    for field in fields:
        if field.field_type == "formula":
            continue
        value = _plain(field, stored.get(field.key), can_view_encrypted)
        if field.field_type in {"number", "currency", "percent"} and value not in (None, ""):
            try:
                value = float(value)
            except (TypeError, ValueError):
                pass
        if field.field_type == "checkbox" and isinstance(value, str):
            value = value.lower() == "true"
        values[field.key] = value
    names: dict[str, Any] = {}
    table = getattr(record, "__table__", None) if record is not None else None
    if table is not None:
        for column in table.columns:
            names[column.key] = getattr(record, column.key, None)
    names.update({k: v for k, v in values.items()})
    for field in fields:
        if field.field_type != "formula":
            continue
        expression = (field.config or {}).get("expression") or ""
        try:
            result = evaluate(expression, names)
        except ExpressionError:
            result = None
        if isinstance(result, (date, datetime)):
            result = result.isoformat()
        if isinstance(result, float):
            result = round(result, 4)
        values[field.key] = result
        names[field.key] = result
    return values


def field_out(field: CustomField) -> dict[str, Any]:
    return {
        "id": str(field.id),
        "object_type": field.object_type,
        "key": field.key,
        "label": field.label,
        "field_type": field.field_type,
        "type_label": TYPE_LABELS.get(field.field_type, field.field_type),
        "required": field.required,
        "options": list(field.options or []),
        "help_text": field.help_text,
        "position": field.position,
        "step": field.step,
        "is_encrypted": field.is_encrypted,
        "is_active": field.is_active,
        "config": {k: v for k, v in (field.config or {}).items() if k != "next"},
    }


async def validate_definition(
    db: AsyncSession,
    organization_id: uuid.UUID,
    object_type: str,
    *,
    field_type: str,
    options: list[str],
    config: dict[str, Any],
    is_encrypted: bool,
    has_feature,
) -> None:
    if field_type not in FIELD_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a field type.")
    if field_type in EXTENDED_TYPES and not has_feature("extended_fields"):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"{TYPE_LABELS[field_type]} fields are part of the Enterprise plan (extended field types).",
        )
    if is_encrypted:
        if not has_feature("field_encryption"):
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Field-level encryption is part of the Enterprise plan.")
        if field_type not in {"text", "textarea", "email", "phone", "number", "date"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only text, email, phone, number and date fields can be encrypted.")
    if field_type in {"picklist", "multiselect"}:
        cleaned = [o.strip() for o in options if o.strip()]
        if len(cleaned) < 1:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one option.")
        if len(set(cleaned)) != len(cleaned):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Options must be unique.")
    if field_type == "lookup":
        target = str(config.get("target") or "")
        if target not in LOOKUP_TARGETS and not target.startswith("module:"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose what the lookup links to.")
        if target.startswith("module:"):
            module = (
                await db.execute(
                    select(CustomModule).where(CustomModule.organization_id == organization_id, CustomModule.key == target[7:])
                )
            ).scalar_one_or_none()
            if module is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That custom module doesn't exist.")
    if field_type == "formula":
        try:
            parse(str(config.get("expression") or ""))
        except ExpressionError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Formula: {exc}") from exc


async def lookup_labels(db: AsyncSession, organization_id: uuid.UUID, fields: list[CustomField], values: dict[str, Any]) -> dict[str, str]:
    """Display names for lookup values, keyed by field key."""

    labels: dict[str, str] = {}
    for field in fields:
        if field.field_type != "lookup":
            continue
        raw = values.get(field.key)
        if not raw:
            continue
        try:
            record_id = uuid.UUID(str(raw))
        except ValueError:
            continue
        target = str((field.config or {}).get("target") or "")
        label = None
        if target == "user":
            user = await db.get(User, record_id)
            label = user.full_name if user else None
        elif target in STANDARD_OBJECTS:
            record = await db.get(STANDARD_OBJECTS[target], record_id)
            if record is not None and record.organization_id == organization_id:
                label = getattr(record, "name", None) or getattr(record, "subject", None) or (
                    f"{getattr(record, 'first_name', '')} {getattr(record, 'last_name', '')}".strip()
                )
        elif target.startswith("module:"):
            record = await db.get(CustomRecord, record_id)
            if record is not None and record.organization_id == organization_id:
                label = record.name
        if label:
            labels[field.key] = label
    return labels
