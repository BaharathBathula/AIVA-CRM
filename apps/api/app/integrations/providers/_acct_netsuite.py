"""Oracle NetSuite as the accounting engine's invoice target.

Customers, invoices, payments and balances, on top of ``accounting.py``:

* **Customers** are matched by AIVA's external ID (``AIVA-ACC-<account id>``),
  then by email, then by company name, within the chosen subsidiary; new ones
  are created in that subsidiary with the external ID, so an interrupted create
  is found again instead of duplicated.
* **Invoices** carry ``externalId = AIVA-INV-<invoice id>``. When an earlier
  attempt may have created one, it is looked up by that external ID first, and
  a "duplicate external ID" answer is treated the same way. Updates replace the
  item lines (``?replace=item``); paid or voided invoices are never changed.
* **Payments** are read back with SuiteQL from the invoice's applied
  transactions (``PreviousTransactionLineLink``, link type ``Payment``):
  customer payments and deposit applications. Credits reduce the balance like
  payments do (paid = total − amount remaining), as in the other adapters.
* **Voiding** isn't available through REST web services, so voiding in AIVA
  credits the invoice in full instead: the invoice is transformed
  (``POST /invoice/{id}/!transform/creditMemo``) into a credit memo
  (``AIVA-VOID-<id>``) applied to it. The invoice then reads as void here.
  Invoices with payments are left for the accountant.
* **Changes** are found with SuiteQL on ``lastmodifieddate`` (AIVA's invoices
  and the payments applied to them). NetSuite compares in the account's time
  zone, so the offset is measured with ``CURRENT_DATE`` when the company is read.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.integrations.core import IntegrationError
from app.integrations.providers import _ns_api as ns
from app.integrations.providers._acct_types import Ctx, CustomerData, InvoiceData, Line, Payment, RemoteCustomer, RemoteInvoice, Summary, decimal, money, parse_date
from app.models.integration_hub import IntegrationRecordLink

# Settings NetSuite adds to the accounting settings (kept in the same workspace config).
EXTRA_DEFAULTS: dict[str, Any] = {
    "location": None,  # NetSuite location on new invoices and sales orders
    "department": None,
    "class_id": None,
    "items_sync": "create",  # off | update (products that match by SKU) | create (also new products)
    "price_level": "1",  # price level whose price becomes the product's unit price (1 = Base Price)
    "stock_sync": True,  # stock on hand from NetSuite (needs inventory management in AIVA)
    "stock_location": None,  # a location's stock, or every location added up
    "orders_from": ["sales_order"],  # sales_order | quote | deal
    "order_mode": "auto",  # auto | manual
    "order_status": "A",  # A: pending approval | B: pending fulfillment
    "order_form": None,  # custom sales order form
    "order_status_back": True,  # fulfilled / cancelled in NetSuite updates the AIVA sales order
    "contacts_sync": True,  # contacts of linked accounts become NetSuite contacts on the customer
    "customers_from": "needed",  # needed (when an invoice or order goes) | customers (accounts at the customer stage)
}

PAYMENT_DOCS = {"CustPymt", "DepAppl"}  # customer payments, deposit applications
VOID_PREFIX = "AIVA-VOID"
INVOICE_PREFIX = "AIVA-INV"
CUSTOMER_PREFIX = "AIVA-ACC"
MAX_CHANGED = 1000
ITEM_TYPES = ("InvtPart", "NonInvtPart", "Service", "Kit", "Assembly", "OthCharge", "DwnLdItem")


def scope_id(config: dict[str, Any]) -> str:
    try:
        return ns.normalize_account(config.get("account_id"))
    except IntegrationError:
        return str(config.get("account_id") or "")


def home_url(config: dict[str, Any]) -> str:
    try:
        return f"{ns.app_base(config.get('account_id'))}/app/center/card.nl"
    except IntegrationError:
        return "https://system.netsuite.com"


def _account(ctx: Ctx) -> str:
    return scope_id(ctx.config)


def subsidiary(ctx: Ctx) -> str | None:
    value = ctx.config.get("subsidiary_id")
    return str(value) if value not in (None, "") and ns._ID.match(str(value)) else None


# ---------------------------------------------------------------------------
# Company and options
# ---------------------------------------------------------------------------


async def company(ctx: Ctx) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    clock = await ns.optional(ctx, "SELECT TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD HH24:MI:SS') AS now FROM dual", limit=1)
    offset = None
    if clock and clock[0].get("now"):
        try:
            local = datetime.strptime(str(clock[0]["now"])[:19], "%Y-%m-%d %H:%M:%S")
            minutes = (local - now.replace(tzinfo=None)).total_seconds() / 60
            offset = int(round(minutes / 15) * 15)  # time zones are whole quarter hours
        except ValueError:
            offset = None
    subsidiaries = await ns.optional(
        ctx, "SELECT s.id, s.name, s.parent, s.iselimination, c.symbol AS currency FROM subsidiary s LEFT JOIN currency c ON c.id = s.currency "
        "WHERE s.isinactive = 'F' ORDER BY s.id", limit=500,
    )
    currencies = await ns.optional(ctx, "SELECT c.id, c.symbol, c.isbasecurrency FROM currency c WHERE c.isinactive = 'F' ORDER BY c.id", limit=300)
    usable = [s for s in subsidiaries if not ns.flag(s.get("iselimination"))]
    chosen = subsidiary(ctx)
    current = next((s for s in usable if str(s.get("id")) == chosen), None) or next((s for s in usable if not s.get("parent")), None) or (usable[0] if usable else None)
    codes = {str(c["symbol"]).upper(): str(c["id"]) for c in currencies if c.get("symbol") and c.get("id")}
    base = next((str(c["symbol"]).upper() for c in currencies if ns.flag(c.get("isbasecurrency")) and c.get("symbol")), None)
    return {
        "name": (current or {}).get("name") or ctx.connection.account_label or f"NetSuite {_account(ctx)}",
        "account_id": _account(ctx),
        "sandbox": ns.is_sandbox(_account(ctx)),
        "currency": str((current or {}).get("currency") or base or "").upper() or None,
        "multi_currency": len(codes) > 1,
        "currencies": codes,
        "one_world": len(usable) > 1,
        "subsidiary_id": str(current["id"]) if current and current.get("id") else None,
        "subsidiaries": [{"id": str(s["id"]), "name": s.get("name") or str(s["id"])} for s in usable if s.get("id")][:200],
        "clock_offset": offset,
        "custom_numbers": True,  # NetSuite allows overriding numbers when the numbering preference allows it
    }


async def options(ctx: Ctx) -> dict[str, Any]:
    types = ", ".join(ns.text(t) for t in ITEM_TYPES)
    items = await ns.suiteql(
        ctx, f"SELECT i.id, i.itemid, i.displayname, i.itemtype FROM item i WHERE i.isinactive = 'F' AND i.itemtype IN ({types}) ORDER BY i.itemid",
        limit=1000,
    )
    taxes = await ns.optional(ctx, "SELECT t.id, t.itemid AS name, t.rate FROM salestaxitem t WHERE t.isinactive = 'F' ORDER BY t.itemid", limit=300)
    terms = await ns.optional(ctx, "SELECT t.id, t.name FROM term t WHERE t.isinactive = 'F' ORDER BY t.name", limit=200)
    locations = await ns.optional(ctx, "SELECT l.id, l.name FROM location l WHERE l.isinactive = 'F' ORDER BY l.name", limit=500)
    departments = await ns.optional(ctx, "SELECT d.id, d.name FROM department d WHERE d.isinactive = 'F' ORDER BY d.name", limit=500)
    classes = await ns.optional(ctx, "SELECT c.id, c.name FROM classification c WHERE c.isinactive = 'F' ORDER BY c.name", limit=500)
    levels = await ns.optional(ctx, "SELECT p.id, p.name FROM pricelevel p WHERE p.isinactive = 'F' ORDER BY p.id", limit=200)

    def pick(rows: list[dict[str, Any]], name: str = "name") -> list[dict[str, Any]]:
        return [{"id": str(r["id"]), "name": r.get(name) or str(r["id"])} for r in rows if r.get("id")]

    return {
        "items": [
            {"id": str(i["id"]), "name": i.get("displayname") or i.get("itemid") or str(i["id"]), "sku": i.get("itemid"), "type": i.get("itemtype")}
            for i in items if i.get("id")
        ],
        "tax_codes": [{"id": str(t["id"]), "name": t.get("name") or str(t["id"]), "rate": t.get("rate"), "taxable": True} for t in taxes if t.get("id")],
        "terms": pick(terms),
        "accounts": [],
        "locations": pick(locations),
        "departments": pick(departments),
        "classes": pick(classes),
        "price_levels": pick(levels),
    }


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

_CUSTOMER_COLUMNS = "c.id, c.entityid, c.companyname, c.email, c.isinactive, c.subsidiary"


def _customer(ctx: Ctx, row: dict[str, Any]) -> RemoteCustomer:
    name = row.get("companyname") or row.get("entityid") or str(row.get("id"))
    return RemoteCustomer(id=str(row["id"]), name=str(name), url=ns.ui_url(_account(ctx), "customer", str(row["id"])), email=row.get("email"))


def _in_subsidiary(ctx: Ctx) -> str:
    chosen = subsidiary(ctx)
    return f" AND c.subsidiary = {ns.ident(chosen)}" if chosen else ""


async def find_customer(ctx: Ctx, data: CustomerData) -> RemoteCustomer | None:
    if data.ref:
        found = await ns.suiteql(
            ctx, f"SELECT {_CUSTOMER_COLUMNS} FROM customer c WHERE c.externalid = {ns.text(ns.external_id(CUSTOMER_PREFIX, data.ref))}", limit=2,
        )
        if found:
            return _customer(ctx, found[0])
    if data.email:
        rows = await ns.suiteql(
            ctx, f"SELECT {_CUSTOMER_COLUMNS} FROM customer c WHERE LOWER(c.email) = {ns.text(data.email.strip().lower())} "
            f"AND c.isinactive = 'F'{_in_subsidiary(ctx)} ORDER BY c.id", limit=5,
        )
        if len(rows) == 1:
            return _customer(ctx, rows[0])
    name = " ".join((data.name or "").split())
    if name:
        rows = await ns.suiteql(
            ctx, f"SELECT {_CUSTOMER_COLUMNS} FROM customer c WHERE (UPPER(c.companyname) = UPPER({ns.text(name)}) OR UPPER(c.entityid) = UPPER({ns.text(name)})) "
            f"AND c.isinactive = 'F'{_in_subsidiary(ctx)} ORDER BY c.id", limit=5,
        )
        if rows:
            return _customer(ctx, rows[0])
    return None


async def get_customer(ctx: Ctx, external_id: str) -> RemoteCustomer | None:
    value = str(external_id or "").strip()
    if not ns._ID.match(value):
        return None
    rows = await ns.suiteql(ctx, f"SELECT {_CUSTOMER_COLUMNS} FROM customer c WHERE c.id = {ns.ident(value)}", limit=1)
    return _customer(ctx, rows[0]) if rows else None


async def search_customers(ctx: Ctx, text_value: str) -> list[RemoteCustomer]:
    term = ns.like(text_value.upper())
    rows = await ns.suiteql(
        ctx, f"SELECT {_CUSTOMER_COLUMNS} FROM customer c WHERE c.isinactive = 'F' AND (UPPER(c.companyname) LIKE {term} OR UPPER(c.entityid) LIKE {term} "
        f"OR UPPER(c.email) LIKE {term}){_in_subsidiary(ctx)} ORDER BY c.companyname", limit=20,
    )
    return [_customer(ctx, r) for r in rows if r.get("id")]


def _address(data: CustomerData) -> dict[str, Any] | None:
    address = {"addr1": data.line1, "addr2": data.line2, "city": data.city, "zip": data.postal_code}
    if data.region and len(data.region.strip()) == 2:
        address["state"] = data.region.strip().upper()
    address = {k: str(v).strip()[:150] for k, v in address.items() if v and str(v).strip()}
    if not address:
        return None
    return {"items": [{"defaultBilling": True, "defaultShipping": True, "addressBookAddress": address}]}


async def create_customer(ctx: Ctx, data: CustomerData, multi_currency: bool) -> RemoteCustomer:
    name = " ".join((data.name or "Customer").split())[:83]
    body: dict[str, Any] = {"companyName": name, "isPerson": False}
    if data.ref:
        body["externalId"] = ns.external_id(CUSTOMER_PREFIX, data.ref)
    if data.email:
        body["email"] = data.email.strip()[:254]
    if data.phone:
        body["phone"] = data.phone.strip()[:32]
    if data.website and data.website.startswith(("http://", "https://")):
        body["url"] = data.website[:999]
    info = await _company_cached(ctx)
    chosen = subsidiary(ctx) or (info.get("subsidiary_id") if not info.get("one_world") else None)
    if chosen:
        body["subsidiary"] = {"id": ns.ident(chosen)}
    elif info.get("one_world"):
        raise IntegrationError("Pick the NetSuite subsidiary new customers belong to (on the NetSuite page) before AIVA creates customers.", 409)
    code = (data.currency or "").upper()
    if multi_currency and code and (info.get("currencies") or {}).get(code):
        body["currency"] = {"id": str(info["currencies"][code])}
    address = _address(data)
    if address:
        body["addressBook"] = address
    try:
        new_id = await _create_entity(ctx, "customer", body, name)
    except IntegrationError as exc:
        if data.ref and ns.duplicate_external_id(exc):
            found = await find_customer(ctx, CustomerData(name=name, ref=data.ref))
            if found is not None:
                return found
        raise
    found = await get_customer(ctx, new_id)
    return found or RemoteCustomer(id=new_id, name=name, url=ns.ui_url(_account(ctx), "customer", new_id), email=data.email)


async def _create_entity(ctx: Ctx, record_type: str, body: dict[str, Any], name: str) -> str:
    """Create, giving the record a name (entity ID) only when the account doesn't number them itself."""

    try:
        return await ns.create_record(ctx, record_type, body)
    except IntegrationError as exc:
        if exc.status_code == 429 or not ns.needs_entity_id(exc) or "entityId" in body:
            raise
    return await ns.create_record(ctx, record_type, {**body, "entityId": name[:83]})


async def _company_cached(ctx: Ctx) -> dict[str, Any]:
    from app.integrations.providers import accounting

    return await accounting.company_info(ctx)


# ---------------------------------------------------------------------------
# Item lookup for lines
# ---------------------------------------------------------------------------


async def item_ids(ctx: Ctx, product_ids: set[str]) -> dict[str, str]:
    """AIVA product id -> NetSuite item id, for products imported from (or linked to) NetSuite items."""

    valid = []
    for value in product_ids:
        try:
            valid.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not valid:
        return {}
    rows = (
        await ctx.db.execute(
            select(IntegrationRecordLink.record_id, IntegrationRecordLink.external_id).where(
                IntegrationRecordLink.organization_id == ctx.connection.organization_id, IntegrationRecordLink.provider == ns.KEY,
                IntegrationRecordLink.record_type == "product", IntegrationRecordLink.external_type == "item",
                IntegrationRecordLink.external_parent == scope_id(ctx.config), IntegrationRecordLink.record_id.in_(valid),
                IntegrationRecordLink.status == "linked",
            )
        )
    ).all()
    return {str(pid): str(eid) for pid, eid in rows}


def _line_amounts(line: Line) -> tuple[Decimal, Decimal]:
    """(quantity, rate) whose product is the line's net amount."""

    if line.discount_percent == 0:
        return line.quantity, line.unit_price
    if line.quantity:
        unit = (line.net / line.quantity).quantize(Decimal("0.00001"))
        if money(unit * line.quantity) == line.net:
            return line.quantity, unit
    return Decimal("1"), line.net


def _num(value: Decimal) -> float | int:
    return int(value) if value == value.to_integral_value() else float(value)


async def item_lines(ctx: Ctx, lines: list[Line], settings: dict[str, Any]) -> list[dict[str, Any]]:
    linked = await item_ids(ctx, {ln.product_id for ln in lines if ln.product_id})
    out = []
    for line in lines:
        item = line.item_ref or (linked.get(line.product_id or "")) or settings.get("default_item")
        if not item:
            raise IntegrationError(
                f"“{line.name}” has no NetSuite item. Pick a default item on the NetSuite page, map the product, or import items from NetSuite.", 409
            )
        quantity, rate = _line_amounts(line)
        description = line.description or line.name
        if line.discount_percent:
            description = f"{description} (less {line.discount_percent.normalize():f}% discount)"
        row: dict[str, Any] = {
            "item": {"id": ns.ident(item)}, "quantity": _num(quantity), "rate": _num(rate), "amount": _num(line.net), "description": description[:999],
        }
        code = settings.get("tax_code") if line.taxable else settings.get("tax_code_exempt")
        if code:
            row["taxCode"] = {"id": ns.ident(code)}
        for name, field in (("location", "location"), ("department", "department"), ("class_id", "class")):
            if settings.get(name):
                row[field] = {"id": ns.ident(settings[name])}
        out.append(row)
    return out


def classification(settings: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, field in (("location", "location"), ("department", "department"), ("class_id", "class")):
        if settings.get(name):
            out[field] = {"id": ns.ident(settings[name])}
    return out


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


async def invoice_body(ctx: Ctx, inv: InvoiceData, customer_id: str, company_info: dict[str, Any]) -> dict[str, Any]:
    settings = ctx.settings
    body: dict[str, Any] = {
        "entity": {"id": ns.ident(customer_id)},
        "tranDate": inv.issue_date.isoformat(),
        "memo": f"AIVA invoice {inv.reference}"[:999],
        "item": {"items": await item_lines(ctx, inv.lines, settings)},
        **classification(settings),
    }
    if inv.due_date:
        body["dueDate"] = inv.due_date.isoformat()
    if settings.get("numbering") == "aiva":
        body["tranId"] = inv.reference[:45]
    if settings.get("terms"):
        body["terms"] = {"id": ns.ident(settings["terms"])}
    currency_id = (company_info.get("currencies") or {}).get(inv.currency.upper())
    if company_info.get("multi_currency") and currency_id:
        body["currency"] = {"id": str(currency_id)}
    if inv.notes:
        body["message"] = inv.notes[:999]
    return body


_INVOICE_QUERY = (
    "SELECT t.id, t.tranid, t.status, t.voided, t.foreigntotal, t.foreignamountunpaid, c.symbol AS currency, t.entity, t.externalid, "
    "TO_CHAR(t.trandate, 'YYYY-MM-DD') AS trandate, TO_CHAR(t.duedate, 'YYYY-MM-DD') AS duedate, "
    "l.linktype AS link_type, l.foreignamount AS link_amount, p.id AS link_id, p.type AS link_doc, p.tranid AS link_number, "
    "p.externalid AS link_external, TO_CHAR(p.trandate, 'YYYY-MM-DD') AS link_date "
    "FROM transaction t LEFT JOIN currency c ON c.id = t.currency "
    "LEFT JOIN previoustransactionlinelink l ON l.previousdoc = t.id "
    "LEFT JOIN transaction p ON p.id = l.nextdoc "
    "WHERE t.type = 'CustInvc' AND t.id = {id}"
)


def _status(row: dict[str, Any], total: Decimal, balance: Decimal, voided_by_aiva: bool) -> str:
    code = str(row.get("status") or "").split(":")[-1].upper()
    if ns.flag(row.get("voided")) or code == "V" or voided_by_aiva:
        return "void"
    if code in {"D", "E"}:  # pending approval, rejected
        return "draft"
    if total > 0 and balance <= 0:
        return "paid"
    return "open"


def remote_invoice(ctx: Ctx, rows: list[dict[str, Any]]) -> RemoteInvoice | None:
    if not rows or not rows[0].get("id"):
        return None
    head = rows[0]
    currency = str(head.get("currency") or "").upper() or None
    total = money(head.get("foreigntotal"), currency)
    balance = money(head.get("foreignamountunpaid"), currency)
    applied: dict[str, dict[str, Any]] = {}
    voided_by_aiva = False
    for row in rows:
        doc = str(row.get("link_doc") or "")
        if doc == "CustCred" and str(row.get("link_external") or "").startswith(f"{VOID_PREFIX}-"):
            voided_by_aiva = True
        if doc not in PAYMENT_DOCS or str(row.get("link_type") or "").lower() != "payment" or not row.get("link_id"):
            continue
        entry = applied.setdefault(str(row["link_id"]), {"amount": Decimal("0"), "row": row})
        entry["amount"] += abs(decimal(row.get("link_amount")))
    payments = [
        Payment(
            id=pid, amount=money(entry["amount"], currency), date=parse_date(entry["row"].get("link_date")),
            reference=entry["row"].get("link_number"), method="Deposit" if entry["row"].get("link_doc") == "DepAppl" else None, currency=currency,
        )
        for pid, entry in applied.items()
    ]
    status = _status(head, total, balance, voided_by_aiva)
    return RemoteInvoice(
        id=str(head["id"]), number=head.get("tranid"), url=ns.ui_url(_account(ctx), "invoice", str(head["id"])), total=total,
        balance=money(0, currency) if status == "void" else balance, currency=currency, status=status, payments=payments,
        due_date=parse_date(head.get("duedate")), issue_date=parse_date(head.get("trandate")),
        meta={"customer_id": str(head.get("entity") or "") or None},
    )


async def get_invoice(ctx: Ctx, external_id: str) -> RemoteInvoice | None:
    value = str(external_id or "")
    if not ns._ID.match(value):
        return None
    rows = await ns.suiteql(ctx, _INVOICE_QUERY.format(id=ns.ident(value)), limit=1000)
    return remote_invoice(ctx, rows)


async def push_invoice(
    ctx: Ctx, inv: InvoiceData, customer_id: str, existing_id: str | None, meta: dict[str, Any], request_id: str, company_info: dict[str, Any]
) -> RemoteInvoice:
    del request_id  # NetSuite's idempotency is the external ID
    body = await invoice_body(ctx, inv, customer_id, company_info)
    ref = ns.external_id(INVOICE_PREFIX, inv.id)
    target = existing_id
    if target is None and meta.get("attempted"):
        target = await ns.find_by_external_id(ctx, "invoice", ref)
    if target is None:
        try:
            target = await ns.create_record(ctx, "invoice", {**body, "externalId": ref})
        except IntegrationError as exc:
            if exc.status_code == 429 or not ns.duplicate_external_id(exc):
                raise
            target = await ns.find_by_external_id(ctx, "invoice", ref)
            if target is None:
                raise
            await _update_invoice(ctx, target, body)
    else:
        await _update_invoice(ctx, target, body)
    remote = await get_invoice(ctx, target)
    if remote is None:
        raise IntegrationError("NetSuite didn't return the invoice it saved.", 502)
    return remote


async def _update_invoice(ctx: Ctx, invoice_id: str, body: dict[str, Any]) -> None:
    current = await get_invoice(ctx, invoice_id)
    if current is None:
        raise IntegrationError("That invoice no longer exists in NetSuite.", 409)
    if current.status == "void":
        raise IntegrationError("This invoice was voided in NetSuite, so AIVA won't change it.", 409)
    if current.payments or current.status == "paid":
        raise IntegrationError("This invoice has payments in NetSuite, so AIVA won't change its lines.", 409)
    await ns.update_record(ctx, "invoice", invoice_id, body, replace=("item",))


async def void_invoice(ctx: Ctx, external_id: str, meta: dict[str, Any]) -> None:
    current = await get_invoice(ctx, external_id)
    if current is None or current.status == "void":
        return
    if current.payments:
        raise IntegrationError("This invoice has payments in NetSuite. Ask your accountant to reverse them there before it's credited.", 409)
    if current.balance != current.total:
        # Something else (a credit memo, a write-off) already reduced it. NetSuite's transform copies every
        # invoice line, so a credit memo now would credit the full total and leave the difference as an
        # unapplied credit on the customer: the accountant voids or finishes crediting it in NetSuite.
        raise IntegrationError(
            f"This invoice was already partly credited in NetSuite (open balance {current.balance} of {current.total}). "
            "Credit the rest there, so the customer isn't credited twice.",
            409,
        )
    ref = ns.external_id(VOID_PREFIX, current.id)
    if await ns.find_by_external_id(ctx, "creditMemo", ref):
        # Credited before but the invoice still reads open: the credit memo isn't applied to it.
        raise IntegrationError("AIVA's credit memo for this invoice exists in NetSuite but isn't applied to it. Apply it there to finish voiding.", 409)
    body = {
        "externalId": ref, "memo": "Voided in AIVA", "toBeEmailed": False,
        # Apply the whole credit to the invoice it came from (as NetSuite's own transform example does).
        "apply": {"items": [{"doc": {"id": current.id}, "apply": True, "amount": float(current.balance)}]},
    }
    try:
        await ns.transform(ctx, "invoice", current.id, "creditMemo", body)
    except IntegrationError as exc:
        if exc.status_code == 429 or not ns.duplicate_external_id(exc):
            raise


async def changed_since(ctx: Ctx, since: datetime) -> tuple[set[str], set[str]]:
    """(invoice ids, deleted payment ids) changed since ``since``: AIVA's invoices, and those payments were applied to."""

    info = await _company_cached(ctx)
    after = ns.stamp(ns.local_time(info, since))
    prefix = ns.text(f"{INVOICE_PREFIX}-%")
    invoices = await ns.suiteql(
        ctx, f"SELECT t.id FROM transaction t WHERE t.type = 'CustInvc' AND t.externalid LIKE {prefix} AND t.lastmodifieddate >= {after} ORDER BY t.id",
        limit=MAX_CHANGED,
    )
    applied = await ns.suiteql(
        ctx, "SELECT DISTINCT l.previousdoc AS id FROM transaction p JOIN previoustransactionlinelink l ON l.nextdoc = p.id "
        f"JOIN transaction t ON t.id = l.previousdoc WHERE p.type IN ('CustPymt', 'DepAppl', 'CustCred') AND t.type = 'CustInvc' "
        f"AND t.externalid LIKE {prefix} AND p.lastmodifieddate >= {after}",
        limit=MAX_CHANGED,
    )
    found = {str(r["id"]) for r in invoices + applied if r.get("id")}
    return found, set()


async def summary(ctx: Ctx, customer_id: str) -> Summary:
    customer = ns.ident(customer_id)
    recent = await ns.suiteql(
        ctx, "SELECT t.id, t.tranid, t.status, t.voided, t.foreigntotal, t.foreignamountunpaid, c.symbol AS currency, "
        "TO_CHAR(t.trandate, 'YYYY-MM-DD') AS trandate, TO_CHAR(t.duedate, 'YYYY-MM-DD') AS duedate "
        f"FROM transaction t LEFT JOIN currency c ON c.id = t.currency WHERE t.type = 'CustInvc' AND t.entity = {customer} "
        "ORDER BY t.trandate DESC, t.id DESC", limit=20,
    )
    open_rows = await ns.suiteql(
        ctx, "SELECT t.id, t.foreignamountunpaid, c.symbol AS currency, TO_CHAR(t.duedate, 'YYYY-MM-DD') AS duedate "
        f"FROM transaction t LEFT JOIN currency c ON c.id = t.currency WHERE t.type = 'CustInvc' AND t.entity = {customer} "
        "AND t.voided = 'F' AND t.foreignamountunpaid > 0", limit=1000,
    )
    today = date.today()
    totals: dict[str | None, list[Decimal]] = {}
    overdue_count = 0
    for row in open_rows:
        currency = str(row.get("currency") or "").upper() or None
        remaining = money(row.get("foreignamountunpaid"), currency)
        bucket = totals.setdefault(currency, [Decimal("0"), Decimal("0")])
        bucket[0] += remaining
        due = parse_date(row.get("duedate"))
        if due and due < today:
            bucket[1] += remaining
            overdue_count += 1
    currency = max(totals, key=lambda c: totals[c][0]) if totals else (str(recent[0].get("currency") or "").upper() or None if recent else None)
    open_balance, overdue = totals.get(currency, [Decimal("0"), Decimal("0")])
    invoices = []
    for row in recent:
        remote = remote_invoice(ctx, [row])
        if remote is None:
            continue
        due = remote.due_date
        late = remote.status == "open" and remote.balance > 0 and due is not None and due < today
        invoices.append({
            "id": remote.id, "number": remote.number, "date": remote.issue_date.isoformat() if remote.issue_date else None,
            "due_date": due.isoformat() if due else None, "total": str(remote.total), "balance": str(remote.balance),
            "status": "overdue" if late else remote.status, "currency": remote.currency, "url": remote.url,
        })
    return Summary(currency=currency, open_balance=money(open_balance, currency), overdue_balance=money(overdue, currency),
                   overdue_count=overdue_count, invoices=invoices[:10])
