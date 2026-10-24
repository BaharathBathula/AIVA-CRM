"""Square: checkout links on AIVA invoices (Checkout API), payments synced back.

The workspace pastes an access token (production or sandbox). Each invoice gets
a quick-pay payment link for its balance at the chosen location; the link's
order id ties Square payments back to the invoice. Webhooks are signed with
``x-square-hmacsha256-signature`` = base64(HMAC-SHA256(signature key, the
notification URL + the raw body)); the URL is the one AIVA registered, built
from APP_BASE_URL. AIVA creates the webhook subscription itself when the token
allows it (an application's own access token), otherwise the admin adds the
URL in the Developer Console and pastes the signature key. Payments and
refunds are always re-read from the Square API before AIVA applies them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _pay_common as pay
from app.integrations.providers._pay_stripe import _public_https
from app.models.contact import Contact
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink

KEY = "square"
BASES = {"production": "https://connect.squareup.com", "sandbox": "https://connect.squareupsandbox.com"}
VERSION = "2025-01-23"
EVENTS = ("payment.created", "payment.updated", "refund.created", "refund.updated")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,192}$")


def environment(value: str | None) -> str:
    text = (value or "production").strip().lower()
    if text in {"production", "live", "prod"}:
        return "production"
    if text in {"sandbox", "test"}:
        return "sandbox"
    raise IntegrationError("Environment is production or sandbox.", 422)


async def _call(token: str, env: str, method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None,
                connection: IntegrationConnection | None = None) -> dict[str, Any]:
    response = await pay.http(
        method, f"{BASES[env]}{path}", json=json_body, params=params,
        headers={"Authorization": f"Bearer {token}", "Square-Version": VERSION, "Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if response.status_code >= 400:
        errors = body.get("errors") if isinstance(body, dict) else None
        first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        message = first.get("detail") or first.get("code") or f"Square answered {response.status_code}."
        if response.status_code == 401:
            message = "Square didn't accept the access token. Copy it again from the Developer Console (and check production vs sandbox)."
            if connection is not None:
                connection.status = "error"
                connection.last_error = message
        error = IntegrationError(f"Square: {message}"[:400] if not message.startswith("Square") else message, 502 if response.status_code >= 500 else 400)
        error.http_status = response.status_code  # type: ignore[attr-defined]
        raise error
    return body if isinstance(body, dict) else {}


async def call(connection: IntegrationConnection, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    token = core.secret(connection, "access_token")
    if not token:
        raise IntegrationError("Square isn't connected. Paste an access token on the Square page.", 409)
    return await _call(token, environment((connection.config or {}).get("environment")), method, path, connection=connection, **kwargs)


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


def signature_for(signature_key: str, url: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(signature_key.encode(), url.encode() + body, hashlib.sha256).digest()).decode()


def valid_signature(signature_key: str | None, url: str | None, body: bytes, header: str | None) -> bool:
    if not signature_key or not url or not header:
        return False
    return hmac.compare_digest(signature_for(signature_key, url, body).encode(), header.strip().encode())


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    token = (values.get("access_token") or "").strip()
    env = environment(values.get("environment"))
    if len(token) < 20:
        raise IntegrationError("Paste the full Square access token.", 422)
    merchant = (await _call(token, env, "GET", "/v2/merchants/me")).get("merchant") or {}
    location_id = (values.get("location_id") or "").strip()
    if location_id and not SAFE_ID.match(location_id):
        raise IntegrationError("That isn't a Square location ID.", 422)
    location = (await _call(token, env, "GET", f"/v2/locations/{location_id or 'main'}")).get("location") or {}
    if location.get("status") and location["status"] != "ACTIVE":
        raise IntegrationError(f"The Square location “{location.get('name')}” isn't active.", 400)
    if "CREDIT_CARD_PROCESSING" not in (location.get("capabilities") or ["CREDIT_CARD_PROCESSING"]):
        raise IntegrationError(f"The Square location “{location.get('name')}” can't take card payments yet.", 400)
    name = merchant.get("business_name") or "Square"
    return {
        "label": f"{name} · {location.get('name') or 'main location'}" + (" (sandbox)" if env == "sandbox" else ""),
        "external_id": merchant.get("id"),
        "config": {"environment": env, "merchant_id": merchant.get("id"), "business_name": name, "location_id": location.get("id"),
                   "location_name": location.get("name"), "currency": (location.get("currency") or merchant.get("currency") or "").upper() or None,
                   "country": location.get("country") or merchant.get("country")},
    }


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await ensure_webhook(db, connection)


async def ensure_webhook(db: AsyncSession, connection: IntegrationConnection) -> str:
    url = core.webhook_url(KEY, connection.webhook_token)
    config = dict(connection.config or {})
    if core.secret(connection, "webhook_signature_key") and not config.get("webhook_subscription_id"):
        connection.config = {**config, "webhook_mode": "webhook"}
        return "webhook"  # added by hand in the Developer Console
    if not url or not _public_https():
        connection.config = {**config, "webhook_mode": "polling"}
        core.log(db, connection.organization_id, KEY, "webhook_skipped",
                 "Square only sends webhooks to a public https address, so AIVA checks Square links every 30 minutes instead.", connection=connection)
        return "polling"
    try:
        listed = await call(connection, "GET", "/v2/webhooks/subscriptions", params={"include_disabled": "true"})
        existing = next((s for s in listed.get("subscriptions") or [] if s.get("notification_url") == url), None)
        if existing is not None:
            await call(connection, "PUT", f"/v2/webhooks/subscriptions/{existing['id']}", json_body={"subscription": {"event_types": list(EVENTS), "enabled": True}})
            rotated = await call(connection, "POST", f"/v2/webhooks/subscriptions/{existing['id']}/signature-key", json_body={"idempotency_key": uuid.uuid4().hex})
            subscription_id, signature_key = existing["id"], rotated.get("signature_key")
        else:
            created = (await call(connection, "POST", "/v2/webhooks/subscriptions", json_body={
                "idempotency_key": uuid.uuid4().hex,
                "subscription": {"name": "AIVA CRM", "event_types": list(EVENTS), "notification_url": url, "api_version": VERSION},
            })).get("subscription") or {}
            subscription_id, signature_key = created.get("id"), created.get("signature_key")
    except IntegrationError as exc:
        connection.config = {**config, "webhook_mode": "polling"}
        if getattr(exc, "http_status", 0) in {401, 403}:
            core.log(db, connection.organization_id, KEY, "webhook_manual",
                     "This token can't create webhook subscriptions. In the Square Developer Console add the webhook URL from this page (payment and refund events) and paste its signature key. Until then AIVA checks links every 30 minutes.",
                     level="warning", connection=connection)
            return "polling"
        raise
    if signature_key:
        core.set_secret(connection, "webhook_signature_key", signature_key)
    connection.config = {**config, "webhook_subscription_id": subscription_id, "webhook_mode": "webhook"}
    core.log(db, connection.organization_id, KEY, "webhook_ready", "Square will tell AIVA about payments and refunds as they happen.", level="success", connection=connection)
    return "webhook"


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    subscription_id = (connection.config or {}).get("webhook_subscription_id")
    if subscription_id:
        try:
            await call(connection, "DELETE", f"/v2/webhooks/subscriptions/{subscription_id}")
        except IntegrationError:
            pass


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    config = connection.config or {}
    location = (await call(connection, "GET", f"/v2/locations/{config.get('location_id') or 'main'}")).get("location") or {}
    hooks = "webhooks on" if config.get("webhook_mode") == "webhook" else "checking every 30 minutes"
    return f"Square answered: location “{location.get('name')}” is {str(location.get('status') or 'active').lower()} ({hooks})."


# ---------------------------------------------------------------------------
# Payment links
# ---------------------------------------------------------------------------


async def create_link(db: AsyncSession, connection: IntegrationConnection, invoice: SalesInvoice, amount: Decimal, *, user_id: uuid.UUID | None, message: str | None) -> IntegrationRecordLink:
    config = connection.config or {}
    currency = invoice.currency.upper()
    if config.get("currency") and config["currency"] != currency:
        raise IntegrationError(f"The Square location takes {config['currency']}, but this invoice is in {currency}.", 422)
    old = await pay.link_for(db, invoice.organization_id, KEY, invoice.id)
    if old is not None and (old.data or {}).get("state") == "open":
        await deactivate_link(db, connection, old)
    label = pay.invoice_label(invoice)
    body: dict[str, Any] = {
        "idempotency_key": f"aiva-{invoice.id}-{uuid.uuid4().hex[:12]}",
        "quick_pay": {"name": f"Invoice {label}"[:255], "price_money": {"amount": pay.to_minor(amount, currency), "currency": currency}, "location_id": config.get("location_id")},
        "payment_note": f"AIVA {label}"[:500],
        "checkout_options": {"allow_tipping": False, "ask_for_shipping_address": False},
        "description": (message or "")[:4096] or None,
    }
    contact = await db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    if contact is not None and contact.email:
        body["pre_populated_data"] = {"buyer_email": contact.email}
    created = (await call(connection, "POST", "/v2/online-checkout/payment-links", json_body={k: v for k, v in body.items() if v is not None})).get("payment_link") or {}
    if not created.get("id") or not str(created.get("url") or "").startswith("https://"):
        raise IntegrationError("Square didn't return a payment link.", 502)
    return await pay.save_link(db, invoice, KEY, external_id=created["id"], url=created["url"], amount=amount,
                               data={"order_id": created.get("order_id"), "environment": config.get("environment")}, user_id=user_id)


async def deactivate_link(db: AsyncSession, connection: IntegrationConnection, link: IntegrationRecordLink) -> None:
    try:
        await call(connection, "DELETE", f"/v2/online-checkout/payment-links/{link.external_id}")
    except IntegrationError as exc:
        if getattr(exc, "http_status", 0) != 404:
            raise
    link.data = {**(link.data or {}), "state": "closed"}


async def check_link(db: AsyncSession, connection: IntegrationConnection, link: IntegrationRecordLink) -> int:
    order_id = (link.data or {}).get("order_id")
    if not order_id or not SAFE_ID.match(str(order_id)):
        return 0
    order = (await call(connection, "GET", f"/v2/orders/{order_id}")).get("order") or {}
    applied = 0
    for tender in order.get("tenders") or []:
        if tender.get("payment_id") and await process_payment(db, connection, str(tender["payment_id"])):
            applied += 1
    return applied


async def _link_for_order(db: AsyncSession, organization_id: uuid.UUID, order_id: str) -> IntegrationRecordLink | None:
    return (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.provider == KEY,
                IntegrationRecordLink.external_type == pay.LINK, IntegrationRecordLink.data["order_id"].astext == order_id,
            ).limit(1)
        )
    ).scalars().first()


async def process_payment(db: AsyncSession, connection: IntegrationConnection, payment_id: str) -> bool:
    if not SAFE_ID.match(payment_id or ""):
        return False
    payment = (await call(connection, "GET", f"/v2/payments/{payment_id}")).get("payment") or {}
    if payment.get("status") != "COMPLETED" or not payment.get("order_id"):
        return False
    link = await _link_for_order(db, connection.organization_id, str(payment["order_id"]))
    if link is None:
        return False
    invoice = await db.get(SalesInvoice, link.record_id)
    if invoice is None or invoice.organization_id != connection.organization_id:
        return False
    amount_money = payment.get("amount_money") or {}
    currency = (amount_money.get("currency") or invoice.currency).upper()
    done = await pay.apply_payment(
        db, connection, invoice, external_id=payment["id"], amount=pay.from_minor(amount_money.get("amount") or 0, currency), currency=currency,
        occurred_at=pay.parse_time(payment.get("created_at")), method=(payment.get("source_type") or "card").lower(),
        email=payment.get("buyer_email_address"), url=payment.get("receipt_url") if str(payment.get("receipt_url") or "").startswith("https://") else None,
        data={"order_id": payment.get("order_id"), "location_id": payment.get("location_id")},
    )
    if done is not None and invoice.status == "paid":
        try:
            await deactivate_link(db, connection, link)
            link.data = {**(link.data or {}), "state": "paid"}
        except IntegrationError:
            pass
    return done is not None


async def process_refund(db: AsyncSession, connection: IntegrationConnection, refund_id: str) -> bool:
    if not SAFE_ID.match(refund_id or ""):
        return False
    found = (await call(connection, "GET", f"/v2/refunds/{refund_id}")).get("refund") or {}
    if found.get("status") != "COMPLETED" or not found.get("payment_id"):
        return False
    amount_money = found.get("amount_money") or {}
    currency = (amount_money.get("currency") or "USD").upper()
    done = await pay.apply_refund(
        db, connection, external_id=found["id"], payment_external_id=str(found["payment_id"]), amount=pay.from_minor(amount_money.get("amount") or 0, currency),
        currency=currency, occurred_at=pay.parse_time(found.get("created_at")), reason=found.get("reason"),
    )
    return done is not None


async def refund(db: AsyncSession, connection: IntegrationConnection, payment: IntegrationObject, amount: Decimal, reason: str | None) -> str:
    currency = payment.currency or "USD"
    created = (await call(connection, "POST", "/v2/refunds", json_body={
        "idempotency_key": f"aiva-refund-{payment.id}-{uuid.uuid4().hex[:10]}", "payment_id": payment.external_id,
        "amount_money": {"amount": pay.to_minor(amount, currency), "currency": currency}, "reason": (reason or "Refund from AIVA")[:192],
    })).get("refund") or {}
    if created.get("status") == "COMPLETED" and created.get("id"):
        await pay.apply_refund(db, connection, external_id=created["id"], payment_external_id=payment.external_id, amount=amount, currency=currency, reason=reason)
        return f"Refunded {pay.fmt(amount, currency)} through Square."
    return f"Square is processing the {pay.fmt(amount, currency)} refund ({str(created.get('status') or 'pending').lower()})."


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method != "POST":
        return JSONResponse({"ok": True, "message": "AIVA receives Square events here (POST)."})
    body = await request.body()
    if len(body) > 1_000_000:
        return JSONResponse({"error": "too large"}, status_code=413)
    url = core.webhook_url(KEY, connection.webhook_token)
    if not valid_signature(core.secret(connection, "webhook_signature_key"), url, body, request.headers.get("x-square-hmacsha256-signature")):
        core.log(db, connection.organization_id, KEY, "webhook_rejected",
                 "Rejected a request with a missing or wrong Square signature. Check the signature key and that APP_BASE_URL matches the address Square calls.",
                 level="warning", connection=connection, detail={"expected_url": url})
        return JSONResponse({"error": "invalid signature"}, status_code=403)
    try:
        event = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    merchant = (connection.config or {}).get("merchant_id")
    if merchant and event.get("merchant_id") and event["merchant_id"] != merchant:
        return JSONResponse({"ignored": "other merchant"})
    kind = str(event.get("type") or "")
    data = (event.get("data") or {})
    object_id = str(data.get("id") or "")
    if kind.startswith("payment."):
        await process_payment(db, connection, object_id or str(((data.get("object") or {}).get("payment") or {}).get("id") or ""))
    elif kind.startswith("refund."):
        await process_refund(db, connection, object_id or str(((data.get("object") or {}).get("refund") or {}).get("id") or ""))
    connection.last_sync_at = pay.utcnow()
    return JSONResponse({"received": True})
