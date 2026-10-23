"""Plan, seats, invoices and plan changes for the current workspace."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_permission
from app.core.config import get_settings
from app.core.plans import PAID_PLANS, PLANS, catalog
from app.db.session import get_db
from app.models.editions import BillingInvoice, Subscription
from app.models.organization import Organization
from app.services import stripe_billing
from app.services.automation import record_audit
from app.services.billing import (
    active_seats,
    add_month,
    change_plan_demo,
    get_subscription,
    plan_state,
    start_trial,
    utcnow,
)

router = APIRouter(prefix="/billing", tags=["Billing"])


class PlanChange(BaseModel):
    plan: str = Field(pattern="^(starter|pro|enterprise)$")


class BillingEmail(BaseModel):
    billing_email: str = Field(min_length=3, max_length=320)


def invoice_out(invoice: BillingInvoice) -> dict:
    return {
        "id": str(invoice.id),
        "number": invoice.number,
        "plan": invoice.plan,
        "plan_name": PLANS.get(invoice.plan, PLANS["starter"]).name,
        "seats": invoice.seats,
        "unit_price": float(invoice.unit_price),
        "amount": float(invoice.amount),
        "currency": invoice.currency,
        "status": invoice.status,
        "description": invoice.description,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "issued_at": invoice.issued_at,
        "paid_at": invoice.paid_at,
        "provider": invoice.provider,
    }


async def _subscription(db: AsyncSession, principal: Principal) -> Subscription:
    subscription = await get_subscription(db, principal.organization_id)
    if subscription is None:
        subscription = start_trial(principal.organization_id, principal.email)
        db.add(subscription)
        await db.flush()
    return subscription


async def billing_overview(db: AsyncSession, principal: Principal) -> dict:
    subscription = await _subscription(db, principal)
    state = plan_state(subscription)
    seats = await active_seats(db, principal.organization_id)
    invoices = (
        await db.execute(
            select(BillingInvoice)
            .where(BillingInvoice.organization_id == principal.organization_id)
            .order_by(BillingInvoice.issued_at.desc().nullslast(), BillingInvoice.created_at.desc())
            .limit(36)
        )
    ).scalars().all()
    plan = state.plan
    return {
        **state.to_dict(),
        "provider": subscription.provider,
        "stripe_enabled": stripe_billing.configured(),
        "billing_email": subscription.billing_email,
        "payment_method": subscription.payment_method,
        "seats_billed": subscription.seats,
        "seats_active": seats,
        "price_per_seat": float(plan.price),
        "monthly_total": float(plan.price * seats),
        "current_period_start": subscription.current_period_start,
        "current_period_end": subscription.current_period_end,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "can_manage": principal.can("admin.settings"),
        "invoices": [invoice_out(invoice) for invoice in invoices],
        "catalog": catalog(),
    }


@router.get("")
async def get_billing(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    data = await billing_overview(db, principal)
    await db.commit()
    return data


@router.post("/change-plan")
async def change_plan(
    payload: PlanChange,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    subscription = await _subscription(db, principal)
    if subscription.provider == "sandbox":
        raise HTTPException(status.HTTP_409_CONFLICT, "A sandbox uses its production workspace's plan.")

    if stripe_billing.configured():
        base = get_settings().app_base_url.rstrip("/")
        if subscription.provider == "stripe" and subscription.stripe_subscription_id and subscription.stripe_customer_id:
            # Already paying through Stripe: plan changes happen in the Stripe portal.
            url = await stripe_billing.create_portal_session(subscription.stripe_customer_id, f"{base}/settings/billing")
            return {"redirect_url": url}
        try:
            url = await stripe_billing.create_checkout_session(
                subscription=subscription,
                plan_key=payload.plan,
                seats=await active_seats(db, principal.organization_id),
                customer_email=subscription.billing_email or principal.email,
                success_url=f"{base}/settings/billing?checkout=success",
                cancel_url=f"{base}/settings/billing?checkout=canceled",
            )
        except stripe_billing.StripeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {exc}") from exc
        return {"redirect_url": url}

    previous = subscription.plan
    invoice = await change_plan_demo(db, subscription, payload.plan)
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="plan_change",
        entity_type="subscription",
        entity_id=subscription.id,
        entity_label=PLANS[payload.plan].name,
        changes={"plan": {"from": previous, "to": payload.plan}},
    )
    await db.commit()
    return {"redirect_url": None, "invoice": invoice_out(invoice), "billing": await billing_overview(db, principal)}


@router.post("/cancel")
async def cancel_plan(
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    subscription = await _subscription(db, principal)
    if subscription.plan == "trial":
        raise HTTPException(status.HTTP_409_CONFLICT, "Your trial ends on its own; there's nothing to cancel.")
    if subscription.provider == "stripe" and subscription.stripe_subscription_id:
        try:
            await stripe_billing._post(  # noqa: SLF001
                f"/subscriptions/{subscription.stripe_subscription_id}", {"cancel_at_period_end": "true"}
            )
        except stripe_billing.StripeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {exc}") from exc
    subscription.cancel_at_period_end = True
    if subscription.current_period_end is None:
        subscription.current_period_end = add_month(utcnow())
    await db.commit()
    return await billing_overview(db, principal)


@router.post("/resume")
async def resume_plan(
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    subscription = await _subscription(db, principal)
    if subscription.provider == "stripe" and subscription.stripe_subscription_id:
        try:
            await stripe_billing._post(  # noqa: SLF001
                f"/subscriptions/{subscription.stripe_subscription_id}", {"cancel_at_period_end": "false"}
            )
        except stripe_billing.StripeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {exc}") from exc
    subscription.cancel_at_period_end = False
    if subscription.status == "canceled":
        subscription.status = "active"
        subscription.current_period_start = utcnow()
        subscription.current_period_end = add_month(utcnow())
    await db.commit()
    return await billing_overview(db, principal)


@router.post("/sync-seats")
async def sync_seats(
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    subscription = await _subscription(db, principal)
    seats = await active_seats(db, principal.organization_id)
    if subscription.provider == "stripe" and subscription.stripe_subscription_id:
        try:
            await stripe_billing.update_seats(subscription, seats)
        except stripe_billing.StripeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {exc}") from exc
    subscription.seats = seats
    await db.commit()
    return await billing_overview(db, principal)


@router.post("/portal")
async def billing_portal(
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    subscription = await _subscription(db, principal)
    if not (stripe_billing.configured() and subscription.stripe_customer_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment details are managed in Stripe once you subscribe with a card.")
    base = get_settings().app_base_url.rstrip("/")
    try:
        url = await stripe_billing.create_portal_session(subscription.stripe_customer_id, f"{base}/settings/billing")
    except stripe_billing.StripeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {exc}") from exc
    return {"redirect_url": url}


@router.patch("/email")
async def set_billing_email(
    payload: BillingEmail,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    subscription = await _subscription(db, principal)
    subscription.billing_email = payload.billing_email.strip().lower()
    await db.commit()
    return await billing_overview(db, principal)


@router.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    invoice = await db.get(BillingInvoice, invoice_id)
    if invoice is None or invoice.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found.")
    organization = await db.get(Organization, principal.organization_id)
    subscription = await get_subscription(db, principal.organization_id)
    return {
        **invoice_out(invoice),
        "organization_name": organization.name if organization else "",
        "billing_email": subscription.billing_email if subscription else None,
    }


__all__ = ["router", "PAID_PLANS"]
