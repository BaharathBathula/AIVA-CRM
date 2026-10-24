"""Sage Accounting (API v3.1).

* Business: ``business_id`` from the sign-in (the ``X-Business`` header);
  someone in several businesses picks one on the Sage page.
* Customers: contacts of type ``CUSTOMER``, matched by email, then name.
* Sales invoices: ``POST /sales_invoices`` with the sales ledger account and
  tax rate chosen on the Sage page; each line carries AIVA's discount and tax
  amount so the totals match. Sage has no idempotency key, so after an
  interrupted attempt AIVA looks for the invoice by its reference first.
* Payments: the invoice's ``payments_allocations``; changes are found with
  ``updated_or_created_since`` (Sage sends no webhooks).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from app.integrations.core import IntegrationError
from app.integrations.providers._acct_types import (
    CustomerData, Ctx, InvoiceData, Payment, RemoteCustomer, RemoteInvoice, Summary, money, parse_date,
)

KEY = "sage_accounting"
BASE = "https://api.accounting.sage.com/v3.1"
MAX_CHANGED_PAGES = 3


def scope_id(config: dict[str, Any]) -> str:
    return str(config.get("business_id") or "")


def _headers(ctx: Ctx, extra: dict[str, str] | None = None) -> dict[str, str]:
    business = scope_id(ctx.config)
    if not business:
        raise IntegrationError("Sage didn't say which business to use. Reconnect Sage Accounting.", 409)
    return {"X-Business": business, **(extra or {})}


def invoice_url(config: dict[str, Any], external_id: str) -> str | None:
    return None


def customer_url(config: dict[str, Any], external_id: str) -> str | None:
    return None


def home_url(config: dict[str, Any]) -> str:
    return "https://app.accounting.sage.com/"


def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        rows = data.get("$items")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


async def _get(ctx: Ctx, path: str, params: dict[str, str] | None = None) -> Any:
    response = await ctx.call("GET", f"{BASE}/{path}", params=params or {}, headers=_headers(ctx))
    return response.json() if response.content else {}


async def _send(ctx: Ctx, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await ctx.call(method, f"{BASE}/{path}", json=body, headers=_headers(ctx, {"Content-Type": "application/json"}))
    return response.json() if response.content else {}


# ---------------------------------------------------------------------------
# Business and lists
# ---------------------------------------------------------------------------


async def company(ctx: Ctx) -> dict[str, Any]:
    business = await _get(ctx, f"businesses/{quote(scope_id(ctx.config), safe='')}")
    settings = await _get(ctx, "financial_settings")
    return {
        "name": (business or {}).get("name") or (business or {}).get("displayed_as"),
        "country": ((business or {}).get("address") or {}).get("country", {}).get("id") if isinstance((business or {}).get("address"), dict) else None,
        "currency": ((settings or {}).get("base_currency") or {}).get("id"),
        "multi_currency": bool((settings or {}).get("multi_currency_enabled")),
        "custom_numbers": False,
    }


async def options(ctx: Ctx) -> dict[str, Any]:
    ledgers = _items(await _get(ctx, "ledger_accounts", {"visible_in": "sales", "items_per_page": "200"}))
    rates = _items(await _get(ctx, "tax_rates", {"usage": "sales", "items_per_page": "100", "attributes": "percentage"}))
    return {
        "items": [],
        "accounts": [{"id": a.get("id"), "name": a.get("displayed_as")} for a in ledgers if a.get("id")],
        "tax_codes": [
            {"id": r.get("id"), "name": r.get("displayed_as"), "rate": r.get("percentage")} for r in rates if r.get("id")
        ],
        "terms": [],
    }


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def _customer(row: dict[str, Any]) -> RemoteCustomer:
    return RemoteCustomer(id=str(row.get("id")), name=row.get("name") or row.get("displayed_as") or "", url=None, email=row.get("email"))


async def get_customer(ctx: Ctx, external_id: str) -> RemoteCustomer | None:
    try:
        row = await _get(ctx, f"contacts/{quote(external_id, safe='')}", {"attributes": "name,email"})
    except IntegrationError as exc:
        if "not found" in str(exc).lower() or "RecordNotFound" in str(exc):
            return None
        raise
    return _customer(row) if isinstance(row, dict) and row.get("id") else None


async def search_customers(ctx: Ctx, text: str) -> list[RemoteCustomer]:
    rows = _items(await _get(ctx, "contacts", {"contact_type_id": "CUSTOMER", "search": text.strip()[:100], "items_per_page": "20",
                                               "attributes": "name,email"}))
    return [_customer(r) for r in rows]


async def find_customer(ctx: Ctx, data: CustomerData) -> RemoteCustomer | None:
    if data.email:
        rows = _items(await _get(ctx, "contacts", {"contact_type_id": "CUSTOMER", "search": data.email, "items_per_page": "5", "attributes": "name,email"}))
        by_email = [r for r in rows if (r.get("email") or "").lower() == data.email.lower()]
        if len(by_email) == 1:
            return _customer(by_email[0])
    rows = _items(await _get(ctx, "contacts", {"contact_type_id": "CUSTOMER", "search": data.name[:100], "items_per_page": "20", "attributes": "name,email"}))
    exact = [r for r in rows if (r.get("name") or r.get("displayed_as") or "").strip().lower() == data.name.strip().lower()]
    return _customer(exact[0]) if exact else None


async def create_customer(ctx: Ctx, data: CustomerData, multi_currency: bool) -> RemoteCustomer:
    contact: dict[str, Any] = {"name": data.name[:100], "contact_type_ids": ["CUSTOMER"]}
    address = {"address_line_1": data.line1, "address_line_2": data.line2, "city": data.city, "region": data.region, "postal_code": data.postal_code}
    if any(address.values()):
        contact["main_address"] = {k: str(v)[:100] for k, v in address.items() if v}
        if data.country and len(data.country) == 2:
            contact["main_address"]["country_id"] = data.country.upper()
    person = {"name": " ".join(x for x in (data.first_name, data.last_name) if x) or data.name, "email": data.email, "telephone": data.phone}
    if data.email or data.phone:
        contact["main_contact_person"] = {k: str(v)[:100] for k, v in person.items() if v}
    if multi_currency and data.currency:
        contact["currency_id"] = data.currency
    row = await _send(ctx, "POST", "contacts", {"contact": contact})
    if not row.get("id"):
        raise IntegrationError("Sage didn't return the customer it created.", 502)
    return _customer(row)


# ---------------------------------------------------------------------------
# Sales invoices
# ---------------------------------------------------------------------------


def invoice_body(inv: InvoiceData, customer_id: str, settings: dict[str, Any], company_info: dict[str, Any]) -> dict[str, Any]:
    if not settings.get("income_account"):
        raise IntegrationError("Pick the Sage sales ledger account for invoice lines on the Sage page.", 409)
    lines = []
    for line in inv.lines:
        entry: dict[str, Any] = {
            "description": (line.description or line.name or "Item")[:200],
            "ledger_account_id": str(settings["income_account"]),
            "quantity": float(line.quantity),
            "unit_price": float(line.unit_price),
        }
        if line.discount:
            entry["discount_amount"] = float(line.discount)
        rate = settings.get("tax_code") if line.taxable else settings.get("tax_code_exempt")
        if rate:
            entry["tax_rate_id"] = str(rate)
            entry["tax_amount"] = float(line.tax)
        lines.append(entry)
    invoice: dict[str, Any] = {
        "contact_id": customer_id,
        "date": inv.issue_date.isoformat(),
        "reference": inv.reference[:25],
        "invoice_lines": lines,
    }
    if inv.due_date:
        invoice["due_date"] = inv.due_date.isoformat()
    if inv.notes:
        invoice["notes"] = inv.notes[:2000]
    if company_info.get("multi_currency") and inv.currency and inv.currency != company_info.get("currency"):
        invoice["currency_id"] = inv.currency
    return {"sales_invoice": invoice}


_STATUS = {"DRAFT": "draft", "UNPAID": "open", "PART_PAID": "open", "PAID": "paid", "VOID": "void", "DISPUTED": "open"}


def _remote(row: dict[str, Any]) -> RemoteInvoice:
    currency = (row.get("currency") or {}).get("id") if isinstance(row.get("currency"), dict) else None
    status = _STATUS.get(str((row.get("status") or {}).get("id") if isinstance(row.get("status"), dict) else row.get("status") or ""), "open")
    payments = []
    for allocation in row.get("payments_allocations") or []:
        if not isinstance(allocation, dict) or not allocation.get("id"):
            continue
        payments.append(Payment(id=str(allocation["id"]), amount=money(allocation.get("amount"), currency), date=parse_date(allocation.get("date")),
                                reference=allocation.get("displayed_as") or allocation.get("reference"), currency=currency))
    return RemoteInvoice(
        id=str(row.get("id")), number=row.get("invoice_number") or row.get("displayed_as"), url=None,
        total=money(row.get("total_amount"), currency), balance=money(row.get("outstanding_amount"), currency) if status != "void" else Decimal("0"),
        currency=currency, status=status, payments=payments, due_date=parse_date(row.get("due_date")), issue_date=parse_date(row.get("date")),
    )


async def _recover(ctx: Ctx, inv: InvoiceData, customer_id: str) -> dict[str, Any] | None:
    rows = _items(await _get(ctx, "sales_invoices", {"contact_id": customer_id, "search": inv.reference, "items_per_page": "20",
                                                     "attributes": "reference,total_amount,outstanding_amount,status,currency,due_date,date,invoice_number"}))
    matches = [r for r in rows if (r.get("reference") or "") == inv.reference[:25]]
    return matches[0] if len(matches) == 1 else None


async def push_invoice(
    ctx: Ctx, inv: InvoiceData, customer_id: str, existing_id: str | None, meta: dict[str, Any], request_id: str, company_info: dict[str, Any]
) -> RemoteInvoice:
    body = invoice_body(inv, customer_id, ctx.settings, company_info)
    if existing_id:
        current = await get_invoice(ctx, existing_id, with_payments=True)
        if current is not None and current.status in {"paid", "void"}:
            raise IntegrationError(f"This invoice is {current.status} in Sage, so AIVA won't change it.", 409)
        if current is not None and current.payments:
            raise IntegrationError("This invoice has payments in Sage, so AIVA won't change it.", 409)
        row = await _send(ctx, "PUT", f"sales_invoices/{quote(existing_id, safe='')}", body)
    else:
        if meta.get("attempted"):
            recovered = await _recover(ctx, inv, customer_id)
            if recovered is not None:
                return _remote(recovered)
        row = await _send(ctx, "POST", "sales_invoices", body)
    if not row.get("id"):
        raise IntegrationError("Sage didn't return the invoice.", 502)
    return _remote(row)


async def get_invoice(ctx: Ctx, external_id: str, *, with_payments: bool = True) -> RemoteInvoice | None:
    try:
        row = await _get(ctx, f"sales_invoices/{quote(external_id, safe='')}")
    except IntegrationError as exc:
        if "not found" in str(exc).lower() or "RecordNotFound" in str(exc):
            return None
        raise
    return _remote(row) if isinstance(row, dict) and row.get("id") else None


async def void_invoice(ctx: Ctx, external_id: str, meta: dict[str, Any]) -> None:
    current = await get_invoice(ctx, external_id)
    if current is None or current.status == "void":
        return
    if current.payments:
        raise IntegrationError("This invoice has payments in Sage. Remove them there before voiding.", 409)
    await ctx.call(
        "DELETE", f"{BASE}/sales_invoices/{quote(external_id, safe='')}", params={"void_reason": "Voided in AIVA"}, headers=_headers(ctx), ok_statuses=(404,)
    )


async def changed_since(ctx: Ctx, since: datetime) -> tuple[set[str], set[str]]:
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found: set[str] = set()
    for page in range(1, MAX_CHANGED_PAGES + 1):
        data = await _get(ctx, "sales_invoices", {"updated_or_created_since": stamp, "items_per_page": "100", "page": str(page)})
        rows = _items(data)
        found.update(str(r["id"]) for r in rows if r.get("id"))
        if not isinstance(data, dict) or not data.get("$next"):
            break
    return found, set()


async def summary(ctx: Ctx, customer_id: str) -> Summary:
    contact = await _get(ctx, f"contacts/{quote(customer_id, safe='')}", {"attributes": "balance,currency"})
    currency = ((contact or {}).get("currency") or {}).get("id") if isinstance((contact or {}).get("currency"), dict) else None
    rows = _items(await _get(ctx, "sales_invoices", {"contact_id": customer_id, "items_per_page": "20",
                                                     "attributes": "total_amount,outstanding_amount,status,currency,due_date,date,invoice_number"}))
    today = date.today()
    overdue = Decimal("0")
    overdue_count = 0
    invoices = []
    for row in rows:
        remote = _remote(row)
        late = remote.status == "open" and remote.balance > 0 and remote.due_date is not None and remote.due_date < today
        if late:
            overdue += remote.balance
            overdue_count += 1
        invoices.append({
            "id": remote.id, "number": remote.number, "date": remote.issue_date.isoformat() if remote.issue_date else None,
            "due_date": remote.due_date.isoformat() if remote.due_date else None, "total": str(remote.total), "balance": str(remote.balance),
            "status": "overdue" if late else remote.status, "currency": remote.currency, "url": None,
        })
    invoices.sort(key=lambda i: i["date"] or "", reverse=True)
    return Summary(currency=currency, open_balance=money((contact or {}).get("balance"), currency), overdue_balance=money(overdue, currency),
                   overdue_count=overdue_count, invoices=invoices[:10])
