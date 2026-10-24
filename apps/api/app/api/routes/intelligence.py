"""AI agents, predictions, Ask AIVA, summaries and drafts."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import AiSuggestion
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Case, CaseComment
from app.models.task import Task
from app.services import ai, ai_agents, ai_context, metering, workspace_settings
from app.services.lookups import names_for
from app.services.predictions import last_activities, predict, predict_open, train

router = APIRouter(tags=["Intelligence"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _quarter_start(day: date) -> date:
    return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)


# ===========================================================================
# Agents and suggestions
# ===========================================================================


@router.get("/ai/agents", dependencies=[Depends(require_feature("ai_agents"))])
async def list_agents(
    principal: Principal = Depends(require_permission("automation.ai")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    settings = ai_agents.agent_settings(organization)
    counts = dict(
        (
            await db.execute(
                select(AiSuggestion.agent, func.count())
                .where(AiSuggestion.organization_id == principal.organization_id, AiSuggestion.status == "open")
                .group_by(AiSuggestion.agent)
            )
        ).all()
    )
    accepted = dict(
        (
            await db.execute(
                select(AiSuggestion.agent, func.count())
                .where(AiSuggestion.organization_id == principal.organization_id, AiSuggestion.status == "accepted")
                .group_by(AiSuggestion.agent)
            )
        ).all()
    )
    return {
        "claude": ai.configured(),
        "agents": [
            {
                "key": key,
                **meta,
                "enabled": settings[key]["enabled"],
                "last_run_at": settings[key]["last_run_at"],
                "open_suggestions": counts.get(key, 0),
                "accepted": accepted.get(key, 0),
            }
            for key, meta in ai_agents.AGENTS.items()
        ],
    }


class AgentToggle(BaseModel):
    enabled: bool


@router.put("/ai/agents/{agent}", dependencies=[Depends(require_feature("ai_agents"))])
async def toggle_agent(
    agent: str,
    payload: AgentToggle,
    principal: Principal = Depends(require_permission("automation.workflows")),
    db: AsyncSession = Depends(get_db),
):
    if agent not in ai_agents.AGENTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent.")
    organization = await db.get(Organization, principal.organization_id)
    stored = dict((organization.settings or {}).get("ai_agents") or {})
    stored[agent] = {**(stored.get(agent) or {}), "enabled": payload.enabled}
    organization.settings = {**(organization.settings or {}), "ai_agents": stored}
    await db.commit()
    return {"enabled": payload.enabled}


@router.post("/ai/agents/{agent}/run", dependencies=[Depends(require_feature("ai_agents"))])
async def run_agent_now(
    agent: str,
    principal: Principal = Depends(require_permission("automation.ai")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    created = await ai_agents.run_agent(db, organization, agent)
    await db.commit()
    return {"created": created, "message": f"{ai_agents.AGENTS[agent]['name']} found {created} new suggestion{'s' if created != 1 else ''}."}


def suggestion_out(s: AiSuggestion, owners: dict[str, str]) -> dict[str, Any]:
    links = {"lead": "/leads/", "contact": "/contacts/", "opportunity": "/opportunities/", "case": "/cases/", "account": "/accounts/"}
    return {
        "id": str(s.id),
        "agent": s.agent,
        "agent_name": ai_agents.AGENTS.get(s.agent, {}).get("name", s.agent),
        "record_type": s.record_type,
        "record_id": str(s.record_id),
        "record_label": s.record_label,
        "link": f"{links.get(s.record_type, '/')}{s.record_id}",
        "title": s.title,
        "body": s.body,
        "action": s.action,
        "confidence": s.confidence,
        "status": s.status,
        "source": s.source,
        "owner_name": owners.get(str(s.owner_user_id)),
        "owner_user_id": str(s.owner_user_id) if s.owner_user_id else None,
        "created_at": s.created_at,
        "resolved_at": s.resolved_at,
    }


@router.get("/ai/suggestions", dependencies=[Depends(require_feature("ai_agents"))])
async def list_suggestions(
    status_filter: str = Query(default="open", alias="status", pattern="^(open|accepted|dismissed|all)$"),
    agent: str | None = None,
    mine: bool = False,
    record_type: str | None = None,
    record_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("automation.ai")),
    db: AsyncSession = Depends(get_db),
):
    query = select(AiSuggestion).where(AiSuggestion.organization_id == principal.organization_id)
    if status_filter != "all":
        query = query.where(AiSuggestion.status == status_filter)
    if agent:
        query = query.where(AiSuggestion.agent == agent)
    if mine:
        query = query.where((AiSuggestion.owner_user_id == principal.user_id) | (AiSuggestion.owner_user_id.is_(None)))
    if record_id:
        query = query.where(AiSuggestion.record_id == record_id)
    if record_type:
        query = query.where(AiSuggestion.record_type == record_type)
    rows = (await db.execute(query.order_by(AiSuggestion.confidence.desc(), AiSuggestion.created_at.desc()).limit(200))).scalars().all()
    owners = await names_for(db, principal.organization_id, "user", [s.owner_user_id for s in rows])
    return [suggestion_out(s, owners) for s in rows]


async def _suggestion(db: AsyncSession, principal: Principal, suggestion_id: uuid.UUID) -> AiSuggestion:
    suggestion = await db.get(AiSuggestion, suggestion_id)
    if suggestion is None or suggestion.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found.")
    if suggestion.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This suggestion was already {suggestion.status}.")
    return suggestion


@router.post("/ai/suggestions/{suggestion_id}/accept", dependencies=[Depends(require_feature("ai_agents"))])
async def accept_suggestion(
    suggestion_id: uuid.UUID,
    principal: Principal = Depends(require_permission("automation.ai", "records.write")),
    db: AsyncSession = Depends(get_db),
):
    suggestion = await _suggestion(db, principal, suggestion_id)
    message = await ai_agents.accept(db, suggestion, principal.user_id)
    await db.commit()
    return {"message": message}


@router.post("/ai/suggestions/{suggestion_id}/dismiss", dependencies=[Depends(require_feature("ai_agents"))])
async def dismiss_suggestion(
    suggestion_id: uuid.UUID,
    principal: Principal = Depends(require_permission("automation.ai")),
    db: AsyncSession = Depends(get_db),
):
    suggestion = await _suggestion(db, principal, suggestion_id)
    suggestion.status = "dismissed"
    suggestion.resolved_by_user_id = principal.user_id
    suggestion.resolved_at = utcnow()
    await db.commit()
    return {"message": "Dismissed."}


# ===========================================================================
# Predictions
# ===========================================================================


@router.get("/predictions", dependencies=[Depends(require_feature("predictive_intelligence"))])
async def predictions(
    mine: bool = False,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    owner = principal.user_id if (mine or not principal.can("records.view_all")) else None
    model, rows = await predict_open(db, principal.organization_id, owner)
    accounts = await names_for(db, principal.organization_id, "account", [opp.account_id for opp, _ in rows])
    owners = await names_for(db, principal.organization_id, "user", [opp.owner_user_id for opp, _ in rows])
    stages = await names_for(db, principal.organization_id, "stage", [opp.stage_id for opp, _ in rows])
    today = date.today()
    start = _quarter_start(today)
    next_month = start.month + 3
    quarter_end = date(start.year + (next_month > 12), (next_month - 1) % 12 + 1, 1) - timedelta(days=1)
    items = []
    for opp, prediction in rows:
        amount = float(opp.amount or 0)
        items.append(
            {
                "id": str(opp.id),
                "name": opp.name,
                "account_name": accounts.get(str(opp.account_id)),
                "owner_name": owners.get(str(opp.owner_user_id)),
                "stage_name": stages.get(str(opp.stage_id)),
                "amount": amount,
                "expected_close_date": opp.expected_close_date,
                "stage_probability": opp.probability,
                "weighted": round(amount * prediction.probability / 100, 2),
                **prediction.to_dict(),
            }
        )
    items.sort(key=lambda item: -item["weighted"])
    in_quarter = [i for i in items if i["predicted_close_date"] and i["predicted_close_date"] <= quarter_end]
    return {
        "model": model.to_dict(),
        "summary": {
            "open_deals": len(items),
            "pipeline": sum(i["amount"] for i in items),
            "predicted_revenue": round(sum(i["weighted"] for i in items), 2),
            "predicted_this_quarter": round(sum(i["weighted"] for i in in_quarter), 2),
            "stage_weighted": round(sum(i["amount"] * (i["stage_probability"] or 0) / 100 for i in items), 2),
            "likely": len([i for i in items if i["label"] == "likely"]),
            "possible": len([i for i in items if i["label"] == "possible"]),
            "at_risk": len([i for i in items if i["label"] == "at_risk"]),
            "quarter_end": quarter_end,
        },
        "items": items,
    }


@router.get("/predictions/opportunities/{opportunity_id}", dependencies=[Depends(require_feature("predictive_intelligence"))])
async def opportunity_prediction(
    opportunity_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    opp = await db.get(Opportunity, opportunity_id)
    if opp is None or opp.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found.")
    stage = await db.get(PipelineStage, opp.stage_id)
    if stage is not None and stage.category != "open":
        return {"closed": True, "outcome": stage.category}
    model = await train(db, principal.organization_id)
    activity = await last_activities(db, principal.organization_id, [opp])
    prediction = predict(model, opp, activity.get(opp.id), stage.probability if stage else None)
    return {"closed": False, "model": model.to_dict(), **prediction.to_dict()}


# ===========================================================================
# Ask AIVA, summaries and drafts (extended AI)
# ===========================================================================


async def crm_snapshot(db: AsyncSession, principal: Principal) -> dict[str, Any]:
    org = principal.organization_id
    today = date.today()
    quarter_start = datetime.combine(_quarter_start(today), datetime.min.time(), tzinfo=timezone.utc)
    month_end = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    stages = {s.id: s for s in (await db.execute(select(PipelineStage).where(PipelineStage.organization_id == org))).scalars()}
    open_ids = [sid for sid, s in stages.items() if s.category == "open"] or [uuid.uuid4()]
    won_ids = [sid for sid, s in stages.items() if s.category == "won"] or [uuid.uuid4()]

    open_opps = (await db.execute(select(Opportunity).where(Opportunity.organization_id == org, Opportunity.stage_id.in_(open_ids)))).scalars().all()
    won_q = (
        await db.execute(
            select(Opportunity).where(
                Opportunity.organization_id == org, Opportunity.stage_id.in_(won_ids), Opportunity.closed_at >= quarter_start
            )
        )
    ).scalars().all()
    owners = await names_for(db, org, "user", [o.owner_user_id for o in open_opps + won_q])
    accounts = await names_for(db, org, "account", [o.account_id for o in open_opps])
    by_stage: dict[str, dict[str, float]] = {}
    for opp in open_opps:
        name = stages[opp.stage_id].name if opp.stage_id in stages else "Unknown"
        bucket = by_stage.setdefault(name, {"count": 0, "amount": 0.0})
        bucket["count"] += 1
        bucket["amount"] += float(opp.amount or 0)
    leaderboard: dict[str, float] = {}
    for opp in won_q:
        leaderboard[owners.get(str(opp.owner_user_id), "Unassigned")] = leaderboard.get(owners.get(str(opp.owner_user_id), "Unassigned"), 0) + float(opp.amount or 0)
    closing = [o for o in open_opps if o.expected_close_date and today <= o.expected_close_date < month_end]
    overdue_tasks = (
        await db.execute(
            select(func.count()).select_from(Task).where(
                Task.organization_id == org, Task.status.in_(("open", "in_progress")), Task.due_at < utcnow()
            )
        )
    ).scalar_one()
    my_tasks = (
        await db.execute(
            select(func.count()).select_from(Task).where(
                Task.organization_id == org, Task.status.in_(("open", "in_progress")), Task.assigned_to_user_id == principal.user_id
            )
        )
    ).scalar_one()
    cases = dict(
        (
            await db.execute(
                select(Case.priority, func.count())
                .where(Case.organization_id == org, Case.status.in_(("new", "open", "pending", "on_hold")))
                .group_by(Case.priority)
            )
        ).all()
    )
    breached = (
        await db.execute(
            select(func.count()).select_from(Case).where(
                Case.organization_id == org,
                Case.status.in_(("new", "open", "pending", "on_hold")),
                Case.resolution_due_at < utcnow(),
            )
        )
    ).scalar_one()
    week = utcnow() - timedelta(days=7)
    leads = dict(
        (
            await db.execute(select(Lead.source, func.count()).where(Lead.organization_id == org, Lead.created_at >= week).group_by(Lead.source))
        ).all()
    )
    _, predictions = await predict_open(db, org)
    at_risk = sorted(
        [(opp, p) for opp, p in predictions if p.label == "at_risk"], key=lambda pair: -float(pair[0].amount or 0)
    )[:5]
    return {
        "today": today.isoformat(),
        "open_pipeline": {"deals": len(open_opps), "amount": round(sum(float(o.amount or 0) for o in open_opps), 2), "by_stage": by_stage},
        "won_this_quarter": {"deals": len(won_q), "amount": round(sum(float(o.amount or 0) for o in won_q), 2)},
        "leaderboard_this_quarter": dict(sorted(leaderboard.items(), key=lambda item: -item[1])),
        "closing_this_month": [
            {"name": o.name, "account": accounts.get(str(o.account_id)), "amount": float(o.amount or 0), "close": o.expected_close_date.isoformat()}
            for o in sorted(closing, key=lambda o: o.expected_close_date)[:10]
        ],
        "top_open_deals": [
            {"name": o.name, "account": accounts.get(str(o.account_id)), "amount": float(o.amount or 0), "owner": owners.get(str(o.owner_user_id))}
            for o in sorted(open_opps, key=lambda o: -float(o.amount or 0))[:5]
        ],
        "at_risk_deals": [
            {"name": o.name, "amount": float(o.amount or 0), "probability": p.probability, "why": [f["label"] for f in p.factors if f["impact"] == "negative"][:3]}
            for o, p in at_risk
        ],
        "tasks": {"overdue": overdue_tasks, "mine_open": my_tasks},
        "open_cases_by_priority": cases,
        "cases_past_resolution_target": breached,
        "new_leads_last_7_days_by_source": leads,
    }


def _money(value: float) -> str:
    return f"${value:,.0f}"


def rules_answer(question: str, data: dict[str, Any]) -> tuple[str, list[str]]:
    q = question.lower()
    answers: list[str] = []
    used: list[str] = []
    pipeline = data["open_pipeline"]
    if any(word in q for word in ("pipeline", "stage", "open deals", "open opportunities")):
        stages = ", ".join(f"{name}: {v['count']} ({_money(v['amount'])})" for name, v in pipeline["by_stage"].items())
        answers.append(f"You have {pipeline['deals']} open deals worth {_money(pipeline['amount'])}. By stage — {stages or 'none yet'}.")
        used.append("open_pipeline")
    if any(word in q for word in ("won", "revenue", "closed", "booked", "sales this")):
        won = data["won_this_quarter"]
        answers.append(f"This quarter you've won {won['deals']} deals for {_money(won['amount'])}.")
        used.append("won_this_quarter")
    if any(word in q for word in ("who", "rep", "leader", "best", "top seller", "top rep")):
        board = data["leaderboard_this_quarter"]
        if board:
            ranking = "; ".join(f"{i + 1}. {name} {_money(amount)}" for i, (name, amount) in enumerate(board.items()))
            answers.append(f"Won revenue by rep this quarter: {ranking}.")
        else:
            answers.append("No deals have been won yet this quarter.")
        used.append("leaderboard_this_quarter")
    if any(word in q for word in ("risk", "slipping", "stuck", "worry", "attention")):
        risky = data["at_risk_deals"]
        if risky:
            answers.append(
                "Deals at risk: " + "; ".join(f"{d['name']} ({_money(d['amount'])}, {d['probability']}% likely — {', '.join(d['why']).lower() or 'limited history'})" for d in risky) + "."
            )
        else:
            answers.append("No large deals are flagged as at risk right now.")
        used.append("at_risk_deals")
    if any(word in q for word in ("this month", "closing", "close soon", "forecast", "commit")):
        closing = data["closing_this_month"]
        if closing:
            answers.append(
                f"{len(closing)} deals are set to close this month: " + "; ".join(f"{d['name']} {_money(d['amount'])} on {d['close']}" for d in closing[:6]) + "."
            )
        else:
            answers.append("No open deals have a close date later this month.")
        used.append("closing_this_month")
    if any(word in q for word in ("biggest", "largest", "top deal", "top deals")):
        answers.append("Largest open deals: " + "; ".join(f"{d['name']} {_money(d['amount'])} ({d['owner'] or 'unassigned'})" for d in data["top_open_deals"]) + ".")
        used.append("top_open_deals")
    if any(word in q for word in ("task", "overdue", "to do", "todo")):
        answers.append(f"There are {data['tasks']['overdue']} overdue tasks in the workspace; you have {data['tasks']['mine_open']} open tasks.")
        used.append("tasks")
    if any(word in q for word in ("case", "ticket", "support", "sla")):
        cases = data["open_cases_by_priority"]
        breakdown = ", ".join(f"{count} {priority}" for priority, count in cases.items()) or "none"
        answers.append(f"Open cases: {breakdown}. {data['cases_past_resolution_target']} are past their resolution target.")
        used.append("open_cases_by_priority")
    if "lead" in q:
        leads = data["new_leads_last_7_days_by_source"]
        total = sum(leads.values())
        detail = ", ".join(f"{source.replace('_', ' ')}: {count}" for source, count in leads.items())
        answers.append(f"{total} new leads in the last 7 days{f' ({detail})' if detail else ''}.")
        used.append("new_leads_last_7_days_by_source")
    if not answers:
        won = data["won_this_quarter"]
        answers.append(
            f"Here's where things stand: {pipeline['deals']} open deals worth {_money(pipeline['amount'])}, "
            f"{won['deals']} deals won this quarter for {_money(won['amount'])}, {data['tasks']['overdue']} overdue tasks and "
            f"{sum(data['open_cases_by_priority'].values())} open cases. Ask about pipeline, won revenue, reps, risky deals, tasks, cases or leads."
        )
        used.append("overview")
    return " ".join(answers), used


class AskPayload(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@router.post("/ai/ask", dependencies=[Depends(require_feature("extended_ai"))])
async def ask_aiva(
    payload: AskPayload,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    await metering.ensure_ai_words(db, principal.organization_id, principal.user_id)
    data = await crm_snapshot(db, principal)
    answer = await ai.complete(
        "Answer the question using only this CRM data (JSON). Use dollar amounts and names from the data. "
        "Keep it under 150 words. If the data doesn't cover the question, say what you can answer instead.\n\n"
        f"Question: {payload.question}\n\nData:\n{json.dumps(data, default=str)}",
        system=await ai_context.system_prompt(db, principal.organization_id, "ask"),
        max_tokens=500,
    )
    if answer:
        metering.record_ai_words(db, principal.organization_id, principal.user_id, "ask", answer, "claude")
        await db.commit()
        return {"answer": answer, "source": "claude", "data": data}
    text, used = rules_answer(payload.question, data)
    metering.record_ai_words(db, principal.organization_id, principal.user_id, "ask", text, "rules")
    await db.commit()
    return {"answer": text, "source": "rules", "used": used, "data": data}


SUMMARY_MODELS = {"lead": Lead, "contact": Contact, "account": Account, "opportunity": Opportunity, "case": Case}


async def _record_facts(db: AsyncSession, principal: Principal, object_type: str, record: Any) -> dict[str, Any]:
    org = principal.organization_id
    facts: dict[str, Any] = {}
    columns = [c.key for c in record.__table__.columns if c.key not in {"organization_id", "custom_fields", "id"}]
    for key in columns:
        value = getattr(record, key, None)
        if value not in (None, "", [], {}):
            facts[key] = value.isoformat() if hasattr(value, "isoformat") else (float(value) if hasattr(value, "quantize") else value)
    filters = {
        "lead": Activity.lead_id == record.id,
        "contact": Activity.contact_id == record.id,
        "account": Activity.account_id == record.id,
        "opportunity": (Activity.opportunity_id == record.id) | (Activity.account_id == getattr(record, "account_id", None)),
    }
    activities = []
    if object_type in filters:
        activities = (
            await db.execute(select(Activity).where(Activity.organization_id == org, filters[object_type]).order_by(Activity.occurred_at.desc()).limit(8))
        ).scalars().all()
    facts["recent_activity"] = [
        {"type": a.activity_type, "when": a.occurred_at.date().isoformat() if a.occurred_at else None, "subject": a.subject, "notes": (a.body or "")[:300]}
        for a in activities
    ]
    if object_type == "case":
        comments = (
            await db.execute(select(CaseComment).where(CaseComment.case_id == record.id).order_by(CaseComment.created_at.desc()).limit(6))
        ).scalars().all()
        facts["conversation"] = [{"internal": c.is_internal, "text": c.body[:400]} for c in reversed(comments)]
    return facts


def rules_summary(object_type: str, record: Any, facts: dict[str, Any]) -> str:
    recent = facts.get("recent_activity") or []
    last = f" The last touch was a {recent[0]['type']} on {recent[0]['when']} (“{recent[0]['subject']}”)." if recent else " There's no logged activity yet."
    if object_type == "opportunity":
        amount = f"${float(record.amount):,.0f}" if record.amount else "no amount"
        close = f"closing {record.expected_close_date:%b %d}" if record.expected_close_date else "no close date"
        step = f" Next step: {record.next_step}." if record.next_step else " No next step is set."
        return f"{record.name} is worth {amount}, {close}, at {record.probability}% probability.{step}{last}"
    if object_type == "case":
        convo = facts.get("conversation") or []
        return (
            f"CS-{record.number} “{record.subject}” is {record.status.replace('_', ' ')} at {record.priority} priority via {record.channel}. "
            f"{len(convo)} replies so far." + (f" Latest: “{convo[-1]['text'][:160]}”." if convo else "")
        )
    if object_type == "account":
        return f"{record.name} is a {record.lifecycle_stage} account{f' in {record.industry}' if record.industry else ''}.{last}"
    name = f"{record.first_name} {record.last_name}".strip()
    role = f", {record.job_title}" if getattr(record, "job_title", None) else ""
    status_text = f" Lead status: {record.status}, score {record.score}." if object_type == "lead" else ""
    return f"{name}{role}.{status_text}{last}"


@router.get("/ai/summary/{object_type}/{record_id}", dependencies=[Depends(require_feature("extended_ai"))])
async def summarize(
    object_type: str,
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    model = SUMMARY_MODELS.get(object_type)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown record type.")
    record = await db.get(model, record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    await metering.ensure_ai_words(db, principal.organization_id, principal.user_id)
    facts = await _record_facts(db, principal, object_type, record)
    text = await ai.complete(
        f"Summarize this CRM {object_type} for a busy {'support agent' if object_type == 'case' else 'sales rep'} in 3-4 sentences, "
        "then give one recommended next action on its own line starting with 'Next:'.\n\n"
        f"{json.dumps(facts, default=str)}",
        system=await ai_context.system_prompt(db, principal.organization_id, "summaries"),
        max_tokens=350,
    )
    source = "claude" if text else "rules"
    text = text or rules_summary(object_type, record, facts)
    metering.record_ai_words(db, principal.organization_id, principal.user_id, "summaries", text, source)
    await db.commit()
    return {"summary": text, "source": source}


class DraftPayload(BaseModel):
    object_type: str = Field(pattern="^(lead|contact|opportunity|case)$")
    record_id: uuid.UUID
    intent: str = Field(default="follow_up", max_length=200)
    tone: str = Field(default="friendly", pattern="^(friendly|formal|brief)$")


@router.post("/ai/draft", dependencies=[Depends(require_feature("extended_ai"))])
async def draft(
    payload: DraftPayload,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    model = SUMMARY_MODELS[payload.object_type]
    record = await db.get(model, payload.record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    await metering.ensure_ai_words(db, principal.organization_id, principal.user_id)
    facts = await _record_facts(db, principal, payload.object_type, record)
    sender = principal.full_name.split(" ")[0]
    text = await ai.complete(
        f"Draft a {payload.tone} email (under 150 words) for this purpose: {payload.intent}. "
        f"It's from {principal.full_name}. Use the CRM data below. Return the subject on the first line as 'Subject: …', "
        f"then a blank line, then the body.\n\n{json.dumps(facts, default=str)}",
        system=await ai_context.system_prompt(db, principal.organization_id, "drafts"),
        max_tokens=450,
    )
    if text:
        metering.record_ai_words(db, principal.organization_id, principal.user_id, "drafts", text, "claude")
        await db.commit()
    if text and text.lower().startswith("subject:"):
        subject, _, body = text.partition("\n")
        return {"subject": subject[8:].strip(), "body": body.strip(), "source": "claude"}
    if text:
        return {"subject": "Following up", "body": text, "source": "claude"}
    context = await workspace_settings.get(db, principal.organization_id, "ai_context")
    signature = context.signature.strip() if "drafts" in context.use_for else ""
    if payload.object_type == "case":
        subject = f"Re: {record.subject}"
        body = f"Hi,\n\nThanks for your patience on “{record.subject}”. Here's where things stand: we're on it and will update you as soon as we have news.\n\nBest regards,\n{sender}"
    else:
        name = getattr(record, "first_name", None) or "there"
        topic = getattr(record, "name", None) or "our conversation"
        subject = f"Following up on {topic}"
        greeting = "Hello" if payload.tone == "formal" else "Hi"
        body = (
            f"{greeting} {name},\n\nI wanted to follow up on {topic}. "
            + ("Do you have 15 minutes this week to talk through next steps?" if payload.tone != "brief" else "Any update on your side?")
            + f"\n\nBest,\n{sender}"
        )
    if signature:
        body = body.rsplit("\n\n", 1)[0] + f"\n\n{signature}"
    if "drafts" in context.use_for:
        body = ai_context.apply_words_to_avoid(body, context)
    metering.record_ai_words(db, principal.organization_id, principal.user_id, "drafts", f"{subject} {body}", "rules")
    await db.commit()
    return {"subject": subject, "body": body, "source": "rules"}
