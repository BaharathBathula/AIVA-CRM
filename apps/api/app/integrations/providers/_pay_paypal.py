"""PayPal: AIVA invoices paid through PayPal invoices (Invoicing API v2).

Why invoices, not Orders: an Orders approval link expires within hours and only
captures if the buyer comes back to AIVA; a PayPal invoice has a payer link that
never expires, works with PayPal, cards, Venmo and Pay Later, keeps its own
payment and refund history, and PayPal tells us about every change. Each AIVA
invoice gets one PayPal invoice (sent "share link only", so PayPal doesn't email
anyone unless asked) for the balance due.

The workspace pastes its REST app's client ID and secret. Access tokens (client
credentials) are cached encrypted on the connection until they expire. AIVA
registers its webhook in the app; every event is checked with PayPal's
``verify-webhook-signature`` before AIVA re-reads the PayPal invoice and applies
its PayPal payments and refunds (idempotent by PayPal transaction ids).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _pay_common as pay
from app.integrations.providers._pay_stripe import _public_https
from app.models.contact import Contact
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.models.organization import Organization

KEY = "paypal"
BASES = {"live": "https://api-m.paypal.com", "sandbox": "https://api-m.sandbox.paypal.com"}
EVENTS = ("INVOICING.INVOICE.PAID", "INVOICING.INVOICE.REFUNDED", "INVOICING.INVOICE.CANCELLED", "INVOICING.INVOICE.UPDATED")
INVOICING_SCOPE = "https://uri.paypal.com/services/invoicing"
NO_DECIMALS = {"HUF", "JPY", "TWD"}
SUPPORTED = {"AUD", "BRL", "CAD", "CNY", "CZK", "DKK", "EUR", "HKD", "HUF", "ILS", "JPY", "MYR", "MXN", "TWD", "NZD", "NOK", "PHP", "PLN", "GBP", "SGD", "SEK", "CHF", "THB", "USD"}
INVOICE_ID = re.compile(r"^INV2-[A-Z0-9-]{4,60}$")


def environment(value: str | None) -> str:
    text = (value or "live").strip().lower()
    if text in {"live", "production", "prod"}:
        return "live"
    if text in {"sandbox", "test"}:
        return "sandbox"
    raise IntegrationError("Environment is live or sandbox.", 422)


def _base(connection: IntegrationConnection) -> str:
    return BASES[environment((connection.config or {}).get("environment"))]


def _value(amount: Decimal, currency: str) -> str:
    return pay.decimal_value(amount, currency, 0 if currency.upper() in NO_DECIMALS else 2)


async def _token_for(base: str, client_id: str, client_secret: str) -> dict[str, Any]:
    response = await pay.http(
        "POST", f"{base}/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or not body.get("access_token"):
        if response.status_code in {400, 401}:
            raise IntegrationError("PayPal didn't accept the client ID and secret. Copy them from your REST app (and check live vs sandbox).", 400)
        raise IntegrationError(f"PayPal answered {response.status_code}.", 502)
    return body


async def _token(db: AsyncSession, connection: IntegrationConnection) -> str:
    if connection.access_token and connection.token_expires_at and connection.token_expires_at - timedelta(minutes=5) > pay.utcnow():
        token = decrypt(connection.access_token)
        if token:
            return token
    client_id = (connection.config or {}).get("client_id") or ""
    client_secret = core.secret(connection, "client_secret") or ""
    if not client_id or not client_secret:
        raise IntegrationError("PayPal isn't connected. Paste the REST app's client ID and secret.", 409)
    body = await _token_for(_base(connection), client_id, client_secret)
    connection.access_token = encrypt(body["access_token"])
    connection.token_expires_at = pay.utcnow() + timedelta(seconds=int(body.get("expires_in") or 3000))
    return body["access_token"]


async def call(db: AsyncSession, connection: IntegrationConnection, method: str, path: str, *, json_body: Any = None,
               params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    token = await _token(db, connection)
    response = await pay.http(
        method, f"{_base(connection)}{path}", json=json_body, params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
    )
    if response.status_code == 401:
        connection.access_token = None
        connection.token_expires_at = None
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if response.status_code >= 400:
        details = body.get("details") if isinstance(body, dict) else None
        issue = (details[0].get("description") or details[0].get("issue")) if isinstance(details, list) and details and isinstance(details[0], dict) else None
        message = issue or (body.get("message") if isinstance(body, dict) else None) or f"PayPal answered {response.status_code}."
        error = IntegrationError(f"PayPal: {message}"[:400], 502 if response.status_code >= 500 else 400)
        error.name = body.get("name") if isinstance(body, dict) else None  # type: ignore[attr-defined]
        error.http_status = response.status_code  # type: ignore[attr-defined]
        raise error
    return body if isinstance(body, dict) else {"items": body}


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    env = environment(values.get("environment"))
    client_id = (values.get("client_id") or "").strip()
    client_secret = (values.get("client_secret") or "").strip()
    if len(client_id) < 20 or len(client_secret) < 20:
        raise IntegrationError("Paste the full client ID and secret from your PayPal REST app.", 422)
    body = await _token_for(BASES[env], client_id, client_secret)
    scopes = str(body.get("scope") or "").split()
    if INVOICING_SCOPE not in scopes:
        raise IntegrationError("This PayPal app can't create invoices. In the PayPal developer dashboard, open the app and turn on Invoicing under Features.", 400)
    return {"label": f"PayPal ({env})", "external_id": body.get("app_id") or client_id[:24], "config": {"environment": env, "app_id": body.get("app_id")}}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    connection.access_token = None
    connection.token_expires_at = None
    await ensure_webhook(db, connection)


async def ensure_webhook(db: AsyncSession, connection: IntegrationConnection) -> str:
    url = core.webhook_url(KEY, connection.webhook_token)
    if not url or not _public_https():
        connection.config = {**(connection.config or {}), "webhook_id": None, "webhook_mode": "polling"}
        core.log(db, connection.organization_id, KEY, "webhook_skipped",
                 "PayPal only sends webhooks to a public https address, so AIVA checks PayPal invoices every 30 minutes instead.", connection=connection)
        return "polling"
    events = [{"name": name} for name in EVENTS]
    try:
        created = await call(db, connection, "POST", "/v1/notifications/webhooks", json_body={"url": url, "event_types": events})
        webhook_id = created.get("id")
    except IntegrationError as exc:
        if getattr(exc, "name", None) != "WEBHOOK_URL_ALREADY_EXISTS":
            raise
        listed = await call(db, connection, "GET", "/v1/notifications/webhooks")
        match = next((w for w in listed.get("webhooks") or [] if w.get("url") == url), None)
        if match is None:
            raise
        webhook_id = match.get("id")
        await call(db, connection, "PATCH", f"/v1/notifications/webhooks/{webhook_id}", json_body=[{"op": "replace", "path": "/event_types", "value": events}])
    connection.config = {**(connection.config or {}), "webhook_id": webhook_id, "webhook_mode": "webhook"}
    core.log(db, connection.organization_id, KEY, "webhook_ready", "PayPal will tell AIVA when invoices are paid or refunded.", level="success", connection=connection)
    return "webhook"


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    webhook_id = (connection.config or {}).get("webhook_id")
    if webhook_id:
        try:
            await call(db, connection, "DELETE", f"/v1/notifications/webhooks/{webhook_id}")
        except IntegrationError:
            pass


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    connection.access_token = None
    await _token(db, connection)
    env = environment((connection.config or {}).get("environment"))
    hooks = "webhook registered" if (connection.config or {}).get("webhook_id") else "checking every 30 minutes"
    return f"PayPal accepted the app's credentials ({env}, {hooks})."


# ---------------------------------------------------------------------------
# Payer links (PayPal invoices)
# ---------------------------------------------------------------------------


async def create_link(db: AsyncSession, connection: IntegrationConnection, invoice: SalesInvoice, amount: Decimal, *, user_id: uuid.UUID | None, message: str | None) -> IntegrationRecordLink:
    currency = invoice.currency.upper()
    if currency not in SUPPORTED:
        raise IntegrationError(f"PayPal doesn't take payments in {currency}.", 422)
    old = await pay.link_for(db, invoice.organization_id, KEY, invoice.id)
    if old is not None and (old.data or {}).get("state") == "open":
        await deactivate_link(db, connection, old)
    organization = await db.get(Organization, invoice.organization_id)
    label = pay.invoice_label(invoice)
    contact = await db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    detail: dict[str, Any] = {
        "currency_code": currency,
        "invoice_number": f"{label}-{uuid.uuid4().hex[:6].upper()}",
        "reference": str(invoice.id),
        "invoice_date": date.today().isoformat(),
        "memo": "Created by AIVA CRM",
        "note": (message or f"{organization.name if organization else ''} invoice {label}").strip()[:4000],
    }
    if invoice.due_date and invoice.due_date >= date.today():
        detail["payment_term"] = {"due_date": invoice.due_date.isoformat()}
    body: dict[str, Any] = {
        "detail": detail,
        "items": [{"name": f"Invoice {label}"[:200], "quantity": "1", "unit_amount": {"currency_code": currency, "value": _value(amount, currency)}}],
        "configuration": {"allow_tip": False, "partial_payment": {"allow_partial_payment": False}},
    }
    if contact is not None and contact.email:
        body["primary_recipients"] = [{"billing_info": {"email_address": contact.email, "name": {"given_name": (contact.first_name or "")[:140], "surname": (contact.last_name or "")[:140]}}}]
    created = await call(db, connection, "POST", "/v2/invoicing/invoices", json_body=body, headers={"Prefer": "return=representation", "PayPal-Request-Id": f"aiva-{invoice.id}-{uuid.uuid4().hex[:10]}"})
    paypal_id = created.get("id") or ""
    if not paypal_id:
        href = next((link.get("href") for link in created.get("links") or [] if link.get("rel") == "self"), "") or created.get("href") or ""
        paypal_id = href.rstrip("/").rsplit("/", 1)[-1]
    if not INVOICE_ID.match(paypal_id):
        raise IntegrationError("PayPal didn't return the new invoice.", 502)
    sent = await call(db, connection, "POST", f"/v2/invoicing/invoices/{paypal_id}/send", json_body={"send_to_invoicer": False, "send_to_recipient": False})
    url = sent.get("href") if str(sent.get("href") or "").startswith("https://") else None
    if not url:
        fetched = await call(db, connection, "GET", f"/v2/invoicing/invoices/{paypal_id}")
        url = ((fetched.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
    if not url or not str(url).startswith("https://"):
        raise IntegrationError("PayPal created the invoice but didn't give a payment link.", 502)
    return await pay.save_link(db, invoice, KEY, external_id=paypal_id, url=url, amount=amount,
                               data={"environment": environment((connection.config or {}).get("environment")), "invoice_number": detail["invoice_number"]}, user_id=user_id)


async def deactivate_link(db: AsyncSession, connection: IntegrationConnection, link: IntegrationRecordLink) -> None:
    try:
        await call(db, connection, "POST", f"/v2/invoicing/invoices/{link.external_id}/cancel",
                   json_body={"send_to_invoicer": False, "send_to_recipient": False, "subject": "Replaced", "note": "This invoice was replaced."})
    except IntegrationError as exc:
        if getattr(exc, "http_status", 0) not in {404, 422}:
            raise
    link.data = {**(link.data or {}), "state": "closed"}


async def check_link(db: AsyncSession, connection: IntegrationConnection, link: IntegrationRecordLink) -> int:
    return await sync_invoice(db, connection, link)


async def sync_invoice(db: AsyncSession, connection: IntegrationConnection, link: IntegrationRecordLink) -> int:
    """Re-read the PayPal invoice and apply its PayPal payments and refunds."""

    if not INVOICE_ID.match(link.external_id or ""):
        return 0
    invoice = await db.get(SalesInvoice, link.record_id)
    if invoice is None or invoice.organization_id != connection.organization_id:
        return 0
    found = await call(db, connection, "GET", f"/v2/invoicing/invoices/{link.external_id}")
    status = found.get("status") or ""
    link.data = {**(link.data or {}), "paypal_status": status}
    if status == "CANCELLED" and (link.data or {}).get("state") == "open":
        link.data = {**link.data, "state": "closed"}
    env = environment((connection.config or {}).get("environment"))
    view = f"https://www.{'sandbox.' if env == 'sandbox' else ''}paypal.com/invoice/details/{link.external_id}"
    applied = 0
    for txn in ((found.get("payments") or {}).get("transactions") or []):
        if txn.get("type") != "PAYPAL" or not txn.get("payment_id"):
            continue  # payments recorded by hand in PayPal aren't money PayPal took
        money_info = txn.get("amount") or {}
        done = await pay.apply_payment(
            db, connection, invoice, external_id=str(txn["payment_id"]), amount=Decimal(str(money_info.get("value") or "0")),
            currency=money_info.get("currency_code") or invoice.currency, occurred_at=pay.parse_time(txn.get("payment_date")),
            method=(txn.get("method") or "PAYPAL").lower(), url=view, data={"paypal_invoice_id": link.external_id},
        )
        applied += 1 if done is not None else 0
    refunds = (found.get("refunds") or {})
    paypal_refunded = Decimal(str((refunds.get("refund_amount") or {}).get("value") or "0"))
    payments = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == connection.organization_id, IntegrationObject.provider == KEY,
                IntegrationObject.object_type == "payment", IntegrationObject.record_id == invoice.id,
            ).order_by(IntegrationObject.occurred_at)
        )
    ).scalars().all()
    recorded = sum((pay.money((p.data or {}).get("refunded") or 0) for p in payments), Decimal("0"))
    for txn in refunds.get("transactions") or []:
        if recorded >= paypal_refunded or txn.get("type") != "PAYPAL" or not txn.get("refund_id"):
            continue
        amount = Decimal(str((txn.get("amount") or {}).get("value") or "0"))
        target = next((p for p in payments if pay.money(p.amount or 0) - pay.money((p.data or {}).get("refunded") or 0) >= amount), payments[0] if payments else None)
        if target is None:
            break
        done = await pay.apply_refund(
            db, connection, external_id=str(txn["refund_id"]), payment_external_id=target.external_id, amount=amount,
            currency=(txn.get("amount") or {}).get("currency_code") or invoice.currency, occurred_at=pay.parse_time(txn.get("refund_date")),
        )
        if done is not None:
            recorded += amount
            applied += 1
    link.last_synced_at = pay.utcnow()
    return applied


async def refund(db: AsyncSession, connection: IntegrationConnection, payment: IntegrationObject, amount: Decimal, reason: str | None) -> str:
    currency = payment.currency or "USD"
    result = await call(
        db, connection, "POST", f"/v2/payments/captures/{payment.external_id}/refund",
        json_body={"amount": {"value": _value(amount, currency), "currency_code": currency}, "note_to_payer": (reason or "Refund")[:255]},
        headers={"PayPal-Request-Id": f"aiva-refund-{payment.id}-{uuid.uuid4().hex[:8]}"},
    )
    if result.get("status") == "COMPLETED" and result.get("id"):
        await pay.apply_refund(db, connection, external_id=str(result["id"]), payment_external_id=payment.external_id, amount=amount, currency=currency, reason=reason)
        return f"Refunded {pay.fmt(amount, currency)} through PayPal."
    return f"PayPal is processing the {pay.fmt(amount, currency)} refund ({result.get('status') or 'pending'})."


async def verify(db: AsyncSession, connection: IntegrationConnection, request: Request, event: dict[str, Any]) -> bool:
    webhook_id = (connection.config or {}).get("webhook_id")
    headers = {name: request.headers.get(f"paypal-{name.replace('_', '-')}") for name in ("auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time")}
    if not webhook_id or not all(headers.values()):
        return False
    result = await call(db, connection, "POST", "/v1/notifications/verify-webhook-signature", json_body={**headers, "webhook_id": webhook_id, "webhook_event": event})
    return result.get("verification_status") == "SUCCESS"


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method != "POST":
        return JSONResponse({"ok": True, "message": "AIVA receives PayPal events here (POST)."})
    body = await request.body()
    if len(body) > 1_000_000:
        return JSONResponse({"error": "too large"}, status_code=413)
    try:
        event = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(event, dict) or not await verify(db, connection, request, event):
        core.log(db, connection.organization_id, KEY, "webhook_rejected", "Rejected a request PayPal couldn't verify.", level="warning", connection=connection)
        return JSONResponse({"error": "not verified"}, status_code=400)
    resource = event.get("resource") or {}
    paypal_id = str(resource.get("id") or (resource.get("invoice") or {}).get("id") or "")
    if str(event.get("event_type") or "").startswith("INVOICING.") and INVOICE_ID.match(paypal_id):
        link = await pay.link_by_external(db, connection.organization_id, KEY, paypal_id)
        if link is not None:
            await sync_invoice(db, connection, link)
    connection.last_sync_at = pay.utcnow()
    return JSONResponse({"received": True})
