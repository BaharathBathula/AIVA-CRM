"""Developer sandboxes (Enterprise).

A sandbox is a separate workspace that copies production's setup —
pipelines and stages, SLAs, scoring rules, roles, teams, custom modules and
fields, email templates, workflows, stage playbooks, approval rules, custom
buttons, web forms and journeys — and optionally a sample of records. People
test changes there, then deploy selected configuration back to production.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.editions import (
    ApprovalRule,
    CustomButton,
    CustomField,
    CustomModule,
    EmailTemplate,
    Journey,
    StageRule,
    Subscription,
    WebForm,
)
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.models.platform import LeadScoringRule, Role, SlaPolicy, Team, WorkflowRule
from app.services.forms import new_slug
from app.services.workspace import unique_slug

MAX_SANDBOXES = 2
DEPLOYABLE = {
    "custom_fields": "Custom modules and fields",
    "email_templates": "Email templates",
    "workflows": "Workflows",
    "custom_buttons": "Custom buttons",
    "approval_rules": "Approval rules",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _remap(value: Any, mapping: dict[str, str]) -> Any:
    """Replace old ids with new ids anywhere inside JSON-like data."""

    if not mapping:
        return copy.deepcopy(value)
    text = json.dumps(value, default=str)
    for old, new in mapping.items():
        text = text.replace(old, new)
    return json.loads(text)


def _clone(model, source, **overrides):
    values = {
        column.key: getattr(source, column.key)
        for column in source.__table__.columns
        if column.key not in {"id", "created_at", "updated_at", "organization_id"}
    }
    values.update(overrides)
    return model(id=uuid.uuid4(), **values)


async def _copy_setup(db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID) -> dict[str, str]:
    """Copy configuration and return the id map (old → new)."""

    ids: dict[str, str] = {}

    async def rows(model):
        return (await db.execute(select(model).where(model.organization_id == source_id))).scalars().all()

    for team in sorted(await rows(Team), key=lambda t: t.parent_team_id is not None):
        clone = _clone(Team, team, organization_id=target_id, parent_team_id=None)
        ids[str(team.id)] = str(clone.id)
        db.add(clone)
    await db.flush()
    for team in await rows(Team):
        if team.parent_team_id and str(team.parent_team_id) in ids:
            clone = await db.get(Team, uuid.UUID(ids[str(team.id)]))
            clone.parent_team_id = uuid.UUID(ids[str(team.parent_team_id)])

    for role in await rows(Role):
        db.add(_clone(Role, role, organization_id=target_id))

    for pipeline in await rows(Pipeline):
        clone = _clone(Pipeline, pipeline, organization_id=target_id)
        ids[str(pipeline.id)] = str(clone.id)
        db.add(clone)
    await db.flush()
    for stage in await rows(PipelineStage):
        clone = _clone(PipelineStage, stage, organization_id=target_id, pipeline_id=uuid.UUID(ids[str(stage.pipeline_id)]))
        ids[str(stage.id)] = str(clone.id)
        db.add(clone)
    await db.flush()

    for model in (SlaPolicy, LeadScoringRule):
        for row in await rows(model):
            db.add(_clone(model, row, organization_id=target_id))

    for module in await rows(CustomModule):
        db.add(_clone(CustomModule, module, organization_id=target_id))
    for field in await rows(CustomField):
        db.add(_clone(CustomField, field, organization_id=target_id))
    for template in await rows(EmailTemplate):
        clone = _clone(EmailTemplate, template, organization_id=target_id, usage_count=0)
        ids[str(template.id)] = str(clone.id)
        db.add(clone)
    await db.flush()

    for rule in await rows(WorkflowRule):
        db.add(_clone(WorkflowRule, rule, organization_id=target_id, conditions=_remap(rule.conditions, ids), actions=_remap(rule.actions, ids)))
    for rule in await rows(StageRule):
        if str(rule.stage_id) in ids:
            db.add(_clone(StageRule, rule, organization_id=target_id, stage_id=uuid.UUID(ids[str(rule.stage_id)])))
    for rule in await rows(ApprovalRule):
        target_stage = uuid.UUID(ids[str(rule.target_stage_id)]) if rule.target_stage_id and str(rule.target_stage_id) in ids else None
        db.add(_clone(ApprovalRule, rule, organization_id=target_id, target_stage_id=target_stage, conditions=_remap(rule.conditions, ids)))
    for button in await rows(CustomButton):
        db.add(_clone(CustomButton, button, organization_id=target_id, run_count=0, last_run_at=None))
    for form in await rows(WebForm):
        db.add(_clone(WebForm, form, organization_id=target_id, slug=new_slug(), is_active=False, submission_count=0, last_submission_at=None))
    for journey in await rows(Journey):
        db.add(_clone(Journey, journey, organization_id=target_id, status="paused", steps=_remap(journey.steps, ids), conditions=_remap(journey.conditions, ids)))
    await db.flush()
    return ids


async def _copy_sample(db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID, ids: dict[str, str]) -> dict[str, int]:
    accounts = (
        await db.execute(select(Account).where(Account.organization_id == source_id).order_by(Account.updated_at.desc()).limit(25))
    ).scalars().all()
    account_map = {}
    for account in accounts:
        clone = _clone(Account, account, organization_id=target_id, parent_account_id=None)
        account_map[account.id] = clone.id
        db.add(clone)
    await db.flush()
    contacts = (
        await db.execute(select(Contact).where(Contact.organization_id == source_id, Contact.account_id.in_(account_map.keys() or [uuid.uuid4()])).limit(100))
    ).scalars().all()
    contact_map = {}
    for contact in contacts:
        clone = _clone(Contact, contact, organization_id=target_id, account_id=account_map.get(contact.account_id))
        contact_map[contact.id] = clone.id
        db.add(clone)
    opps = (
        await db.execute(select(Opportunity).where(Opportunity.organization_id == source_id, Opportunity.account_id.in_(account_map.keys() or [uuid.uuid4()])).limit(50))
    ).scalars().all()
    copied_opps = 0
    for opp in opps:
        if str(opp.stage_id) not in ids or str(opp.pipeline_id) not in ids:
            continue
        db.add(
            _clone(
                Opportunity,
                opp,
                organization_id=target_id,
                account_id=account_map[opp.account_id],
                primary_contact_id=contact_map.get(opp.primary_contact_id),
                pipeline_id=uuid.UUID(ids[str(opp.pipeline_id)]),
                stage_id=uuid.UUID(ids[str(opp.stage_id)]),
            )
        )
        copied_opps += 1
    leads = (await db.execute(select(Lead).where(Lead.organization_id == source_id).order_by(Lead.created_at.desc()).limit(50))).scalars().all()
    for lead in leads:
        db.add(
            _clone(
                Lead, lead, organization_id=target_id, converted_account_id=None, converted_contact_id=None, converted_opportunity_id=None
            )
        )
    await db.flush()
    return {"accounts": len(accounts), "contacts": len(contacts), "opportunities": copied_opps, "leads": len(leads)}


async def sandboxes_of(db: AsyncSession, organization_id: uuid.UUID) -> list[Organization]:
    return list(
        (
            await db.execute(
                select(Organization).where(Organization.settings["sandbox_of"].astext == str(organization_id)).order_by(Organization.created_at)
            )
        ).scalars()
    )


async def create(db: AsyncSession, parent: Organization, creator_id: uuid.UUID, *, name: str | None, sample_data: bool) -> tuple[Organization, dict[str, int]]:
    if (parent.settings or {}).get("sandbox_of"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Create sandboxes from your production workspace.")
    if len(await sandboxes_of(db, parent.id)) >= MAX_SANDBOXES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"You can have up to {MAX_SANDBOXES} sandboxes. Delete one first.")
    info = db.sync_session.info
    previous = info.get("automation_disabled", False)
    info["automation_disabled"] = True
    try:
        title = (name or f"{parent.name} Sandbox").strip()[:200]
        sandbox = Organization(
            id=uuid.uuid4(),
            name=title,
            slug=await unique_slug(db, f"{parent.slug}-sandbox"),
            is_active=True,
            settings={
                **{k: v for k, v in (parent.settings or {}).items() if k not in {"ai_agents", "hipaa_mode"}},
                "sandbox_of": str(parent.id),
                "sandbox_created_at": utcnow().isoformat(),
                "sandbox_created_by": str(creator_id),
            },
        )
        db.add(sandbox)
        await db.flush()
        members = (
            await db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == parent.id, OrganizationMembership.status == "active"
                )
            )
        ).scalars().all()
        ids = await _copy_setup(db, parent.id, sandbox.id)
        for membership in members:
            db.add(
                OrganizationMembership(
                    id=uuid.uuid4(),
                    organization_id=sandbox.id,
                    user_id=membership.user_id,
                    role=membership.role,
                    status="active",
                    team_id=uuid.UUID(ids[str(membership.team_id)]) if membership.team_id and str(membership.team_id) in ids else None,
                    reports_to_user_id=membership.reports_to_user_id,
                )
            )
        parent_subscription = (
            await db.execute(select(Subscription).where(Subscription.organization_id == parent.id))
        ).scalar_one_or_none()
        db.add(
            Subscription(
                id=uuid.uuid4(),
                organization_id=sandbox.id,
                plan=parent_subscription.plan if parent_subscription else "enterprise",
                status="active",
                seats=len(members),
                provider="sandbox",
                current_period_start=utcnow(),
                current_period_end=parent_subscription.current_period_end if parent_subscription else None,
            )
        )
        counts = await _copy_sample(db, parent.id, sandbox.id, ids) if sample_data else {}
        await db.flush()
    finally:
        info["automation_disabled"] = previous
    return sandbox, counts


async def deploy(db: AsyncSession, sandbox: Organization, parent: Organization, categories: list[str]) -> dict[str, int]:
    """Copy selected configuration from the sandbox to production (create or update by name/key)."""

    unknown = [c for c in categories if c not in DEPLOYABLE]
    if unknown or not categories:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose what to deploy.")
    results: dict[str, int] = {}
    info = db.sync_session.info
    previous = info.get("automation_disabled", False)
    info["automation_disabled"] = True
    try:
        if "custom_fields" in categories:
            count = 0
            prod_modules = {m.key: m for m in (await db.execute(select(CustomModule).where(CustomModule.organization_id == parent.id))).scalars()}
            for module in (await db.execute(select(CustomModule).where(CustomModule.organization_id == sandbox.id))).scalars():
                target = prod_modules.get(module.key)
                if target is None:
                    db.add(_clone(CustomModule, module, organization_id=parent.id))
                else:
                    target.name, target.singular, target.description, target.icon, target.is_active = (
                        module.name, module.singular, module.description, module.icon, module.is_active,
                    )
                count += 1
            prod_fields = {(f.object_type, f.key): f for f in (await db.execute(select(CustomField).where(CustomField.organization_id == parent.id))).scalars()}
            for field in (await db.execute(select(CustomField).where(CustomField.organization_id == sandbox.id))).scalars():
                target = prod_fields.get((field.object_type, field.key))
                if target is None:
                    db.add(_clone(CustomField, field, organization_id=parent.id))
                elif target.field_type == field.field_type:
                    for attr in ("label", "required", "options", "help_text", "position", "step", "is_active"):
                        setattr(target, attr, getattr(field, attr))
                    target.config = {**(field.config or {}), **({"next": (target.config or {}).get("next")} if (target.config or {}).get("next") else {})}
                count += 1
            results["custom_fields"] = count

        if "email_templates" in categories:
            prod = {t.name: t for t in (await db.execute(select(EmailTemplate).where(EmailTemplate.organization_id == parent.id))).scalars()}
            count = 0
            for template in (await db.execute(select(EmailTemplate).where(EmailTemplate.organization_id == sandbox.id))).scalars():
                target = prod.get(template.name)
                if target is None:
                    db.add(_clone(EmailTemplate, template, organization_id=parent.id, usage_count=0))
                else:
                    target.subject, target.body, target.category, target.is_shared = template.subject, template.body, template.category, template.is_shared
                count += 1
            results["email_templates"] = count

        # Stage ids differ between the workspaces; map them by pipeline + stage name.
        async def stage_map() -> dict[str, str]:
            def key(rows):
                return {(p_name, s.name): str(s.id) for s, p_name in rows}

            sandbox_rows = (
                await db.execute(select(PipelineStage, Pipeline.name).join(Pipeline, Pipeline.id == PipelineStage.pipeline_id).where(PipelineStage.organization_id == sandbox.id))
            ).all()
            prod_rows = (
                await db.execute(select(PipelineStage, Pipeline.name).join(Pipeline, Pipeline.id == PipelineStage.pipeline_id).where(PipelineStage.organization_id == parent.id))
            ).all()
            prod = key(prod_rows)
            return {stage_id: prod[k] for k, stage_id in key(sandbox_rows).items() if k in prod}

        stages = await stage_map() if {"workflows", "approval_rules"} & set(categories) else {}

        if "workflows" in categories:
            prod = {w.name: w for w in (await db.execute(select(WorkflowRule).where(WorkflowRule.organization_id == parent.id))).scalars()}
            count = 0
            for rule in (await db.execute(select(WorkflowRule).where(WorkflowRule.organization_id == sandbox.id))).scalars():
                conditions, actions = _remap(rule.conditions, stages), _remap(rule.actions, stages)
                target = prod.get(rule.name)
                if target is None:
                    db.add(_clone(WorkflowRule, rule, organization_id=parent.id, conditions=conditions, actions=actions, is_active=False))
                else:
                    target.description, target.object_type, target.trigger_event, target.trigger_field = (
                        rule.description, rule.object_type, rule.trigger_event, rule.trigger_field,
                    )
                    target.conditions, target.actions = conditions, actions
                count += 1
            results["workflows"] = count

        if "custom_buttons" in categories:
            prod = {(b.object_type, b.label): b for b in (await db.execute(select(CustomButton).where(CustomButton.organization_id == parent.id))).scalars()}
            count = 0
            for button in (await db.execute(select(CustomButton).where(CustomButton.organization_id == sandbox.id))).scalars():
                target = prod.get((button.object_type, button.label))
                if target is None:
                    db.add(_clone(CustomButton, button, organization_id=parent.id, run_count=0, last_run_at=None))
                else:
                    target.description, target.action_type, target.config, target.is_active = button.description, button.action_type, button.config, button.is_active
                count += 1
            results["custom_buttons"] = count

        if "approval_rules" in categories:
            prod = {r.name: r for r in (await db.execute(select(ApprovalRule).where(ApprovalRule.organization_id == parent.id))).scalars()}
            count = 0
            for rule in (await db.execute(select(ApprovalRule).where(ApprovalRule.organization_id == sandbox.id))).scalars():
                target_stage = uuid.UUID(stages[str(rule.target_stage_id)]) if rule.target_stage_id and str(rule.target_stage_id) in stages else None
                if rule.target_stage_id and target_stage is None:
                    continue
                target = prod.get(rule.name)
                if target is None:
                    db.add(_clone(ApprovalRule, rule, organization_id=parent.id, target_stage_id=target_stage))
                else:
                    target.object_type, target.trigger, target.target_stage_id = rule.object_type, rule.trigger, target_stage
                    target.conditions, target.approver_type, target.approver_value, target.lock_record, target.is_active = (
                        rule.conditions, rule.approver_type, rule.approver_value, rule.lock_record, rule.is_active,
                    )
                count += 1
            results["approval_rules"] = count
        await db.flush()
    finally:
        info["automation_disabled"] = previous
    return results
