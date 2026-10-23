"""AI agents that review the CRM and propose actions.

Five agents look for work worth doing and leave suggestions in an inbox:
accept one and AIVA does it (qualify a lead, create a follow-up task,
reprioritise a case, link an article …); dismiss it and it stays dismissed.

Agents decide *what* to suggest with rules over your data. When Claude is
configured they also write the email drafts and reply drafts they attach.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import AiSuggestion, Subscription
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Case, KnowledgeArticle
from app.models.task import Task
from app.services import ai
from app.services.billing import plan_state
from app.services.enrichment import enrich
from app.services.predictions import predict_open

logger = logging.getLogger("aiva.agents")

AGENTS: dict[str, dict[str, str]] = {
    "lead_qualifier": {
        "name": "Lead qualifier",
        "description": "Reviews new leads, qualifies strong fits and makes sure nobody waits for a first touch.",
    },
    "deal_coach": {
        "name": "Deal coach",
        "description": "Watches open deals for slipped close dates, missing next steps and silence.",
    },
    "case_triage": {
        "name": "Case triage",
        "description": "Reads new cases, suggests priority and category, links an article and drafts a reply.",
    },
    "follow_up_writer": {
        "name": "Follow-up writer",
        "description": "Drafts follow-up emails after calls and meetings that had no follow-up yet.",
    },
    "data_hygiene": {
        "name": "Data hygiene",
        "description": "Finds duplicate contacts and records missing key details, and fills gaps.",
    },
}

URGENT_WORDS = ("down", "outage", "can't log in", "cannot log in", "crash", "data loss", "breach", "security", "urgent", "not working", "error 500", "all users")
HIGH_WORDS = ("broken", "fails", "failing", "wrong", "missing", "delayed", "slow", "bug", "blocked")
CATEGORIES = (
    ("Billing", ("invoice", "billing", "charge", "refund", "payment", "seats", "subscription")),
    ("Account & sign-in", ("login", "log in", "password", "sso", "sign in", "mfa", "2fa")),
    ("Reporting", ("report", "dashboard", "chart")),
    ("Integrations", ("sync", "integration", "gmail", "outlook", "slack", "api", "webhook")),
    ("Data management", ("import", "export", "duplicate", "data")),
    ("Quotes", ("quote", "pdf", "discount", "pricing")),
    ("Automation", ("workflow", "automation", "rule")),
    ("Mobile", ("mobile", "iphone", "android", "app")),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(agent: str, record_id: Any, kind: str) -> str:
    return hashlib.sha256(f"{agent}:{record_id}:{kind}".encode()).hexdigest()


def agent_settings(organization: Organization) -> dict[str, dict[str, Any]]:
    stored = (organization.settings or {}).get("ai_agents") or {}
    return {key: {"enabled": bool((stored.get(key) or {}).get("enabled", True)), "last_run_at": (stored.get(key) or {}).get("last_run_at")} for key in AGENTS}


class _Collector:
    def __init__(self, db: AsyncSession, organization_id: uuid.UUID, agent: str):
        self.db = db
        self.organization_id = organization_id
        self.agent = agent
        self.created = 0
        self._known: set[str] | None = None

    async def known(self) -> set[str]:
        if self._known is None:
            cutoff = utcnow() - timedelta(days=30)
            self._known = set(
                (
                    await self.db.execute(
                        select(AiSuggestion.fingerprint).where(
                            AiSuggestion.organization_id == self.organization_id,
                            AiSuggestion.agent == self.agent,
                            (AiSuggestion.status == "open") | (AiSuggestion.updated_at >= cutoff),
                        )
                    )
                ).scalars()
            )
        return self._known

    async def add(self, *, kind: str, record_type: str, record_id: uuid.UUID, record_label: str, title: str, body: str | None, action: dict, confidence: int, owner: uuid.UUID | None, source: str = "rules") -> None:
        key = fingerprint(self.agent, record_id, kind)
        known = await self.known()
        if key in known:
            return
        known.add(key)
        self.db.add(
            AiSuggestion(
                id=uuid.uuid4(),
                organization_id=self.organization_id,
                agent=self.agent,
                record_type=record_type,
                record_id=record_id,
                record_label=record_label[:300],
                title=title[:300],
                body=body,
                action=action,
                confidence=max(1, min(99, confidence)),
                status="open",
                source=source,
                owner_user_id=owner,
                fingerprint=key,
            )
        )
        self.created += 1


async def _draft(prompt: str, fallback: str) -> tuple[str, str]:
    text = await ai.complete(prompt, max_tokens=500)
    return (text, "claude") if text else (fallback, "rules")


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


async def run_lead_qualifier(db: AsyncSession, org: uuid.UUID) -> int:
    out = _Collector(db, org, "lead_qualifier")
    since = utcnow() - timedelta(days=60)
    leads = (
        await db.execute(
            select(Lead).where(Lead.organization_id == org, Lead.status.in_(("new", "contacted")), Lead.created_at >= since).limit(300)
        )
    ).scalars().all()
    touched = set(
        (
            await db.execute(select(Activity.lead_id).where(Activity.organization_id == org, Activity.lead_id.in_([l.id for l in leads] or [uuid.uuid4()])))
        ).scalars()
    )
    for lead in leads:
        name = f"{lead.first_name} {lead.last_name}".strip()
        label = f"{name}{f' ({lead.company_name})' if lead.company_name else ''}"
        if lead.score >= 60:
            await out.add(
                kind="qualify", record_type="lead", record_id=lead.id, record_label=label,
                title=f"Qualify {name}: score {lead.score}",
                body=f"{name} scores {lead.score}/100 on your lead scoring rules"
                + (f" and holds a senior title ({lead.job_title})" if lead.job_title else "")
                + ". Marking them qualified moves them into the sales queue.",
                action={"type": "update_field", "field": "status", "value": "qualified"},
                confidence=min(95, lead.score), owner=lead.owner_user_id,
            )
        age = (utcnow() - lead.created_at).days if lead.created_at else 0
        if lead.status == "new" and lead.id not in touched and age >= 2:
            await out.add(
                kind="first_touch", record_type="lead", record_id=lead.id, record_label=label,
                title=f"{name} has waited {age} days for a first touch",
                body="Leads contacted within a day convert far more often. A call task has been drafted for the owner.",
                action={"type": "create_task", "title": f"Call {name}", "task_type": "call", "due_in_days": 0, "priority": "high"},
                confidence=70 + min(20, age), owner=lead.owner_user_id,
            )
        if not lead.email or not lead.phone or not lead.company_name:
            await out.add(
                kind="enrich", record_type="lead", record_id=lead.id, record_label=label,
                title=f"Fill in missing details for {name}",
                body="Some contact details are missing. Enrichment can add the company, website and a consistent phone format.",
                action={"type": "enrich"}, confidence=55, owner=lead.owner_user_id,
            )
    return out.created


async def run_deal_coach(db: AsyncSession, org: uuid.UUID) -> int:
    out = _Collector(db, org, "deal_coach")
    model, predictions = await predict_open(db, org)
    today = date.today()
    for opp, prediction in predictions:
        label = opp.name
        if opp.expected_close_date and opp.expected_close_date < today:
            new_date = today + timedelta(days=30)
            await out.add(
                kind=f"slipped:{opp.expected_close_date}", record_type="opportunity", record_id=opp.id, record_label=label,
                title=f"{opp.name}: close date passed {(today - opp.expected_close_date).days} days ago",
                body=f"Update the close date so the forecast stays honest. Suggested: {new_date:%b %d, %Y}.",
                action={"type": "update_field", "field": "expected_close_date", "value": new_date.isoformat()},
                confidence=85, owner=opp.owner_user_id,
            )
        if not opp.next_step:
            await out.add(
                kind="next_step", record_type="opportunity", record_id=opp.id, record_label=label,
                title=f"{opp.name} has no next step",
                body="Deals with an agreed next step close more often. A task to agree one has been drafted.",
                action={"type": "create_task", "title": f"Agree next step for {opp.name}", "due_in_days": 2},
                confidence=60, owner=opp.owner_user_id,
            )
        silent = next((f for f in prediction.factors if f["label"].startswith("No activity") or f["label"].startswith("No calls")), None)
        if silent:
            draft, source = await _draft(
                f"Write a short, warm re-engagement email (under 120 words) to the main contact for the deal '{opp.name}'"
                f"{f' worth ${float(opp.amount):,.0f}' if opp.amount else ''}. Next step on file: {opp.next_step or 'none'}. "
                "No subject line, just the body, sign off with the sender's first name placeholder {{sender_name}}.",
                f"Hi {{{{first_name}}}},\n\nI wanted to check in on {opp.name}. Is now still a good time to keep things moving? "
                "If it helps, I can send over a summary of where we landed and the next steps.\n\nBest,\n{{sender_name}}",
            )
            await out.add(
                kind="reengage", record_type="opportunity", record_id=opp.id, record_label=label,
                title=f"Re-engage {opp.name} ({silent['label'].lower()})",
                body=draft,
                action={"type": "create_task", "title": f"Send re-engagement email for {opp.name}", "task_type": "email", "due_in_days": 1, "description": draft},
                confidence=65, owner=opp.owner_user_id, source=source,
            )
        if prediction.label == "at_risk" and opp.amount and model.median_won_amount and float(opp.amount) >= model.median_won_amount:
            reasons = "; ".join(f["label"] for f in prediction.factors if f["impact"] == "negative")
            await out.add(
                kind="at_risk", record_type="opportunity", record_id=opp.id, record_label=label,
                title=f"{opp.name} is at risk ({prediction.probability}% likely to win)",
                body=f"What's holding it back: {reasons or 'limited history'}. Review it with the owner this week.",
                action={"type": "create_task", "title": f"Deal review: {opp.name}", "task_type": "review", "due_in_days": 3},
                confidence=100 - prediction.probability, owner=opp.owner_user_id,
            )
    return out.created


def _triage(text: str) -> tuple[str | None, str | None]:
    lowered = text.lower()
    priority = "urgent" if any(w in lowered for w in URGENT_WORDS) else "high" if any(w in lowered for w in HIGH_WORDS) else None
    category = next((name for name, words in CATEGORIES if any(w in lowered for w in words)), None)
    return priority, category


async def run_case_triage(db: AsyncSession, org: uuid.UUID) -> int:
    out = _Collector(db, org, "case_triage")
    cases = (
        await db.execute(select(Case).where(Case.organization_id == org, Case.status.in_(("new", "open"))).order_by(Case.created_at.desc()).limit(200))
    ).scalars().all()
    articles = (
        await db.execute(select(KnowledgeArticle).where(KnowledgeArticle.organization_id == org, KnowledgeArticle.status == "published"))
    ).scalars().all()
    agents = [
        user_id
        for user_id, role in (
            await db.execute(
                select(OrganizationMembership.user_id, OrganizationMembership.role).where(
                    OrganizationMembership.organization_id == org, OrganizationMembership.status == "active"
                )
            )
        ).all()
        if role in {"support_agent", "admin", "owner"}
    ]
    load = Counter(
        dict(
            (
                await db.execute(
                    select(Case.assigned_to_user_id, func.count())
                    .where(Case.organization_id == org, Case.status.in_(("new", "open", "pending")), Case.assigned_to_user_id.is_not(None))
                    .group_by(Case.assigned_to_user_id)
                )
            ).all()
        )
    )
    for case in cases:
        text = f"{case.subject} {case.description or ''}"
        label = f"CS-{case.number}: {case.subject}"
        priority, category = _triage(text)
        order = ["low", "medium", "high", "urgent"]
        if priority and order.index(priority) > order.index(case.priority):
            await out.add(
                kind=f"priority:{priority}", record_type="case", record_id=case.id, record_label=label,
                title=f"Raise CS-{case.number} to {priority}",
                body=f"The customer's words suggest a {priority} issue (“{case.subject}”).",
                action={"type": "update_field", "field": "priority", "value": priority},
                confidence=75 if priority == "urgent" else 62, owner=case.assigned_to_user_id,
            )
        if category and not case.category:
            await out.add(
                kind=f"category:{category}", record_type="case", record_id=case.id, record_label=label,
                title=f"Categorise CS-{case.number} as {category}",
                body="Categories route cases and power reporting.",
                action={"type": "update_field", "field": "category", "value": category},
                confidence=68, owner=case.assigned_to_user_id,
            )
        if case.assigned_to_user_id is None and agents:
            agent = min(agents, key=lambda user_id: load.get(user_id, 0))
            load[agent] += 1
            await out.add(
                kind="assign", record_type="case", record_id=case.id, record_label=label,
                title=f"Assign CS-{case.number} to the least busy agent",
                body="This case is unassigned. AIVA picked the support agent with the fewest open cases.",
                action={"type": "update_field", "field": "assigned_to_user_id", "value": str(agent)},
                confidence=70, owner=None,
            )
        words = {w for w in text.lower().replace("/", " ").split() if len(w) > 3}
        best = max(articles, key=lambda a: len(words & set(f"{a.title} {a.summary or ''}".lower().split())), default=None)
        if best is not None and len(words & set(f"{best.title} {best.summary or ''}".lower().split())) >= 2 and str(best.id) not in (case.linked_article_ids or []):
            draft, source = await _draft(
                f"Write a short, empathetic support reply (under 120 words) to a customer whose case is titled "
                f"'{case.subject}'. Details: {(case.description or '')[:600]}. Point them to our article '{best.title}'. "
                "Plain text, no subject line.",
                f"Hi,\n\nThanks for reaching out about “{case.subject}”. Our article “{best.title}” walks through this step by step. "
                "If it doesn't solve things, reply here and we'll dig in right away.\n\nBest regards",
            )
            await out.add(
                kind=f"article:{best.id}", record_type="case", record_id=case.id, record_label=label,
                title=f"Link “{best.title}” to CS-{case.number} and reply",
                body=draft,
                action={"type": "link_article", "article_id": str(best.id), "reply": draft},
                confidence=60, owner=case.assigned_to_user_id, source=source,
            )
    return out.created


async def run_follow_up_writer(db: AsyncSession, org: uuid.UUID) -> int:
    out = _Collector(db, org, "follow_up_writer")
    since = utcnow() - timedelta(days=7)
    meetings = (
        await db.execute(
            select(Activity).where(
                Activity.organization_id == org,
                Activity.activity_type.in_(("meeting", "call")),
                Activity.occurred_at >= since,
                Activity.occurred_at <= utcnow() - timedelta(hours=4),
                Activity.contact_id.is_not(None),
            ).limit(200)
        )
    ).scalars().all()
    for meeting in meetings:
        followed = (
            await db.execute(
                select(func.count()).select_from(Activity).where(
                    Activity.organization_id == org,
                    Activity.contact_id == meeting.contact_id,
                    Activity.activity_type == "email",
                    Activity.direction == "outbound",
                    Activity.occurred_at >= meeting.occurred_at,
                )
            )
        ).scalar_one()
        if followed:
            continue
        contact = await db.get(Contact, meeting.contact_id)
        if contact is None:
            continue
        name = f"{contact.first_name} {contact.last_name}".strip()
        draft, source = await _draft(
            f"Write a follow-up email (under 140 words) after a {meeting.activity_type} with {name}. "
            f"Notes from the {meeting.activity_type}: {(meeting.body or meeting.subject or '')[:800]}. "
            "Thank them, recap the key points as 2-3 short bullets, confirm the next step. Plain text, no subject line.",
            f"Hi {contact.first_name},\n\nThanks for your time today. Quick recap of what we covered:\n"
            f"- {meeting.subject}\n\nI'll follow up with the next steps we discussed. Let me know if I missed anything.\n\nBest,\n{{{{sender_name}}}}",
        )
        await out.add(
            kind=f"follow_up:{meeting.id}", record_type="contact", record_id=contact.id, record_label=name,
            title=f"Send a follow-up to {name} after the {meeting.activity_type}",
            body=draft,
            action={"type": "create_task", "title": f"Send follow-up email to {name}", "task_type": "email", "due_in_days": 0, "description": draft},
            confidence=72, owner=contact.owner_user_id or meeting.created_by_user_id, source=source,
        )
    return out.created


async def run_data_hygiene(db: AsyncSession, org: uuid.UUID) -> int:
    out = _Collector(db, org, "data_hygiene")
    duplicates = (
        await db.execute(
            select(func.lower(Contact.email), func.count())
            .where(Contact.organization_id == org, Contact.email.is_not(None), Contact.email != "", Contact.is_archived.is_(False))
            .group_by(func.lower(Contact.email))
            .having(func.count() > 1)
            .limit(50)
        )
    ).all()
    for email, count in duplicates:
        contacts = (
            await db.execute(select(Contact).where(Contact.organization_id == org, func.lower(Contact.email) == email).order_by(Contact.created_at))
        ).scalars().all()
        keep = contacts[0]
        await out.add(
            kind=f"duplicate:{email}", record_type="contact", record_id=keep.id, record_label=f"{keep.first_name} {keep.last_name}",
            title=f"{count} contacts share {email}",
            body="Duplicates split history and make reports double count. Review them and archive the extras.",
            action={"type": "open"}, confidence=80, owner=keep.owner_user_id,
        )
    contacts = (
        await db.execute(
            select(Contact).where(
                Contact.organization_id == org,
                Contact.is_archived.is_(False),
                (Contact.department.is_(None)) & (Contact.job_title.is_not(None)),
            ).limit(100)
        )
    ).scalars().all()
    for contact in contacts:
        await out.add(
            kind="enrich", record_type="contact", record_id=contact.id, record_label=f"{contact.first_name} {contact.last_name}",
            title=f"Add department and seniority for {contact.first_name} {contact.last_name}",
            body=f"Their title (“{contact.job_title}”) is enough to fill these in.",
            action={"type": "enrich"}, confidence=58, owner=contact.owner_user_id,
        )
    return out.created


RUNNERS = {
    "lead_qualifier": run_lead_qualifier,
    "deal_coach": run_deal_coach,
    "case_triage": run_case_triage,
    "follow_up_writer": run_follow_up_writer,
    "data_hygiene": run_data_hygiene,
}


async def run_agent(db: AsyncSession, organization: Organization, agent: str) -> int:
    if agent not in RUNNERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent.")
    info = db.sync_session.info
    previous = info.get("automation_disabled", False)
    info["automation_disabled"] = True
    try:
        created = await RUNNERS[agent](db, organization.id)
    finally:
        info["automation_disabled"] = previous
    stored = dict((organization.settings or {}).get("ai_agents") or {})
    stored[agent] = {**(stored.get(agent) or {"enabled": True}), "last_run_at": utcnow().isoformat()}
    organization.settings = {**(organization.settings or {}), "ai_agents": stored}
    return created


async def run_scheduled(db: AsyncSession) -> int:
    """Run enabled agents once a day for workspaces whose plan includes AI agents."""

    cutoff = utcnow() - timedelta(hours=24)
    rows = (
        await db.execute(select(Organization, Subscription).outerjoin(Subscription, Subscription.organization_id == Organization.id).where(Organization.is_active.is_(True)))
    ).all()
    total = 0
    for organization, subscription in rows:
        state = plan_state(subscription)
        if "ai_agents" not in state.features or state.read_only:
            continue
        for agent, config in agent_settings(organization).items():
            if not config["enabled"]:
                continue
            last = config["last_run_at"]
            if last and datetime.fromisoformat(last) > cutoff:
                continue
            try:
                total += await run_agent(db, organization, agent)
                await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Agent %s failed for %s", agent, organization.id)
                await db.rollback()
            return total  # one agent per tick keeps each run short
    return total


# ---------------------------------------------------------------------------
# Accepting suggestions
# ---------------------------------------------------------------------------

UPDATABLE = {
    "lead": {"status": Lead},
    "opportunity": {"expected_close_date": Opportunity, "priority": Opportunity, "next_step": Opportunity},
    "case": {"priority": Case, "category": Case, "assigned_to_user_id": Case},
}
MODELS = {"lead": Lead, "contact": Contact, "opportunity": Opportunity, "case": Case}


async def accept(db: AsyncSession, suggestion: AiSuggestion, actor: uuid.UUID) -> str:
    action = suggestion.action or {}
    kind = action.get("type")
    model = MODELS.get(suggestion.record_type)
    record = await db.get(model, suggestion.record_id) if model else None
    if record is None or record.organization_id != suggestion.organization_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "That record no longer exists.")

    if kind == "update_field":
        field = action.get("field")
        if field not in UPDATABLE.get(suggestion.record_type, {}):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This suggestion can't change that field.")
        value = action.get("value")
        if field == "expected_close_date":
            value = date.fromisoformat(value)
        elif field == "assigned_to_user_id":
            value = uuid.UUID(value)
        setattr(record, field, value)
        message = f"Updated {field.replace('_', ' ')}"
    elif kind == "create_task":
        task = Task(
            id=uuid.uuid4(),
            organization_id=suggestion.organization_id,
            title=(action.get("title") or suggestion.title)[:250],
            description=action.get("description") or suggestion.body,
            task_type=action.get("task_type") or "follow_up",
            status="open",
            priority=action.get("priority") or "medium",
            source="ai",
            due_at=utcnow() + timedelta(days=int(action.get("due_in_days") or 1)),
            assigned_to_user_id=getattr(record, "owner_user_id", None) or getattr(record, "assigned_to_user_id", None) or actor,
            created_by_user_id=actor,
            is_recurring=False,
            is_ai_generated=True,
            tags=["ai"],
            task_metadata={"ai_suggestion_id": str(suggestion.id), "agent": suggestion.agent},
            lead_id=record.id if suggestion.record_type == "lead" else None,
            contact_id=record.id if suggestion.record_type == "contact" else getattr(record, "contact_id", None),
            opportunity_id=record.id if suggestion.record_type == "opportunity" else None,
            account_id=getattr(record, "account_id", None),
        )
        db.add(task)
        message = f"Created task “{task.title}”"
    elif kind == "link_article":
        article_id = str(action.get("article_id"))
        if article_id not in (record.linked_article_ids or []):
            record.linked_article_ids = [*(record.linked_article_ids or []), article_id]
        message = "Linked the article"
    elif kind == "enrich":
        result = enrich(record)
        message = f"Enriched {len(result['applied'])} field{'s' if len(result['applied']) != 1 else ''}"
    elif kind == "open":
        message = "Marked as handled"
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown suggestion.")

    suggestion.status = "accepted"
    suggestion.resolved_by_user_id = actor
    suggestion.resolved_at = utcnow()
    return message


async def stage_lookup(db: AsyncSession, organization_id: uuid.UUID) -> dict[uuid.UUID, PipelineStage]:
    return {s.id: s for s in (await db.execute(select(PipelineStage).where(PipelineStage.organization_id == organization_id))).scalars()}
