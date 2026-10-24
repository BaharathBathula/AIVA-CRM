"""Audit trail, workflow automation and lead scoring.

A single SQLAlchemy ``before_flush`` hook sees every insert, update and
delete made through the ORM. For each change it:

1. writes an :class:`AuditLog` row (who changed what, before and after),
2. re-scores leads against the organization's lead scoring rules, and
3. evaluates active :class:`WorkflowRule` rows and runs their actions
   (create a task, update a field, notify someone, assign an owner).

Because it hooks the session rather than individual services, every module
(existing and new) is audited and automatable without extra code. Rules are
loaded per organization by the auth dependency before the request runs,
since flush events cannot issue queries.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.editions import (
    ApprovalRequest,
    ApprovalRule,
    Integration,
    Journey,
    JourneyEnrollment,
    OutboundMessage,
    StageRule,
)
from app.models.pipeline_stage import PipelineStage
from app.models.platform import (
    AuditLog,
    Dashboard,
    KnowledgeArticle,
    LeadScoringRule,
    Notification,
    Report,
    Role,
    SlaPolicy,
    Team,
    WorkflowRule,
    WorkflowRun,
)
from app.models.task import Task
from app.services.catalog import OBJECTS, ObjectDef, object_for_instance

logger = logging.getLogger("aiva.automation")

CACHE_TTL_SECONDS = 15.0
_CACHE: dict[uuid.UUID, tuple[float, dict[str, Any]]] = {}

AUDIT_ONLY: dict[type, tuple[str, Any]] = {
    KnowledgeArticle: ("kb_article", lambda o: o.title),
    WorkflowRule: ("workflow", lambda o: o.name),
    Role: ("role", lambda o: o.name),
    Team: ("team", lambda o: o.name),
    SlaPolicy: ("sla_policy", lambda o: o.name),
    LeadScoringRule: ("scoring_rule", lambda o: o.name),
    Report: ("report", lambda o: o.name),
    Dashboard: ("dashboard", lambda o: o.name),
}

IGNORED_FIELDS = {"updated_at", "created_at"}

# Records created by a signed-in user are owned by that user unless an owner
# was chosen. Cases are excluded so new cases stay in the queue.
AUTO_OWNER_TYPES = {"lead", "account", "contact", "opportunity", "task", "activity", "quote"}

OPERATORS = (
    "equals", "not_equals", "contains", "not_contains", "starts_with",
    "greater_than", "less_than", "is_empty", "is_not_empty", "in",
)

ACTION_TYPES = ("create_task", "update_field", "notify", "assign_owner", "post_to_chat")

CHAT_EVENTS = {
    "lead_created": "New lead",
    "deal_created": "New deal",
    "deal_won": "Deal won",
    "deal_lost": "Deal lost",
    "case_created": "New case",
    "case_urgent": "Urgent case",
    "quote_accepted": "Quote accepted",
    "form_submitted": "Web form submitted",
}


class AutomationBlocked(Exception):
    """Raised inside a flush when a rule stops the change (returned as an API error)."""

    def __init__(self, status_code: int, detail: str, code: str, extra: dict[str, Any] | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.extra = extra or {}
TRIGGERS = ("created", "updated", "created_or_updated", "field_changed")


# ---------------------------------------------------------------------------
# Rule cache
# ---------------------------------------------------------------------------


def invalidate_automation_cache(organization_id: uuid.UUID | None = None) -> None:
    if organization_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(organization_id, None)


async def load_automation_context(
    db: AsyncSession,
    organization_id: uuid.UUID,
    features: frozenset[str] | None = None,
) -> dict[str, Any]:
    entry = _CACHE.get(organization_id)
    if entry and time.monotonic() - entry[0] < CACHE_TTL_SECONDS:
        context = entry[1]
    else:
        rules = (
            await db.execute(
                select(WorkflowRule).where(
                    WorkflowRule.organization_id == organization_id,
                    WorkflowRule.is_active.is_(True),
                )
            )
        ).scalars().all()
        scoring = (
            await db.execute(
                select(LeadScoringRule).where(
                    LeadScoringRule.organization_id == organization_id,
                    LeadScoringRule.is_active.is_(True),
                )
            )
        ).scalars().all()
        stages = (
            await db.execute(select(PipelineStage).where(PipelineStage.organization_id == organization_id))
        ).scalars().all()
        stage_rules = (
            await db.execute(select(StageRule).where(StageRule.organization_id == organization_id))
        ).scalars().all()
        approval_rules = (
            await db.execute(
                select(ApprovalRule).where(ApprovalRule.organization_id == organization_id, ApprovalRule.is_active.is_(True))
            )
        ).scalars().all()
        locked = (
            await db.execute(
                select(ApprovalRequest.record_id, ApprovalRequest.rule_name).where(
                    ApprovalRequest.organization_id == organization_id, ApprovalRequest.status == "pending"
                )
            )
        ).all()
        locking_rules = {rule.name for rule in approval_rules if rule.lock_record}
        journeys = (
            await db.execute(
                select(Journey).where(
                    Journey.organization_id == organization_id, Journey.status == "active", Journey.trigger == "created"
                )
            )
        ).scalars().all()
        from app.models.governance import Territory, WorkspaceSetting
        from app.services.territories import as_context

        territories = (
            await db.execute(
                select(Territory).where(Territory.organization_id == organization_id, Territory.is_active.is_(True), Territory.auto_assign.is_(True))
            )
        ).scalars().all()
        workspace = {
            row.namespace: row.data or {}
            for row in (
                await db.execute(
                    select(WorkspaceSetting).where(
                        WorkspaceSetting.organization_id == organization_id, WorkspaceSetting.namespace.in_(("prospecting", "enrichment"))
                    )
                )
            ).scalars()
        }
        prospecting = workspace.get("prospecting") or {}
        chats = (
            await db.execute(
                select(Integration).where(
                    Integration.organization_id == organization_id,
                    Integration.kind.in_(("slack", "teams")),
                    Integration.is_active.is_(True),
                )
            )
        ).scalars().all()
        # Outgoing webhooks (Zapier, Make, n8n): which events have subscribers.
        from app.integrations.providers.automation import context_entries as webhook_entries

        webhooks = await webhook_entries(db, organization_id)
        context = {
            "stages": {str(stage.id): {"name": stage.name, "category": stage.category} for stage in stages},
            "stage_rules": {
                str(rule.stage_id): {
                    "required_fields": list(rule.required_fields or []),
                    "checklist": list(rule.checklist or []),
                    "exit_requires_checklist": rule.exit_requires_checklist,
                }
                for rule in stage_rules
            },
            "approval_rules": [
                {
                    "id": str(rule.id),
                    "name": rule.name,
                    "object_type": rule.object_type,
                    "trigger": rule.trigger,
                    "target_stage_id": str(rule.target_stage_id) if rule.target_stage_id else None,
                    "conditions": list(rule.conditions or []),
                }
                for rule in approval_rules
            ],
            "locked": {str(record_id): name for record_id, name in locked if name in locking_rules},
            "journeys": [
                {"id": journey.id, "object_type": journey.object_type, "conditions": list(journey.conditions or [])}
                for journey in journeys
            ],
            "chat": [{"channel": chat.kind, "events": list(chat.events or [])} for chat in chats],
            "webhooks": webhooks,
            "workflow_rules": [
                {
                    "id": rule.id,
                    "name": rule.name,
                    "object_type": rule.object_type,
                    "trigger_event": rule.trigger_event,
                    "trigger_field": rule.trigger_field,
                    "conditions": list(rule.conditions or []),
                    "actions": list(rule.actions or []),
                }
                for rule in rules
            ],
            "scoring_rules": [
                {
                    "id": rule.id,
                    "name": rule.name,
                    "field": rule.field,
                    "operator": rule.operator,
                    "value": rule.value,
                    "points": rule.points,
                    "object_type": rule.object_type or "lead",
                }
                for rule in scoring
            ],
            "territories": [as_context(t) for t in territories],
            "default_contact_stage": prospecting.get("default_contact_stage") or "cold",
            # Settings → Waterfall enrichment owns this switch.
            "auto_enrich_new": bool((workspace.get("enrichment") or {}).get("auto_enrich_new", False)),
        }
        _CACHE[organization_id] = (time.monotonic(), context)

    sync_session = db.sync_session if hasattr(db, "sync_session") else db
    sync_session.info["automation"] = context
    if features is not None:
        sync_session.info["features"] = features
    return context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    return value


def _as_number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _compare_value(actual: Any) -> Any:
    if isinstance(actual, uuid.UUID):
        return str(actual)
    if isinstance(actual, bool):
        return "true" if actual else "false"
    return actual


def evaluate_condition(obj: Any, condition: dict[str, Any]) -> bool:
    field = condition.get("field")
    operator = condition.get("operator", "equals")
    expected = condition.get("value")
    if not field or not hasattr(obj, field):
        return False

    actual = _compare_value(getattr(obj, field))

    if operator == "is_empty":
        return actual in (None, "", [], {})
    if operator == "is_not_empty":
        return actual not in (None, "", [], {})
    if actual is None:
        return operator in {"not_equals", "not_contains"}

    if operator in {"greater_than", "less_than"}:
        if isinstance(actual, (datetime, date)):
            try:
                other = date.fromisoformat(str(expected)[:10])
                left = actual.date() if isinstance(actual, datetime) else actual
                return left > other if operator == "greater_than" else left < other
            except ValueError:
                return False
        left, right = _as_number(actual), _as_number(expected)
        if left is None or right is None:
            return False
        return left > right if operator == "greater_than" else left < right

    text_actual = str(actual).lower()
    text_expected = str(expected if expected is not None else "").lower()

    if operator == "equals":
        number_left, number_right = _as_number(actual), _as_number(expected)
        if number_left is not None and number_right is not None and not isinstance(actual, str):
            return number_left == number_right
        return text_actual == text_expected
    if operator == "not_equals":
        return text_actual != text_expected
    if operator == "contains":
        if isinstance(actual, list):
            return any(str(item).lower() == text_expected for item in actual)
        return text_expected in text_actual
    if operator == "not_contains":
        return text_expected not in text_actual
    if operator == "starts_with":
        return text_actual.startswith(text_expected)
    if operator == "in":
        options = [part.strip().lower() for part in text_expected.split(",") if part.strip()]
        return text_actual in options
    return False


def score_lead(lead: Any, rules: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    breakdown = []
    total = 0
    for rule in rules:
        if evaluate_condition(lead, rule):
            total += int(rule["points"])
            breakdown.append({"rule": rule["name"], "points": int(rule["points"])})
    return max(0, min(100, total)), breakdown


def render_template(template: str | None, obj: Any, definition: ObjectDef | None) -> str:
    if not template:
        return ""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key == "record" and definition is not None:
            try:
                return definition.label_of(obj)
            except Exception:  # noqa: BLE001
                return ""
        value = getattr(obj, key, None)
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return f"{value:,.2f}"
        return str(value)

    return re.sub(r"\{\{\s*([a-zA-Z_]+)\s*\}\}", replace, template)


def _label(obj: Any) -> str:
    definition = object_for_instance(obj)
    try:
        if definition is not None:
            return str(definition.label_of(obj))[:300]
        audit = AUDIT_ONLY.get(type(obj))
        if audit:
            return str(audit[1](obj))[:300]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _entity_type(obj: Any) -> str | None:
    definition = object_for_instance(obj)
    if definition is not None:
        return definition.key
    audit = AUDIT_ONLY.get(type(obj))
    return audit[0] if audit else None


def _changes(obj: Any) -> dict[str, dict[str, Any]]:
    state = inspect(obj)
    changes: dict[str, dict[str, Any]] = {}
    for attr in state.mapper.column_attrs:
        if attr.key in IGNORED_FIELDS:
            continue
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        if old == new:
            continue
        changes[attr.key] = {"from": jsonable(old), "to": jsonable(new)}
    return changes


# ---------------------------------------------------------------------------
# Workflow actions
# ---------------------------------------------------------------------------


def _resolve_user(spec: Any, obj: Any, definition: ObjectDef, actor: uuid.UUID | None) -> uuid.UUID | None:
    if spec in (None, "", "owner"):
        if definition.owner_field:
            return getattr(obj, definition.owner_field, None)
        return actor
    if spec == "actor":
        return actor
    try:
        return uuid.UUID(str(spec))
    except ValueError:
        return None


def _coerce_for_field(definition: ObjectDef, field_key: str, value: Any) -> Any:
    field = definition.field(field_key)
    if field is None or not field.updatable:
        raise ValueError(f"Field '{field_key}' can't be updated by a workflow.")
    if field.type == "enum" and value not in field.options:
        raise ValueError(f"'{value}' isn't a valid {field.label.lower()}.")
    if field.type in {"user", "ref"}:
        return uuid.UUID(str(value)) if value else None
    return value


def _link_task(task: Task, obj: Any, entity_type: str) -> None:
    if entity_type == "account":
        task.account_id = obj.id
    elif entity_type == "contact":
        task.contact_id = obj.id
        task.account_id = getattr(obj, "account_id", None)
    elif entity_type == "lead":
        task.lead_id = obj.id
    elif entity_type == "opportunity":
        task.opportunity_id = obj.id
        task.account_id = getattr(obj, "account_id", None)
    elif entity_type in {"case", "quote"}:
        task.account_id = getattr(obj, "account_id", None)
        task.contact_id = getattr(obj, "contact_id", None)


def run_action(
    session: Session,
    action: dict[str, Any],
    obj: Any,
    definition: ObjectDef,
    rule: dict[str, Any],
    actor: uuid.UUID | None,
) -> str:
    kind = action.get("type")
    params = action.get("params") or {}
    org_id = obj.organization_id

    if kind == "create_task":
        due_days = int(params.get("due_in_days") or 1)
        task = Task(
            id=uuid.uuid4(),
            organization_id=org_id,
            title=render_template(params.get("title") or "Follow up on {{record}}", obj, definition)[:250],
            description=render_template(params.get("description"), obj, definition) or None,
            task_type=params.get("task_type") or "follow_up",
            status="open",
            priority=params.get("priority") or "medium",
            source="workflow",
            due_at=datetime.now(timezone.utc) + timedelta(days=due_days),
            assigned_to_user_id=_resolve_user(params.get("assign_to"), obj, definition, actor),
            created_by_user_id=actor,
            is_recurring=False,
            is_ai_generated=False,
            tags=[],
            task_metadata={
                "workflow_rule_id": str(rule["id"]),
                "entity_type": definition.key,
                "entity_id": str(obj.id),
            },
        )
        _link_task(task, obj, definition.key)
        session.add(task)
        return f"Created task “{task.title}”"

    if kind == "update_field":
        field_key = params.get("field")
        value = _coerce_for_field(definition, field_key, params.get("value"))
        setattr(obj, field_key, value)
        return f"Set {field_key} to {value}"

    if kind == "assign_owner":
        if not definition.owner_field:
            raise ValueError("This record type has no owner.")
        user_id = _resolve_user(params.get("user_id"), obj, definition, actor)
        if user_id is None:
            raise ValueError("Choose who should own the record.")
        setattr(obj, definition.owner_field, user_id)
        return "Assigned owner"

    if kind == "notify":
        recipient = _resolve_user(params.get("recipient"), obj, definition, actor)
        if recipient is None:
            return "Skipped notification: the record has no owner"
        session.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=org_id,
                user_id=recipient,
                kind="workflow",
                title=render_template(params.get("title") or "{{record}} needs attention", obj, definition)[:200],
                body=render_template(params.get("message"), obj, definition) or f"Triggered by workflow “{rule['name']}”.",
                link=definition.link(obj),
                is_read=False,
            )
        )
        return "Sent notification"

    if kind == "post_to_chat":
        channel = params.get("channel") or "slack"
        if channel not in {"slack", "teams"}:
            raise ValueError("Choose Slack or Microsoft Teams.")
        text = render_template(params.get("message") or "{{record}} needs attention", obj, definition)
        session.add(
            OutboundMessage(
                id=uuid.uuid4(),
                organization_id=org_id,
                channel=channel,
                event="workflow",
                payload={"title": rule["name"], "text": text[:3000], "link": definition.link(obj)},
                status="pending",
            )
        )
        return f"Queued {channel.title()} message"

    raise ValueError(f"Unknown action '{kind}'.")


def _record_value(obj: Any, key: str) -> Any:
    if key.startswith("cf."):
        return (getattr(obj, "custom_fields", None) or {}).get(key[3:])
    return getattr(obj, key, None)


def _check_stage_rules(obj: Any, context: dict[str, Any], old_stage: str | None, new_stage: str | None) -> None:
    rules = context.get("stage_rules") or {}
    stages = context.get("stages") or {}
    playbook = (getattr(obj, "custom_fields", None) or {}).get("_playbook") or {}
    if old_stage and old_stage in rules:
        rule = rules[old_stage]
        done = set(playbook.get(old_stage) or [])
        open_items = [item for item in rule["checklist"] if item not in done]
        if rule["exit_requires_checklist"] and open_items:
            name = stages.get(old_stage, {}).get("name", "this stage")
            raise AutomationBlocked(
                422,
                f"Finish the {name} checklist before moving on: {', '.join(open_items[:3])}{'…' if len(open_items) > 3 else ''}.",
                "process_checklist",
                {"stage_id": old_stage, "missing": open_items},
            )
    if new_stage and new_stage in rules:
        missing = [key for key in rules[new_stage]["required_fields"] if _record_value(obj, key) in (None, "", [])]
        if missing:
            name = stages.get(new_stage, {}).get("name", "that stage")
            labels = [key[3:].replace("_", " ") if key.startswith("cf.") else key.replace("_", " ") for key in missing]
            raise AutomationBlocked(
                422,
                f"To move this deal to {name}, fill in: {', '.join(labels)}.",
                "process_required_fields",
                {"stage_id": new_stage, "missing": missing},
            )


def _check_approvals(obj: Any, context: dict[str, Any], new_stage: str, bypass: set) -> None:
    if str(obj.id) in bypass:
        return
    for rule in context.get("approval_rules") or []:
        if rule["object_type"] != "opportunity" or rule["trigger"] != "stage_change":
            continue
        if rule["target_stage_id"] and rule["target_stage_id"] != new_stage:
            continue
        if all(evaluate_condition(obj, condition) for condition in rule["conditions"]):
            stage_name = (context.get("stages") or {}).get(new_stage, {}).get("name", "that stage")
            raise AutomationBlocked(
                409,
                f"Moving this deal to {stage_name} needs approval ({rule['name']}). Submit it for approval.",
                "approval_required",
                {"rule_id": rule["id"], "rule_name": rule["name"], "requested_change": {"stage_id": new_stage}},
            )


def _chat_events(kind: str, entity_type: str, obj: Any, changes: dict[str, Any], context: dict[str, Any]) -> list[str]:
    events: list[str] = []
    if entity_type == "lead" and kind == "created":
        events.append("lead_created")
    elif entity_type == "opportunity":
        if kind == "created":
            events.append("deal_created")
        if "stage_id" in changes or kind == "created":
            category = (context.get("stages") or {}).get(str(obj.stage_id), {}).get("category")
            if category == "won" and (kind == "updated"):
                events.append("deal_won")
            elif category == "lost" and kind == "updated":
                events.append("deal_lost")
    elif entity_type == "case":
        if kind == "created":
            events.append("case_created")
        if getattr(obj, "priority", None) == "urgent" and (kind == "created" or "priority" in changes):
            events.append("case_urgent")
    elif entity_type == "quote" and kind == "updated" and changes.get("status", {}).get("to") == "accepted":
        events.append("quote_accepted")
    return events


def _queue_chat(session: Session, obj: Any, entity_type: str, event: str, context: dict[str, Any]) -> None:
    definition = OBJECTS.get(entity_type)
    label = _label(obj)
    details = []
    amount = getattr(obj, "amount", None) if entity_type == "opportunity" else getattr(obj, "total", None)
    if amount:
        details.append(f"${float(amount):,.0f}")
    if entity_type == "case":
        details.append(f"priority {obj.priority}")
    if entity_type == "lead" and getattr(obj, "company_name", None):
        details.append(obj.company_name)
    for chat in context.get("chat") or []:
        if event not in chat["events"]:
            continue
        session.add(
            OutboundMessage(
                id=uuid.uuid4(),
                organization_id=obj.organization_id,
                channel=chat["channel"],
                event=event,
                payload={
                    "title": CHAT_EVENTS.get(event, event),
                    "text": f"{label}" + (f" · {' · '.join(details)}" if details else ""),
                    "link": definition.link(obj) if definition else None,
                },
                status="pending",
            )
        )


def _rule_matches(rule: dict[str, Any], kind: str, entity_type: str, obj: Any, changes: dict[str, Any]) -> bool:
    if rule["object_type"] != entity_type:
        return False
    trigger = rule["trigger_event"]
    if trigger == "created" and kind != "created":
        return False
    if trigger == "updated" and kind != "updated":
        return False
    if trigger == "field_changed" and (kind != "updated" or rule.get("trigger_field") not in changes):
        return False
    if trigger == "created_or_updated" and kind not in {"created", "updated"}:
        return False
    return all(evaluate_condition(obj, condition) for condition in rule.get("conditions") or [])


# ---------------------------------------------------------------------------
# Flush hook
# ---------------------------------------------------------------------------


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, flush_context: Any, instances: Any) -> None:  # noqa: ARG001
    if session.info.get("automation_disabled"):
        return

    context = session.info.get("automation") or {}
    actor = session.info.get("actor_user_id")
    created_ids: set = session.info.setdefault("aiva_created_ids", set())

    events: list[tuple[str, str, Any, dict[str, Any]]] = []

    features_now = session.info.get("features")
    territories = context.get("territories") or []
    for obj in list(session.new):
        entity_type = _entity_type(obj)
        if entity_type is None:
            continue
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        created_ids.add(obj.id)
        definition = OBJECTS.get(entity_type)
        if entity_type == "contact" and not getattr(obj, "stage", None):
            obj.stage = context.get("default_contact_stage") or "cold"
        if (
            entity_type in {"lead", "contact", "account"}
            and context.get("auto_enrich_new")
            and (features_now is None or "data_enrichment" in features_now)
            and not session.info.get("skip_auto_enrich")
        ):
            from app.services.enrichment import enrich

            try:
                enrich(obj)
            except Exception:  # noqa: BLE001 - enrichment must never block a save
                logger.warning("Auto-enrichment failed for a new %s", entity_type)
        if (
            entity_type in {"account", "lead"}
            and territories
            and (features_now is None or "territories" in features_now)
            and getattr(obj, "territory_id", None) is None
        ):
            from app.services.territories import find_territory, next_member

            territory = find_territory(obj, entity_type, territories)
            if territory is not None:
                obj.territory_id = uuid.UUID(territory["id"])
                if territory.get("assignment") == "round_robin" and definition is not None and definition.owner_field and getattr(obj, definition.owner_field, None) is None:
                    member = next_member(territory)
                    if member is not None:
                        setattr(obj, definition.owner_field, member)
        if (
            actor is not None
            and entity_type in AUTO_OWNER_TYPES
            and definition is not None
            and definition.owner_field
            and getattr(obj, definition.owner_field, None) is None
        ):
            setattr(obj, definition.owner_field, actor)
        events.append(("created", entity_type, obj, {}))

    for obj in list(session.dirty):
        entity_type = _entity_type(obj)
        if entity_type is None or obj.id in created_ids:
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        changes = _changes(obj)
        if changes:
            events.append(("updated", entity_type, obj, changes))

    for obj in list(session.deleted):
        entity_type = _entity_type(obj)
        if entity_type is not None:
            events.append(("deleted", entity_type, obj, {}))

    if not events:
        return

    scoring_rules = context.get("scoring_rules") or []
    workflow_rules = context.get("workflow_rules") or []
    features = session.info.get("features")
    bypass = session.info.get("approval_bypass") or set()
    locked = context.get("locked") or {}

    def enabled(feature: str) -> bool:
        return features is None or feature in features

    for kind, entity_type, obj, changes in events:
        organization_id = getattr(obj, "organization_id", None)

        if kind == "updated" and locked and str(getattr(obj, "id", "")) in locked and str(obj.id) not in bypass:
            if set(changes) - {"custom_fields", "score"}:
                raise AutomationBlocked(
                    423,
                    f"This record is waiting for approval ({locked[str(obj.id)]}) and can't be edited until it's decided.",
                    "record_locked",
                )

        if entity_type == "opportunity" and kind != "deleted":
            new_stage = str(obj.stage_id) if getattr(obj, "stage_id", None) else None
            old_stage = None
            if kind == "updated" and "stage_id" in changes:
                old_stage = changes["stage_id"]["from"]
            stage_moved = kind == "created" or "stage_id" in changes
            if stage_moved and enabled("approvals") and kind == "updated" and new_stage:
                _check_approvals(obj, context, new_stage, bypass)
            if stage_moved and enabled("process_management"):
                _check_stage_rules(obj, context, old_stage, new_stage)

        if kind != "deleted" and organization_id is not None and context.get("chat") and enabled("chat_integrations"):
            for event_name in _chat_events(kind, entity_type, obj, changes, context):
                _queue_chat(session, obj, entity_type, event_name, context)

        if (
            kind == "created"
            and entity_type in {"lead", "contact"}
            and context.get("journeys")
            and enabled("customer_journeys")
        ):
            for journey in context["journeys"]:
                if journey["object_type"] != entity_type:
                    continue
                if all(evaluate_condition(obj, condition) for condition in journey["conditions"]):
                    session.add(
                        JourneyEnrollment(
                            id=uuid.uuid4(),
                            organization_id=organization_id,
                            journey_id=journey["id"],
                            record_type=entity_type,
                            record_id=obj.id,
                            record_label=_label(obj) or None,
                            status="active",
                            current_step=0,
                            next_run_at=datetime.now(timezone.utc),
                            history=[],
                        )
                    )

        # Scoring runs before auditing so the score change is recorded.
        if entity_type in {"lead", "contact", "account"} and kind != "deleted" and scoring_rules:
            rules_for = [r for r in scoring_rules if (r.get("object_type") or "lead") == entity_type]
            if rules_for and (kind == "created" or set(changes) - {"score"}):
                new_score, _ = score_lead(obj, rules_for)
                if new_score != (obj.score or 0):
                    if kind == "updated":
                        changes["score"] = {"from": obj.score, "to": new_score}
                    obj.score = new_score

        if organization_id is not None:
            session.add(
                AuditLog(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    actor_user_id=actor,
                    action={"created": "create", "updated": "update", "deleted": "delete"}[kind],
                    entity_type=entity_type,
                    entity_id=getattr(obj, "id", None),
                    entity_label=_label(obj) or None,
                    changes=changes,
                    ip_address=session.info.get("ip"),
                    user_agent=session.info.get("user_agent"),
                )
            )

        if organization_id is not None and context.get("webhooks") and enabled("api_access"):
            # Outbox for Zapier / Make / n8n: queued in this transaction, sent by the scheduler.
            from app.integrations.providers.automation import queue_events

            queue_events(session, kind, entity_type, obj, dict(changes), context)

        if kind == "deleted" or not workflow_rules or entity_type not in OBJECTS:
            continue

        definition = OBJECTS[entity_type]
        for rule in workflow_rules:
            if not _rule_matches(rule, kind, entity_type, obj, changes):
                continue
            messages, status = [], "success"
            for action in rule.get("actions") or []:
                try:
                    messages.append(run_action(session, action, obj, definition, rule, actor))
                except Exception as exc:  # noqa: BLE001 - one bad action must not block the save
                    status = "error"
                    messages.append(f"{action.get('type')}: {exc}")
                    logger.warning("Workflow %s action failed: %s", rule["id"], exc)
            session.add(
                WorkflowRun(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    rule_id=rule["id"],
                    entity_type=entity_type,
                    entity_id=obj.id,
                    entity_label=_label(obj) or None,
                    status=status,
                    message="; ".join(messages)[:2000],
                )
            )


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _reset_created(session: Session) -> None:
    session.info.pop("aiva_created_ids", None)


def record_audit(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    entity_label: str | None = None,
    changes: dict[str, Any] | None = None,
    actor_user_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Explicit audit entry for events that aren't record changes (sign-ins, role changes)."""

    info = db.sync_session.info if hasattr(db, "sync_session") else {}
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            organization_id=organization_id,
            actor_user_id=actor_user_id if actor_user_id is not None else info.get("actor_user_id"),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=(entity_label or "")[:300] or None,
            changes=jsonable(changes or {}),
            ip_address=ip or info.get("ip"),
            user_agent=(user_agent or info.get("user_agent") or None),
        )
    )
