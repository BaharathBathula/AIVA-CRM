"""Service desk: cases, SLA policies and knowledge base."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, case as sa_case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.membership import OrganizationMembership
from app.models.platform import Case, CaseComment, KnowledgeArticle, Notification, SlaPolicy, Team
from app.services.catalog import CASE_CHANNEL, CASE_PRIORITY, CASE_STATUS
from app.services.lookups import names_for

router = APIRouter(tags=["Service"])

OPEN_STATUSES = ("new", "open", "pending", "on_hold")
DONE_STATUSES = ("resolved", "closed")


def now() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# SLA policies
# ===========================================================================


class SlaPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    priority: str = Field(pattern="^(low|medium|high|urgent)$")
    first_response_minutes: int = Field(ge=1, le=60 * 24 * 30)
    resolution_minutes: int = Field(ge=1, le=60 * 24 * 90)
    is_active: bool = True


def sla_out(policy: SlaPolicy) -> dict:
    return {
        "id": str(policy.id),
        "name": policy.name,
        "priority": policy.priority,
        "first_response_minutes": policy.first_response_minutes,
        "resolution_minutes": policy.resolution_minutes,
        "is_active": policy.is_active,
    }


@router.get("/sla-policies")
async def list_sla_policies(
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    policies = (
        await db.execute(select(SlaPolicy).where(SlaPolicy.organization_id == principal.organization_id))
    ).scalars().all()
    return [sla_out(p) for p in sorted(policies, key=lambda p: (order.get(p.priority, 9), p.name))]


def _check_sla(payload: SlaPayload) -> None:
    if payload.resolution_minutes < payload.first_response_minutes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Resolution time can't be shorter than first response time.")


@router.post("/sla-policies", status_code=status.HTTP_201_CREATED)
async def create_sla_policy(
    payload: SlaPayload,
    principal: Principal = Depends(require_permission("service.sla_manage")),
    db: AsyncSession = Depends(get_db),
):
    _check_sla(payload)
    policy = SlaPolicy(id=uuid.uuid4(), organization_id=principal.organization_id, **payload.model_dump())
    db.add(policy)
    await db.commit()
    return sla_out(policy)


@router.patch("/sla-policies/{policy_id}")
async def update_sla_policy(
    policy_id: uuid.UUID,
    payload: SlaPayload,
    principal: Principal = Depends(require_permission("service.sla_manage")),
    db: AsyncSession = Depends(get_db),
):
    _check_sla(payload)
    policy = await db.get(SlaPolicy, policy_id)
    if policy is None or policy.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA policy not found.")
    for key, value in payload.model_dump().items():
        setattr(policy, key, value)
    await db.commit()
    return sla_out(policy)


@router.delete("/sla-policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sla_policy(
    policy_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.sla_manage")),
    db: AsyncSession = Depends(get_db),
):
    policy = await db.get(SlaPolicy, policy_id)
    if policy is None or policy.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA policy not found.")
    await db.delete(policy)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _policy_for(db: AsyncSession, organization_id: uuid.UUID, priority: str) -> SlaPolicy | None:
    return (
        await db.execute(
            select(SlaPolicy)
            .where(
                SlaPolicy.organization_id == organization_id,
                SlaPolicy.priority == priority,
                SlaPolicy.is_active.is_(True),
            )
            .order_by(SlaPolicy.first_response_minutes)
            .limit(1)
        )
    ).scalar_one_or_none()


def _apply_sla(case: Case, policy: SlaPolicy | None, start: datetime) -> None:
    case.sla_policy_id = policy.id if policy else None
    if policy is None:
        case.first_response_due_at = None
        case.resolution_due_at = None
        return
    if case.first_responded_at is None:
        case.first_response_due_at = start + timedelta(minutes=policy.first_response_minutes)
    case.resolution_due_at = start + timedelta(minutes=policy.resolution_minutes)


def sla_state(case: Case, at: datetime | None = None) -> dict:
    at = at or now()
    if case.status in DONE_STATUSES:
        if case.resolution_due_at is None:
            return {"state": "none"}
        done = case.resolved_at or case.closed_at or at
        return {"state": "met" if done <= case.resolution_due_at else "missed", "target": "resolution"}

    if case.first_responded_at is None and case.first_response_due_at is not None:
        target, due = "first_response", case.first_response_due_at
        window = (case.first_response_due_at - case.created_at) if case.created_at else None
    elif case.resolution_due_at is not None:
        target, due = "resolution", case.resolution_due_at
        window = (case.resolution_due_at - case.created_at) if case.created_at else None
    else:
        return {"state": "none"}

    remaining = (due - at).total_seconds() / 60
    if remaining < 0:
        state = "breached"
    else:
        threshold = max(30.0, (window.total_seconds() / 60 * 0.25) if window else 60.0)
        state = "at_risk" if remaining <= threshold else "on_track"
    return {"state": state, "target": target, "due_at": due, "minutes_remaining": round(remaining)}


# ===========================================================================
# Cases
# ===========================================================================


class CaseCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=250)
    description: str | None = None
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    channel: str = Field(default="email", pattern="^(email|phone|web|chat|portal|social)$")
    category: str | None = Field(default=None, max_length=80)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    assigned_to_user_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class CaseUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=250)
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(new|open|pending|on_hold|resolved|closed)$")
    priority: str | None = Field(default=None, pattern="^(low|medium|high|urgent)$")
    channel: str | None = Field(default=None, pattern="^(email|phone|web|chat|portal|social)$")
    category: str | None = Field(default=None, max_length=80)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    assigned_to_user_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    tags: list[str] | None = None
    satisfaction_rating: int | None = Field(default=None, ge=1, le=5)


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    is_internal: bool = False


class ArticleLink(BaseModel):
    article_id: uuid.UUID


async def _validate_case_links(db: AsyncSession, organization_id: uuid.UUID, data: dict) -> None:
    for key, model, label in (
        ("account_id", Account, "account"),
        ("contact_id", Contact, "contact"),
        ("team_id", Team, "queue"),
    ):
        value = data.get(key)
        if value is None:
            continue
        record = await db.get(model, value)
        if record is None or record.organization_id != organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The selected {label} isn't in this workspace.")
    assignee = data.get("assigned_to_user_id")
    if assignee is not None:
        member = (
            await db.execute(
                select(OrganizationMembership.id).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.user_id == assignee,
                    OrganizationMembership.status == "active",
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Assign the case to an active member of this workspace.")
    if data.get("contact_id") and not data.get("account_id"):
        contact = await db.get(Contact, data["contact_id"])
        data["account_id"] = contact.account_id


def _notify_assignee(db: AsyncSession, case: Case, actor: uuid.UUID) -> None:
    if case.assigned_to_user_id and case.assigned_to_user_id != actor:
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=case.organization_id,
                user_id=case.assigned_to_user_id,
                kind="assignment",
                title=f"Case CS-{case.number} assigned to you",
                body=case.subject,
                link=f"/cases/{case.id}",
            )
        )


async def cases_out(db: AsyncSession, organization_id: uuid.UUID, cases: list[Case]) -> list[dict]:
    users = await names_for(db, organization_id, "user", [c.assigned_to_user_id for c in cases])
    accounts = await names_for(db, organization_id, "account", [c.account_id for c in cases])
    contacts = await names_for(db, organization_id, "contact", [c.contact_id for c in cases])
    teams = await names_for(db, organization_id, "team", [c.team_id for c in cases])
    at = now()
    return [
        {
            "id": str(c.id),
            "number": c.number,
            "display_number": f"CS-{c.number}",
            "subject": c.subject,
            "status": c.status,
            "priority": c.priority,
            "channel": c.channel,
            "category": c.category,
            "account_id": str(c.account_id) if c.account_id else None,
            "account_name": accounts.get(str(c.account_id)),
            "contact_id": str(c.contact_id) if c.contact_id else None,
            "contact_name": contacts.get(str(c.contact_id)),
            "assigned_to_user_id": str(c.assigned_to_user_id) if c.assigned_to_user_id else None,
            "assigned_to_name": users.get(str(c.assigned_to_user_id)),
            "team_id": str(c.team_id) if c.team_id else None,
            "team_name": teams.get(str(c.team_id)),
            "tags": c.tags or [],
            "sla": sla_state(c, at),
            "first_response_due_at": c.first_response_due_at,
            "resolution_due_at": c.resolution_due_at,
            "first_responded_at": c.first_responded_at,
            "resolved_at": c.resolved_at,
            "closed_at": c.closed_at,
            "satisfaction_rating": c.satisfaction_rating,
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        }
        for c in cases
    ]


def _breached_clause(at: datetime):
    return and_(
        Case.status.in_(OPEN_STATUSES),
        or_(
            and_(Case.first_responded_at.is_(None), Case.first_response_due_at < at),
            Case.resolution_due_at < at,
        ),
    )


@router.get("/cases")
async def list_cases(
    view: str = Query(default="all_open", pattern="^(my_open|unassigned|all_open|breached|resolved|all)$"),
    case_status: str | None = Query(default=None, alias="status"),
    priority: str | None = None,
    assigned_to_user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    at = now()
    query = select(Case).where(Case.organization_id == principal.organization_id)
    if view == "my_open":
        query = query.where(Case.assigned_to_user_id == principal.user_id, Case.status.in_(OPEN_STATUSES))
    elif view == "unassigned":
        query = query.where(Case.assigned_to_user_id.is_(None), Case.status.in_(OPEN_STATUSES))
    elif view == "all_open":
        query = query.where(Case.status.in_(OPEN_STATUSES))
    elif view == "breached":
        query = query.where(_breached_clause(at))
    elif view == "resolved":
        query = query.where(Case.status.in_(DONE_STATUSES))
    if case_status:
        query = query.where(Case.status == case_status)
    if priority:
        query = query.where(Case.priority == priority)
    if assigned_to_user_id:
        query = query.where(Case.assigned_to_user_id == assigned_to_user_id)
    if team_id:
        query = query.where(Case.team_id == team_id)
    if account_id:
        query = query.where(Case.account_id == account_id)
    if contact_id:
        query = query.where(Case.contact_id == contact_id)
    if search:
        term = search.strip().lower().removeprefix("cs-")
        conditions = [Case.subject.ilike(f"%{search}%"), Case.description.ilike(f"%{search}%")]
        if term.isdigit():
            conditions.append(Case.number == int(term))
        query = query.where(or_(*conditions))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    priority_order = sa_case({"urgent": 0, "high": 1, "medium": 2, "low": 3}, value=Case.priority, else_=4)
    ordering = (
        [Case.resolved_at.desc().nulls_last(), Case.number.desc()]
        if view == "resolved"
        else [priority_order, func.coalesce(Case.first_response_due_at, Case.resolution_due_at).asc().nulls_last(), Case.number.desc()]
    )
    cases = (await db.execute(query.order_by(*ordering).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"total": total, "page": page, "page_size": page_size, "items": await cases_out(db, principal.organization_id, cases)}


@router.get("/cases/stats")
async def case_stats(
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    at = now()
    org = Case.organization_id == principal.organization_id

    async def count(*where) -> int:
        return (await db.execute(select(func.count()).select_from(Case).where(org, *where))).scalar_one()

    start_of_day = at.replace(hour=0, minute=0, second=0, microsecond=0)
    month_ago = at - timedelta(days=30)
    first_response = (
        await db.execute(
            select(func.avg(func.extract("epoch", Case.first_responded_at - Case.created_at) / 3600)).where(
                org, Case.first_responded_at.is_not(None), Case.created_at >= month_ago
            )
        )
    ).scalar_one()
    resolution = (
        await db.execute(
            select(func.avg(func.extract("epoch", Case.resolved_at - Case.created_at) / 3600)).where(
                org, Case.resolved_at.is_not(None), Case.created_at >= month_ago
            )
        )
    ).scalar_one()
    csat = (
        await db.execute(select(func.avg(Case.satisfaction_rating)).where(org, Case.satisfaction_rating.is_not(None)))
    ).scalar_one()
    by_priority = dict(
        (
            await db.execute(
                select(Case.priority, func.count()).where(org, Case.status.in_(OPEN_STATUSES)).group_by(Case.priority)
            )
        ).all()
    )
    met = await count(Case.resolved_at.is_not(None), Case.resolution_due_at.is_not(None), Case.resolved_at <= Case.resolution_due_at, Case.created_at >= month_ago)
    with_target = await count(Case.resolved_at.is_not(None), Case.resolution_due_at.is_not(None), Case.created_at >= month_ago)
    return {
        "open": await count(Case.status.in_(OPEN_STATUSES)),
        "mine": await count(Case.status.in_(OPEN_STATUSES), Case.assigned_to_user_id == principal.user_id),
        "unassigned": await count(Case.status.in_(OPEN_STATUSES), Case.assigned_to_user_id.is_(None)),
        "breached": await count(_breached_clause(at)),
        "resolved_today": await count(Case.resolved_at >= start_of_day),
        "avg_first_response_hours": round(float(first_response), 1) if first_response is not None else None,
        "avg_resolution_hours": round(float(resolution), 1) if resolution is not None else None,
        "csat": round(float(csat), 2) if csat is not None else None,
        "sla_met_percent": round(met / with_target * 100, 1) if with_target else None,
        "open_by_priority": {p: by_priority.get(p, 0) for p in CASE_PRIORITY},
    }


async def _get_case(db: AsyncSession, principal: Principal, case_id: uuid.UUID) -> Case:
    case = await db.get(Case, case_id)
    if case is None or case.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found.")
    return case


async def case_detail(db: AsyncSession, case: Case) -> dict:
    data = (await cases_out(db, case.organization_id, [case]))[0]
    comments = (
        await db.execute(select(CaseComment).where(CaseComment.case_id == case.id).order_by(CaseComment.created_at))
    ).scalars().all()
    authors = await names_for(db, case.organization_id, "user", [c.author_user_id for c in comments] + [case.created_by_user_id])
    article_ids = [uuid.UUID(a) for a in (case.linked_article_ids or [])]
    articles = []
    if article_ids:
        articles = (
            await db.execute(select(KnowledgeArticle).where(KnowledgeArticle.id.in_(article_ids)))
        ).scalars().all()
    policy = await db.get(SlaPolicy, case.sla_policy_id) if case.sla_policy_id else None
    data.update(
        {
            "description": case.description,
            "created_by_name": authors.get(str(case.created_by_user_id)),
            "sla_policy": sla_out(policy) if policy else None,
            "comments": [
                {
                    "id": str(c.id),
                    "body": c.body,
                    "is_internal": c.is_internal,
                    "author_user_id": str(c.author_user_id) if c.author_user_id else None,
                    "author_name": authors.get(str(c.author_user_id), "Customer"),
                    "created_at": c.created_at,
                }
                for c in comments
            ],
            "linked_articles": [{"id": str(a.id), "title": a.title, "status": a.status} for a in articles],
        }
    )
    return data


@router.post("/cases", status_code=status.HTTP_201_CREATED)
async def create_case(
    payload: CaseCreate,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    await _validate_case_links(db, principal.organization_id, data)
    number = (
        await db.execute(select(func.coalesce(func.max(Case.number), 1000)).where(Case.organization_id == principal.organization_id))
    ).scalar_one() + 1
    created = now()
    case = Case(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        number=number,
        status="new",
        created_by_user_id=principal.user_id,
        linked_article_ids=[],
        **data,
    )
    case.created_at = created
    _apply_sla(case, await _policy_for(db, principal.organization_id, case.priority), created)
    db.add(case)
    _notify_assignee(db, case, principal.user_id)
    await db.commit()
    await db.refresh(case)
    return await case_detail(db, case)


@router.get("/cases/{case_id}")
async def get_case(
    case_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    return await case_detail(db, await _get_case(db, principal, case_id))


@router.patch("/cases/{case_id}")
async def update_case(
    case_id: uuid.UUID,
    payload: CaseUpdate,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case(db, principal, case_id)
    data = payload.model_dump(exclude_unset=True)
    await _validate_case_links(db, principal.organization_id, data)
    previous_assignee = case.assigned_to_user_id
    previous_priority = case.priority
    previous_status = case.status

    for key, value in data.items():
        setattr(case, key, value)

    if case.priority != previous_priority and case.status in OPEN_STATUSES:
        _apply_sla(case, await _policy_for(db, principal.organization_id, case.priority), case.created_at)

    if case.status != previous_status:
        if case.status == "resolved":
            case.resolved_at = case.resolved_at or now()
        elif case.status == "closed":
            case.resolved_at = case.resolved_at or now()
            case.closed_at = now()
        elif case.status in OPEN_STATUSES:
            case.resolved_at = None
            case.closed_at = None

    if case.assigned_to_user_id != previous_assignee:
        _notify_assignee(db, case, principal.user_id)

    await db.commit()
    await db.refresh(case)
    return await case_detail(db, case)


@router.post("/cases/{case_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    case_id: uuid.UUID,
    payload: CommentCreate,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case(db, principal, case_id)
    db.add(
        CaseComment(
            id=uuid.uuid4(),
            organization_id=principal.organization_id,
            case_id=case.id,
            author_user_id=principal.user_id,
            body=payload.body.strip(),
            is_internal=payload.is_internal,
        )
    )
    if not payload.is_internal:
        if case.first_responded_at is None:
            case.first_responded_at = now()
        if case.status == "new":
            case.status = "open"
    if case.assigned_to_user_id is None:
        case.assigned_to_user_id = principal.user_id
    await db.commit()
    await db.refresh(case)
    return await case_detail(db, case)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.cases", "records.delete")),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case(db, principal, case_id)
    await db.delete(case)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


STOP_WORDS = {"with", "from", "that", "this", "have", "when", "what", "their", "there", "about", "after", "before", "cannot", "can't", "does", "into", "your", "will", "been", "were", "they", "them", "then", "than"}


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{3,}", text.lower())
    return [w for w in dict.fromkeys(words) if w not in STOP_WORDS][:8]


@router.get("/cases/{case_id}/suggested-articles")
async def suggested_articles(
    case_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case(db, principal, case_id)
    words = _keywords(f"{case.subject} {case.category or ''}")
    if not words:
        return []
    articles = (
        await db.execute(
            select(KnowledgeArticle).where(
                KnowledgeArticle.organization_id == principal.organization_id,
                KnowledgeArticle.status == "published",
                or_(*[or_(KnowledgeArticle.title.ilike(f"%{w}%"), KnowledgeArticle.body.ilike(f"%{w}%")) for w in words]),
            )
        )
    ).scalars().all()
    linked = set(case.linked_article_ids or [])

    def score(article: KnowledgeArticle) -> int:
        title, body = article.title.lower(), article.body.lower()
        return sum(3 if w in title else 1 if w in body else 0 for w in words)

    ranked = sorted(articles, key=lambda a: (-score(a), -a.helpful_count))[:5]
    return [
        {"id": str(a.id), "title": a.title, "summary": a.summary, "category": a.category, "linked": str(a.id) in linked}
        for a in ranked
    ]


@router.post("/cases/{case_id}/articles")
async def link_article(
    case_id: uuid.UUID,
    payload: ArticleLink,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case(db, principal, case_id)
    article = await db.get(KnowledgeArticle, payload.article_id)
    if article is None or article.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found.")
    ids = list(case.linked_article_ids or [])
    if str(article.id) not in ids:
        case.linked_article_ids = ids + [str(article.id)]
    await db.commit()
    await db.refresh(case)
    return await case_detail(db, case)


@router.delete("/cases/{case_id}/articles/{article_id}")
async def unlink_article(
    case_id: uuid.UUID,
    article_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case(db, principal, case_id)
    case.linked_article_ids = [a for a in (case.linked_article_ids or []) if a != str(article_id)]
    await db.commit()
    await db.refresh(case)
    return await case_detail(db, case)


# ===========================================================================
# Knowledge base
# ===========================================================================


class ArticlePayload(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    summary: str | None = Field(default=None, max_length=500)
    body: str = Field(default="", max_length=100000)
    category: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list)


class Feedback(BaseModel):
    helpful: bool


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:200] or "article"


async def _unique_article_slug(db: AsyncSession, organization_id: uuid.UUID, title: str, exclude: uuid.UUID | None = None) -> str:
    base, candidate, index = _slug(title), _slug(title), 2
    while True:
        query = select(KnowledgeArticle.id).where(
            KnowledgeArticle.organization_id == organization_id, KnowledgeArticle.slug == candidate
        )
        if exclude:
            query = query.where(KnowledgeArticle.id != exclude)
        if (await db.execute(query)).scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{index}"
        index += 1


async def article_out(db: AsyncSession, article: KnowledgeArticle, detail: bool = False) -> dict:
    authors = await names_for(db, article.organization_id, "user", [article.author_user_id])
    data = {
        "id": str(article.id),
        "title": article.title,
        "slug": article.slug,
        "summary": article.summary,
        "category": article.category,
        "status": article.status,
        "tags": article.tags or [],
        "author_name": authors.get(str(article.author_user_id)),
        "view_count": article.view_count,
        "helpful_count": article.helpful_count,
        "not_helpful_count": article.not_helpful_count,
        "published_at": article.published_at,
        "updated_at": article.updated_at,
        "created_at": article.created_at,
    }
    if detail:
        data["body"] = article.body
    return data


@router.get("/knowledge")
async def list_articles(
    search: str | None = Query(default=None, max_length=200),
    category: str | None = None,
    article_status: str | None = Query(default=None, alias="status"),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(KnowledgeArticle).where(KnowledgeArticle.organization_id == principal.organization_id)
    can_write = principal.can("service.knowledge_write") or principal.can("service.knowledge_publish")
    if not can_write:
        query = query.where(KnowledgeArticle.status == "published")
    elif article_status:
        query = query.where(KnowledgeArticle.status == article_status)
    if category:
        query = query.where(KnowledgeArticle.category == category)
    if search:
        query = query.where(
            or_(
                KnowledgeArticle.title.ilike(f"%{search}%"),
                KnowledgeArticle.summary.ilike(f"%{search}%"),
                KnowledgeArticle.body.ilike(f"%{search}%"),
            )
        )
    articles = (
        await db.execute(query.order_by(KnowledgeArticle.status, KnowledgeArticle.view_count.desc(), KnowledgeArticle.title))
    ).scalars().all()
    categories = (
        await db.execute(
            select(KnowledgeArticle.category, func.count())
            .where(KnowledgeArticle.organization_id == principal.organization_id, KnowledgeArticle.category.is_not(None))
            .group_by(KnowledgeArticle.category)
            .order_by(KnowledgeArticle.category)
        )
    ).all()
    return {
        "items": [await article_out(db, a) for a in articles],
        "categories": [{"name": name, "count": count} for name, count in categories],
        "can_write": can_write,
        "can_publish": principal.can("service.knowledge_publish"),
    }


async def _get_article(db: AsyncSession, principal: Principal, article_id: uuid.UUID) -> KnowledgeArticle:
    article = await db.get(KnowledgeArticle, article_id)
    if article is None or article.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found.")
    if article.status != "published" and not (
        principal.can("service.knowledge_write") or principal.can("service.knowledge_publish")
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found.")
    return article


@router.post("/knowledge", status_code=status.HTTP_201_CREATED)
async def create_article(
    payload: ArticlePayload,
    principal: Principal = Depends(require_permission("service.knowledge_write")),
    db: AsyncSession = Depends(get_db),
):
    article = KnowledgeArticle(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        slug=await _unique_article_slug(db, principal.organization_id, payload.title),
        status="draft",
        author_user_id=principal.user_id,
        **payload.model_dump(),
    )
    db.add(article)
    await db.commit()
    await db.refresh(article)
    return await article_out(db, article, detail=True)


@router.get("/knowledge/{article_id}")
async def get_article(
    article_id: uuid.UUID,
    track: bool = False,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    article = await _get_article(db, principal, article_id)
    if track and article.status == "published":
        db.sync_session.info["automation_disabled"] = True
        article.view_count += 1
        await db.commit()
        await db.refresh(article)
    return await article_out(db, article, detail=True)


@router.patch("/knowledge/{article_id}")
async def update_article(
    article_id: uuid.UUID,
    payload: ArticlePayload,
    principal: Principal = Depends(require_permission("service.knowledge_write")),
    db: AsyncSession = Depends(get_db),
):
    article = await _get_article(db, principal, article_id)
    if article.title != payload.title:
        article.slug = await _unique_article_slug(db, principal.organization_id, payload.title, exclude=article.id)
    for key, value in payload.model_dump().items():
        setattr(article, key, value)
    await db.commit()
    await db.refresh(article)
    return await article_out(db, article, detail=True)


@router.post("/knowledge/{article_id}/publish")
async def publish_article(
    article_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.knowledge_publish")),
    db: AsyncSession = Depends(get_db),
):
    article = await _get_article(db, principal, article_id)
    if not article.body.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "Write the article body before publishing.")
    article.status = "published"
    article.published_at = now()
    await db.commit()
    await db.refresh(article)
    return await article_out(db, article, detail=True)


@router.post("/knowledge/{article_id}/archive")
async def archive_article(
    article_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.knowledge_publish")),
    db: AsyncSession = Depends(get_db),
):
    article = await _get_article(db, principal, article_id)
    article.status = "archived"
    await db.commit()
    await db.refresh(article)
    return await article_out(db, article, detail=True)


@router.post("/knowledge/{article_id}/feedback")
async def article_feedback(
    article_id: uuid.UUID,
    payload: Feedback,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    article = await _get_article(db, principal, article_id)
    db.sync_session.info["automation_disabled"] = True
    if payload.helpful:
        article.helpful_count += 1
    else:
        article.not_helpful_count += 1
    await db.commit()
    return {"helpful_count": article.helpful_count, "not_helpful_count": article.not_helpful_count}


@router.delete("/knowledge/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(
    article_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.knowledge_publish")),
    db: AsyncSession = Depends(get_db),
):
    article = await _get_article(db, principal, article_id)
    await db.delete(article)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/service/options")
async def service_options(_: Principal = Depends(require_permission("service.cases"))):
    return {"statuses": CASE_STATUS, "priorities": CASE_PRIORITY, "channels": CASE_CHANNEL}
