"""Shared plumbing for the payment integrations (Stripe, PayPal, Square, Chargebee).

* ``http``: the one place provider APIs are called (tests stand in for it).
* Money: amounts are ``Decimal``; providers get minor units for the currency's
  exponent (JPY has none, KWD has three), never floats.
* ``apply_payment`` / ``apply_refund`` change an AIVA invoice exactly once per
  provider payment or refund id, record it as an ``IntegrationObject``, put it
  on the account's timeline and tell the invoice's owner.
* Payment links live in ``IntegrationRecordLink`` rows (``record_type`` =
  ``sales_invoice``, ``external_type`` = ``payment_link``), one per processor
  per invoice.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.models.platform import Notification
from app.services.automation import record_audit

logger = logging.getLogger("aiva.integrations.payments")

INVOICE = "sales_invoice"
LINK = "payment_link"
NAMES = {"stripe": "Stripe", "paypal": "PayPal", "square": "Square", "chargebee": "Chargebee"}
PROCESSORS = ("stripe", "paypal", "square")  # the ones that take invoice payments

# Currencies with no minor unit, or three digits of it (ISO 4217, as the processors treat them).
ZERO_DECIMAL = {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}
THREE_DECIMAL = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
VENDOR_HOSTS = {
    "stripe.com": "Stripe",
    "paypal.com": "PayPal",
    "squareup.com": "Square",
    "squareupsandbox.com": "Square",
    "chargebee.com": "Chargebee",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


async def http(method: str, url: str, *, timeout: float = 25, **kwargs: Any) -> httpx.Response:
    """Call a payment provider. Network failures become a friendly IntegrationError."""

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            return await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        host = (urlsplit(url).hostname or "").lower()
        vendor = next((name for suffix, name in VENDOR_HOSTS.items() if host == suffix or host.endswith("." + suffix)), "the payment provider")
        logger.warning("Network error calling %s: %r", host, exc)
        raise IntegrationError(f"Couldn't reach {vendor}. Try again in a moment.", 502) from exc


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise IntegrationError("That amount isn't a number.", 422) from exc


def exponent(currency: str) -> int:
    code = (currency or "").upper()
    if code in ZERO_DECIMAL:
        return 0
    if code in THREE_DECIMAL:
        return 3
    return 2


def to_minor(amount: Decimal, currency: str) -> int:
    """Decimal amount → integer minor units (cents, yen, fils)."""

    places = exponent(currency)
    quantum = Decimal(1).scaleb(-places)
    return int((Decimal(amount).quantize(quantum, rounding=ROUND_HALF_UP) * (Decimal(10) ** places)).to_integral_value())


def from_minor(value: Any, currency: str) -> Decimal:
    places = exponent(currency)
    return (Decimal(int(value or 0)) / (Decimal(10) ** places)).quantize(Decimal("0.01") if places <= 2 else Decimal("0.001"), rounding=ROUND_HALF_UP)


def decimal_value(value: Any, currency: str, places: int | None = None) -> str:
    """A decimal string with the currency's number of places ("12.50", "1250" for JPY)."""

    digits = exponent(currency) if places is None else places
    quantum = Decimal(1).scaleb(-digits)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def fmt(amount: Decimal, currency: str) -> str:
    places = exponent(currency)
    return f"{(currency or '').upper()} {Decimal(amount):,.{max(places, 0)}f}".strip()


def balance(invoice: SalesInvoice) -> Decimal:
    return max(money(invoice.total) - money(invoice.amount_paid), Decimal("0.00"))


def invoice_label(invoice: SalesInvoice) -> str:
    return f"INV-{invoice.number}"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def settings(db: AsyncSession, organization_id: uuid.UUID, key: str) -> dict[str, Any]:
    config = await core.workspace_config(db, organization_id, key)
    return {
        "notify_owner": config.get("notify_owner", True) is not False,
        "link_message": (config.get("link_message") or "")[:500],
        **{k: v for k, v in config.items() if k not in {"notify_owner", "link_message"}},
    }


def clean_common(config: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    if "notify_owner" in config:
        cleaned["notify_owner"] = bool(config.get("notify_owner"))
    if "link_message" in config:
        message = str(config.get("link_message") or "").strip()
        if len(message) > 500:
            raise IntegrationError("Keep the payment page message under 500 characters.", 422)
        cleaned["link_message"] = message
    return cleaned


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


async def link_for(db: AsyncSession, organization_id: uuid.UUID, provider: str, invoice_id: uuid.UUID) -> IntegrationRecordLink | None:
    return (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id,
                IntegrationRecordLink.provider == provider,
                IntegrationRecordLink.record_type == INVOICE,
                IntegrationRecordLink.record_id == invoice_id,
                IntegrationRecordLink.external_type == LINK,
                IntegrationRecordLink.external_parent == "",
            )
        )
    ).scalar_one_or_none()


async def link_by_external(db: AsyncSession, organization_id: uuid.UUID, provider: str, external_id: str) -> IntegrationRecordLink | None:
    return (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id,
                IntegrationRecordLink.provider == provider,
                IntegrationRecordLink.external_type == LINK,
                IntegrationRecordLink.external_parent == "",
                IntegrationRecordLink.external_id == external_id,
            )
        )
    ).scalar_one_or_none()


async def save_link(
    db: AsyncSession,
    invoice: SalesInvoice,
    provider: str,
    *,
    external_id: str,
    url: str,
    amount: Decimal,
    data: dict[str, Any] | None = None,
    user_id: uuid.UUID | None = None,
) -> IntegrationRecordLink:
    link = await link_for(db, invoice.organization_id, provider, invoice.id)
    if link is None:
        link = IntegrationRecordLink(
            id=uuid.uuid4(), organization_id=invoice.organization_id, provider=provider, record_type=INVOICE, record_id=invoice.id,
            external_type=LINK, external_parent="", external_id=external_id,
        )
        db.add(link)
    link.external_id = external_id[:200]
    link.external_url = url[:1000]
    link.status = "linked"
    link.last_error = None
    link.last_synced_at = utcnow()
    link.data = {
        **(data or {}),
        "amount": str(amount),
        "currency": invoice.currency.upper(),
        "created_by": str(user_id) if user_id else None,
        "created_at": utcnow().isoformat(),
        "state": "open",
    }
    return link


def link_out(link: IntegrationRecordLink, invoice: SalesInvoice | None = None) -> dict[str, Any]:
    data = link.data or {}
    amount = data.get("amount")
    stale = False
    if invoice is not None and amount is not None and data.get("state") == "open":
        try:
            stale = money(amount) != balance(invoice)
        except IntegrationError:
            stale = False
    return {
        "provider": link.provider,
        "provider_name": NAMES.get(link.provider, link.provider),
        "external_id": link.external_id,
        "url": link.external_url,
        "amount": amount,
        "currency": data.get("currency"),
        "state": data.get("state") or "open",
        "stale": stale,
        "status": link.status,
        "last_error": link.last_error,
        "created_at": data.get("created_at"),
        "emailed_at": data.get("emailed_at"),
        "checked_at": data.get("checked_at"),
    }


# ---------------------------------------------------------------------------
# Payments and refunds
# ---------------------------------------------------------------------------


async def lock_invoice(db: AsyncSession, invoice: SalesInvoice) -> SalesInvoice:
    """Re-read the invoice with a row lock, so two payments arriving together both count."""

    locked = (
        await db.execute(select(SalesInvoice).where(SalesInvoice.id == invoice.id).with_for_update().execution_options(populate_existing=True))
    ).scalar_one()
    return locked


async def find_object(db: AsyncSession, organization_id: uuid.UUID, provider: str, object_type: str, external_id: str) -> IntegrationObject | None:
    return (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == organization_id,
                IntegrationObject.provider == provider,
                IntegrationObject.object_type == object_type,
                IntegrationObject.external_parent == "",
                IntegrationObject.external_id == external_id,
            )
        )
    ).scalar_one_or_none()


async def _owner(db: AsyncSession, invoice: SalesInvoice | None, account_id: uuid.UUID | None) -> uuid.UUID | None:
    if invoice is not None and invoice.owner_user_id:
        return invoice.owner_user_id
    if account_id:
        return (await db.execute(select(Account.owner_user_id).where(Account.id == account_id))).scalar_one_or_none()
    return None


async def _contact_email(db: AsyncSession, contact_id: uuid.UUID | None) -> str | None:
    if not contact_id:
        return None
    return (await db.execute(select(Contact.email).where(Contact.id == contact_id))).scalar_one_or_none()


def _activity(
    invoice: SalesInvoice | None,
    *,
    organization_id: uuid.UUID,
    account_id: uuid.UUID | None,
    contact_id: uuid.UUID | None,
    subject: str,
    body: str | None,
    when: datetime,
    external_id: str,
    metadata: dict[str, Any],
) -> Activity | None:
    if not (account_id or contact_id):
        return None
    return Activity(
        id=uuid.uuid4(),
        organization_id=organization_id,
        account_id=account_id,
        contact_id=contact_id,
        opportunity_id=invoice.opportunity_id if invoice is not None else None,
        activity_type="system",
        subject=subject[:300],
        body=body,
        direction="inbound",
        occurred_at=when,
        external_id=external_id[:500],
        activity_metadata=metadata,
    )


async def notify(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None, title: str, body: str | None, link: str | None) -> None:
    if not user_id:
        return
    db.add(
        Notification(
            id=uuid.uuid4(), organization_id=organization_id, user_id=user_id, kind="payment",
            title=title[:200], body=(body or "")[:500] or None, link=(link or "")[:300] or None, is_read=False,
        )
    )


async def apply_payment(
    db: AsyncSession,
    connection: IntegrationConnection,
    invoice: SalesInvoice,
    *,
    external_id: str,
    amount: Decimal,
    currency: str,
    occurred_at: datetime | None = None,
    method: str | None = None,
    email: str | None = None,
    url: str | None = None,
    data: dict[str, Any] | None = None,
) -> IntegrationObject | None:
    """Record one completed payment. Returns the new object, or None if it was already recorded."""

    provider = connection.provider
    org = connection.organization_id
    if invoice.organization_id != org:
        raise IntegrationError("That invoice belongs to another workspace.", 403)
    existing = await find_object(db, org, provider, "payment", external_id)
    if existing is not None:
        return None
    invoice = await lock_invoice(db, invoice)
    currency = (currency or "").upper()
    amount = Decimal(amount)
    when = occurred_at or utcnow()
    name = NAMES.get(provider, provider)
    label = invoice_label(invoice)
    obj = IntegrationObject(
        id=uuid.uuid4(), organization_id=org, provider=provider, object_type="payment", external_id=external_id[:200], external_parent="",
        title=f"Payment for {label}", status="succeeded", amount=money(amount), currency=currency[:3] or None,
        email=(email or await _contact_email(db, invoice.contact_id) or None), url=url, occurred_at=when, external_updated_at=utcnow(),
        data={**(data or {}), "method": method, "applied": False, "refunded": "0"},
        account_id=invoice.account_id, contact_id=invoice.contact_id, opportunity_id=invoice.opportunity_id,
        record_type=INVOICE, record_id=invoice.id,
    )
    db.add(obj)
    await db.flush()
    owner = await _owner(db, invoice, invoice.account_id)
    config = await settings(db, org, provider)
    shown = fmt(amount, currency)
    if currency != invoice.currency.upper():
        message = f"{name} payment of {shown} for {label} is in a different currency than the invoice ({invoice.currency}); record it by hand."
        core.log(db, org, provider, "payment_currency_mismatch", message, level="warning", connection=connection, detail={"invoice_id": str(invoice.id), "object_id": str(obj.id)})
        await notify(db, org, owner, f"Check the {name} payment on {label}", message, f"/invoices/{invoice.id}")
        return obj
    if invoice.status == "void":
        message = f"{name} took {shown} for {label}, but the invoice is void. Refund it in {name} if it shouldn't have been paid."
        core.log(db, org, provider, "payment_on_void_invoice", message, level="warning", connection=connection, detail={"invoice_id": str(invoice.id)})
        await notify(db, org, owner, f"Payment on void invoice {label}", message, f"/invoices/{invoice.id}")
        return obj
    invoice.amount_paid = money(invoice.amount_paid) + money(amount)
    obj.data = {**obj.data, "applied": True}
    if invoice.amount_paid >= money(invoice.total):
        invoice.status = "paid"
        invoice.paid_at = when
    elif invoice.status == "draft":
        invoice.status = "sent"
    over = money(invoice.amount_paid) - money(invoice.total)
    message = f"{shown} received via {name} for {label}" + (" (paid in full)." if invoice.status == "paid" else f"; {fmt(balance(invoice), currency)} still due.")
    core.log(db, org, provider, "payment_received", message, level="success", connection=connection, detail={"invoice_id": str(invoice.id), "object_id": str(obj.id), "external_id": external_id})
    if over > 0:
        core.log(db, org, provider, "overpaid", f"{label} is overpaid by {fmt(over, currency)}.", level="warning", connection=connection, detail={"invoice_id": str(invoice.id)})
    activity = _activity(
        invoice, organization_id=org, account_id=invoice.account_id, contact_id=invoice.contact_id,
        subject=f"Paid {shown} on {label} via {name}", body=None, when=when, external_id=f"{provider}:payment:{external_id}",
        metadata={"kind": "payment", "provider": provider, "invoice_id": str(invoice.id), "integration_object_id": str(obj.id), "amount": str(amount), "currency": currency},
    )
    if activity is not None:
        db.add(activity)
    record_audit(db, organization_id=org, action="payment", entity_type="invoice", entity_id=invoice.id, entity_label=label,
                 changes={"amount": str(amount), "currency": currency, "provider": provider, "external_id": external_id})
    if config["notify_owner"]:
        await notify(db, org, owner, f"{label} {'paid' if invoice.status == 'paid' else 'part-paid'}: {shown}", f"Received via {name}.", f"/invoices/{invoice.id}")
    if invoice.status == "paid":
        for other in (await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.record_type == INVOICE,
                IntegrationRecordLink.record_id == invoice.id, IntegrationRecordLink.external_type == LINK,
            )
        )).scalars():
            other.data = {**(other.data or {}), "state": "paid" if other.provider == provider else "superseded"}
    connection.last_sync_at = utcnow()
    return obj


async def apply_refund(
    db: AsyncSession,
    connection: IntegrationConnection,
    *,
    external_id: str,
    payment_external_id: str,
    amount: Decimal,
    currency: str,
    occurred_at: datetime | None = None,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> IntegrationObject | None:
    """Record one completed refund of a payment AIVA knows. None if already recorded or unknown."""

    provider = connection.provider
    org = connection.organization_id
    if await find_object(db, org, provider, "refund", external_id) is not None:
        return None
    payment = await find_object(db, org, provider, "payment", payment_external_id)
    invoice = await db.get(SalesInvoice, payment.record_id) if payment is not None and payment.record_id else None
    if invoice is not None and invoice.organization_id != org:
        invoice = None
    if invoice is not None:
        invoice = await lock_invoice(db, invoice)
    currency = (currency or "").upper()
    when = occurred_at or utcnow()
    name = NAMES.get(provider, provider)
    obj = IntegrationObject(
        id=uuid.uuid4(), organization_id=org, provider=provider, object_type="refund", external_id=external_id[:200], external_parent="",
        title=f"Refund for {invoice_label(invoice)}" if invoice else f"{name} refund", status="succeeded", amount=money(amount), currency=currency[:3] or None,
        email=payment.email if payment else None, occurred_at=when, external_updated_at=utcnow(),
        data={**(data or {}), "payment_external_id": payment_external_id, "reason": reason, "applied": False},
        account_id=payment.account_id if payment else None, contact_id=payment.contact_id if payment else None,
        opportunity_id=payment.opportunity_id if payment else None,
        record_type=INVOICE if invoice else None, record_id=invoice.id if invoice else None,
    )
    db.add(obj)
    await db.flush()
    if payment is not None:
        refunded = money((payment.data or {}).get("refunded") or 0) + money(amount)
        payment.data = {**(payment.data or {}), "refunded": str(refunded)}
        payment.status = "refunded" if payment.amount is not None and refunded >= money(payment.amount) else "partially_refunded"
    if invoice is None or payment is None or not (payment.data or {}).get("applied") or currency != invoice.currency.upper():
        core.log(db, org, provider, "refund_recorded", f"{name} refund of {fmt(money(amount), currency)} recorded.", connection=connection, detail={"object_id": str(obj.id)})
        return obj
    label = invoice_label(invoice)
    was_paid = invoice.status == "paid"
    invoice.amount_paid = max(money(invoice.amount_paid) - money(amount), Decimal("0.00"))
    if was_paid and invoice.amount_paid < money(invoice.total):
        invoice.status = "sent"
        invoice.paid_at = None
    obj.data = {**obj.data, "applied": True}
    shown = fmt(money(amount), currency)
    core.log(db, org, provider, "refund", f"{shown} refunded via {name} on {label}; {fmt(balance(invoice), currency)} now due.", level="warning", connection=connection,
             detail={"invoice_id": str(invoice.id), "object_id": str(obj.id)})
    activity = _activity(
        invoice, organization_id=org, account_id=invoice.account_id, contact_id=invoice.contact_id,
        subject=f"Refunded {shown} on {label} via {name}", body=reason, when=when, external_id=f"{provider}:refund:{external_id}",
        metadata={"kind": "refund", "provider": provider, "invoice_id": str(invoice.id), "integration_object_id": str(obj.id), "amount": str(amount), "currency": currency},
    )
    if activity is not None:
        activity.direction = "outbound"
        db.add(activity)
    record_audit(db, organization_id=org, action="refund", entity_type="invoice", entity_id=invoice.id, entity_label=label,
                 changes={"amount": str(amount), "currency": currency, "provider": provider, "external_id": external_id})
    if (await settings(db, org, provider))["notify_owner"]:
        await notify(db, org, await _owner(db, invoice, invoice.account_id), f"Refund on {label}: {shown}", f"Refunded via {name}.", f"/invoices/{invoice.id}")
    return obj


async def mark_failed(db: AsyncSession, connection: IntegrationConnection, invoice: SalesInvoice, reason: str) -> None:
    """A payment attempt failed: tell the owner, once per reason per invoice and day."""

    name = NAMES.get(connection.provider, connection.provider)
    message = f"A {name} payment for {invoice_label(invoice)} failed: {reason}"[:500]
    core.log(db, connection.organization_id, connection.provider, "payment_failed", message, level="warning", connection=connection, detail={"invoice_id": str(invoice.id)})
    if (await settings(db, connection.organization_id, connection.provider))["notify_owner"]:
        await notify(db, connection.organization_id, await _owner(db, invoice, invoice.account_id), f"Payment failed on {invoice_label(invoice)}", message, f"/invoices/{invoice.id}")


def object_out(obj: IntegrationObject, records: dict[tuple[str, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    data = obj.data or {}
    invoice_ref = (records or {}).get(("invoice", str(obj.record_id))) if obj.record_type == INVOICE and obj.record_id else None
    return {
        "id": str(obj.id),
        "provider": obj.provider,
        "provider_name": NAMES.get(obj.provider, obj.provider),
        "type": obj.object_type,
        "external_id": obj.external_id,
        "title": obj.title,
        "status": obj.status,
        "amount": str(obj.amount) if obj.amount is not None else None,
        "currency": obj.currency,
        "email": obj.email,
        "url": obj.url,
        "occurred_at": obj.occurred_at,
        "invoice_id": str(obj.record_id) if obj.record_type == INVOICE and obj.record_id else None,
        "invoice": invoice_ref,
        "refunded": data.get("refunded"),
        "method": data.get("method"),
        "applied": bool(data.get("applied")),
        "reason": data.get("reason"),
        "account_id": str(obj.account_id) if obj.account_id else None,
        "data": {k: v for k, v in data.items() if k in {"plan", "billing_period", "mrr", "next_billing_at", "current_term_end", "cancelled_at", "cancel_reason", "trial_end", "status_label", "quantity"}},
    }


def parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return parse_time(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
