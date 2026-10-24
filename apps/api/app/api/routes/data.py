"""Data import and export, enrichment, and call logging."""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.core.crypto import is_encrypted
from app.db.session import get_db
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import CustomModule, CustomRecord, ImportJob
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.task import Task
from app.models.user import User
from app.services.automation import jsonable, record_audit
from app.services.catalog import OBJECTS
from app.services.custom_fields import FieldValueError, apply_values, fields_for, present
from app.services.enrichment import enrich
from app.services.lookups import names_for

router = APIRouter(tags=["Data"])

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

IMPORT_FIELDS: dict[str, list[tuple[str, str, bool]]] = {
    "lead": [
        ("first_name", "First name", False),
        ("last_name", "Last name", True),
        ("email", "Email", False),
        ("phone", "Phone", False),
        ("company_name", "Company", False),
        ("job_title", "Job title", False),
        ("source", "Source", False),
        ("status", "Status", False),
        ("notes", "Notes", False),
        ("owner", "Owner (email)", False),
    ],
    "contact": [
        ("first_name", "First name", False),
        ("last_name", "Last name", True),
        ("email", "Email", False),
        ("phone", "Phone", False),
        ("mobile", "Mobile", False),
        ("job_title", "Job title", False),
        ("department", "Department", False),
        ("segment", "Segment", False),
        ("linkedin_url", "LinkedIn URL", False),
        ("account", "Account name", False),
        ("owner", "Owner (email)", False),
    ],
    "account": [
        ("name", "Account name", True),
        ("domain", "Domain", False),
        ("website", "Website", False),
        ("industry", "Industry", False),
        ("phone", "Phone", False),
        ("employee_count", "Employees", False),
        ("annual_revenue", "Annual revenue", False),
        ("billing_city", "City", False),
        ("billing_state", "State", False),
        ("billing_postal_code", "Postal code", False),
        ("billing_country", "Country", False),
        ("description", "Description", False),
        ("owner", "Owner (email)", False),
    ],
}
MODELS = {"lead": Lead, "contact": Contact, "account": Account}
LEAD_STATUSES = {"new", "contacted", "qualified", "unqualified", "nurture"}

ALIASES = {
    "firstname": "first_name", "first": "first_name", "givenname": "first_name",
    "lastname": "last_name", "last": "last_name", "surname": "last_name", "familyname": "last_name",
    "emailaddress": "email", "mail": "email", "e_mail": "email", "workemail": "email",
    "phonenumber": "phone", "telephone": "phone", "tel": "phone", "workphone": "phone",
    "mobilephone": "mobile", "cell": "mobile", "cellphone": "mobile",
    "company": "company_name", "companyname": "company_name", "organization": "company_name", "organisation": "company_name",
    "title": "job_title", "jobtitle": "job_title", "position": "job_title", "role": "job_title",
    "accountname": "account", "account": "account",
    "owneremail": "owner", "owner": "owner", "assignedto": "owner",
    "leadsource": "source", "source": "source",
    "leadstatus": "status", "status": "status",
    "notes": "notes", "description": "description", "comments": "notes",
    "website": "website", "url": "website", "domain": "domain",
    "industry": "industry", "employees": "employee_count", "numberofemployees": "employee_count", "employeecount": "employee_count",
    "annualrevenue": "annual_revenue", "revenue": "annual_revenue",
    "city": "billing_city", "state": "billing_state", "province": "billing_state", "zip": "billing_postal_code",
    "zipcode": "billing_postal_code", "postalcode": "billing_postal_code", "postcode": "billing_postal_code", "country": "billing_country",
    "linkedin": "linkedin_url", "linkedinurl": "linkedin_url", "department": "department", "segment": "segment",
    "name": "name",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    if len(text.encode()) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Files can be up to 5 MB. Split larger files.")
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)]
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty.")
    headers = [h.strip() or f"Column {i + 1}" for i, h in enumerate(rows[0])]
    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Import up to {MAX_ROWS:,} rows at a time.")
    return headers, body


def suggest(header: str, options: set[str]) -> str | None:
    key = re.sub(r"[^a-z0-9]", "", header.lower())
    for candidate in (ALIASES.get(key), key, header.lower().strip().replace(" ", "_")):
        if candidate and candidate in options:
            return candidate
    return None


async def _field_options(db: AsyncSession, principal: Principal, object_type: str) -> list[dict[str, Any]]:
    custom = await fields_for(db, principal.organization_id, object_type)
    return [
        {"key": key, "label": label, "required": required} for key, label, required in IMPORT_FIELDS[object_type]
    ] + [
        {"key": f"cf.{f.key}", "label": f"{f.label} (custom)", "required": False}
        for f in custom
        if f.field_type not in {"formula", "auto_number", "lookup"}
    ]


class PreviewPayload(BaseModel):
    object_type: str = Field(pattern="^(lead|contact|account)$")
    csv: str = Field(min_length=1)
    filename: str | None = None


@router.post("/data/import/preview", dependencies=[Depends(require_feature("data_import_export"))])
async def import_preview(
    payload: PreviewPayload,
    principal: Principal = Depends(require_permission("data.import")),
    db: AsyncSession = Depends(get_db),
):
    headers, rows = parse_csv(payload.csv)
    fields = await _field_options(db, principal, payload.object_type)
    options = {f["key"] for f in fields} | {f["key"][3:] for f in fields if f["key"].startswith("cf.")}
    mapping = {}
    for header in headers:
        match = suggest(header, options)
        if match and not any(f["key"] == match for f in fields) and f"cf.{match}" in {f["key"] for f in fields}:
            match = f"cf.{match}"
        mapping[header] = match
    return {
        "headers": headers,
        "sample": rows[:5],
        "total_rows": len(rows),
        "fields": fields,
        "mapping": mapping,
    }


class ImportPayload(PreviewPayload):
    mapping: dict[str, str | None]
    duplicate_action: str = Field(default="skip", pattern="^(skip|update|create)$")
    default_owner_user_id: uuid.UUID | None = None
    default_source: str | None = Field(default=None, max_length=80)


def _number(value: str, integer: bool = False) -> Any:
    cleaned = value.replace(",", "").replace("$", "").strip()
    if not cleaned:
        return None
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' isn't a number") from exc
    return int(number) if integer else number


@router.post("/data/import", dependencies=[Depends(require_feature("data_import_export"))])
async def run_import(
    payload: ImportPayload,
    principal: Principal = Depends(require_permission("data.import")),
    db: AsyncSession = Depends(get_db),
):
    headers, rows = parse_csv(payload.csv)
    object_type = payload.object_type
    model = MODELS[object_type]
    mapping = {header: target for header, target in payload.mapping.items() if target and header in headers}
    targets = set(mapping.values())
    required = [key for key, _, req in IMPORT_FIELDS[object_type] if req]
    if object_type in {"lead", "contact"}:
        if "last_name" not in targets and "email" not in targets:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Map a column to Last name or Email.")
    elif not set(required) <= targets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Map a column to the account name.")
    if len(targets) != len(mapping):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Two columns are mapped to the same field.")

    custom_fields = await fields_for(db, principal.organization_id, object_type)
    members = dict(
        (
            await db.execute(
                select(func.lower(User.email), User.id)
                .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
                .where(OrganizationMembership.organization_id == principal.organization_id)
            )
        ).all()
    )
    index = {header: position for position, header in enumerate(headers)}

    existing: dict[str, Any] = {}
    if object_type in {"lead", "contact"}:
        for record in (
            await db.execute(select(model).where(model.organization_id == principal.organization_id, model.email.is_not(None)))
        ).scalars():
            existing[record.email.lower()] = record
    else:
        for record in (await db.execute(select(Account).where(Account.organization_id == principal.organization_id))).scalars():
            existing[record.name.strip().lower()] = record
    accounts_by_name: dict[str, Account] = {}
    if object_type == "contact":
        for account in (await db.execute(select(Account).where(Account.organization_id == principal.organization_id))).scalars():
            accounts_by_name[account.name.strip().lower()] = account

    created = updated = skipped = 0
    errors: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    from app.services import workspace_settings as _ws

    # Settings → Team sharing & defaults: imported accounts and leads can be routed by territory instead of going to the importer.
    route_by_territory = object_type in {"account", "lead"} and (await _ws.get(db, principal.organization_id, "sharing")).import_owner == "territory"
    blocked: set[str] = set()
    if object_type in {"lead", "contact"}:
        from app.services import prospecting, workspace_settings

        rules = await workspace_settings.get(db, principal.organization_id, "prospecting")
        email_header = next((h for h, t in mapping.items() if t == "email"), None)
        if email_header is not None:
            emails = [row[index[email_header]].strip().lower() for row in rows if index[email_header] < len(row)]
            blocked = await prospecting.suppressed_emails(db, principal.organization_id, emails) if rules.check_suppression_on_import else set()
            # Removal requests are always honoured, whatever the setting.
            if not rules.check_suppression_on_import:
                from app.models.governance import SuppressionEntry

                erased = set(
                    (
                        await db.execute(
                            select(SuppressionEntry.value).where(
                                SuppressionEntry.organization_id == principal.organization_id, SuppressionEntry.reason == "removal_request"
                            )
                        )
                    ).scalars()
                )
                blocked = {e for e in emails if e in erased}

    for row_number, row in enumerate(rows, start=2):
        values: dict[str, Any] = {}
        custom: dict[str, Any] = {}
        try:
            for header, target in mapping.items():
                cell = row[index[header]].strip() if index[header] < len(row) else ""
                if not cell:
                    continue
                if target.startswith("cf."):
                    custom[target[3:]] = cell
                    continue
                if target == "email":
                    cell = cell.lower()
                    if not EMAIL_RE.match(cell):
                        raise ValueError(f"'{cell}' isn't an email address")
                elif target == "employee_count":
                    cell = _number(cell, integer=True)
                elif target == "annual_revenue":
                    cell = _number(cell)
                elif target == "owner":
                    owner = members.get(cell.lower())
                    if owner is None:
                        raise ValueError(f"No workspace member has the email {cell}")
                    cell = owner
                elif target == "status" and object_type == "lead":
                    cell = cell.lower().replace(" ", "_")
                    if cell not in LEAD_STATUSES:
                        raise ValueError(f"'{cell}' isn't a lead status")
                elif target == "source":
                    cell = cell.lower().replace(" ", "_")[:80]
                values[target] = cell
            if object_type in {"lead", "contact"} and not values.get("last_name"):
                if values.get("email"):
                    values["last_name"] = values["email"].split("@")[0][:120]
                else:
                    raise ValueError("Last name is empty")
            if object_type == "account" and not values.get("name"):
                raise ValueError("Account name is empty")
            if values.get("email") and values["email"] in blocked:
                skipped += 1
                errors.append({"row": row_number, "message": f"{values['email']} is on the do-not-contact list"})
                continue
            key = (values.get("email") or "") if object_type != "account" else values["name"].strip().lower()
            if key and key in seen_keys and payload.duplicate_action != "create":
                skipped += 1
                continue
            if key:
                seen_keys.add(key)
            match = existing.get(key) if key else None
            if match is not None and payload.duplicate_action == "skip":
                skipped += 1
                continue

            explicit_owner = values.pop("owner", None) or payload.default_owner_user_id
            owner = explicit_owner or principal.user_id
            account_name = values.pop("account", None)
            record = match if (match is not None and payload.duplicate_action == "update") else None
            if record is None:
                record = model(id=uuid.uuid4(), organization_id=principal.organization_id)
                if object_type == "lead":
                    record.first_name = ""
                    record.status = "new"
                    record.score = 0
                    record.source = payload.default_source or "import"
                if object_type == "contact":
                    record.first_name = ""
                    record.tags = []
                    record.is_primary = False
                    record.is_active = True
                    record.is_archived = False
                if object_type == "account":
                    record.lifecycle_stage = "prospect"
                    record.is_archived = False
                record.custom_fields = {}
                # Left empty, the save hook gives it to the territory's next rep (or the importer when nothing matches).
                record.owner_user_id = None if (route_by_territory and not explicit_owner) else owner
                db.add(record)
                created += 1
            else:
                updated += 1
            for key_name, value in values.items():
                setattr(record, key_name, value)
            if account_name and object_type == "contact":
                account = accounts_by_name.get(account_name.lower())
                if account is None:
                    account = Account(
                        id=uuid.uuid4(),
                        organization_id=principal.organization_id,
                        name=account_name[:200],
                        lifecycle_stage="prospect",
                        is_archived=False,
                        owner_user_id=owner,
                        custom_fields={},
                    )
                    db.add(account)
                    accounts_by_name[account_name.lower()] = account
                record.account_id = account.id
            if custom:
                record.custom_fields = apply_values(custom_fields, record.custom_fields or {}, custom, creating=False)
        except (ValueError, FieldValueError) as exc:
            errors.append({"row": row_number, "message": str(exc)})
            continue

    job = ImportJob(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        object_type=object_type,
        filename=(payload.filename or "import.csv")[:300],
        status="completed" if not errors else "partial",
        total_rows=len(rows),
        created_count=created,
        updated_count=updated,
        skipped_count=skipped,
        error_count=len(errors),
        errors=errors[:100],
        mapping=mapping,
        options={"duplicate_action": payload.duplicate_action},
        created_by_user_id=principal.user_id,
    )
    db.add(job)
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="import",
        entity_type=object_type,
        entity_label=job.filename,
        changes={"created": created, "updated": updated, "skipped": skipped, "errors": len(errors)},
    )
    await db.commit()
    return import_out(job)


def import_out(job: ImportJob, owners: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "object_type": job.object_type,
        "filename": job.filename,
        "status": job.status,
        "total_rows": job.total_rows,
        "created": job.created_count,
        "updated": job.updated_count,
        "skipped": job.skipped_count,
        "errors": job.error_count,
        "error_rows": job.errors,
        "created_by": (owners or {}).get(str(job.created_by_user_id)),
        "created_at": job.created_at,
    }


@router.get("/data/imports", dependencies=[Depends(require_feature("data_import_export"))])
async def list_imports(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    jobs = (
        await db.execute(
            select(ImportJob).where(ImportJob.organization_id == principal.organization_id).order_by(ImportJob.created_at.desc()).limit(30)
        )
    ).scalars().all()
    owners = await names_for(db, principal.organization_id, "user", [j.created_by_user_id for j in jobs])
    return [import_out(job, owners) for job in jobs]


EXPORTABLE = ("lead", "contact", "account", "opportunity", "case", "task", "product", "quote", "activity")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if is_encrypted(value):
        return "(encrypted)"
    value = jsonable(value)
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@router.get("/data/export/{object_type}.csv", dependencies=[Depends(require_feature("data_import_export"))])
async def export_csv(
    object_type: str,
    principal: Principal = Depends(require_permission("records.export")),
    db: AsyncSession = Depends(get_db),
):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    org = principal.organization_id

    if object_type.startswith("m_"):
        module = (
            await db.execute(select(CustomModule).where(CustomModule.organization_id == org, CustomModule.key == object_type))
        ).scalar_one_or_none()
        if module is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
        fields = await fields_for(db, org, module.key)
        writer.writerow(["id", "name", *[f.label for f in fields], "created_at"])
        for record in (await db.execute(select(CustomRecord).where(CustomRecord.module_id == module.id).order_by(CustomRecord.created_at))).scalars():
            values = present(fields, record.data, can_view_encrypted=principal.can("data.view_encrypted"))
            writer.writerow([record.id, record.name, *[_cell(values.get(f.key)) for f in fields], _cell(record.created_at)])
        filename = f"{module.key}.csv"
    else:
        definition = OBJECTS.get(object_type)
        if definition is None or object_type not in EXPORTABLE:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That record type can't be exported.")
        if object_type in {"quote", "product"} and not principal.has("cpq"):
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Quotes and products are part of the Pro plan.")
        model = definition.model
        columns = [f for f in definition.fields if not f.virtual]
        custom = await fields_for(db, org, object_type) if hasattr(model, "custom_fields") else []
        writer.writerow(["id", *[f.label for f in columns], *[c.label for c in custom]])
        query = select(model).where(model.organization_id == org)
        if definition.owner_field and not principal.can("records.view_all"):
            query = query.where(getattr(model, definition.owner_field) == principal.user_id)
        rows = (await db.execute(query.order_by(model.created_at).limit(100000))).scalars().all()
        lookups: dict[str, dict[str, str]] = {}
        for field in columns:
            if field.type == "user":
                lookups[field.key] = await names_for(db, org, "user", [getattr(r, field.key) for r in rows])
            elif field.type == "ref" and field.ref:
                lookups[field.key] = await names_for(db, org, field.ref, [getattr(r, field.key) for r in rows])
        for record in rows:
            values = present(custom, getattr(record, "custom_fields", None) or {}, can_view_encrypted=principal.can("data.view_encrypted")) if custom else {}
            line = [record.id]
            for field in columns:
                value = getattr(record, field.key, None)
                if field.key in lookups and value is not None:
                    value = lookups[field.key].get(str(value), value)
                line.append(_cell(value))
            line.extend(_cell(values.get(c.key)) for c in custom)
            writer.writerow(line)
        filename = f"{definition.label.lower()}-{date.today().isoformat()}.csv"
    record_audit(db, organization_id=org, action="export", entity_type=object_type, entity_label=filename)
    await db.commit()
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ===========================================================================
# Enrichment
# ===========================================================================


class BulkEnrich(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


@router.post("/enrich/{object_type}/{record_id}", dependencies=[Depends(require_feature("data_enrichment"))])
async def enrich_record(
    object_type: str,
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    model = MODELS.get(object_type)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leads, contacts and accounts can be enriched.")
    record = await db.get(model, record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    result = enrich(record)
    await db.commit()
    return result


@router.get("/enrich/{object_type}/{record_id}", dependencies=[Depends(require_feature("data_enrichment"))])
async def enrichment_insights(
    object_type: str,
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    model = MODELS.get(object_type)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown record type.")
    record = await db.get(model, record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    return {"insights": (record.custom_fields or {}).get("_enrichment"), "attribution": (record.custom_fields or {}).get("_attribution")}


@router.post("/enrich/{object_type}", dependencies=[Depends(require_feature("data_enrichment"))])
async def enrich_many(
    object_type: str,
    payload: BulkEnrich,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    model = MODELS.get(object_type)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leads, contacts and accounts can be enriched.")
    records = (
        await db.execute(select(model).where(model.organization_id == principal.organization_id, model.id.in_(payload.ids)))
    ).scalars().all()
    changed = 0
    for record in records:
        if enrich(record)["applied"]:
            changed += 1
    await db.commit()
    return {"processed": len(records), "updated": changed}


# ===========================================================================
# Calls
# ===========================================================================

OUTCOMES = ("connected", "left_voicemail", "no_answer", "busy", "wrong_number", "scheduled_callback")


class CallLog(BaseModel):
    phone: str | None = Field(default=None, max_length=60)
    direction: str = Field(default="outbound", pattern="^(inbound|outbound)$")
    outcome: str = Field(default="connected")
    duration_seconds: int = Field(default=0, ge=0, le=86400)
    notes: str | None = Field(default=None, max_length=20000)
    lead_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    follow_up_in_days: int | None = Field(default=None, ge=0, le=365)
    follow_up_title: str | None = Field(default=None, max_length=250)


@router.post("/calls", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("calling"))])
async def log_call(
    payload: CallLog,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    from app.services import workspace_settings

    dialer = await workspace_settings.get(db, principal.organization_id, "dialer")
    outcomes = {o.key: o for o in dialer.outcomes}
    if payload.outcome not in outcomes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose how the call went.")
    outcome = outcomes[payload.outcome]
    if dialer.require_notes and not (payload.notes or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Your workspace asks for call notes. Add a line about the call.")
    if payload.follow_up_in_days is None and dialer.auto_follow_up and outcome.follow_up:
        payload.follow_up_in_days = dialer.follow_up_days
    links = {}
    who = None
    for key, model in (("lead_id", Lead), ("contact_id", Contact), ("account_id", Account)):
        value = getattr(payload, key)
        if value is None:
            continue
        record = await db.get(model, value)
        if record is None or record.organization_id != principal.organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
        links[key] = value
        if who is None:
            who = getattr(record, "name", None) or f"{getattr(record, 'first_name', '')} {getattr(record, 'last_name', '')}".strip()
        if key == "contact_id" and record.account_id and not payload.account_id:
            links["account_id"] = record.account_id
    if payload.opportunity_id:
        links["opportunity_id"] = payload.opportunity_id
    minutes, seconds = divmod(payload.duration_seconds, 60)
    outcome_label = outcome.label.lower()
    activity = Activity(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        activity_type="call",
        subject=f"{'Call with' if payload.direction == 'outbound' else 'Call from'} {who or payload.phone or 'unknown'} · {outcome_label}"[:250],
        body=payload.notes,
        direction=payload.direction,
        occurred_at=utcnow(),
        activity_metadata={
            "phone": payload.phone,
            "outcome": payload.outcome,
            "connected": outcome.connected,
            "duration_seconds": payload.duration_seconds,
            "duration": f"{minutes}:{seconds:02d}",
            "source": "dialer",
        },
        **links,
    )
    db.add(activity)
    task_id = None
    if payload.follow_up_in_days is not None:
        task = Task(
            id=uuid.uuid4(),
            organization_id=principal.organization_id,
            title=(payload.follow_up_title or f"Follow up with {who or payload.phone or 'caller'}")[:250],
            task_type="call" if outcome.follow_up else "follow_up",
            status="open",
            priority="medium",
            source="manual",
            due_at=utcnow() + timedelta(days=payload.follow_up_in_days),
            reminder_at=utcnow() + timedelta(days=payload.follow_up_in_days) - timedelta(minutes=15) if payload.follow_up_in_days else None,
            assigned_to_user_id=principal.user_id,
            created_by_user_id=principal.user_id,
            is_recurring=False,
            is_ai_generated=False,
            tags=[],
            task_metadata={"from_call": str(activity.id)},
            lead_id=links.get("lead_id"),
            contact_id=links.get("contact_id"),
            account_id=links.get("account_id"),
            opportunity_id=links.get("opportunity_id"),
        )
        db.add(task)
        task_id = str(task.id)
    await db.commit()
    return {"id": str(activity.id), "subject": activity.subject, "task_id": task_id}


@router.get("/calls", dependencies=[Depends(require_feature("calling"))])
async def list_calls(
    days: int = Query(default=30, ge=1, le=365),
    mine: bool = False,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    from app.services import workspace_settings

    dialer = await workspace_settings.get(db, principal.organization_id, "dialer")
    since = utcnow() - timedelta(days=days)
    query = select(Activity).where(
        Activity.organization_id == principal.organization_id, Activity.activity_type == "call", Activity.occurred_at >= since
    )
    if mine or not principal.can("records.view_all"):
        query = query.where(Activity.created_by_user_id == principal.user_id)
    calls = (await db.execute(query.order_by(Activity.occurred_at.desc()).limit(500))).scalars().all()
    users = await names_for(db, principal.organization_id, "user", [c.created_by_user_id for c in calls])
    contacts = await names_for(db, principal.organization_id, "contact", [c.contact_id for c in calls])
    accounts = await names_for(db, principal.organization_id, "account", [c.account_id for c in calls])
    today = utcnow().date()
    items = []
    durations = []
    connected = 0
    for call in calls:
        meta = call.activity_metadata or {}
        duration = int(meta.get("duration_seconds") or 0)
        durations.append(duration)
        if meta.get("connected", meta.get("outcome", "connected") == "connected"):
            connected += 1
        link = (
            f"/contacts/{call.contact_id}" if call.contact_id else f"/leads/{call.lead_id}" if call.lead_id else f"/accounts/{call.account_id}" if call.account_id else None
        )
        items.append(
            {
                "id": str(call.id),
                "subject": call.subject,
                "notes": call.body,
                "direction": call.direction,
                "outcome": meta.get("outcome", "connected"),
                "duration_seconds": duration,
                "phone": meta.get("phone"),
                "occurred_at": call.occurred_at,
                "by": users.get(str(call.created_by_user_id)),
                "contact_name": contacts.get(str(call.contact_id)),
                "account_name": accounts.get(str(call.account_id)),
                "link": link,
            }
        )
    return {
        "items": items,
        "stats": {
            "total": len(calls),
            "today": len([c for c in calls if c.occurred_at.date() == today]),
            "connected": connected,
            "connect_rate": round(connected / len(calls) * 100) if calls else 0,
            "average_duration_seconds": round(sum(durations) / len([d for d in durations if d])) if any(durations) else 0,
            "talk_time_seconds": sum(durations),
        },
        "outcomes": [o.key for o in dialer.outcomes],
        "outcome_options": [o.model_dump() for o in dialer.outcomes],
        "dialer": {"calling_method": dialer.calling_method, "require_notes": dialer.require_notes, "auto_follow_up": dialer.auto_follow_up},
    }
