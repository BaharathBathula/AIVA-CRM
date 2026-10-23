"""Customer journeys: multi-step nurture flows for leads and contacts.

Steps run in order. ``wait`` pauses the enrollment; every other step runs
immediately and moves on. A ``branch`` step checks conditions and either
continues, jumps to another step, or ends the journey for that person.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.editions import CampaignRecipient, EmailTemplate, Journey, JourneyEnrollment
from app.models.lead import Lead
from app.models.organization import Organization
from app.models.platform import Notification
from app.models.task import Task
from app.models.user import User
from app.services.automation import OPERATORS, _coerce_for_field, evaluate_condition
from app.services.catalog import OBJECTS
from app.services.merge import new_send_token, record_context, render

logger = logging.getLogger("aiva.journeys")

STEP_TYPES = ("send_email", "wait", "create_task", "update_field", "notify", "branch")
MODELS = {"lead": Lead, "contact": Contact}
MAX_STEPS_PER_RUN = 25


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_steps(object_type: str, steps: list[dict[str, Any]], conditions: list[dict[str, Any]]) -> None:
    definition = OBJECTS.get(object_type)
    if object_type not in MODELS or definition is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Journeys run on leads or contacts.")
    if not steps:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one step.")
    if len(steps) > 40:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A journey can have up to 40 steps.")

    def check_conditions(items: list[dict[str, Any]]) -> None:
        for condition in items:
            field = definition.field(condition.get("field") or "")
            if field is None or field.virtual:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{condition.get('field')}' isn't a {definition.singular.lower()} field.")
            if condition.get("operator", "equals") not in OPERATORS:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown condition.")

    check_conditions(conditions)
    for index, step in enumerate(steps, start=1):
        kind = step.get("type")
        params = step.get("params") or {}
        if kind not in STEP_TYPES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: choose what the step does.")
        if kind == "send_email" and not (params.get("template_id") or (params.get("subject") and params.get("body"))):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: choose a template or write the email.")
        if kind == "wait":
            days = int(params.get("days") or 0)
            hours = int(params.get("hours") or 0)
            if days < 0 or hours < 0 or (days == 0 and hours == 0) or days > 365:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: wait between 1 hour and 365 days.")
        if kind == "create_task" and not str(params.get("title") or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: give the task a title.")
        if kind == "update_field":
            field = definition.field(params.get("field") or "")
            if field is None or not field.updatable:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: choose a field the journey can update.")
        if kind == "branch":
            check_conditions(params.get("conditions") or [])
            target = params.get("else")
            if target not in (None, "", "exit", "continue"):
                try:
                    goto = int(target)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: choose where to go otherwise.") from exc
                if goto < 1 or goto > len(steps):
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {index}: that step doesn't exist.")


async def enroll(db: AsyncSession, journey: Journey, records: list[Any]) -> int:
    existing = set(
        (
            await db.execute(select(JourneyEnrollment.record_id).where(JourneyEnrollment.journey_id == journey.id))
        ).scalars()
    )
    added = 0
    definition = OBJECTS[journey.object_type]
    for record in records:
        if record.id in existing:
            continue
        db.add(
            JourneyEnrollment(
                id=uuid.uuid4(),
                organization_id=journey.organization_id,
                journey_id=journey.id,
                record_type=journey.object_type,
                record_id=record.id,
                record_label=definition.label_of(record)[:300],
                status="active",
                current_step=0,
                next_run_at=utcnow(),
                history=[],
            )
        )
        added += 1
    return added


async def _send_email(db: AsyncSession, journey: Journey, enrollment: JourneyEnrollment, record: Any, params: dict) -> str:
    if not getattr(record, "email", None):
        return "Skipped email: no email address"
    if getattr(record, "email_opt_out", False):
        return "Skipped email: unsubscribed"
    subject, body = params.get("subject") or "", params.get("body") or ""
    if params.get("template_id"):
        template = await db.get(EmailTemplate, uuid.UUID(str(params["template_id"])))
        if template is None or template.organization_id != journey.organization_id:
            return "Skipped email: template was deleted"
        subject, body = template.subject, template.body
        template.usage_count = (template.usage_count or 0) + 1
    token = new_send_token()
    sender = await db.get(User, journey.created_by_user_id) if journey.created_by_user_id else None
    context = await record_context(db, record, sender=sender, organization=await db.get(Organization, journey.organization_id), token=token)
    db.add(
        CampaignRecipient(
            id=uuid.uuid4(),
            organization_id=journey.organization_id,
            campaign_id=None,
            journey_id=journey.id,
            record_type=enrollment.record_type,
            record_id=record.id,
            email=record.email.lower(),
            name=f"{record.first_name} {record.last_name}".strip() or None,
            token=token,
            status="queued",
            subject=render(subject, context)[:300],
            body=render(body, context),
        )
    )
    return f"Queued email “{render(subject, context)[:80]}”"


async def _run_step(db: AsyncSession, journey: Journey, enrollment: JourneyEnrollment, record: Any, step: dict) -> tuple[str, Any]:
    """Run one step. Returns (message, flow) where flow is "next", "wait", "exit" or a step index."""

    kind = step.get("type")
    params = step.get("params") or {}
    definition = OBJECTS[journey.object_type]
    owner_id = getattr(record, "owner_user_id", None) or journey.created_by_user_id

    if kind == "send_email":
        return await _send_email(db, journey, enrollment, record, params), "next"
    if kind == "wait":
        delay = timedelta(days=int(params.get("days") or 0), hours=int(params.get("hours") or 0))
        enrollment.next_run_at = utcnow() + delay
        return f"Waiting until {enrollment.next_run_at:%b %d, %H:%M} UTC", "wait"
    if kind == "create_task":
        task = Task(
            id=uuid.uuid4(),
            organization_id=journey.organization_id,
            title=render(params.get("title"), await record_context(db, record))[:250] or "Follow up",
            description=f"Created by the “{journey.name}” journey.",
            task_type=params.get("task_type") or "follow_up",
            status="open",
            priority=params.get("priority") or "medium",
            source="workflow",
            due_at=utcnow() + timedelta(days=int(params.get("due_in_days") or 1)),
            assigned_to_user_id=owner_id,
            created_by_user_id=journey.created_by_user_id,
            is_recurring=False,
            is_ai_generated=False,
            tags=[],
            task_metadata={"journey_id": str(journey.id)},
            lead_id=record.id if journey.object_type == "lead" else None,
            contact_id=record.id if journey.object_type == "contact" else None,
            account_id=getattr(record, "account_id", None),
        )
        db.add(task)
        return f"Created task “{task.title}”", "next"
    if kind == "update_field":
        value = _coerce_for_field(definition, params.get("field"), params.get("value"))
        setattr(record, params.get("field"), value)
        return f"Set {params.get('field')} to {value}", "next"
    if kind == "notify":
        if owner_id is None:
            return "Skipped notification: no owner", "next"
        context = await record_context(db, record)
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=journey.organization_id,
                user_id=owner_id,
                kind="journey",
                title=render(params.get("title") or "{{full_name}} reached a journey step", context)[:200],
                body=f"From the “{journey.name}” journey.",
                link=definition.link(record),
                is_read=False,
            )
        )
        return "Notified the owner", "next"
    if kind == "branch":
        matched = all(evaluate_condition(record, condition) for condition in params.get("conditions") or [])
        if matched:
            return "Conditions matched", "next"
        target = params.get("else") or "exit"
        if target == "continue":
            return "Conditions didn't match; continuing", "next"
        if target == "exit":
            return "Conditions didn't match; left the journey", "exit"
        return f"Conditions didn't match; jumping to step {target}", int(target) - 1
    return f"Skipped unknown step '{kind}'", "next"


async def advance(db: AsyncSession, enrollment: JourneyEnrollment) -> None:
    journey = await db.get(Journey, enrollment.journey_id)
    if journey is None or journey.status != "active":
        return
    model = MODELS.get(enrollment.record_type)
    record = await db.get(model, enrollment.record_id) if model else None
    if record is None:
        enrollment.status = "exited"
        enrollment.next_run_at = None
        enrollment.history = [*enrollment.history, {"at": utcnow().isoformat(), "message": "Record was deleted"}]
        return
    steps = journey.steps or []
    history = list(enrollment.history or [])
    for _ in range(MAX_STEPS_PER_RUN):
        index = enrollment.current_step
        if index >= len(steps):
            enrollment.status = "completed"
            enrollment.completed_at = utcnow()
            enrollment.next_run_at = None
            history.append({"at": utcnow().isoformat(), "message": "Completed the journey"})
            break
        step = steps[index]
        try:
            message, flow = await _run_step(db, journey, enrollment, record, step)
        except Exception as exc:  # noqa: BLE001 - one bad step shouldn't stop the scheduler
            logger.warning("Journey %s step %s failed: %s", journey.id, index, exc)
            enrollment.status = "failed"
            enrollment.next_run_at = None
            history.append({"at": utcnow().isoformat(), "step": index + 1, "message": f"Failed: {exc}"})
            break
        history.append({"at": utcnow().isoformat(), "step": index + 1, "type": step.get("type"), "message": message})
        if flow == "wait":
            enrollment.current_step = index + 1
            break
        if flow == "exit":
            enrollment.status = "exited"
            enrollment.next_run_at = None
            break
        enrollment.current_step = index + 1 if flow == "next" else int(flow)
    enrollment.history = history[-60:]


async def run_due(db: AsyncSession, limit: int = 100) -> int:
    rows = (
        await db.execute(
            select(JourneyEnrollment)
            .join(Journey, Journey.id == JourneyEnrollment.journey_id)
            .where(
                JourneyEnrollment.status == "active",
                JourneyEnrollment.next_run_at <= utcnow(),
                Journey.status == "active",
            )
            .order_by(JourneyEnrollment.next_run_at)
            .limit(limit)
        )
    ).scalars().all()
    for enrollment in rows:
        db.sync_session.info["actor_user_id"] = None
        await advance(db, enrollment)
    if rows:
        await db.commit()
    return len(rows)
