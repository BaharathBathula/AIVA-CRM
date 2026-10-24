"""Outbound: overview, sending domains, mailboxes, diagnose, snippets,
sequences and enrollments, the dialer, and the public click redirect."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_feature, require_permission
from app.db.session import get_db
from app.models.contact import Contact
from app.models.editions import MailboxConnection
from app.models.lead import Lead
from app.models.outbound import SendingDomain, Sequence, SequenceEnrollment, SequenceMessage, SequenceStep, Snippet
from app.models.task import Task
from app.models.user import User
from app.services import mailer, outbound, workspace_settings
from app.services.automation import record_audit
from app.services.lookups import names_for
from app.services.merge import record_context, render

router = APIRouter(tags=["Outbound"])
public_router = APIRouter(prefix="/public", tags=["Public"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Overview
# ===========================================================================


async def _message_stats(db: AsyncSession, org: uuid.UUID, since: datetime, sequence_id: uuid.UUID | None = None, sender: uuid.UUID | None = None) -> dict[str, int]:
    query = select(
        func.count().filter(SequenceMessage.status == "sent"),
        func.count().filter(SequenceMessage.open_count > 0),
        func.count().filter(SequenceMessage.click_count > 0),
        func.count().filter(SequenceMessage.replied_at.is_not(None)),
        func.count().filter(SequenceMessage.bounced_at.is_not(None)),
        func.count().filter(SequenceMessage.status == "failed"),
    ).where(
        SequenceMessage.organization_id == org,
        SequenceMessage.kind.in_(("auto_email", "manual_email")),
        SequenceMessage.created_at >= since,
    )
    if sequence_id:
        query = query.where(SequenceMessage.sequence_id == sequence_id)
    if sender:
        query = query.where(SequenceMessage.sender_user_id == sender)
    sent, opened, clicked, replied, bounced, failed = (await db.execute(query)).one()
    return {"sent": sent, "opened": opened, "clicked": clicked, "replied": replied, "bounced": bounced, "failed": failed}


def _rates(stats: dict[str, int]) -> dict[str, float]:
    sent = stats.get("sent") or 0
    return {k: round(stats.get(k, 0) / sent * 100, 1) if sent else 0.0 for k in ("opened", "clicked", "replied", "bounced")}


@router.get("/outbound/overview")
async def outbound_overview(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    since = utcnow() - timedelta(days=30)
    stats = await _message_stats(db, org, since)
    unsubscribed = (
        await db.execute(
            select(func.count()).select_from(SequenceEnrollment).where(
                SequenceEnrollment.organization_id == org, SequenceEnrollment.status == "unsubscribed", SequenceEnrollment.finished_at >= since
            )
        )
    ).scalar_one()
    daily = (
        await db.execute(
            select(func.date(SequenceMessage.sent_at), func.count(), func.count().filter(SequenceMessage.replied_at.is_not(None)))
            .where(SequenceMessage.organization_id == org, SequenceMessage.status == "sent", SequenceMessage.sent_at >= since)
            .group_by(func.date(SequenceMessage.sent_at))
            .order_by(func.date(SequenceMessage.sent_at))
        )
    ).all()
    sequences = dict(
        (await db.execute(select(Sequence.status, func.count()).where(Sequence.organization_id == org).group_by(Sequence.status))).all()
    )
    enrollments = dict(
        (
            await db.execute(
                select(SequenceEnrollment.status, func.count()).where(SequenceEnrollment.organization_id == org).group_by(SequenceEnrollment.status)
            )
        ).all()
    )
    mailboxes = (await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org))).scalars().all()
    domains = (await db.execute(select(SendingDomain).where(SendingDomain.organization_id == org))).scalars().all()
    protection = await workspace_settings.get(db, org, "sending")
    tracking = await workspace_settings.get(db, org, "tracking")
    capacity = sum(outbound.mailbox_limit(protection, m.user_id) for m in mailboxes if m.status == "connected")
    sent_today = 0
    for m in mailboxes:
        sent_today += (await outbound.mailbox_stats(db, org, m.user_id, protection))["sent_today"]
    tasks_due = (
        await db.execute(
            select(func.count()).select_from(Task).where(
                Task.organization_id == org, Task.source == "sequence", Task.status.in_(("open", "in_progress")), Task.assigned_to_user_id == principal.user_id
            )
        )
    ).scalar_one()
    checklist = [
        {"key": "domain", "label": "Verify a sending domain", "done": any(d.status == "verified" for d in domains), "href": "/settings/outbound/domains"},
        {"key": "mailbox", "label": "Connect a mailbox", "done": any(m.status == "connected" for m in mailboxes), "href": "/settings/outbound/mailboxes"},
        {"key": "protection", "label": "Set sending limits", "done": True, "href": "/settings/outbound/protection"},
        {"key": "tracking", "label": "Turn on open and click tracking", "done": tracking.open_tracking and tracking.click_tracking, "href": "/settings/outbound/tracking"},
        {"key": "sequence", "label": "Turn on a sequence", "done": bool(sequences.get("active")), "href": "/sequences"},
    ]
    return {
        "last_30_days": {**stats, "unsubscribed": unsubscribed},
        "rates": _rates(stats),
        "daily": [{"date": d.isoformat() if hasattr(d, "isoformat") else str(d), "sent": int(s), "replied": int(r)} for d, s, r in daily],
        "sequences": {k: int(v) for k, v in sequences.items()},
        "enrollments": {k: int(v) for k, v in enrollments.items()},
        "mailboxes": {"connected": sum(1 for m in mailboxes if m.status == "connected"), "errors": sum(1 for m in mailboxes if m.status == "error"), "total": len(mailboxes)},
        "domains": {"verified": sum(1 for d in domains if d.status == "verified"), "total": len(domains)},
        "capacity_today": capacity,
        "sent_today": sent_today,
        "in_window_now": outbound.in_window(protection, utcnow()),
        "next_window": outbound.next_window(protection, utcnow()),
        "my_sequence_tasks": tasks_due,
        "checklist": checklist,
    }


# ===========================================================================
# Domains
# ===========================================================================


class DomainPayload(BaseModel):
    domain: str = Field(min_length=3, max_length=255)
    dkim_selector: str | None = Field(default=None, max_length=63, pattern=r"^[A-Za-z0-9._-]*$")
    tracking_host: str | None = Field(default=None, max_length=255)
    is_default: bool = False


def domain_out(row: SendingDomain) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "domain": row.domain,
        "status": row.status,
        "dkim_selector": row.dkim_selector,
        "tracking_host": row.tracking_host,
        "checks": row.checks or {},
        "last_checked_at": row.last_checked_at,
        "is_default": row.is_default,
    }


async def _run_check(db: AsyncSession, row: SendingDomain) -> None:
    checks = await outbound.check_domain(row.domain, row.dkim_selector, row.tracking_host)
    row.checks = checks
    row.status = outbound.domain_status(checks)
    row.last_checked_at = utcnow()


@router.get("/outbound/domains")
async def list_domains(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(SendingDomain).where(SendingDomain.organization_id == principal.organization_id).order_by(SendingDomain.domain))).scalars().all()
    mailbox_domains = sorted(
        {
            (email or "").split("@")[-1].lower()
            for email in (await db.execute(select(MailboxConnection.email_address).where(MailboxConnection.organization_id == principal.organization_id))).scalars()
            if email and "@" in email
        }
    )
    from app.core.config import get_settings

    return {
        "items": [domain_out(r) for r in rows],
        "suggested": [d for d in mailbox_domains if d not in {r.domain for r in rows}],
        "app_host": urlsplit(get_settings().app_base_url).hostname,
        "can_edit": principal.can("outbound.manage"),
    }


@router.post("/outbound/domains", status_code=status.HTTP_201_CREATED)
async def add_domain(payload: DomainPayload, principal: Principal = Depends(require_permission("outbound.manage")), db: AsyncSession = Depends(get_db)):
    try:
        domain = workspace_settings.clean_domain(payload.domain)
        tracking = workspace_settings.clean_domain(payload.tracking_host) if payload.tracking_host else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if tracking and not tracking.endswith(f".{domain}"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Use a subdomain of {domain} for tracking, like track.{domain}.")
    exists = (await db.execute(select(SendingDomain.id).where(SendingDomain.organization_id == principal.organization_id, SendingDomain.domain == domain))).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{domain} is already added.")
    count = (await db.execute(select(func.count()).select_from(SendingDomain).where(SendingDomain.organization_id == principal.organization_id))).scalar_one()
    row = SendingDomain(
        id=uuid.uuid4(), organization_id=principal.organization_id, domain=domain, dkim_selector=payload.dkim_selector or None,
        tracking_host=tracking, is_default=payload.is_default or count == 0, created_by_user_id=principal.user_id,
    )
    db.add(row)
    await _run_check(db, row)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="sending_domain", entity_id=row.id, entity_label=domain)
    await db.commit()
    return domain_out(row)


@router.patch("/outbound/domains/{domain_id}")
async def update_domain(domain_id: uuid.UUID, payload: DomainPayload, principal: Principal = Depends(require_permission("outbound.manage")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SendingDomain, domain_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Domain not found.")
    try:
        tracking = workspace_settings.clean_domain(payload.tracking_host) if payload.tracking_host else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if tracking and not tracking.endswith(f".{row.domain}"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Use a subdomain of {row.domain} for tracking, like track.{row.domain}.")
    row.dkim_selector = payload.dkim_selector or None
    row.tracking_host = tracking
    if payload.is_default:
        for other in (await db.execute(select(SendingDomain).where(SendingDomain.organization_id == principal.organization_id))).scalars():
            other.is_default = other.id == row.id
    await _run_check(db, row)
    await db.commit()
    return domain_out(row)


@router.post("/outbound/domains/{domain_id}/check")
async def recheck_domain(domain_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SendingDomain, domain_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Domain not found.")
    await _run_check(db, row)
    await db.commit()
    return domain_out(row)


@router.delete("/outbound/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(domain_id: uuid.UUID, principal: Principal = Depends(require_permission("outbound.manage")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SendingDomain, domain_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Domain not found.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Mailboxes
# ===========================================================================


@router.get("/outbound/mailboxes")
async def list_mailboxes(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    query = select(MailboxConnection, User).join(User, User.id == MailboxConnection.user_id).where(MailboxConnection.organization_id == org)
    if not principal.can("outbound.manage"):
        query = query.where(MailboxConnection.user_id == principal.user_id)
    rows = (await db.execute(query.order_by(User.full_name))).all()
    protection = await workspace_settings.get(db, org, "sending")
    domains = {d.domain: d for d in (await db.execute(select(SendingDomain).where(SendingDomain.organization_id == org))).scalars()}
    since = utcnow() - timedelta(days=30)
    items = []
    for connection, user in rows:
        stats = await outbound.mailbox_stats(db, org, connection.user_id, protection)
        totals = await _message_stats(db, org, since, sender=connection.user_id)
        domain = connection.email_address.split("@")[-1].lower()
        health = 100
        issues = []
        if connection.status != "connected":
            health -= 50
            issues.append(connection.last_error or "The mailbox isn't connected.")
        if domain not in domains:
            health -= 15
            issues.append(f"{domain} isn't added under Domains yet.")
        elif domains[domain].status != "verified":
            health -= 20
            issues.append(f"{domain} has DNS issues (SPF, DKIM or DMARC).")
        if stats["bounce_rate"] >= protection.bounce_pause_percent:
            health -= 25
            issues.append(f"Bounce rate is {stats['bounce_rate']}%.")
        if not connection.imap_host:
            health -= 10
            issues.append("Replies aren't synced (no IMAP server), so sequences can't stop when people reply.")
        items.append(
            {
                "id": str(connection.id),
                "user_id": str(connection.user_id),
                "user_name": user.full_name,
                "email": connection.email_address,
                "provider": connection.provider,
                "status": connection.status,
                "last_error": connection.last_error,
                "last_sync_at": connection.last_sync_at,
                "reply_sync": bool(connection.imap_host and connection.sync_enabled),
                "domain": domain,
                "domain_status": domains[domain].status if domain in domains else None,
                **stats,
                "sent_30_days": totals["sent"],
                "replied_30_days": totals["replied"],
                "health": max(0, health),
                "issues": issues,
                "custom_limit": str(connection.user_id) in protection.mailbox_limits,
            }
        )
    return {
        "items": items,
        "defaults": {"daily_limit": protection.daily_limit_per_mailbox, "hourly_limit": protection.hourly_limit_per_mailbox},
        "server_mail": mailer.server_config() is not None,
        "can_edit": principal.can("outbound.manage"),
    }


class MailboxLimit(BaseModel):
    daily_limit: int | None = Field(default=None, ge=1, le=5000)


@router.put("/outbound/mailboxes/{user_id}/limit")
async def set_mailbox_limit(user_id: uuid.UUID, payload: MailboxLimit, principal: Principal = Depends(require_permission("outbound.manage")), db: AsyncSession = Depends(get_db)):
    protection = await workspace_settings.get(db, principal.organization_id, "sending")
    limits = dict(protection.mailbox_limits)
    if payload.daily_limit is None:
        limits.pop(str(user_id), None)
    else:
        limits[str(user_id)] = payload.daily_limit
    await workspace_settings.save(db, principal.organization_id, "sending", {"mailbox_limits": limits}, principal.user_id)
    await db.commit()
    return {"user_id": str(user_id), "daily_limit": payload.daily_limit or protection.daily_limit_per_mailbox, "custom": payload.daily_limit is not None}


@router.post("/outbound/mailboxes/{connection_id}/test")
async def test_mailbox(connection_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    connection = await db.get(MailboxConnection, connection_id)
    if connection is None or connection.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mailbox not found.")
    if connection.user_id != principal.user_id and not principal.can("outbound.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only test your own mailbox.")
    try:
        await mailer.verify_smtp(mailer.mailbox_config(connection))
        connection.status = "connected"
        connection.last_error = None
        ok, message = True, f"AIVA signed in to {connection.smtp_host} as {connection.username}."
    except mailer.MailError as exc:
        connection.status = "error"
        connection.last_error = str(exc)[:500]
        ok, message = False, str(exc)
    await db.commit()
    return {"ok": ok, "message": message}


# ===========================================================================
# Diagnose
# ===========================================================================


@router.get("/outbound/diagnose")
async def diagnose(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    checks: list[dict[str, Any]] = []

    def add(area: str, label: str, state: str, detail: str, fix: str | None = None, href: str | None = None) -> None:
        checks.append({"area": area, "label": label, "status": state, "detail": detail, "fix": fix, "href": href})

    domains = (await db.execute(select(SendingDomain).where(SendingDomain.organization_id == org))).scalars().all()
    if not domains:
        add("Domains", "Sending domain", "fail", "No sending domain is set up, so AIVA can't check SPF, DKIM and DMARC.", "Add the domain your team sends from.", "/settings/outbound/domains")
    for domain in domains:
        stale = not domain.last_checked_at or domain.last_checked_at < utcnow() - timedelta(days=1)
        if stale:
            await _run_check(db, domain)
        for key, label in (("spf", "SPF"), ("dkim", "DKIM"), ("dmarc", "DMARC"), ("mx", "MX")):
            check = (domain.checks or {}).get(key)
            if check:
                add(f"Domain · {domain.domain}", label, check["status"], check["detail"], check.get("fix"), "/settings/outbound/domains")
        if (domain.checks or {}).get("error"):
            add(f"Domain · {domain.domain}", "DNS", "warn", domain.checks["error"], "Run the check again in a minute.", "/settings/outbound/domains")
    mailboxes = (await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org))).scalars().all()
    if not mailboxes:
        state = "warn" if mailer.server_config() else "fail"
        add("Mailboxes", "Connected mailboxes", state, "Nobody has connected a mailbox. Sequence emails " + ("go out from the server's address." if state == "warn" else "can't be delivered."),
            "Each rep connects Gmail or Outlook in Settings → Integrations.", "/settings/outbound/mailboxes")
    protection = await workspace_settings.get(db, org, "sending")
    for mailbox in mailboxes:
        if mailbox.status != "connected":
            add("Mailboxes", mailbox.email_address, "fail", mailbox.last_error or "Not connected.", "Reconnect it in Settings → Integrations.", "/settings/outbound/mailboxes")
        if not mailbox.imap_host:
            add("Mailboxes", f"{mailbox.email_address} replies", "warn", "Replies aren't synced, so sequences can't stop when people reply.", "Add the IMAP server in Settings → Integrations.", "/settings/integrations")
        stats = await outbound.mailbox_stats(db, org, mailbox.user_id, protection)
        if stats["paused"]:
            add("Mailboxes", f"{mailbox.email_address} bounces", "fail", f"Bounce rate is {stats['bounce_rate']}% over the last {stats['sample']} emails, so sending is paused.",
                "Clean the list (remove bad addresses) before sending more.", "/settings/outbound/protection")
        elif stats["bounce_rate"] >= protection.bounce_pause_percent / 2 and stats["sample"] >= 20:
            add("Mailboxes", f"{mailbox.email_address} bounces", "warn", f"Bounce rate is {stats['bounce_rate']}%.", "Verify addresses before enrolling people.", "/settings/outbound/protection")
    if protection.daily_limit_per_mailbox > 500:
        add("Sending protection", "Daily limit", "warn", f"{protection.daily_limit_per_mailbox} emails a day per mailbox is high for cold outreach.", "Most providers recommend 200 or fewer per mailbox.", "/settings/outbound/protection")
    else:
        add("Sending protection", "Daily limit", "pass", f"Up to {protection.daily_limit_per_mailbox} emails a day per mailbox.")
    if protection.window_end_hour - protection.window_start_hour > 14:
        add("Sending protection", "Sending window", "warn", "Emails can go out late at night.", "Send during business hours in your prospects' time zone.", "/settings/outbound/protection")
    tracking = await workspace_settings.get(db, org, "tracking")
    if not tracking.unsubscribe_link:
        add("Tracking", "Unsubscribe link", "fail", "Emails don't include an unsubscribe link. Gmail and Yahoo require one for bulk senders.", "Turn it back on in Tracking.", "/settings/outbound/tracking")
    else:
        add("Tracking", "Unsubscribe link", "pass", "Every email includes a one-click unsubscribe link.")
    if tracking.click_tracking and not tracking.tracking_domain:
        add("Tracking", "Tracking domain", "warn", "Tracked links use AIVA's address, which some spam filters treat as a shared domain.", "Add a custom tracking domain like track.yourcompany.com.", "/settings/outbound/tracking")
    since = utcnow() - timedelta(days=30)
    stats = await _message_stats(db, org, since)
    rates = _rates(stats)
    if stats["sent"] >= 50 and rates["replied"] < 1:
        add("Engagement", "Reply rate", "warn", f"{rates['replied']}% of emails got replies in the last 30 days.", "Shorter, more personal emails usually do better.", "/sequences")
    unsubscribed = (
        await db.execute(select(func.count()).select_from(SequenceEnrollment).where(SequenceEnrollment.organization_id == org, SequenceEnrollment.status == "unsubscribed", SequenceEnrollment.finished_at >= since))
    ).scalar_one()
    if stats["sent"] >= 50 and unsubscribed / max(1, stats["sent"]) > 0.02:
        add("Engagement", "Unsubscribes", "warn", f"{unsubscribed} people unsubscribed from {stats['sent']} emails.", "Tighten targeting with personas so emails reach the right people.", "/settings/personas")
    steps = (
        await db.execute(
            select(SequenceStep, Sequence.name)
            .join(Sequence, Sequence.id == SequenceStep.sequence_id)
            .where(SequenceStep.organization_id == org, SequenceStep.kind.in_(("auto_email", "manual_email")), Sequence.status.in_(("active", "draft", "paused")))
        )
    ).all()
    for step, name in steps:
        issues = outbound.content_issues(step.subject, step.body)
        if issues:
            add("Content", f"{name} · step {step.position + 1}", "warn", "; ".join(issues), "Edit the step to fix these.", f"/sequences/{step.sequence_id}")
    summary = {s: sum(1 for c in checks if c["status"] == s) for s in ("pass", "warn", "fail")}
    await db.commit()
    return {"checks": checks, "summary": summary, "score": max(0, 100 - summary["fail"] * 15 - summary["warn"] * 5), "checked_at": utcnow()}


# ===========================================================================
# Snippets
# ===========================================================================


class SnippetPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    shortcut: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_-]*$")
    body: str = Field(min_length=1, max_length=20000)
    category: str | None = Field(default=None, max_length=60)
    is_shared: bool | None = None


def snippet_out(row: Snippet, names: dict[str, str], principal: Principal) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "shortcut": row.shortcut,
        "body": row.body,
        "category": row.category,
        "is_shared": row.is_shared,
        "owner_name": names.get(str(row.owner_user_id)) if row.owner_user_id else None,
        "usage_count": row.usage_count,
        "updated_at": row.updated_at,
        "can_edit": row.owner_user_id == principal.user_id or principal.can("outbound.manage"),
    }


def _visible_snippets(principal: Principal):
    return select(Snippet).where(Snippet.organization_id == principal.organization_id, or_(Snippet.is_shared.is_(True), Snippet.owner_user_id == principal.user_id))


@router.get("/snippets")
async def list_snippets(search: str | None = Query(default=None, max_length=100), principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    query = _visible_snippets(principal)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(Snippet.name.ilike(like), Snippet.shortcut.ilike(like), Snippet.body.ilike(like)))
    rows = (await db.execute(query.order_by(Snippet.usage_count.desc(), Snippet.name))).scalars().all()
    names = await names_for(db, principal.organization_id, "user", [r.owner_user_id for r in rows])
    return {"items": [snippet_out(r, names, principal) for r in rows]}


async def _shortcut_free(db: AsyncSession, principal: Principal, shortcut: str | None, exclude: uuid.UUID | None = None) -> None:
    if not shortcut:
        return
    query = _visible_snippets(principal).where(func.lower(Snippet.shortcut) == shortcut.lower())
    if exclude:
        query = query.where(Snippet.id != exclude)
    if (await db.execute(query)).scalars().first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"/{shortcut} is already used by another snippet.")


@router.post("/snippets", status_code=status.HTTP_201_CREATED)
async def create_snippet(payload: SnippetPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    await _shortcut_free(db, principal, payload.shortcut)
    sharing = await workspace_settings.get(db, principal.organization_id, "sharing")
    row = Snippet(
        id=uuid.uuid4(), organization_id=principal.organization_id, name=payload.name.strip(), shortcut=payload.shortcut or None, body=payload.body,
        category=payload.category, is_shared=sharing.snippets_shared if payload.is_shared is None else payload.is_shared, owner_user_id=principal.user_id,
    )
    db.add(row)
    await db.commit()
    return snippet_out(row, {str(principal.user_id): principal.full_name}, principal)


async def _snippet(db: AsyncSession, principal: Principal, snippet_id: uuid.UUID, edit: bool = False) -> Snippet:
    row = await db.get(Snippet, snippet_id)
    if row is None or row.organization_id != principal.organization_id or (not row.is_shared and row.owner_user_id != principal.user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Snippet not found.")
    if edit and row.owner_user_id != principal.user_id and not principal.can("outbound.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the snippet's owner can change it.")
    return row


@router.put("/snippets/{snippet_id}")
async def update_snippet(snippet_id: uuid.UUID, payload: SnippetPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _snippet(db, principal, snippet_id, edit=True)
    await _shortcut_free(db, principal, payload.shortcut, exclude=row.id)
    row.name, row.shortcut, row.body, row.category = payload.name.strip(), payload.shortcut or None, payload.body, payload.category
    if payload.is_shared is not None:
        row.is_shared = payload.is_shared
    await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.owner_user_id])
    return snippet_out(row, names, principal)


@router.delete("/snippets/{snippet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snippet(snippet_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _snippet(db, principal, snippet_id, edit=True)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SnippetUse(BaseModel):
    record_type: str | None = Field(default=None, pattern="^(lead|contact)$")
    record_id: uuid.UUID | None = None


@router.post("/snippets/{snippet_id}/use")
async def use_snippet(snippet_id: uuid.UUID, payload: SnippetUse, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Count a use and return the text, with merge fields filled for a record when one is given."""

    row = await _snippet(db, principal, snippet_id)
    row.usage_count = (row.usage_count or 0) + 1
    text = row.body
    if payload.record_type and payload.record_id:
        model = Contact if payload.record_type == "contact" else Lead
        record = await db.get(model, payload.record_id)
        if record is not None and record.organization_id == principal.organization_id:
            sender = await db.get(User, principal.user_id)
            text = render(row.body, await record_context(db, record, sender=sender))
    await db.commit()
    return {"text": text}


# ===========================================================================
# Sequences
# ===========================================================================


class SequenceSettingsPayload(BaseModel):
    stop_on_reply: bool = True
    stop_on_meeting: bool = True


class SequencePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    is_shared: bool | None = None
    settings: SequenceSettingsPayload = Field(default_factory=SequenceSettingsPayload)


class StepPayload(BaseModel):
    kind: str = Field(pattern="^(auto_email|manual_email|call|linkedin|task)$")
    delay_days: int = Field(default=0, ge=0, le=365)
    delay_hours: int = Field(default=0, ge=0, le=23)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=20000)
    note: str | None = Field(default=None, max_length=5000)
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    thread_reply: bool = True
    is_active: bool = True


async def _sequence(db: AsyncSession, principal: Principal, sequence_id: uuid.UUID, edit: bool = False) -> Sequence:
    row = await db.get(Sequence, sequence_id)
    if row is None or row.organization_id != principal.organization_id or (not row.is_shared and row.owner_user_id != principal.user_id and not principal.can("records.view_all")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sequence not found.")
    if edit:
        if not principal.can("sequences.manage"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role doesn't allow building sequences.")
        if row.owner_user_id != principal.user_id and not principal.can("outbound.manage") and not row.is_shared:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the sequence's owner can change it.")
    return row


async def _sequence_stats(db: AsyncSession, org: uuid.UUID, sequence_ids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    if not sequence_ids:
        return {}
    enroll_rows = (
        await db.execute(
            select(SequenceEnrollment.sequence_id, SequenceEnrollment.status, func.count())
            .where(SequenceEnrollment.organization_id == org, SequenceEnrollment.sequence_id.in_(sequence_ids))
            .group_by(SequenceEnrollment.sequence_id, SequenceEnrollment.status)
        )
    ).all()
    message_rows = (
        await db.execute(
            select(
                SequenceMessage.sequence_id,
                func.count().filter(SequenceMessage.status == "sent"),
                func.count().filter(SequenceMessage.open_count > 0),
                func.count().filter(SequenceMessage.click_count > 0),
                func.count().filter(SequenceMessage.replied_at.is_not(None)),
                func.count().filter(SequenceMessage.bounced_at.is_not(None)),
            )
            .where(SequenceMessage.organization_id == org, SequenceMessage.sequence_id.in_(sequence_ids), SequenceMessage.kind.in_(("auto_email", "manual_email")))
            .group_by(SequenceMessage.sequence_id)
        )
    ).all()
    out: dict[str, dict[str, Any]] = {str(s): {"enrollments": {}, "total": 0, "sent": 0, "opened": 0, "clicked": 0, "replied": 0, "bounced": 0} for s in sequence_ids}
    for sid, state, count in enroll_rows:
        out[str(sid)]["enrollments"][state] = int(count)
        out[str(sid)]["total"] += int(count)
    for sid, sent, opened, clicked, replied, bounced in message_rows:
        out[str(sid)].update(sent=int(sent), opened=int(opened), clicked=int(clicked), replied=int(replied), bounced=int(bounced))
    for value in out.values():
        value["rates"] = _rates(value)
        value["active"] = value["enrollments"].get("active", 0) + value["enrollments"].get("paused", 0)
    return out


def step_out(step: SequenceStep) -> dict[str, Any]:
    return {
        "id": str(step.id),
        "position": step.position,
        "kind": step.kind,
        "kind_label": outbound.STEP_KINDS.get(step.kind, step.kind),
        "delay_days": step.delay_days,
        "delay_hours": step.delay_hours,
        "subject": step.subject,
        "body": step.body,
        "note": step.note,
        "priority": step.priority,
        "thread_reply": step.thread_reply,
        "is_active": step.is_active,
        "issues": outbound.content_issues(step.subject, step.body) if step.kind in ("auto_email", "manual_email") else [],
    }


def sequence_out(row: Sequence, names: dict[str, str], stats: dict[str, Any] | None, principal: Principal, step_count: int | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "is_shared": row.is_shared,
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "owner_name": names.get(str(row.owner_user_id)) if row.owner_user_id else None,
        "settings": {"stop_on_reply": True, "stop_on_meeting": True, **(row.settings or {})},
        "stats": stats or {},
        "step_count": step_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "can_edit": principal.can("sequences.manage") and (row.owner_user_id == principal.user_id or row.is_shared or principal.can("outbound.manage")),
    }


@router.get("/sequences", dependencies=[Depends(require_feature("sequences"))])
async def list_sequences(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(draft|active|paused|archived)$"),
    mine: bool = False,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(Sequence).where(Sequence.organization_id == org)
    if not principal.can("records.view_all"):
        query = query.where(or_(Sequence.is_shared.is_(True), Sequence.owner_user_id == principal.user_id))
    if mine:
        query = query.where(Sequence.owner_user_id == principal.user_id)
    if status_filter:
        query = query.where(Sequence.status == status_filter)
    else:
        query = query.where(Sequence.status != "archived")
    rows = (await db.execute(query.order_by(case((Sequence.status == "active", 0), else_=1), Sequence.updated_at.desc()))).scalars().all()
    stats = await _sequence_stats(db, org, [r.id for r in rows])
    counts = dict(
        (
            await db.execute(
                select(SequenceStep.sequence_id, func.count()).where(SequenceStep.sequence_id.in_([r.id for r in rows] or [uuid.uuid4()]), SequenceStep.is_active.is_(True)).group_by(SequenceStep.sequence_id)
            )
        ).all()
    )
    names = await names_for(db, org, "user", [r.owner_user_id for r in rows])
    return {
        "items": [sequence_out(r, names, stats.get(str(r.id)), principal, int(counts.get(r.id, 0))) for r in rows],
        "can_create": principal.can("sequences.manage"),
    }


@router.post("/sequences", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("sequences"))])
async def create_sequence(payload: SequencePayload, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    sharing = await workspace_settings.get(db, principal.organization_id, "sharing")
    row = Sequence(
        id=uuid.uuid4(), organization_id=principal.organization_id, name=payload.name.strip(), description=payload.description,
        owner_user_id=principal.user_id, is_shared=sharing.sequences_shared if payload.is_shared is None else payload.is_shared,
        settings=payload.settings.model_dump(), status="draft",
    )
    db.add(row)
    await db.commit()
    return sequence_out(row, {str(principal.user_id): principal.full_name}, None, principal, 0)


@router.get("/sequences/{sequence_id}", dependencies=[Depends(require_feature("sequences"))])
async def get_sequence(sequence_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id)
    steps = list((await db.execute(select(SequenceStep).where(SequenceStep.sequence_id == row.id).order_by(SequenceStep.position))).scalars())
    stats = await _sequence_stats(db, principal.organization_id, [row.id])
    step_stats = {
        str(step_id): {"sent": int(sent), "opened": int(opened), "clicked": int(clicked), "replied": int(replied), "bounced": int(bounced), "tasks": int(tasks)}
        for step_id, sent, opened, clicked, replied, bounced, tasks in (
            await db.execute(
                select(
                    SequenceMessage.step_id,
                    func.count().filter(SequenceMessage.status == "sent"),
                    func.count().filter(SequenceMessage.open_count > 0),
                    func.count().filter(SequenceMessage.click_count > 0),
                    func.count().filter(SequenceMessage.replied_at.is_not(None)),
                    func.count().filter(SequenceMessage.bounced_at.is_not(None)),
                    func.count().filter(SequenceMessage.task_id.is_not(None)),
                )
                .where(SequenceMessage.sequence_id == row.id)
                .group_by(SequenceMessage.step_id)
            )
        ).all()
    }
    waiting = dict(
        (
            await db.execute(
                select(SequenceEnrollment.current_position, func.count())
                .where(SequenceEnrollment.sequence_id == row.id, SequenceEnrollment.status.in_(("active", "paused")))
                .group_by(SequenceEnrollment.current_position)
            )
        ).all()
    )
    names = await names_for(db, principal.organization_id, "user", [row.owner_user_id])
    active_index = 0
    out_steps = []
    for step in steps:
        item = step_out(step)
        item["stats"] = step_stats.get(str(step.id), {})
        item["waiting"] = int(waiting.get(active_index, 0)) if step.is_active else 0
        if step.is_active:
            active_index += 1
        out_steps.append(item)
    defaults = await workspace_settings.get(db, principal.organization_id, "sequences")
    protection = await workspace_settings.get(db, principal.organization_id, "sending")
    return {
        **sequence_out(row, names, stats.get(str(row.id)), principal, sum(1 for s in steps if s.is_active)),
        "steps": out_steps,
        "defaults": defaults.model_dump(mode="json"),
        "schedule": {
            "days": protection.send_days, "start": protection.window_start_hour, "end": protection.window_end_hour, "timezone": protection.timezone,
        },
        "step_kinds": [{"key": k, "label": v} for k, v in outbound.STEP_KINDS.items()],
    }


@router.put("/sequences/{sequence_id}", dependencies=[Depends(require_feature("sequences"))])
async def update_sequence(sequence_id: uuid.UUID, payload: SequencePayload, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id, edit=True)
    row.name, row.description = payload.name.strip(), payload.description
    if payload.is_shared is not None:
        row.is_shared = payload.is_shared
    row.settings = {**(row.settings or {}), **payload.settings.model_dump()}
    await db.commit()
    return await get_sequence(sequence_id, principal, db)


class SequenceStatus(BaseModel):
    status: str = Field(pattern="^(active|paused|archived|draft)$")


@router.post("/sequences/{sequence_id}/status", dependencies=[Depends(require_feature("sequences"))])
async def set_sequence_status(sequence_id: uuid.UUID, payload: SequenceStatus, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id, edit=True)
    if payload.status == "active":
        steps = await outbound.active_steps(db, row.id)
        if not steps:
            raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one step before turning the sequence on.")
        for number, step in enumerate(steps, start=1):
            if step.kind != "auto_email":
                continue
            if not (step.body or "").strip():
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {number} needs a message.")
            if not (step.subject or "").strip() and not (step.thread_reply and number > 1):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Step {number} needs a subject.")
    if payload.status == "archived":
        for enrollment in (await db.execute(select(SequenceEnrollment).where(SequenceEnrollment.sequence_id == row.id, SequenceEnrollment.status.in_(("active", "paused"))))).scalars():
            await outbound.finish(db, enrollment, "removed", "Sequence archived")
        row.archived_at = utcnow()
    previous = row.status
    row.status = payload.status
    record_audit(db, organization_id=principal.organization_id, action="update", entity_type="sequence", entity_id=row.id, entity_label=row.name,
                 changes={"status": {"from": previous, "to": payload.status}})
    await db.commit()
    return await get_sequence(sequence_id, principal, db)


@router.delete("/sequences/{sequence_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("sequences"))])
async def delete_sequence(sequence_id: uuid.UUID, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id, edit=True)
    used = (await db.execute(select(func.count()).select_from(SequenceEnrollment).where(SequenceEnrollment.sequence_id == row.id))).scalar_one()
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, "People have been through this sequence, so archive it instead to keep its history.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sequences/{sequence_id}/duplicate", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("sequences"))])
async def duplicate_sequence(sequence_id: uuid.UUID, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id)
    copy = Sequence(id=uuid.uuid4(), organization_id=principal.organization_id, name=f"{row.name} (copy)"[:200], description=row.description,
                    owner_user_id=principal.user_id, is_shared=row.is_shared, settings=dict(row.settings or {}), status="draft")
    db.add(copy)
    await db.flush()
    for step in (await db.execute(select(SequenceStep).where(SequenceStep.sequence_id == row.id).order_by(SequenceStep.position))).scalars():
        db.add(SequenceStep(id=uuid.uuid4(), organization_id=principal.organization_id, sequence_id=copy.id, position=step.position, kind=step.kind,
                            delay_days=step.delay_days, delay_hours=step.delay_hours, subject=step.subject, body=step.body, note=step.note,
                            priority=step.priority, thread_reply=step.thread_reply, is_active=step.is_active))
    await db.commit()
    return {"id": str(copy.id)}


# --------------------------------------------------------------------------- steps


def _validate_step(payload: StepPayload, position: int) -> None:
    if payload.kind == "auto_email":
        if not (payload.body or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An automatic email needs a message.")
        if not (payload.subject or "").strip() and not (payload.thread_reply and position > 0):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An automatic email needs a subject (or send it as a reply in the same thread).")
    if payload.kind in ("call", "linkedin", "task") and not (payload.note or "").strip() and payload.kind == "task":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Describe what the task is.")


@router.post("/sequences/{sequence_id}/steps", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("sequences"))])
async def add_step(sequence_id: uuid.UUID, payload: StepPayload, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id, edit=True)
    count = (await db.execute(select(func.count()).select_from(SequenceStep).where(SequenceStep.sequence_id == row.id))).scalar_one()
    if count >= 30:
        raise HTTPException(status.HTTP_409_CONFLICT, "A sequence can have up to 30 steps.")
    _validate_step(payload, count)
    step = SequenceStep(id=uuid.uuid4(), organization_id=principal.organization_id, sequence_id=row.id, position=count, **payload.model_dump())
    db.add(step)
    await db.commit()
    return step_out(step)


async def _step(db: AsyncSession, row: Sequence, step_id: uuid.UUID) -> SequenceStep:
    step = await db.get(SequenceStep, step_id)
    if step is None or step.sequence_id != row.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found.")
    return step


@router.put("/sequences/{sequence_id}/steps/{step_id}", dependencies=[Depends(require_feature("sequences"))])
async def update_step(sequence_id: uuid.UUID, step_id: uuid.UUID, payload: StepPayload, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id, edit=True)
    step = await _step(db, row, step_id)
    _validate_step(payload, step.position)
    for key, value in payload.model_dump().items():
        setattr(step, key, value)
    await db.commit()
    return step_out(step)


@router.delete("/sequences/{sequence_id}/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("sequences"))])
async def delete_step(sequence_id: uuid.UUID, step_id: uuid.UUID, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id, edit=True)
    step = await _step(db, row, step_id)
    active = (
        await db.execute(select(func.count()).select_from(SequenceEnrollment).where(SequenceEnrollment.sequence_id == row.id, SequenceEnrollment.status.in_(("active", "paused"))))
    ).scalar_one()
    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, "People are in this sequence. Turn the step off instead of deleting it.")
    await db.delete(step)
    await db.flush()
    for position, other in enumerate((await db.execute(select(SequenceStep).where(SequenceStep.sequence_id == row.id).order_by(SequenceStep.position))).scalars()):
        other.position = position
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class StepOrder(BaseModel):
    step_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)


@router.post("/sequences/{sequence_id}/steps/reorder", dependencies=[Depends(require_feature("sequences"))])
async def reorder_steps(sequence_id: uuid.UUID, payload: StepOrder, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id, edit=True)
    active = (
        await db.execute(select(func.count()).select_from(SequenceEnrollment).where(SequenceEnrollment.sequence_id == row.id, SequenceEnrollment.status.in_(("active", "paused"))))
    ).scalar_one()
    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Steps can't be reordered while people are in the sequence.")
    steps = {s.id: s for s in (await db.execute(select(SequenceStep).where(SequenceStep.sequence_id == row.id))).scalars()}
    if set(payload.step_ids) != set(steps):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Send every step in the new order.")
    for position, step_id in enumerate(payload.step_ids):
        steps[step_id].position = position
    await db.commit()
    return await get_sequence(sequence_id, principal, db)


class PreviewPayload(BaseModel):
    record_type: str = Field(pattern="^(lead|contact)$")
    record_id: uuid.UUID


@router.post("/sequences/{sequence_id}/steps/{step_id}/preview", dependencies=[Depends(require_feature("sequences"))])
async def preview_step(sequence_id: uuid.UUID, step_id: uuid.UUID, payload: PreviewPayload, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    row = await _sequence(db, principal, sequence_id)
    step = await _step(db, row, step_id)
    model = Contact if payload.record_type == "contact" else Lead
    record = await db.get(model, payload.record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    sender = await db.get(User, principal.user_id)
    context = await record_context(db, record, sender=sender)
    subject, body = render(step.subject, context), render(step.body or step.note, context)
    return {"subject": subject, "body": body, "issues": outbound.content_issues(subject, body), "to": record.email}


# --------------------------------------------------------------------------- enrollments


class EnrollPayload(BaseModel):
    record_type: str = Field(pattern="^(lead|contact)$")
    record_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    sender_user_id: uuid.UUID | None = None


@router.post("/sequences/{sequence_id}/enroll", dependencies=[Depends(require_feature("sequences"))])
async def enroll_people(sequence_id: uuid.UUID, payload: EnrollPayload, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    # Anyone can use a shared sequence; a private one only its owner (or an outbound admin).
    row = await _sequence(db, principal, sequence_id, edit=True)
    sender = payload.sender_user_id
    if sender and sender != principal.user_id and not principal.can("outbound.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only outbound admins can send from someone else's mailbox.")
    result = await outbound.enroll(db, principal, row, payload.record_type, payload.record_ids, sender)
    await db.commit()
    return result


def enrollment_out(row: SequenceEnrollment, names: dict[str, str], steps: int, sequence_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "sequence_id": str(row.sequence_id),
        "sequence_name": sequence_name,
        "record_type": row.record_type,
        "record_id": str(row.record_id),
        "href": f"/{row.record_type}s/{row.record_id}",
        "name": row.name,
        "email": row.email,
        "status": row.status,
        "step": min(row.current_position + 1, steps) if steps else 0,
        "steps": steps,
        "next_run_at": row.next_run_at,
        "waiting_on_task": row.status == "active" and row.next_run_at is None,
        "sender_name": names.get(str(row.sender_user_id)) if row.sender_user_id else None,
        "enrolled_at": row.created_at,
        "finished_at": row.finished_at,
        "finished_reason": row.finished_reason,
        "error": row.error,
    }


@router.get("/sequences/{sequence_id}/enrollments", dependencies=[Depends(require_feature("sequences"))])
async def list_enrollments(
    sequence_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    row = await _sequence(db, principal, sequence_id)
    query = select(SequenceEnrollment).where(SequenceEnrollment.sequence_id == row.id)
    if status_filter == "active":
        query = query.where(SequenceEnrollment.status.in_(("active", "paused")))
    elif status_filter:
        query = query.where(SequenceEnrollment.status == status_filter)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(SequenceEnrollment.name.ilike(like), SequenceEnrollment.email.ilike(like)))
    rows = (await db.execute(query.order_by(SequenceEnrollment.created_at.desc()).limit(500))).scalars().all()
    steps = len(await outbound.active_steps(db, row.id))
    names = await names_for(db, principal.organization_id, "user", [r.sender_user_id for r in rows])
    return {"items": [enrollment_out(r, names, steps) for r in rows]}


@router.get("/sequences/for/{record_type}/{record_id}", dependencies=[Depends(require_feature("sequences"))])
async def enrollments_for_record(record_type: str, record_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(SequenceEnrollment, Sequence.name)
            .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
            .where(SequenceEnrollment.organization_id == principal.organization_id, SequenceEnrollment.record_type == record_type, SequenceEnrollment.record_id == record_id)
            .order_by(SequenceEnrollment.created_at.desc())
        )
    ).all()
    names = await names_for(db, principal.organization_id, "user", [r.sender_user_id for r, _ in rows])
    out = []
    for enrollment, name in rows:
        steps = len(await outbound.active_steps(db, enrollment.sequence_id))
        item = enrollment_out(enrollment, names, steps, name)
        messages = (
            await db.execute(select(SequenceMessage).where(SequenceMessage.enrollment_id == enrollment.id).order_by(SequenceMessage.created_at))
        ).scalars().all()
        item["history"] = [
            {"kind": m.kind, "subject": m.subject, "status": m.status, "sent_at": m.sent_at or m.created_at, "opened": m.open_count > 0,
             "clicked": m.click_count > 0, "replied": m.replied_at is not None, "bounced": m.bounced_at is not None, "delivery": m.delivery}
            for m in messages
        ]
        out.append(item)
    return {"items": out}


async def _enrollment(db: AsyncSession, principal: Principal, enrollment_id: uuid.UUID) -> tuple[SequenceEnrollment, Sequence]:
    row = await db.get(SequenceEnrollment, enrollment_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrollment not found.")
    sequence = await _sequence(db, principal, row.sequence_id)
    # Changing someone's place in a sequence needs the right to use that sequence,
    # unless it's the rep the enrollment sends as.
    if row.sender_user_id != principal.user_id:
        await _sequence(db, principal, row.sequence_id, edit=True)
    return row, sequence


class EnrollmentAction(BaseModel):
    action: str = Field(pattern="^(pause|resume|remove|finish|skip_wait)$")


@router.post("/sequence-enrollments/{enrollment_id}/action", dependencies=[Depends(require_feature("sequences"))])
async def enrollment_action(enrollment_id: uuid.UUID, payload: EnrollmentAction, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    row, sequence = await _enrollment(db, principal, enrollment_id)
    if row.status in outbound.FINISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This person has already left the sequence.")
    if payload.action == "pause":
        row.status = "paused"
    elif payload.action == "resume":
        row.status = "active"
        if row.next_run_at is not None and row.next_run_at < utcnow():
            row.next_run_at = utcnow()
    elif payload.action == "remove":
        await outbound.finish(db, row, "removed", f"Removed by {principal.full_name}")
    elif payload.action == "finish":
        await outbound.finish(db, row, "finished", f"Marked finished by {principal.full_name}")
    elif payload.action == "skip_wait":
        if row.status != "active":
            raise HTTPException(status.HTTP_409_CONFLICT, "Resume the person first.")
        if row.next_run_at is None:
            # Waiting on a manual task: treat it as done.
            message = (
                await db.execute(select(SequenceMessage).where(SequenceMessage.enrollment_id == row.id, SequenceMessage.status == "task_created").order_by(SequenceMessage.created_at.desc()).limit(1))
            ).scalar_one_or_none()
            if message is not None:
                message.status = "skipped"
                if message.task_id:
                    task = await db.get(Task, message.task_id)
                    if task is not None and task.status in ("open", "in_progress"):
                        task.status = "cancelled"
            await outbound.advance(db, row, await outbound.active_steps(db, row.sequence_id))
        else:
            row.next_run_at = utcnow()
    await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.sender_user_id])
    return enrollment_out(row, names, len(await outbound.active_steps(db, row.sequence_id)), sequence.name)


class ManualSend(BaseModel):
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=20000)


@router.post("/sequence-enrollments/{enrollment_id}/send", dependencies=[Depends(require_feature("sequences"))])
async def send_manual_email(enrollment_id: uuid.UUID, payload: ManualSend, principal: Principal = Depends(require_permission("sequences.manage")), db: AsyncSession = Depends(get_db)):
    """Send the current manual-email step now (optionally edited) and move on."""

    row, sequence = await _enrollment(db, principal, enrollment_id)
    if row.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "This person isn't active in the sequence.")
    steps = await outbound.active_steps(db, row.sequence_id)
    if row.current_position >= len(steps) or steps[row.current_position].kind != "manual_email":
        raise HTTPException(status.HTTP_409_CONFLICT, "The current step isn't a manual email.")
    if row.sender_user_id != principal.user_id and not principal.can("outbound.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the sender can send this email.")
    step = steps[row.current_position]
    try:
        message = await outbound.send_email_step(db, row, step, subject_override=payload.subject, body_override=payload.body)
    except ValueError as exc:
        await db.commit()  # keeps the enrollment finished when they're suppressed or deleted
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if message.status != "sent":
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The email couldn't be sent: {message.error}")
    for waiting in (await db.execute(select(SequenceMessage).where(SequenceMessage.enrollment_id == row.id, SequenceMessage.status == "task_created"))).scalars():
        waiting.status = "completed"
        if waiting.task_id:
            task = await db.get(Task, waiting.task_id)
            if task is not None and task.status in ("open", "in_progress"):
                task.status = "completed"
                task.completed_at = utcnow()
    await outbound.advance(db, row, steps)
    await db.commit()
    return {"sent": True, "delivery": message.delivery}


# ===========================================================================
# Dialer (Twilio click-to-call)
# ===========================================================================


async def _twilio(db: AsyncSession, org: uuid.UUID) -> tuple[str, str, str]:
    dialer = await workspace_settings.get(db, org, "dialer")
    token = await workspace_settings.secret(db, org, "dialer", "twilio_auth_token")
    if not (dialer.twilio_account_sid and token and dialer.twilio_from_number):
        raise HTTPException(status.HTTP_409_CONFLICT, "Add your Twilio account SID, auth token and phone number in Dialer settings.")
    return dialer.twilio_account_sid, token, dialer.twilio_from_number


@router.post("/dialer/test")
async def test_dialer(principal: Principal = Depends(require_permission("outbound.manage")), db: AsyncSession = Depends(get_db)):
    sid, token, number = await _twilio(db, principal.organization_id)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json", auth=(sid, token))
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Twilio couldn't be reached ({exc.__class__.__name__})."}
    if response.status_code == 200:
        return {"ok": True, "message": f"Connected to Twilio account “{response.json().get('friendly_name', sid)}”. Calls will show {number}."}
    return {"ok": False, "message": "Twilio rejected the account SID or auth token."}


class CallPayload(BaseModel):
    to: str = Field(min_length=5, max_length=40)
    record_type: str | None = Field(default=None, pattern="^(lead|contact|account)$")
    record_id: uuid.UUID | None = None


@router.post("/dialer/call")
async def start_call(payload: CallPayload, principal: Principal = Depends(require_feature("calling")), db: AsyncSession = Depends(get_db)):
    """Ring the rep's phone first, then connect them to the person (Twilio)."""

    if not principal.can("records.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role doesn't allow making calls.")
    dialer = await workspace_settings.get(db, principal.organization_id, "dialer")
    if dialer.calling_method != "twilio":
        raise HTTPException(status.HTTP_409_CONFLICT, "Calls go through your phone (tel: links). Switch the dialer to Twilio to call from AIVA.")
    sid, token, number = await _twilio(db, principal.organization_id)
    user = await db.get(User, principal.user_id)
    if not user or not user.phone:
        raise HTTPException(status.HTTP_409_CONFLICT, "Add your phone number in Settings → Profile so AIVA can ring you first.")
    to = "".join(ch for ch in payload.to if ch.isdigit() or ch == "+")
    import html as _html

    record = f' record="record-from-answer"' if dialer.record_calls else ""
    twiml = f'<Response><Say>Connecting your AIVA call.</Say><Dial callerId="{_html.escape(number)}"{record}>{_html.escape(to)}</Dial></Response>'
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                data={"To": user.phone, "From": number, "Twiml": twiml},
                auth=(sid, token),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Twilio couldn't be reached ({exc.__class__.__name__}).") from exc
    body = response.json() if response.content else {}
    if response.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Twilio: {body.get('message') or response.status_code}")
    return {"call_sid": body.get("sid"), "status": body.get("status"), "message": f"Calling your phone ({user.phone}) now; answer to connect to {to}."}


# ===========================================================================
# Public click tracking
# ===========================================================================


@public_router.get("/click/{token}")
async def track_click(token: str, u: str = Query(max_length=4000), s: str = Query(max_length=64), db: AsyncSession = Depends(get_db)):
    import hmac as _hmac

    if not _hmac.compare_digest(outbound.click_signature(token, u), s) or not u.lower().startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link isn't valid.")
    message = (await db.execute(select(SequenceMessage).where(SequenceMessage.token == token))).scalar_one_or_none()
    if message is not None:
        message.clicked_at = message.clicked_at or utcnow()
        message.click_count = (message.click_count or 0) + 1
        if not message.open_count:
            message.open_count = 1
            message.opened_at = message.opened_at or utcnow()
        await db.commit()
    return RedirectResponse(u, status_code=status.HTTP_302_FOUND)
