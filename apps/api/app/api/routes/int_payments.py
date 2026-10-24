"""Integration endpoints: payments (Stripe, PayPal, Square, Chargebee).

* ``/payments/processors``: which processors can take invoice payments.
* ``/payments/invoices/{id}``: an invoice's payment links, payments and refunds;
  create, close, email and check links; refund a payment.
* ``/payments/accounts/{id}``: Chargebee subscriptions, MRR and renewals, plus
  every processor's payments for the account.
* ``/payments/{provider}/webhook`` and ``/payments/chargebee/sync``: admin
  set-up actions.

Inbound webhooks arrive through the hub's ``/public/integrations/{key}/webhook/{token}``
and are handled in ``app/integrations/providers/payments.py``.
"""

from __future__ import annotations

import html
import re
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _pay_chargebee, _pay_common as pay, _pay_paypal, _pay_square, _pay_stripe, payments
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationObject, IntegrationRecordLink
from app.models.organization import Organization
from app.services import mailer, records
from app.services.automation import record_audit

router = APIRouter(prefix="/payments", tags=["Integrations"], dependencies=[Depends(require_feature("online_payments"))])
public_router = APIRouter(prefix="/public", tags=["Public"])

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
WEBHOOK_SETUP = {"stripe": _pay_stripe.ensure_webhook, "paypal": _pay_paypal.ensure_webhook, "square": _pay_square.ensure_webhook}


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _invoice(db: AsyncSession, principal: Principal, invoice_id: uuid.UUID) -> SalesInvoice:
    invoice = await db.get(SalesInvoice, invoice_id)
    if invoice is None or invoice.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found.")
    return invoice


async def invoice_payments(db: AsyncSession, principal: Principal, invoice: SalesInvoice) -> dict[str, Any]:
    org = principal.organization_id
    links = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.record_type == pay.INVOICE,
                IntegrationRecordLink.record_id == invoice.id, IntegrationRecordLink.external_type == pay.LINK,
            )
        )
    ).scalars().all()
    objects = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == org, IntegrationObject.record_type == pay.INVOICE, IntegrationObject.record_id == invoice.id,
                IntegrationObject.object_type.in_(("payment", "refund")),
            ).order_by(IntegrationObject.occurred_at.desc().nulls_last())
        )
    ).scalars().all()
    contact = await db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    return {
        "invoice": {
            "id": str(invoice.id),
            "label": pay.invoice_label(invoice),
            "status": invoice.status,
            "currency": invoice.currency,
            "total": str(pay.money(invoice.total)),
            "amount_paid": str(pay.money(invoice.amount_paid)),
            "balance": str(pay.balance(invoice)),
            "contact_name": f"{contact.first_name or ''} {contact.last_name or ''}".strip() if contact else None,
            "contact_email": contact.email if contact else None,
        },
        "processors": await payments.processors(db, org),
        "links": [pay.link_out(link, invoice) for link in sorted(links, key=lambda l: pay.PROCESSORS.index(l.provider) if l.provider in pay.PROCESSORS else 9)],
        "payments": [pay.object_out(o) for o in objects],
        "can_send": principal.can("sales.invoices"),
        "can_refund": principal.can("sales.invoices") and principal.can("admin.settings"),
    }


@router.get("/processors")
async def list_processors(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return {"items": await payments.processors(db, principal.organization_id), "can_send": principal.can("sales.invoices")}


@router.get("/invoices/{invoice_id}")
async def get_invoice_payments(invoice_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return await invoice_payments(db, principal, await _invoice(db, principal, invoice_id))


class LinkPayload(BaseModel):
    provider: str = Field(pattern="^(stripe|paypal|square)$")
    message: str | None = Field(default=None, max_length=500)


@router.post("/invoices/{invoice_id}/links")
async def create_link(invoice_id: uuid.UUID, payload: LinkPayload, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)):
    """Create (or replace) the processor's payment link for the invoice's balance."""

    invoice = await _invoice(db, principal, invoice_id)
    try:
        await payments.create_link(db, principal, invoice, payload.provider, payload.message)
    except IntegrationError as exc:
        await db.rollback()
        raise _error(exc) from exc
    record_audit(db, organization_id=principal.organization_id, action="payment_link_created", entity_type="invoice", entity_id=invoice.id,
                 entity_label=pay.invoice_label(invoice), changes={"provider": payload.provider})
    await db.commit()
    return await invoice_payments(db, principal, invoice)


@router.delete("/invoices/{invoice_id}/links/{provider}")
async def close_link(invoice_id: uuid.UUID, provider: str, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)):
    if provider not in pay.PROCESSORS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown payment processor.")
    invoice = await _invoice(db, principal, invoice_id)
    try:
        await payments.close_link(db, principal, invoice, provider)
    except IntegrationError as exc:
        await db.rollback()
        raise _error(exc) from exc
    await db.commit()
    return await invoice_payments(db, principal, invoice)


class EmailPayload(BaseModel):
    provider: str = Field(pattern="^(stripe|paypal|square)$")
    to: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=4000)


@router.post("/invoices/{invoice_id}/email")
async def email_link(invoice_id: uuid.UUID, payload: EmailPayload, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)):
    """Email the payment link to the invoice's contact from the sender's own mailbox."""

    invoice = await _invoice(db, principal, invoice_id)
    link = await pay.link_for(db, principal.organization_id, payload.provider, invoice.id)
    if link is None or (link.data or {}).get("state") != "open" or not link.external_url:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Create a {pay.NAMES[payload.provider]} payment link first.")
    contact = await db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    to = (payload.to or (contact.email if contact else "") or "").strip()
    if not EMAIL.match(to):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add an email address to send the link to.")
    organization = await db.get(Organization, principal.organization_id)
    company = organization.name if organization else "us"
    label = pay.invoice_label(invoice)
    due = pay.fmt(pay.balance(invoice), invoice.currency)
    greeting = f"Hi {contact.first_name}," if contact and contact.first_name and to.lower() == (contact.email or "").lower() else "Hello,"
    intro = (payload.message or f"Here's a secure link to pay invoice {label} ({due}).").strip()
    subject = (payload.subject or f"Invoice {label} from {company}").strip()
    text = f"{greeting}\n\n{intro}\n\nPay online: {link.external_url}\n\nThank you,\n{principal.full_name or company}"
    html_body = (
        f"<p>{html.escape(greeting)}</p><p>{html.escape(intro).replace(chr(10), '<br>')}</p>"
        f"<p><a href=\"{html.escape(link.external_url, quote=True)}\" style=\"display:inline-block;padding:10px 18px;border-radius:8px;background:#1f1d2b;color:#fff;text-decoration:none\">"
        f"Pay {html.escape(due)}</a></p><p style=\"color:#666;font-size:13px\">Or open this link: {html.escape(link.external_url)}</p>"
        f"<p>Thank you,<br>{html.escape(principal.full_name or company)}</p>"
    )
    config = await mailer.sender_config(db, principal.organization_id, principal.user_id)
    if config is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "AIVA has no mailbox to send from. Connect your mailbox in Integrations, or copy the link and send it yourself.")
    try:
        await mailer.send(config, to=to, to_name=(f"{contact.first_name or ''} {contact.last_name or ''}".strip() if contact else None), subject=subject, text=text, html_body=html_body)
    except mailer.MailError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The email wasn't sent: {exc}") from exc
    link.data = {**(link.data or {}), "emailed_at": pay.utcnow().isoformat(), "emailed_to": to}
    if invoice.status == "draft":
        invoice.status = "sent"
    if invoice.contact_id or invoice.account_id:
        db.add(Activity(
            id=uuid.uuid4(), organization_id=principal.organization_id, contact_id=invoice.contact_id, account_id=invoice.account_id,
            opportunity_id=invoice.opportunity_id, created_by_user_id=principal.user_id, activity_type="email", subject=subject[:300],
            body=text, direction="outbound", occurred_at=pay.utcnow(),
            activity_metadata={"kind": "payment_link", "provider": payload.provider, "invoice_id": str(invoice.id), "to": to},
        ))
    core.log(db, principal.organization_id, payload.provider, "link_emailed", f"Emailed the {pay.NAMES[payload.provider]} payment link for {label} to {to}.", user_id=principal.user_id)
    await db.commit()
    result = await invoice_payments(db, principal, invoice)
    result["message"] = f"Sent to {to}."
    return result


@router.post("/invoices/{invoice_id}/check")
async def check_invoice(invoice_id: uuid.UUID, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)):
    """Ask the processors about this invoice's payments and refunds now."""

    invoice = await _invoice(db, principal, invoice_id)
    try:
        found = await payments.check_invoice(db, invoice)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    await db.commit()
    result = await invoice_payments(db, principal, invoice)
    result["message"] = f"Found {found} new payment{'s' if found != 1 else ''} or refund{'s' if found != 1 else ''}." if found else "Nothing new."
    return result


class RefundPayload(BaseModel):
    payment_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reason: str | None = Field(default=None, max_length=255)


@router.post("/refunds")
async def refund_payment(payload: RefundPayload, principal: Principal = Depends(require_permission("sales.invoices", "admin.settings")), db: AsyncSession = Depends(get_db)):
    """Refund (part of) a Stripe, PayPal or Square payment. Admins with invoice rights only."""

    payment = await db.get(IntegrationObject, payload.payment_id)
    if payment is None or payment.organization_id != principal.organization_id or payment.object_type != "payment":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found.")
    try:
        message = await payments.refund(db, payment, pay.money(payload.amount), payload.reason)
    except IntegrationError as exc:
        await db.rollback()
        raise _error(exc) from exc
    record_audit(db, organization_id=principal.organization_id, action="refund_requested", entity_type="invoice", entity_id=payment.record_id,
                 entity_label=payment.title, changes={"amount": str(payload.amount), "provider": payment.provider, "payment": payment.external_id})
    core.log(db, principal.organization_id, payment.provider, "refund_requested", message, user_id=principal.user_id, detail={"object_id": str(payment.id)})
    await db.commit()
    if payment.record_type == pay.INVOICE and payment.record_id:
        invoice = await db.get(SalesInvoice, payment.record_id)
        if invoice is not None:
            result = await invoice_payments(db, principal, invoice)
            result["message"] = message
            return result
    return {"message": message}


@router.get("/accounts/{account_id}")
async def account_billing(account_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Chargebee subscriptions and every processor's payments for an account."""

    await records.resolve(db, principal.organization_id, "account", account_id)  # record access
    org = principal.organization_id
    chargebee_on = await core.is_enabled(db, org, "chargebee")
    subscriptions = await payments.subscription_summary(db, org, account_id)
    link = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == "chargebee", IntegrationRecordLink.record_type == "account",
                IntegrationRecordLink.record_id == account_id, IntegrationRecordLink.external_type == "customer",
            )
        )
    ).scalar_one_or_none()
    return {
        "chargebee": {"enabled": chargebee_on, "customer_id": link.external_id if link else None, "customer_url": link.external_url if link else None, **subscriptions},
        "payments": await payments.recent(db, org, None, 50, account_id=account_id),
        "processors": await payments.processors(db, org),
    }


@router.post("/chargebee/sync")
async def chargebee_sync(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    try:
        connection = await core.active_connection(db, principal.organization_id, "chargebee")
        handled = await _pay_chargebee.sync(db, connection)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    core.log(db, principal.organization_id, "chargebee", "synced", f"Synced {handled} changed customer(s) and subscription(s).", connection=connection, user_id=principal.user_id)
    await db.commit()
    more = bool((connection.config or {}).get("customers_offset") or (connection.config or {}).get("subscriptions_offset"))
    return {"handled": handled, "more": more, "message": f"Synced {handled} change{'s' if handled != 1 else ''}." + (" More will follow in the background." if more else "")}


@router.post("/{provider}/webhook")
async def setup_webhook(provider: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Create or repair AIVA's webhook at Stripe, PayPal or Square."""

    setup = WEBHOOK_SETUP.get(provider)
    if setup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown payment processor.")
    try:
        connection = await core.active_connection(db, principal.organization_id, provider)
        mode = await setup(db, connection)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    connection.last_error = None
    await db.commit()
    return {"mode": mode, "message": "Webhook ready." if mode == "webhook" else "No public https address, so AIVA checks for payments every 30 minutes."}
