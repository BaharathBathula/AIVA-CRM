"""QuickBooks Online (Intuit Accounting API v3, minor version 75).

* Company: ``realm_id`` and the API base (production or sandbox) come from the
  sign-in (see ``identity.py``).
* Customers: matched by email, then display name; created with the account's
  billing address. Display names must be unique across customers, vendors and
  employees, so a clash looks the existing customer up instead.
* Invoices: ``POST /invoice`` with ``requestid`` (Intuit's idempotency key), so a
  retried create never makes a second invoice. Updates send the ``SyncToken``.
  Lines carry their net (after-discount) amount; taxable lines use the chosen
  tax code and QuickBooks works out the tax.
* Payments: read from the invoice's linked transactions. Changes are found with
  Change Data Capture (``/cdc``) and nudged by webhooks.
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
    CustomerData, Ctx, InvoiceData, Payment, RemoteCustomer, RemoteInvoice, Summary, decimal, money, parse_date,
)

KEY = "quickbooks"
MINOR_VERSION = "75"
MAX_PAYMENTS_READ = 10


def scope_id(config: dict[str, Any]) -> str:
    return str(config.get("realm_id") or "")


def _base(ctx: Ctx) -> str:
    config = ctx.config
    realm = scope_id(config)
    api_base = str(config.get("api_base") or "")
    if not realm.isdigit() or api_base not in {"https://quickbooks.api.intuit.com", "https://sandbox-quickbooks.api.intuit.com"}:
        raise IntegrationError("QuickBooks didn't say which company to use. Reconnect QuickBooks and pick a company.", 409)
    return f"{api_base}/v3/company/{realm}"


def _app_host(config: dict[str, Any]) -> str:
    return "https://app.sandbox.qbo.intuit.com" if config.get("environment") == "sandbox" else "https://app.qbo.intuit.com"


def invoice_url(config: dict[str, Any], external_id: str) -> str:
    return f"{_app_host(config)}/app/invoice?txnId={quote(str(external_id), safe='')}"


def customer_url(config: dict[str, Any], external_id: str) -> str:
    return f"{_app_host(config)}/app/customerdetail?nameId={quote(str(external_id), safe='')}"


def home_url(config: dict[str, Any]) -> str:
    return f"{_app_host(config)}/app/homepage"


def _q(value: str) -> str:
    """A string literal for the QuickBooks query language."""

    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


async def _get(ctx: Ctx, path: str, **params: str) -> dict[str, Any]:
    response = await ctx.call("GET", f"{_base(ctx)}/{path}", params={**params, "minorversion": MINOR_VERSION})
    return response.json()


async def _post(ctx: Ctx, path: str, body: dict[str, Any], **params: str) -> dict[str, Any]:
    response = await ctx.call(
        "POST", f"{_base(ctx)}/{path}", params={**params, "minorversion": MINOR_VERSION}, json=body, headers={"Content-Type": "application/json"}
    )
    return response.json()


async def query(ctx: Ctx, statement: str) -> dict[str, Any]:
    return (await _get(ctx, "query", query=statement)).get("QueryResponse") or {}


# ---------------------------------------------------------------------------
# Company and lists
# ---------------------------------------------------------------------------


async def company(ctx: Ctx) -> dict[str, Any]:
    realm = scope_id(ctx.config)
    info = (await _get(ctx, f"companyinfo/{realm}")).get("CompanyInfo") or {}
    prefs = (await _get(ctx, "preferences")).get("Preferences") or {}
    currency = prefs.get("CurrencyPrefs") or {}
    sales = prefs.get("SalesFormsPrefs") or {}
    tax = prefs.get("TaxPrefs") or {}
    return {
        "name": info.get("CompanyName") or info.get("LegalName"),
        "country": info.get("Country"),
        "currency": (currency.get("HomeCurrency") or {}).get("value"),
        "multi_currency": bool(currency.get("MultiCurrencyEnabled")),
        "custom_numbers": bool(sales.get("CustomTxnNumbers")),
        "automated_tax": bool(tax.get("PartnerTaxEnabled")),
        "uses_sales_tax": bool(tax.get("UsingSalesTax")),
    }


async def options(ctx: Ctx) -> dict[str, Any]:
    items = (await query(ctx, "select * from Item where Active = true maxresults 1000")).get("Item") or []
    codes = (await query(ctx, "select * from TaxCode where Active = true maxresults 200")).get("TaxCode") or []
    terms = (await query(ctx, "select * from Term where Active = true maxresults 100")).get("Term") or []
    return {
        "items": [
            {"id": str(i.get("Id")), "name": i.get("FullyQualifiedName") or i.get("Name"), "sku": i.get("Sku"), "type": i.get("Type")}
            for i in items
            if i.get("Type") in {"Service", "NonInventory", "Inventory"}
        ],
        "tax_codes": [{"id": str(c.get("Id")), "name": c.get("Name"), "taxable": bool(c.get("Taxable"))} for c in codes],
        "terms": [{"id": str(t.get("Id")), "name": t.get("Name")} for t in terms],
        "accounts": [],
    }


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def _customer(row: dict[str, Any], config: dict[str, Any]) -> RemoteCustomer:
    return RemoteCustomer(
        id=str(row.get("Id")), name=row.get("DisplayName") or row.get("CompanyName") or "", url=customer_url(config, str(row.get("Id"))),
        email=(row.get("PrimaryEmailAddr") or {}).get("Address"),
    )


def _display_name(name: str) -> str:
    # ":" separates sub-customers in QuickBooks names.
    return " ".join(name.replace(":", "-").split())[:500] or "Customer"


async def find_customer(ctx: Ctx, data: CustomerData) -> RemoteCustomer | None:
    if data.email:
        rows = (await query(ctx, f"select * from Customer where PrimaryEmailAddr = {_q(data.email)} maxresults 5")).get("Customer") or []
        active = [r for r in rows if r.get("Active", True)]
        if len(active) == 1:
            return _customer(active[0], ctx.config)
    rows = (await query(ctx, f"select * from Customer where DisplayName = {_q(_display_name(data.name))} maxresults 5")).get("Customer") or []
    if rows:
        return _customer(rows[0], ctx.config)
    return None


async def get_customer(ctx: Ctx, external_id: str) -> RemoteCustomer | None:
    try:
        row = (await _get(ctx, f"customer/{quote(external_id, safe='')}")).get("Customer") or {}
    except IntegrationError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    return _customer(row, ctx.config) if row.get("Id") else None


async def search_customers(ctx: Ctx, text: str) -> list[RemoteCustomer]:
    term = text.replace("%", "").strip()[:100]
    rows = (await query(ctx, f"select * from Customer where DisplayName like {_q('%' + term + '%')} maxresults 20")).get("Customer") or []
    return [_customer(r, ctx.config) for r in rows]


async def create_customer(ctx: Ctx, data: CustomerData, multi_currency: bool) -> RemoteCustomer:
    body: dict[str, Any] = {"DisplayName": _display_name(data.name), "CompanyName": data.name[:100]}
    if data.email:
        body["PrimaryEmailAddr"] = {"Address": data.email[:100]}
    if data.phone:
        body["PrimaryPhone"] = {"FreeFormNumber": data.phone[:30]}
    if data.website and data.website.startswith(("http://", "https://")):
        body["WebAddr"] = {"URI": data.website[:1000]}
    if data.first_name:
        body["GivenName"] = data.first_name[:100]
    if data.last_name:
        body["FamilyName"] = data.last_name[:100]
    address = {
        "Line1": data.line1, "Line2": data.line2, "City": data.city, "CountrySubDivisionCode": data.region,
        "PostalCode": data.postal_code, "Country": data.country,
    }
    if any(address.values()):
        body["BillAddr"] = {k: str(v)[:255] for k, v in address.items() if v}
    if multi_currency and data.currency:
        body["CurrencyRef"] = {"value": data.currency}
    try:
        row = (await _post(ctx, "customer", body)).get("Customer") or {}
    except IntegrationError as exc:
        # 6240 "Duplicate Name Exists": someone already has that display name.
        if "Duplicate" in str(exc) or "already" in str(exc):
            found = await find_customer(ctx, CustomerData(name=data.name))
            if found is not None:
                return found
        raise
    return _customer(row, ctx.config)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def _line_amounts(line: Any) -> tuple[Decimal, Decimal]:
    """(quantity, unit price) whose product is the line's net amount."""

    if line.discount_percent == 0:
        return line.quantity, line.unit_price
    if line.quantity:
        unit = (line.net / line.quantity).quantize(Decimal("0.00001"))
        if money(unit * line.quantity) == line.net:
            return line.quantity, unit
    return Decimal("1"), line.net


def invoice_body(inv: InvoiceData, customer_id: str, settings: dict[str, Any], company_info: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for index, line in enumerate(inv.lines, start=1):
        quantity, unit = _line_amounts(line)
        item = line.item_ref or settings.get("default_item")
        if not item:
            raise IntegrationError("Pick a default QuickBooks product/service on the QuickBooks page (each invoice line needs one).", 409)
        detail: dict[str, Any] = {"ItemRef": {"value": str(item)}, "Qty": float(quantity), "UnitPrice": float(unit)}
        code = settings.get("tax_code") if line.taxable else settings.get("tax_code_exempt")
        if code:
            detail["TaxCodeRef"] = {"value": str(code)}
        description = line.description or line.name
        if line.discount_percent:
            description = f"{description} (less {line.discount_percent.normalize():f}% discount)"
        lines.append({
            "LineNum": index, "DetailType": "SalesItemLineDetail", "Amount": float(line.net),
            "Description": (description or "")[:4000], "SalesItemLineDetail": detail,
        })
    body: dict[str, Any] = {
        "CustomerRef": {"value": customer_id},
        "TxnDate": inv.issue_date.isoformat(),
        "Line": lines,
        "PrivateNote": f"AIVA invoice {inv.reference}"[:4000],
    }
    if inv.due_date:
        body["DueDate"] = inv.due_date.isoformat()
    if inv.notes:
        body["CustomerMemo"] = {"value": inv.notes[:1000]}
    if settings.get("numbering") == "aiva" and company_info.get("custom_numbers"):
        body["DocNumber"] = inv.reference[:21]
    if settings.get("terms"):
        body["SalesTermRef"] = {"value": str(settings["terms"])}
    if company_info.get("multi_currency"):
        body["CurrencyRef"] = {"value": inv.currency}
    if inv.email:
        body["BillEmail"] = {"Address": inv.email[:100]}
    if company_info.get("country") not in (None, "US"):
        body["GlobalTaxCalculation"] = "TaxExcluded"
    return body


def _remote(row: dict[str, Any], config: dict[str, Any], payments: list[Payment] | None = None) -> RemoteInvoice:
    total = decimal(row.get("TotalAmt"))
    voided = total == 0 and str(row.get("PrivateNote") or "").startswith("Voided")
    currency = (row.get("CurrencyRef") or {}).get("value")
    return RemoteInvoice(
        id=str(row.get("Id")), number=row.get("DocNumber"), url=invoice_url(config, str(row.get("Id"))),
        total=money(total, currency), balance=money(row.get("Balance"), currency), currency=currency,
        status="void" if voided else ("paid" if decimal(row.get("Balance")) <= 0 and total > 0 else "open"),
        payments=payments or [], due_date=parse_date(row.get("DueDate")), issue_date=parse_date(row.get("TxnDate")),
        meta={"sync_token": str(row.get("SyncToken") or "0")},
    )


async def push_invoice(
    ctx: Ctx, inv: InvoiceData, customer_id: str, existing_id: str | None, meta: dict[str, Any], request_id: str, company_info: dict[str, Any]
) -> RemoteInvoice:
    body = invoice_body(inv, customer_id, ctx.settings, company_info)
    if existing_id:
        current = (await _get(ctx, f"invoice/{quote(existing_id, safe='')}")).get("Invoice") or {}
        if decimal(current.get("TotalAmt")) == 0 and str(current.get("PrivateNote") or "").startswith("Voided"):
            raise IntegrationError("This invoice was voided in QuickBooks, so AIVA won't change it.", 409)
        # Sparse: fields AIVA doesn't manage (custom fields, messages added in QuickBooks) are kept; lines are replaced.
        body.update({"Id": existing_id, "SyncToken": str(current.get("SyncToken") or meta.get("sync_token") or "0"), "sparse": True})
        row = (await _post(ctx, "invoice", body, requestid=request_id)).get("Invoice") or {}
    else:
        row = (await _post(ctx, "invoice", body, requestid=request_id)).get("Invoice") or {}
    if not row.get("Id"):
        raise IntegrationError("QuickBooks didn't return the invoice it created.", 502)
    return _remote(row, ctx.config)


async def _payment(ctx: Ctx, payment_id: str, invoice_id: str) -> Payment | None:
    row = (await _get(ctx, f"payment/{quote(payment_id, safe='')}")).get("Payment") or {}
    if not row:
        return None
    applied = Decimal("0")
    for line in row.get("Line") or []:
        for linked in line.get("LinkedTxn") or []:
            if linked.get("TxnType") == "Invoice" and str(linked.get("TxnId")) == invoice_id:
                applied += decimal(line.get("Amount"))
    currency = (row.get("CurrencyRef") or {}).get("value")
    return Payment(
        id=str(row.get("Id")), amount=money(applied or row.get("TotalAmt"), currency), date=parse_date(row.get("TxnDate")),
        reference=row.get("PaymentRefNum"), method=(row.get("PaymentMethodRef") or {}).get("name"), currency=currency,
    )


async def get_invoice(ctx: Ctx, external_id: str, *, with_payments: bool = True) -> RemoteInvoice | None:
    try:
        row = (await _get(ctx, f"invoice/{quote(external_id, safe='')}")).get("Invoice") or {}
    except IntegrationError as exc:
        if "Object Not Found" in str(exc) or "not found" in str(exc).lower():
            return None
        raise
    if not row:
        return None
    payments: list[Payment] = []
    if with_payments:
        ids = [str(t.get("TxnId")) for t in row.get("LinkedTxn") or [] if t.get("TxnType") == "Payment"][:MAX_PAYMENTS_READ]
        for payment_id in ids:
            found = await _payment(ctx, payment_id, str(row.get("Id")))
            if found is not None:
                payments.append(found)
    return _remote(row, ctx.config, payments)


async def void_invoice(ctx: Ctx, external_id: str, meta: dict[str, Any]) -> None:
    current = (await _get(ctx, f"invoice/{quote(external_id, safe='')}")).get("Invoice") or {}
    if not current:
        return
    if decimal(current.get("TotalAmt")) == 0 and str(current.get("PrivateNote") or "").startswith("Voided"):
        return
    if any(t.get("TxnType") == "Payment" for t in current.get("LinkedTxn") or []):
        raise IntegrationError("This invoice has payments in QuickBooks. Remove them there before voiding.", 409)
    await _post(ctx, "invoice", {"Id": external_id, "SyncToken": str(current.get("SyncToken") or "0")}, operation="void")


async def changed_since(ctx: Ctx, since: datetime) -> tuple[set[str], set[str]]:
    """(invoice ids, deleted payment ids) changed since ``since`` (QuickBooks keeps 30 days)."""

    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = await _get(ctx, "cdc", entities="Invoice,Payment", changedSince=stamp)
    invoices: set[str] = set()
    deleted_payments: set[str] = set()
    for block in data.get("CDCResponse") or []:
        for response in block.get("QueryResponse") or []:
            for row in response.get("Invoice") or []:
                if row.get("Id"):
                    invoices.add(str(row["Id"]))
            for row in response.get("Payment") or []:
                if row.get("status") == "Deleted":
                    deleted_payments.add(str(row.get("Id")))
                    continue
                for line in row.get("Line") or []:
                    for linked in line.get("LinkedTxn") or []:
                        if linked.get("TxnType") == "Invoice" and linked.get("TxnId"):
                            invoices.add(str(linked["TxnId"]))
    return invoices, deleted_payments


async def summary(ctx: Ctx, customer_id: str) -> Summary:
    customer = (await _get(ctx, f"customer/{quote(customer_id, safe='')}")).get("Customer") or {}
    currency = (customer.get("CurrencyRef") or {}).get("value")
    rows = (
        await query(ctx, f"select * from Invoice where CustomerRef = {_q(customer_id)} orderby TxnDate desc maxresults 20")
    ).get("Invoice") or []
    today = date.today()
    overdue = Decimal("0")
    overdue_count = 0
    invoices = []
    for row in rows:
        remote = _remote(row, ctx.config)
        due = remote.due_date
        if remote.status == "open" and remote.balance > 0 and due and due < today:
            overdue += remote.balance
            overdue_count += 1
        invoices.append({
            "id": remote.id, "number": remote.number, "date": remote.issue_date.isoformat() if remote.issue_date else None,
            "due_date": due.isoformat() if due else None, "total": str(remote.total), "balance": str(remote.balance),
            "status": "overdue" if remote.status == "open" and remote.balance > 0 and due and due < today else remote.status,
            "currency": remote.currency, "url": remote.url,
        })
    return Summary(currency=currency, open_balance=money(customer.get("Balance"), currency), overdue_balance=money(overdue, currency),
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
    """[(realm id, entity name, entity id)] from either webhook format.

    Classic: ``{"eventNotifications": [{"realmId", "dataChangeEvent": {"entities": [{"name", "id"}]}}]}``.
    CloudEvents: ``[{"type": "qbo.payment.created.v1", "intuitaccountid", "intuitentityid"}]``.
    """

    found: list[tuple[str, str, str]] = []
    if isinstance(payload, dict) and isinstance(payload.get("eventNotifications"), list):
        for note in payload["eventNotifications"]:
            realm = str((note or {}).get("realmId") or "")
            for entity in ((note or {}).get("dataChangeEvent") or {}).get("entities") or []:
                if realm and entity.get("name") and entity.get("id"):
                    found.append((realm, str(entity["name"]), str(entity["id"])))
    events = payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) and payload.get("specversion") else [])
    for event in events:
        if not isinstance(event, dict):
            continue
        realm = str(event.get("intuitaccountid") or "")
        entity_id = str(event.get("intuitentityid") or "")
        kind = str(event.get("type") or "")
        parts = kind.split(".")
        name = parts[1].capitalize() if len(parts) >= 3 else ""
        if realm and entity_id and name:
            found.append((realm, name, entity_id))
    return [f for f in found if f[0].isdigit()][:500]
