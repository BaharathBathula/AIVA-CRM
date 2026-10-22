"""Provision a new workspace with sensible defaults.

Used by self-service sign-up and by the development bootstrap so that a
fresh organization opens with a working pipeline, service levels, lead
scoring and starter dashboards instead of empty screens.
"""

from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Dashboard, LeadScoringRule, Report, SlaPolicy

DEFAULT_STAGES = [
    ("Qualification", 10, "open"),
    ("Discovery", 25, "open"),
    ("Demo", 40, "open"),
    ("Proposal", 60, "open"),
    ("Negotiation", 80, "open"),
    ("Closed Won", 100, "won"),
    ("Closed Lost", 0, "lost"),
]

DEFAULT_SLAS = [
    ("Urgent", "urgent", 30, 4 * 60),
    ("High", "high", 2 * 60, 24 * 60),
    ("Standard", "medium", 8 * 60, 3 * 24 * 60),
    ("Low", "low", 24 * 60, 5 * 24 * 60),
]

DEFAULT_SCORING = [
    ("Executive title (Chief, VP)", "job_title", "contains", "chief", 25),
    ("Vice president", "job_title", "contains", "vp", 20),
    ("Director", "job_title", "contains", "director", 15),
    ("Has business email", "email", "is_not_empty", None, 5),
    ("Has phone number", "phone", "is_not_empty", None, 5),
    ("Company known", "company_name", "is_not_empty", None, 10),
    ("Came from a referral", "source", "equals", "referral", 20),
    ("Came from the website", "source", "equals", "website", 10),
    ("Qualified by sales", "status", "equals", "qualified", 25),
    ("Contacted", "status", "equals", "contacted", 10),
]

DEFAULT_SETTINGS = {
    "currency": "USD",
    "timezone": "America/Chicago",
    "fiscal_year_start_month": 1,
    "date_format": "MMM d, yyyy",
    "session_timeout_minutes": 30,
}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "workspace"


async def unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    candidate = base
    while (
        await db.execute(select(func.count()).select_from(Organization).where(Organization.slug == candidate))
    ).scalar_one():
        candidate = f"{base}-{secrets.token_hex(2)}"
    return candidate


def starter_reports(organization_id: uuid.UUID, owner_id: uuid.UUID | None) -> list[Report]:
    def report(name: str, **values) -> Report:
        return Report(
            id=uuid.uuid4(),
            organization_id=organization_id,
            owner_user_id=owner_id,
            name=name,
            folder=values.pop("folder", "Starter reports"),
            is_shared=True,
            **values,
        )

    return [
        report(
            "Open pipeline by stage",
            description="Amount of open opportunities in each stage.",
            object_type="opportunity",
            columns=["name", "account_id", "stage_id", "amount", "expected_close_date", "owner_user_id"],
            filters=[{"field": "stage_category", "operator": "equals", "value": "open"}],
            group_by="stage_id",
            aggregate="sum",
            aggregate_field="amount",
            chart_type="bar",
        ),
        report(
            "Won revenue by month",
            description="Closed-won opportunity amount by close month.",
            object_type="opportunity",
            columns=["name", "account_id", "amount", "closed_at", "owner_user_id"],
            filters=[{"field": "stage_category", "operator": "equals", "value": "won"}],
            group_by="closed_at",
            date_granularity="month",
            aggregate="sum",
            aggregate_field="amount",
            chart_type="line",
        ),
        report(
            "Leads by source",
            description="Where new leads come from.",
            object_type="lead",
            columns=["first_name", "last_name", "company_name", "source", "status", "score"],
            group_by="source",
            aggregate="count",
            chart_type="donut",
        ),
        report(
            "Open cases by priority",
            description="Unresolved support cases by priority.",
            object_type="case",
            columns=["number", "subject", "status", "priority", "assigned_to_user_id", "created_at"],
            filters=[{"field": "status", "operator": "in", "value": "new,open,pending,on_hold"}],
            group_by="priority",
            aggregate="count",
            chart_type="bar",
            folder="Service",
        ),
        report(
            "Cases opened by week",
            description="Case volume trend.",
            object_type="case",
            columns=["number", "subject", "status", "priority", "created_at"],
            filters=[{"field": "created_at", "operator": "in_period", "value": "last_90_days"}],
            group_by="created_at",
            date_granularity="week",
            aggregate="count",
            chart_type="line",
            folder="Service",
        ),
        report(
            "Pipeline by owner",
            description="Open opportunity amount per sales rep.",
            object_type="opportunity",
            columns=["name", "account_id", "stage_id", "amount", "owner_user_id"],
            filters=[{"field": "stage_category", "operator": "equals", "value": "open"}],
            group_by="owner_user_id",
            aggregate="sum",
            aggregate_field="amount",
            chart_type="bar",
        ),
    ]


async def provision_workspace(
    db: AsyncSession,
    organization: Organization,
    owner_id: uuid.UUID | None,
    *,
    pipeline_id: uuid.UUID | None = None,
    stage_ids: list[uuid.UUID] | None = None,
) -> Pipeline:
    """Create default pipeline, SLAs, scoring rules, reports and dashboards (idempotent)."""

    # Defaults are system setup, not user edits: keep them out of the audit log.
    info = db.sync_session.info
    previously_disabled = info.get("automation_disabled", False)
    info["automation_disabled"] = True
    try:
        return await _provision(db, organization, owner_id, pipeline_id=pipeline_id, stage_ids=stage_ids)
    finally:
        info["automation_disabled"] = previously_disabled


async def _provision(
    db: AsyncSession,
    organization: Organization,
    owner_id: uuid.UUID | None,
    *,
    pipeline_id: uuid.UUID | None,
    stage_ids: list[uuid.UUID] | None,
) -> Pipeline:
    organization.settings = {**DEFAULT_SETTINGS, **(organization.settings or {})}

    pipeline = (
        await db.execute(
            select(Pipeline).where(Pipeline.organization_id == organization.id, Pipeline.is_default.is_(True))
        )
    ).scalar_one_or_none()
    if pipeline is None:
        pipeline = Pipeline(
            id=pipeline_id or uuid.uuid4(),
            organization_id=organization.id,
            name="Default Sales Pipeline",
            is_default=True,
            is_active=True,
        )
        db.add(pipeline)
        await db.flush()
        for index, (name, probability, category) in enumerate(DEFAULT_STAGES, start=1):
            db.add(
                PipelineStage(
                    id=(stage_ids[index - 1] if stage_ids else uuid.uuid4()),
                    organization_id=organization.id,
                    pipeline_id=pipeline.id,
                    name=name,
                    position=index,
                    probability=probability,
                    category=category,
                    is_active=True,
                )
            )

    has_slas = (
        await db.execute(select(func.count()).select_from(SlaPolicy).where(SlaPolicy.organization_id == organization.id))
    ).scalar_one()
    if not has_slas:
        for name, priority, first_response, resolution in DEFAULT_SLAS:
            db.add(
                SlaPolicy(
                    id=uuid.uuid4(),
                    organization_id=organization.id,
                    name=name,
                    priority=priority,
                    first_response_minutes=first_response,
                    resolution_minutes=resolution,
                    is_active=True,
                )
            )

    has_scoring = (
        await db.execute(
            select(func.count()).select_from(LeadScoringRule).where(LeadScoringRule.organization_id == organization.id)
        )
    ).scalar_one()
    if not has_scoring:
        for name, field, operator, value, points in DEFAULT_SCORING:
            db.add(
                LeadScoringRule(
                    id=uuid.uuid4(),
                    organization_id=organization.id,
                    name=name,
                    field=field,
                    operator=operator,
                    value=value,
                    points=points,
                    is_active=True,
                )
            )

    has_reports = (
        await db.execute(select(func.count()).select_from(Report).where(Report.organization_id == organization.id))
    ).scalar_one()
    if not has_reports:
        reports = starter_reports(organization.id, owner_id)
        db.add_all(reports)
        by_name = {report.name: report for report in reports}

        def widget(name: str, size: str = "medium") -> dict:
            return {"id": uuid.uuid4().hex[:10], "report_id": str(by_name[name].id), "size": size}

        db.add(
            Dashboard(
                id=uuid.uuid4(),
                organization_id=organization.id,
                owner_user_id=owner_id,
                name="Sales overview",
                description="Pipeline health, revenue trend and lead sources.",
                widgets=[
                    widget("Open pipeline by stage", "large"),
                    widget("Won revenue by month"),
                    widget("Leads by source"),
                    widget("Pipeline by owner"),
                ],
                is_shared=True,
            )
        )
        db.add(
            Dashboard(
                id=uuid.uuid4(),
                organization_id=organization.id,
                owner_user_id=owner_id,
                name="Service overview",
                description="Case volume and backlog by priority.",
                widgets=[widget("Open cases by priority"), widget("Cases opened by week")],
                is_shared=True,
            )
        )

    await db.flush()
    return pipeline
