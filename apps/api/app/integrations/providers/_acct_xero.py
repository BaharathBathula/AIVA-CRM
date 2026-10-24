"""Xero (Accounting API 2.0).

* Organisation: ``tenant_id`` from the sign-in; a person who shares several
  organisations picks one on the Xero page. Every call sends ``Xero-tenant-id``.
* Contacts: matched by email, then name (names are unique in Xero); created as
  customers with the account's address.
* Invoices: ``POST /Invoices`` (``ACCREC``, tax-exclusive lines with the line
  discount) with an ``Idempotency-Key``, so a retried create never doubles up.
  New invoices are approved (``AUTHORISED``) or left as drafts, as chosen.
* Payments: the invoice's ``Payments``. Changes are found with
  ``If-Modified-Since`` and nudged by webhooks (``x-xero-signature``).
* Limits: 60 calls a minute and 5,000 a day per organisation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from app.integrations.core import IntegrationError
from app.integrations.providers._acct_types import (
    CustomerData, Ctx, InvoiceData, Payment, RemoteCustomer, RemoteInvoice, Summary, money, parse_date,
)

KEY = "xero"
BASE = "https://api.xero.com/api.xro/2.0"
MAX_CHANGED_PAGES = 3


def scope_id(config: dict[str, Any]) -> str:
    return str(config.get("tenant_id") or "")


def _headers(ctx: Ctx, extra: dict[str, str] | None = None) -> dict[str, str]:
    tenant = scope_id(ctx.config)
    if not tenant:
        raise IntegrationError("Xero didn't say which organisation to use. Reconnect Xero and tick an organisation.", 409)
    return {"Xero-tenant-id": tenant, **(extra or {})}


def _deep(config: dict[str, Any], path: str) -> str:
    shortcode = str(config.get("short_code") or "")
    if shortcode:
        return f"https://go.xero.com/organisationlogin/default.aspx?shortcode={quote(shortcode, safe='')}&redirecturl={quote(path, safe='')}"
    return f"https://go.xero.com{path}"


def invoice_url(config: dict[str, Any], external_id: str) -> str:
    return _deep(config, f"/AccountsReceivable/View.aspx?InvoiceID={external_id}")


def customer_url(config: dict[str, Any], external_id: str) -> str:
    return _deep(config, f"/Contacts/View/{external_id}")


def home_url(config: dict[str, Any]) -> str:
    return _deep(config, "/Dashboard/")


async def _get(ctx: Ctx, path: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = await ctx.call("GET", f"{BASE}/{path}", params=params or {}, headers=_headers(ctx, headers))
    return response.json() if response.content else {}


async def _post(ctx: Ctx, path: str, body: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
    extra = {"Content-Type": "application/json"}
    if idempotency_key:
        extra["Idempotency-Key"] = idempotency_key[:128]
    response = await ctx.call("POST", f"{BASE}/{path}", json=body, headers=_headers(ctx, extra))
    return response.json()


# ---------------------------------------------------------------------------
# Organisation and lists
# ---------------------------------------------------------------------------


async def company(ctx: Ctx) -> dict[str, Any]:
    org = ((await _get(ctx, "Organisation")).get("Organisations") or [{}])[0]
    return {
        "name": org.get("Name"),
        "country": org.get("CountryCode"),
        "currency": org.get("BaseCurrency"),
        "multi_currency": True,  # Xero accepts any enabled currency; it says so if one isn't
        "custom_numbers": True,
        "short_code": org.get("ShortCode"),
    }


async def options(ctx: Ctx) -> dict[str, Any]:
    accounts = (await _get(ctx, "Accounts", {"where": 'Class=="REVENUE"&&Status=="ACTIVE"'})).get("Accounts") or []
    rates = (await _get(ctx, "TaxRates", {"where": 'Status=="ACTIVE"'})).get("TaxRates") or []
    items = (await _get(ctx, "Items")).get("Items") or []
    return {
        "items": [{"id": i.get("Code"), "name": i.get("Name") or i.get("Code"), "sku": i.get("Code"), "type": "item"} for i in items if i.get("Code")],
        "accounts": [{"id": a.get("Code"), "name": f"{a.get('Code')} · {a.get('Name')}"} for a in accounts if a.get("Code")],
        "tax_codes": [
            {"id": r.get("TaxType"), "name": f"{r.get('Name')} ({r.get('EffectiveRate')}%)", "rate": r.get("EffectiveRate")}
            for r in rates
            if r.get("TaxType") and r.get("CanApplyToRevenue", True)
        ],
        "terms": [],
    }


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


def _customer(row: dict[str, Any], config: dict[str, Any]) -> RemoteCustomer:
    return RemoteCustomer(id=str(row.get("ContactID")), name=row.get("Name") or "", url=customer_url(config, str(row.get("ContactID"))),
                          email=row.get("EmailAddress"))


async def get_customer(ctx: Ctx, external_id: str) -> RemoteCustomer | None:
    try:
        rows = (await _get(ctx, f"Contacts/{quote(external_id, safe='')}")).get("Contacts") or []
    except IntegrationError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    return _customer(rows[0], ctx.config) if rows and rows[0].get("ContactID") else None


async def search_customers(ctx: Ctx, text: str) -> list[RemoteCustomer]:
    rows = (await _get(ctx, "Contacts", {"searchTerm": text.strip()[:100], "summaryOnly": "true", "page": "1"})).get("Contacts") or []
    return [_customer(r, ctx.config) for r in rows if r.get("ContactStatus", "ACTIVE") == "ACTIVE"][:20]


async def find_customer(ctx: Ctx, data: CustomerData) -> RemoteCustomer | None:
    candidates: list[dict[str, Any]] = []
    for term in [t for t in (data.email, data.name) if t]:
        rows = (await _get(ctx, "Contacts", {"searchTerm": term[:100], "summaryOnly": "true", "page": "1"})).get("Contacts") or []
        candidates.extend(r for r in rows if r.get("ContactStatus", "ACTIVE") == "ACTIVE")
        if data.email:
            by_email = [r for r in candidates if (r.get("EmailAddress") or "").lower() == data.email.lower()]
            if len(by_email) == 1:
                return _customer(by_email[0], ctx.config)
        by_name = [r for r in candidates if (r.get("Name") or "").strip().lower() == data.name.strip().lower()]
        if by_name:
            return _customer(by_name[0], ctx.config)
    return None


async def create_customer(ctx: Ctx, data: CustomerData, multi_currency: bool) -> RemoteCustomer:
    contact: dict[str, Any] = {"Name": data.name[:255]}
    if data.email:
        contact["EmailAddress"] = data.email[:255]
    if data.first_name:
        contact["FirstName"] = data.first_name[:255]
    if data.last_name:
        contact["LastName"] = data.last_name[:255]
    if data.phone:
        contact["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": data.phone[:50]}]
    if data.website and data.website.startswith(("http://", "https://")):
        contact["Website"] = data.website[:255]
    address = {"AddressLine1": data.line1, "AddressLine2": data.line2, "City": data.city, "Region": data.region,
               "PostalCode": data.postal_code, "Country": data.country}
    if any(address.values()):
        contact["Addresses"] = [{"AddressType": "POBOX", **{k: str(v)[:255] for k, v in address.items() if v}}]
    if data.currency:
        contact["DefaultCurrency"] = data.currency
    try:
        rows = (await _post(ctx, "Contacts", {"Contacts": [contact]})).get("Contacts") or []
    except IntegrationError as exc:
        if "already assigned" in str(exc) or "must be unique" in str(exc):
            found = await find_customer(ctx, CustomerData(name=data.name))
            if found is not None:
                return found
        raise
    if not rows or not rows[0].get("ContactID"):
        raise IntegrationError("Xero didn't return the contact it created.", 502)
    return _customer(rows[0], ctx.config)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def invoice_body(inv: InvoiceData, customer_id: str, settings: dict[str, Any], existing_id: str | None) -> dict[str, Any]:
    if not settings.get("income_account"):
        raise IntegrationError("Pick the Xero sales account for invoice lines on the Xero page.", 409)
    lines = []
    for line in inv.lines:
        item: dict[str, Any] = {
            "Description": (line.description or line.name or "Item")[:4000],
            "Quantity": float(line.quantity),
            "UnitAmount": float(line.unit_price),
            "AccountCode": str(settings["income_account"]),
        }
        if line.discount_percent:
            item["DiscountRate"] = float(line.discount_percent)
        tax_type = settings.get("tax_code") if line.taxable else settings.get("tax_code_exempt")
        if tax_type:
            item["TaxType"] = str(tax_type)
        if line.item_ref:
            item["ItemCode"] = str(line.item_ref)
        lines.append(item)
    invoice: dict[str, Any] = {
        "Type": "ACCREC",
        "Contact": {"ContactID": customer_id},
        "Date": inv.issue_date.isoformat(),
        "LineAmountTypes": "Exclusive",
        "Reference": inv.reference[:255],
        "CurrencyCode": inv.currency,
        "LineItems": lines,
    }
    if inv.due_date:
        invoice["DueDate"] = inv.due_date.isoformat()
    if settings.get("numbering") == "aiva":
        invoice["InvoiceNumber"] = inv.reference[:255]
    if existing_id:
        invoice["InvoiceID"] = existing_id
    else:
        invoice["Status"] = "DRAFT" if settings.get("xero_status") == "DRAFT" else "AUTHORISED"
    return {"Invoices": [invoice]}


_STATUS = {"DRAFT": "draft", "SUBMITTED": "draft", "AUTHORISED": "open", "PAID": "paid", "VOIDED": "void", "DELETED": "deleted"}


def _remote(row: dict[str, Any], config: dict[str, Any]) -> RemoteInvoice:
    currency = row.get("CurrencyCode")
    payments = [
        Payment(id=str(p.get("PaymentID")), amount=money(p.get("Amount"), currency), date=parse_date(p.get("Date")), reference=p.get("Reference"),
                currency=currency)
        for p in row.get("Payments") or []
        if p.get("PaymentID") and str(p.get("Status") or "AUTHORISED") != "DELETED"
    ]
    status = _STATUS.get(str(row.get("Status") or ""), "open")
    total = money(row.get("Total"), currency)
    balance = money(row.get("AmountDue"), currency) if status not in {"void", "deleted"} else Decimal("0")
    return RemoteInvoice(
        id=str(row.get("InvoiceID")), number=row.get("InvoiceNumber"), url=invoice_url(config, str(row.get("InvoiceID"))),
        total=total, balance=balance, currency=currency, status="void" if status == "deleted" else status, payments=payments,
        due_date=parse_date(row.get("DueDateString") or row.get("DueDate")), issue_date=parse_date(row.get("DateString") or row.get("Date")),
    )


def _first_invoice(data: dict[str, Any]) -> dict[str, Any]:
    rows = data.get("Invoices") or []
    if not rows or not rows[0].get("InvoiceID"):
        raise IntegrationError("Xero didn't return the invoice.", 502)
    row = rows[0]
    if row.get("HasErrors"):
        messages = "; ".join(e.get("Message", "") for e in row.get("ValidationErrors") or [] if e.get("Message"))
        raise IntegrationError(f"Xero said: {messages or 'the invoice has errors'}", 400)
    return row


async def push_invoice(
    ctx: Ctx, inv: InvoiceData, customer_id: str, existing_id: str | None, meta: dict[str, Any], request_id: str, company_info: dict[str, Any]
) -> RemoteInvoice:
    if existing_id:
        current = await get_invoice(ctx, existing_id, with_payments=False)
        if current is not None and current.status in {"paid", "void"}:
            raise IntegrationError(f"This invoice is {current.status} in Xero, so AIVA won't change it.", 409)
        if current is not None and current.payments:
            raise IntegrationError("This invoice has payments in Xero, so AIVA won't change it.", 409)
    body = invoice_body(inv, customer_id, ctx.settings, existing_id)
    row = _first_invoice(await _post(ctx, f"Invoices/{quote(existing_id, safe='')}" if existing_id else "Invoices", body, request_id))
    return _remote(row, ctx.config)


async def get_invoice(ctx: Ctx, external_id: str, *, with_payments: bool = True) -> RemoteInvoice | None:
    try:
        data = await _get(ctx, f"Invoices/{quote(external_id, safe='')}")
    except IntegrationError as exc:
        if "not found" in str(exc).lower() or "404" in str(exc):
            return None
        raise
    rows = data.get("Invoices") or []
    return _remote(rows[0], ctx.config) if rows else None


async def void_invoice(ctx: Ctx, external_id: str, meta: dict[str, Any]) -> None:
    current = await get_invoice(ctx, external_id)
    if current is None or current.status == "void":
        return
    if current.payments:
        raise IntegrationError("This invoice has payments in Xero. Remove them there before voiding.", 409)
    new_status = "DELETED" if current.status == "draft" else "VOIDED"
    await _post(ctx, f"Invoices/{quote(external_id, safe='')}", {"Invoices": [{"InvoiceID": external_id, "Status": new_status}]})


async def changed_since(ctx: Ctx, since: datetime) -> tuple[set[str], set[str]]:
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    found: set[str] = set()
    for page in range(1, MAX_CHANGED_PAGES + 1):
        rows = (
            await _get(ctx, "Invoices", {"where": 'Type=="ACCREC"', "summaryOnly": "true", "page": str(page)}, headers={"If-Modified-Since": stamp})
        ).get("Invoices") or []
        found.update(str(r["InvoiceID"]) for r in rows if r.get("InvoiceID"))
        if len(rows) < 100:
            break
    return found, set()


async def summary(ctx: Ctx, customer_id: str) -> Summary:
    contact = ((await _get(ctx, f"Contacts/{quote(customer_id, safe='')}")).get("Contacts") or [{}])[0]
    receivable = ((contact.get("Balances") or {}).get("AccountsReceivable") or {})
    currency = contact.get("DefaultCurrency")
    rows = (
        await _get(ctx, "Invoices", {"ContactIDs": customer_id, "where": 'Type=="ACCREC"', "order": "Date DESC", "summaryOnly": "true", "page": "1"})
    ).get("Invoices") or []
    today = date.today()
    invoices = []
    overdue_count = 0
    for row in rows[:20]:
        remote = _remote(row, ctx.config)
        late = remote.status == "open" and remote.balance > 0 and remote.due_date is not None and remote.due_date < today
        overdue_count += 1 if late else 0
        invoices.append({
            "id": remote.id, "number": remote.number, "date": remote.issue_date.isoformat() if remote.issue_date else None,
            "due_date": remote.due_date.isoformat() if remote.due_date else None, "total": str(remote.total), "balance": str(remote.balance),
            "status": "overdue" if late else remote.status, "currency": remote.currency, "url": remote.url,
        })
    return Summary(currency=currency, open_balance=money(receivable.get("Outstanding"), currency), overdue_balance=money(receivable.get("Overdue"), currency),
                   overdue_count=overdue_count, invoices=invoices[:10])


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def signature_ok(key: str | None, body: bytes, header: str | None) -> bool:
    if not key or not header:
        return False
    expected = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest())
    return hmac.compare_digest(expected, header.strip().encode())


def parse_webhook(payload: Any) -> list[tuple[str, str, str]]:
    """[(tenant id, category, resource id)]; an empty list is Xero's intent-to-receive check."""

    found: list[tuple[str, str, str]] = []
    if isinstance(payload, dict):
        for event in payload.get("events") or []:
            if isinstance(event, dict) and event.get("tenantId") and event.get("resourceId") and event.get("eventCategory"):
                found.append((str(event["tenantId"]), str(event["eventCategory"]).capitalize(), str(event["resourceId"])))
    return found[:500]


def tenant_choices(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in config.get("tenants") or [] if isinstance(t, dict) and t.get("id")]

