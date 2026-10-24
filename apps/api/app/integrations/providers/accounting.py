"""Accounting: QuickBooks Online, Xero, FreshBooks, Sage Accounting and Oracle NetSuite.

One accounting system is the workspace's *invoice target* at a time: switching
one on switches the others off (their connections are kept). NetSuite is an ERP:
on top of what's described here it also syncs items, stock, contacts and sales
orders (``netsuite.py``, run from ``sync_connection``).

What syncs
==========

* **Customers.** An AIVA account becomes a customer the first time one of its
  invoices goes over: matched by the primary contact's email, then by name,
  otherwise created (``IntegrationRecordLink`` account ↔ customer). People can
  also link an account to an existing customer by hand.
* **Invoices.** Sent (or paid) AIVA invoices are created in the accounting
  system, automatically or when someone clicks *Send to …*. Changes are sent
  again (``sync_hash`` skips unchanged ones), and voiding in AIVA voids there.
  Creating is idempotent: QuickBooks gets a ``requestid``, Xero an
  ``Idempotency-Key``, and for FreshBooks and Sage an interrupted attempt is
  looked up before creating again. A pending link is saved before the call.
* **Payments.** Read back from the accounting system — never trusted from a
  webhook body. AIVA keeps track of how much of an invoice's paid amount came
  from the accounting system; the rest came from AIVA itself (Stripe, PayPal,
  Square, or recorded by hand). The invoice is paid up to the larger of the two
  views, so a card payment that the bookkeeper also records in the books is
  counted once, and removing a payment in the books only takes back what the
  accounting sync added.
  Each payment is kept as an ``IntegrationObject`` on the invoice; the account
  gets a timeline entry and the invoice owner a notification.
* **Balances.** Each linked account's open and overdue amounts (and recent
  invoices) are cached on its link and refreshed every few hours.

When
====

The scheduler (``run_due``) works through connected companies with a call and
time budget per company (Xero allows 60 calls a minute), and pauses a company
when the provider answers 429 (``Retry-After``). Webhooks (QuickBooks, Xero,
FreshBooks) only queue a nudge; the next run reads the changed invoices back.
Changes are also polled every few minutes, and open invoices re-checked every
few hours, so nothing is missed when webhooks aren't set up.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from types import ModuleType
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _acct_freshbooks, _acct_http as http, _acct_netsuite, _acct_quickbooks, _acct_sage, _acct_xero
from app.integrations.providers._acct_types import CustomerData, Ctx, InvoiceData, Line, RemoteInvoice, decimal, money
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink, IntegrationToggle
from app.models.platform import Notification, Product

logger = logging.getLogger("aiva.integrations.accounting")

KEYS = ("quickbooks", "xero", "freshbooks", "sage_accounting", "netsuite")
ADAPTERS: dict[str, ModuleType] = {
    "quickbooks": _acct_quickbooks,
    "xero": _acct_xero,
    "freshbooks": _acct_freshbooks,
    "sage_accounting": _acct_sage,
    "netsuite": _acct_netsuite,
}
FEATURE = "accounting_sync"
INVOICE = "sales_invoice"  # record_type of AIVA invoices on links and objects

# Calls each company may use per scheduler run (runs are ~30 seconds apart). NetSuite
# counts concurrent requests, not calls; AIVA sends one at a time per account.
RUN_CALLS = {"quickbooks": 120, "xero": 25, "freshbooks": 40, "sage_accounting": 40, "netsuite": 60}
RUN_SECONDS = 20.0
TOTAL_RUN_SECONDS = 45.0
MAX_CONNECTIONS_PER_RUN = 10
CHANGES_EVERY = timedelta(minutes=5)
SAFETY_EVERY = timedelta(hours=6)
ROLLUP_EVERY = timedelta(hours=6)
COMPANY_EVERY = timedelta(hours=24)
OPTIONS_TTL = timedelta(minutes=10)
PUSH_BATCH = 10
REFRESH_BATCH = 15
ROLLUP_BATCH = 5
MAX_NUDGES = 300
RETRY_STEPS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=12), timedelta(hours=24))
LOOKBACK_LIMIT = {"quickbooks": timedelta(days=29)}  # QuickBooks CDC keeps 30 days

DEFAULTS: dict[str, Any] = {
    "sync_mode": "auto",  # auto: send invoices when they're sent in AIVA | manual
    "sync_from": None,  # invoices issued before this date are only sent by hand
    "numbering": "provider",  # provider: the accounting system numbers invoices | aiva: use INV-1001
    "default_item": None,  # QuickBooks product/service for lines without a mapping
    "item_map": {},  # AIVA product id -> provider item (QuickBooks item id / Xero item code)
    "income_account": None,  # Xero sales account code / Sage sales ledger account id
    "tax_code": None,  # taxable lines: QuickBooks tax code / Xero tax type / Sage tax rate / FreshBooks tax name
    "tax_code_exempt": None,  # lines without tax
    "terms": None,  # QuickBooks payment terms
    "xero_status": "AUTHORISED",  # new Xero invoices: AUTHORISED (approved) or DRAFT
    "notify_owner": True,  # tell the invoice's owner when a payment arrives
    "create_customers": True,  # create a customer when no existing one matches
}
SERVER_WEBHOOK_KEYS = {  # the server's own Intuit / Xero apps' webhook keys
    "quickbooks": "QUICKBOOKS_WEBHOOK_VERIFIER_TOKEN",
    "xero": "XERO_WEBHOOK_KEY",
}
WORKSPACE_WEBHOOK_SECRET = "webhook_key"  # a workspace's own app's key (toggle secret)
MAX_BODY = 512 * 1024


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def name_of(key: str) -> str:
    return core.definition(key).name


def adapter(key: str) -> ModuleType:
    return ADAPTERS[key]


def scope_of(connection: IntegrationConnection) -> str:
    return adapter(connection.provider).scope_id(connection.config or {})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ===========================================================================
# Settings and per-connection state
# ===========================================================================


async def settings_for(db: AsyncSession, organization_id: uuid.UUID, key: str) -> dict[str, Any]:
    config = await core.workspace_config(db, organization_id, key)
    defaults = {**DEFAULTS, **getattr(ADAPTERS.get(key), "EXTRA_DEFAULTS", {})}
    merged = {name: config.get(name, default) for name, default in defaults.items()}
    if not isinstance(merged["item_map"], dict):
        merged["item_map"] = {}
    return merged


def state(connection: IntegrationConnection) -> dict[str, Any]:
    value = (connection.config or {}).get("sync")
    return dict(value) if isinstance(value, dict) else {}


async def patch_state(db: AsyncSession, connection: IntegrationConnection, **values: Any) -> None:
    """Merge into ``config.sync`` atomically, so a webhook and a sync run can't undo each other."""

    patch = json.loads(json.dumps(values, default=str))
    await db.execute(
        text(
            "UPDATE integration_connections SET config = jsonb_set(coalesce(config, '{}'::jsonb), '{sync}', "
            "coalesce(config->'sync', '{}'::jsonb) || CAST(:patch AS jsonb)) WHERE id = :id"
        ),
        {"patch": json.dumps(patch), "id": connection.id},
    )
    current = dict(connection.config or {})
    current["sync"] = {**state(connection), **patch}
    set_committed_value(connection, "config", current)


async def patch_config(db: AsyncSession, connection: IntegrationConnection, **values: Any) -> None:
    """Merge top-level keys into the connection's config atomically."""

    patch = json.loads(json.dumps(values, default=str))
    await db.execute(
        text("UPDATE integration_connections SET config = coalesce(config, '{}'::jsonb) || CAST(:patch AS jsonb) WHERE id = :id"),
        {"patch": json.dumps(patch), "id": connection.id},
    )
    set_committed_value(connection, "config", {**(connection.config or {}), **patch})


async def nudge(db: AsyncSession, connection: IntegrationConnection, items: list[str], check_now: bool = False) -> None:
    """Queue things a webhook said changed ("Invoice:123", "Payment:9"); the next run reads them back."""

    items = [i[:120] for i in dict.fromkeys(items)][:MAX_NUDGES]
    if items:
        await db.execute(
            text(
                "UPDATE integration_connections SET config = jsonb_set("
                "coalesce(config, '{}'::jsonb) || jsonb_build_object('sync', coalesce(config->'sync', '{}'::jsonb)), '{sync,nudges}', "
                "coalesce(config#>'{sync,nudges}', '[]'::jsonb) || CAST(:items AS jsonb), true) "
                "WHERE id = :id AND jsonb_array_length(coalesce(config#>'{sync,nudges}', '[]'::jsonb)) < :cap"
            ),
            {"items": json.dumps(items), "id": connection.id, "cap": MAX_NUDGES},
        )
    if check_now or items:
        await db.execute(
            text(
                "UPDATE integration_connections SET config = jsonb_set(coalesce(config, '{}'::jsonb), '{sync}', "
                "coalesce(config->'sync', '{}'::jsonb) || jsonb_build_object('check_now', true)) WHERE id = :id"
            ),
            {"id": connection.id},
        )


async def _take_nudges(db: AsyncSession, connection: IntegrationConnection) -> list[str]:
    row = (
        await db.execute(text("SELECT config#>'{sync,nudges}' FROM integration_connections WHERE id = :id FOR UPDATE"), {"id": connection.id})
    ).first()
    values = row[0] if row and isinstance(row[0], list) else []
    if values:
        await db.execute(text("UPDATE integration_connections SET config = config #- '{sync,nudges}' WHERE id = :id"), {"id": connection.id})
    current = dict(connection.config or {})
    sync = dict(current.get("sync") or {})
    sync.pop("nudges", None)
    current["sync"] = sync
    set_committed_value(connection, "config", current)
    return [str(v) for v in values if isinstance(v, str)][:MAX_NUDGES]


async def active_key(db: AsyncSession, organization_id: uuid.UUID) -> str | None:
    """The workspace's accounting system: switched on (and connected, when several are on)."""

    toggles = await core.toggles(db, organization_id)
    on = [k for k in KEYS if toggles.get(k) is not None and toggles[k].enabled]
    if len(on) <= 1:
        return on[0] if on else None
    for key in on:
        connection = await core.connection_for(db, organization_id, key)
        if connection is not None and connection.status == "connected":
            return key
    return on[0]


async def connection_of(db: AsyncSession, organization_id: uuid.UUID, key: str | None = None) -> IntegrationConnection:
    key = key or await active_key(db, organization_id)
    if key is None:
        raise IntegrationError("No accounting system is turned on. An admin can turn one on in Integrations.", 409)
    return await core.active_connection(db, organization_id, key)


async def context(db: AsyncSession, connection: IntegrationConnection, budget: http.Budget | None = None) -> Ctx:
    return Ctx(db=db, connection=connection, settings=await settings_for(db, connection.organization_id, connection.provider), budget=budget or http.unlimited())


async def company_info(ctx: Ctx, *, refresh: bool = False) -> dict[str, Any]:
    current = state(ctx.connection)
    cached = current.get("company") if isinstance(current.get("company"), dict) else None
    checked = _parse_dt(current.get("company_checked_at"))
    if cached and not refresh and checked and utcnow() - checked < COMPANY_EVERY:
        return cached
    info = await adapter(ctx.connection.provider).company(ctx)
    info = {k: v for k, v in info.items() if v is not None}
    await patch_state(ctx.db, ctx.connection, company=info, company_checked_at=utcnow())
    if ctx.connection.provider == "xero" and info.get("short_code"):
        await patch_config(ctx.db, ctx.connection, short_code=info["short_code"])
    return info


async def plan_allows(db: AsyncSession, organization_id: uuid.UUID, cache: dict[Any, bool], feature: str = FEATURE) -> bool:
    """Whether the workspace's plan includes ``feature`` (and isn't read-only). ``cache`` is per run."""

    if (organization_id, feature) not in cache:
        from app.models.editions import Subscription
        from app.services.billing import plan_state
        from app.services.metering import addon_features

        subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == organization_id))).scalar_one_or_none()
        plan = plan_state(subscription)
        features = set(plan.features) | set(await addon_features(db, organization_id))
        cache[(organization_id, feature)] = feature in features and not plan.read_only
    return cache[(organization_id, feature)]


# ===========================================================================
# Links
# ===========================================================================


async def record_link(
    db: AsyncSession, organization_id: uuid.UUID, key: str, record_type: str, record_id: uuid.UUID, external_type: str, scope: str, *, lock: bool = False
) -> IntegrationRecordLink | None:
    query = select(IntegrationRecordLink).where(
        IntegrationRecordLink.organization_id == organization_id,
        IntegrationRecordLink.provider == key,
        IntegrationRecordLink.record_type == record_type,
        IntegrationRecordLink.record_id == record_id,
        IntegrationRecordLink.external_type == external_type,
        IntegrationRecordLink.external_parent == scope,
    )
    return (await db.execute(query.with_for_update() if lock else query)).scalar_one_or_none()


async def _lock(db: AsyncSession, name: str) -> None:
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(name, 0))))


def _retry_at(errors: int) -> datetime:
    return utcnow() + RETRY_STEPS[min(max(errors, 1), len(RETRY_STEPS)) - 1]


def _failed(link: IntegrationRecordLink, message: str) -> bool:
    """Record a failure and when to try again. True when the message is new (worth logging)."""

    data = dict(link.data or {})
    errors = int(data.get("errors") or 0) + 1
    new = link.last_error != message[:2000]
    data.update(errors=errors, retry_at=_retry_at(errors).isoformat())
    link.data = data
    link.last_error = message[:2000]
    link.status = "pending" if str(link.external_id).startswith("pending:") else "error"
    return new


def _ok(link: IntegrationRecordLink) -> None:
    data = {k: v for k, v in (link.data or {}).items() if k not in {"errors", "retry_at"}}
    link.data = data
    link.last_error = None


# ===========================================================================
# Customers
# ===========================================================================


async def _primary_contact(db: AsyncSession, account: Account) -> Contact | None:
    rows = (
        await db.execute(
            select(Contact).where(Contact.organization_id == account.organization_id, Contact.account_id == account.id).order_by(
                Contact.is_primary.desc(), Contact.email.is_(None), Contact.created_at
            ).limit(1)
        )
    ).scalars().all()
    return rows[0] if rows else None


async def customer_data(db: AsyncSession, account: Account, currency: str | None) -> CustomerData:
    contact = await _primary_contact(db, account)
    return CustomerData(
        name=account.name,
        email=contact.email if contact and contact.email else None,
        first_name=contact.first_name if contact else None,
        last_name=contact.last_name if contact else None,
        phone=account.phone or (contact.phone if contact else None),
        website=account.website,
        line1=account.billing_address_line1,
        line2=account.billing_address_line2,
        city=account.billing_city,
        region=account.billing_state,
        postal_code=account.billing_postal_code,
        country=account.billing_country,
        currency=currency,
        ref=str(account.id),
    )


async def save_customer_link(ctx: Ctx, account_id: uuid.UUID, remote: Any, *, how: str) -> IntegrationRecordLink:
    db, connection, key = ctx.db, ctx.connection, ctx.connection.provider
    scope = scope_of(connection)
    taken = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == connection.organization_id, IntegrationRecordLink.provider == key,
                IntegrationRecordLink.external_type == "customer", IntegrationRecordLink.external_parent == scope,
                IntegrationRecordLink.external_id == str(remote.id), IntegrationRecordLink.record_id != account_id,
            )
        )
    ).scalar_one_or_none()
    if taken is not None:
        other = await db.get(Account, taken.record_id)
        raise IntegrationError(
            f"{name_of(key)} customer “{remote.name}” is already linked to the AIVA account “{other.name if other else 'another account'}”. "
            "Link this account to a different customer on its page.",
            409,
        )
    link = await record_link(db, connection.organization_id, key, "account", account_id, "customer", scope)
    if link is None:
        link = IntegrationRecordLink(
            id=uuid.uuid4(), organization_id=connection.organization_id, provider=key, record_type="account", record_id=account_id,
            external_type="customer", external_parent=scope, external_id=str(remote.id),
        )
        db.add(link)
    link.external_id = str(remote.id)
    link.external_url = getattr(remote, "url", None)
    link.status = "linked"
    link.last_error = None
    link.last_synced_at = utcnow()
    link.data = {**(link.data or {}), "name": remote.name, "email": getattr(remote, "email", None), "linked_by": how, "refreshed_at": None}
    await db.flush()
    return link


async def ensure_customer(ctx: Ctx, account_id: uuid.UUID, currency: str | None) -> IntegrationRecordLink:
    db, connection = ctx.db, ctx.connection
    key = connection.provider
    link = await record_link(db, connection.organization_id, key, "account", account_id, "customer", scope_of(connection))
    if link is not None and link.status == "linked":
        return link
    account = await db.get(Account, account_id)
    if account is None or account.organization_id != connection.organization_id:
        raise IntegrationError("The invoice's account no longer exists.", 409)
    data = await customer_data(db, account, currency)
    mod = adapter(key)
    found = await mod.find_customer(ctx, data)
    how = "matched"
    if found is None:
        if not ctx.settings.get("create_customers", True):
            raise IntegrationError(
                f"No {name_of(key)} customer matches “{account.name}”, and creating customers is off. Link one on the account's page.", 409
            )
        info = await company_info(ctx)
        found = await mod.create_customer(ctx, data, bool(info.get("multi_currency")))
        how = "created"
    link = await save_customer_link(ctx, account_id, found, how=how)
    core.log(
        db, connection.organization_id, key, f"customer_{how}",
        f"{'Created' if how == 'created' else 'Matched'} {name_of(key)} customer “{found.name}” for {account.name}.",
        level="success", connection=connection, detail={"account_id": str(account_id), "customer_id": str(found.id)},
    )
    return link


# ===========================================================================
# Invoices: AIVA → accounting
# ===========================================================================


async def invoice_data(db: AsyncSession, invoice: SalesInvoice, settings: dict[str, Any]) -> InvoiceData:
    currency = (invoice.currency or "USD").upper()
    product_ids = {str(line.get("product_id")) for line in invoice.line_items or [] if line.get("product_id")}
    skus: dict[str, str | None] = {}
    if product_ids:
        valid = []
        for value in product_ids:
            try:
                valid.append(uuid.UUID(value))
            except ValueError:
                continue
        if valid:
            rows = (await db.execute(select(Product.id, Product.sku).where(Product.organization_id == invoice.organization_id, Product.id.in_(valid)))).all()
            skus = {str(pid): sku for pid, sku in rows}
    item_map = settings.get("item_map") or {}
    lines: list[Line] = []
    for raw in invoice.line_items or []:
        quantity = decimal(raw.get("quantity") or 0)
        unit = money(raw.get("unit_price"), currency)
        discount_percent = decimal(raw.get("discount_percent") or 0)
        tax_percent = decimal(raw.get("tax_percent") or 0)
        gross = money(quantity * unit, currency)
        discount = money(gross * discount_percent / 100, currency)
        net = gross - discount
        tax = money(net * tax_percent / 100, currency)
        product_id = str(raw.get("product_id")) if raw.get("product_id") else None
        lines.append(Line(
            name=str(raw.get("name") or "Item")[:255], description=str(raw.get("description") or "")[:4000], quantity=quantity, unit_price=unit,
            discount_percent=discount_percent, tax_percent=tax_percent, gross=gross, discount=discount, net=net, tax=tax,
            product_id=product_id, sku=skus.get(product_id or ""), item_ref=str(item_map[product_id]) if product_id and item_map.get(product_id) else None,
        ))
    if not lines:
        raise IntegrationError("This invoice has no lines to send.", 409)
    email = None
    if invoice.contact_id:
        contact = await db.get(Contact, invoice.contact_id)
        email = contact.email if contact is not None and contact.organization_id == invoice.organization_id else None
    return InvoiceData(
        id=str(invoice.id), reference=f"INV-{invoice.number}", issue_date=invoice.issue_date, due_date=invoice.due_date, currency=currency,
        terms=invoice.terms, notes=invoice.notes, lines=lines, subtotal=money(invoice.subtotal, currency), discount_total=money(invoice.discount_total, currency),
        tax_total=money(invoice.tax_total, currency), total=money(invoice.total, currency), email=email,
    )


def payload_hash(data: InvoiceData, customer_id: str, settings: dict[str, Any]) -> str:
    material = {
        "customer": customer_id,
        "issue_date": data.issue_date, "due_date": data.due_date, "currency": data.currency, "terms": data.terms, "notes": data.notes,
        "reference": data.reference, "email": data.email,
        "lines": [[ln.name, ln.description, str(ln.quantity), str(ln.unit_price), str(ln.discount_percent), str(ln.tax_percent), ln.item_ref] for ln in data.lines],
        "settings": {k: settings.get(k) for k in ("numbering", "default_item", "income_account", "tax_code", "tax_code_exempt", "terms", "xero_status")},
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def _label(invoice: SalesInvoice) -> str:
    return f"INV-{invoice.number}"


async def push_invoice(ctx: Ctx, invoice: SalesInvoice, *, by_user: uuid.UUID | None = None, force: bool = False) -> IntegrationRecordLink:
    """Create or update ``invoice`` in the accounting system. Safe to call twice."""

    db, connection = ctx.db, ctx.connection
    key = connection.provider
    org = connection.organization_id
    scope = scope_of(connection)
    if invoice.status == "draft":
        raise IntegrationError(f"Send {_label(invoice)} in AIVA before it goes to {name_of(key)}.", 409)
    if invoice.status == "void":
        raise IntegrationError(f"{_label(invoice)} is void.", 409)
    if not invoice.account_id:
        raise IntegrationError(f"Add an account to {_label(invoice)} first: {name_of(key)} needs a customer.", 409)
    lock_name = f"aiva-accounting-invoice:{invoice.id}"
    await _lock(db, lock_name)
    link = await record_link(db, org, key, INVOICE, invoice.id, "invoice", scope)
    remote_status = (link.data or {}).get("remote_status") if link else None
    if link is not None and link.status == "linked" and remote_status in {"paid", "void"}:
        if force:
            raise IntegrationError(f"{_label(invoice)} is {remote_status} in {name_of(key)}, so it can't be changed there.", 409)
        link.last_synced_at = utcnow()
        return link
    if link is not None and link.status == "removed":
        # Deleted in the accounting system: send it again as a new invoice.
        link.external_id = f"pending:{invoice.id}"
        link.external_url = None
        link.status = "pending"
        link.sync_hash = None
        link.data = {"attempted": False}
        await db.flush()
    try:
        customer_link = await ensure_customer(ctx, invoice.account_id, (invoice.currency or "USD").upper())
        data = await invoice_data(db, invoice, ctx.settings)
    except (http.RateLimited, http.BudgetSpent):
        raise
    except IntegrationError as exc:
        if link is None:
            link = IntegrationRecordLink(
                id=uuid.uuid4(), organization_id=org, provider=key, record_type=INVOICE, record_id=invoice.id, external_type="invoice",
                external_parent=scope, external_id=f"pending:{invoice.id}", status="pending", data={"attempted": False},
            )
            db.add(link)
        if _failed(link, str(exc)):
            core.log(db, org, key, "invoice_failed", f"Couldn't send {_label(invoice)} to {name_of(key)}: {exc}", level="error", connection=connection,
                     user_id=by_user, detail={"invoice_id": str(invoice.id)})
        await db.commit()
        raise
    digest = payload_hash(data, customer_link.external_id, ctx.settings)
    if link is not None and link.status == "linked" and not force and link.sync_hash == digest:
        link.last_synced_at = utcnow()
        return link
    created = link is None or str(link.external_id).startswith("pending:")
    if link is None:
        link = IntegrationRecordLink(
            id=uuid.uuid4(), organization_id=org, provider=key, record_type=INVOICE, record_id=invoice.id, external_type="invoice",
            external_parent=scope, external_id=f"pending:{invoice.id}", status="pending", data={"attempted": False},
        )
        db.add(link)
    meta = dict(link.data or {})
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"aiva:{key}:{scope}:{invoice.id}:{'create' if created else digest}"))
    if created:
        # Saved before calling: if this attempt is interrupted, the next one looks for
        # what it may have created instead of creating a second invoice.
        link.data = {**meta, "attempted": True}
    await db.commit()
    await _lock(db, lock_name)
    company = await company_info(ctx)
    try:
        remote = await adapter(key).push_invoice(
            ctx, data, customer_link.external_id, None if created else link.external_id, meta, request_id, company
        )
    except (http.RateLimited, http.BudgetSpent):
        raise
    except IntegrationError as exc:
        if _failed(link, str(exc)):
            core.log(db, org, key, "invoice_failed", f"Couldn't send {_label(invoice)} to {name_of(key)}: {exc}", level="error", connection=connection,
                     user_id=by_user, detail={"invoice_id": str(invoice.id)})
        await db.commit()
        raise
    link.external_id = remote.id
    link.external_url = remote.url
    link.status = "linked"
    link.sync_hash = digest
    link.last_synced_at = utcnow()
    _ok(link)
    previous = dict(link.data or {})
    previous.pop("attempted", None)
    link.data = {
        **previous, **remote.meta, "number": remote.number, "remote_status": remote.status, "total": str(remote.total), "balance": str(remote.balance),
        "currency": remote.currency, "customer_id": customer_link.external_id, "checked_at": utcnow().isoformat(),
    }
    mismatch = remote.total and remote.total != data.total and remote.status != "void"
    if mismatch:
        link.data = {**link.data, "total_mismatch": {"aiva": str(data.total), "provider": str(remote.total)}}
    else:
        link.data = {k: v for k, v in link.data.items() if k != "total_mismatch"}
    if invoice.exported_at is None:
        invoice.exported_at = utcnow()  # keeps the QuickBooks CSV export from repeating it
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise IntegrationError(f"{name_of(key)} invoice {remote.number or remote.id} is already linked to another AIVA invoice.", 409) from exc
    verb = "Sent" if created else "Updated"
    core.log(
        db, org, key, "invoice_sent" if created else "invoice_updated",
        f"{verb} {_label(invoice)} to {name_of(key)}" + (f" as {remote.number}" if remote.number and remote.number != data.reference else "") + ".",
        level="success", connection=connection, user_id=by_user, detail={"invoice_id": str(invoice.id), "external_id": remote.id},
    )
    if mismatch:
        core.log(
            db, org, key, "invoice_total_differs",
            f"{name_of(key)} totals {_label(invoice)} at {remote.total} {remote.currency or data.currency}; AIVA has {data.total}. "
            "Check the tax and discount settings.",
            level="warning", connection=connection, detail={"invoice_id": str(invoice.id)},
        )
    await apply_remote(ctx, link, invoice, remote, payments_read=False)
    return link


async def void_remote(ctx: Ctx, invoice: SalesInvoice, link: IntegrationRecordLink, *, by_user: uuid.UUID | None = None) -> None:
    db, connection = ctx.db, ctx.connection
    key = connection.provider
    try:
        await adapter(key).void_invoice(ctx, link.external_id, dict(link.data or {}))
    except (http.RateLimited, http.BudgetSpent):
        raise
    except IntegrationError as exc:
        if _failed(link, str(exc)):
            core.log(db, connection.organization_id, key, "void_failed", f"Couldn't void {_label(invoice)} in {name_of(key)}: {exc}", level="warning",
                     connection=connection, user_id=by_user, detail={"invoice_id": str(invoice.id)})
        return
    _ok(link)
    link.status = "linked"
    link.last_synced_at = utcnow()
    link.data = {**(link.data or {}), "remote_status": "void", "balance": "0"}
    core.log(db, connection.organization_id, key, "invoice_voided", f"Voided {_label(invoice)} in {name_of(key)}.", level="info",
             connection=connection, user_id=by_user, detail={"invoice_id": str(invoice.id)})


# ===========================================================================
# Payments: accounting → AIVA
# ===========================================================================


def _as_datetime(value: date | None) -> datetime:
    return datetime.combine(value, dtime(12), tzinfo=timezone.utc) if value else utcnow()


async def _save_payments(ctx: Ctx, link: IntegrationRecordLink, invoice: SalesInvoice | None, remote: RemoteInvoice, complete: bool) -> list[IntegrationObject]:
    """Upsert each payment as an IntegrationObject. Returns the ones new to AIVA."""

    db, connection = ctx.db, ctx.connection
    key = connection.provider
    scope = scope_of(connection)
    existing = {
        row.external_id: row
        for row in (
            await db.execute(
                select(IntegrationObject).where(
                    IntegrationObject.organization_id == connection.organization_id, IntegrationObject.provider == key,
                    IntegrationObject.object_type == "payment", IntegrationObject.external_parent == scope,
                    IntegrationObject.data["invoice_external_id"].astext == remote.id,
                )
            )
        ).scalars()
    }
    new: list[IntegrationObject] = []
    seen: set[str] = set()
    for payment in remote.payments:
        seen.add(payment.id)
        row = existing.get(payment.id)
        if row is None:
            row = (
                await db.execute(
                    select(IntegrationObject).where(
                        IntegrationObject.organization_id == connection.organization_id, IntegrationObject.provider == key,
                        IntegrationObject.object_type == "payment", IntegrationObject.external_parent == scope, IntegrationObject.external_id == payment.id,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            row = IntegrationObject(
                id=uuid.uuid4(), organization_id=connection.organization_id, provider=key, object_type="payment", external_id=payment.id,
                external_parent=scope,
            )
            db.add(row)
            new.append(row)
        row.title = f"Payment for {_label(invoice) if invoice else remote.number or remote.id}"[:500]
        row.status = "received"
        row.amount = payment.amount
        row.currency = (payment.currency or remote.currency or (invoice.currency if invoice else None) or "")[:3] or None
        row.occurred_at = _as_datetime(payment.date)
        row.data = {
            "invoice_external_id": remote.id, "invoice_number": remote.number, "reference": payment.reference, "method": payment.method,
            "paid_on": payment.date.isoformat() if payment.date else None,
        }
        if invoice is not None:
            row.record_type = INVOICE
            row.record_id = invoice.id
            row.account_id = invoice.account_id
            row.contact_id = invoice.contact_id
            row.opportunity_id = invoice.opportunity_id
    if complete:
        for external_id, row in existing.items():
            if external_id not in seen and row.status != "deleted":
                row.status = "deleted"
    await db.flush()
    return new


async def apply_remote(
    ctx: Ctx, link: IntegrationRecordLink, invoice: SalesInvoice | None, remote: RemoteInvoice, *, payments_read: bool = True
) -> bool:
    """Bring AIVA in line with what the accounting system says about one invoice. True if AIVA changed.

    ``payments_read`` is False when ``remote`` came back from a create/update (its
    payment list wasn't read), so missing payments aren't taken as removed."""

    db, connection = ctx.db, ctx.connection
    key = connection.provider
    org = connection.organization_id
    settings = ctx.settings
    data = dict(link.data or {})
    complete = payments_read and (remote.status in {"void", "paid"} or len(remote.payments) < 10)
    new_payments = await _save_payments(ctx, link, invoice, remote, complete)
    previous_paid = decimal(data.get("provider_paid") or 0)
    provider_paid = remote.paid
    previous_status = data.get("remote_status")
    changed = False
    if invoice is not None and invoice.status != "void" and provider_paid != previous_paid:
        currency = invoice.currency or remote.currency
        total = money(invoice.total, currency)
        before_paid = money(invoice.amount_paid, currency)
        # What the accounting sync itself added earlier; everything else on the invoice came
        # from AIVA (payment links, recorded by hand). The books usually record those same
        # payments too, so take the larger view instead of adding them up.
        applied = min(before_paid, max(Decimal("0"), decimal(data.get("aiva_applied", previous_paid))))
        others = max(Decimal("0"), before_paid - applied)
        after_paid = money(min(total, max(others, provider_paid)), currency)
        data["aiva_applied"] = str(max(Decimal("0"), after_paid - others))
        if after_paid != before_paid:
            changed = True
            invoice.amount_paid = after_paid
            delta = after_paid - before_paid
            latest = max((p.date for p in remote.payments if p.date), default=None)
            if after_paid >= total and total > 0:
                invoice.status = "paid"
                invoice.paid_at = _as_datetime(latest)
            elif invoice.status == "paid":
                invoice.status = "sent"
                invoice.paid_at = None
            amount_text = f"{abs(delta):,.2f} {currency}"
            if delta > 0:
                subject = f"Payment of {amount_text} for {_label(invoice)} recorded in {name_of(key)}"
            else:
                subject = f"A payment of {amount_text} for {_label(invoice)} was removed in {name_of(key)}"
            if invoice.account_id or invoice.contact_id or invoice.opportunity_id:
                db.add(Activity(
                    id=uuid.uuid4(), organization_id=org, account_id=invoice.account_id, contact_id=invoice.contact_id,
                    opportunity_id=invoice.opportunity_id, activity_type="system", subject=subject[:300],
                    body=f"{_label(invoice)} balance: {money(total - after_paid, currency):,.2f} {currency}.", occurred_at=_as_datetime(latest) if delta > 0 else utcnow(),
                    external_id=f"{key}:payment:{remote.id}:{provider_paid}"[:500],
                    activity_metadata={"integration": key, "kind": "invoice_payment", "invoice_id": str(invoice.id), "amount": str(delta)},
                ))
            if settings.get("notify_owner", True) and invoice.owner_user_id:
                title = f"{_label(invoice)} is paid" if invoice.status == "paid" else (subject if delta < 0 else f"Payment received for {_label(invoice)}")
                db.add(Notification(
                    id=uuid.uuid4(), organization_id=org, user_id=invoice.owner_user_id, kind="invoice", title=title[:200],
                    body=f"{subject}.", link=f"/invoices/{invoice.id}",
                ))
            core.log(db, org, key, "payment_synced", f"{subject}.", level="success" if delta > 0 else "warning", connection=connection,
                     detail={"invoice_id": str(invoice.id), "amount": str(delta), "payments": [p.external_id for p in new_payments]})
    if remote.status == "void" and previous_status != "void" and invoice is not None and invoice.status != "void":
        core.log(db, org, key, "invoice_voided_remote", f"{_label(invoice)} was voided in {name_of(key)}; it's still open in AIVA.", level="warning",
                 connection=connection, detail={"invoice_id": str(invoice.id)})
        if invoice.owner_user_id:
            db.add(Notification(id=uuid.uuid4(), organization_id=org, user_id=invoice.owner_user_id, kind="invoice",
                                title=f"{_label(invoice)} was voided in {name_of(key)}"[:200], body="Void it in AIVA too, or ask your accountant.",
                                link=f"/invoices/{invoice.id}"))
    data.update(
        provider_paid=str(provider_paid), remote_status=remote.status, balance=str(remote.balance), total=str(remote.total), number=remote.number,
        currency=remote.currency, checked_at=utcnow().isoformat(),
    )
    data.update(remote.meta)
    link.data = data
    if link.external_url is None and remote.url:
        link.external_url = remote.url
    await db.flush()
    return changed


async def refresh_invoice(ctx: Ctx, link: IntegrationRecordLink) -> bool:
    db, connection = ctx.db, ctx.connection
    invoice = await db.get(SalesInvoice, link.record_id)
    if invoice is not None and invoice.organization_id != connection.organization_id:
        invoice = None
    remote = await adapter(connection.provider).get_invoice(ctx, link.external_id)
    if remote is None:
        if (link.data or {}).get("remote_status") != "deleted":
            link.data = {**(link.data or {}), "remote_status": "deleted", "checked_at": utcnow().isoformat()}
            link.status = "removed"
            label = _label(invoice) if invoice else link.external_id
            core.log(db, connection.organization_id, connection.provider, "invoice_missing",
                     f"{label} no longer exists in {name_of(connection.provider)}. Send it again from the invoice if it should be there.",
                     level="warning", connection=connection, detail={"invoice_id": str(link.record_id)})
        return False
    return await apply_remote(ctx, link, invoice, remote)


async def refresh_account(ctx: Ctx, link: IntegrationRecordLink) -> None:
    result = await adapter(ctx.connection.provider).summary(ctx, link.external_id)
    link.data = {
        **(link.data or {}), "open_balance": str(result.open_balance), "overdue_balance": str(result.overdue_balance),
        "overdue_count": result.overdue_count, "currency": result.currency, "invoices": result.invoices, "refreshed_at": utcnow().isoformat(),
    }
    link.last_synced_at = utcnow()


# ===========================================================================
# Scheduler
# ===========================================================================


def _push_candidates(org: uuid.UUID, key: str, scope: str, sync_from: date):
    link = IntegrationRecordLink
    now = utcnow()
    joined = and_(
        link.organization_id == SalesInvoice.organization_id, link.provider == key, link.record_type == INVOICE, link.record_id == SalesInvoice.id,
        link.external_type == "invoice", link.external_parent == scope,
    )
    due = or_(link.data["retry_at"].astext.is_(None), link.data["retry_at"].astext <= now.isoformat())
    return (
        select(SalesInvoice, link)
        .outerjoin(link, joined)
        .where(
            SalesInvoice.organization_id == org,
            SalesInvoice.account_id.is_not(None),
            SalesInvoice.issue_date >= sync_from,
            or_(
                and_(SalesInvoice.status.in_(("sent", "paid")), link.id.is_(None)),
                and_(SalesInvoice.status.in_(("sent", "paid")), link.status == "linked", SalesInvoice.updated_at > link.last_synced_at),
                and_(SalesInvoice.status.in_(("sent", "paid")), link.status.in_(("pending", "error")), due),
                and_(SalesInvoice.status == "void", link.status.in_(("linked", "error")), ~link.external_id.startswith("pending:"),
                     link.data["remote_status"].astext.notin_(("void", "deleted")), due),
            ),
        )
        .order_by(SalesInvoice.updated_at.asc())
        .limit(PUSH_BATCH)
    )


def _sync_from(settings: dict[str, Any], connection: IntegrationConnection) -> date:
    value = settings.get("sync_from")
    if value:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            pass
    return (connection.created_at or utcnow()).date()


async def _invoice_links(db: AsyncSession, connection: IntegrationConnection, external_ids: set[str]) -> list[IntegrationRecordLink]:
    if not external_ids:
        return []
    return list(
        (
            await db.execute(
                select(IntegrationRecordLink).where(
                    IntegrationRecordLink.organization_id == connection.organization_id, IntegrationRecordLink.provider == connection.provider,
                    IntegrationRecordLink.external_type == "invoice", IntegrationRecordLink.external_parent == scope_of(connection),
                    IntegrationRecordLink.external_id.in_(list(external_ids)[:500]), IntegrationRecordLink.status.in_(("linked", "error")),
                )
            )
        ).scalars()
    )


async def _invoices_of_payments(db: AsyncSession, connection: IntegrationConnection, payment_ids: set[str]) -> set[str]:
    if not payment_ids:
        return set()
    rows = (
        await db.execute(
            select(IntegrationObject.data["invoice_external_id"].astext).where(
                IntegrationObject.organization_id == connection.organization_id, IntegrationObject.provider == connection.provider,
                IntegrationObject.object_type == "payment", IntegrationObject.external_parent == scope_of(connection),
                IntegrationObject.external_id.in_(list(payment_ids)[:500]),
            )
        )
    ).scalars()
    return {r for r in rows if r}


async def sync_connection(db: AsyncSession, connection: IntegrationConnection, budget: http.Budget, *, everything: bool = False) -> dict[str, int]:
    """One pass over one company: read back what changed there, send what changed here."""

    ctx = await context(db, connection, budget)
    key = connection.provider
    org = connection.organization_id
    now = utcnow()
    counts = {"refreshed": 0, "sent": 0, "voided": 0, "failed": 0, "balances": 0}
    await company_info(ctx)

    # 1. What changed in the accounting system (webhook nudges, then the change feed).
    nudges = await _take_nudges(db, connection)
    current = state(connection)
    external_invoices = {n.split(":", 1)[1] for n in nudges if n.startswith("Invoice:")}
    payments = {n.split(":", 1)[1] for n in nudges if n.startswith("Payment:")}
    external_invoices |= await _invoices_of_payments(db, connection, payments)
    cursor = _parse_dt(current.get("cursor"))
    if everything or current.get("check_now") or payments or cursor is None or now - cursor >= CHANGES_EVERY:
        since = cursor or now - timedelta(days=1)
        limit = LOOKBACK_LIMIT.get(key)
        if limit is not None and now - since > limit:
            since = now - limit
        changed, deleted_payments = await adapter(key).changed_since(ctx, since)
        external_invoices |= changed
        external_invoices |= await _invoices_of_payments(db, connection, deleted_payments)
        await patch_state(db, connection, cursor=(now - timedelta(minutes=2)).isoformat(), check_now=False)
    await db.commit()
    for link in (await _invoice_links(db, connection, external_invoices))[:REFRESH_BATCH]:
        if await refresh_invoice(ctx, link):
            counts["refreshed"] += 1
        await db.commit()

    # 2. What changed in AIVA (automatic mode).
    if ctx.settings.get("sync_mode", "auto") == "auto" or everything:
        sync_from = _sync_from(ctx.settings, connection)
        rows = (await db.execute(_push_candidates(org, key, scope_of(connection), sync_from))).all()
        for invoice, link in rows:
            try:
                if invoice.status == "void":
                    await void_remote(ctx, invoice, link)
                    counts["voided"] += 1
                else:
                    await push_invoice(ctx, invoice)
                    counts["sent"] += 1
            except (http.RateLimited, http.BudgetSpent):
                raise
            except IntegrationError:
                counts["failed"] += 1
            await db.commit()

    # 3. Safety net: open invoices not looked at for a while.
    stale_before = (now - SAFETY_EVERY).isoformat()
    stale = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key, IntegrationRecordLink.external_type == "invoice",
                IntegrationRecordLink.external_parent == scope_of(connection), IntegrationRecordLink.status == "linked",
                IntegrationRecordLink.data["remote_status"].astext.in_(("open", "draft")),
                or_(IntegrationRecordLink.data["checked_at"].astext.is_(None), IntegrationRecordLink.data["checked_at"].astext < stale_before),
            ).order_by(IntegrationRecordLink.data["checked_at"].astext.asc().nulls_first()).limit(REFRESH_BATCH)
        )
    ).scalars().all()
    for link in stale:
        if await refresh_invoice(ctx, link):
            counts["refreshed"] += 1
        await db.commit()

    # 4. Account balances.
    accounts = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key, IntegrationRecordLink.external_type == "customer",
                IntegrationRecordLink.external_parent == scope_of(connection), IntegrationRecordLink.status == "linked",
                or_(IntegrationRecordLink.data["refreshed_at"].astext.is_(None), IntegrationRecordLink.data["refreshed_at"].astext < (now - ROLLUP_EVERY).isoformat()),
            ).order_by(IntegrationRecordLink.data["refreshed_at"].astext.asc().nulls_first()).limit(ROLLUP_BATCH)
        )
    ).scalars().all()
    for link in accounts:
        try:
            await refresh_account(ctx, link)
            counts["balances"] += 1
        except (http.RateLimited, http.BudgetSpent):
            raise
        except IntegrationError as exc:
            link.data = {**(link.data or {}), "refreshed_at": now.isoformat()}
            link.last_error = str(exc)[:2000]
        await db.commit()

    if key == "freshbooks":
        await _resend_freshbooks(ctx)
    if key == "netsuite":
        # The ERP side: customers, contacts, items and stock, sales orders (same budget and pauses).
        from app.integrations.providers import netsuite as erp

        counts.update(await erp.sync_extras(ctx, everything=everything))

    await patch_state(db, connection, last_run_at=utcnow().isoformat(), last_error=None, last_counts=counts)
    await db.commit()
    return counts


async def _resend_freshbooks(ctx: Ctx) -> None:
    """FreshBooks confirms a callback by calling AIVA right away, sometimes before the new
    connection is saved. Ask it to send the code again (at most hourly) until confirmed."""

    connection = ctx.connection
    current = state(connection)
    callbacks = current.get("callbacks") or {}
    pending = [cid for cid in callbacks if not core.secret(connection, f"fb_verifier:{cid}")]
    asked = _parse_dt(current.get("resend_at"))
    if not pending or (asked and utcnow() - asked < timedelta(hours=1)):
        return
    for callback_id in pending[:2]:
        try:
            await ctx.call("PUT", f"{_acct_freshbooks.callbacks_url(scope_of(connection))}/{callback_id}",
                           json={"callback": {"resend": True}}, headers={"Content-Type": "application/json"}, ok_statuses=(400, 404, 422))
        except IntegrationError:
            pass
    await patch_state(ctx.db, connection, resend_at=utcnow().isoformat())


async def run_due(db: AsyncSession, organization_id: uuid.UUID | None = None) -> int:
    """Scheduler entry point. ``organization_id`` limits the run to one workspace."""

    started = time.monotonic()
    db.sync_session.info["automation_disabled"] = True
    query = (
        select(IntegrationConnection.id)
        .where(IntegrationConnection.provider.in_(KEYS), IntegrationConnection.user_id.is_(None), IntegrationConnection.status == "connected")
        .order_by(core.scheduler_order())
        .limit(MAX_CONNECTIONS_PER_RUN * 3)
    )
    if organization_id is not None:
        query = query.where(IntegrationConnection.organization_id == organization_id)
    rows = (await db.execute(query)).scalars().all()
    plans: dict[Any, bool] = {}
    handled = 0
    examined: list[uuid.UUID] = []
    try:
        handled = await _run_connections(db, rows, started, plans, examined)
    finally:
        # Everything looked at, synced or skipped, goes to the back of the queue.
        await db.rollback()
        await core.mark_seen_ids(db, examined)
    return handled


async def _run_connections(db: AsyncSession, rows: list[uuid.UUID], started: float, plans: dict[Any, bool], examined: list[uuid.UUID]) -> int:
    handled = 0
    done = 0
    for connection_id in rows:
        if done >= MAX_CONNECTIONS_PER_RUN or time.monotonic() - started > TOTAL_RUN_SECONDS:
            break
        examined.append(connection_id)
        connection = await db.get(IntegrationConnection, connection_id)
        if connection is None or connection.status != "connected":
            continue
        org = connection.organization_id
        if not await core.is_enabled(db, org, connection.provider) or not await plan_allows(db, org, plans, core.definition(connection.provider).feature):
            continue
        paused = _parse_dt(state(connection).get("paused_until"))
        if paused and paused > utcnow():
            continue
        done += 1
        try:
            counts = await sync_connection(db, connection, http.Budget(calls=RUN_CALLS[connection.provider], seconds=RUN_SECONDS))
            handled += sum(counts.values())
        except http.RateLimited as exc:
            await db.rollback()
            connection = await db.get(IntegrationConnection, connection_id)
            if connection is not None:
                until = utcnow() + timedelta(seconds=exc.retry_after)
                await patch_state(db, connection, paused_until=until.isoformat(), last_run_at=utcnow().isoformat())
                core.log(db, org, connection.provider, "rate_limited", f"{exc}", level="warning", connection=connection)
                await db.commit()
        except http.BudgetSpent:
            await db.commit()
            connection = await db.get(IntegrationConnection, connection_id)
            if connection is not None:
                await patch_state(db, connection, last_run_at=utcnow().isoformat())
                await db.commit()
        except IntegrationError as exc:
            await db.rollback()
            connection = await db.get(IntegrationConnection, connection_id)
            if connection is not None:
                previous = state(connection).get("last_error")
                await patch_state(db, connection, last_error=str(exc)[:1000], last_run_at=utcnow().isoformat())
                if previous != str(exc)[:1000]:
                    core.log(db, org, connection.provider, "sync_failed", f"Sync paused on an error: {exc}", level="error", connection=connection)
                await db.commit()
        except Exception:  # noqa: BLE001 - one company must not stop the others
            logger.exception("Accounting sync failed for connection %s", connection_id)
            await db.rollback()
    return handled


# ===========================================================================
# Hub hooks
# ===========================================================================


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    if not enabled:
        return
    for other in KEYS:
        if other == key:
            continue
        row = await core.toggle_row(db, organization_id, other)
        if row is not None and row.enabled:
            row.enabled = False
            core.log(db, organization_id, other, "switched_off",
                     f"{name_of(other)} was turned off because {name_of(key)} is now the workspace's accounting system.", level="warning")
            core.log(db, organization_id, key, "replaced_other",
                     f"{name_of(key)} is now the accounting system; {name_of(other)} was turned off (its connection is kept).", level="info")
    toggle = await core.toggle_row(db, organization_id, key)
    if toggle is not None and not (toggle.config or {}).get("sync_from"):
        toggle.config = {**(toggle.config or {}), "sync_from": date.today().isoformat()}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if not connection.webhook_token:
        connection.webhook_token = core.new_webhook_token()
    await db.flush()
    await patch_state(db, connection, cursor=(utcnow() - timedelta(minutes=5)).isoformat(), check_now=False, paused_until=None, last_error=None,
                      company=None, company_checked_at=None)
    ctx = await context(db, connection, http.Budget(calls=10, seconds=30))
    info = await company_info(ctx, refresh=True)
    if key == "freshbooks":
        await _register_freshbooks(ctx)
    core.log(db, connection.organization_id, key, "company",
             f"Using {info.get('name') or connection.account_label or name_of(key)}" + (f" ({info['currency']})" if info.get("currency") else "") + ".",
             level="info", connection=connection)


async def _register_freshbooks(ctx: Ctx) -> None:
    connection = ctx.connection
    uri = core.webhook_url("freshbooks", connection.webhook_token)
    if not uri or not uri.startswith("https://"):
        core.log(ctx.db, connection.organization_id, "freshbooks", "callbacks_skipped",
                 "AIVA isn't on a public https address, so FreshBooks can't call it back; payments are checked every few minutes instead.", level="info",
                 connection=connection)
        return
    for callback_id in list((state(connection).get("callbacks") or {}).keys()):
        try:
            await _acct_freshbooks.delete_callback(ctx, callback_id)
        except IntegrationError:
            pass
    made = await _acct_freshbooks.register_callbacks(ctx, uri)
    await patch_state(ctx.db, connection, callbacks=made)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    ctx = await context(db, connection, http.Budget(calls=6, seconds=20))
    try:
        if key == "freshbooks":
            for callback_id in list((state(connection).get("callbacks") or {}).keys()):
                await _acct_freshbooks.delete_callback(ctx, callback_id)
        elif key == "xero":
            response = await ctx.call("GET", "https://api.xero.com/connections")
            tenant = _acct_xero.scope_id(connection.config or {})
            for row in response.json() or []:
                if isinstance(row, dict) and row.get("tenantId") == tenant and row.get("id"):
                    await ctx.call("DELETE", f"https://api.xero.com/connections/{row['id']}", ok_statuses=(404,))
        elif key == "quickbooks":
            app = await core.oauth_app(db, connection.organization_id, "intuit")
            refresh = http.refresh_token_of(connection)
            if app.get("client_id") and app.get("client_secret") and refresh:
                await http.basic_post("https://developer.api.intuit.com/v2/oauth2/tokens/revoke", auth=(app["client_id"], app["client_secret"]),
                                      json={"token": refresh})
    except (IntegrationError, http.BudgetSpent):
        pass


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    ctx = await context(db, connection, http.Budget(calls=6, seconds=30))
    info = await company_info(ctx, refresh=True)
    return f"{name_of(key)} answered: {info.get('name') or connection.account_label}" + (f" ({info['currency']})" if info.get("currency") else "") + "."


_CHOICES = {"xero": "tenants", "freshbooks": "businesses", "sage_accounting": "businesses", "netsuite": "subsidiaries"}


def company_choices(key: str, connection: IntegrationConnection | None) -> list[dict[str, Any]]:
    if connection is None or key not in _CHOICES:
        return []
    out = []
    for row in (connection.config or {}).get(_CHOICES[key]) or []:
        if not isinstance(row, dict):
            continue
        ident = row.get("id") if key != "freshbooks" else row.get("account_id")
        if ident:
            out.append({"id": str(ident), "name": row.get("name") or str(ident)})
    return out


def webhook_info(key: str, connection: IntegrationConnection | None, toggle: IntegrationToggle | None) -> dict[str, Any]:
    if key in {"sage_accounting", "netsuite"}:
        return {"mode": "polling"}
    if key == "freshbooks":
        url = core.webhook_url(key, connection.webhook_token) if connection is not None else None
        callbacks = state(connection).get("callbacks") if connection is not None else None
        verified = [cid for cid in (callbacks or {}) if connection is not None and core.secret(connection, f"fb_verifier:{cid}")]
        return {"mode": "callbacks", "public": bool(url and url.startswith("https://")), "callbacks": len(callbacks or {}), "verified": len(verified)}
    return {
        "mode": "app",
        "app_webhook_url": core.app_webhook_url(key),
        "server_key": bool(os.getenv(SERVER_WEBHOOK_KEYS[key], "").strip()),
        "workspace_key": bool(toggle is not None and core.secret(toggle, WORKSPACE_WEBHOOK_SECRET)),
        "public": core.app_webhook_url(key).startswith("https://"),
    }


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, key)
    toggle = await core.toggle_row(db, org, key)
    settings = await settings_for(db, org, key)
    current = state(connection) if connection is not None else {}
    scope = scope_of(connection) if connection is not None else ""
    other = None
    for candidate in KEYS:
        if candidate != key and await core.is_enabled(db, org, candidate):
            other = {"key": candidate, "name": name_of(candidate)}
    counts: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    waiting = no_account = 0
    if connection is not None and scope:
        link = IntegrationRecordLink
        rows = (
            await db.execute(
                select(link.external_type, link.status, func.count()).where(link.organization_id == org, link.provider == key, link.external_parent == scope)
                .group_by(link.external_type, link.status)
            )
        ).all()
        for external_type, status, count in rows:
            counts[f"{external_type}_{status}"] = int(count)
        failed = (
            await db.execute(
                select(link, SalesInvoice.number).join(SalesInvoice, SalesInvoice.id == link.record_id).where(
                    link.organization_id == org, link.provider == key, link.external_parent == scope, link.record_type == INVOICE,
                    link.last_error.is_not(None),
                ).order_by(link.updated_at.desc()).limit(10)
            )
        ).all()
        errors = [
            {"invoice_id": str(row.record_id), "number": f"INV-{number}", "message": row.last_error, "at": row.updated_at,
             "retry_at": (row.data or {}).get("retry_at")}
            for row, number in failed
        ]
        sync_from = _sync_from(settings, connection)
        waiting = int((await db.execute(select(func.count()).select_from(_push_candidates(org, key, scope, sync_from).limit(None).subquery()))).scalar_one())
        no_account = int(
            (
                await db.execute(
                    select(func.count()).select_from(SalesInvoice).where(
                        SalesInvoice.organization_id == org, SalesInvoice.status.in_(("sent", "paid")), SalesInvoice.account_id.is_(None),
                        SalesInvoice.issue_date >= sync_from,
                    )
                )
            ).scalar_one()
        )
        payments = (
            await db.execute(
                select(IntegrationObject).where(
                    IntegrationObject.organization_id == org, IntegrationObject.provider == key, IntegrationObject.object_type == "payment",
                    IntegrationObject.external_parent == scope, IntegrationObject.status == "received",
                ).order_by(IntegrationObject.occurred_at.desc().nulls_last()).limit(8)
            )
        ).scalars().all()
        recent = [payment_out(p) for p in payments]
    manage = principal.can("admin.settings")
    out: dict[str, Any] = {
        "other_active": other,
        "company": current.get("company") if isinstance(current.get("company"), dict) else None,
        "choices": company_choices(key, connection),
        "scope_id": scope or None,
        "counts": counts,
        "waiting": waiting,
        "no_account": no_account,
        "errors": errors,
        "recent_payments": recent,
        "last_run_at": current.get("last_run_at"),
        "last_counts": current.get("last_counts") or {},
        "paused_until": current.get("paused_until") if _parse_dt(current.get("paused_until")) and _parse_dt(current.get("paused_until")) > utcnow() else None,
        "last_error": current.get("last_error"),
        "home_url": adapter(key).home_url(connection.config or {}) if connection is not None else None,
        "sync_mode": settings["sync_mode"],
    }
    if manage:
        out["settings"] = settings
        info = webhook_info(key, connection, toggle)
        out["webhooks"] = {k: v for k, v in info.items() if k != "app_webhook_url"}
        out["app_webhook_url"] = info.get("app_webhook_url")
    return out


def payment_out(row: IntegrationObject) -> dict[str, Any]:
    return {
        "id": str(row.id), "provider": row.provider, "provider_name": name_of(row.provider) if row.provider in ADAPTERS else row.provider,
        "external_id": row.external_id, "amount": str(row.amount) if row.amount is not None else None, "currency": row.currency, "status": row.status,
        "paid_on": (row.data or {}).get("paid_on"), "reference": (row.data or {}).get("reference"), "method": (row.data or {}).get("method"),
        "invoice_id": str(row.record_id) if row.record_type == INVOICE and row.record_id else None,
        "invoice_number": (row.data or {}).get("invoice_number"), "title": row.title, "at": row.occurred_at,
    }


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}

    def text_or_none(name: str, limit: int = 120) -> None:
        if name in config:
            value = config.get(name)
            if value in (None, ""):
                cleaned[name] = None
            elif isinstance(value, (str, int)) and len(str(value)) <= limit:
                cleaned[name] = str(value).strip() or None
            else:
                raise IntegrationError(f"That {name.replace('_', ' ')} isn't valid.", 422)

    if "sync_mode" in config:
        if config["sync_mode"] not in {"auto", "manual"}:
            raise IntegrationError("Choose automatic or manual sending.", 422)
        cleaned["sync_mode"] = config["sync_mode"]
    if "numbering" in config:
        if config["numbering"] not in {"provider", "aiva"}:
            raise IntegrationError("Choose who numbers invoices.", 422)
        cleaned["numbering"] = config["numbering"]
    if "xero_status" in config:
        if config["xero_status"] not in {"AUTHORISED", "DRAFT"}:
            raise IntegrationError("New Xero invoices can be approved or drafts.", 422)
        cleaned["xero_status"] = config["xero_status"]
    if "sync_from" in config:
        value = config["sync_from"]
        if value in (None, ""):
            cleaned["sync_from"] = None
        else:
            try:
                cleaned["sync_from"] = date.fromisoformat(str(value)[:10]).isoformat()
            except ValueError as exc:
                raise IntegrationError("Use a date like 2026-01-31 for “send invoices issued from”.", 422) from exc
    for name in ("default_item", "income_account", "tax_code", "tax_code_exempt", "terms"):
        text_or_none(name)
    for name in ("notify_owner", "create_customers"):
        if name in config:
            if not isinstance(config[name], bool):
                raise IntegrationError(f"{name.replace('_', ' ').capitalize()} must be on or off.", 422)
            cleaned[name] = config[name]
    if "item_map" in config:
        raw = config["item_map"] or {}
        if not isinstance(raw, dict) or len(raw) > 2000:
            raise IntegrationError("The product mapping isn't valid.", 422)
        ids: list[uuid.UUID] = []
        for product_id, value in raw.items():
            try:
                ids.append(uuid.UUID(str(product_id)))
            except ValueError as exc:
                raise IntegrationError("The product mapping refers to an unknown product.", 422) from exc
            if value is not None and (not isinstance(value, (str, int)) or len(str(value)) > 120):
                raise IntegrationError("The product mapping isn't valid.", 422)
        known = set()
        if ids:
            known = {
                str(pid)
                for pid in (await db.execute(select(Product.id).where(Product.organization_id == principal.organization_id, Product.id.in_(ids)))).scalars()
            }
        cleaned["item_map"] = {str(k): str(v) for k, v in raw.items() if str(uuid.UUID(str(k))) in known and v not in (None, "")}
    return cleaned


# ===========================================================================
# Webhooks
# ===========================================================================


async def _candidates(db: AsyncSession, key: str, scopes: set[str]) -> list[IntegrationConnection]:
    field = "realm_id" if key == "quickbooks" else "tenant_id"
    if not scopes:
        return []
    return list(
        (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.provider == key, IntegrationConnection.user_id.is_(None),
                    IntegrationConnection.config[field].astext.in_(list(scopes)[:200]),
                )
            )
        ).scalars()
    )


async def _keys_for(db: AsyncSession, key: str, organization_id: uuid.UUID) -> list[str]:
    keys = []
    server = os.getenv(SERVER_WEBHOOK_KEYS[key], "").strip()
    if server:
        keys.append(server)
    toggle = await core.toggle_row(db, organization_id, key)
    own = core.secret(toggle, WORKSPACE_WEBHOOK_SECRET) if toggle is not None else None
    if own:
        keys.append(own)
    return keys


async def _any_key_matches(db: AsyncSession, key: str, body: bytes, signature: str | None, check: Any) -> bool:
    """For Xero's intent-to-receive check (no events, so no organisation to look up)."""

    server = os.getenv(SERVER_WEBHOOK_KEYS[key], "").strip()
    if server and check(server, body, signature):
        return True
    toggles = (
        await db.execute(select(IntegrationToggle).where(IntegrationToggle.provider == key, IntegrationToggle.secrets.has_key(WORKSPACE_WEBHOOK_SECRET)).limit(500))
    ).scalars().all()
    return any(check(core.secret(t, WORKSPACE_WEBHOOK_SECRET), body, signature) for t in toggles)


async def _app_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    mod = adapter(key)
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"error": "too large"}, status_code=413)
    header = "intuit-signature" if key == "quickbooks" else "x-xero-signature"
    signature = request.headers.get(header)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    events = mod.parse_webhook(payload)
    if not events:
        if key == "xero":
            # Intent to receive: 200 with an empty body when signed with the app's key, 401 otherwise.
            if connection is not None:
                ok = any(mod.signature_ok(k, body, signature) for k in await _keys_for(db, key, connection.organization_id))
            else:
                ok = await _any_key_matches(db, key, body, signature, mod.signature_ok)
            return Response(status_code=200 if ok else 401)
        return JSONResponse({"ok": True, "ignored": "no events"})
    scopes = {scope for scope, _, _ in events}
    candidates = [connection] if connection is not None else await _candidates(db, key, scopes)
    candidates = [c for c in candidates if c is not None and scope_of(c) in scopes]
    if not candidates:
        return JSONResponse({"ok": True, "ignored": "no workspace uses these companies"})
    verified = []
    for candidate in candidates:
        if any(mod.signature_ok(k, body, signature) for k in await _keys_for(db, key, candidate.organization_id)):
            verified.append(candidate)
    if not verified:
        if signature:
            for candidate in candidates:
                core.log(db, candidate.organization_id, key, "webhook_rejected", f"A {name_of(key)} notification was refused: its signature didn't match.",
                         level="warning", connection=candidate)
        return Response(status_code=401)
    for candidate in verified:
        if not await core.is_enabled(db, candidate.organization_id, key):
            continue
        mine = [(name, ident) for scope, name, ident in events if scope == scope_of(candidate)]
        items = [f"{'Invoice' if name.lower() == 'invoice' else 'Payment'}:{ident}" for name, ident in mine if name.lower() in {"invoice", "payment"}]
        await nudge(db, candidate, items, check_now=any(name.lower() == "payment" for name, _ in mine))
    return Response(status_code=200)


async def _freshbooks_webhook(db: AsyncSession, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "use the connection's webhook URL"}, status_code=404)
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"error": "too large"}, status_code=413)
    params = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
    values = dict(params)
    name = values.get("name") or ""
    callbacks = state(connection).get("callbacks") or {}
    if name == "callback.verify":
        callback_id = str(values.get("object_id") or "")
        verifier = str(values.get("verifier") or "")
        if callback_id not in callbacks or not verifier or len(verifier) > 200:
            return JSONResponse({"error": "unknown callback"}, status_code=404)
        ctx = await context(db, connection, http.Budget(calls=3, seconds=10))
        try:
            confirmed = await _acct_freshbooks.confirm_callback(ctx, callback_id, verifier)
        except IntegrationError:
            confirmed = False
        if confirmed:
            core.set_secret(connection, f"fb_verifier:{callback_id}", verifier)
            core.log(db, connection.organization_id, "freshbooks", "callback_verified",
                     f"FreshBooks will notify AIVA about {callbacks.get(callback_id, 'changes')} events.", level="success", connection=connection)
        return JSONResponse({"ok": confirmed})
    signature = request.headers.get("x-freshbooks-hmac-sha256")
    verifiers = [core.secret(connection, f"fb_verifier:{cid}") for cid in callbacks]
    if not any(_acct_freshbooks.signature_ok(v, params, signature) for v in verifiers if v):
        if signature:
            core.log(db, connection.organization_id, "freshbooks", "webhook_rejected", "A FreshBooks notification was refused: its signature didn't match.",
                     level="warning", connection=connection)
        return Response(status_code=401)
    object_id = str(values.get("object_id") or "")[:60]
    if str(values.get("account_id") or "") not in ("", scope_of(connection)):
        return JSONResponse({"ok": True, "ignored": "another business"})
    if name.startswith("invoice.") and object_id:
        await nudge(db, connection, [f"Invoice:{object_id}"])
    elif name.startswith("payment.") and object_id:
        await nudge(db, connection, [f"Payment:{object_id}"], check_now=True)
    return JSONResponse({"ok": True})


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if request.method == "GET":
        return JSONResponse({"ok": True, "message": f"AIVA receives {name_of(key)} notifications here (POST)."})
    if key in {"quickbooks", "xero"}:
        return await _app_webhook(db, key, connection, request)
    if key == "freshbooks":
        return await _freshbooks_webhook(db, connection, request)
    return JSONResponse({"error": f"{name_of(key)} doesn't send webhooks; AIVA checks for changes on a schedule."}, status_code=404)

