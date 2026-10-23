"""Customer and partner portals: admin setup and the portal's own API.

Portal users sign in with a one-time link sent to their email (no
password). Customers see their cases, raise new ones, read published
knowledge articles and accept or decline quotes; partners register deals.
Every portal request is limited to records linked to the portal user's
contact and account.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.core.config import get_settings
from app.core.crypto import new_token, sha256
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.editions import Portal, PortalToken, PortalUser, SalesInvoice, Subscription
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.platform import Case, CaseComment, KnowledgeArticle, Notification, Quote
from app.services import mailer
from app.services.billing import plan_state
from app.services.lookups import names_for

router = APIRouter(tags=["Portals"])

MODULES = {
    "customer": ("cases", "knowledge", "quotes", "invoices"),
    "partner": ("deal_registration", "knowledge"),
}
LOGIN_MINUTES = 30
SESSION_DAYS = 7


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def portal_feature(audience: str) -> str:
    return "customer_portal" if audience == "customer" else "custom_portals"


# ===========================================================================
# Admin
# ===========================================================================


class PortalPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    audience: str = Field(default="customer", pattern="^(customer|partner)$")
    settings: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


def _settings(audience: str, raw: dict[str, Any]) -> dict[str, Any]:
    modules = [m for m in raw.get("modules") or MODULES[audience] if m in MODULES[audience]]
    accent = str(raw.get("accent") or "").strip()
    if accent and not re.match(r"^#[0-9a-fA-F]{6}$", accent):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use a hex color such as #2563eb.")
    return {
        "modules": modules or list(MODULES[audience]),
        "accent": accent or None,
        "welcome": str(raw.get("welcome") or "")[:500] or None,
        "logo_text": str(raw.get("logo_text") or "")[:60] or None,
        "support_email": str(raw.get("support_email") or "")[:320] or None,
    }


def portal_url(portal: Portal) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/portal/{portal.slug}"


async def portal_out(db: AsyncSession, portal: Portal) -> dict[str, Any]:
    users = (await db.execute(select(func.count()).select_from(PortalUser).where(PortalUser.portal_id == portal.id))).scalar_one()
    return {
        "id": str(portal.id),
        "name": portal.name,
        "slug": portal.slug,
        "audience": portal.audience,
        "settings": portal.settings,
        "is_active": portal.is_active,
        "url": portal_url(portal),
        "user_count": users,
        "available_modules": list(MODULES[portal.audience]),
    }


@router.get("/portals")
async def list_portals(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    portals = (await db.execute(select(Portal).where(Portal.organization_id == principal.organization_id).order_by(Portal.name))).scalars().all()
    return {
        "items": [await portal_out(db, p) for p in portals],
        "features": {"customer_portal": principal.has("customer_portal"), "custom_portals": principal.has("custom_portals")},
    }


@router.post("/portals", status_code=status.HTTP_201_CREATED)
async def create_portal(
    payload: PortalPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    feature = portal_feature(payload.audience)
    if not principal.has(feature):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Portals are part of the Pro plan.")
    organization = await db.get(Organization, principal.organization_id)
    base = re.sub(r"[^a-z0-9]+", "-", f"{organization.slug}-{payload.name}".lower()).strip("-")[:60]
    slug = base
    while (await db.execute(select(func.count()).select_from(Portal).where(Portal.slug == slug))).scalar_one():
        slug = f"{base}-{new_token(nbytes=2).lower()}"
    portal = Portal(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        name=payload.name.strip(),
        slug=slug,
        audience=payload.audience,
        settings=_settings(payload.audience, payload.settings),
        is_active=payload.is_active,
    )
    db.add(portal)
    await db.commit()
    return await portal_out(db, portal)


async def _portal_admin(db: AsyncSession, principal: Principal, portal_id: uuid.UUID) -> Portal:
    portal = await db.get(Portal, portal_id)
    if portal is None or portal.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Portal not found.")
    return portal


@router.patch("/portals/{portal_id}")
async def update_portal(
    portal_id: uuid.UUID,
    payload: PortalPayload,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    portal = await _portal_admin(db, principal, portal_id)
    portal.name = payload.name.strip()
    portal.settings = _settings(portal.audience, payload.settings)
    portal.is_active = payload.is_active
    await db.commit()
    return await portal_out(db, portal)


@router.delete("/portals/{portal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portal(
    portal_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    portal = await _portal_admin(db, principal, portal_id)
    await db.delete(portal)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/portals/{portal_id}/users")
async def list_portal_users(
    portal_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    await _portal_admin(db, principal, portal_id)
    users = (await db.execute(select(PortalUser).where(PortalUser.portal_id == portal_id).order_by(PortalUser.full_name))).scalars().all()
    accounts = await names_for(db, principal.organization_id, "account", [u.account_id for u in users])
    return [
        {
            "id": str(u.id),
            "full_name": u.full_name,
            "email": u.email,
            "status": u.status,
            "contact_id": str(u.contact_id) if u.contact_id else None,
            "account_name": accounts.get(str(u.account_id)),
            "last_login_at": u.last_login_at,
            "created_at": u.created_at,
        }
        for u in users
    ]


class PortalInvite(BaseModel):
    contact_id: uuid.UUID
    send_email: bool = True


async def _login_link(db: AsyncSession, portal: Portal, user: PortalUser) -> str:
    token = new_token(nbytes=24)
    db.add(
        PortalToken(
            id=uuid.uuid4(),
            portal_user_id=user.id,
            purpose="login",
            token_hash=sha256(token),
            expires_at=utcnow() + timedelta(minutes=LOGIN_MINUTES),
        )
    )
    return f"{portal_url(portal)}/auth?token={token}"


async def _send_link(db: AsyncSession, portal: Portal, user: PortalUser, link: str, *, invite: bool) -> str:
    config = mailer.server_config()
    subject = f"{'You’re invited to' if invite else 'Sign in to'} {portal.name}"
    text = (
        f"Hi {user.full_name.split(' ')[0]},\n\n"
        + (f"You now have access to {portal.name}. " if invite else "")
        + f"Use this link to sign in (it works once and expires in {LOGIN_MINUTES} minutes):\n\n{link}\n"
    )
    try:
        return await mailer.send(config, to=user.email, to_name=user.full_name, subject=subject, text=text)
    except mailer.MailError:
        return "failed"


@router.post("/portals/{portal_id}/users", status_code=status.HTTP_201_CREATED)
async def invite_portal_user(
    portal_id: uuid.UUID,
    payload: PortalInvite,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    portal = await _portal_admin(db, principal, portal_id)
    if not principal.has(portal_feature(portal.audience)):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Portals are part of the Pro plan.")
    contact = await db.get(Contact, payload.contact_id)
    if contact is None or contact.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found.")
    if not contact.email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add an email address to the contact first.")
    email = contact.email.strip().lower()
    user = (await db.execute(select(PortalUser).where(PortalUser.portal_id == portal.id, PortalUser.email == email))).scalar_one_or_none()
    if user is None:
        user = PortalUser(
            id=uuid.uuid4(),
            organization_id=principal.organization_id,
            portal_id=portal.id,
            contact_id=contact.id,
            account_id=contact.account_id,
            email=email,
            full_name=f"{contact.first_name} {contact.last_name}".strip() or email,
            status="active",
        )
        db.add(user)
    else:
        user.status = "active"
    await db.flush()
    link = await _login_link(db, portal, user)
    delivery = await _send_link(db, portal, user, link, invite=True) if payload.send_email else "skipped"
    await db.commit()
    result = {"id": str(user.id), "email": email, "delivery": delivery}
    if get_settings().expose_dev_links or delivery != "smtp":
        result["login_url"] = link
    return result


class PortalUserStatus(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


@router.patch("/portals/{portal_id}/users/{user_id}")
async def set_portal_user_status(
    portal_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: PortalUserStatus,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    await _portal_admin(db, principal, portal_id)
    user = await db.get(PortalUser, user_id)
    if user is None or user.portal_id != portal_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Portal user not found.")
    user.status = payload.status
    await db.commit()
    return {"status": user.status}


# ===========================================================================
# Portal API (public, authenticated with a portal session token)
# ===========================================================================


async def _public_portal(db: AsyncSession, slug: str) -> tuple[Portal, Organization]:
    portal = (await db.execute(select(Portal).where(Portal.slug == slug))).scalar_one_or_none()
    if portal is None or not portal.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This portal isn't available.")
    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == portal.organization_id))).scalar_one_or_none()
    state = plan_state(subscription)
    if portal_feature(portal.audience) not in state.features or state.read_only:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This portal isn't available.")
    organization = await db.get(Organization, portal.organization_id)
    return portal, organization


def _modules(portal: Portal) -> list[str]:
    return list((portal.settings or {}).get("modules") or MODULES[portal.audience])


async def portal_session(
    slug: str,
    x_portal_token: str | None = Header(default=None, alias="X-Portal-Token"),
    db: AsyncSession = Depends(get_db),
) -> tuple[Portal, PortalUser]:
    portal, _ = await _public_portal(db, slug)
    if not x_portal_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to the portal.")
    token = (
        await db.execute(
            select(PortalToken).where(PortalToken.token_hash == sha256(x_portal_token), PortalToken.purpose == "session")
        )
    ).scalar_one_or_none()
    if token is None or token.expires_at <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your portal session ended. Sign in again.")
    user = await db.get(PortalUser, token.portal_user_id)
    if user is None or user.portal_id != portal.id or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your portal access was turned off.")
    return portal, user


@router.get("/portal-api/{slug}")
async def portal_info(slug: str, db: AsyncSession = Depends(get_db)):
    portal, organization = await _public_portal(db, slug)
    settings = portal.settings or {}
    return {
        "name": portal.name,
        "audience": portal.audience,
        "organization_name": organization.name,
        "accent": settings.get("accent"),
        "welcome": settings.get("welcome"),
        "logo_text": settings.get("logo_text") or organization.name,
        "support_email": settings.get("support_email"),
        "modules": _modules(portal),
    }


class PortalLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)


@router.post("/portal-api/{slug}/login", status_code=status.HTTP_202_ACCEPTED)
async def portal_login(slug: str, payload: PortalLogin, db: AsyncSession = Depends(get_db)):
    portal, _ = await _public_portal(db, slug)
    user = (
        await db.execute(select(PortalUser).where(PortalUser.portal_id == portal.id, PortalUser.email == payload.email.strip().lower()))
    ).scalar_one_or_none()
    result: dict[str, Any] = {"message": "If that email has access, a sign-in link is on its way."}
    if user is not None and user.status == "active":
        link = await _login_link(db, portal, user)
        delivery = await _send_link(db, portal, user, link, invite=False)
        await db.commit()
        if get_settings().expose_dev_links:
            result["login_url"] = link
        result["delivery"] = delivery if get_settings().expose_dev_links else None
    return result


class PortalVerify(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post("/portal-api/{slug}/verify")
async def portal_verify(slug: str, payload: PortalVerify, db: AsyncSession = Depends(get_db)):
    portal, _ = await _public_portal(db, slug)
    token = (
        await db.execute(select(PortalToken).where(PortalToken.token_hash == sha256(payload.token), PortalToken.purpose == "login"))
    ).scalar_one_or_none()
    if token is None or token.used_at is not None or token.expires_at <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That sign-in link has expired or was already used. Ask for a new one.")
    user = await db.get(PortalUser, token.portal_user_id)
    if user is None or user.portal_id != portal.id or user.status != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That sign-in link isn't valid for this portal.")
    token.used_at = utcnow()
    session_token = new_token(nbytes=32)
    db.add(
        PortalToken(
            id=uuid.uuid4(),
            portal_user_id=user.id,
            purpose="session",
            token_hash=sha256(session_token),
            expires_at=utcnow() + timedelta(days=SESSION_DAYS),
        )
    )
    user.last_login_at = utcnow()
    await db.commit()
    return {"session_token": session_token, "expires_in": SESSION_DAYS * 86400, "name": user.full_name}


@router.post("/portal-api/{slug}/logout", status_code=status.HTTP_204_NO_CONTENT)
async def portal_logout(
    slug: str,
    x_portal_token: str | None = Header(default=None, alias="X-Portal-Token"),
    db: AsyncSession = Depends(get_db),
):
    if x_portal_token:
        token = (await db.execute(select(PortalToken).where(PortalToken.token_hash == sha256(x_portal_token)))).scalar_one_or_none()
        if token is not None:
            token.expires_at = utcnow()
            await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/portal-api/{slug}/me")
async def portal_me(session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    account = await db.get(Account, user.account_id) if user.account_id else None
    return {
        "name": user.full_name,
        "email": user.email,
        "account_name": account.name if account else None,
        "modules": _modules(portal),
        "audience": portal.audience,
    }


def _require(portal: Portal, module: str) -> None:
    if module not in _modules(portal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This portal doesn't include that.")


def _case_scope(user: PortalUser):
    conditions = []
    if user.contact_id:
        conditions.append(Case.contact_id == user.contact_id)
    if user.account_id:
        conditions.append(Case.account_id == user.account_id)
    return or_(*conditions) if conditions else Case.id.is_(None)


def portal_case_out(case: Case) -> dict[str, Any]:
    return {
        "id": str(case.id),
        "number": f"CS-{case.number}",
        "subject": case.subject,
        "description": case.description,
        "status": case.status,
        "priority": case.priority,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "resolved_at": case.resolved_at,
    }


@router.get("/portal-api/{slug}/cases")
async def portal_cases(session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "cases")
    cases = (
        await db.execute(
            select(Case).where(Case.organization_id == portal.organization_id, _case_scope(user)).order_by(Case.updated_at.desc()).limit(100)
        )
    ).scalars().all()
    return [portal_case_out(c) for c in cases]


class PortalCase(BaseModel):
    subject: str = Field(min_length=3, max_length=250)
    description: str = Field(min_length=3, max_length=10000)
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")


@router.post("/portal-api/{slug}/cases", status_code=status.HTTP_201_CREATED)
async def portal_new_case(payload: PortalCase, session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "cases")
    from app.api.routes.service import _apply_sla, _policy_for  # noqa: PLC0415

    number = (
        await db.execute(select(func.coalesce(func.max(Case.number), 1000)).where(Case.organization_id == portal.organization_id))
    ).scalar_one() + 1
    now = utcnow()
    case = Case(
        id=uuid.uuid4(),
        organization_id=portal.organization_id,
        number=number,
        subject=payload.subject.strip(),
        description=payload.description.strip(),
        status="new",
        priority=payload.priority,
        channel="portal",
        contact_id=user.contact_id,
        account_id=user.account_id,
        linked_article_ids=[],
        tags=["portal"],
        custom_fields={},
    )
    case.created_at = now
    _apply_sla(case, await _policy_for(db, portal.organization_id, payload.priority), now)
    db.add(case)
    await db.commit()
    return portal_case_out(case)


async def _portal_case(db: AsyncSession, portal: Portal, user: PortalUser, case_id: uuid.UUID) -> Case:
    case = (
        await db.execute(select(Case).where(Case.id == case_id, Case.organization_id == portal.organization_id, _case_scope(user)))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found.")
    return case


@router.get("/portal-api/{slug}/cases/{case_id}")
async def portal_case(case_id: uuid.UUID, session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "cases")
    case = await _portal_case(db, portal, user, case_id)
    comments = (
        await db.execute(
            select(CaseComment).where(CaseComment.case_id == case.id, CaseComment.is_internal.is_(False)).order_by(CaseComment.created_at)
        )
    ).scalars().all()
    authors = await names_for(db, portal.organization_id, "user", [c.author_user_id for c in comments])
    return {
        **portal_case_out(case),
        "comments": [
            {
                "id": str(c.id),
                "body": c.body,
                "from_us": c.author_user_id is not None,
                "author": authors.get(str(c.author_user_id)) or user.full_name,
                "created_at": c.created_at,
            }
            for c in comments
        ],
    }


class PortalReply(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


@router.post("/portal-api/{slug}/cases/{case_id}/comments", status_code=status.HTTP_201_CREATED)
async def portal_reply(case_id: uuid.UUID, payload: PortalReply, session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "cases")
    case = await _portal_case(db, portal, user, case_id)
    if case.status == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is closed. Open a new one if you still need help.")
    db.add(
        CaseComment(
            id=uuid.uuid4(),
            organization_id=portal.organization_id,
            case_id=case.id,
            author_user_id=None,
            body=payload.body.strip(),
            is_internal=False,
        )
    )
    if case.status in {"pending", "resolved"}:
        case.status = "open"
        case.resolved_at = None
    if case.assigned_to_user_id:
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=portal.organization_id,
                user_id=case.assigned_to_user_id,
                kind="case",
                title=f"Customer replied on CS-{case.number}"[:200],
                body=payload.body[:200],
                link=f"/cases/{case.id}",
                is_read=False,
            )
        )
    await db.commit()
    return await portal_case(case_id, session, db)


@router.get("/portal-api/{slug}/articles")
async def portal_articles(
    slug: str,
    q: str | None = Query(default=None, max_length=200),
    session=Depends(portal_session),
    db: AsyncSession = Depends(get_db),
):
    portal, _ = session
    _require(portal, "knowledge")
    query = select(KnowledgeArticle).where(KnowledgeArticle.organization_id == portal.organization_id, KnowledgeArticle.status == "published")
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(KnowledgeArticle.title.ilike(like), KnowledgeArticle.summary.ilike(like), KnowledgeArticle.body.ilike(like)))
    articles = (await db.execute(query.order_by(KnowledgeArticle.view_count.desc()).limit(50))).scalars().all()
    return [{"id": str(a.id), "title": a.title, "summary": a.summary, "category": a.category} for a in articles]


@router.get("/portal-api/{slug}/articles/{article_id}")
async def portal_article(article_id: uuid.UUID, session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, _ = session
    _require(portal, "knowledge")
    article = await db.get(KnowledgeArticle, article_id)
    if article is None or article.organization_id != portal.organization_id or article.status != "published":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found.")
    article.view_count = (article.view_count or 0) + 1
    await db.commit()
    return {"id": str(article.id), "title": article.title, "summary": article.summary, "body": article.body, "category": article.category}


def _quote_scope(user: PortalUser):
    conditions = []
    if user.contact_id:
        conditions.append(Quote.contact_id == user.contact_id)
    if user.account_id:
        conditions.append(Quote.account_id == user.account_id)
    return or_(*conditions) if conditions else Quote.id.is_(None)


def portal_quote_out(quote: Quote, detail: bool = False) -> dict[str, Any]:
    data = {
        "id": str(quote.id),
        "number": f"Q-{quote.number}",
        "name": quote.name,
        "status": quote.status,
        "total": float(quote.total),
        "currency": quote.currency,
        "valid_until": quote.valid_until,
        "sent_at": quote.sent_at,
    }
    if detail:
        data["lines"] = [
            {"name": l.name, "description": l.description, "quantity": float(l.quantity), "unit_price": float(l.unit_price), "discount_percent": float(l.discount_percent), "line_total": float(l.line_total)}
            for l in quote.line_items
        ]
        data["subtotal"] = float(quote.subtotal)
        data["discount_total"] = float(quote.discount_total)
        data["tax_total"] = float(quote.tax_total)
        data["terms"] = quote.terms
    return data


@router.get("/portal-api/{slug}/quotes")
async def portal_quotes(session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "quotes")
    quotes = (
        await db.execute(
            select(Quote).where(Quote.organization_id == portal.organization_id, _quote_scope(user), Quote.status.in_(("sent", "accepted", "declined")))
            .order_by(Quote.updated_at.desc())
        )
    ).scalars().all()
    return [portal_quote_out(q, detail=True) for q in quotes]


class QuoteDecision(BaseModel):
    accept: bool


@router.post("/portal-api/{slug}/quotes/{quote_id}/decision")
async def portal_quote_decision(quote_id: uuid.UUID, payload: QuoteDecision, session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "quotes")
    quote = (
        await db.execute(select(Quote).where(Quote.id == quote_id, Quote.organization_id == portal.organization_id, _quote_scope(user)))
    ).scalar_one_or_none()
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found.")
    if quote.status != "sent":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This quote is {quote.status}.")
    now = utcnow()
    if payload.accept:
        quote.status = "accepted"
        quote.accepted_at = now
        if quote.opportunity_id:
            opportunity = await db.get(Opportunity, quote.opportunity_id)
            if opportunity is not None:
                opportunity.amount = quote.total
        subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == portal.organization_id))).scalar_one_or_none()
        if "inventory" in plan_state(subscription).features:
            from app.api.routes.operations import apply_quote_sale  # noqa: PLC0415

            await apply_quote_sale(db, quote, None)
    else:
        quote.status = "declined"
        quote.declined_at = now
    if quote.owner_user_id:
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=portal.organization_id,
                user_id=quote.owner_user_id,
                kind="quote",
                title=f"{user.full_name} {'accepted' if payload.accept else 'declined'} Q-{quote.number}"[:200],
                body=f"{quote.name} · ${float(quote.total):,.2f}",
                link=f"/quotes/{quote.id}",
                is_read=False,
            )
        )
    await db.commit()
    return portal_quote_out(quote, detail=True)


@router.get("/portal-api/{slug}/invoices")
async def portal_invoices(session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "invoices")
    conditions = []
    if user.contact_id:
        conditions.append(SalesInvoice.contact_id == user.contact_id)
    if user.account_id:
        conditions.append(SalesInvoice.account_id == user.account_id)
    if not conditions:
        return []
    invoices = (
        await db.execute(
            select(SalesInvoice).where(SalesInvoice.organization_id == portal.organization_id, or_(*conditions), SalesInvoice.status.in_(("sent", "paid")))
            .order_by(SalesInvoice.number.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(i.id),
            "number": f"INV-{i.number}",
            "status": i.status,
            "issue_date": i.issue_date,
            "due_date": i.due_date,
            "total": float(i.total),
            "balance": float(i.total - i.amount_paid),
            "currency": i.currency,
        }
        for i in invoices
    ]


class Registration(BaseModel):
    company_name: str = Field(min_length=1, max_length=250)
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    estimated_value: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=5000)


@router.get("/portal-api/{slug}/registrations")
async def portal_registrations(session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "deal_registration")
    leads = (
        await db.execute(
            select(Lead).where(
                Lead.organization_id == portal.organization_id,
                Lead.custom_fields["_partner"]["portal_user_id"].astext == str(user.id),
            ).order_by(Lead.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(l.id),
            "company_name": l.company_name,
            "contact": f"{l.first_name} {l.last_name}".strip(),
            "status": l.status,
            "estimated_value": ((l.custom_fields or {}).get("_partner") or {}).get("estimated_value"),
            "created_at": l.created_at,
        }
        for l in leads
    ]


@router.post("/portal-api/{slug}/registrations", status_code=status.HTTP_201_CREATED)
async def portal_register(payload: Registration, session=Depends(portal_session), db: AsyncSession = Depends(get_db)):
    portal, user = session
    _require(portal, "deal_registration")
    duplicate = (
        await db.execute(
            select(func.count()).select_from(Lead).where(
                Lead.organization_id == portal.organization_id,
                func.lower(Lead.company_name) == payload.company_name.strip().lower(),
                Lead.status.not_in(("unqualified", "converted")),
            )
        )
    ).scalar_one()
    lead = Lead(
        id=uuid.uuid4(),
        organization_id=portal.organization_id,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        email=(payload.email or "").strip().lower() or None,
        phone=payload.phone,
        company_name=payload.company_name.strip(),
        notes=payload.notes,
        source="partner",
        status="new",
        score=0,
        custom_fields={
            "_partner": {
                "portal_id": str(portal.id),
                "portal_user_id": str(user.id),
                "partner_name": user.full_name,
                "estimated_value": payload.estimated_value,
                "possible_duplicate": bool(duplicate),
            }
        },
    )
    db.add(lead)
    await db.commit()
    return {"id": str(lead.id), "status": lead.status, "possible_duplicate": bool(duplicate)}
