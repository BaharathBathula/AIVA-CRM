"""Global search, notifications, workflow automation and metadata."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.platform import Case, KnowledgeArticle, Notification, Product, Quote, Team, WorkflowRule, WorkflowRun
from app.models.user import User
from app.services.automation import ACTION_TYPES, OPERATORS, TRIGGERS, invalidate_automation_cache
from app.services.catalog import OBJECTS, catalog
from app.services.reporting import PERIODS

router = APIRouter(tags=["Platform"])


# ===========================================================================
# Global search
# ===========================================================================


@router.get("/search")
async def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=5, ge=1, le=20),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    term = q.strip()
    like = f"%{term}%"
    org = principal.organization_id
    groups = []

    async def add(kind: str, label: str, rows: list[dict]):
        if rows:
            groups.append({"type": kind, "label": label, "items": rows})

    accounts = (
        await db.execute(
            select(Account).where(Account.organization_id == org, or_(Account.name.ilike(like), Account.domain.ilike(like))).limit(limit)
        )
    ).scalars()
    await add("account", "Accounts", [
        {"id": str(a.id), "title": a.name, "subtitle": a.industry or a.domain or a.lifecycle_stage, "href": f"/accounts/{a.id}"}
        for a in accounts
    ])

    full_name = func.concat(Contact.first_name, " ", Contact.last_name)
    contacts = (
        await db.execute(
            select(Contact).where(Contact.organization_id == org, or_(full_name.ilike(like), Contact.email.ilike(like))).limit(limit)
        )
    ).scalars()
    await add("contact", "Contacts", [
        {"id": str(c.id), "title": f"{c.first_name} {c.last_name}".strip(), "subtitle": c.email or c.job_title, "href": f"/contacts/{c.id}"}
        for c in contacts
    ])

    lead_name = func.concat(Lead.first_name, " ", Lead.last_name)
    leads = (
        await db.execute(
            select(Lead)
            .where(Lead.organization_id == org, or_(lead_name.ilike(like), Lead.email.ilike(like), Lead.company_name.ilike(like)))
            .limit(limit)
        )
    ).scalars()
    await add("lead", "Leads", [
        {"id": str(l.id), "title": f"{l.first_name} {l.last_name}".strip(), "subtitle": f"{l.company_name or 'Lead'} · score {l.score}", "href": f"/leads/{l.id}"}
        for l in leads
    ])

    opportunities = (
        await db.execute(select(Opportunity).where(Opportunity.organization_id == org, Opportunity.name.ilike(like)).limit(limit))
    ).scalars()
    await add("opportunity", "Opportunities", [
        {"id": str(o.id), "title": o.name, "subtitle": f"${float(o.amount or 0):,.0f}", "href": f"/opportunities/{o.id}"}
        for o in opportunities
    ])

    if principal.can("service.cases"):
        number = term.lower().removeprefix("cs-")
        conditions = [Case.subject.ilike(like)]
        if number.isdigit():
            conditions.append(Case.number == int(number))
        cases = (await db.execute(select(Case).where(Case.organization_id == org, or_(*conditions)).limit(limit))).scalars()
        await add("case", "Cases", [
            {"id": str(c.id), "title": f"CS-{c.number} · {c.subject}", "subtitle": f"{c.status.replace('_', ' ')} · {c.priority}", "href": f"/cases/{c.id}"}
            for c in cases
        ])

    number = term.lower().removeprefix("q-")
    conditions = [Quote.name.ilike(like)]
    if number.isdigit():
        conditions.append(Quote.number == int(number))
    quotes = (await db.execute(select(Quote).where(Quote.organization_id == org, or_(*conditions)).limit(limit))).scalars()
    await add("quote", "Quotes", [
        {"id": str(qt.id), "title": f"Q-{qt.number} · {qt.name}", "subtitle": f"{qt.status} · ${float(qt.total):,.0f}", "href": f"/quotes/{qt.id}"}
        for qt in quotes
    ])

    articles = (
        await db.execute(
            select(KnowledgeArticle)
            .where(KnowledgeArticle.organization_id == org, KnowledgeArticle.status == "published", KnowledgeArticle.title.ilike(like))
            .limit(limit)
        )
    ).scalars()
    await add("article", "Knowledge", [
        {"id": str(a.id), "title": a.title, "subtitle": a.category, "href": f"/knowledge/{a.id}"} for a in articles
    ])

    products = (
        await db.execute(
            select(Product).where(Product.organization_id == org, or_(Product.name.ilike(like), Product.sku.ilike(like))).limit(limit)
        )
    ).scalars()
    await add("product", "Products", [
        {"id": str(p.id), "title": p.name, "subtitle": p.sku or p.family, "href": "/products"} for p in products
    ])

    from app.models.collaboration import Document
    from app.models.meeting import Meeting
    from app.models.supply import PurchaseOrder, SalesOrder, Vendor
    from app.models.work import Project

    def numbered(model, prefix: str, text_column):
        digits = term.lower().removeprefix(prefix)
        conditions = [text_column.ilike(like)]
        if digits.isdigit():
            conditions.append(model.number == int(digits))
        return or_(*conditions)

    if principal.has("sales_orders"):
        rows = (await db.execute(select(SalesOrder).where(SalesOrder.organization_id == org, numbered(SalesOrder, "so-", SalesOrder.subject)).limit(limit))).scalars()
        await add("sales_order", "Sales orders", [
            {"id": str(o.id), "title": f"SO-{o.number} · {o.subject}", "subtitle": f"{o.status} · ${float(o.total):,.0f}", "href": f"/sales-orders/{o.id}"} for o in rows
        ])
    if principal.has("purchase_orders"):
        rows = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.organization_id == org, numbered(PurchaseOrder, "po-", PurchaseOrder.subject)).limit(limit))).scalars()
        await add("purchase_order", "Purchase orders", [
            {"id": str(o.id), "title": f"PO-{o.number} · {o.subject}", "subtitle": f"{o.status.replace('_', ' ')} · ${float(o.total):,.0f}", "href": f"/purchase-orders/{o.id}"} for o in rows
        ])
        rows = (await db.execute(select(Vendor).where(Vendor.organization_id == org, or_(Vendor.name.ilike(like), Vendor.email.ilike(like))).limit(limit))).scalars()
        await add("vendor", "Vendors", [
            {"id": str(v.id), "title": v.name, "subtitle": v.category or v.email, "href": f"/vendors/{v.id}"} for v in rows
        ])
    if principal.has("projects"):
        rows = (await db.execute(select(Project).where(Project.organization_id == org, numbered(Project, "prj-", Project.name)).limit(limit))).scalars()
        await add("project", "Projects", [
            {"id": str(p.id), "title": f"PRJ-{p.number} · {p.name}", "subtitle": p.status.replace("_", " "), "href": f"/projects/{p.id}"} for p in rows
        ])
    if principal.has("documents"):
        visible = or_(Document.visibility != "private", Document.owner_user_id == principal.user_id)
        rows = (await db.execute(select(Document).where(Document.organization_id == org, visible, Document.name.ilike(like)).limit(limit))).scalars()
        await add("document", "Documents", [
            {"id": str(d.id), "title": d.name, "subtitle": f"Version {d.version}", "href": f"/documents/{d.id}"} for d in rows
        ])
    if principal.has("tasks_meetings_calls"):
        rows = (await db.execute(select(Meeting).where(Meeting.organization_id == org, Meeting.title.ilike(like)).order_by(Meeting.start_at.desc()).limit(limit))).scalars()
        await add("meeting", "Meetings", [
            {"id": str(m.id), "title": m.title, "subtitle": f"{m.start_at:%b %d, %Y} · {m.status}", "href": f"/meetings?open={m.id}"} for m in rows
        ])

    return {"query": term, "groups": groups}


# ===========================================================================
# Notifications
# ===========================================================================


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    query = select(Notification).where(
        Notification.organization_id == principal.organization_id, Notification.user_id == principal.user_id
    )
    if unread_only:
        query = query.where(Notification.is_read.is_(False))
    items = (await db.execute(query.order_by(Notification.created_at.desc()).limit(limit))).scalars().all()
    unread = (
        await db.execute(
            select(func.count()).select_from(Notification).where(
                Notification.organization_id == principal.organization_id,
                Notification.user_id == principal.user_id,
                Notification.is_read.is_(False),
            )
        )
    ).scalar_one()
    return {
        "unread": unread,
        "items": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "is_read": n.is_read,
                "created_at": n.created_at,
            }
            for n in items
        ],
    }


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found.")
    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Marked as read."}


@router.post("/notifications/read-all")
async def mark_all_read(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        update(Notification)
        .where(
            Notification.organization_id == principal.organization_id,
            Notification.user_id == principal.user_id,
            Notification.is_read.is_(False),
        )
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"updated": result.rowcount or 0}


# ===========================================================================
# Workflow automation
# ===========================================================================


class Condition(BaseModel):
    field: str
    operator: str = "equals"
    value: str | None = None


class Action(BaseModel):
    type: str
    params: dict = Field(default_factory=dict)


class WorkflowPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    object_type: str
    trigger_event: str
    trigger_field: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    actions: list[Action] = Field(min_length=1)
    is_active: bool = True


async def _validate_workflow(db: AsyncSession, principal: Principal, payload: WorkflowPayload) -> None:
    definition = OBJECTS.get(payload.object_type)
    if definition is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose which records the workflow watches.")
    if payload.trigger_event not in TRIGGERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose when the workflow runs.")
    if payload.trigger_event == "field_changed" and not definition.field(payload.trigger_field or ""):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose which field change starts the workflow.")
    for condition in payload.conditions:
        field = definition.field(condition.field)
        if field is None or field.virtual:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{condition.field}' isn't a {definition.singular.lower()} field.")
        if condition.operator not in OPERATORS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown condition '{condition.operator}'.")

    member_ids = {
        str(member_id)
        for member_id in (
            await db.execute(
                select(OrganizationMembership.user_id).where(OrganizationMembership.organization_id == principal.organization_id)
            )
        ).scalars()
    }

    def check_user(spec, label: str, allow_owner: bool = True):
        if spec in (None, "", "owner") and allow_owner:
            return
        if spec == "actor":
            return
        if str(spec) not in member_ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Choose a workspace member for {label}.")

    for action in payload.actions:
        if action.type not in ACTION_TYPES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown action '{action.type}'.")
        params = action.params
        if action.type == "create_task":
            if not str(params.get("title") or "").strip():
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give the task a title.")
            check_user(params.get("assign_to"), "the task assignee")
        elif action.type == "update_field":
            field = definition.field(params.get("field") or "")
            if field is None or not field.updatable:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a field the workflow can update.")
            if field.type == "enum" and params.get("value") not in field.options:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Choose a valid {field.label.lower()}.")
            if field.type == "user":
                check_user(params.get("value"), field.label.lower(), allow_owner=False)
        elif action.type == "notify":
            check_user(params.get("recipient"), "the notification")
        elif action.type == "assign_owner":
            if not definition.owner_field:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{definition.label} don't have an owner.")
            check_user(params.get("user_id"), "the new owner", allow_owner=False)


async def workflows_out(db: AsyncSession, rules: list[WorkflowRule]) -> list[dict]:
    if not rules:
        return []
    stats = {
        rule_id: (count, last, errors)
        for rule_id, count, last, errors in (
            await db.execute(
                select(
                    WorkflowRun.rule_id,
                    func.count(),
                    func.max(WorkflowRun.created_at),
                    func.count().filter(WorkflowRun.status == "error"),
                )
                .where(WorkflowRun.rule_id.in_([r.id for r in rules]))
                .group_by(WorkflowRun.rule_id)
            )
        ).all()
    }
    return [
        {
            "id": str(rule.id),
            "name": rule.name,
            "description": rule.description,
            "object_type": rule.object_type,
            "trigger_event": rule.trigger_event,
            "trigger_field": rule.trigger_field,
            "conditions": rule.conditions,
            "actions": rule.actions,
            "is_active": rule.is_active,
            "run_count": stats.get(rule.id, (0, None, 0))[0],
            "last_run_at": stats.get(rule.id, (0, None, 0))[1],
            "error_count": stats.get(rule.id, (0, None, 0))[2],
            "updated_at": rule.updated_at,
        }
        for rule in rules
    ]


@router.get("/workflows")
async def list_workflows(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    rules = (
        await db.execute(select(WorkflowRule).where(WorkflowRule.organization_id == principal.organization_id).order_by(WorkflowRule.name))
    ).scalars().all()
    return await workflows_out(db, rules)


async def _get_rule(db: AsyncSession, principal: Principal, rule_id: uuid.UUID) -> WorkflowRule:
    rule = await db.get(WorkflowRule, rule_id)
    if rule is None or rule.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found.")
    return rule


@router.post("/workflows", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowPayload,
    principal: Principal = Depends(require_permission("automation.workflows")),
    db: AsyncSession = Depends(get_db),
):
    await _validate_workflow(db, principal, payload)
    data = payload.model_dump()
    rule = WorkflowRule(id=uuid.uuid4(), organization_id=principal.organization_id, created_by_user_id=principal.user_id, **data)
    db.add(rule)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return (await workflows_out(db, [rule]))[0]


@router.put("/workflows/{rule_id}")
async def update_workflow(
    rule_id: uuid.UUID,
    payload: WorkflowPayload,
    principal: Principal = Depends(require_permission("automation.workflows")),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, principal, rule_id)
    await _validate_workflow(db, principal, payload)
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return (await workflows_out(db, [rule]))[0]


@router.post("/workflows/{rule_id}/toggle")
async def toggle_workflow(
    rule_id: uuid.UUID,
    principal: Principal = Depends(require_permission("automation.workflows")),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, principal, rule_id)
    rule.is_active = not rule.is_active
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return {"is_active": rule.is_active}


@router.delete("/workflows/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    rule_id: uuid.UUID,
    principal: Principal = Depends(require_permission("automation.workflows")),
    db: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(db, principal, rule_id)
    await db.delete(rule)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workflows/{rule_id}/runs")
async def workflow_runs(
    rule_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    await _get_rule(db, principal, rule_id)
    runs = (
        await db.execute(select(WorkflowRun).where(WorkflowRun.rule_id == rule_id).order_by(WorkflowRun.created_at.desc()).limit(100))
    ).scalars()
    return [
        {
            "id": str(run.id),
            "entity_type": run.entity_type,
            "entity_id": str(run.entity_id) if run.entity_id else None,
            "entity_label": run.entity_label,
            "link": OBJECTS[run.entity_type].link(SimpleNamespace(id=run.entity_id)) if run.entity_type in OBJECTS and run.entity_id else None,
            "status": run.status,
            "message": run.message,
            "created_at": run.created_at,
        }
        for run in runs
    ]


# ===========================================================================
# Metadata for builders and pickers
# ===========================================================================


@router.get("/meta/catalog")
async def meta_catalog(_: Principal = Depends(get_current_principal)):
    return {
        "objects": catalog(),
        "operators": list(OPERATORS),
        "periods": list(PERIODS),
        "triggers": list(TRIGGERS),
        "actions": list(ACTION_TYPES),
    }


@router.get("/meta/members")
async def meta_members(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(User, OrganizationMembership)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == principal.organization_id,
                OrganizationMembership.status == "active",
            )
            .order_by(User.full_name)
        )
    ).all()
    teams = {
        t.id: t.name for t in (await db.execute(select(Team).where(Team.organization_id == principal.organization_id))).scalars()
    }
    return [
        {
            "id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
            "role": membership.role,
            "team_id": str(membership.team_id) if membership.team_id else None,
            "team_name": teams.get(membership.team_id),
        }
        for user, membership in rows
    ]
