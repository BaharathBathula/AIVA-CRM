"""Subscriptions: trials, plan changes, seats, invoices and plan limits."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plans import ALL_FEATURES, LIMIT_LABELS, PAID_PLANS, PLANS, TRIAL_DAYS, Plan, plan_for
from app.models.editions import BillingInvoice, Subscription
from app.models.membership import OrganizationMembership


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def add_month(moment: datetime) -> datetime:
    month = moment.month + 1
    year = moment.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(moment.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return moment.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class PlanState:
    plan: Plan
    status: str
    read_only: bool
    features: frozenset[str]
    trial_ends_at: datetime | None
    days_left: int | None

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.key,
            "plan_name": self.plan.name,
            "status": self.status,
            "read_only": self.read_only,
            "trial_ends_at": self.trial_ends_at,
            "days_left": self.days_left,
            "features": sorted(self.features),
            "limits": self.plan.limits,
        }


def plan_state(subscription: Subscription | None, now: datetime | None = None) -> PlanState:
    """What the workspace can do right now."""

    now = now or utcnow()
    if subscription is None:
        # Workspaces without a subscription row (should not happen) keep full access.
        return PlanState(PLANS["enterprise"], "active", False, ALL_FEATURES, None, None)

    plan = plan_for(subscription.plan)
    status_value = subscription.status
    days_left = None

    if subscription.plan == "trial":
        ends = subscription.trial_ends_at
        if ends is not None:
            remaining = (ends - now).total_seconds()
            days_left = max(0, int((remaining + 86399) // 86400))
            if remaining <= 0:
                status_value = "expired"
    elif subscription.cancel_at_period_end and subscription.current_period_end and subscription.current_period_end <= now:
        status_value = "canceled"

    read_only = status_value in {"expired", "canceled"}
    return PlanState(plan, status_value, read_only, plan.features, subscription.trial_ends_at, days_left)


async def get_subscription(db: AsyncSession, organization_id: uuid.UUID) -> Subscription | None:
    return (
        await db.execute(select(Subscription).where(Subscription.organization_id == organization_id))
    ).scalar_one_or_none()


async def active_seats(db: AsyncSession, organization_id: uuid.UUID) -> int:
    count = (
        await db.execute(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id, OrganizationMembership.status == "active")
        )
    ).scalar_one()
    return max(1, int(count))


def start_trial(organization_id: uuid.UUID, billing_email: str | None, now: datetime | None = None) -> Subscription:
    now = now or utcnow()
    return Subscription(
        id=uuid.uuid4(),
        organization_id=organization_id,
        plan="trial",
        status="trialing",
        trial_ends_at=now + timedelta(days=TRIAL_DAYS),
        current_period_start=now,
        current_period_end=now + timedelta(days=TRIAL_DAYS),
        seats=1,
        billing_email=billing_email,
        provider="demo",
    )


async def next_invoice_number(db: AsyncSession, organization_id: uuid.UUID, now: datetime) -> str:
    count = (
        await db.execute(select(func.count()).select_from(BillingInvoice).where(BillingInvoice.organization_id == organization_id))
    ).scalar_one()
    return f"AIVA-{now:%Y}-{count + 1:04d}"


async def record_invoice(
    db: AsyncSession,
    subscription: Subscription,
    *,
    plan: Plan,
    seats: int,
    period_start: datetime,
    period_end: datetime,
    provider: str = "demo",
    provider_ref: str | None = None,
    amount: Decimal | None = None,
    number: str | None = None,
    description: str | None = None,
) -> BillingInvoice:
    now = utcnow()
    total = amount if amount is not None else (plan.price * seats)
    invoice = BillingInvoice(
        id=uuid.uuid4(),
        organization_id=subscription.organization_id,
        number=number or await next_invoice_number(db, subscription.organization_id, now),
        plan=plan.key,
        seats=seats,
        unit_price=plan.price,
        amount=total,
        currency="USD",
        status="paid",
        description=description or f"AIVA {plan.name} · {seats} {'user' if seats == 1 else 'users'} · {period_start:%b %d} – {period_end:%b %d, %Y}",
        period_start=period_start,
        period_end=period_end,
        issued_at=now,
        paid_at=now,
        provider=provider,
        provider_ref=provider_ref,
    )
    db.add(invoice)
    return invoice


async def change_plan_demo(db: AsyncSession, subscription: Subscription, plan_key: str) -> BillingInvoice:
    """Switch plans without a payment provider (demo billing): applies at once."""

    if plan_key not in PAID_PLANS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose Starter, Pro or Enterprise.")
    plan = PLANS[plan_key]
    now = utcnow()
    seats = await active_seats(db, subscription.organization_id)
    subscription.plan = plan_key
    subscription.status = "active"
    subscription.seats = seats
    subscription.cancel_at_period_end = False
    subscription.current_period_start = now
    subscription.current_period_end = add_month(now)
    subscription.provider = "demo" if subscription.provider != "sandbox" else "sandbox"
    return await record_invoice(
        db, subscription, plan=plan, seats=seats, period_start=now, period_end=subscription.current_period_end
    )


async def enforce_limit(db: AsyncSession, organization_id: uuid.UUID, plan: Plan, key: str, model, *where) -> None:
    """Raise 402 when creating one more record would pass the plan's limit."""

    limit = plan.limit(key)
    if limit is None:
        return
    count = (
        await db.execute(select(func.count()).select_from(model).where(model.organization_id == organization_id, *where))
    ).scalar_one()
    if count >= limit:
        label = LIMIT_LABELS.get(key, key.replace("_", " "))
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"The {plan.name} plan includes up to {limit} {label}. Upgrade to Pro for unlimited {label}.",
        )
