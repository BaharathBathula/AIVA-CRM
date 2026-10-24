"""AIVA as a Model Context Protocol (MCP) server.

AI assistants such as Claude connect to ``POST /mcp`` (Streamable HTTP
transport, JSON responses) with an AIVA API key as the Bearer token. They
can search and read CRM records and, when the workspace allows it and the
key is read/write, create leads, tasks and activity and update deals. Every
call runs with the key owner's permissions and is logged.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_permission
from app.core.config import get_settings
from app.db.session import get_db
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import ApiKey
from app.models.governance import McpCall
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.task import Task
from app.models.user import User
from app.services import workspace_settings
from app.services.lookups import names_for

router = APIRouter(tags=["MCP"])
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "aiva-crm", "title": "AIVA CRM", "version": "1.0.0"}


class ToolError(Exception):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _person(record: Any) -> str:
    return f"{record.first_name or ''} {record.last_name or ''}".strip() or getattr(record, "email", None) or "Unnamed"


def _number(value: Any, what: str, default: int, low: int, high: int) -> int:
    if value in (None, ""):
        return default
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{what} must be a whole number.") from exc


async def _member_id(db: AsyncSession, principal: Principal, email: Any) -> uuid.UUID:
    """A person in this workspace by email (people in other workspaces are never visible)."""

    from app.models.membership import OrganizationMembership

    member = (
        await db.execute(
            select(User.id)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == principal.organization_id,
                OrganizationMembership.status == "active",
                func.lower(User.email) == str(email).strip().lower(),
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise ToolError("No one in this workspace has that email.")
    return member


def _uuid(value: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ToolError(f"{what} must be an id like 3f2b…") from exc


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def tool_search(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if len(query) < 2:
        raise ToolError("Search for at least 2 characters.")
    types = args.get("types") or ["lead", "contact", "account", "opportunity"]
    like = f"%{query}%"
    org = principal.organization_id
    out: list[dict[str, Any]] = []
    if "lead" in types:
        for r in (await db.execute(select(Lead).where(Lead.organization_id == org, or_(Lead.first_name.ilike(like), Lead.last_name.ilike(like), Lead.email.ilike(like), Lead.company_name.ilike(like))).limit(10))).scalars():
            out.append({"type": "lead", "id": str(r.id), "name": _person(r), "company": r.company_name, "email": r.email, "status": r.status})
    if "contact" in types:
        for r in (await db.execute(select(Contact).where(Contact.organization_id == org, Contact.is_archived.is_(False), or_(Contact.first_name.ilike(like), Contact.last_name.ilike(like), Contact.email.ilike(like))).limit(10))).scalars():
            out.append({"type": "contact", "id": str(r.id), "name": _person(r), "email": r.email, "title": r.job_title})
    if "account" in types:
        for r in (await db.execute(select(Account).where(Account.organization_id == org, Account.is_archived.is_(False), or_(Account.name.ilike(like), Account.domain.ilike(like))).limit(10))).scalars():
            out.append({"type": "account", "id": str(r.id), "name": r.name, "industry": r.industry, "stage": r.lifecycle_stage})
    if "opportunity" in types:
        for r in (await db.execute(select(Opportunity).where(Opportunity.organization_id == org, Opportunity.name.ilike(like)).limit(10))).scalars():
            out.append({"type": "opportunity", "id": str(r.id), "name": r.name, "amount": _jsonable(r.amount), "close_date": _jsonable(r.expected_close_date)})
    return {"results": out, "count": len(out)}


MODELS = {"lead": Lead, "contact": Contact, "account": Account, "opportunity": Opportunity}
HIDDEN = {"organization_id", "custom_fields"}


async def tool_get_record(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("type") or "")
    model = MODELS.get(kind)
    if model is None:
        raise ToolError("type must be lead, contact, account or opportunity.")
    record = await db.get(model, _uuid(args.get("id"), "id"))
    if record is None or record.organization_id != principal.organization_id:
        raise ToolError("Record not found.")
    fields = {c.key: _jsonable(getattr(record, c.key)) for c in record.__table__.columns if c.key not in HIDDEN and getattr(record, c.key) not in (None, "", [], {})}
    custom = {k: v for k, v in (record.custom_fields or {}).items() if not k.startswith("_") and not (isinstance(v, str) and v.startswith("enc:"))}
    if custom:
        fields["custom_fields"] = custom
    column = {"lead": Activity.lead_id, "contact": Activity.contact_id, "account": Activity.account_id, "opportunity": Activity.opportunity_id}[kind]
    activities = (await db.execute(select(Activity).where(Activity.organization_id == principal.organization_id, column == record.id).order_by(Activity.occurred_at.desc()).limit(10))).scalars().all()
    fields["recent_activity"] = [{"type": a.activity_type, "subject": a.subject, "when": _jsonable(a.occurred_at), "notes": (a.body or "")[:400]} for a in activities]
    if kind == "opportunity":
        stage = await db.get(PipelineStage, record.stage_id)
        fields["stage"] = stage.name if stage else None
    if kind == "account":
        fields["open_deals"] = [
            {"id": str(o.id), "name": o.name, "amount": _jsonable(o.amount)}
            for o in (await db.execute(select(Opportunity).where(Opportunity.account_id == record.id).limit(20))).scalars()
        ]
    return fields


async def tool_open_deals(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    org = principal.organization_id
    stages = {s.id: s for s in (await db.execute(select(PipelineStage).where(PipelineStage.organization_id == org))).scalars()}
    open_ids = [sid for sid, s in stages.items() if s.category == "open"]
    query = select(Opportunity).where(Opportunity.organization_id == org, Opportunity.stage_id.in_(open_ids or [uuid.uuid4()]))
    if args.get("owner_email"):
        query = query.where(Opportunity.owner_user_id == await _member_id(db, principal, args["owner_email"]))
    if args.get("closing_within_days"):
        query = query.where(Opportunity.expected_close_date <= date.today() + timedelta(days=_number(args["closing_within_days"], "closing_within_days", 30, 0, 3650)))
    limit = _number(args.get("limit"), "limit", 25, 1, 100)
    rows = (await db.execute(query.order_by(Opportunity.amount.desc().nulls_last()).limit(limit))).scalars().all()
    owners = await names_for(db, org, "user", [r.owner_user_id for r in rows])
    accounts = await names_for(db, org, "account", [r.account_id for r in rows])
    return {
        "deals": [
            {"id": str(r.id), "name": r.name, "account": accounts.get(str(r.account_id)), "amount": _jsonable(r.amount), "stage": stages[r.stage_id].name if r.stage_id in stages else None,
             "probability": r.probability, "close_date": _jsonable(r.expected_close_date), "owner": owners.get(str(r.owner_user_id)), "next_step": r.next_step}
            for r in rows
        ]
    }


async def tool_pipeline(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    org = principal.organization_id
    rows = (
        await db.execute(
            select(PipelineStage.name, PipelineStage.category, PipelineStage.position, func.count(Opportunity.id), func.coalesce(func.sum(Opportunity.amount), 0))
            .outerjoin(Opportunity, Opportunity.stage_id == PipelineStage.id)
            .where(PipelineStage.organization_id == org)
            .group_by(PipelineStage.id)
            .order_by(PipelineStage.position)
        )
    ).all()
    return {"stages": [{"stage": n, "category": c, "deals": int(count), "amount": float(total)} for n, c, _, count, total in rows]}


async def tool_tasks(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    query = select(Task).where(Task.organization_id == principal.organization_id, Task.status.in_(("open", "in_progress")))
    if args.get("mine", True):
        query = query.where(Task.assigned_to_user_id == principal.user_id)
    if args.get("overdue_only"):
        query = query.where(Task.due_at < datetime.now(timezone.utc))
    rows = (await db.execute(query.order_by(Task.due_at.asc().nulls_last()).limit(50))).scalars().all()
    return {"tasks": [{"id": str(t.id), "title": t.title, "due": _jsonable(t.due_at), "priority": t.priority, "type": t.task_type} for t in rows]}


async def tool_meetings(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    days = _number(args.get("days_ahead"), "days_ahead", 7, 1, 60)
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(Meeting).where(Meeting.organization_id == principal.organization_id, Meeting.start_at >= now - timedelta(hours=1), Meeting.start_at <= now + timedelta(days=days), Meeting.status == "scheduled").order_by(Meeting.start_at).limit(50)
        )
    ).scalars().all()
    return {"meetings": [{"id": str(m.id), "title": m.title, "start": _jsonable(m.start_at), "attendees": [a.get("name") or a.get("email") for a in m.attendees or []]} for m in rows]}


def _require_write(principal: Principal) -> None:
    if not principal.can("records.write"):
        raise ToolError("Your role can't change records.")


async def tool_create_lead(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    _require_write(principal)
    from app.schemas.lead import LeadCreate
    from app.services import prospecting
    from app.services.leads import create_lead

    try:
        payload = LeadCreate(**{k: v for k, v in args.items() if k in LeadCreate.model_fields})
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Check the lead details: {exc}") from exc
    try:
        duplicates = await prospecting.check_new_person(db, principal.organization_id, "lead", email=payload.email, first_name=payload.first_name, last_name=payload.last_name, company=payload.company_name)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc
    lead = await create_lead(db, principal.organization_id, payload)
    return {"id": str(lead.id), "name": _person(lead), "possible_duplicates": duplicates, "url": f"{get_settings().app_base_url.rstrip('/')}/leads/{lead.id}"}


async def tool_create_task(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    _require_write(principal)
    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolError("Give the task a title.")
    due = None
    if args.get("due_date"):
        try:
            due = datetime.fromisoformat(str(args["due_date"])).replace(tzinfo=timezone.utc) if "T" in str(args["due_date"]) else datetime.combine(date.fromisoformat(str(args["due_date"])), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=17)
        except ValueError as exc:
            raise ToolError("due_date must look like 2026-11-03.") from exc
    links: dict[str, uuid.UUID] = {}
    if args.get("related_type") and args.get("related_id"):
        kind = str(args["related_type"])
        model = MODELS.get(kind)
        record = await db.get(model, _uuid(args["related_id"], "related_id")) if model else None
        if record is None or record.organization_id != principal.organization_id:
            raise ToolError("The related record wasn't found.")
        links[f"{kind}_id"] = record.id
    assignee = principal.user_id
    if args.get("assign_to_email"):
        assignee = await _member_id(db, principal, args["assign_to_email"])
    task = Task(id=uuid.uuid4(), organization_id=principal.organization_id, title=title[:250], description=args.get("description"), task_type="follow_up", status="open",
                priority=str(args.get("priority") or "medium") if args.get("priority") in ("low", "medium", "high", "urgent") else "medium", source="ai", due_at=due,
                assigned_to_user_id=assignee, created_by_user_id=principal.user_id, is_recurring=False, is_ai_generated=True, tags=["mcp"], task_metadata={"source": "mcp"}, **links)
    db.add(task)
    await db.commit()
    return {"id": str(task.id), "title": task.title, "due": _jsonable(task.due_at)}


async def tool_log_activity(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    _require_write(principal)
    kind = str(args.get("activity_type") or "note")
    if kind not in ("note", "call", "email", "meeting"):
        raise ToolError("activity_type must be note, call, email or meeting.")
    model = MODELS.get(str(args.get("related_type") or ""))
    record = await db.get(model, _uuid(args.get("related_id"), "related_id")) if model else None
    if record is None or record.organization_id != principal.organization_id:
        raise ToolError("Say which record it's about (related_type and related_id).")
    key = f"{args['related_type']}_id"
    extra = {"account_id": record.account_id} if args["related_type"] in ("contact", "opportunity") and getattr(record, "account_id", None) else {}
    activity = Activity(id=uuid.uuid4(), organization_id=principal.organization_id, created_by_user_id=principal.user_id, activity_type=kind,
                        subject=str(args.get("subject") or kind.title())[:250], body=args.get("body"), occurred_at=datetime.now(timezone.utc),
                        activity_metadata={"source": "mcp"}, **{key: record.id, **extra})
    db.add(activity)
    await db.commit()
    return {"id": str(activity.id), "subject": activity.subject}


async def tool_update_deal(db: AsyncSession, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    _require_write(principal)
    deal = await db.get(Opportunity, _uuid(args.get("id"), "id"))
    if deal is None or deal.organization_id != principal.organization_id:
        raise ToolError("Deal not found.")
    changed = []
    if args.get("stage"):
        stage = (
            await db.execute(select(PipelineStage).where(PipelineStage.pipeline_id == deal.pipeline_id, func.lower(PipelineStage.name) == str(args["stage"]).lower()))
        ).scalar_one_or_none()
        if stage is None:
            raise ToolError("That stage isn't in the deal's pipeline.")
        deal.stage_id = stage.id
        changed.append("stage")
    if args.get("amount") is not None:
        try:
            amount = Decimal(str(args["amount"]))
        except (ArithmeticError, ValueError) as exc:
            raise ToolError("amount must be a number, like 25000.") from exc
        if not amount.is_finite() or amount < 0:
            raise ToolError("amount must be zero or more.")
        deal.amount = amount
        changed.append("amount")
    if args.get("next_step") is not None:
        deal.next_step = str(args["next_step"])[:500]
        changed.append("next_step")
    if args.get("close_date"):
        try:
            deal.expected_close_date = date.fromisoformat(str(args["close_date"]))
        except ValueError as exc:
            raise ToolError("close_date must look like 2026-11-30.") from exc
        changed.append("close_date")
    if not changed:
        raise ToolError("Nothing to change. Pass stage, amount, next_step or close_date.")
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - e.g. a stage playbook or approval rule stopped the change
        await db.rollback()
        raise ToolError(getattr(exc, "detail", None) or str(exc)) from exc
    return {"id": str(deal.id), "changed": changed}


TOOLS: dict[str, dict[str, Any]] = {
    "search_records": {
        "handler": tool_search, "write": False,
        "description": "Search AIVA leads, contacts, accounts and deals by name, email, company or domain.",
        "schema": {"type": "object", "properties": {"query": {"type": "string"}, "types": {"type": "array", "items": {"type": "string", "enum": ["lead", "contact", "account", "opportunity"]}}}, "required": ["query"]},
    },
    "get_record": {
        "handler": tool_get_record, "write": False,
        "description": "Get every field and the recent activity for one lead, contact, account or deal.",
        "schema": {"type": "object", "properties": {"type": {"type": "string", "enum": ["lead", "contact", "account", "opportunity"]}, "id": {"type": "string"}}, "required": ["type", "id"]},
    },
    "list_open_deals": {
        "handler": tool_open_deals, "write": False,
        "description": "Open deals, biggest first. Optionally filter by owner email or close date.",
        "schema": {"type": "object", "properties": {"owner_email": {"type": "string"}, "closing_within_days": {"type": "integer"}, "limit": {"type": "integer"}}},
    },
    "pipeline_summary": {
        "handler": tool_pipeline, "write": False,
        "description": "Deal counts and amounts by pipeline stage.",
        "schema": {"type": "object", "properties": {}},
    },
    "list_tasks": {
        "handler": tool_tasks, "write": False,
        "description": "Open tasks (yours by default).",
        "schema": {"type": "object", "properties": {"mine": {"type": "boolean"}, "overdue_only": {"type": "boolean"}}},
    },
    "upcoming_meetings": {
        "handler": tool_meetings, "write": False,
        "description": "Scheduled meetings in the next few days.",
        "schema": {"type": "object", "properties": {"days_ahead": {"type": "integer"}}},
    },
    "create_lead": {
        "handler": tool_create_lead, "write": True,
        "description": "Create a lead. Duplicate and do-not-contact rules apply.",
        "schema": {"type": "object", "properties": {"first_name": {"type": "string"}, "last_name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"},
                                                     "company_name": {"type": "string"}, "job_title": {"type": "string"}, "source": {"type": "string"}, "notes": {"type": "string"}},
                   "required": ["first_name", "last_name"]},
    },
    "create_task": {
        "handler": tool_create_task, "write": True,
        "description": "Create a follow-up task, optionally linked to a record and assigned to a teammate.",
        "schema": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}, "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                                                     "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]}, "related_type": {"type": "string", "enum": ["lead", "contact", "account", "opportunity"]},
                                                     "related_id": {"type": "string"}, "assign_to_email": {"type": "string"}}, "required": ["title"]},
    },
    "log_activity": {
        "handler": tool_log_activity, "write": True,
        "description": "Log a note, call, email or meeting on a record's timeline.",
        "schema": {"type": "object", "properties": {"activity_type": {"type": "string", "enum": ["note", "call", "email", "meeting"]}, "subject": {"type": "string"}, "body": {"type": "string"},
                                                     "related_type": {"type": "string", "enum": ["lead", "contact", "account", "opportunity"]}, "related_id": {"type": "string"}},
                   "required": ["related_type", "related_id", "subject"]},
    },
    "update_deal": {
        "handler": tool_update_deal, "write": True,
        "description": "Move a deal to another stage or change its amount, next step or close date. Stage playbooks and approvals still apply.",
        "schema": {"type": "object", "properties": {"id": {"type": "string"}, "stage": {"type": "string"}, "amount": {"type": "number"}, "next_step": {"type": "string"},
                                                     "close_date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["id"]},
    },
}


async def _available(db: AsyncSession, principal: Principal) -> tuple[workspace_settings.McpSettings, bool, list[str]]:
    settings = await workspace_settings.get(db, principal.organization_id, "mcp")
    key_writes = True
    if principal.api_key_id:
        key = await db.get(ApiKey, principal.api_key_id)
        key_writes = bool(key and key.scope != "read")
    can_read = principal.can("records.read")
    names = [
        name for name, tool in TOOLS.items()
        if can_read
        and name not in settings.disabled_tools
        and (not tool["write"] or (settings.allow_write and key_writes and principal.can("records.write")))
    ]
    return settings, key_writes, names


def _rpc(result: Any = None, *, id_: Any, error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, **({"error": error} if error else {"result": result})}


async def handle_message(db: AsyncSession, principal: Principal, message: dict[str, Any], client: str | None) -> dict[str, Any] | None:
    method = message.get("method")
    id_ = message.get("id")
    params = message.get("params") or {}
    if not isinstance(method, str):
        return _rpc(id_=id_, error={"code": -32600, "message": "Invalid request."})
    if id_ is None:
        return None  # notifications (e.g. notifications/initialized) need no answer
    if method == "initialize":
        requested = params.get("protocolVersion")
        organization = await db.get(Organization, principal.organization_id)
        return _rpc(
            {
                "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": f"You're connected to the {organization.name if organization else 'AIVA'} workspace in AIVA CRM as {principal.full_name}. "
                "Search before you create, and link tasks and activity to the right record.",
            },
            id_=id_,
        )
    if method == "ping":
        return _rpc({}, id_=id_)
    if method == "tools/list":
        _settings, _writes, names = await _available(db, principal)
        return _rpc(
            {
                "tools": [
                    {"name": n, "description": TOOLS[n]["description"], "inputSchema": TOOLS[n]["schema"],
                     "annotations": {"readOnlyHint": not TOOLS[n]["write"], "destructiveHint": False}}
                    for n in names
                ]
            },
            id_=id_,
        )
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        started = time.monotonic()
        _settings, key_writes, names = await _available(db, principal)
        if name not in TOOLS:
            return _rpc(id_=id_, error={"code": -32602, "message": f"Unknown tool: {name}"})
        status_value, error = "ok", None
        if name not in names:
            reason = (
                "Write tools are turned off for this workspace (Settings → MCP)." if TOOLS[name]["write"] and not _settings.allow_write
                else "This API key is read-only." if TOOLS[name]["write"] and not key_writes
                else "This tool is turned off for this workspace."
            )
            result = {"content": [{"type": "text", "text": reason}], "isError": True}
            status_value, error = "denied", reason
        else:
            try:
                data = await TOOLS[name]["handler"](db, principal, args if isinstance(args, dict) else {})
                result = {"content": [{"type": "text", "text": json.dumps(data, default=_jsonable)}], "structuredContent": json.loads(json.dumps(data, default=_jsonable)), "isError": False}
            except ToolError as exc:
                await db.rollback()
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
                status_value, error = "error", str(exc)
            except (ValueError, TypeError, ArithmeticError) as exc:
                # Bad arguments the tool didn't anticipate: report them instead of failing the request.
                await db.rollback()
                message = f"Those arguments aren't valid ({exc.__class__.__name__})."
                result = {"content": [{"type": "text", "text": message}], "isError": True}
                status_value, error = "error", message
        db.add(McpCall(id=uuid.uuid4(), organization_id=principal.organization_id, user_id=principal.user_id, api_key_id=principal.api_key_id, method=method,
                       tool=str(name)[:80], status=status_value, error=error, duration_ms=int((time.monotonic() - started) * 1000), client_name=(client or "")[:120] or None))
        await db.commit()
        return _rpc(result, id_=id_)
    return _rpc(id_=id_, error={"code": -32601, "message": f"Method not found: {method}"})


@router.post("/mcp")
async def mcp_endpoint(request: Request, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    settings = await workspace_settings.get(db, principal.organization_id, "mcp")
    if not settings.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "MCP access is turned off for this workspace (Settings → MCP).")
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc(id_=None, error={"code": -32700, "message": "Parse error."}), status_code=400)
    client = request.headers.get("user-agent")
    if isinstance(body, list):
        replies = [r for r in [await handle_message(db, principal, m, client) for m in body if isinstance(m, dict)] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=status.HTTP_202_ACCEPTED)
    if not isinstance(body, dict):
        return JSONResponse(_rpc(id_=None, error={"code": -32600, "message": "Invalid request."}), status_code=400)
    if body.get("method") == "initialize":
        params = body.get("params") or {}
        info = params.get("clientInfo") or {}
        client = f"{info.get('name', '')} {info.get('version', '')}".strip() or client
        db.add(McpCall(id=uuid.uuid4(), organization_id=principal.organization_id, user_id=principal.user_id, api_key_id=principal.api_key_id, method="initialize",
                       status="ok", client_name=(client or "")[:120] or None))
        await db.commit()
    reply = await handle_message(db, principal, body, client)
    if reply is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(reply)


@router.get("/mcp")
async def mcp_get():
    # No server-initiated stream: this server answers each POST with JSON.
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, headers={"Allow": "POST"})


@router.get("/mcp/overview")
async def mcp_overview(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    settings = await workspace_settings.get(db, principal.organization_id, "mcp")
    calls = (await db.execute(select(McpCall).where(McpCall.organization_id == principal.organization_id).order_by(McpCall.created_at.desc()).limit(50))).scalars().all()
    names = await names_for(db, principal.organization_id, "user", [c.user_id for c in calls])
    since = datetime.now(timezone.utc) - timedelta(days=30)
    usage = dict(
        (
            await db.execute(
                select(McpCall.tool, func.count()).where(McpCall.organization_id == principal.organization_id, McpCall.created_at >= since, McpCall.tool.is_not(None)).group_by(McpCall.tool)
            )
        ).all()
    )
    base = get_settings().app_base_url.rstrip("/")
    return {
        "settings": settings.model_dump(mode="json"),
        "endpoint": f"{base}/api/aiva/mcp",
        "tools": [{"name": n, "description": t["description"], "write": t["write"], "calls_30_days": int(usage.get(n, 0)), "enabled": n not in settings.disabled_tools} for n, t in TOOLS.items()],
        "calls": [
            {"at": c.created_at, "user": names.get(str(c.user_id)), "method": c.method, "tool": c.tool, "status": c.status, "error": c.error, "duration_ms": c.duration_ms, "client": c.client_name}
            for c in calls
        ],
        "can_edit": principal.can("admin.settings"),
        "has_api_access": principal.has("api_access"),
    }
