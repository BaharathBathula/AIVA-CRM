"""Stripe: payment links on AIVA invoices, paid status back through a signed webhook.

The workspace pastes a secret key or a restricted key from its own Stripe
account (this is not AIVA's own billing, which uses ``STRIPE_SECRET_KEY``). AIVA
then creates a webhook endpoint in that account and keeps its signing secret
encrypted. Each invoice gets a Payment Link for its outstanding balance (one
completed checkout per link); the PaymentIntent carries the workspace and
invoice ids in its metadata. Webhooks are verified (``Stripe-Signature``,
5-minute tolerance) and every money change is re-read from the Stripe API
before AIVA applies it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _pay_common as pay
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.models.organization import Organization

KEY = "stripe"
API = "https://api.stripe.com/v1"
VERSION = "2024-06-20"
TOLERANCE_SECONDS = 300
KEY_PATTERN = re.compile(r"^(sk|rk)_(live|test)_[A-Za-z0-9]{10,}$")
EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "charge.refunded",
)


class StripeError(IntegrationError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None, http_status: int | None = None):
        super().__init__(message, status_code)
        self.code = code
        self.http_status = http_status


def _key(connection: IntegrationConnection) -> str:
    key = core.secret(connection, "secret_key")
    if not key:
        raise IntegrationError("Stripe isn't connected. Paste a secret or restricted key on the Stripe page.", 409)
    return key


def _flatten(data: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's form encoding: nested dicts and lists become a[b][0][c]=value."""

    out: list[tuple[str, str]] = []
    for name, value in data.items():
        field = f"{prefix}[{name}]" if prefix else str(name)
        if value is None:
            continue
        if isinstance(value, dict):
            out.extend(_flatten(value, field))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    out.extend(_flatten(item, f"{field}[{index}]"))
                else:
                    out.append((f"{field}[{index}]", str(item)))
        elif isinstance(value, bool):
            out.append((field, "true" if value else "false"))
        else:
            out.append((field, str(value)))
    return out


async def call(key: str, method: str, path: str, *, data: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
               connection: IntegrationConnection | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Stripe-Version": VERSION, "Accept": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = await pay.http(
        method, f"{API}{path}", headers=headers,
        data=_flatten(data) if data is not None else None,
        params=_flatten(params) if params else None,
    )
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if response.status_code >= 400:
        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
        message = error.get("message") or f"Stripe answered {response.status_code}."
        if response.status_code == 401:
            message = "Stripe didn't accept the key. Copy a current secret or restricted key from Developers → API keys."
            if connection is not None:
                connection.status = "error"
                connection.last_error = message
        elif response.status_code == 403:
            message = f"The restricted key isn't allowed to do this. {error.get('message') or ''}".strip()
        raise StripeError(f"Stripe: {message}" if not message.startswith("Stripe") else message, 502 if response.status_code >= 500 else 400,
                          error.get("code"), response.status_code)
    return body if isinstance(body, dict) else {}


def _mode(key: str) -> str:
    return "live" if "_live_" in key else "test"


def _dashboard(mode: str, kind: str, object_id: str) -> str:
    return f"https://dashboard.stripe.com/{'test/' if mode == 'test' else ''}{kind}/{object_id}"


def _public_https() -> bool:
    base = get_settings().app_base_url or ""
    host = (base.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]).lower()
    return base.startswith("https://") and host not in {"localhost", "127.0.0.1"} and not host.endswith(".localhost")


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


def sign(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def valid_signature(secret: str | None, body: bytes, header: str | None, now: float | None = None) -> bool:
    if not secret or not header:
        return False
    stamp = ""
    candidates: list[str] = []
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            stamp = value
        elif name == "v1" and value:
            candidates.append(value)
    if not (stamp.isascii() and stamp.isdigit()) or not candidates:
        return False
    if abs((now or time.time()) - int(stamp)) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest().encode()
    return any(hmac.compare_digest(expected, candidate.encode()) for candidate in candidates)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    secret_key = (values.get("secret_key") or "").strip()
    if secret_key.startswith("pk_"):
        raise IntegrationError("That's a publishable key. Paste a secret key (sk_…) or a restricted key (rk_…).", 422)
    if not KEY_PATTERN.match(secret_key):
        raise IntegrationError("Stripe keys start with sk_live_, sk_test_, rk_live_ or rk_test_.", 422)
    mode = _mode(secret_key)
    config: dict[str, Any] = {"mode": mode, "key_type": "restricted" if secret_key.startswith("rk_") else "secret"}
    label = f"Stripe ({mode})"
    external_id = None
    try:
        account = await call(secret_key, "GET", "/account")
        settings = account.get("settings") or {}
        name = (settings.get("dashboard") or {}).get("display_name") or (account.get("business_profile") or {}).get("name") or account.get("email")
        config.update(account_id=account.get("id"), account_name=name, default_currency=(account.get("default_currency") or "").upper() or None,
                      country=account.get("country"), charges_enabled=account.get("charges_enabled"))
        external_id = account.get("id")
        label = f"{name or 'Stripe account'} ({mode})"
    except StripeError as exc:
        if exc.http_status != 403:
            raise
        # Restricted keys often can't read the account; check one they need instead.
        await call(secret_key, "GET", "/payment_links", params={"limit": 1})
    return {"label": label, "external_id": external_id, "config": config}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await ensure_webhook(db, connection)


async def ensure_webhook(db: AsyncSession, connection: IntegrationConnection) -> str:
    """Create (or re-create) AIVA's webhook endpoint in the Stripe account."""

    secret_key = _key(connection)
    url = core.webhook_url(KEY, connection.webhook_token)
    config = dict(connection.config or {})
    if not url or not _public_https():
        connection.config = {**config, "webhook_endpoint_id": None, "webhook_mode": "polling"}
        core.log(db, connection.organization_id, KEY, "webhook_skipped",
                 "Stripe can only send webhooks to a public https address, so AIVA checks payment links every 30 minutes instead. Set APP_BASE_URL to your public address.",
                 connection=connection)
        return "polling"
    kept = None
    try:
        found = await call(secret_key, "GET", "/webhook_endpoints", params={"limit": 100}, connection=connection)
    except StripeError as exc:
        if exc.http_status != 403:
            raise
        connection.config = {**config, "webhook_endpoint_id": None, "webhook_mode": "polling"}
        core.log(db, connection.organization_id, KEY, "webhook_manual",
                 "This restricted key can't manage webhook endpoints, so AIVA checks payment links every 30 minutes. Give the key write access to Webhook Endpoints and set the webhook up again for instant updates.",
                 level="warning", connection=connection)
        return "polling"
    for endpoint in found.get("data") or []:
        if endpoint.get("url") != url:
            continue
        if endpoint.get("id") == config.get("webhook_endpoint_id") and core.secret(connection, "webhook_secret") and kept is None:
            kept = endpoint
            await call(secret_key, "POST", f"/webhook_endpoints/{endpoint['id']}", data={"enabled_events": list(EVENTS), "disabled": False}, connection=connection)
        else:
            await call(secret_key, "DELETE", f"/webhook_endpoints/{endpoint['id']}", connection=connection)
    if kept is None:
        created = await call(
            secret_key, "POST", "/webhook_endpoints", connection=connection,
            data={
                "url": url, "enabled_events": list(EVENTS), "api_version": VERSION,
                "description": "AIVA CRM: invoice payments", "metadata": {"aiva_organization_id": str(connection.organization_id)},
            },
        )
        core.set_secret(connection, "webhook_secret", created.get("secret"))
        kept = created
    connection.config = {**config, "webhook_endpoint_id": kept.get("id"), "webhook_mode": "webhook"}
    core.log(db, connection.organization_id, KEY, "webhook_ready", "Stripe will tell AIVA about payments and refunds as they happen.", level="success", connection=connection)
    return "webhook"


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    endpoint = (connection.config or {}).get("webhook_endpoint_id")
    secret_key = core.secret(connection, "secret_key")
    if endpoint and secret_key:
        try:
            await call(secret_key, "DELETE", f"/webhook_endpoints/{endpoint}")
        except IntegrationError:
            pass


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    secret_key = _key(connection)
    links = await call(secret_key, "GET", "/payment_links", params={"limit": 1}, connection=connection)
    config = connection.config or {}
    mode = config.get("mode") or _mode(secret_key)
    hooks = "webhooks on" if config.get("webhook_mode") == "webhook" else "checking every 30 minutes (no public webhook address)"
    return f"Stripe answered ({mode} mode, {'has' if links.get('data') else 'no'} payment links yet, {hooks})."


# ---------------------------------------------------------------------------
# Payment links
# ---------------------------------------------------------------------------


async def create_link(db: AsyncSession, connection: IntegrationConnection, invoice: SalesInvoice, amount: Decimal, *, user_id: uuid.UUID | None, message: str | None) -> IntegrationRecordLink:
    secret_key = _key(connection)
    old = await pay.link_for(db, invoice.organization_id, KEY, invoice.id)
    if old is not None and (old.data or {}).get("state") == "open":
        await deactivate_link(db, connection, old)
    organization = await db.get(Organization, invoice.organization_id)
    label = pay.invoice_label(invoice)
    metadata = {"aiva_organization_id": str(invoice.organization_id), "aiva_invoice_id": str(invoice.id), "aiva_invoice_number": label}
    currency = invoice.currency.lower()
    price = await call(
        secret_key, "POST", "/prices", connection=connection, idempotency_key=f"aiva-price-{invoice.id}-{uuid.uuid4().hex[:8]}",
        data={"currency": currency, "unit_amount": pay.to_minor(amount, currency), "product_data": {"name": f"Invoice {label}"[:250], "metadata": metadata}, "metadata": metadata},
    )
    body: dict[str, Any] = {
        "line_items": [{"price": price["id"], "quantity": 1}],
        "metadata": metadata,
        "payment_intent_data": {"metadata": metadata, "description": f"{(organization.name if organization else 'Invoice')} {label}"[:1000]},
        "restrictions": {"completed_sessions": {"limit": 1}},
        "after_completion": {"type": "hosted_confirmation", "hosted_confirmation": {"custom_message": (message or f"Thank you. Your payment for {label} is complete.")[:500]}},
        "submit_type": "pay",
    }
    link = await call(secret_key, "POST", "/payment_links", data=body, connection=connection, idempotency_key=f"aiva-link-{invoice.id}-{price['id']}")
    saved = await pay.save_link(db, invoice, KEY, external_id=link["id"], url=link.get("url") or "", amount=amount,
                                data={"price_id": price["id"], "mode": (connection.config or {}).get("mode")}, user_id=user_id)
    return saved


async def deactivate_link(db: AsyncSession, connection: IntegrationConnection, link: IntegrationRecordLink) -> None:
    try:
        await call(_key(connection), "POST", f"/payment_links/{link.external_id}", data={"active": False}, connection=connection)
    except StripeError as exc:
        if exc.http_status not in {400, 404}:
            raise
    link.data = {**(link.data or {}), "state": "closed"}


async def check_link(db: AsyncSession, connection: IntegrationConnection, link: IntegrationRecordLink) -> int:
    """Look for completed checkouts on a link (missed webhooks, or no public URL)."""

    secret_key = _key(connection)
    sessions = await call(secret_key, "GET", "/checkout/sessions", params={"payment_link": link.external_id, "limit": 10}, connection=connection)
    applied = 0
    for session in sessions.get("data") or []:
        if session.get("payment_status") == "paid" and session.get("payment_intent"):
            if await process_payment_intent(db, connection, str(session["payment_intent"])):
                applied += 1
    return applied


# ---------------------------------------------------------------------------
# Applying payments and refunds (always re-read from Stripe)
# ---------------------------------------------------------------------------


async def _invoice_from_metadata(db: AsyncSession, connection: IntegrationConnection, metadata: dict[str, Any]) -> SalesInvoice | None:
    if str(metadata.get("aiva_organization_id") or "") != str(connection.organization_id):
        return None
    try:
        invoice_id = uuid.UUID(str(metadata.get("aiva_invoice_id")))
    except ValueError:
        return None
    invoice = await db.get(SalesInvoice, invoice_id)
    return invoice if invoice is not None and invoice.organization_id == connection.organization_id else None


async def process_payment_intent(db: AsyncSession, connection: IntegrationConnection, payment_intent_id: str) -> bool:
    if not re.match(r"^pi_[A-Za-z0-9]+$", payment_intent_id or ""):
        return False
    intent = await call(_key(connection), "GET", f"/payment_intents/{payment_intent_id}", connection=connection)
    if intent.get("status") != "succeeded":
        return False
    invoice = await _invoice_from_metadata(db, connection, intent.get("metadata") or {})
    if invoice is None:
        return False
    currency = (intent.get("currency") or "").upper()
    mode = "live" if intent.get("livemode") else "test"
    applied = await pay.apply_payment(
        db, connection, invoice, external_id=intent["id"], amount=pay.from_minor(intent.get("amount_received") or 0, currency), currency=currency,
        occurred_at=pay.parse_time(intent.get("created")), method=(intent.get("payment_method_types") or ["card"])[0],
        email=intent.get("receipt_email"), url=_dashboard(mode, "payments", intent["id"]),
        data={"charge": intent.get("latest_charge"), "mode": mode},
    )
    return applied is not None


async def process_refunds(db: AsyncSession, connection: IntegrationConnection, charge_id: str) -> int:
    if not re.match(r"^(ch|py)_[A-Za-z0-9]+$", charge_id or ""):
        return 0
    refunds = await call(_key(connection), "GET", "/refunds", params={"charge": charge_id, "limit": 100}, connection=connection)
    applied = 0
    for refund in refunds.get("data") or []:
        if refund.get("status") != "succeeded" or not refund.get("payment_intent"):
            continue
        currency = (refund.get("currency") or "").upper()
        done = await pay.apply_refund(
            db, connection, external_id=refund["id"], payment_external_id=str(refund["payment_intent"]),
            amount=pay.from_minor(refund.get("amount") or 0, currency), currency=currency, occurred_at=pay.parse_time(refund.get("created")),
            reason=refund.get("reason"),
        )
        if done is not None:
            applied += 1
    return applied


async def refund(db: AsyncSession, connection: IntegrationConnection, payment: IntegrationObject, amount: Decimal, reason: str | None) -> str:
    currency = payment.currency or "USD"
    created = await call(
        _key(connection), "POST", "/refunds", connection=connection, idempotency_key=f"aiva-refund-{payment.id}-{pay.to_minor(amount, currency)}-{uuid.uuid4().hex[:6]}",
        data={"payment_intent": payment.external_id, "amount": pay.to_minor(amount, currency), "reason": "requested_by_customer",
              "metadata": {"aiva_note": (reason or "")[:400], "aiva_organization_id": str(connection.organization_id)}},
    )
    if created.get("status") == "succeeded":
        await pay.apply_refund(db, connection, external_id=created["id"], payment_external_id=payment.external_id, amount=amount, currency=currency, reason=reason)
        return f"Refunded {pay.fmt(amount, currency)} through Stripe."
    return f"Stripe is processing the {pay.fmt(amount, currency)} refund ({created.get('status')}); AIVA updates the invoice when it completes."


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method != "POST":
        return JSONResponse({"ok": True, "message": "AIVA receives Stripe events here (POST)."})
    body = await request.body()
    if len(body) > 1_000_000:
        return JSONResponse({"error": "too large"}, status_code=413)
    if not valid_signature(core.secret(connection, "webhook_secret"), body, request.headers.get("stripe-signature")):
        core.log(db, connection.organization_id, KEY, "webhook_rejected", "Rejected a request with a missing or wrong Stripe-Signature.", level="warning", connection=connection)
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    try:
        event = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    kind = event.get("type") or ""
    item = ((event.get("data") or {}).get("object")) or {}
    mode = (connection.config or {}).get("mode")
    if mode and bool(event.get("livemode")) != (mode == "live"):
        return JSONResponse({"ignored": "other mode"})
    connection.last_sync_at = pay.utcnow()
    if kind in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        if item.get("payment_status") == "paid" and item.get("payment_intent"):
            await process_payment_intent(db, connection, str(item["payment_intent"]))
    elif kind == "payment_intent.succeeded":
        await process_payment_intent(db, connection, str(item.get("id") or ""))
    elif kind in {"checkout.session.async_payment_failed", "payment_intent.payment_failed"}:
        invoice = await _invoice_from_metadata(db, connection, item.get("metadata") or {})
        if invoice is not None:
            reason = ((item.get("last_payment_error") or {}).get("message")) or "the payment didn't go through."
            await pay.mark_failed(db, connection, invoice, reason)
    elif kind == "charge.refunded":
        await process_refunds(db, connection, str(item.get("id") or ""))
    return JSONResponse({"received": True})
