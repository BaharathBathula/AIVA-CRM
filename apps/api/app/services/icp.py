"""Ideal customer profile: persona matching, buying-intent scores and signals.

* **Personas** match people by job title keywords (with exclusions), and
  optionally by seniority and department derived from the title.
* **Buying intent** scores accounts from the website pages their people read
  that match intent topics (keywords in page titles or URL patterns).
* **Signals** are rules evaluated in the background ("visited the pricing
  page", "opened 3+ emails this week", "deal stalled for 14 days"…). Each hit
  is recorded once, shown in the Signals feed, and can notify the owner and
  create a follow-up task.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import WebFormSubmission
from app.models.engagement import WebPageView, WebVisitor
from app.models.icp import IntentTopic, Persona, SignalEvent, SignalRule
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.outbound import SequenceEnrollment, SequenceMessage
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Notification
from app.models.task import Task
from app.services.enrichment import department, seniority

logger = logging.getLogger("aiva.icp")

SIGNAL_KINDS: dict[str, dict[str, Any]] = {
    "page_visit": {"label": "Visited key pages", "defaults": {"paths": ["/pricing"], "min_views": 1, "days": 1}},
    "intent_surge": {"label": "Buying intent surge", "defaults": {"level": "high", "days": 7}},
    "email_engagement": {"label": "Opened emails repeatedly", "defaults": {"opens": 3, "days": 7}},
    "form_submitted": {"label": "Filled in a form", "defaults": {"days": 1}},
    "deal_stalled": {"label": "Deal stalled", "defaults": {"days": 14}},
    "job_change": {"label": "Champion changed jobs", "defaults": {"days": 30}},
    "sequence_reply": {"label": "Replied to a sequence", "defaults": {"days": 2}},
    "persona_match": {"label": "New person matches a persona", "defaults": {"persona_id": None, "days": 2}},
}
LEVELS = (("high", 10), ("medium", 4), ("low", 1))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------


def _contains(text: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    return re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", text) is not None


def persona_matches(title: str | None, persona: Any) -> bool:
    text = (title or "").lower().strip()
    if not text:
        return False
    keywords = [k for k in (persona.title_keywords or []) if k.strip()]
    if keywords and not any(_contains(text, k) for k in keywords):
        return False
    if any(_contains(text, k) for k in persona.exclude_keywords or []):
        return False
    if persona.seniorities and seniority(title) not in persona.seniorities:
        return False
    if persona.departments and department(title) not in persona.departments:
        return False
    return bool(keywords or persona.seniorities or persona.departments)


async def people_with_titles(db: AsyncSession, organization_id: uuid.UUID, limit: int = 20000) -> list[tuple[str, Any]]:
    contacts = (
        await db.execute(select(Contact).where(Contact.organization_id == organization_id, Contact.job_title.is_not(None), Contact.is_archived.is_(False)).limit(limit))
    ).scalars().all()
    leads = (
        await db.execute(select(Lead).where(Lead.organization_id == organization_id, Lead.job_title.is_not(None), Lead.status != "converted").limit(limit))
    ).scalars().all()
    return [("contact", c) for c in contacts] + [("lead", l) for l in leads]


def person_out(kind: str, record: Any, accounts: dict[str, str]) -> dict[str, Any]:
    company = accounts.get(str(getattr(record, "account_id", None))) if kind == "contact" else record.company_name
    return {
        "type": kind,
        "id": str(record.id),
        "name": f"{record.first_name or ''} {record.last_name or ''}".strip() or record.email,
        "title": record.job_title,
        "company": company,
        "email": record.email,
        "href": f"/{kind}s/{record.id}",
        "seniority": seniority(record.job_title),
        "department": department(record.job_title),
    }


# ---------------------------------------------------------------------------
# Buying intent
# ---------------------------------------------------------------------------


def topic_matches(topic: Any, path: str, title: str | None) -> bool:
    text = f"{path} {title or ''}".lower()
    for pattern in topic.url_patterns or []:
        pattern = pattern.strip()
        if pattern and (fnmatch.fnmatch(path.lower(), pattern.lower()) or path.lower().startswith(pattern.lower().rstrip("*"))):
            return True
    return any(_contains(text, k) for k in topic.keywords or [])


def level_for(score: float) -> str | None:
    for name, threshold in LEVELS:
        if score >= threshold:
            return name
    return None


async def account_intent(db: AsyncSession, organization_id: uuid.UUID, days: int = 30) -> list[dict[str, Any]]:
    topics = (await db.execute(select(IntentTopic).where(IntentTopic.organization_id == organization_id, IntentTopic.is_active.is_(True)))).scalars().all()
    if not topics:
        return []
    since = utcnow() - timedelta(days=days)
    rows = (
        await db.execute(
            select(WebPageView.path, WebPageView.title, WebPageView.viewed_at, WebVisitor.id, Contact.account_id, Lead.company_name, Lead.id)
            .join(WebVisitor, WebVisitor.id == WebPageView.visitor_id)
            .outerjoin(Contact, Contact.id == WebVisitor.contact_id)
            .outerjoin(Lead, Lead.id == WebVisitor.lead_id)
            .where(WebPageView.organization_id == organization_id, WebPageView.viewed_at >= since)
            .where((WebVisitor.contact_id.is_not(None)) | (WebVisitor.lead_id.is_not(None)))
        )
    ).all()
    buckets: dict[str, dict[str, Any]] = {}
    for path, title, viewed_at, visitor_id, account_id, lead_company, lead_id in rows:
        key = f"account:{account_id}" if account_id else (f"lead:{lead_id}" if lead_id else None)
        if key is None:
            continue
        for topic in topics:
            if not topic_matches(topic, path or "/", title):
                continue
            bucket = buckets.setdefault(key, {"key": key, "account_id": str(account_id) if account_id else None, "lead_id": str(lead_id) if lead_id else None,
                                              "company": lead_company, "score": 0.0, "topics": {}, "visitors": set(), "last_seen": viewed_at})
            # Recent reading counts more than older reading.
            age = (utcnow() - viewed_at).days if viewed_at else days
            weight = topic.weight * (1.0 if age <= 7 else 0.5)
            bucket["score"] += weight
            bucket["topics"][topic.name] = bucket["topics"].get(topic.name, 0) + 1
            bucket["visitors"].add(str(visitor_id))
            if viewed_at and viewed_at > bucket["last_seen"]:
                bucket["last_seen"] = viewed_at
    account_ids = [uuid.UUID(b["account_id"]) for b in buckets.values() if b["account_id"]]
    names = {}
    if account_ids:
        names = {str(a.id): (a.name, a.owner_user_id) for a in (await db.execute(select(Account).where(Account.id.in_(account_ids)))).scalars()}
    out = []
    for bucket in buckets.values():
        level = level_for(bucket["score"])
        if level is None:
            continue
        name, owner = names.get(bucket["account_id"], (bucket["company"] or "Unknown company", None)) if bucket["account_id"] else (bucket["company"] or "Lead", None)
        out.append(
            {
                "key": bucket["key"],
                "account_id": bucket["account_id"],
                "lead_id": bucket["lead_id"],
                "name": name,
                "owner_user_id": str(owner) if owner else None,
                "href": f"/accounts/{bucket['account_id']}" if bucket["account_id"] else f"/leads/{bucket['lead_id']}",
                "score": round(bucket["score"], 1),
                "level": level,
                "topics": sorted(({"name": k, "views": v} for k, v in bucket["topics"].items()), key=lambda t: -t["views"]),
                "visitors": len(bucket["visitors"]),
                "last_seen": bucket["last_seen"],
            }
        )
    return sorted(out, key=lambda r: -r["score"])


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


async def _fire(
    db: AsyncSession,
    rule: SignalRule,
    *,
    record_type: str,
    record_id: uuid.UUID,
    label: str,
    dedupe: str,
    detail: str,
    owner: uuid.UUID | None,
    account_id: uuid.UUID | None = None,
    links: dict[str, uuid.UUID | None] | None = None,
) -> bool:
    exists = (await db.execute(select(SignalEvent.id).where(SignalEvent.rule_id == rule.id, SignalEvent.dedupe_key == dedupe[:200]))).first()
    if exists:
        return False
    event = SignalEvent(
        id=uuid.uuid4(), organization_id=rule.organization_id, rule_id=rule.id, record_type=record_type, record_id=record_id, record_label=label[:300],
        account_id=account_id, owner_user_id=owner, detail=detail[:2000], dedupe_key=dedupe[:200], fired_at=utcnow(),
    )
    db.add(event)
    href = f"/{record_type}s/{record_id}" if record_type in ("lead", "contact", "account") else (f"/opportunities/{record_id}" if record_type == "opportunity" else "/signals")
    if owner and rule.notify_owner:
        db.add(Notification(id=uuid.uuid4(), organization_id=rule.organization_id, user_id=owner, kind="signal",
                            title=f"{rule.name}: {label}"[:200], body=detail[:500], link=href, is_read=False))
    if owner and rule.create_task:
        links = links or {}
        db.add(
            Task(
                id=uuid.uuid4(), organization_id=rule.organization_id, title=f"Follow up: {label} ({rule.name})"[:250], description=detail,
                task_type="follow_up", status="open", priority="high", source="signal", due_at=utcnow() + timedelta(days=1),
                assigned_to_user_id=owner, created_by_user_id=None, is_recurring=False, is_ai_generated=False, tags=["signal"],
                task_metadata={"signal_event_id": str(event.id), "signal_rule_id": str(rule.id)},
                lead_id=links.get("lead_id"), contact_id=links.get("contact_id"), account_id=links.get("account_id") or account_id,
                opportunity_id=links.get("opportunity_id"),
            )
        )
    rule.fire_count = (rule.fire_count or 0) + 1
    return True


async def _person(db: AsyncSession, visitor: WebVisitor) -> tuple[str, Any] | None:
    if visitor.contact_id:
        contact = await db.get(Contact, visitor.contact_id)
        return ("contact", contact) if contact else None
    if visitor.lead_id:
        lead = await db.get(Lead, visitor.lead_id)
        return ("lead", lead) if lead else None
    return None


def _name(record: Any) -> str:
    return f"{record.first_name or ''} {record.last_name or ''}".strip() or getattr(record, "email", None) or "Someone"


async def evaluate_rule(db: AsyncSession, rule: SignalRule) -> int:
    config = {**SIGNAL_KINDS.get(rule.kind, {}).get("defaults", {}), **(rule.config or {})}
    now = utcnow()
    days = max(1, int(config.get("days") or 1))
    since = now - timedelta(days=days)
    fired = 0
    org = rule.organization_id
    if rule.kind == "page_visit":
        patterns = [p.strip() for p in config.get("paths") or [] if p.strip()]
        rows = (
            await db.execute(
                select(WebPageView.visitor_id, WebPageView.path, func.count())
                .join(WebVisitor, WebVisitor.id == WebPageView.visitor_id)
                .where(WebPageView.organization_id == org, WebPageView.viewed_at >= since)
                .where((WebVisitor.contact_id.is_not(None)) | (WebVisitor.lead_id.is_not(None)))
                .group_by(WebPageView.visitor_id, WebPageView.path)
            )
        ).all()
        counts: dict[uuid.UUID, dict[str, int]] = {}
        for visitor_id, path, count in rows:
            if any(fnmatch.fnmatch(path or "/", p) or (path or "/").startswith(p.rstrip("*")) for p in patterns):
                counts.setdefault(visitor_id, {})[path] = int(count)
        for visitor_id, paths in counts.items():
            if sum(paths.values()) < int(config.get("min_views") or 1):
                continue
            visitor = await db.get(WebVisitor, visitor_id)
            person = await _person(db, visitor) if visitor else None
            if not person:
                continue
            kind, record = person
            day = now.strftime("%Y-%m-%d")
            if await _fire(db, rule, record_type=kind, record_id=record.id, label=_name(record), dedupe=f"{kind}:{record.id}:{day}",
                           detail=f"Viewed {', '.join(sorted(paths))} ({sum(paths.values())} views in the last {days} day{'s' if days != 1 else ''}).",
                           owner=record.owner_user_id, account_id=getattr(record, "account_id", None), links={f"{kind}_id": record.id}):
                fired += 1
    elif rule.kind == "intent_surge":
        wanted = config.get("level", "high")
        order = {"low": 1, "medium": 2, "high": 3}
        week = now.strftime("%G-W%V")
        for row in await account_intent(db, org, days):
            if order.get(row["level"], 0) < order.get(wanted, 3) or not row["account_id"]:
                continue
            topics = ", ".join(t["name"] for t in row["topics"][:3])
            if await _fire(db, rule, record_type="account", record_id=uuid.UUID(row["account_id"]), label=row["name"], dedupe=f"account:{row['account_id']}:{week}",
                           detail=f"{row['level'].title()} buying intent (score {row['score']}) on {topics}.",
                           owner=uuid.UUID(row["owner_user_id"]) if row["owner_user_id"] else None, account_id=uuid.UUID(row["account_id"])):
                fired += 1
    elif rule.kind == "email_engagement":
        threshold = int(config.get("opens") or 3)
        rows = (
            await db.execute(
                select(SequenceMessage.enrollment_id, func.sum(SequenceMessage.open_count))
                .where(SequenceMessage.organization_id == org, SequenceMessage.sent_at >= since, SequenceMessage.status == "sent")
                .group_by(SequenceMessage.enrollment_id)
            )
        ).all()
        for enrollment_id, opens in rows:
            if int(opens or 0) < threshold:
                continue
            enrollment = await db.get(SequenceEnrollment, enrollment_id)
            if enrollment is None:
                continue
            model = Contact if enrollment.record_type == "contact" else Lead
            record = await db.get(model, enrollment.record_id)
            if record is None:
                continue
            if await _fire(db, rule, record_type=enrollment.record_type, record_id=record.id, label=_name(record), dedupe=f"enrollment:{enrollment.id}",
                           detail=f"Opened sequence emails {int(opens)} times in the last {days} days.", owner=record.owner_user_id,
                           account_id=getattr(record, "account_id", None), links={f"{enrollment.record_type}_id": record.id}):
                fired += 1
    elif rule.kind == "form_submitted":
        form_id = config.get("form_id")
        query = select(WebFormSubmission).where(WebFormSubmission.organization_id == org, WebFormSubmission.created_at >= since, WebFormSubmission.record_id.is_not(None))
        if form_id:
            query = query.where(WebFormSubmission.form_id == uuid.UUID(str(form_id)))
        for submission in (await db.execute(query.limit(500))).scalars():
            model = {"lead": Lead, "contact": Contact}.get(submission.record_type or "")
            record = await db.get(model, submission.record_id) if model else None
            if record is None:
                continue
            if await _fire(db, rule, record_type=submission.record_type, record_id=record.id, label=_name(record), dedupe=f"submission:{submission.id}",
                           detail="Submitted a web form.", owner=record.owner_user_id, account_id=getattr(record, "account_id", None),
                           links={f"{submission.record_type}_id": record.id}):
                fired += 1
    elif rule.kind == "deal_stalled":
        open_stages = select(PipelineStage.id).where(PipelineStage.organization_id == org, PipelineStage.category == "open")
        deals = (await db.execute(select(Opportunity).where(Opportunity.organization_id == org, Opportunity.stage_id.in_(open_stages)).limit(2000))).scalars().all()
        for deal in deals:
            last = (
                await db.execute(select(func.max(Activity.occurred_at)).where(Activity.organization_id == org, Activity.opportunity_id == deal.id))
            ).scalar_one()
            last = last or deal.stage_entered_at or deal.created_at
            if last and last < since:
                idle = (now - last).days
                if await _fire(db, rule, record_type="opportunity", record_id=deal.id, label=deal.name, dedupe=f"opportunity:{deal.id}:{last.date().isoformat()}",
                               detail=f"No activity for {idle} days.", owner=deal.owner_user_id, account_id=deal.account_id, links={"opportunity_id": deal.id}):
                    fired += 1
    elif rule.kind == "job_change":
        for contact in (
            await db.execute(select(Contact).where(Contact.organization_id == org, Contact.stage == "changed_job", Contact.updated_at >= since).limit(500))
        ).scalars():
            if await _fire(db, rule, record_type="contact", record_id=contact.id, label=_name(contact), dedupe=f"contact:{contact.id}",
                           detail=f"{_name(contact)} changed jobs. Find out where they went, and who replaces them.", owner=contact.owner_user_id,
                           account_id=contact.account_id, links={"contact_id": contact.id}):
                fired += 1
    elif rule.kind == "sequence_reply":
        for enrollment in (
            await db.execute(select(SequenceEnrollment).where(SequenceEnrollment.organization_id == org, SequenceEnrollment.status == "replied", SequenceEnrollment.finished_at >= since))
        ).scalars():
            model = Contact if enrollment.record_type == "contact" else Lead
            record = await db.get(model, enrollment.record_id)
            if record is None:
                continue
            if await _fire(db, rule, record_type=enrollment.record_type, record_id=record.id, label=_name(record), dedupe=f"enrollment:{enrollment.id}",
                           detail="Replied to a sequence email.", owner=enrollment.sender_user_id or record.owner_user_id,
                           account_id=getattr(record, "account_id", None), links={f"{enrollment.record_type}_id": record.id}):
                fired += 1
    elif rule.kind == "persona_match":
        persona_id = config.get("persona_id")
        query = select(Persona).where(Persona.organization_id == org, Persona.is_active.is_(True))
        if persona_id:
            query = query.where(Persona.id == uuid.UUID(str(persona_id)))
        personas = (await db.execute(query)).scalars().all()
        for kind, model in (("contact", Contact), ("lead", Lead)):
            for record in (await db.execute(select(model).where(model.organization_id == org, model.created_at >= since, model.job_title.is_not(None)).limit(1000))).scalars():
                for persona in personas:
                    if persona_matches(record.job_title, persona):
                        if await _fire(db, rule, record_type=kind, record_id=record.id, label=_name(record), dedupe=f"{kind}:{record.id}:{persona.id}",
                                       detail=f"{record.job_title} matches the {persona.name} persona.", owner=record.owner_user_id,
                                       account_id=getattr(record, "account_id", None), links={f"{kind}_id": record.id}):
                            fired += 1
                        break
    rule.last_evaluated_at = now
    return fired


async def evaluate_due(db: AsyncSession) -> int:
    """Scheduler job: evaluate signal rules at most every five minutes."""

    from app.services.billing import get_subscription, plan_state

    cutoff = utcnow() - timedelta(minutes=5)
    rules = (
        await db.execute(
            select(SignalRule).where(SignalRule.is_active.is_(True), (SignalRule.last_evaluated_at.is_(None)) | (SignalRule.last_evaluated_at <= cutoff)).limit(100)
        )
    ).scalars().all()
    allowed: dict[uuid.UUID, bool] = {}
    total = 0
    db.sync_session.info["automation_disabled"] = True
    for rule in rules:
        if rule.organization_id not in allowed:
            from app.services.metering import addon_features

            state = plan_state(await get_subscription(db, rule.organization_id))
            features = state.features | await addon_features(db, rule.organization_id)
            allowed[rule.organization_id] = "buying_intent" in features and not state.read_only
        if not allowed[rule.organization_id]:
            # Check again in five minutes.
            rule.last_evaluated_at = utcnow()
            continue
        try:
            total += await evaluate_rule(db, rule)
        except Exception:  # noqa: BLE001
            logger.exception("Signal rule %s failed", rule.id)
            rule.last_evaluated_at = utcnow()
    await db.commit()
    db.sync_session.info["automation_disabled"] = False
    return total
