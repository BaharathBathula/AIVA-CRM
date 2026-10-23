"""Process management, approvals, custom buttons, teams and hierarchy, sandboxes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote as url_quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_feature, require_permission
from app.core.crypto import decrypt, encrypt, hmac_sha256, new_token
from app.db.session import get_db
from app.models.editions import (
    ApprovalRequest,
    ApprovalRule,
    CustomButton,
    CustomModule,
    CustomRecord,
    StageRule,
    TeamMember,
)
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.opportunity_stage_history import OpportunityStageHistory
from app.models.organization import Organization
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Notification, Team
from app.models.task import Task
from app.models.user import User
from app.services import approvals as approval_service
from app.services import sandbox as sandbox_service
from app.services.automation import OPERATORS, invalidate_automation_cache, jsonable, record_audit
from app.services.catalog import OBJECTS
from app.services.custom_fields import STANDARD_OBJECTS, apply_values, fields_for, present
from app.services.expressions import ExpressionError, evaluate, parse
from app.services.lookups import names_for
from app.services.visibility import MODES, subordinate_ids

router = APIRouter(tags=["Enterprise"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Process management (stage playbooks)
# ===========================================================================

OPPORTUNITY_REQUIRABLE = (
    ("amount", "Amount"),
    ("expected_close_date", "Close date"),
    ("next_step", "Next step"),
    ("primary_contact_id", "Primary contact"),
    ("description", "Description"),
    ("competitor", "Competitor"),
    ("lead_source", "Lead source"),
    ("loss_reason", "Loss reason"),
)


class StageRulePayload(BaseModel):
    required_fields: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    exit_requires_checklist: bool = False
    guidance: str | None = Field(default=None, max_length=4000)


async def _requirable(db: AsyncSession, organization_id: uuid.UUID) -> list[dict[str, str]]:
    custom = await fields_for(db, organization_id, "opportunity")
    return [{"key": key, "label": label} for key, label in OPPORTUNITY_REQUIRABLE] + [
        {"key": f"cf.{f.key}", "label": f.label} for f in custom if f.field_type not in {"formula", "auto_number"}
    ]


@router.get("/process/stage-rules", dependencies=[Depends(require_feature("process_management"))])
async def list_stage_rules(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    pipelines = (await db.execute(select(Pipeline).where(Pipeline.organization_id == org).order_by(Pipeline.name))).scalars().all()
    stages = (await db.execute(select(PipelineStage).where(PipelineStage.organization_id == org).order_by(PipelineStage.position))).scalars().all()
    rules = {r.stage_id: r for r in (await db.execute(select(StageRule).where(StageRule.organization_id == org))).scalars()}
    return {
        "fields": await _requirable(db, org),
        "pipelines": [
            {
                "id": str(p.id),
                "name": p.name,
                "stages": [
                    {
                        "id": str(s.id),
                        "name": s.name,
                        "category": s.category,
                        "is_active": s.is_active,
                        "rule": {
                            "required_fields": rules[s.id].required_fields,
                            "checklist": rules[s.id].checklist,
                            "exit_requires_checklist": rules[s.id].exit_requires_checklist,
                            "guidance": rules[s.id].guidance,
                        }
                        if s.id in rules
                        else None,
                    }
                    for s in stages
                    if s.pipeline_id == p.id
                ],
            }
            for p in pipelines
        ],
    }


@router.put("/process/stage-rules/{stage_id}", dependencies=[Depends(require_feature("process_management"))])
async def save_stage_rule(
    stage_id: uuid.UUID,
    payload: StageRulePayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    stage = await db.get(PipelineStage, stage_id)
    if stage is None or stage.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stage not found.")
    allowed = {f["key"] for f in await _requirable(db, principal.organization_id)}
    bad = [key for key in payload.required_fields if key not in allowed]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{bad[0]}' can't be required.")
    checklist = [item.strip()[:200] for item in payload.checklist if item.strip()]
    if len(checklist) > 20:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Keep checklists to 20 items.")
    rule = (await db.execute(select(StageRule).where(StageRule.stage_id == stage_id))).scalar_one_or_none()
    if rule is None:
        rule = StageRule(id=uuid.uuid4(), organization_id=principal.organization_id, stage_id=stage_id)
        db.add(rule)
    rule.required_fields = list(dict.fromkeys(payload.required_fields))
    rule.checklist = checklist
    rule.exit_requires_checklist = payload.exit_requires_checklist and bool(checklist)
    rule.guidance = payload.guidance
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return {"message": f"Playbook for {stage.name} saved."}


@router.delete("/process/stage-rules/{stage_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stage_rule(
    stage_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    rule = (
        await db.execute(select(StageRule).where(StageRule.stage_id == stage_id, StageRule.organization_id == principal.organization_id))
    ).scalar_one_or_none()
    if rule is not None:
        await db.delete(rule)
        await db.commit()
        invalidate_automation_cache(principal.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _value(opp: Opportunity, key: str) -> Any:
    if key.startswith("cf."):
        return (opp.custom_fields or {}).get(key[3:])
    return getattr(opp, key, None)


@router.get("/process/opportunities/{opportunity_id}", dependencies=[Depends(require_feature("process_management"))])
async def opportunity_playbook(
    opportunity_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    opp = await db.get(Opportunity, opportunity_id)
    if opp is None or opp.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found.")
    stages = (
        await db.execute(select(PipelineStage).where(PipelineStage.pipeline_id == opp.pipeline_id).order_by(PipelineStage.position))
    ).scalars().all()
    rules = {r.stage_id: r for r in (await db.execute(select(StageRule).where(StageRule.stage_id.in_([s.id for s in stages] or [uuid.uuid4()])))).scalars()}
    labels = {f["key"]: f["label"] for f in await _requirable(db, principal.organization_id)}
    done = ((opp.custom_fields or {}).get("_playbook") or {})
    current = rules.get(opp.stage_id)
    return {
        "stage_id": str(opp.stage_id),
        "current": {
            "guidance": current.guidance if current else None,
            "exit_requires_checklist": current.exit_requires_checklist if current else False,
            "checklist": [{"item": item, "done": item in (done.get(str(opp.stage_id)) or [])} for item in (current.checklist if current else [])],
        },
        "stages": [
            {
                "id": str(stage.id),
                "name": stage.name,
                "category": stage.category,
                "is_current": stage.id == opp.stage_id,
                "missing": [
                    {"key": key, "label": labels.get(key, key)}
                    for key in (rules[stage.id].required_fields if stage.id in rules else [])
                    if _value(opp, key) in (None, "", [])
                ],
                "has_rule": stage.id in rules,
            }
            for stage in stages
            if stage.is_active
        ],
    }


class ChecklistToggle(BaseModel):
    item: str = Field(min_length=1, max_length=200)
    done: bool


@router.post("/process/opportunities/{opportunity_id}/checklist", dependencies=[Depends(require_feature("process_management"))])
async def toggle_checklist(
    opportunity_id: uuid.UUID,
    payload: ChecklistToggle,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    opp = await db.get(Opportunity, opportunity_id)
    if opp is None or opp.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found.")
    playbook = dict((opp.custom_fields or {}).get("_playbook") or {})
    items = set(playbook.get(str(opp.stage_id)) or [])
    if payload.done:
        items.add(payload.item)
    else:
        items.discard(payload.item)
    playbook[str(opp.stage_id)] = sorted(items)
    opp.custom_fields = {**(opp.custom_fields or {}), "_playbook": playbook}
    await db.commit()
    return await opportunity_playbook(opportunity_id, principal, db)


# ===========================================================================
# Approvals
# ===========================================================================


class ApprovalRulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    object_type: str = Field(pattern="^(opportunity|quote)$")
    trigger: str = Field(pattern="^(stage_change|quote_send)$")
    target_stage_id: uuid.UUID | None = None
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    approver_type: str = Field(default="role", pattern="^(user|role|manager)$")
    approver_value: str | None = Field(default=None, max_length=80)
    lock_record: bool = True
    is_active: bool = True


QUOTE_CONDITION_FIELDS = ("total", "subtotal", "discount_total", "discount_percent", "tax_total", "line_count", "currency")


async def _validate_rule(db: AsyncSession, principal: Principal, payload: ApprovalRulePayload) -> None:
    if payload.object_type == "opportunity" and payload.trigger != "stage_change":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Deal approvals run when a deal changes stage.")
    if payload.object_type == "quote" and payload.trigger != "quote_send":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Quote approvals run when a quote is sent.")
    if payload.object_type == "opportunity":
        if payload.target_stage_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose the stage that needs approval.")
        stage = await db.get(PipelineStage, payload.target_stage_id)
        if stage is None or stage.organization_id != principal.organization_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That stage doesn't exist.")
    definition = OBJECTS["opportunity"]
    for condition in payload.conditions:
        field = condition.get("field") or ""
        if condition.get("operator", "equals") not in OPERATORS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown condition.")
        if payload.object_type == "quote" and field not in QUOTE_CONDITION_FIELDS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{field}' isn't a quote field you can check.")
        if payload.object_type == "opportunity":
            definition_field = definition.field(field)
            if definition_field is None or definition_field.virtual:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{field}' isn't a deal field.")
    if payload.approver_type == "user":
        try:
            user_id = uuid.UUID(str(payload.approver_value))
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose who approves.") from exc
        member = (
            await db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == principal.organization_id, OrganizationMembership.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The approver must be a workspace member.")
    if payload.approver_type == "role" and not payload.approver_value:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose which role approves.")


async def approval_rule_out(db: AsyncSession, rule: ApprovalRule) -> dict[str, Any]:
    stage_names = await names_for(db, rule.organization_id, "stage", [rule.target_stage_id])
    approver_label = rule.approver_value
    if rule.approver_type == "user" and rule.approver_value:
        approver_label = (await names_for(db, rule.organization_id, "user", [rule.approver_value])).get(rule.approver_value)
    elif rule.approver_type == "manager":
        approver_label = "Requester's manager"
    return {
        "id": str(rule.id),
        "name": rule.name,
        "object_type": rule.object_type,
        "trigger": rule.trigger,
        "target_stage_id": str(rule.target_stage_id) if rule.target_stage_id else None,
        "target_stage_name": stage_names.get(str(rule.target_stage_id)),
        "conditions": rule.conditions,
        "approver_type": rule.approver_type,
        "approver_value": rule.approver_value,
        "approver_label": approver_label,
        "lock_record": rule.lock_record,
        "is_active": rule.is_active,
    }


@router.get("/approval-rules", dependencies=[Depends(require_feature("approvals"))])
async def list_approval_rules(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    rules = (
        await db.execute(select(ApprovalRule).where(ApprovalRule.organization_id == principal.organization_id).order_by(ApprovalRule.name))
    ).scalars().all()
    return {"items": [await approval_rule_out(db, r) for r in rules], "quote_fields": list(QUOTE_CONDITION_FIELDS)}


@router.post("/approval-rules", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("approvals"))])
async def create_approval_rule(
    payload: ApprovalRulePayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    await _validate_rule(db, principal, payload)
    rule = ApprovalRule(id=uuid.uuid4(), organization_id=principal.organization_id, **payload.model_dump())
    db.add(rule)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return await approval_rule_out(db, rule)


async def _approval_rule(db: AsyncSession, principal: Principal, rule_id: uuid.UUID) -> ApprovalRule:
    rule = await db.get(ApprovalRule, rule_id)
    if rule is None or rule.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval rule not found.")
    return rule


@router.put("/approval-rules/{rule_id}", dependencies=[Depends(require_feature("approvals"))])
async def update_approval_rule(
    rule_id: uuid.UUID,
    payload: ApprovalRulePayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    rule = await _approval_rule(db, principal, rule_id)
    await _validate_rule(db, principal, payload)
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return await approval_rule_out(db, rule)


@router.delete("/approval-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_approval_rule(
    rule_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    rule = await _approval_rule(db, principal, rule_id)
    await db.delete(rule)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ApprovalSubmit(BaseModel):
    object_type: str = Field(pattern="^(opportunity|quote)$")
    record_id: uuid.UUID
    rule_id: uuid.UUID
    requested_change: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=2000)


async def approval_out(db: AsyncSession, request: ApprovalRequest, principal: Principal) -> dict[str, Any]:
    users = await names_for(db, request.organization_id, "user", [request.requested_by_user_id, request.approver_user_id, request.decided_by_user_id])
    return {
        "id": str(request.id),
        "rule_name": request.rule_name,
        "object_type": request.object_type,
        "record_id": str(request.record_id),
        "record_label": request.record_label,
        "link": f"/opportunities/{request.record_id}" if request.object_type == "opportunity" else f"/quotes/{request.record_id}",
        "requested_change": request.requested_change,
        "status": request.status,
        "requested_by": users.get(str(request.requested_by_user_id)),
        "requested_by_user_id": str(request.requested_by_user_id) if request.requested_by_user_id else None,
        "approver": users.get(str(request.approver_user_id)) or (f"Any {request.approver_role.replace('_', ' ')}" if request.approver_role else None),
        "decided_by": users.get(str(request.decided_by_user_id)),
        "decided_at": request.decided_at,
        "request_comment": request.request_comment,
        "decision_comment": request.decision_comment,
        "can_decide": approval_service.can_decide(request, principal.user_id, principal.role),
        "created_at": request.created_at,
    }


@router.post("/approvals", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("approvals"))])
async def submit_for_approval(
    payload: ApprovalSubmit,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    request = await approval_service.submit(
        db,
        organization_id=principal.organization_id,
        requester=principal.user_id,
        object_type=payload.object_type,
        record_id=payload.record_id,
        rule_id=payload.rule_id,
        requested_change=payload.requested_change,
        comment=payload.comment,
    )
    record_audit(db, organization_id=principal.organization_id, action="submit", entity_type="approval", entity_id=request.id, entity_label=request.record_label)
    await db.commit()
    return await approval_out(db, request, principal)


@router.get("/approvals", dependencies=[Depends(require_feature("approvals"))])
async def list_approvals(
    view: str = Query(default="inbox", pattern="^(inbox|mine|history)$"),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(ApprovalRequest).where(ApprovalRequest.organization_id == principal.organization_id)
    if view == "mine":
        query = query.where(ApprovalRequest.requested_by_user_id == principal.user_id)
    elif view == "history":
        query = query.where(ApprovalRequest.status != "pending")
    else:
        query = query.where(ApprovalRequest.status == "pending")
    rows = (await db.execute(query.order_by(ApprovalRequest.created_at.desc()).limit(200))).scalars().all()
    items = [await approval_out(db, r, principal) for r in rows]
    if view == "inbox":
        items = [item for item in items if item["can_decide"]]
    pending = (
        await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.organization_id == principal.organization_id, ApprovalRequest.status == "pending")
        )
    ).scalars().all()
    return {
        "items": items,
        "counts": {
            "inbox": len([r for r in pending if approval_service.can_decide(r, principal.user_id, principal.role)]),
            "mine": len([r for r in pending if r.requested_by_user_id == principal.user_id]),
        },
    }


@router.get("/approvals/record/{record_id}", dependencies=[Depends(require_feature("approvals"))])
async def record_approvals(
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.organization_id == principal.organization_id, ApprovalRequest.record_id == record_id)
            .order_by(ApprovalRequest.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    return [await approval_out(db, r, principal) for r in rows]


class Decision(BaseModel):
    approve: bool
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/approvals/{request_id}/decide", dependencies=[Depends(require_feature("approvals"))])
async def decide_approval(
    request_id: uuid.UUID,
    payload: Decision,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    request = await db.get(ApprovalRequest, request_id)
    if request is None or request.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found.")
    message = await approval_service.decide(db, request, user_id=principal.user_id, role=principal.role, approve=payload.approve, comment=payload.comment)
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="approve" if payload.approve else "reject",
        entity_type="approval",
        entity_id=request.id,
        entity_label=request.record_label,
        changes={"comment": payload.comment} if payload.comment else {},
    )
    await db.commit()
    return {"message": message, "request": await approval_out(db, request, principal)}


@router.post("/approvals/{request_id}/cancel", dependencies=[Depends(require_feature("approvals"))])
async def cancel_approval(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    request = await db.get(ApprovalRequest, request_id)
    if request is None or request.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found.")
    if request.status != "pending" or request.requested_by_user_id != principal.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only the person who asked can withdraw a pending request.")
    request.status = "cancelled"
    request.decided_at = utcnow()
    invalidate_automation_cache(principal.organization_id)
    await db.commit()
    return await approval_out(db, request, principal)


# ===========================================================================
# Custom buttons and functions
# ===========================================================================

BUTTON_OBJECTS = ("lead", "contact", "account", "opportunity", "case")


class ButtonPayload(BaseModel):
    object_type: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    action_type: str = Field(pattern="^(open_url|webhook|function)$")
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


async def _button_object(db: AsyncSession, principal: Principal, object_type: str) -> None:
    if object_type in BUTTON_OBJECTS:
        return
    module = (
        await db.execute(select(CustomModule).where(CustomModule.organization_id == principal.organization_id, CustomModule.key == object_type))
    ).scalar_one_or_none()
    if module is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose where the button appears.")


def _validate_button(payload: ButtonPayload, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(payload.config)
    if payload.action_type == "open_url":
        url = str(config.get("url") or "").strip()
        if not url.startswith(("https://", "http://", "mailto:", "tel:")):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The link must start with https://, mailto: or tel:.")
        return {"url": url, "new_tab": bool(config.get("new_tab", True))}
    if payload.action_type == "webhook":
        url = str(config.get("url") or "").strip()
        if not url.startswith("https://"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Webhooks must use https://.")
        secret = (existing or {}).get("secret") or encrypt(new_token(nbytes=24))
        return {"url": url, "secret": secret, "confirm": bool(config.get("confirm"))}
    steps = config.get("steps") or []
    if not steps or len(steps) > 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add between 1 and 10 steps.")
    cleaned = []
    for index, step in enumerate(steps, start=1):
        kind = step.get("type")
        if kind == "set_field":
            if not step.get("field"):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: choose a field.")
            try:
                parse(str(step.get("expression") or ""))
            except ExpressionError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: {exc}") from exc
            cleaned.append({"type": kind, "field": step["field"], "expression": step["expression"]})
        elif kind == "create_task":
            if not str(step.get("title") or "").strip():
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: give the task a title.")
            cleaned.append({"type": kind, "title": step["title"], "due_in_days": int(step.get("due_in_days") or 1)})
        elif kind == "notify":
            cleaned.append({"type": kind, "message": str(step.get("message") or "")[:300]})
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: unknown step.")
    return {"steps": cleaned, "confirm": bool(config.get("confirm"))}


def button_out(button: CustomButton, *, include_secret: bool = False) -> dict[str, Any]:
    config = dict(button.config or {})
    secret = config.pop("secret", None)
    if include_secret and secret:
        config["secret"] = decrypt(secret)
    return {
        "id": str(button.id),
        "object_type": button.object_type,
        "label": button.label,
        "description": button.description,
        "action_type": button.action_type,
        "config": config,
        "has_secret": bool(secret),
        "is_active": button.is_active,
        "run_count": button.run_count,
        "last_run_at": button.last_run_at,
    }


@router.get("/buttons")
async def list_buttons(
    object_type: str | None = None,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    if not principal.has("custom_buttons"):
        return []
    query = select(CustomButton).where(CustomButton.organization_id == principal.organization_id)
    if object_type:
        query = query.where(CustomButton.object_type == object_type, CustomButton.is_active.is_(True))
    buttons = (await db.execute(query.order_by(CustomButton.object_type, CustomButton.position, CustomButton.label))).scalars().all()
    return [button_out(b) for b in buttons]


@router.post("/buttons", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("custom_buttons"))])
async def create_button(
    payload: ButtonPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    await _button_object(db, principal, payload.object_type)
    button = CustomButton(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        object_type=payload.object_type,
        label=payload.label.strip(),
        description=payload.description,
        action_type=payload.action_type,
        config=_validate_button(payload),
        is_active=payload.is_active,
    )
    db.add(button)
    await db.commit()
    return button_out(button, include_secret=True)


async def _button(db: AsyncSession, principal: Principal, button_id: uuid.UUID) -> CustomButton:
    button = await db.get(CustomButton, button_id)
    if button is None or button.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Button not found.")
    return button


@router.put("/buttons/{button_id}", dependencies=[Depends(require_feature("custom_buttons"))])
async def update_button(
    button_id: uuid.UUID,
    payload: ButtonPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    button = await _button(db, principal, button_id)
    await _button_object(db, principal, payload.object_type)
    button.object_type = payload.object_type
    button.label = payload.label.strip()
    button.description = payload.description
    button.config = _validate_button(payload, button.config if button.action_type == payload.action_type else None)
    button.action_type = payload.action_type
    button.is_active = payload.is_active
    await db.commit()
    return button_out(button, include_secret=True)


@router.delete("/buttons/{button_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_button(
    button_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    button = await _button(db, principal, button_id)
    await db.delete(button)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ButtonRun(BaseModel):
    record_id: uuid.UUID


async def _button_record(db: AsyncSession, principal: Principal, object_type: str, record_id: uuid.UUID) -> tuple[Any, dict[str, Any]]:
    if object_type in STANDARD_OBJECTS:
        record = await db.get(STANDARD_OBJECTS[object_type], record_id)
        if record is None or record.organization_id != principal.organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
        fields = await fields_for(db, principal.organization_id, object_type)
        values = {column.key: getattr(record, column.key) for column in record.__table__.columns if column.key != "custom_fields"}
        values.update({f"cf_{k}": v for k, v in present(fields, record.custom_fields or {}, record=record).items()})
        return record, values
    record = await db.get(CustomRecord, record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    module = await db.get(CustomModule, record.module_id)
    fields = await fields_for(db, principal.organization_id, module.key)
    values = {"id": record.id, "name": record.name, "owner_user_id": record.owner_user_id, "created_at": record.created_at}
    values.update({f"cf_{k}": v for k, v in present(fields, record.data or {}).items()})
    return record, values


def _fill(template: str, values: dict[str, Any], encode: bool) -> str:
    import re

    def replace(match):
        value = values.get(match.group(1).replace("cf.", "cf_"))
        text = "" if value is None else str(jsonable(value))
        return url_quote(text, safe="") if encode else text

    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}", replace, template)


@router.post("/buttons/{button_id}/run", dependencies=[Depends(require_feature("custom_buttons"))])
async def run_button(
    button_id: uuid.UUID,
    payload: ButtonRun,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    button = await _button(db, principal, button_id)
    if not button.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This button is turned off.")
    record, values = await _button_record(db, principal, button.object_type, payload.record_id)
    config = button.config or {}
    result: dict[str, Any] = {"open_url": None, "message": "Done."}

    if button.action_type == "open_url":
        url = _fill(config.get("url", ""), values, encode=True)
        result = {"open_url": url, "new_tab": config.get("new_tab", True), "message": "Opening link…"}
    elif button.action_type == "webhook":
        body = json.dumps(
            {
                "event": "button.clicked",
                "button": {"id": str(button.id), "label": button.label},
                "record_type": button.object_type,
                "record": jsonable(values),
                "user": {"id": str(principal.user_id), "email": principal.email, "name": principal.full_name},
                "organization_id": str(principal.organization_id),
                "sent_at": utcnow().isoformat(),
            },
            default=str,
        ).encode()
        signature = hmac_sha256(decrypt(config.get("secret")) or "", body)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    config["url"], content=body, headers={"Content-Type": "application/json", "X-AIVA-Signature": f"sha256={signature}"}
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The webhook didn't answer: {exc.__class__.__name__}.") from exc
        if response.status_code >= 400:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The webhook answered {response.status_code}.")
        result = {"open_url": None, "message": f"Sent to webhook ({response.status_code})."}
    else:
        messages = []
        names = {k.replace("cf_", "", 1) if k.startswith("cf_") else k: v for k, v in values.items()}
        for step in config.get("steps") or []:
            if step["type"] == "set_field":
                value = evaluate(step["expression"], names)
                field = step["field"]
                if field.startswith("cf."):
                    object_key = button.object_type
                    fields = await fields_for(db, principal.organization_id, object_key)
                    current = (record.custom_fields if hasattr(record, "custom_fields") else record.data) or {}
                    updated = apply_values(fields, current, {field[3:]: value}, creating=False)
                    if hasattr(record, "custom_fields"):
                        record.custom_fields = {**{k: v for k, v in current.items() if k.startswith("_")}, **updated}
                    else:
                        record.data = updated
                else:
                    definition = OBJECTS.get(button.object_type)
                    target = definition.field(field) if definition else None
                    if target is None or not target.updatable:
                        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{field}' can't be set by a button.")
                    setattr(record, field, value)
                names[field.replace("cf.", "")] = value
                messages.append(f"Set {field.replace('cf.', '')}")
            elif step["type"] == "create_task":
                task = Task(
                    id=uuid.uuid4(),
                    organization_id=principal.organization_id,
                    title=_fill(step["title"], values, encode=False)[:250],
                    task_type="follow_up",
                    status="open",
                    priority="medium",
                    source="button",
                    due_at=utcnow() + timedelta(days=int(step.get("due_in_days") or 1)),
                    assigned_to_user_id=principal.user_id,
                    created_by_user_id=principal.user_id,
                    is_recurring=False,
                    is_ai_generated=False,
                    tags=[],
                    task_metadata={"button_id": str(button.id), "record_id": str(payload.record_id)},
                    lead_id=payload.record_id if button.object_type == "lead" else None,
                    contact_id=payload.record_id if button.object_type == "contact" else None,
                    account_id=payload.record_id if button.object_type == "account" else getattr(record, "account_id", None),
                    opportunity_id=payload.record_id if button.object_type == "opportunity" else None,
                )
                db.add(task)
                messages.append("Created a task")
            elif step["type"] == "notify":
                owner = getattr(record, "owner_user_id", None) or getattr(record, "assigned_to_user_id", None) or principal.user_id
                db.add(
                    Notification(
                        id=uuid.uuid4(),
                        organization_id=principal.organization_id,
                        user_id=owner,
                        kind="button",
                        title=f"{button.label}: {_fill(step.get('message') or '', values, encode=False)}"[:200],
                        body=f"Run by {principal.full_name}.",
                        link=None,
                        is_read=False,
                    )
                )
                messages.append("Notified the owner")
        result = {"open_url": None, "message": "; ".join(messages) or "Done."}

    button.run_count = (button.run_count or 0) + 1
    button.last_run_at = utcnow()
    record_audit(db, organization_id=principal.organization_id, action="button", entity_type=button.object_type, entity_id=payload.record_id, entity_label=button.label)
    await db.commit()
    return result


# ===========================================================================
# Teams and reporting hierarchy
# ===========================================================================


@router.get("/org/hierarchy")
async def hierarchy(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    organization = await db.get(Organization, org)
    rows = (
        await db.execute(
            select(User, OrganizationMembership)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == org, OrganizationMembership.status == "active")
            .order_by(User.full_name)
        )
    ).all()
    teams = (await db.execute(select(Team).where(Team.organization_id == org).order_by(Team.name))).scalars().all()
    extra = (await db.execute(select(TeamMember).where(TeamMember.organization_id == org))).scalars().all()
    members_by_team: dict[uuid.UUID, list[dict[str, Any]]] = {}
    names = {user.id: user.full_name for user, _ in rows}
    for user, membership in rows:
        if membership.team_id:
            members_by_team.setdefault(membership.team_id, []).append({"user_id": str(user.id), "name": user.full_name, "role": "member", "primary": True})
    for row in extra:
        if row.user_id in names:
            members_by_team.setdefault(row.team_id, []).append({"user_id": str(row.user_id), "name": names[row.user_id], "role": row.role, "primary": False})
    return {
        "visibility": (organization.settings or {}).get("record_visibility", "all"),
        "features": {"multi_team": principal.has("multi_team"), "reporting_hierarchy": principal.has("reporting_hierarchy")},
        "members": [
            {
                "user_id": str(user.id),
                "name": user.full_name,
                "email": user.email,
                "job_title": user.job_title,
                "role": membership.role,
                "team_id": str(membership.team_id) if membership.team_id else None,
                "reports_to_user_id": str(membership.reports_to_user_id) if membership.reports_to_user_id else None,
                "reports_to_name": names.get(membership.reports_to_user_id),
            }
            for user, membership in rows
        ],
        "teams": [
            {
                "id": str(team.id),
                "name": team.name,
                "description": team.description,
                "parent_team_id": str(team.parent_team_id) if team.parent_team_id else None,
                "manager_user_id": str(team.manager_user_id) if team.manager_user_id else None,
                "manager_name": names.get(team.manager_user_id),
                "members": members_by_team.get(team.id, []),
            }
            for team in teams
        ],
    }


class ManagerPayload(BaseModel):
    reports_to_user_id: uuid.UUID | None = None


@router.put("/org/members/{user_id}/manager", dependencies=[Depends(require_feature("reporting_hierarchy"))])
async def set_manager(
    user_id: uuid.UUID,
    payload: ManagerPayload,
    principal: Principal = Depends(require_permission("admin.users")),
    db: AsyncSession = Depends(get_db),
):
    membership = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == principal.organization_id, OrganizationMembership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found.")
    if payload.reports_to_user_id:
        if payload.reports_to_user_id == user_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "People can't report to themselves.")
        if payload.reports_to_user_id in await subordinate_ids(db, principal.organization_id, user_id):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That would create a loop: the manager already reports to this person.")
    membership.reports_to_user_id = payload.reports_to_user_id
    await db.commit()
    return {"message": "Saved."}


class ParentPayload(BaseModel):
    parent_team_id: uuid.UUID | None = None


@router.put("/org/teams/{team_id}/parent", dependencies=[Depends(require_feature("multi_team"))])
async def set_team_parent(
    team_id: uuid.UUID,
    payload: ParentPayload,
    principal: Principal = Depends(require_permission("admin.teams")),
    db: AsyncSession = Depends(get_db),
):
    team = await db.get(Team, team_id)
    if team is None or team.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found.")
    if payload.parent_team_id:
        cursor = await db.get(Team, payload.parent_team_id)
        if cursor is None or cursor.organization_id != principal.organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent team not found.")
        while cursor is not None:
            if cursor.id == team.id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A team can't sit under one of its own sub-teams.")
            cursor = await db.get(Team, cursor.parent_team_id) if cursor.parent_team_id else None
    team.parent_team_id = payload.parent_team_id
    await db.commit()
    return {"message": "Saved."}


class TeamMemberPayload(BaseModel):
    user_id: uuid.UUID
    role: str = Field(default="member", pattern="^(member|lead)$")


@router.post("/org/teams/{team_id}/members", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("multi_team"))])
async def add_team_member(
    team_id: uuid.UUID,
    payload: TeamMemberPayload,
    principal: Principal = Depends(require_permission("admin.teams")),
    db: AsyncSession = Depends(get_db),
):
    team = await db.get(Team, team_id)
    if team is None or team.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found.")
    member = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == principal.organization_id, OrganizationMembership.user_id == payload.user_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found.")
    existing = (await db.execute(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == payload.user_id))).scalar_one_or_none()
    if existing is not None:
        existing.role = payload.role
    else:
        db.add(TeamMember(id=uuid.uuid4(), organization_id=principal.organization_id, team_id=team_id, user_id=payload.user_id, role=payload.role))
    await db.commit()
    return {"message": "Added."}


@router.delete("/org/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.teams")),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id, TeamMember.organization_id == principal.organization_id)
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class VisibilityPayload(BaseModel):
    mode: str


@router.put("/org/visibility")
async def set_visibility(
    payload: VisibilityPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    if payload.mode not in MODES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose who can see records.")
    if payload.mode == "team" and not principal.has("multi_team"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Team-based visibility is part of the Enterprise plan.")
    if payload.mode == "hierarchy" and not principal.has("reporting_hierarchy"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Hierarchy-based visibility is part of the Enterprise plan.")
    organization = await db.get(Organization, principal.organization_id)
    previous = (organization.settings or {}).get("record_visibility", "all")
    organization.settings = {**(organization.settings or {}), "record_visibility": payload.mode}
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="update",
        entity_type="organization",
        entity_id=organization.id,
        entity_label="Record visibility",
        changes={"record_visibility": {"from": previous, "to": payload.mode}},
    )
    await db.commit()
    return {"mode": payload.mode}


# ===========================================================================
# Sandboxes
# ===========================================================================


async def sandbox_out(db: AsyncSession, sandbox: Organization) -> dict[str, Any]:
    settings = sandbox.settings or {}
    creator = await names_for(db, sandbox.id, "user", [settings.get("sandbox_created_by")])
    return {
        "id": str(sandbox.id),
        "name": sandbox.name,
        "slug": sandbox.slug,
        "created_at": settings.get("sandbox_created_at"),
        "created_by": creator.get(str(settings.get("sandbox_created_by"))),
    }


@router.get("/sandboxes", dependencies=[Depends(require_feature("sandbox"))])
async def list_sandboxes(
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    parent_id = (organization.settings or {}).get("sandbox_of")
    if parent_id:
        parent = await db.get(Organization, uuid.UUID(parent_id))
        return {
            "is_sandbox": True,
            "production": {"id": parent_id, "name": parent.name if parent else "Production"},
            "items": [],
            "deployable": [{"key": k, "label": v} for k, v in sandbox_service.DEPLOYABLE.items()],
        }
    return {
        "is_sandbox": False,
        "production": None,
        "items": [await sandbox_out(db, s) for s in await sandbox_service.sandboxes_of(db, organization.id)],
        "max": sandbox_service.MAX_SANDBOXES,
        "deployable": [{"key": k, "label": v} for k, v in sandbox_service.DEPLOYABLE.items()],
    }


class SandboxPayload(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    sample_data: bool = True


@router.post("/sandboxes", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("sandbox"))])
async def create_sandbox(
    payload: SandboxPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    sandbox, counts = await sandbox_service.create(db, organization, principal.user_id, name=payload.name, sample_data=payload.sample_data)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="sandbox", entity_id=sandbox.id, entity_label=sandbox.name, changes={"sample": counts})
    await db.commit()
    return {**(await sandbox_out(db, sandbox)), "copied": counts}


async def _sandbox(db: AsyncSession, principal: Principal, sandbox_id: uuid.UUID) -> Organization:
    sandbox = await db.get(Organization, sandbox_id)
    if sandbox is None or (sandbox.settings or {}).get("sandbox_of") != str(principal.organization_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sandbox not found.")
    return sandbox


async def _drop_sandbox(db: AsyncSession, sandbox: Organization) -> None:
    """Delete a sandbox workspace. Deals go first: their stage links block a cascading delete."""

    db.sync_session.info["automation_disabled"] = True
    await db.execute(sa_delete(OpportunityStageHistory).where(OpportunityStageHistory.organization_id == sandbox.id))
    await db.execute(sa_delete(Opportunity).where(Opportunity.organization_id == sandbox.id))
    await db.delete(sandbox)
    await db.flush()


@router.delete("/sandboxes/{sandbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sandbox(
    sandbox_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    sandbox = await _sandbox(db, principal, sandbox_id)
    record_audit(db, organization_id=principal.organization_id, action="delete", entity_type="sandbox", entity_id=sandbox.id, entity_label=sandbox.name)
    await _drop_sandbox(db, sandbox)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sandboxes/{sandbox_id}/refresh", dependencies=[Depends(require_feature("sandbox"))])
async def refresh_sandbox(
    sandbox_id: uuid.UUID,
    payload: SandboxPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    sandbox = await _sandbox(db, principal, sandbox_id)
    name = sandbox.name
    await _drop_sandbox(db, sandbox)
    organization = await db.get(Organization, principal.organization_id)
    fresh, counts = await sandbox_service.create(db, organization, principal.user_id, name=name, sample_data=payload.sample_data)
    await db.commit()
    return {**(await sandbox_out(db, fresh)), "copied": counts}


class DeployPayload(BaseModel):
    categories: list[str] = Field(min_length=1)


@router.post("/sandboxes/deploy", dependencies=[Depends(require_feature("sandbox"))])
async def deploy_from_sandbox(
    payload: DeployPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    sandbox = await db.get(Organization, principal.organization_id)
    parent_id = (sandbox.settings or {}).get("sandbox_of")
    if not parent_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Open the sandbox to deploy its changes to production.")
    parent = await db.get(Organization, uuid.UUID(parent_id))
    membership = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == parent.id,
                OrganizationMembership.user_id == principal.user_id,
                OrganizationMembership.status == "active",
            )
        )
    ).scalar_one_or_none()
    if membership is None or membership.role not in {"owner", "admin"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only production administrators can deploy changes.")
    results = await sandbox_service.deploy(db, sandbox, parent, payload.categories)
    record_audit(
        db,
        organization_id=parent.id,
        action="deploy",
        entity_type="sandbox",
        entity_id=sandbox.id,
        entity_label=sandbox.name,
        changes=results,
        actor_user_id=principal.user_id,
    )
    await db.commit()
    invalidate_automation_cache(parent.id)
    return {"deployed": results, "production": parent.name}
