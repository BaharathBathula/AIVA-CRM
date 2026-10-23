"""Stripe Checkout and webhooks for paid plans.

Only used when ``STRIPE_SECRET_KEY`` is set. Prices are sent inline
(``price_data``) so no products need to be created in the Stripe dashboard
first. Webhook signatures are verified with ``STRIPE_WEBHOOK_SECRET``.
"""

from __future__ import annotations

import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.plans import PAID_PLANS, PLANS
from app.models.editions import BillingInvoice, Subscription
from app.services.billing import record_invoice

API = "https://api.stripe.com/v1"


class StripeError(Exception):
    pass


def configured() -> bool:
    return bool(get_settings().stripe_secret_key)


async def _post(path: str, data: dict[str, Any]) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{API}{path}", data=data, auth=(settings.stripe_secret_key, ""))
    body = response.json() if response.content else {}
    if response.status_code >= 400:
        message = (body.get("error") or {}).get("message") or "Stripe rejected the request."
        raise StripeError(message)
    return body


async def create_checkout_session(
    *,
    subscription: Subscription,
    plan_key: str,
    seats: int,
    customer_email: str | None,
    success_url: str,
    cancel_url: str,
) -> str:
    plan = PLANS[plan_key]
    data: dict[str, Any] = {
        "mode": "subscription",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(subscription.organization_id),
        "line_items[0][quantity]": str(seats),
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(int(plan.price * 100)),
        "line_items[0][price_data][recurring][interval]": "month",
        "line_items[0][price_data][product_data][name]": f"AIVA CRM {plan.name}",
        "metadata[organization_id]": str(subscription.organization_id),
        "metadata[plan]": plan_key,
        "subscription_data[metadata][organization_id]": str(subscription.organization_id),
        "subscription_data[metadata][plan]": plan_key,
        "allow_promotion_codes": "true",
    }
    if subscription.stripe_customer_id:
        data["customer"] = subscription.stripe_customer_id
    elif customer_email:
        data["customer_email"] = customer_email
    session = await _post("/checkout/sessions", data)
    return session["url"]


async def create_portal_session(customer_id: str, return_url: str) -> str:
    session = await _post("/billing_portal/sessions", {"customer": customer_id, "return_url": return_url})
    return session["url"]


async def update_seats(subscription: Subscription, seats: int) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{API}/subscriptions/{subscription.stripe_subscription_id}", auth=(settings.stripe_secret_key, "")
        )
    if response.status_code >= 400:
        raise StripeError("Couldn't load the Stripe subscription.")
    item_id = response.json()["items"]["data"][0]["id"]
    await _post(
        f"/subscriptions/{subscription.stripe_subscription_id}",
        {"items[0][id]": item_id, "items[0][quantity]": str(seats), "proration_behavior": "create_prorations"},
    )


def verify_webhook(payload: bytes, signature_header: str | None, secret: str, tolerance: int = 300) -> dict:
    """Check the Stripe-Signature header and return the parsed event."""

    if not signature_header or not secret:
        raise StripeError("Missing signature.")
    parts: dict[str, list[str]] = {}
    for item in signature_header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts.get("t", ["0"])[0])
    except ValueError as exc:
        raise StripeError("Bad signature timestamp.") from exc
    if abs(time.time() - timestamp) > tolerance:
        raise StripeError("Signature is too old.")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", [])):
        raise StripeError("Signature doesn't match.")
    return json.loads(payload)


STATUS_MAP = {
    "active": "active",
    "trialing": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "canceled": "canceled",
    "incomplete_expired": "canceled",
}


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None


async def _subscription_for(db: AsyncSession, organization_id: str | None, stripe_subscription_id: str | None) -> Subscription | None:
    if organization_id:
        try:
            org = uuid.UUID(organization_id)
        except ValueError:
            org = None
        if org:
            found = (await db.execute(select(Subscription).where(Subscription.organization_id == org))).scalar_one_or_none()
            if found:
                return found
    if stripe_subscription_id:
        return (
            await db.execute(select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id))
        ).scalar_one_or_none()
    return None


async def handle_event(db: AsyncSession, event: dict) -> str:
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    metadata = obj.get("metadata") or {}

    if kind == "checkout.session.completed":
        subscription = await _subscription_for(db, obj.get("client_reference_id") or metadata.get("organization_id"), None)
        if subscription is None:
            return "ignored: unknown workspace"
        plan_key = metadata.get("plan")
        if plan_key in PAID_PLANS:
            subscription.plan = plan_key
        subscription.status = "active"
        subscription.provider = "stripe"
        subscription.stripe_customer_id = obj.get("customer") or subscription.stripe_customer_id
        subscription.stripe_subscription_id = obj.get("subscription") or subscription.stripe_subscription_id
        subscription.cancel_at_period_end = False
        return "subscription activated"

    if kind in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        subscription = await _subscription_for(db, metadata.get("organization_id"), obj.get("id"))
        if subscription is None:
            return "ignored: unknown subscription"
        subscription.provider = "stripe"
        subscription.stripe_subscription_id = obj.get("id")
        subscription.stripe_customer_id = obj.get("customer") or subscription.stripe_customer_id
        subscription.status = "canceled" if kind.endswith("deleted") else STATUS_MAP.get(obj.get("status", ""), "active")
        subscription.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
        items = (obj.get("items") or {}).get("data") or []
        if items:
            subscription.seats = int(items[0].get("quantity") or subscription.seats)
            subscription.current_period_start = _ts(items[0].get("current_period_start")) or subscription.current_period_start
            subscription.current_period_end = _ts(items[0].get("current_period_end")) or subscription.current_period_end
        subscription.current_period_start = _ts(obj.get("current_period_start")) or subscription.current_period_start
        subscription.current_period_end = _ts(obj.get("current_period_end")) or subscription.current_period_end
        if metadata.get("plan") in PAID_PLANS:
            subscription.plan = metadata["plan"]
        return f"subscription {subscription.status}"

    if kind == "invoice.paid":
        sub_id = obj.get("subscription") or ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
        subscription = await _subscription_for(db, None, sub_id)
        if subscription is None:
            return "ignored: unknown subscription"
        existing = (
            await db.execute(select(BillingInvoice).where(BillingInvoice.provider_ref == obj.get("id")))
        ).scalar_one_or_none()
        if existing:
            return "duplicate invoice"
        plan = PLANS.get(subscription.plan, PLANS["starter"])
        lines = (obj.get("lines") or {}).get("data") or [{}]
        period = lines[0].get("period") or {}
        await record_invoice(
            db,
            subscription,
            plan=plan,
            seats=subscription.seats,
            period_start=_ts(period.get("start")) or datetime.now(timezone.utc),
            period_end=_ts(period.get("end")) or datetime.now(timezone.utc),
            provider="stripe",
            provider_ref=obj.get("id"),
            amount=Decimal(obj.get("amount_paid") or 0) / 100,
            number=obj.get("number"),
        )
        if subscription.status == "past_due":
            subscription.status = "active"
        return "invoice recorded"

    if kind == "invoice.payment_failed":
        subscription = await _subscription_for(db, None, obj.get("subscription"))
        if subscription is not None:
            subscription.status = "past_due"
        return "payment failed"

    return "ignored"
