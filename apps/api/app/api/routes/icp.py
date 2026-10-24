"""Ideal customer profile: personas, buying intent and signals."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.contact import Contact
from app.models.icp import IntentTopic, Persona, SignalEvent, SignalRule
from app.models.lead import Lead
from app.services import icp
from app.services.automation import record_audit
from app.services.enrichment import DEPARTMENTS, SENIORITY
from app.services.lookups import names_for

router = APIRouter(tags=["Ideal customer profile"])
FEATURE = [Depends(require_feature("buying_intent"))]


# ===========================================================================
# Personas
# ===========================================================================


class PersonaPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    color: str = Field(default="blue", max_length=20)
    title_keywords: list[str] = Field(default_factory=list, max_length=100)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=100)
    seniorities: list[str] = Field(default_factory=list, max_length=10)
    departments: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True


def _clean(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = value.strip()
        if value and value.lower() not in {v.lower() for v in out}:
            out.append(value[:80])
    return out


def _validate_persona(payload: PersonaPayload) -> None:
    payload.title_keywords = _clean(payload.title_keywords)
    payload.exclude_keywords = _clean(payload.exclude_keywords)
    allowed_seniority = {s for s, _ in SENIORITY}
    allowed_departments = {d for d, _ in DEPARTMENTS}
    if any(s not in allowed_seniority for s in payload.seniorities):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick seniorities from the list.")
    if any(d not in allowed_departments for d in payload.departments):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick departments from the list.")
    if not (payload.title_keywords or payload.seniorities or payload.departments):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add title keywords, a seniority or a department so AIVA knows who fits.")


def persona_out(row: Persona, count: int | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "color": row.color,
        "title_keywords": list(row.title_keywords or []),
        "exclude_keywords": list(row.exclude_keywords or []),
        "seniorities": list(row.seniorities or []),
        "departments": list(row.departments or []),
        "is_active": row.is_active,
        "position": row.position,
        "matches": count,
    }


@router.get("/personas", dependencies=FEATURE)
async def list_personas(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Persona).where(Persona.organization_id == principal.organization_id).order_by(Persona.position, Persona.name))).scalars().all()
    people = await icp.people_with_titles(db, principal.organization_id)
    counts = {str(r.id): sum(1 for _, p in people if icp.persona_matches(p.job_title, r)) for r in rows}
    matched_any = sum(1 for _, p in people if any(icp.persona_matches(p.job_title, r) for r in rows if r.is_active))
    return {
        "items": [persona_out(r, counts[str(r.id)]) for r in rows],
        "people_with_titles": len(people),
        "matched_any": matched_any,
        "seniorities": [s for s, _ in SENIORITY],
        "departments": [d for d, _ in DEPARTMENTS],
        "can_edit": principal.can("marketing.icp"),
    }


@router.post("/personas", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_persona(payload: PersonaPayload, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    _validate_persona(payload)
    if (await db.execute(select(Persona.id).where(Persona.organization_id == principal.organization_id, func.lower(Persona.name) == payload.name.strip().lower()))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A persona with that name already exists.")
    count = (await db.execute(select(func.count()).select_from(Persona).where(Persona.organization_id == principal.organization_id))).scalar_one()
    row = Persona(id=uuid.uuid4(), organization_id=principal.organization_id, position=count, **{**payload.model_dump(), "name": payload.name.strip()})
    db.add(row)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="persona", entity_id=row.id, entity_label=row.name)
    await db.commit()
    return persona_out(row)


@router.put("/personas/{persona_id}", dependencies=FEATURE)
async def update_persona(persona_id: uuid.UUID, payload: PersonaPayload, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Persona, persona_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Persona not found.")
    _validate_persona(payload)
    clash = (
        await db.execute(select(Persona.id).where(Persona.organization_id == principal.organization_id, func.lower(Persona.name) == payload.name.strip().lower(), Persona.id != row.id))
    ).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "A persona with that name already exists.")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.name = payload.name.strip()
    await db.commit()
    return persona_out(row)


@router.delete("/personas/{persona_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def delete_persona(persona_id: uuid.UUID, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Persona, persona_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Persona not found.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/personas/{persona_id}/people", dependencies=FEATURE)
async def persona_people(persona_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Persona, persona_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Persona not found.")
    people = [(k, p) for k, p in await icp.people_with_titles(db, principal.organization_id) if icp.persona_matches(p.job_title, row)]
    accounts = await names_for(db, principal.organization_id, "account", [getattr(p, "account_id", None) for _, p in people])
    return {"items": [icp.person_out(k, p, accounts) for k, p in people[:500]], "total": len(people)}


class PersonaTest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.post("/personas/test", dependencies=FEATURE)
async def test_title(payload: PersonaTest, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Persona).where(Persona.organization_id == principal.organization_id, Persona.is_active.is_(True)))).scalars().all()
    return {
        "seniority": icp.seniority(payload.title),
        "department": icp.department(payload.title),
        "personas": [persona_out(r) for r in rows if icp.persona_matches(payload.title, r)],
    }


@router.get("/personas/for/{record_type}/{record_id}", dependencies=FEATURE)
async def personas_for_record(record_type: str, record_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    model = {"contact": Contact, "lead": Lead}.get(record_type)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Personas apply to contacts and leads.")
    record = await db.get(model, record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    rows = (await db.execute(select(Persona).where(Persona.organization_id == principal.organization_id, Persona.is_active.is_(True)))).scalars().all()
    return {"items": [persona_out(r) for r in rows if icp.persona_matches(record.job_title, r)]}


# ===========================================================================
# Buying intent
# ===========================================================================


class TopicPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    url_patterns: list[str] = Field(default_factory=list, max_length=100)
    weight: int = Field(default=1, ge=1, le=10)
    is_active: bool = True


def topic_out(row: IntentTopic) -> dict[str, Any]:
    return {
        "id": str(row.id), "name": row.name, "description": row.description, "keywords": list(row.keywords or []),
        "url_patterns": list(row.url_patterns or []), "weight": row.weight, "is_active": row.is_active,
    }


def _validate_topic(payload: TopicPayload) -> None:
    payload.keywords = _clean(payload.keywords)
    payload.url_patterns = [p if p.startswith("/") else f"/{p}" for p in _clean(payload.url_patterns)]
    if not payload.keywords and not payload.url_patterns:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add keywords or page paths (like /pricing) for this topic.")


@router.get("/intent", dependencies=FEATURE)
async def buying_intent(days: int = Query(default=30, ge=1, le=180), principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    topics = (await db.execute(select(IntentTopic).where(IntentTopic.organization_id == principal.organization_id).order_by(IntentTopic.name))).scalars().all()
    accounts = await icp.account_intent(db, principal.organization_id, days)
    owners = await names_for(db, principal.organization_id, "user", [a["owner_user_id"] for a in accounts])
    for account in accounts:
        account["owner_name"] = owners.get(account["owner_user_id"]) if account["owner_user_id"] else None
    return {
        "topics": [topic_out(t) for t in topics],
        "accounts": accounts,
        "levels": {lvl: sum(1 for a in accounts if a["level"] == lvl) for lvl in ("high", "medium", "low")},
        "can_edit": principal.can("marketing.icp"),
    }


@router.get("/intent/account/{account_id}", dependencies=FEATURE)
async def account_intent(account_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = [a for a in await icp.account_intent(db, principal.organization_id, 30) if a["account_id"] == str(account_id)]
    return rows[0] if rows else {"account_id": str(account_id), "level": None, "score": 0, "topics": []}


@router.post("/intent/topics", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_topic(payload: TopicPayload, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    _validate_topic(payload)
    if (await db.execute(select(IntentTopic.id).where(IntentTopic.organization_id == principal.organization_id, func.lower(IntentTopic.name) == payload.name.strip().lower()))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A topic with that name already exists.")
    row = IntentTopic(id=uuid.uuid4(), organization_id=principal.organization_id, **{**payload.model_dump(), "name": payload.name.strip()})
    db.add(row)
    await db.commit()
    return topic_out(row)


@router.put("/intent/topics/{topic_id}", dependencies=FEATURE)
async def update_topic(topic_id: uuid.UUID, payload: TopicPayload, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    row = await db.get(IntentTopic, topic_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found.")
    _validate_topic(payload)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.name = payload.name.strip()
    await db.commit()
    return topic_out(row)


@router.delete("/intent/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def delete_topic(topic_id: uuid.UUID, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    row = await db.get(IntentTopic, topic_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Signals
# ===========================================================================


class SignalPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    notify_owner: bool = True
    create_task: bool = False
    is_active: bool = True


def _validate_signal(payload: SignalPayload) -> None:
    if payload.kind not in icp.SIGNAL_KINDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick what the signal watches for.")
    config = {**icp.SIGNAL_KINDS[payload.kind]["defaults"], **payload.config}
    allowed = set(icp.SIGNAL_KINDS[payload.kind]["defaults"]) | {"form_id"}
    config = {k: v for k, v in config.items() if k in allowed}
    try:
        config["days"] = max(1, min(180, int(config.get("days") or 1)))
        if payload.kind == "page_visit":
            paths = [str(p).strip() for p in config.get("paths") or [] if str(p).strip()]
            if not paths:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one page path, like /pricing.")
            config["paths"] = [p if p.startswith("/") else f"/{p}" for p in paths][:20]
            config["min_views"] = max(1, min(100, int(config.get("min_views") or 1)))
        if payload.kind == "email_engagement":
            config["opens"] = max(1, min(100, int(config.get("opens") or 3)))
        if payload.kind == "intent_surge" and config.get("level") not in ("low", "medium", "high"):
            config["level"] = "high"
        if payload.kind == "persona_match" and config.get("persona_id"):
            config["persona_id"] = str(uuid.UUID(str(config["persona_id"])))
        if payload.kind == "form_submitted" and config.get("form_id"):
            config["form_id"] = str(uuid.UUID(str(config["form_id"])))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Check the signal's numbers and choices.") from exc
    payload.config = config


def signal_out(row: SignalRule) -> dict[str, Any]:
    return {
        "id": str(row.id), "name": row.name, "kind": row.kind, "kind_label": icp.SIGNAL_KINDS.get(row.kind, {}).get("label", row.kind),
        "config": row.config or {}, "notify_owner": row.notify_owner, "create_task": row.create_task, "is_active": row.is_active,
        "last_evaluated_at": row.last_evaluated_at, "fire_count": row.fire_count,
    }


@router.get("/signals/rules", dependencies=FEATURE)
async def list_signal_rules(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(SignalRule).where(SignalRule.organization_id == principal.organization_id).order_by(SignalRule.created_at))).scalars().all()
    return {
        "items": [signal_out(r) for r in rows],
        "kinds": [{"key": k, "label": v["label"], "defaults": v["defaults"]} for k, v in icp.SIGNAL_KINDS.items()],
        "can_edit": principal.can("marketing.icp"),
    }


@router.post("/signals/rules", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_signal_rule(payload: SignalPayload, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    _validate_signal(payload)
    row = SignalRule(id=uuid.uuid4(), organization_id=principal.organization_id, created_by_user_id=principal.user_id, **{**payload.model_dump(), "name": payload.name.strip()})
    db.add(row)
    await db.commit()
    return signal_out(row)


@router.put("/signals/rules/{rule_id}", dependencies=FEATURE)
async def update_signal_rule(rule_id: uuid.UUID, payload: SignalPayload, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SignalRule, rule_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signal not found.")
    _validate_signal(payload)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.name = payload.name.strip()
    await db.commit()
    return signal_out(row)


@router.delete("/signals/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def delete_signal_rule(rule_id: uuid.UUID, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SignalRule, rule_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signal not found.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/signals/rules/{rule_id}/run", dependencies=FEATURE)
async def run_signal_rule(rule_id: uuid.UUID, principal: Principal = Depends(require_permission("marketing.icp")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SignalRule, rule_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signal not found.")
    db.sync_session.info["automation_disabled"] = True
    fired = await icp.evaluate_rule(db, row)
    await db.commit()
    return {"fired": fired, "rule": signal_out(row)}


@router.get("/signals/events", dependencies=FEATURE)
async def list_signal_events(
    status_filter: str | None = Query(default="new", alias="status", pattern="^(new|actioned|dismissed|all)$"),
    rule_id: uuid.UUID | None = None,
    mine: bool = False,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(SignalEvent, SignalRule.name, SignalRule.kind).join(SignalRule, SignalRule.id == SignalEvent.rule_id).where(SignalEvent.organization_id == principal.organization_id)
    if status_filter and status_filter != "all":
        query = query.where(SignalEvent.status == status_filter)
    if rule_id:
        query = query.where(SignalEvent.rule_id == rule_id)
    if mine or not principal.can("records.view_all"):
        query = query.where(SignalEvent.owner_user_id == principal.user_id)
    rows = (await db.execute(query.order_by(SignalEvent.fired_at.desc()).limit(500))).all()
    owners = await names_for(db, principal.organization_id, "user", [e.owner_user_id for e, _, _ in rows])
    accounts = await names_for(db, principal.organization_id, "account", [e.account_id for e, _, _ in rows])
    counts = dict(
        (await db.execute(select(SignalEvent.status, func.count()).where(SignalEvent.organization_id == principal.organization_id).group_by(SignalEvent.status))).all()
    )
    return {
        "items": [
            {
                "id": str(e.id), "rule_id": str(e.rule_id), "rule_name": name, "kind": kind, "record_type": e.record_type, "record_id": str(e.record_id),
                "record_label": e.record_label, "href": f"/{'opportunities' if e.record_type == 'opportunity' else e.record_type + 's'}/{e.record_id}",
                "account_id": str(e.account_id) if e.account_id else None, "account_name": accounts.get(str(e.account_id)) if e.account_id else None,
                "owner_name": owners.get(str(e.owner_user_id)) if e.owner_user_id else None, "detail": e.detail, "fired_at": e.fired_at, "status": e.status,
            }
            for e, name, kind in rows
        ],
        "counts": {k: int(v) for k, v in counts.items()},
    }


class EventAction(BaseModel):
    status: str = Field(pattern="^(new|actioned|dismissed)$")


@router.post("/signals/events/{event_id}", dependencies=FEATURE)
async def act_on_signal(event_id: uuid.UUID, payload: EventAction, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SignalEvent, event_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signal not found.")
    row.status = payload.status
    row.actioned_by_user_id = principal.user_id if payload.status != "new" else None
    row.actioned_at = icp.utcnow() if payload.status != "new" else None
    await db.commit()
    return {"id": str(row.id), "status": row.status}
