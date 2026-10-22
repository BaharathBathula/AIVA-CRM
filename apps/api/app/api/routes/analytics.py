"""Reports, dashboards and the home summary."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_permission
from app.db.session import get_db
from app.models.activity import Activity
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Case, Dashboard, Quote, Report
from app.models.task import Task
from app.services.lookups import names_for
from app.services.reporting import CHARTS, GRANULARITIES, ReportFilter, ReportSpec, run_report

router = APIRouter(tags=["Analytics"])


# ===========================================================================
# Reports
# ===========================================================================


class ReportPayload(ReportSpec):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    folder: str | None = Field(default=None, max_length=80)
    is_shared: bool = True


def report_out(report: Report, owners: dict[str, str] | None = None) -> dict:
    return {
        "id": str(report.id),
        "name": report.name,
        "description": report.description,
        "folder": report.folder,
        "object_type": report.object_type,
        "columns": report.columns,
        "filters": report.filters,
        "group_by": report.group_by,
        "date_granularity": report.date_granularity,
        "aggregate": report.aggregate,
        "aggregate_field": report.aggregate_field,
        "chart_type": report.chart_type,
        "sort_field": report.sort_field,
        "sort_direction": report.sort_direction,
        "row_limit": report.row_limit,
        "is_shared": report.is_shared,
        "owner_user_id": str(report.owner_user_id) if report.owner_user_id else None,
        "owner_name": (owners or {}).get(str(report.owner_user_id)),
        "updated_at": report.updated_at,
        "created_at": report.created_at,
    }


def spec_of(report: Report) -> ReportSpec:
    return ReportSpec(
        object_type=report.object_type,
        columns=report.columns or [],
        filters=[ReportFilter(**f) for f in report.filters or []],
        group_by=report.group_by,
        date_granularity=report.date_granularity,
        aggregate=report.aggregate,
        aggregate_field=report.aggregate_field,
        sort_field=report.sort_field,
        sort_direction=report.sort_direction,
        row_limit=report.row_limit,
        chart_type=report.chart_type,
    )


def _visible(principal: Principal):
    return and_(
        Report.organization_id == principal.organization_id,
        or_(Report.is_shared.is_(True), Report.owner_user_id == principal.user_id),
    )


async def _get_report(db: AsyncSession, principal: Principal, report_id: uuid.UUID, edit: bool = False) -> Report:
    report = (await db.execute(select(Report).where(Report.id == report_id, _visible(principal)))).scalar_one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found.")
    if edit and report.owner_user_id != principal.user_id and not principal.can("admin.settings"):
        if not report.is_shared:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can change this report.")
    return report


def _validate_payload(payload: ReportSpec) -> None:
    if payload.chart_type not in CHARTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a chart type.")
    if payload.chart_type in {"bar", "line", "donut"} and not payload.group_by:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Charts need a field to group by.")
    if payload.date_granularity and payload.date_granularity not in GRANULARITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose day, week, month, quarter or year.")


@router.get("/reports")
async def list_reports(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    reports = (await db.execute(select(Report).where(_visible(principal)).order_by(Report.folder.nulls_last(), Report.name))).scalars().all()
    owners = await names_for(db, principal.organization_id, "user", [r.owner_user_id for r in reports])
    return [report_out(r, owners) for r in reports]


@router.post("/reports/run")
async def run_adhoc_report(
    spec: ReportSpec,
    principal: Principal = Depends(require_permission("analytics.reports")),
    db: AsyncSession = Depends(get_db),
):
    _validate_payload(spec)
    return await run_report(db, principal, spec)


@router.post("/reports", status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportPayload,
    principal: Principal = Depends(require_permission("analytics.reports")),
    db: AsyncSession = Depends(get_db),
):
    _validate_payload(payload)
    await run_report(db, principal, payload)  # validates fields and filters
    data = payload.model_dump()
    data["filters"] = [f.model_dump() for f in payload.filters]
    report = Report(id=uuid.uuid4(), organization_id=principal.organization_id, owner_user_id=principal.user_id, **data)
    db.add(report)
    await db.commit()
    return report_out(report)


@router.get("/reports/{report_id}")
async def get_report(
    report_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report(db, principal, report_id)
    owners = await names_for(db, principal.organization_id, "user", [report.owner_user_id])
    return report_out(report, owners)


@router.get("/reports/{report_id}/run")
async def run_saved_report(
    report_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report(db, principal, report_id)
    return {"report": report_out(report), "result": await run_report(db, principal, spec_of(report))}


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: uuid.UUID,
    payload: ReportPayload,
    principal: Principal = Depends(require_permission("analytics.reports")),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report(db, principal, report_id, edit=True)
    _validate_payload(payload)
    await run_report(db, principal, payload)
    data = payload.model_dump()
    data["filters"] = [f.model_dump() for f in payload.filters]
    for key, value in data.items():
        setattr(report, key, value)
    await db.commit()
    return report_out(report)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: uuid.UUID,
    principal: Principal = Depends(require_permission("analytics.reports")),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report(db, principal, report_id, edit=True)
    dashboards = (await db.execute(select(Dashboard).where(Dashboard.organization_id == principal.organization_id))).scalars()
    for dashboard in dashboards:
        widgets = [w for w in dashboard.widgets or [] if w.get("report_id") != str(report.id)]
        if len(widgets) != len(dashboard.widgets or []):
            dashboard.widgets = widgets
    await db.delete(report)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Dashboards
# ===========================================================================


class Widget(BaseModel):
    id: str | None = None
    report_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)
    size: str = Field(default="medium", pattern="^(small|medium|large)$")


class DashboardPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    widgets: list[Widget] = Field(default_factory=list)
    is_shared: bool = True


def dashboard_out(dashboard: Dashboard) -> dict:
    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "description": dashboard.description,
        "widgets": dashboard.widgets or [],
        "is_shared": dashboard.is_shared,
        "owner_user_id": str(dashboard.owner_user_id) if dashboard.owner_user_id else None,
        "updated_at": dashboard.updated_at,
    }


def _dashboard_visible(principal: Principal):
    return and_(
        Dashboard.organization_id == principal.organization_id,
        or_(Dashboard.is_shared.is_(True), Dashboard.owner_user_id == principal.user_id),
    )


@router.get("/dashboards")
async def list_dashboards(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    dashboards = (await db.execute(select(Dashboard).where(_dashboard_visible(principal)).order_by(Dashboard.name))).scalars()
    return [dashboard_out(d) for d in dashboards]


async def _get_dashboard(db: AsyncSession, principal: Principal, dashboard_id: uuid.UUID) -> Dashboard:
    dashboard = (
        await db.execute(select(Dashboard).where(Dashboard.id == dashboard_id, _dashboard_visible(principal)))
    ).scalar_one_or_none()
    if dashboard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dashboard not found.")
    return dashboard


def _widgets(payload: DashboardPayload) -> list[dict]:
    return [
        {"id": w.id or uuid.uuid4().hex[:10], "report_id": str(w.report_id), "title": w.title, "size": w.size}
        for w in payload.widgets
    ]


@router.post("/dashboards", status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    payload: DashboardPayload,
    principal: Principal = Depends(require_permission("analytics.dashboards")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = Dashboard(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        owner_user_id=principal.user_id,
        name=payload.name.strip(),
        description=payload.description,
        widgets=_widgets(payload),
        is_shared=payload.is_shared,
    )
    db.add(dashboard)
    await db.commit()
    return dashboard_out(dashboard)


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(
    dashboard_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await _get_dashboard(db, principal, dashboard_id)
    widgets = []
    for widget in dashboard.widgets or []:
        report = (
            await db.execute(select(Report).where(Report.id == uuid.UUID(widget["report_id"]), _visible(principal)))
        ).scalar_one_or_none()
        if report is None:
            widgets.append({**widget, "error": "This report was deleted or isn't shared with you."})
            continue
        try:
            spec = spec_of(report)
            spec.row_limit = min(spec.row_limit, 10)
            result = await run_report(db, principal, spec)
            widgets.append({**widget, "report": report_out(report), "result": result})
        except HTTPException as exc:
            widgets.append({**widget, "report": report_out(report), "error": exc.detail})
    return dashboard_out(dashboard) | {"widgets": widgets}


@router.put("/dashboards/{dashboard_id}")
async def update_dashboard(
    dashboard_id: uuid.UUID,
    payload: DashboardPayload,
    principal: Principal = Depends(require_permission("analytics.dashboards")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await _get_dashboard(db, principal, dashboard_id)
    dashboard.name = payload.name.strip()
    dashboard.description = payload.description
    dashboard.widgets = _widgets(payload)
    dashboard.is_shared = payload.is_shared
    await db.commit()
    return dashboard_out(dashboard)


@router.delete("/dashboards/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: uuid.UUID,
    principal: Principal = Depends(require_permission("analytics.dashboards")),
    db: AsyncSession = Depends(get_db),
):
    dashboard = await _get_dashboard(db, principal, dashboard_id)
    await db.delete(dashboard)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Home summary
# ===========================================================================


@router.get("/home/summary")
async def home_summary(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    now = datetime.now(timezone.utc)
    today = date.today()
    quarter_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    ninety_days = now - timedelta(days=90)
    thirty_days = now - timedelta(days=30)

    opp_rows = (
        await db.execute(
            select(Opportunity, PipelineStage.category, PipelineStage.name, PipelineStage.position)
            .join(PipelineStage, PipelineStage.id == Opportunity.stage_id)
            .join(Pipeline, Pipeline.id == Opportunity.pipeline_id)
            .where(Opportunity.organization_id == org, Pipeline.is_default.is_(True))
        )
    ).all()
    open_opps = [(o, name, pos) for o, cat, name, pos in opp_rows if cat == "open"]
    won_quarter = [
        o for o, cat, _, _ in opp_rows
        if cat == "won" and (o.closed_at.date() if o.closed_at else o.expected_close_date or today) >= quarter_start
    ]
    closed_recent = [
        (o, cat) for o, cat, _, _ in opp_rows
        if cat in {"won", "lost"} and (o.closed_at or o.updated_at) and (o.closed_at or o.updated_at) >= ninety_days
    ]
    won_recent = sum(1 for _, cat in closed_recent if cat == "won")

    stages: dict[str, dict] = {}
    for o, name, position in open_opps:
        bucket = stages.setdefault(name, {"stage": name, "position": position, "count": 0, "amount": 0.0})
        bucket["count"] += 1
        bucket["amount"] += float(o.amount or 0)

    async def count(model, *where) -> int:
        return (await db.execute(select(func.count()).select_from(model).where(model.organization_id == org, *where))).scalar_one()

    open_case_statuses = ("new", "open", "pending", "on_hold")
    breached = await count(
        Case,
        Case.status.in_(open_case_statuses),
        or_(and_(Case.first_responded_at.is_(None), Case.first_response_due_at < now), Case.resolution_due_at < now),
    )
    my_tasks = (
        await db.execute(
            select(Task)
            .where(
                Task.organization_id == org,
                Task.assigned_to_user_id == principal.user_id,
                Task.status.in_(("open", "in_progress")),
            )
            .order_by(Task.due_at.asc().nulls_last())
            .limit(8)
        )
    ).scalars().all()
    overdue_tasks = await count(Task, Task.status.in_(("open", "in_progress")), Task.due_at < now)
    my_overdue = await count(
        Task, Task.status.in_(("open", "in_progress")), Task.due_at < now, Task.assigned_to_user_id == principal.user_id
    )

    avg_score = (
        await db.execute(select(func.avg(Lead.score)).where(Lead.organization_id == org, Lead.status.notin_(("converted", "unqualified"))))
    ).scalar_one()
    hot_leads = (
        await db.execute(
            select(Lead)
            .where(Lead.organization_id == org, Lead.status.notin_(("converted", "unqualified")))
            .order_by(Lead.score.desc(), Lead.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    activities = (
        await db.execute(select(Activity).where(Activity.organization_id == org).order_by(Activity.occurred_at.desc()).limit(8))
    ).scalars().all()
    authors = await names_for(db, org, "user", [a.created_by_user_id for a in activities])
    accounts = await names_for(db, org, "account", [a.account_id for a in activities] + [t.account_id for t in my_tasks])

    open_quotes = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Quote.total), 0)).where(Quote.organization_id == org, Quote.status == "sent")
        )
    ).one()

    closing_soon = sorted(
        (o for o, _, _ in open_opps if o.expected_close_date and today <= o.expected_close_date <= today + timedelta(days=30)),
        key=lambda o: o.expected_close_date,
    )[:6]
    closing_accounts = await names_for(db, org, "account", [o.account_id for o in closing_soon])

    return {
        "kpis": {
            "open_pipeline": sum(float(o.amount or 0) for o, _, _ in open_opps),
            "weighted_pipeline": sum(float(o.amount or 0) * (o.probability or 0) / 100 for o, _, _ in open_opps),
            "open_opportunities": len(open_opps),
            "won_this_quarter": sum(float(o.amount or 0) for o in won_quarter),
            "won_count_quarter": len(won_quarter),
            "win_rate": round(won_recent / len(closed_recent) * 100, 1) if closed_recent else None,
            "new_leads_30d": await count(Lead, Lead.created_at >= thirty_days),
            "avg_lead_score": round(float(avg_score), 1) if avg_score is not None else None,
            "open_cases": await count(Case, Case.status.in_(open_case_statuses)),
            "sla_breaches": breached,
            "overdue_tasks": overdue_tasks,
            "my_overdue_tasks": my_overdue,
            "quotes_out": open_quotes[0],
            "quotes_out_value": float(open_quotes[1] or 0),
        },
        "pipeline_by_stage": sorted(stages.values(), key=lambda s: s["position"]),
        "closing_soon": [
            {
                "id": str(o.id),
                "name": o.name,
                "account_name": closing_accounts.get(str(o.account_id)),
                "amount": float(o.amount or 0),
                "expected_close_date": o.expected_close_date,
                "probability": o.probability,
            }
            for o in closing_soon
        ],
        "my_tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "priority": t.priority,
                "status": t.status,
                "due_at": t.due_at,
                "overdue": bool(t.due_at and t.due_at < now),
                "account_name": accounts.get(str(t.account_id)),
            }
            for t in my_tasks
        ],
        "hot_leads": [
            {
                "id": str(l.id),
                "name": f"{l.first_name} {l.last_name}".strip(),
                "company_name": l.company_name,
                "score": l.score,
                "status": l.status,
            }
            for l in hot_leads
        ],
        "recent_activity": [
            {
                "id": str(a.id),
                "activity_type": a.activity_type,
                "subject": a.subject,
                "occurred_at": a.occurred_at,
                "account_name": accounts.get(str(a.account_id)),
                "author_name": authors.get(str(a.created_by_user_id)),
            }
            for a in activities
        ],
    }
