"""Mass email: audiences, recipient lists, delivery, opens and unsubscribes."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import CampaignRecipient, EmailCampaign, EmailTemplate, Journey, TargetAccount
from app.models.lead import Lead
from app.models.organization import Organization
from app.models.user import User
from app.services import mailer
from app.services.catalog import OBJECTS
from app.services.merge import new_send_token, record_context, render, text_to_html, unsubscribe_url
from app.services.reporting import ReportFilter, _Query, build_condition

logger = logging.getLogger("aiva.campaigns")

AUDIENCE_TYPES = {"lead": Lead, "contact": Contact}
MAX_RECIPIENTS = 5000
INLINE_SEND_LIMIT = 200


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def audience_records(db: AsyncSession, principal: Principal, audience: dict[str, Any]) -> list[Any]:
    object_type = audience.get("object_type") or "contact"
    model = AUDIENCE_TYPES.get(object_type)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Send to leads or contacts.")
    definition = OBJECTS[object_type]
    q = _Query(definition)
    conditions = [
        model.organization_id == principal.organization_id,
        model.email.is_not(None),
        model.email != "",
        model.email_opt_out.is_(False),
    ]
    for raw in audience.get("filters") or []:
        conditions.append(build_condition(q, ReportFilter(**raw), principal))
    query = select(model)
    if object_type == "contact":
        conditions.append(Contact.is_archived.is_(False))
        tier = audience.get("abm_tier")
        if tier:
            target_accounts = select(TargetAccount.account_id).where(TargetAccount.organization_id == principal.organization_id)
            if tier != "all":
                target_accounts = target_accounts.where(TargetAccount.tier == tier)
            conditions.append(Contact.account_id.in_(target_accounts))
    if object_type == "lead":
        conditions.append(Lead.status != "converted")
    query = q.apply_joins(query).where(*conditions).order_by(model.created_at).limit(MAX_RECIPIENTS + 1)
    rows = list((await db.execute(query)).scalars().unique().all())
    if len(rows) > MAX_RECIPIENTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Narrow the audience to {MAX_RECIPIENTS:,} people or fewer.")
    return rows


async def audience_count(db: AsyncSession, principal: Principal, audience: dict[str, Any]) -> dict[str, Any]:
    rows = await audience_records(db, principal, audience)
    unique = {row.email.lower() for row in rows}
    sample = [
        {"id": str(row.id), "name": f"{row.first_name} {row.last_name}".strip(), "email": row.email}
        for row in rows[:5]
    ]
    return {"count": len(unique), "sample": sample}


async def prepare_recipients(db: AsyncSession, principal: Principal, campaign: EmailCampaign) -> int:
    """Create one queued, personalised message per unique email address."""

    records = await audience_records(db, principal, campaign.audience or {})
    sender = await db.get(User, principal.user_id)
    organization = await db.get(Organization, principal.organization_id)
    seen: set[str] = set()
    created = 0
    for record in records:
        email = record.email.strip().lower()
        if email in seen:
            continue
        seen.add(email)
        token = new_send_token()
        context = await record_context(db, record, sender=sender, organization=organization, token=token)
        db.add(
            CampaignRecipient(
                id=uuid.uuid4(),
                organization_id=campaign.organization_id,
                campaign_id=campaign.id,
                record_type=(campaign.audience or {}).get("object_type") or "contact",
                record_id=record.id,
                email=email,
                name=f"{record.first_name} {record.last_name}".strip() or None,
                token=token,
                status="queued",
                subject=render(campaign.subject, context)[:300],
                body=render(campaign.body, context),
            )
        )
        created += 1
    campaign.stats = {**(campaign.stats or {}), "recipients": created, "sent": 0, "failed": 0, "opened": 0, "unsubscribed": 0}
    if campaign.template_id:
        template = await db.get(EmailTemplate, campaign.template_id)
        if template is not None:
            template.usage_count = (template.usage_count or 0) + 1
    return created


async def recompute_stats(db: AsyncSession, campaign: EmailCampaign) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(CampaignRecipient.status == "sent"),
                func.count().filter(CampaignRecipient.status == "failed"),
                func.count().filter(CampaignRecipient.status == "queued"),
                func.count().filter(CampaignRecipient.opened_at.is_not(None)),
                func.count().filter(CampaignRecipient.unsubscribed_at.is_not(None)),
                func.count().filter(CampaignRecipient.delivery == "outbox"),
            ).where(CampaignRecipient.campaign_id == campaign.id)
        )
    ).one()
    total, sent, failed, queued, opened, unsubscribed, outbox = rows
    campaign.stats = {
        "recipients": total,
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "opened": opened,
        "unsubscribed": unsubscribed,
        "outbox": outbox,
        "open_rate": round(opened / sent * 100, 1) if sent else 0,
    }
    return campaign.stats


async def deliver(db: AsyncSession, *, campaign_id: uuid.UUID | None = None, limit: int = 100) -> int:
    """Send queued campaign and journey messages. Returns how many were processed."""

    now = utcnow()
    query = (
        select(CampaignRecipient, EmailCampaign)
        .outerjoin(EmailCampaign, EmailCampaign.id == CampaignRecipient.campaign_id)
        .where(CampaignRecipient.status == "queued")
        .order_by(CampaignRecipient.created_at)
        .limit(limit)
    )
    if campaign_id is not None:
        query = query.where(CampaignRecipient.campaign_id == campaign_id)
    else:
        query = query.where(
            (CampaignRecipient.campaign_id.is_(None))
            | ((EmailCampaign.status == "sending") & ((EmailCampaign.scheduled_at.is_(None)) | (EmailCampaign.scheduled_at <= now)))
        )
    rows = (await db.execute(query)).all()
    if not rows:
        return 0

    configs: dict[tuple, Any] = {}
    touched: set[uuid.UUID] = set()
    for recipient, campaign in rows:
        if campaign is not None:
            sender_id = campaign.created_by_user_id
        else:
            journey = await db.get(Journey, recipient.journey_id) if recipient.journey_id else None
            sender_id = journey.created_by_user_id if journey else None
        key = (recipient.organization_id, sender_id)
        if key not in configs:
            configs[key] = await mailer.sender_config(db, recipient.organization_id, sender_id)
        config = configs[key]
        record_model = AUDIENCE_TYPES.get(recipient.record_type)
        record = await db.get(record_model, recipient.record_id) if record_model else None
        if record is not None and getattr(record, "email_opt_out", False):
            recipient.status = "skipped"
            recipient.error = "Unsubscribed before sending."
            continue
        footer = (
            f'You received this email because you\'re in touch with us. <a href="{unsubscribe_url(recipient.token)}">Unsubscribe</a>.'
        )
        text = f"{recipient.body or ''}\n\n—\nUnsubscribe: {unsubscribe_url(recipient.token)}"
        try:
            delivery = await mailer.send(
                config,
                to=recipient.email,
                to_name=recipient.name,
                subject=recipient.subject or "(no subject)",
                text=text,
                html_body=text_to_html(recipient.body or "", token=recipient.token, footer=footer),
                reply_to=campaign.reply_to if campaign else None,
                from_name=campaign.from_name if campaign else None,
                headers={
                    "List-Unsubscribe": f"<{unsubscribe_url(recipient.token)}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                },
            )
            recipient.status = "sent"
            recipient.delivery = delivery
            recipient.sent_at = utcnow()
            recipient.error = None
        except mailer.MailError as exc:
            recipient.status = "failed"
            recipient.error = str(exc)[:500]
            logger.warning("Campaign email to %s failed: %s", recipient.email, exc)
        if recipient.status == "sent" and record is not None:
            db.add(
                Activity(
                    id=uuid.uuid4(),
                    organization_id=recipient.organization_id,
                    contact_id=record.id if recipient.record_type == "contact" else None,
                    account_id=getattr(record, "account_id", None) if recipient.record_type == "contact" else None,
                    lead_id=record.id if recipient.record_type == "lead" else None,
                    created_by_user_id=campaign.created_by_user_id if campaign else None,
                    activity_type="email",
                    subject=(recipient.subject or "(no subject)")[:250],
                    body=recipient.body,
                    direction="outbound",
                    occurred_at=utcnow(),
                    activity_metadata={
                        "source": "campaign" if campaign else "journey",
                        "campaign_id": str(campaign.id) if campaign else None,
                        "journey_id": str(recipient.journey_id) if recipient.journey_id else None,
                        "delivery": recipient.delivery,
                    },
                )
            )
        if campaign is not None:
            touched.add(campaign.id)
    await db.flush()

    for touched_id in touched:
        campaign = await db.get(EmailCampaign, touched_id)
        stats = await recompute_stats(db, campaign)
        if not stats.get("queued"):
            campaign.status = "sent" if stats.get("sent") or not stats.get("failed") else "failed"
            campaign.completed_at = utcnow()
    await db.commit()
    return len(rows)


async def start_scheduled(db: AsyncSession) -> int:
    """Move scheduled campaigns whose time has come into sending."""

    now = utcnow()
    campaigns = (
        await db.execute(select(EmailCampaign).where(EmailCampaign.status == "scheduled", EmailCampaign.scheduled_at <= now))
    ).scalars().all()
    for campaign in campaigns:
        campaign.status = "sending"
        campaign.started_at = now
    if campaigns:
        await db.commit()
    return len(campaigns)
