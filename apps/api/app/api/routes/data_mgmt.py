"""Data management: waterfall enrichment, global picklists, goals, removal
(right-to-be-forgotten) requests and the do-not-contact list."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import CampaignRecipient, CustomField, TeamMember
from app.models.email_message import EmailMessage
from app.models.email_thread import EmailThread
from app.models.engagement import WebVisitor
from app.models.governance import DataRequest, GlobalPicklist, Goal, RemovalRequest, SuppressionEntry
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.outbound import SequenceEnrollment, SequenceMessage
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Team
from app.models.task import Task
from app.services import waterfall, workspace_settings
from app.services.automation import record_audit
from app.services.lookups import names_for

router = APIRouter(tags=["Data management"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Waterfall enrichment
# ===========================================================================

ENRICH_MODELS = {"lead": Lead, "contact": Contact, "account": Account}


@router.get("/enrichment/providers")
async def enrichment_providers(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    settings = await workspace_settings.get(db, principal.organization_id, "enrichment")
    keys = await workspace_settings.secrets_set(db, principal.organization_id, "enrichment")
    catalog = {p["key"]: p for p in waterfall.provider_list()}
    return {
        "providers": [
            {
                **catalog[p.key],
                "enabled": p.enabled,
                "key_set": keys.get(catalog[p.key]["needs_key"], False) if catalog[p.key]["needs_key"] else True,
                "plan_ok": not catalog[p.key]["cost"] or principal.has("waterfall_enrichment"),
            }
            for p in settings.providers
        ],
        "settings": settings.model_dump(mode="json"),
        "fields": [{"key": k, "label": v} for k, v in waterfall.FIELD_LABELS.items()],
        "can_edit": principal.can("admin.settings"),
    }


@router.post("/enrichment/providers/{provider}/test")
async def test_enrichment_provider(provider: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    try:
        return await waterfall.test_provider(db, principal.organization_id, provider)
    except waterfall.ProviderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


class EnrichRun(BaseModel):
    record_type: str = Field(pattern="^(lead|contact|account)$")
    record_id: uuid.UUID


class EnrichBulk(BaseModel):
    record_type: str = Field(pattern="^(lead|contact|account)$")
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


@router.post("/enrichment/run", dependencies=[Depends(require_feature("data_enrichment"))])
async def run_enrichment(payload: EnrichRun, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    record = await db.get(ENRICH_MODELS[payload.record_type], payload.record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    result = await waterfall.run(db, principal, payload.record_type, record)
    await db.commit()
    return result


@router.post("/enrichment/run-bulk", dependencies=[Depends(require_feature("data_enrichment"))])
async def run_enrichment_bulk(payload: EnrichBulk, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    model = ENRICH_MODELS[payload.record_type]
    records = (await db.execute(select(model).where(model.organization_id == principal.organization_id, model.id.in_(payload.ids)))).scalars().all()
    updated = credits = 0
    stopped = None
    for record in records:
        try:
            result = await waterfall.run(db, principal, payload.record_type, record)
        except HTTPException as exc:
            stopped = exc.detail
            break
        updated += 1 if result["applied"] else 0
        credits += result["credits"]
    await db.commit()
    return {"processed": len(records), "updated": updated, "credits": credits, "stopped": stopped}


# ===========================================================================
# Global picklists
# ===========================================================================


class PicklistValue(BaseModel):
    value: str = Field(min_length=1, max_length=120)
    color: str = Field(default="gray", max_length=20)
    is_active: bool = True


class PicklistPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    values: list[PicklistValue] = Field(default_factory=list, max_length=500)


async def _picklist_fields(db: AsyncSession, org: uuid.UUID) -> dict[str, list[dict[str, str]]]:
    rows = (await db.execute(select(CustomField).where(CustomField.organization_id == org, CustomField.picklist_id.is_not(None)))).scalars().all()
    out: dict[str, list[dict[str, str]]] = {}
    for f in rows:
        out.setdefault(str(f.picklist_id), []).append({"id": str(f.id), "label": f.label, "object_type": f.object_type})
    return out


def picklist_out(row: GlobalPicklist, fields: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "values": list(row.values or []),
        "fields": fields.get(str(row.id), []),
        "updated_at": row.updated_at,
    }


def _clean_values(values: list[PicklistValue]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for value in values:
        key = value.value.strip()
        if key.lower() in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"“{key}” is listed twice.")
        seen.add(key.lower())
        out.append({"value": key, "color": value.color, "is_active": value.is_active})
    return out


async def _sync_fields(db: AsyncSession, row: GlobalPicklist) -> int:
    options = [v["value"] for v in row.values or [] if v.get("is_active", True)]
    fields = (await db.execute(select(CustomField).where(CustomField.picklist_id == row.id))).scalars().all()
    for field in fields:
        field.options = options
    return len(fields)


@router.get("/picklists")
async def list_picklists(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(GlobalPicklist).where(GlobalPicklist.organization_id == principal.organization_id).order_by(GlobalPicklist.name))).scalars().all()
    fields = await _picklist_fields(db, principal.organization_id)
    return {"items": [picklist_out(r, fields) for r in rows], "can_edit": principal.can("admin.settings")}


@router.post("/picklists", status_code=status.HTTP_201_CREATED)
async def create_picklist(payload: PicklistPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    name = payload.name.strip()
    if (await db.execute(select(GlobalPicklist.id).where(GlobalPicklist.organization_id == principal.organization_id, func.lower(GlobalPicklist.name) == name.lower()))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A picklist with that name already exists.")
    row = GlobalPicklist(id=uuid.uuid4(), organization_id=principal.organization_id, name=name, description=payload.description, values=_clean_values(payload.values))
    db.add(row)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="picklist", entity_id=row.id, entity_label=name)
    await db.commit()
    return picklist_out(row, {})


@router.put("/picklists/{picklist_id}")
async def update_picklist(picklist_id: uuid.UUID, payload: PicklistPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(GlobalPicklist, picklist_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Picklist not found.")
    name = payload.name.strip()
    clash = (
        await db.execute(
            select(GlobalPicklist.id).where(
                GlobalPicklist.organization_id == principal.organization_id, func.lower(GlobalPicklist.name) == name.lower(), GlobalPicklist.id != row.id
            )
        )
    ).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "A picklist with that name already exists.")
    values = _clean_values(payload.values)
    if not [v for v in values if v["is_active"]] and (await _picklist_fields(db, principal.organization_id)).get(str(row.id)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Fields use this picklist, so keep at least one active value.")
    before = list(row.values or [])
    row.name, row.description, row.values = name, payload.description, values
    synced = await _sync_fields(db, row)
    record_audit(
        db, organization_id=principal.organization_id, action="update", entity_type="picklist", entity_id=row.id, entity_label=name,
        changes={"values": {"from": [v["value"] for v in before], "to": [v["value"] for v in values]}, "fields_updated": synced},
    )
    await db.commit()
    return picklist_out(row, await _picklist_fields(db, principal.organization_id))


@router.delete("/picklists/{picklist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_picklist(picklist_id: uuid.UUID, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(GlobalPicklist, picklist_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Picklist not found.")
    used = (await _picklist_fields(db, principal.organization_id)).get(str(row.id))
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{len(used)} field{'s use' if len(used) != 1 else ' uses'} this picklist. Switch {'them' if len(used) != 1 else 'it'} to their own options first.")
    record_audit(db, organization_id=principal.organization_id, action="delete", entity_type="picklist", entity_id=row.id, entity_label=row.name)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Goals
# ===========================================================================

METRICS: dict[str, dict[str, str]] = {
    "deals_won_amount": {"label": "Revenue won", "unit": "money"},
    "deals_won": {"label": "Deals won", "unit": "count"},
    "pipeline_created": {"label": "Pipeline created", "unit": "money"},
    "deals_created": {"label": "Deals created", "unit": "count"},
    "meetings_held": {"label": "Meetings held", "unit": "count"},
    "calls_logged": {"label": "Calls logged", "unit": "count"},
    "emails_sent": {"label": "Emails sent", "unit": "count"},
    "tasks_completed": {"label": "Tasks completed", "unit": "count"},
    "leads_created": {"label": "Leads created", "unit": "count"},
}


def period_bounds(period: str, fiscal_start_month: int = 1, today: date | None = None) -> tuple[datetime, datetime, str]:
    today = today or date.today()
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        label = f"Week of {start:%b %d}"
    elif period == "month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        label = f"{start:%B %Y}"
    elif period == "quarter":
        offset = (today.month - fiscal_start_month) % 12
        quarter_start_month = (fiscal_start_month - 1 + (offset // 3) * 3) % 12 + 1
        year = today.year if quarter_start_month <= today.month else today.year - 1
        start = date(year, quarter_start_month, 1)
        month = quarter_start_month + 3
        end = date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
        label = f"Quarter starting {start:%b %Y}"
    else:
        year = today.year if today.month >= fiscal_start_month else today.year - 1
        start = date(year, fiscal_start_month, 1)
        end = date(year + 1, fiscal_start_month, 1)
        label = f"Year starting {start:%b %Y}"
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end, time.min, tzinfo=timezone.utc),
        label,
    )


async def metric_by_user(db: AsyncSession, org: uuid.UUID, metric: str, start: datetime, end: datetime) -> dict[str, Decimal]:
    won = select(PipelineStage.id).where(PipelineStage.organization_id == org, PipelineStage.category == "won")
    if metric in {"deals_won_amount", "deals_won"}:
        value = func.coalesce(func.sum(Opportunity.amount), 0) if metric == "deals_won_amount" else func.count()
        query = select(Opportunity.owner_user_id, value).where(
            Opportunity.organization_id == org, Opportunity.stage_id.in_(won), Opportunity.closed_at >= start, Opportunity.closed_at < end
        ).group_by(Opportunity.owner_user_id)
    elif metric in {"pipeline_created", "deals_created"}:
        value = func.coalesce(func.sum(Opportunity.amount), 0) if metric == "pipeline_created" else func.count()
        query = select(Opportunity.owner_user_id, value).where(
            Opportunity.organization_id == org, Opportunity.created_at >= start, Opportunity.created_at < end
        ).group_by(Opportunity.owner_user_id)
    elif metric == "meetings_held":
        query = select(Meeting.organizer_user_id, func.count()).where(
            Meeting.organization_id == org, Meeting.status == "completed", Meeting.start_at >= start, Meeting.start_at < end
        ).group_by(Meeting.organizer_user_id)
    elif metric == "calls_logged":
        query = select(Activity.created_by_user_id, func.count()).where(
            Activity.organization_id == org, Activity.activity_type == "call", Activity.occurred_at >= start, Activity.occurred_at < end
        ).group_by(Activity.created_by_user_id)
    elif metric == "emails_sent":
        activity = dict(
            (
                await db.execute(
                    select(Activity.created_by_user_id, func.count()).where(
                        Activity.organization_id == org, Activity.activity_type == "email", Activity.direction == "outbound",
                        Activity.occurred_at >= start, Activity.occurred_at < end,
                    ).group_by(Activity.created_by_user_id)
                )
            ).all()
        )
        return {str(k): Decimal(v) for k, v in activity.items() if k}
    elif metric == "tasks_completed":
        query = select(Task.assigned_to_user_id, func.count()).where(
            Task.organization_id == org, Task.status == "completed", Task.completed_at >= start, Task.completed_at < end
        ).group_by(Task.assigned_to_user_id)
    elif metric == "leads_created":
        query = select(Lead.owner_user_id, func.count()).where(Lead.organization_id == org, Lead.created_at >= start, Lead.created_at < end).group_by(Lead.owner_user_id)
    else:
        return {}
    return {str(user): Decimal(str(value or 0)) for user, value in (await db.execute(query)).all() if user}


async def _team_members(db: AsyncSession, org: uuid.UUID, team_ids: list[str]) -> set[str]:
    ids = [uuid.UUID(t) for t in team_ids]
    members = set(
        str(u)
        for u in (
            await db.execute(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.organization_id == org, OrganizationMembership.team_id.in_(ids), OrganizationMembership.status == "active"
                )
            )
        ).scalars()
    )
    members |= set(str(u) for u in (await db.execute(select(TeamMember.user_id).where(TeamMember.organization_id == org, TeamMember.team_id.in_(ids)))).scalars())
    return members


async def goal_out(db: AsyncSession, org: uuid.UUID, row: Goal, fiscal_start: int, names: dict[str, str], teams: dict[str, str], cache: dict) -> dict[str, Any]:
    start, end, label = period_bounds(row.period, fiscal_start)
    key = (row.metric, start)
    if key not in cache:
        cache[key] = await metric_by_user(db, org, row.metric, start, end)
    values = cache[key]
    target = Decimal(row.target)
    people: list[dict[str, Any]] = []
    if row.assignee_type == "user":
        for user_id in row.assignee_ids or []:
            actual = values.get(str(user_id), Decimal(0))
            people.append({"id": str(user_id), "name": names.get(str(user_id), "Former user"), "actual": float(actual), "target": float(target),
                           "percent": round(float(actual / target * 100), 1) if target else 0})
        actual_total = sum((Decimal(p["actual"]) for p in people), Decimal(0))
        target_total = target * max(1, len(people))
    else:
        members = await _team_members(db, org, row.assignee_ids or []) if row.assignee_type == "team" else None
        actual_total = sum((v for u, v in values.items() if members is None or u in members), Decimal(0))
        target_total = target
    elapsed = (utcnow() - start).total_seconds() / max(1.0, (end - start).total_seconds())
    percent = round(float(actual_total / target_total * 100), 1) if target_total else 0
    pace = "ahead" if percent >= min(100, elapsed * 100) else ("behind" if percent < elapsed * 100 * 0.8 else "on_track")
    return {
        "id": str(row.id),
        "name": row.name,
        "metric": row.metric,
        "metric_label": METRICS.get(row.metric, {}).get("label", row.metric),
        "unit": METRICS.get(row.metric, {}).get("unit", "count"),
        "period": row.period,
        "period_label": label,
        "period_start": start,
        "period_end": end,
        "target": float(target),
        "target_total": float(target_total),
        "actual": float(actual_total),
        "percent": percent,
        "elapsed_percent": round(min(100.0, max(0.0, elapsed * 100)), 1),
        "pace": "done" if percent >= 100 else pace,
        "assignee_type": row.assignee_type,
        "assignee_ids": [str(a) for a in row.assignee_ids or []],
        "assignees": [names.get(str(a)) or teams.get(str(a)) or "Former" for a in row.assignee_ids or []],
        "people": people,
        "is_active": row.is_active,
    }


class GoalPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    metric: str
    period: str = Field(default="month", pattern="^(week|month|quarter|year)$")
    target: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    assignee_type: str = Field(default="user", pattern="^(user|team|workspace)$")
    assignee_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    is_active: bool = True


async def _validate_goal(db: AsyncSession, org: uuid.UUID, payload: GoalPayload) -> None:
    if payload.metric not in METRICS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a metric to track.")
    if payload.assignee_type != "workspace" and not payload.assignee_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose who the goal is for.")
    if payload.assignee_type == "workspace":
        payload.assignee_ids = []
    if payload.assignee_type == "team":
        found = set((await db.execute(select(Team.id).where(Team.organization_id == org, Team.id.in_(payload.assignee_ids)))).scalars())
        if len(found) != len(set(payload.assignee_ids)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick teams from this workspace.")
    if payload.assignee_type == "user":
        found = set(
            (
                await db.execute(
                    select(OrganizationMembership.user_id).where(OrganizationMembership.organization_id == org, OrganizationMembership.user_id.in_(payload.assignee_ids))
                )
            ).scalars()
        )
        if len(found) != len(set(payload.assignee_ids)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick people from this workspace.")


async def _goal_context(db: AsyncSession, org: uuid.UUID, rows: list[Goal]) -> tuple[int, dict[str, str], dict[str, str]]:
    organization = await db.get(Organization, org)
    fiscal = int((organization.settings or {}).get("fiscal_year_start_month") or 1)
    names = await names_for(db, org, "user", [a for r in rows if r.assignee_type == "user" for a in r.assignee_ids or []])
    teams = {str(t.id): t.name for t in (await db.execute(select(Team).where(Team.organization_id == org))).scalars()}
    return fiscal, names, teams


@router.get("/goals", dependencies=[Depends(require_feature("goals"))])
async def list_goals(mine: bool = False, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    rows = (await db.execute(select(Goal).where(Goal.organization_id == org).order_by(Goal.is_active.desc(), Goal.created_at))).scalars().all()
    if mine:
        rows = [r for r in rows if r.assignee_type == "workspace" or str(principal.user_id) in [str(a) for a in r.assignee_ids or []]]
    fiscal, names, teams = await _goal_context(db, org, rows)
    cache: dict = {}
    return {
        "items": [await goal_out(db, org, r, fiscal, names, teams, cache) for r in rows],
        "metrics": [{"key": k, **v} for k, v in METRICS.items()],
        "can_edit": principal.can("sales.forecast_manage") or principal.can("admin.settings"),
    }


@router.post("/goals", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("goals"))])
async def create_goal(payload: GoalPayload, principal: Principal = Depends(require_permission("sales.forecast_manage")), db: AsyncSession = Depends(get_db)):
    await _validate_goal(db, principal.organization_id, payload)
    row = Goal(
        id=uuid.uuid4(), organization_id=principal.organization_id, name=payload.name.strip(), metric=payload.metric, period=payload.period,
        target=payload.target, assignee_type=payload.assignee_type, assignee_ids=[str(a) for a in payload.assignee_ids],
        is_active=payload.is_active, created_by_user_id=principal.user_id,
    )
    db.add(row)
    await db.commit()
    fiscal, names, teams = await _goal_context(db, principal.organization_id, [row])
    return await goal_out(db, principal.organization_id, row, fiscal, names, teams, {})


@router.put("/goals/{goal_id}", dependencies=[Depends(require_feature("goals"))])
async def update_goal(goal_id: uuid.UUID, payload: GoalPayload, principal: Principal = Depends(require_permission("sales.forecast_manage")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Goal, goal_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Goal not found.")
    await _validate_goal(db, principal.organization_id, payload)
    row.name, row.metric, row.period, row.target = payload.name.strip(), payload.metric, payload.period, payload.target
    row.assignee_type, row.assignee_ids, row.is_active = payload.assignee_type, [str(a) for a in payload.assignee_ids], payload.is_active
    await db.commit()
    fiscal, names, teams = await _goal_context(db, principal.organization_id, [row])
    return await goal_out(db, principal.organization_id, row, fiscal, names, teams, {})


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("goals"))])
async def delete_goal(goal_id: uuid.UUID, principal: Principal = Depends(require_permission("sales.forecast_manage")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Goal, goal_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Goal not found.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Removal requests (right to be forgotten)
# ===========================================================================

DEADLINE_DAYS = {"gdpr": 30, "ccpa": 45, "other": 30}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def removal_out(row: RemovalRequest, names: dict[str, str]) -> dict[str, Any]:
    now = utcnow()
    return {
        "id": str(row.id),
        "email": row.email,
        "full_name": row.full_name,
        "regulation": row.regulation,
        "source": row.source,
        "status": row.status,
        "received_at": row.received_at,
        "due_at": row.due_at,
        "days_left": max(0, (row.due_at - now).days) if row.status == "pending" else None,
        "overdue": row.status == "pending" and row.due_at < now,
        "notes": row.notes,
        "summary": row.summary or {},
        "completed_at": row.completed_at,
        "completed_by": names.get(str(row.completed_by_user_id)) if row.completed_by_user_id else None,
        "created_by": names.get(str(row.created_by_user_id)) if row.created_by_user_id else None,
        "created_at": row.created_at,
    }


async def find_personal_data(db: AsyncSession, org: uuid.UUID, email: str) -> dict[str, Any]:
    email = email.lower()
    leads = (await db.execute(select(Lead).where(Lead.organization_id == org, func.lower(Lead.email) == email))).scalars().all()
    contacts = (await db.execute(select(Contact).where(Contact.organization_id == org, func.lower(Contact.email) == email))).scalars().all()
    visitors = (await db.execute(select(WebVisitor).where(WebVisitor.organization_id == org, func.lower(WebVisitor.email) == email))).scalars().all()
    messages = (
        await db.execute(
            select(EmailMessage).where(
                EmailMessage.organization_id == org,
                or_(func.lower(EmailMessage.from_address) == email, func.lower(cast(EmailMessage.to_recipients, String)).contains(email)),
            )
        )
    ).scalars().all()
    recipients = (await db.execute(select(CampaignRecipient).where(CampaignRecipient.organization_id == org, func.lower(CampaignRecipient.email) == email))).scalars().all()
    enrollments = (await db.execute(select(SequenceEnrollment).where(SequenceEnrollment.organization_id == org, func.lower(SequenceEnrollment.email) == email))).scalars().all()
    meetings = [
        m for m in (await db.execute(select(Meeting).where(Meeting.organization_id == org))).scalars()
        if any((a.get("email") or "").lower() == email for a in m.attendees or [])
    ]
    return {"leads": leads, "contacts": contacts, "visitors": visitors, "messages": messages, "recipients": recipients, "enrollments": enrollments, "meetings": meetings}


def _matches_out(found: dict[str, Any]) -> dict[str, Any]:
    return {
        "leads": [{"id": str(r.id), "label": f"{r.first_name} {r.last_name}".strip(), "href": f"/leads/{r.id}"} for r in found["leads"]],
        "contacts": [{"id": str(r.id), "label": f"{r.first_name} {r.last_name}".strip(), "href": f"/contacts/{r.id}"} for r in found["contacts"]],
        "counts": {
            "website_visitors": len(found["visitors"]),
            "email_messages": len(found["messages"]),
            "campaign_sends": len(found["recipients"]),
            "sequence_enrollments": len(found["enrollments"]),
            "meeting_invites": len(found["meetings"]),
        },
    }


class RemovalPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str | None = Field(default=None, max_length=200)
    regulation: str = Field(default="gdpr", pattern="^(gdpr|ccpa|other)$")
    source: str = Field(default="email", pattern="^(email|web_form|phone|letter|other)$")
    received_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5000)


@router.get("/removal-requests")
async def list_removal_requests(principal: Principal = Depends(require_permission("data.privacy")), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(RemovalRequest).where(RemovalRequest.organization_id == principal.organization_id).order_by(RemovalRequest.received_at.desc()).limit(500))
    ).scalars().all()
    names = await names_for(db, principal.organization_id, "user", [u for r in rows for u in (r.completed_by_user_id, r.created_by_user_id)])
    items = [removal_out(r, names) for r in rows]
    return {
        "items": items,
        "counts": {
            "pending": sum(1 for i in items if i["status"] == "pending"),
            "overdue": sum(1 for i in items if i["overdue"]),
            "completed": sum(1 for i in items if i["status"] == "completed"),
        },
    }


@router.post("/removal-requests", status_code=status.HTTP_201_CREATED)
async def create_removal_request(payload: RemovalPayload, principal: Principal = Depends(require_permission("data.privacy")), db: AsyncSession = Depends(get_db)):
    email = payload.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the person's email address.")
    received = payload.received_at or utcnow()
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    if received > utcnow() + timedelta(minutes=5):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The request can't be received in the future.")
    row = RemovalRequest(
        id=uuid.uuid4(), organization_id=principal.organization_id, email=email, full_name=payload.full_name, regulation=payload.regulation,
        source=payload.source, received_at=received, due_at=received + timedelta(days=DEADLINE_DAYS[payload.regulation]),
        notes=payload.notes, created_by_user_id=principal.user_id,
    )
    db.add(row)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="removal_request", entity_id=row.id, entity_label=f"Removal request ({payload.regulation.upper()})")
    await db.commit()
    return removal_out(row, {str(principal.user_id): principal.full_name})


@router.get("/removal-requests/{request_id}")
async def removal_detail(request_id: uuid.UUID, principal: Principal = Depends(require_permission("data.privacy")), db: AsyncSession = Depends(get_db)):
    row = await db.get(RemovalRequest, request_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found.")
    names = await names_for(db, principal.organization_id, "user", [row.completed_by_user_id, row.created_by_user_id])
    out = removal_out(row, names)
    if row.status == "pending":
        out["matches"] = _matches_out(await find_personal_data(db, principal.organization_id, row.email))
    return out


class RemovalDecision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/removal-requests/{request_id}/complete")
async def complete_removal(request_id: uuid.UUID, payload: RemovalDecision, principal: Principal = Depends(require_permission("data.privacy")), db: AsyncSession = Depends(get_db)):
    """Erase everything AIVA holds about the person and block them from coming back."""

    org = principal.organization_id
    row = await db.get(RemovalRequest, request_id)
    if row is None or row.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found.")
    if row.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This request is already closed.")
    db.sync_session.info["automation_disabled"] = True
    found = await find_personal_data(db, org, row.email)
    lead_ids = [r.id for r in found["leads"]]
    contact_ids = [r.id for r in found["contacts"]]
    summary: dict[str, int] = {}
    # Activities and notes about the person go with them.
    if lead_ids or contact_ids:
        conditions = []
        if lead_ids:
            conditions.append(Activity.lead_id.in_(lead_ids))
        if contact_ids:
            conditions.append(Activity.contact_id.in_(contact_ids))
        result = await db.execute(delete(Activity).where(Activity.organization_id == org, or_(*conditions)))
        summary["activities"] = result.rowcount or 0
        task_conditions = []
        if lead_ids:
            task_conditions.append(Task.lead_id.in_(lead_ids))
        if contact_ids:
            task_conditions.append(Task.contact_id.in_(contact_ids))
        for task in (await db.execute(select(Task).where(Task.organization_id == org, or_(*task_conditions)))).scalars():
            task.lead_id = None
            task.contact_id = None
        dr_conditions = []
        if lead_ids:
            dr_conditions.append(and_(DataRequest.record_type == "lead", DataRequest.record_id.in_(lead_ids)))
        if contact_ids:
            dr_conditions.append(and_(DataRequest.record_type == "contact", DataRequest.record_id.in_(contact_ids)))
        await db.execute(delete(DataRequest).where(DataRequest.organization_id == org, or_(*dr_conditions)))
    for record in found["leads"] + found["contacts"]:
        await db.delete(record)
    summary["leads"] = len(found["leads"])
    summary["contacts"] = len(found["contacts"])
    for visitor in found["visitors"]:
        await db.delete(visitor)
    summary["website_visitors"] = len(found["visitors"])
    thread_ids = {m.thread_id for m in found["messages"]}
    for message in found["messages"]:
        await db.delete(message)
    summary["email_messages"] = len(found["messages"])
    await db.flush()
    for thread_id in thread_ids:
        remaining = (await db.execute(select(func.count()).select_from(EmailMessage).where(EmailMessage.thread_id == thread_id))).scalar_one()
        thread = await db.get(EmailThread, thread_id)
        if thread is None:
            continue
        if not remaining:
            await db.delete(thread)
        else:
            thread.participants = [p for p in thread.participants or [] if (p.get("email") or "").lower() != row.email]
    for recipient in found["recipients"]:
        recipient.email = "erased@removed.invalid"
        recipient.name = None
        recipient.body = None
    summary["campaign_sends"] = len(found["recipients"])
    for enrollment in found["enrollments"]:
        await db.execute(delete(SequenceMessage).where(SequenceMessage.enrollment_id == enrollment.id))
        await db.delete(enrollment)
    summary["sequence_enrollments"] = len(found["enrollments"])
    for meeting in found["meetings"]:
        meeting.attendees = [a for a in meeting.attendees or [] if (a.get("email") or "").lower() != row.email]
    summary["meeting_invites"] = len(found["meetings"])
    existing = (
        await db.execute(select(SuppressionEntry).where(SuppressionEntry.organization_id == org, SuppressionEntry.kind == "email", SuppressionEntry.value == row.email))
    ).scalar_one_or_none()
    if existing is None:
        db.add(SuppressionEntry(id=uuid.uuid4(), organization_id=org, kind="email", value=row.email, reason="removal_request",
                                note="Kept so this person is never contacted or imported again.", created_by_user_id=principal.user_id))
    else:
        existing.reason = "removal_request"
    row.status = "completed"
    row.completed_at = utcnow()
    row.completed_by_user_id = principal.user_id
    row.summary = summary
    if payload.note:
        row.notes = f"{row.notes}\n{payload.note}".strip() if row.notes else payload.note
    row.full_name = None
    record_audit(db, organization_id=org, action="personal_data_erased", entity_type="removal_request", entity_id=row.id,
                 entity_label=f"Removal request ({row.regulation.upper()})", changes=summary)
    await db.commit()
    return removal_out(row, {str(principal.user_id): principal.full_name})


@router.post("/removal-requests/{request_id}/reject")
async def reject_removal(request_id: uuid.UUID, payload: RemovalDecision, principal: Principal = Depends(require_permission("data.privacy")), db: AsyncSession = Depends(get_db)):
    row = await db.get(RemovalRequest, request_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found.")
    if row.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This request is already closed.")
    if not (payload.note or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say why the request was rejected (for example, identity couldn't be verified).")
    row.status = "rejected"
    row.completed_at = utcnow()
    row.completed_by_user_id = principal.user_id
    row.notes = f"{row.notes}\n{payload.note}".strip() if row.notes else payload.note
    record_audit(db, organization_id=principal.organization_id, action="update", entity_type="removal_request", entity_id=row.id, entity_label="Removal request rejected")
    await db.commit()
    return removal_out(row, {str(principal.user_id): principal.full_name})


# ===========================================================================
# Do-not-contact list
# ===========================================================================


class SuppressionPayload(BaseModel):
    values: list[str] = Field(min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=300)


@router.get("/suppression")
async def list_suppression(
    search: str | None = Query(default=None, max_length=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(SuppressionEntry).where(SuppressionEntry.organization_id == principal.organization_id)
    if search:
        query = query.where(SuppressionEntry.value.ilike(f"%{search.strip().lower()}%"))
    rows = (await db.execute(query.order_by(SuppressionEntry.created_at.desc()).limit(500))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(SuppressionEntry).where(SuppressionEntry.organization_id == principal.organization_id))).scalar_one()
    return {
        "items": [{"id": str(r.id), "kind": r.kind, "value": r.value, "reason": r.reason, "note": r.note, "created_at": r.created_at} for r in rows],
        "total": total,
        "can_edit": principal.can("data.privacy"),
    }


@router.post("/suppression", status_code=status.HTTP_201_CREATED)
async def add_suppression(payload: SuppressionPayload, principal: Principal = Depends(require_permission("data.privacy")), db: AsyncSession = Depends(get_db)):
    added = skipped = 0
    for raw in payload.values:
        value = raw.strip().lower().removeprefix("@")
        if not value:
            continue
        if "@" in value:
            if not EMAIL_RE.match(value):
                skipped += 1
                continue
            kind = "email"
        else:
            try:
                value = workspace_settings.clean_domain(value)
            except ValueError:
                skipped += 1
                continue
            kind = "domain"
        exists = (
            await db.execute(select(SuppressionEntry.id).where(SuppressionEntry.organization_id == principal.organization_id, SuppressionEntry.kind == kind, SuppressionEntry.value == value))
        ).first()
        if exists:
            skipped += 1
            continue
        db.add(SuppressionEntry(id=uuid.uuid4(), organization_id=principal.organization_id, kind=kind, value=value, reason="manual", note=payload.note, created_by_user_id=principal.user_id))
        added += 1
        await db.flush()
    await db.commit()
    return {"added": added, "skipped": skipped}


@router.delete("/suppression/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_suppression(entry_id: uuid.UUID, principal: Principal = Depends(require_permission("data.privacy")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SuppressionEntry, entry_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found.")
    if row.reason == "removal_request":
        raise HTTPException(status.HTTP_409_CONFLICT, "This address belongs to a completed removal request, so it stays blocked.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
