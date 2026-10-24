"""Credits and AI words: monthly allowances, usage records and limits.

Credits pay for data from outside providers (waterfall enrichment, data
requests). AI words count every word AIVA writes for people (drafts,
summaries, answers, agent suggestions, conversation notes). Both reset on
the first of each month (UTC). Allowances are per paid seat plus add-ons;
admins can also cap individual users (Settings → License settings).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plans import ADDONS, AI_WORDS_PER_SEAT, CREDITS_PER_SEAT
from app.models.governance import AiUsageEvent, CreditTransaction, OrgAddon
from app.services import workspace_settings
from app.services.billing import active_seats, get_subscription, plan_state

WORD_RE = re.compile(r"\S+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def period_key(moment: datetime | None = None) -> str:
    return (moment or utcnow()).strftime("%Y-%m")


def count_words(text: str | None) -> int:
    return len(WORD_RE.findall(text or ""))


async def active_addons(db: AsyncSession, organization_id: uuid.UUID) -> list[OrgAddon]:
    now = utcnow()
    rows = (
        await db.execute(select(OrgAddon).where(OrgAddon.organization_id == organization_id, OrgAddon.status.in_(("active", "cancelled"))))
    ).scalars().all()
    return [row for row in rows if row.status == "active" or (row.renews_at and row.renews_at > now)]


async def addon_features(db: AsyncSession, organization_id: uuid.UUID) -> frozenset[str]:
    keys: set[str] = set()
    for row in await active_addons(db, organization_id):
        addon = ADDONS.get(row.addon_key)
        if addon:
            keys |= set(addon.features)
    return frozenset(keys)


async def allowances(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, Any]:
    subscription = await get_subscription(db, organization_id)
    plan = plan_state(subscription).plan.key
    seats = await active_seats(db, organization_id)
    addons = await active_addons(db, organization_id)
    extra_credits = sum(ADDONS[a.addon_key].credits * a.quantity for a in addons if a.addon_key in ADDONS)
    extra_words = sum(ADDONS[a.addon_key].ai_words * a.quantity for a in addons if a.addon_key in ADDONS)
    return {
        "plan": plan,
        "seats": seats,
        "credits_per_seat": CREDITS_PER_SEAT.get(plan, 0),
        "credits_plan": CREDITS_PER_SEAT.get(plan, 0) * seats,
        "credits_addons": extra_credits,
        "credits_total": CREDITS_PER_SEAT.get(plan, 0) * seats + extra_credits,
        "ai_words_per_seat": AI_WORDS_PER_SEAT.get(plan, 0),
        "ai_words_plan": AI_WORDS_PER_SEAT.get(plan, 0) * seats,
        "ai_words_addons": extra_words,
        "ai_words_total": AI_WORDS_PER_SEAT.get(plan, 0) * seats + extra_words,
    }


async def credits_used(db: AsyncSession, organization_id: uuid.UUID, period: str | None = None, user_id: uuid.UUID | None = None) -> int:
    query = select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
        CreditTransaction.organization_id == organization_id, CreditTransaction.period == (period or period_key())
    )
    if user_id is not None:
        query = query.where(CreditTransaction.user_id == user_id)
    total = (await db.execute(query)).scalar_one()
    return max(0, -int(total))


async def words_used(db: AsyncSession, organization_id: uuid.UUID, period: str | None = None, user_id: uuid.UUID | None = None) -> int:
    query = select(func.coalesce(func.sum(AiUsageEvent.words), 0)).where(
        AiUsageEvent.organization_id == organization_id, AiUsageEvent.period == (period or period_key())
    )
    if user_id is not None:
        query = query.where(AiUsageEvent.user_id == user_id)
    return int((await db.execute(query)).scalar_one())


def _user_limit(limits: dict[str, int], default: int | None, user_id: uuid.UUID | None) -> int | None:
    if user_id is None:
        return None
    if str(user_id) in limits:
        return limits[str(user_id)]
    return default


async def credit_balance(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None = None) -> dict[str, Any]:
    allowance = await allowances(db, organization_id)
    used = await credits_used(db, organization_id)
    licenses = await workspace_settings.get(db, organization_id, "licenses")
    limit = _user_limit(licenses.credit_limits, licenses.credit_limit_default, user_id)
    mine = await credits_used(db, organization_id, user_id=user_id) if user_id else None
    remaining = allowance["credits_total"] - used
    if limit is not None and mine is not None:
        remaining = min(remaining, limit - mine)
    return {"total": allowance["credits_total"], "used": used, "remaining": max(0, remaining), "user_limit": limit, "user_used": mine}


async def charge_credits(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    amount: int,
    feature: str,
    *,
    record_type: str | None = None,
    record_id: uuid.UUID | None = None,
    description: str | None = None,
    check: bool = True,
) -> CreditTransaction | None:
    """Spend credits. Raises 402 when the workspace (or the user's cap) has run out."""

    if amount <= 0:
        return None
    if check:
        balance = await credit_balance(db, organization_id, user_id)
        if balance["remaining"] < amount:
            if balance["user_limit"] is not None and (balance["user_used"] or 0) + amount > balance["user_limit"]:
                message = "You've used your monthly credit limit. Ask an admin to raise it in Settings → License settings."
            else:
                message = "Your workspace is out of credits for this month. Add a credit pack in Settings → Product add-ons."
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, message)
    row = CreditTransaction(
        id=uuid.uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        kind="usage",
        amount=-amount,
        feature=feature,
        record_type=record_type,
        record_id=record_id,
        description=(description or "")[:300] or None,
        period=period_key(),
    )
    db.add(row)
    return row


async def refund_credits(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None, amount: int, feature: str, description: str) -> None:
    if amount > 0:
        db.add(
            CreditTransaction(
                id=uuid.uuid4(), organization_id=organization_id, user_id=user_id, kind="refund", amount=amount,
                feature=feature, description=description[:300], period=period_key(),
            )
        )


async def ai_words_available(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None) -> dict[str, Any]:
    allowance = await allowances(db, organization_id)
    used = await words_used(db, organization_id)
    licenses = await workspace_settings.get(db, organization_id, "licenses")
    limit = _user_limit(licenses.ai_word_limits, licenses.ai_word_limit_default, user_id)
    mine = await words_used(db, organization_id, user_id=user_id) if user_id else 0
    remaining = allowance["ai_words_total"] - used
    if limit is not None:
        remaining = min(remaining, limit - mine)
    return {"total": allowance["ai_words_total"], "used": used, "remaining": max(0, remaining), "user_limit": limit, "user_used": mine}


async def ensure_ai_words(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    available = await ai_words_available(db, organization_id, user_id)
    if available["remaining"] <= 0:
        if available["user_limit"] is not None and available["user_used"] >= available["user_limit"]:
            message = "You've used your monthly AI word limit. Ask an admin to raise it in Settings → License settings."
        else:
            message = "Your workspace has used all of this month's AI words. Add an AI words pack in Settings → Product add-ons."
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, message)


def record_ai_words(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None, feature: str, text: str | None, source: str) -> int:
    words = count_words(text)
    if words:
        db.add(
            AiUsageEvent(
                id=uuid.uuid4(), organization_id=organization_id, user_id=user_id, feature=feature,
                words=words, source=source, period=period_key(),
            )
        )
    return words
