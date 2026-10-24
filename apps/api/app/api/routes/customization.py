"""Custom modules, custom fields and custom records."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_feature, require_permission
from app.core.plans import plan_for
from app.db.session import get_db
from app.models.editions import CustomField, CustomModule, CustomRecord
from app.services.automation import record_audit
from app.services.billing import enforce_limit
from app.services.custom_fields import (
    BASIC_TYPES,
    EXTENDED_TYPES,
    LOOKUP_TARGETS,
    STANDARD_LABELS,
    STANDARD_OBJECTS,
    TYPE_LABELS,
    FieldValueError,
    apply_values,
    field_out,
    fields_for,
    lookup_labels,
    make_key,
    present,
    validate_definition,
)
from app.services.lookups import names_for

router = APIRouter(tags=["Customization"])

ICONS = ("box", "car", "home", "building", "briefcase", "file", "shield", "truck", "wrench", "heart", "star", "calendar")


# ===========================================================================
# Custom modules
# ===========================================================================


class ModulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    singular: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    icon: str = "box"
    is_active: bool = True


async def module_out(db: AsyncSession, module: CustomModule) -> dict:
    count = (
        await db.execute(select(func.count()).select_from(CustomRecord).where(CustomRecord.module_id == module.id))
    ).scalar_one()
    fields = await fields_for(db, module.organization_id, module.key, include_inactive=True)
    return {
        "id": str(module.id),
        "key": module.key,
        "name": module.name,
        "singular": module.singular,
        "description": module.description,
        "icon": module.icon,
        "is_active": module.is_active,
        "record_count": count,
        "field_count": len(fields),
        "steps": max([f.step for f in fields if f.is_active] or [1]),
        "created_at": module.created_at,
    }


@router.get("/custom-modules")
async def list_modules(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    modules = (
        await db.execute(
            select(CustomModule).where(CustomModule.organization_id == principal.organization_id).order_by(CustomModule.name)
        )
    ).scalars().all()
    return [await module_out(db, module) for module in modules]


@router.post("/custom-modules", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("custom_modules"))])
async def create_module(
    payload: ModulePayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    await enforce_limit(db, principal.organization_id, plan_for(principal.plan), "custom_modules", CustomModule)
    base = "m_" + make_key(payload.name)[:40]
    key = base
    suffix = 2
    while (
        await db.execute(
            select(func.count()).select_from(CustomModule).where(
                CustomModule.organization_id == principal.organization_id, CustomModule.key == key
            )
        )
    ).scalar_one():
        key = f"{base}_{suffix}"
        suffix += 1
    module = CustomModule(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        key=key,
        name=payload.name.strip(),
        singular=payload.singular.strip(),
        description=payload.description,
        icon=payload.icon if payload.icon in ICONS else "box",
        is_active=payload.is_active,
    )
    db.add(module)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="custom_module", entity_id=module.id, entity_label=module.name)
    await db.commit()
    return await module_out(db, module)


async def _module(db: AsyncSession, principal: Principal, module_id: uuid.UUID) -> CustomModule:
    module = await db.get(CustomModule, module_id)
    if module is None or module.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
    return module


async def _module_by_key(db: AsyncSession, principal: Principal, key: str) -> CustomModule:
    module = (
        await db.execute(
            select(CustomModule).where(CustomModule.organization_id == principal.organization_id, CustomModule.key == key)
        )
    ).scalar_one_or_none()
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
    return module


@router.patch("/custom-modules/{module_id}")
async def update_module(
    module_id: uuid.UUID,
    payload: ModulePayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    module = await _module(db, principal, module_id)
    module.name = payload.name.strip()
    module.singular = payload.singular.strip()
    module.description = payload.description
    module.icon = payload.icon if payload.icon in ICONS else module.icon
    module.is_active = payload.is_active
    await db.commit()
    return await module_out(db, module)


@router.delete("/custom-modules/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_module(
    module_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    module = await _module(db, principal, module_id)
    for field in await fields_for(db, principal.organization_id, module.key, include_inactive=True):
        await db.delete(field)
    record_audit(db, organization_id=principal.organization_id, action="delete", entity_type="custom_module", entity_id=module.id, entity_label=module.name)
    await db.delete(module)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Custom fields
# ===========================================================================


class FieldPayload(BaseModel):
    object_type: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    field_type: str
    required: bool = False
    options: list[str] = Field(default_factory=list)
    help_text: str | None = Field(default=None, max_length=300)
    step: int = Field(default=1, ge=1, le=10)
    is_encrypted: bool = False
    is_active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    picklist_id: uuid.UUID | None = None


async def _picklist_options(db: AsyncSession, principal: Principal, payload: FieldPayload) -> list[str] | None:
    """A field that uses a global picklist takes its options from it."""

    if payload.picklist_id is None:
        return None
    if payload.field_type not in {"picklist", "multiselect"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only picklist and multi-select fields can use a global picklist.")
    from app.models.governance import GlobalPicklist

    picklist = await db.get(GlobalPicklist, payload.picklist_id)
    if picklist is None or picklist.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Picklist not found.")
    options = [str(v.get("value")) for v in picklist.values or [] if v.get("is_active", True) and str(v.get("value") or "").strip()]
    if not options:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That picklist has no active values yet.")
    return options


async def _check_object(db: AsyncSession, principal: Principal, object_type: str) -> None:
    if object_type in STANDARD_OBJECTS:
        return
    await _module_by_key(db, principal, object_type)


@router.get("/custom-fields/types")
async def field_types(principal: Principal = Depends(get_current_principal)):
    return {
        "types": [
            {"key": key, "label": TYPE_LABELS[key], "extended": key in EXTENDED_TYPES, "available": key in BASIC_TYPES or principal.has("extended_fields")}
            for key in BASIC_TYPES + EXTENDED_TYPES
        ],
        "lookup_targets": list(LOOKUP_TARGETS),
        "standard_objects": [{"key": k, "label": v} for k, v in STANDARD_LABELS.items()],
        "can_encrypt": principal.has("field_encryption"),
        "can_use_steps": principal.has("sequential_forms"),
    }


@router.get("/custom-fields")
async def list_fields(
    object_type: str = Query(min_length=1, max_length=60),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    await _check_object(db, principal, object_type)
    return [field_out(f) for f in await fields_for(db, principal.organization_id, object_type, include_inactive=True)]


@router.post("/custom-fields", status_code=status.HTTP_201_CREATED)
async def create_field(
    payload: FieldPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    await _check_object(db, principal, payload.object_type)
    shared = await _picklist_options(db, principal, payload)
    if shared is not None:
        payload.options = shared
    await validate_definition(
        db,
        principal.organization_id,
        payload.object_type,
        field_type=payload.field_type,
        options=payload.options,
        config=payload.config,
        is_encrypted=payload.is_encrypted,
        has_feature=principal.has,
    )
    existing = await fields_for(db, principal.organization_id, payload.object_type, include_inactive=True)
    if len(existing) >= 200:
        raise HTTPException(status.HTTP_409_CONFLICT, "A record type can have up to 200 custom fields.")
    base = make_key(payload.label)
    taken = {f.key for f in existing}
    model = STANDARD_OBJECTS.get(payload.object_type)
    if model is not None:
        taken |= {column.key for column in model.__table__.columns}
    key = base
    suffix = 2
    while key in taken:
        key = f"{base}_{suffix}"
        suffix += 1
    field = CustomField(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        object_type=payload.object_type,
        key=key,
        label=payload.label.strip(),
        field_type=payload.field_type,
        required=payload.required and payload.field_type not in {"formula", "auto_number"},
        options=[o.strip() for o in payload.options if o.strip()],
        help_text=payload.help_text,
        position=len(existing),
        step=payload.step if principal.has("sequential_forms") else 1,
        is_encrypted=payload.is_encrypted,
        is_active=payload.is_active,
        config=payload.config,
        picklist_id=payload.picklist_id,
    )
    db.add(field)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="custom_field", entity_id=field.id, entity_label=f"{payload.object_type}.{field.label}")
    await db.commit()
    return field_out(field)


async def _field(db: AsyncSession, principal: Principal, field_id: uuid.UUID) -> CustomField:
    field = await db.get(CustomField, field_id)
    if field is None or field.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Field not found.")
    return field


@router.patch("/custom-fields/{field_id}")
async def update_field(
    field_id: uuid.UUID,
    payload: FieldPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    field = await _field(db, principal, field_id)
    if payload.field_type != field.field_type:
        raise HTTPException(status.HTTP_409_CONFLICT, "A field's type can't change. Create a new field instead.")
    if payload.is_encrypted != field.is_encrypted:
        raise HTTPException(status.HTTP_409_CONFLICT, "Encryption is chosen when a field is created.")
    shared = await _picklist_options(db, principal, payload)
    if shared is not None:
        payload.options = shared
    field.picklist_id = payload.picklist_id
    await validate_definition(
        db,
        principal.organization_id,
        field.object_type,
        field_type=field.field_type,
        options=payload.options,
        config=payload.config,
        is_encrypted=field.is_encrypted,
        has_feature=lambda feature: True if feature == "extended_fields" and field.field_type in EXTENDED_TYPES else principal.has(feature),
    )
    field.label = payload.label.strip()
    field.required = payload.required and field.field_type not in {"formula", "auto_number"}
    field.options = [o.strip() for o in payload.options if o.strip()]
    field.help_text = payload.help_text
    field.step = payload.step if principal.has("sequential_forms") else field.step
    field.is_active = payload.is_active
    next_number = (field.config or {}).get("next")
    field.config = {**payload.config, **({"next": next_number} if next_number else {})}
    await db.commit()
    return field_out(field)


class ReorderPayload(BaseModel):
    field_ids: list[uuid.UUID]


@router.post("/custom-fields/reorder")
async def reorder_fields(
    payload: ReorderPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    for position, field_id in enumerate(payload.field_ids):
        field = await _field(db, principal, field_id)
        field.position = position
    await db.commit()
    return {"message": "Order saved."}


@router.delete("/custom-fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    field_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    field = await _field(db, principal, field_id)
    record_audit(db, organization_id=principal.organization_id, action="delete", entity_type="custom_field", entity_id=field.id, entity_label=f"{field.object_type}.{field.label}")
    await db.delete(field)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Custom field values on standard records
# ===========================================================================


async def _standard_record(db: AsyncSession, principal: Principal, object_type: str, record_id: uuid.UUID):
    model = STANDARD_OBJECTS.get(object_type)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown record type.")
    record = await db.get(model, record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    return record


async def values_out(db: AsyncSession, principal: Principal, fields: list[CustomField], data: dict, record: Any = None) -> dict:
    values = present(fields, data, record=record, can_view_encrypted=principal.can("data.view_encrypted"))
    return {
        "fields": [field_out(f) for f in fields],
        "values": values,
        "labels": await lookup_labels(db, principal.organization_id, fields, values),
    }


@router.get("/custom-fields/values/{object_type}/{record_id}")
async def get_values(
    object_type: str,
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    record = await _standard_record(db, principal, object_type, record_id)
    fields = await fields_for(db, principal.organization_id, object_type)
    return await values_out(db, principal, fields, record.custom_fields or {}, record)


class ValuesPayload(BaseModel):
    values: dict[str, Any]


@router.put("/custom-fields/values/{object_type}/{record_id}")
async def set_values(
    object_type: str,
    record_id: uuid.UUID,
    payload: ValuesPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    record = await _standard_record(db, principal, object_type, record_id)
    fields = await fields_for(db, principal.organization_id, object_type)
    try:
        data = apply_values(fields, record.custom_fields or {}, payload.values, creating=False)
    except FieldValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    # Keep internal keys (attribution, playbook progress) that aren't custom fields.
    record.custom_fields = {**{k: v for k, v in (record.custom_fields or {}).items() if k.startswith("_")}, **data}
    await db.commit()
    return await values_out(db, principal, fields, record.custom_fields, record)


# ===========================================================================
# Custom records
# ===========================================================================


class RecordPayload(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    values: dict[str, Any] = Field(default_factory=dict)
    owner_user_id: uuid.UUID | None = None


async def record_out(db: AsyncSession, principal: Principal, module: CustomModule, record: CustomRecord, fields: list[CustomField]) -> dict:
    owners = await names_for(db, principal.organization_id, "user", [record.owner_user_id])
    return {
        "id": str(record.id),
        "module_key": module.key,
        "name": record.name,
        "owner_user_id": str(record.owner_user_id) if record.owner_user_id else None,
        "owner_name": owners.get(str(record.owner_user_id)),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        **(await values_out(db, principal, fields, record.data or {}, None)),
    }


@router.get("/modules/{module_key}/records")
async def list_records(
    module_key: str,
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    module = await _module_by_key(db, principal, module_key)
    fields = await fields_for(db, principal.organization_id, module.key)
    query = select(CustomRecord).where(CustomRecord.module_id == module.id)
    if q:
        query = query.where(CustomRecord.name.ilike(f"%{q.strip()}%"))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(query.order_by(CustomRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    owners = await names_for(db, principal.organization_id, "user", [r.owner_user_id for r in rows])
    can_view = principal.can("data.view_encrypted")
    items = []
    for record in rows:
        values = present(fields, record.data or {}, can_view_encrypted=can_view)
        items.append(
            {
                "id": str(record.id),
                "name": record.name,
                "owner_name": owners.get(str(record.owner_user_id)),
                "values": values,
                "labels": await lookup_labels(db, principal.organization_id, fields, values),
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }
        )
    return {
        "module": await module_out(db, module),
        "fields": [field_out(f) for f in fields],
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/modules/{module_key}/records", status_code=status.HTTP_201_CREATED)
async def create_record(
    module_key: str,
    payload: RecordPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    module = await _module_by_key(db, principal, module_key)
    if not module.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{module.name} is turned off.")
    fields = await fields_for(db, principal.organization_id, module.key)
    try:
        data = apply_values(fields, {}, payload.values, creating=True)
    except FieldValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    record = CustomRecord(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        module_id=module.id,
        name=payload.name.strip(),
        data=data,
        owner_user_id=payload.owner_user_id or principal.user_id,
        created_by_user_id=principal.user_id,
    )
    db.add(record)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type=module.key, entity_id=record.id, entity_label=record.name)
    await db.commit()
    return await record_out(db, principal, module, record, fields)


async def _record(db: AsyncSession, principal: Principal, module: CustomModule, record_id: uuid.UUID) -> CustomRecord:
    record = await db.get(CustomRecord, record_id)
    if record is None or record.module_id != module.id or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    return record


@router.get("/modules/{module_key}/records/{record_id}")
async def get_record(
    module_key: str,
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    module = await _module_by_key(db, principal, module_key)
    record = await _record(db, principal, module, record_id)
    fields = await fields_for(db, principal.organization_id, module.key)
    return {"module": await module_out(db, module), **(await record_out(db, principal, module, record, fields))}


@router.patch("/modules/{module_key}/records/{record_id}")
async def update_record(
    module_key: str,
    record_id: uuid.UUID,
    payload: RecordPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    module = await _module_by_key(db, principal, module_key)
    record = await _record(db, principal, module, record_id)
    fields = await fields_for(db, principal.organization_id, module.key)
    try:
        data = apply_values(fields, record.data or {}, payload.values, creating=False)
    except FieldValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    before = {"name": record.name}
    record.name = payload.name.strip()
    record.data = data
    if payload.owner_user_id:
        record.owner_user_id = payload.owner_user_id
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="update",
        entity_type=module.key,
        entity_id=record.id,
        entity_label=record.name,
        changes={"name": {"from": before["name"], "to": record.name}} if before["name"] != record.name else {},
    )
    await db.commit()
    return await record_out(db, principal, module, record, fields)


@router.delete("/modules/{module_key}/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    module_key: str,
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.delete")),
    db: AsyncSession = Depends(get_db),
):
    module = await _module_by_key(db, principal, module_key)
    record = await _record(db, principal, module, record_id)
    record_audit(db, organization_id=principal.organization_id, action="delete", entity_type=module.key, entity_id=record.id, entity_label=record.name)
    await db.delete(record)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
