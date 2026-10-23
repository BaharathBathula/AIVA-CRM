"""Approval processes for deals and quotes (Enterprise)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.editions import ApprovalRequest, ApprovalRule
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Notification, Quote
from app.services.automation import evaluate_condition, invalidate_automation_cache

ADMIN_ROLES = {"owner", "admin"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def quote_view(quote: Quote) -> SimpleNamespace:
    values = {column.key: getattr(quote, column.key) for column in quote.__table__.columns}
    subtotal = float(quote.subtotal or 0)
    values["discount_percent"] = round(float(quote.discount_total or 0) / subtotal * 100, 2) if subtotal else 0
    values["line_count"] = len(quote.line_items)
    return SimpleNamespace(**values)


async def matching_quote_rule(db: AsyncSession, quote: Quote) -> ApprovalRule | None:
    rules = (
        await db.execute(
            select(ApprovalRule).where(
                ApprovalRule.organization_id == quote.organization_id,
                ApprovalRule.is_active.is_(True),
                ApprovalRule.object_type == "quote",
                ApprovalRule.trigger == "quote_send",
            )
        )
    ).scalars().all()
    view = quote_view(quote)
    for rule in rules:
        if all(evaluate_condition(view, condition) for condition in rule.conditions or []):
            return rule
    return None


async def resolve_approver(db: AsyncSession, rule: ApprovalRule, organization_id: uuid.UUID, requester: uuid.UUID) -> tuple[uuid.UUID | None, str | None]:
    if rule.approver_type == "user" and rule.approver_value:
        return uuid.UUID(rule.approver_value), None
    if rule.approver_type == "manager":
        manager = (
            await db.execute(
                select(OrganizationMembership.reports_to_user_id).where(
                    OrganizationMembership.organization_id == organization_id, OrganizationMembership.user_id == requester
                )
            )
        ).scalar_one_or_none()
        if manager:
            return manager, None
        return None, "admin"
    return None, rule.approver_value or "admin"


async def notify_approvers(db: AsyncSession, request: ApprovalRequest, link: str) -> None:
    if request.approver_user_id:
        recipients = [request.approver_user_id]
    else:
        roles = {request.approver_role or "admin"}
        if roles & ADMIN_ROLES:
            roles |= ADMIN_ROLES
        recipients = list(
            (
                await db.execute(
                    select(OrganizationMembership.user_id).where(
                        OrganizationMembership.organization_id == request.organization_id,
                        OrganizationMembership.status == "active",
                        OrganizationMembership.role.in_(roles),
                    )
                )
            ).scalars()
        )
    for user_id in recipients:
        if user_id == request.requested_by_user_id:
            continue
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=request.organization_id,
                user_id=user_id,
                kind="approval",
                title=f"Approval needed: {request.record_label or request.object_type}"[:200],
                body=f"{request.rule_name}. {request.request_comment or ''}".strip(),
                link="/approvals",
                is_read=False,
            )
        )


async def record_for(db: AsyncSession, organization_id: uuid.UUID, object_type: str, record_id: uuid.UUID) -> Any:
    model = {"opportunity": Opportunity, "quote": Quote}.get(object_type)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Approvals cover deals and quotes.")
    record = await db.get(model, record_id)
    if record is None or record.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    return record


async def submit(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    requester: uuid.UUID,
    object_type: str,
    record_id: uuid.UUID,
    rule_id: uuid.UUID,
    requested_change: dict[str, Any],
    comment: str | None,
) -> ApprovalRequest:
    rule = await db.get(ApprovalRule, rule_id)
    if rule is None or rule.organization_id != organization_id or not rule.is_active or rule.object_type != object_type:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval rule not found.")
    record = await record_for(db, organization_id, object_type, record_id)
    pending = (
        await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.record_id == record_id, ApprovalRequest.status == "pending")
        )
    ).scalars().first()
    if pending is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This record is already waiting for approval.")

    if object_type == "opportunity":
        try:
            stage_id = uuid.UUID(str(requested_change.get("stage_id")))
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose the stage to move to.") from exc
        stage = await db.get(PipelineStage, stage_id)
        if stage is None or stage.organization_id != organization_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That stage doesn't exist.")
        change = {"stage_id": str(stage_id), "stage_name": stage.name}
        label = record.name
    else:
        change = {"status": "sent"}
        label = f"Q-{record.number}: {record.name}"

    approver_user, approver_role = await resolve_approver(db, rule, organization_id, requester)
    request = ApprovalRequest(
        id=uuid.uuid4(),
        organization_id=organization_id,
        rule_id=rule.id,
        rule_name=rule.name,
        object_type=object_type,
        record_id=record_id,
        record_label=label[:300],
        requested_change=change,
        status="pending",
        requested_by_user_id=requester,
        approver_user_id=approver_user,
        approver_role=approver_role,
        request_comment=comment,
    )
    db.add(request)
    await notify_approvers(db, request, "/approvals")
    invalidate_automation_cache(organization_id)
    return request


def can_decide(request: ApprovalRequest, user_id: uuid.UUID, role: str) -> bool:
    if request.status != "pending":
        return False
    if request.requested_by_user_id == user_id and role not in ADMIN_ROLES:
        return False
    if request.approver_user_id == user_id:
        return True
    if request.approver_role and request.approver_role == role:
        return True
    return role in ADMIN_ROLES


async def apply_change(db: AsyncSession, request: ApprovalRequest) -> str:
    session_info = db.sync_session.info
    bypass = set(session_info.get("approval_bypass") or set())
    bypass.add(str(request.record_id))
    session_info["approval_bypass"] = bypass
    if request.object_type == "opportunity":
        from app.schemas.opportunity import OpportunityUpdate
        from app.services.opportunities import update_opportunity

        await update_opportunity(
            db,
            request.organization_id,
            request.record_id,
            OpportunityUpdate(stage_id=uuid.UUID(request.requested_change["stage_id"])),
        )
        return f"Moved to {request.requested_change.get('stage_name', 'the requested stage')}"
    quote = await db.get(Quote, request.record_id)
    if quote is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The quote no longer exists.")
    if quote.status == "draft":
        quote.status = "sent"
        quote.sent_at = utcnow()
    return "Quote marked as sent"


async def decide(db: AsyncSession, request: ApprovalRequest, *, user_id: uuid.UUID, role: str, approve: bool, comment: str | None) -> str:
    if not can_decide(request, user_id, role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't decide this request.")
    request.status = "approved" if approve else "rejected"
    request.decided_by_user_id = user_id
    request.decided_at = utcnow()
    request.decision_comment = comment
    invalidate_automation_cache(request.organization_id)
    db.sync_session.info.setdefault("automation", {}).setdefault("locked", {}).pop(str(request.record_id), None)
    outcome = "Rejected"
    if approve:
        outcome = await apply_change(db, request)
    if request.requested_by_user_id:
        link = f"/opportunities/{request.record_id}" if request.object_type == "opportunity" else f"/quotes/{request.record_id}"
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=request.organization_id,
                user_id=request.requested_by_user_id,
                kind="approval",
                title=f"{'Approved' if approve else 'Rejected'}: {request.record_label}"[:200],
                body=comment or (outcome if approve else "Your request was rejected."),
                link=link,
                is_read=False,
            )
        )
    return outcome
