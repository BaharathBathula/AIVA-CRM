"""FreshBooks (Accounting API).

* Business: ``account_id`` from the sign-in; someone in several businesses picks
  one on the FreshBooks page.
* Clients: matched by email, then organisation name; created with the account's
  address.
* Invoices: ``POST /invoices/invoices``. FreshBooks lines are free-form, so each
  line carries its net (after-discount) price and AIVA's tax percentage under
  the chosen tax name. FreshBooks has no idempotency key, so before creating
  again after an interrupted attempt AIVA looks for the invoice it may have made.
* Payments: ``/payments/payments?search[invoiceid]=``. FreshBooks calls AIVA
  back on payment and invoice events (callbacks verified with
  ``X-FreshBooks-Hmac-SHA256``); changes are also found with ``updated_min``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.integrations.core import IntegrationError
from app.integrations.providers._acct_types import (
    CustomerData, Ctx, InvoiceData, Payment, RemoteCustomer, RemoteInvoice, Summary, money, parse_date,
)

KEY = "freshbooks"
API = "https://api.freshbooks.com"
EASTERN = ZoneInfo("America/New_York")  # FreshBooks reports "updated" times in US Eastern
CALLBACK_EVENTS = ("payment", "invoice")


def scope_id(config: dict[str, Any]) -> str:
    return str(config.get("account_id") or "")


def _base(ctx: Ctx) -> str:
    account = scope_id(ctx.config)
    if not account or not account.replace("-", "").isalnum():
        raise IntegrationError("FreshBooks didn't say which business to use. Reconnect FreshBooks.", 409)
    return f"{API}/accounting/account/{account}"


def invoice_url(config: dict[str, Any], external_id: str) -> str | None:
    return None  # FreshBooks has no stable deep link to an invoice for staff


def customer_url(config: dict[str, Any], external_id: str) -> str | None:
    return None


def home_url(config: dict[str, Any]) -> str:
    return "https://my.freshbooks.com/"


def _result(data: dict[str, Any]) -> dict[str, Any]:
    return ((data or {}).get("response") or {}).get("result") or {}


async def _get(ctx: Ctx, path: str, params: Any = None) -> dict[str, Any]:
    response = await ctx.call("GET", f"{_base(ctx)}/{path}", params=params or {})
    return _result(response.json())


async def _send(ctx: Ctx, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await ctx.call(method, f"{_base(ctx)}/{path}", json=body, headers={"Content-Type": "application/json"})
    return _result(response.json())


# ---------------------------------------------------------------------------
# Business and lists
# ---------------------------------------------------------------------------


async def company(ctx: Ctx) -> dict[str, Any]:
    response = await ctx.call("GET", f"{API}/auth/api/v1/users/me")
    info = (response.json() or {}).get("response") or {}
    account = scope_id(ctx.config)
    business = next(
        (m.get("business") or {} for m in info.get("business_memberships") or [] if str((m.get("business") or {}).get("account_id")) == account), {}
    )
    return {"name": business.get("name"), "country": (business.get("address") or {}).get("country"), "currency": None,
            "multi_currency": True, "custom_numbers": True}


async def options(ctx: Ctx) -> dict[str, Any]:
    taxes = (await _get(ctx, "taxes/taxes", {"per_page": "100"})).get("taxes") or []
    return {
        "items": [],
        "accounts": [],
        "tax_codes": [{"id": t.get("name"), "name": f"{t.get('name')} ({t.get('amount')}%)", "rate": t.get("amount")} for t in taxes if t.get("name")],
        "terms": [],
    }


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def _customer(row: dict[str, Any]) -> RemoteCustomer:
    name = row.get("organization") or " ".join(x for x in (row.get("fname"), row.get("lname")) if x) or row.get("email") or ""
    return RemoteCustomer(id=str(row.get("id") or row.get("userid")), name=name, url=None, email=row.get("email"))


async def get_customer(ctx: Ctx, external_id: str) -> RemoteCustomer | None:
    try:
        row = (await _get(ctx, f"users/clients/{quote(external_id, safe='')}")).get("client") or {}
    except IntegrationError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    return _customer(row) if (row.get("id") or row.get("userid")) else None


async def search_customers(ctx: Ctx, text: str) -> list[RemoteCustomer]:
    rows = (await _get(ctx, "users/clients", {"search[organization_like]": text.strip()[:100], "per_page": "20"})).get("clients") or []
    return [_customer(r) for r in rows if str(r.get("vis_state", 0)) == "0"]


async def find_customer(ctx: Ctx, data: CustomerData) -> RemoteCustomer | None:
    if data.email:
        rows = (await _get(ctx, "users/clients", {"search[email]": data.email, "per_page": "5"})).get("clients") or []
        rows = [r for r in rows if str(r.get("vis_state", 0)) == "0"]
        if len(rows) == 1:
            return _customer(rows[0])
    rows = (await _get(ctx, "users/clients", {"search[organization_like]": data.name[:100], "per_page": "20"})).get("clients") or []
    exact = [r for r in rows if (r.get("organization") or "").strip().lower() == data.name.strip().lower() and str(r.get("vis_state", 0)) == "0"]
    return _customer(exact[0]) if exact else None


async def create_customer(ctx: Ctx, data: CustomerData, multi_currency: bool) -> RemoteCustomer:
    client: dict[str, Any] = {"organization": data.name[:255]}
    for key, value in (
        ("email", data.email), ("fname", data.first_name), ("lname", data.last_name), ("bus_phone", data.phone),
        ("p_street", data.line1), ("p_street2", data.line2), ("p_city", data.city), ("p_province", data.region),
        ("p_code", data.postal_code), ("p_country", data.country), ("currency_code", data.currency),
    ):
        if value:
            client[key] = str(value)[:255]
    row = (await _send(ctx, "POST", "users/clients", {"client": client})).get("client") or {}
    if not (row.get("id") or row.get("userid")):
        raise IntegrationError("FreshBooks didn't return the client it created.", 502)
    return _customer(row)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def _line_amounts(line: Any) -> tuple[Decimal, Decimal]:
    if line.discount_percent == 0:
        return line.quantity, line.unit_price
    if line.quantity:
        unit = money(line.net / line.quantity)
        if money(unit * line.quantity) == line.net:
            return line.quantity, unit
    return Decimal("1"), line.net


def invoice_body(inv: InvoiceData, customer_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for line in inv.lines:
        quantity, unit = _line_amounts(line)
        entry: dict[str, Any] = {
            "type": 0,
            "name": (line.name or "Item")[:255],
            "description": (line.description or "")[:4000],
            "qty": str(quantity.normalize()),
            "unit_cost": {"amount": f"{unit:f}", "code": inv.currency},
        }
        if line.discount_percent:
            entry["description"] = f"{entry['description']} (less {line.discount_percent.normalize():f}% discount)".strip()
        if line.taxable:
            entry["taxName1"] = str(settings.get("tax_code") or "Tax")[:50]
            entry["taxAmount1"] = f"{line.tax_percent.normalize():f}"
        lines.append(entry)
    days = max(0, (inv.due_date - inv.issue_date).days) if inv.due_date else 0
    invoice: dict[str, Any] = {
        "customerid": int(customer_id) if str(customer_id).isdigit() else customer_id,
        "create_date": inv.issue_date.isoformat(),
        "due_offset_days": days,
        "currency_code": inv.currency,
        "lines": lines,
    }
    if inv.notes:
        invoice["notes"] = inv.notes[:4000]
    if inv.terms:
        invoice["terms"] = inv.terms[:4000]
    if settings.get("numbering") == "aiva":
        invoice["invoice_number"] = inv.reference[:30]
    return {"invoice": invoice}


def _status(row: dict[str, Any]) -> str:
    if str(row.get("vis_state", 0)) == "1":
        return "void"
    if str(row.get("v3_status") or row.get("status") or "").lower() == "draft":
        return "draft"
    if str(row.get("payment_status") or "").lower() == "paid":
        return "paid"
    return "open"


def _remote(row: dict[str, Any], payments: list[Payment] | None = None) -> RemoteInvoice:
    currency = row.get("currency_code") or (row.get("amount") or {}).get("code")
    total = money((row.get("amount") or {}).get("amount"), currency)
    outstanding = money((row.get("outstanding") or {}).get("amount"), currency)
    return RemoteInvoice(
        id=str(row.get("invoiceid") or row.get("id")), number=row.get("invoice_number"), url=None, total=total,
        balance=outstanding if _status(row) != "void" else Decimal("0"), currency=currency, status=_status(row), payments=payments or [],
        due_date=parse_date(row.get("due_date")), issue_date=parse_date(row.get("create_date")),
    )


async def _recover(ctx: Ctx, inv: InvoiceData, customer_id: str) -> dict[str, Any] | None:
    """An invoice a previous, interrupted attempt may have created (same client, day and amount)."""

    rows = (
        await _get(ctx, "invoices/invoices", {"search[customerid]": customer_id, "search[date_min]": inv.issue_date.isoformat(),
                                              "search[date_max]": inv.issue_date.isoformat(), "per_page": "20"})
    ).get("invoices") or []
    matches = [r for r in rows if money((r.get("amount") or {}).get("amount")) == inv.total and str(r.get("vis_state", 0)) == "0"]
    return matches[0] if len(matches) == 1 else None


async def push_invoice(
    ctx: Ctx, inv: InvoiceData, customer_id: str, existing_id: str | None, meta: dict[str, Any], request_id: str, company_info: dict[str, Any]
) -> RemoteInvoice:
    body = invoice_body(inv, customer_id, ctx.settings)
    if existing_id:
        current = await get_invoice(ctx, existing_id, with_payments=True)
        if current is not None and current.status in {"paid", "void"}:
            raise IntegrationError(f"This invoice is {current.status} in FreshBooks, so AIVA won't change it.", 409)
        if current is not None and current.payments:
            raise IntegrationError("This invoice has payments in FreshBooks, so AIVA won't change it.", 409)
        row = (await _send(ctx, "PUT", f"invoices/invoices/{quote(existing_id, safe='')}", body)).get("invoice") or {}
    else:
        if meta.get("attempted"):
            recovered = await _recover(ctx, inv, customer_id)
            if recovered is not None:
                return _remote(recovered)
        row = (await _send(ctx, "POST", "invoices/invoices", body)).get("invoice") or {}
    if not (row.get("invoiceid") or row.get("id")):
        raise IntegrationError("FreshBooks didn't return the invoice.", 502)
    return _remote(row)


async def _payments(ctx: Ctx, invoice_id: str, currency: str | None) -> list[Payment]:
    rows = (await _get(ctx, "payments/payments", {"search[invoiceid]": invoice_id, "per_page": "50"})).get("payments") or []
    return [
        Payment(id=str(r.get("id") or r.get("paymentid")), amount=money((r.get("amount") or {}).get("amount"), currency), date=parse_date(r.get("date")),
                reference=r.get("note"), method=r.get("type"), currency=(r.get("amount") or {}).get("code") or currency)
        for r in rows
        if str(r.get("vis_state", 0)) == "0" and (r.get("id") or r.get("paymentid"))
    ]


async def get_invoice(ctx: Ctx, external_id: str, *, with_payments: bool = True) -> RemoteInvoice | None:
    try:
        row = (await _get(ctx, f"invoices/invoices/{quote(external_id, safe='')}")).get("invoice") or {}
    except IntegrationError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    if not row:
        return None
    remote = _remote(row)
    if with_payments:
        remote.payments = await _payments(ctx, remote.id, remote.currency)
    return remote


async def void_invoice(ctx: Ctx, external_id: str, meta: dict[str, Any]) -> None:
    current = await get_invoice(ctx, external_id)
    if current is None or current.status == "void":
        return
    if current.payments:
        raise IntegrationError("This invoice has payments in FreshBooks. Remove them there before deleting it.", 409)
    await _send(ctx, "PUT", f"invoices/invoices/{quote(external_id, safe='')}", {"invoice": {"vis_state": 1}})


async def changed_since(ctx: Ctx, since: datetime) -> tuple[set[str], set[str]]:
    # FreshBooks filters on Eastern time; go back an extra hour for clock skew.
    stamp = (since - timedelta(hours=1)).astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
    invoices = (await _get(ctx, "invoices/invoices", {"search[updated_min]": stamp, "per_page": "100"})).get("invoices") or []
    found = {str(r.get("invoiceid") or r.get("id")) for r in invoices if r.get("invoiceid") or r.get("id")}
    payments = (await _get(ctx, "payments/payments", {"search[updated_min]": stamp, "per_page": "100"})).get("payments") or []
    found.update(str(p["invoiceid"]) for p in payments if p.get("invoiceid"))
    return found, set()


async def summary(ctx: Ctx, customer_id: str) -> Summary:
    rows = (await _get(ctx, "invoices/invoices", {"search[customerid]": customer_id, "per_page": "50", "sort": "create_date_desc"})).get("invoices") or []
    today = date.today()
    open_balance = overdue = Decimal("0")
    overdue_count = 0
    currency = None
    invoices = []
    for row in rows:
        remote = _remote(row)
        currency = currency or remote.currency
        if remote.status == "open":
            open_balance += remote.balance
        late = remote.status == "open" and remote.balance > 0 and remote.due_date is not None and remote.due_date < today
        if late:
            overdue += remote.balance
            overdue_count += 1
        invoices.append({
            "id": remote.id, "number": remote.number, "date": remote.issue_date.isoformat() if remote.issue_date else None,
            "due_date": remote.due_date.isoformat() if remote.due_date else None, "total": str(remote.total), "balance": str(remote.balance),
            "status": "overdue" if late else remote.status, "currency": remote.currency, "url": None,
        })
    return Summary(currency=currency, open_balance=money(open_balance, currency), overdue_balance=money(overdue, currency), overdue_count=overdue_count,
                   invoices=invoices[:10])


# ---------------------------------------------------------------------------
# Callbacks (webhooks)
# ---------------------------------------------------------------------------


def callbacks_url(account_id: str) -> str:
    return f"{API}/events/account/{account_id}/events/callbacks"


async def register_callbacks(ctx: Ctx, uri: str) -> dict[str, str]:
    """Ask FreshBooks to call ``uri`` on payment and invoice events. Returns {callback id: event}."""

    made: dict[str, str] = {}
    base = callbacks_url(scope_id(ctx.config))
    for event in CALLBACK_EVENTS:
        response = await ctx.call("POST", base, json={"callback": {"event": event, "uri": uri}}, headers={"Content-Type": "application/json"})
        row = _result(response.json()).get("callback") or {}
        if row.get("callbackid") or row.get("id"):
            made[str(row.get("callbackid") or row.get("id"))] = event
    return made


async def confirm_callback(ctx: Ctx, callback_id: str, verifier: str) -> bool:
    response = await ctx.call(
        "PUT", f"{callbacks_url(scope_id(ctx.config))}/{quote(callback_id, safe='')}",
        json={"callback": {"verifier": verifier}}, headers={"Content-Type": "application/json"}, ok_statuses=(400, 404, 422),
    )
    if response.status_code >= 400:
        return False
    row = _result(response.json()).get("callback") or {}
    return bool(row.get("verified", True))


async def delete_callback(ctx: Ctx, callback_id: str) -> None:
    await ctx.call("DELETE", f"{callbacks_url(scope_id(ctx.config))}/{quote(callback_id, safe='')}", ok_statuses=(404,))


def signature_ok(verifier: str | None, params: list[tuple[str, str]], header: str | None) -> bool:
    """``X-FreshBooks-Hmac-SHA256``: base64 HMAC-SHA256 of the parameters as Python's ``json.dumps``."""

    if not verifier or not header:
        return False
    candidates = [dict(params), dict(sorted(params))]
    for candidate in candidates:
        message = json.dumps({k: str(v) for k, v in candidate.items()})
        expected = base64.b64encode(hmac.new(verifier.encode(), message.encode("utf-8"), hashlib.sha256).digest())
        if hmac.compare_digest(expected, header.strip().encode()):
            return True
    return False

