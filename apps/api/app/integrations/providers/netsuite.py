"""Oracle NetSuite: customers, contacts, items, prices, stock, sales orders, invoices and payments.

NetSuite joins the accounting engine (``accounting.py`` with the
``_acct_netsuite`` adapter) as the workspace's accounting system, so invoices,
payments, balances and customer matching work exactly as for QuickBooks or
Xero, counted once across payment processors, the books and NetSuite. This
module adds the hub hooks and the ERP side, run inside the same scheduler pass
(``sync_extras``, called from ``accounting.sync_connection``) with the same call
budget, pause-on-429 and fair rotation:

* **Customers.** Made when an invoice or order needs one, or ahead of time for
  every account at the *customer* stage (``customers_from``).
* **Contacts.** Contacts of linked accounts become NetSuite contacts attached to
  the customer (``AIVA-CON-<id>``), and are updated when they change in AIVA.
* **Items, prices and stock.** NetSuite items become AIVA products (SKU = item
  name/number), matched by SKU first. Changed items are read in pages ordered by
  (last modified, id) and the position is saved after every page, so a failed
  run loses nothing. The chosen price level gives the unit price; stock on hand
  (one location or all of them) is refreshed every half hour and recorded as a
  stock adjustment, when the plan includes inventory.
* **Sales orders.** Confirmed AIVA sales orders, accepted quotes (not yet
  turned into an order) and won deals (without a quote or order) become NetSuite
  sales orders with ``externalId = AIVA-SO-<id>``; a pending link is saved before
  the create call, and an interrupted create is found again by that external ID.
  When a quote's order later gets an AIVA sales order, the link moves to it.
  Orders are only changed while NetSuite has them pending approval or
  fulfilment. Status comes back: fulfilled/billed or cancelled in NetSuite
  updates the AIVA sales order (stock is NetSuite's to move).

Credentials: the consumer key and token ID are moved into the encrypted
secrets on connect (``_ns_api.take_identifiers``); nothing secret is returned
to the browser or written to the activity log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _acct_http as http
from app.integrations.providers import _acct_netsuite as adapter
from app.integrations.providers import _ns_api as ns
from app.integrations.providers import accounting
from app.integrations.providers._acct_types import Ctx, Line, decimal, money
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import StockMovement
from app.models.integration_hub import IntegrationConnection, IntegrationRecordLink
from app.models.opportunity import Opportunity
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Notification, Product, Quote
from app.models.supply import SalesOrder

logger = logging.getLogger("aiva.integrations.netsuite")

KEY = ns.KEY
FEATURE = "erp_sync"
ORDER = "sales_order"  # external_type of NetSuite sales orders on links
ORDER_KINDS = ("sales_order", "quote", "opportunity")  # AIVA records that become NetSuite sales orders
SOURCE_FOR = {"sales_order": "sales_order", "quote": "quote", "opportunity": "deal"}  # record type -> orders_from value
ORDER_PREFIX = "AIVA-SO"
CONTACT_PREFIX = "AIVA-CON"
INVENTORY_TYPES = {"InvtPart", "Assembly"}
ITEM_PAGE = 200
ITEMS_EVERY = timedelta(minutes=5)
STOCK_EVERY = timedelta(minutes=30)
STOCK_BATCH = 300
ORDER_BATCH = 5
ORDERS_EVERY = timedelta(minutes=10)
ORDER_PAGE = 200
CONTACT_BATCH = 10
CUSTOMER_BATCH = 5
EDITABLE = {"A", "B"}  # pending approval, pending fulfillment
FULFILLED = {"F", "G"}  # pending billing, billed
SO_STATUS = {
    "A": "Pending approval", "B": "Pending fulfillment", "C": "Cancelled", "D": "Partially fulfilled",
    "E": "Pending billing / partially fulfilled", "F": "Pending billing", "G": "Billed", "H": "Closed",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _code(value: Any) -> str:
    return str(value or "").split(":")[-1].upper()[:2]


async def settings_for(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, Any]:
    return await accounting.settings_for(db, organization_id, KEY)


async def _fresh_state(db: AsyncSession, connection: IntegrationConnection) -> dict[str, Any]:
    """The connection's sync state as saved (another run may have moved it on)."""

    row = (await db.execute(sql_text("SELECT config->'sync' FROM integration_connections WHERE id = :id"), {"id": connection.id})).first()
    return dict(row[0]) if row and isinstance(row[0], dict) else {}


async def _recover(db: AsyncSession, connection: IntegrationConnection) -> None:
    """Roll back a failed write and reload the connection (a rollback expires what the session holds)."""

    await db.rollback()
    await db.refresh(connection)


# ===========================================================================
# Hub hooks
# ===========================================================================


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    creds = ns.credentials_from(values)
    data = await ns.probe(creds)
    rows = [r for r in (data.get("items") or []) if isinstance(r, dict)]
    name = next((str(r.get("name")) for r in rows if r.get("name")), None)
    label = f"{name or 'NetSuite'} ({creds.account})"
    return {"label": label[:320], "external_id": creds.account, "config": {"account_id": creds.account}}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    ns.take_identifiers(connection)
    await db.flush()
    scope = accounting.scope_of(connection)
    current = accounting.state(connection)
    if current.get("erp_account") != scope:
        # Another NetSuite account than before: its items and orders start from scratch.
        await accounting.patch_state(db, connection, erp_account=scope, items_cursor=None, items_last_id=None, items_caught_up=False,
                                     items_checked_at=None, items_total=0, orders_cursor=None, orders_last_id=None, orders_checked_at=None,
                                     stock_checked_at=None)
        await accounting.patch_config(db, connection, subsidiary_id=None, subsidiary_name=None)
    await accounting.on_connected(db, key, connection)
    info = accounting.state(connection).get("company") or {}
    subsidiaries = [s for s in info.get("subsidiaries") or [] if isinstance(s, dict) and s.get("id")]
    patch: dict[str, Any] = {"subsidiaries": subsidiaries}
    chosen = (connection.config or {}).get("subsidiary_id")
    if chosen and not any(str(s["id"]) == str(chosen) for s in subsidiaries):
        patch.update(subsidiary_id=None, subsidiary_name=None)
        chosen = None
    if not chosen and len(subsidiaries) == 1:
        patch.update(subsidiary_id=str(subsidiaries[0]["id"]), subsidiary_name=subsidiaries[0].get("name"))
    await accounting.patch_config(db, connection, **patch)
    if len(subsidiaries) > 1 and not (connection.config or {}).get("subsidiary_id"):
        core.log(db, connection.organization_id, KEY, "pick_subsidiary",
                 "This is a OneWorld account: pick the subsidiary new customers and contacts belong to on the NetSuite page.", level="warning",
                 connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    # Tokens are revoked in NetSuite (Setup → Users/Roles → Access Tokens); nothing to call.
    return None


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    await accounting.on_toggle(db, key, organization_id, enabled)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    message = await accounting.test(db, key, connection, principal)
    sub = (connection.config or {}).get("subsidiary_name")
    return message[:-1] + (f"; new customers go to {sub}." if sub else ".")


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    return await accounting.handle_webhook(db, key, connection, request)


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    out = await accounting.detail(db, key, principal)
    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    erp: dict[str, Any] = {"items": {}, "orders": {}, "contacts": {}, "order_errors": []}
    if connection is not None:
        scope = accounting.scope_of(connection)
        current = accounting.state(connection)
        config = connection.config or {}
        link = IntegrationRecordLink
        rows = (
            await db.execute(
                select(link.record_type, link.status, func.count()).where(link.organization_id == org, link.provider == KEY, link.external_parent == scope)
                .group_by(link.record_type, link.status)
            )
        ).all()
        by: dict[str, dict[str, int]] = {}
        for record_type, status, count in rows:
            by.setdefault(record_type, {})[status] = int(count)
        orders: dict[str, int] = {}
        for kind in ORDER_KINDS:
            for status, count in by.get(kind, {}).items():
                orders[status] = orders.get(status, 0) + count
        failed = (
            await db.execute(
                select(link).where(link.organization_id == org, link.provider == KEY, link.external_parent == scope, link.external_type == ORDER,
                                   link.last_error.is_not(None)).order_by(link.updated_at.desc()).limit(10)
            )
        ).scalars().all()
        erp = {
            "account_id": scope or None,
            "sandbox": ns.is_sandbox(scope) if scope else False,
            "subsidiary": {"id": config.get("subsidiary_id"), "name": config.get("subsidiary_name")} if config.get("subsidiary_id") else None,
            "items": {
                "linked": by.get("product", {}).get("linked", 0), "removed": by.get("product", {}).get("removed", 0),
                "caught_up": bool(current.get("items_caught_up")), "checked_at": current.get("items_checked_at"), "imported": int(current.get("items_total") or 0),
                "stock_checked_at": current.get("stock_checked_at"),
            },
            "orders": orders,
            "contacts": by.get("contact", {}),
            "order_errors": [
                {"record_type": r.record_type, "record_id": str(r.record_id), "label": (r.data or {}).get("label"), "message": r.last_error,
                 "at": r.updated_at, "retry_at": (r.data or {}).get("retry_at")}
                for r in failed
            ],
        }
        if principal.can("admin.settings"):
            erp["credentials"] = {"consumer_key": config.get("consumer_key_hint"), "token_id": config.get("token_id_hint")}
    out["erp"] = erp
    out["plan"] = {"inventory": principal.has("inventory"), "sales_orders": principal.has("sales_orders")}
    return out


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    cleaned = await accounting.clean_settings(db, key, principal, config, personal)
    for name in ("default_item", "tax_code", "tax_code_exempt", "terms"):
        if cleaned.get(name) is not None and not ns._ID.match(str(cleaned[name])):
            raise IntegrationError("Pick NetSuite records from the lists (internal IDs).", 422)
    if any(not ns._ID.match(str(v)) for v in (cleaned.get("item_map") or {}).values()):
        raise IntegrationError("Map products to NetSuite items from the list.", 422)
    for name in ("location", "department", "class_id", "order_form", "stock_location", "price_level"):
        if name in config:
            value = config.get(name)
            if value in (None, ""):
                cleaned[name] = "1" if name == "price_level" else None
            elif isinstance(value, (str, int)) and ns._ID.match(str(value).strip()):
                cleaned[name] = str(value).strip()
            else:
                raise IntegrationError(f"That {name.replace('_id', '').replace('_', ' ')} isn't a NetSuite record.", 422)
    choices = {
        "items_sync": {"off", "update", "create"}, "order_mode": {"auto", "manual"}, "order_status": {"A", "B"}, "customers_from": {"needed", "customers"},
    }
    for name, allowed in choices.items():
        if name in config:
            if config[name] not in allowed:
                raise IntegrationError(f"Choose one of the options for {name.replace('_', ' ')}.", 422)
            cleaned[name] = config[name]
    for name in ("stock_sync", "order_status_back", "contacts_sync"):
        if name in config:
            if not isinstance(config[name], bool):
                raise IntegrationError(f"{name.replace('_', ' ').capitalize()} must be on or off.", 422)
            cleaned[name] = config[name]
    if "orders_from" in config:
        value = config["orders_from"]
        if not isinstance(value, list) or any(v not in {"sales_order", "quote", "deal"} for v in value):
            raise IntegrationError("Orders can come from sales orders, accepted quotes and won deals.", 422)
        cleaned["orders_from"] = [v for v in ("sales_order", "quote", "deal") if v in value]
    return cleaned


# ===========================================================================
# Scheduler: the ERP side of one pass
# ===========================================================================


async def sync_extras(ctx: Ctx, *, everything: bool = False) -> dict[str, int]:
    """Customers, contacts, items and stock, sales orders. Raises ``RateLimited``/``BudgetSpent`` like the rest of the pass."""

    settings = ctx.settings
    plans: dict[Any, bool] = {}
    org = ctx.connection.organization_id
    counts = {"customers": 0, "contacts": 0, "items": 0, "orders": 0, "order_updates": 0, "stock": 0}
    if settings.get("customers_from") == "customers":
        counts["customers"] = await push_customers(ctx)
    if settings.get("contacts_sync", True):
        counts["contacts"] = await push_contacts(ctx)
    if settings.get("items_sync", "create") != "off":
        counts["items"] = await import_items(ctx, force=everything)
    if settings.get("orders_from") and (settings.get("order_mode", "auto") == "auto" or everything):
        counts["orders"] = await push_orders(ctx)
    counts["order_updates"] = await refresh_orders(ctx, force=everything)
    if settings.get("stock_sync", True) and await accounting.plan_allows(ctx.db, org, plans, "inventory"):
        counts["stock"] = await refresh_stock(ctx, force=everything)
    return counts


# ---------------------------------------------------------------------------
# Customers ahead of time
# ---------------------------------------------------------------------------


async def push_customers(ctx: Ctx, limit: int = CUSTOMER_BATCH) -> int:
    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    link = IntegrationRecordLink
    joined = and_(link.organization_id == Account.organization_id, link.provider == KEY, link.record_type == "account", link.record_id == Account.id,
                  link.external_type == "customer", link.external_parent == scope)
    now = utcnow().isoformat()
    rows = (
        await db.execute(
            select(Account.id).outerjoin(link, joined).where(
                Account.organization_id == org, Account.lifecycle_stage == "customer", Account.is_archived.is_(False),
                or_(link.id.is_(None), and_(link.status != "linked", or_(link.data["retry_at"].astext.is_(None), link.data["retry_at"].astext <= now))),
            ).order_by(Account.created_at.asc()).limit(limit)
        )
    ).scalars().all()
    done = 0
    for account_id in rows:
        try:
            await accounting.ensure_customer(ctx, account_id, None)
            done += 1
        except (http.RateLimited, http.BudgetSpent):
            raise
        except IntegrationError as exc:
            failed = await accounting.record_link(db, org, KEY, "account", account_id, "customer", scope)
            if failed is None:
                failed = IntegrationRecordLink(id=uuid.uuid4(), organization_id=org, provider=KEY, record_type="account", record_id=account_id,
                                               external_type="customer", external_parent=scope, external_id=f"pending:{account_id}", status="error")
                db.add(failed)
            if accounting._failed(failed, str(exc)):
                core.log(db, org, KEY, "customer_failed", f"Couldn't make a NetSuite customer for an account: {exc}", level="warning",
                         connection=connection, detail={"account_id": str(account_id)})
        await db.commit()
    return done


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


def _contact_body(contact: Contact, customer_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "firstName": (contact.first_name or "")[:32] or None, "lastName": (contact.last_name or "")[:32] or None,
        "company": {"id": ns.ident(customer_id)},
    }
    if contact.email:
        body["email"] = contact.email.strip()[:254]
    if contact.phone:
        body["phone"] = contact.phone.strip()[:32]
    if contact.mobile:
        body["mobilePhone"] = contact.mobile.strip()[:32]
    if contact.job_title:
        body["title"] = contact.job_title.strip()[:99]
    return {k: v for k, v in body.items() if v is not None}


async def push_contacts(ctx: Ctx, limit: int = CONTACT_BATCH) -> int:
    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    customer = IntegrationRecordLink.__table__.alias("customer_link")
    mine = IntegrationRecordLink
    joined = and_(mine.organization_id == Contact.organization_id, mine.provider == KEY, mine.record_type == "contact", mine.record_id == Contact.id,
                  mine.external_type == "contact", mine.external_parent == scope)
    now = utcnow().isoformat()
    rows = (
        await db.execute(
            select(Contact.id, customer.c.external_id)
            .join(customer, and_(customer.c.organization_id == Contact.organization_id, customer.c.provider == KEY, customer.c.record_type == "account",
                                 customer.c.record_id == Contact.account_id, customer.c.external_type == "customer",
                                 customer.c.external_parent == scope, customer.c.status == "linked"))
            .outerjoin(mine, joined)
            .where(
                Contact.organization_id == org, Contact.is_archived.is_(False),
                or_(
                    mine.id.is_(None),
                    and_(mine.status == "linked", Contact.updated_at > mine.last_synced_at),
                    and_(mine.status.in_(("pending", "error")), or_(mine.data["retry_at"].astext.is_(None), mine.data["retry_at"].astext <= now)),
                ),
            ).order_by(Contact.updated_at.asc()).limit(limit)
        )
    ).all()
    done = 0
    for contact_id, customer_id in rows:
        try:
            if await push_contact(ctx, contact_id, str(customer_id)):
                done += 1
        except (http.RateLimited, http.BudgetSpent):
            raise
        except IntegrationError:
            pass
        await db.commit()
    return done


async def push_contact(ctx: Ctx, contact_id: uuid.UUID, customer_id: str) -> bool:
    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    await accounting._lock(db, f"aiva-ns-contact:{contact_id}")
    contact = await db.get(Contact, contact_id)
    if contact is None or contact.organization_id != org:
        return False
    link = await accounting.record_link(db, org, KEY, "contact", contact_id, "contact", scope)
    body = _contact_body(contact, customer_id)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if link is not None and link.status == "linked" and link.sync_hash == digest:
        link.last_synced_at = utcnow()
        return False
    created = link is None or str(link.external_id).startswith("pending:")
    if link is None:
        link = IntegrationRecordLink(id=uuid.uuid4(), organization_id=org, provider=KEY, record_type="contact", record_id=contact_id,
                                     external_type="contact", external_parent=scope, external_id=f"pending:{contact_id}", status="pending",
                                     data={"attempted": False})
        db.add(link)
    attempted = bool((link.data or {}).get("attempted"))
    if created:
        link.data = {**(link.data or {}), "attempted": True}
        await db.commit()
        await accounting._lock(db, f"aiva-ns-contact:{contact_id}")
    ref = ns.external_id(CONTACT_PREFIX, contact_id)
    name = " ".join(filter(None, [contact.first_name, contact.last_name])) or contact.email or "Contact"
    try:
        if created:
            target = await ns.find_by_external_id(ctx, "contact", ref) if attempted else None
            if target is None:
                info = await accounting.company_info(ctx)
                chosen = adapter.subsidiary(ctx) or (info.get("subsidiary_id") if not info.get("one_world") else None)
                if not chosen and info.get("one_world"):
                    raise IntegrationError("Pick the NetSuite subsidiary on the NetSuite page before contacts are sent.", 409)
                full = {**body, "externalId": ref, **({"subsidiary": {"id": ns.ident(chosen)}} if chosen else {})}
                try:
                    target = await adapter._create_entity(ctx, "contact", full, name)
                except IntegrationError as exc:
                    if exc.status_code == 429 or not ns.duplicate_external_id(exc):
                        raise
                    target = await ns.find_by_external_id(ctx, "contact", ref)
                    if target is None:
                        raise
        else:
            target = link.external_id
            if not await ns.update_record(ctx, "contact", target, body, missing_ok=True):
                # Deleted in NetSuite: make it again on the next run (a new contact with the same external ID).
                link.external_id = f"pending:{contact_id}"
                link.external_url = None
                link.status = "pending"
                link.sync_hash = None
                link.data = {**(link.data or {}), "attempted": False, "retry_at": None}
                core.log(db, org, KEY, "contact_missing", f"{name} was deleted in NetSuite; AIVA adds them again.", level="warning", connection=connection,
                         detail={"contact_id": str(contact_id)})
                return False
    except (http.RateLimited, http.BudgetSpent):
        raise
    except IntegrationError as exc:
        if accounting._failed(link, str(exc)):
            core.log(db, org, KEY, "contact_failed", f"Couldn't send {name} to NetSuite: {exc}", level="warning", connection=connection,
                     detail={"contact_id": str(contact_id)})
        raise
    link.external_id = str(target)
    link.external_url = ns.ui_url(scope, "contact", str(target))
    link.status = "linked"
    link.sync_hash = digest
    link.last_synced_at = utcnow()
    accounting._ok(link)
    link.data = {k: v for k, v in {**(link.data or {}), "name": name, "customer_id": customer_id}.items() if k != "attempted"}
    try:
        await db.flush()
    except IntegrityError as exc:
        await _recover(db, connection)
        raise IntegrationError("That NetSuite contact is already linked to another AIVA contact.", 409) from exc
    if created:
        core.log(db, org, KEY, "contact_sent", f"Added {name} to the NetSuite customer.", level="success", connection=connection,
                 detail={"contact_id": str(contact_id), "external_id": str(target)})
    return True


# ---------------------------------------------------------------------------
# Items, prices and stock
# ---------------------------------------------------------------------------

_ITEM_COLUMNS = (
    "i.id, i.itemid, i.displayname, i.description, i.itemtype, i.isinactive, "
    "TO_CHAR(i.lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS') AS modified"
)


async def _prices(ctx: Ctx, item_ids: list[str]) -> dict[str, dict[str, Decimal]]:
    """item id -> {currency: unit price at the chosen price level, lowest quantity tier}."""

    if not item_ids:
        return {}
    level = ns.ident(ctx.settings.get("price_level") or "1")
    where = f"p.pricelevel = {level} AND p.item IN ({ns.ids(item_ids)})"
    try:
        rows = await ns.suiteql(ctx, f"SELECT p.item, p.unitprice, p.priceqty, c.symbol AS currency FROM pricing p LEFT JOIN currency c ON c.id = p.currency WHERE {where}")
    except http.RateLimited:
        raise
    except IntegrationError:
        rows = await ns.optional(ctx, f"SELECT p.item, p.unitprice, c.symbol AS currency FROM pricing p LEFT JOIN currency c ON c.id = p.currency WHERE {where}")
    best: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    for row in rows:
        if row.get("item") is None or row.get("unitprice") in (None, ""):
            continue
        currency = str(row.get("currency") or "").upper() or "_"
        tier = decimal(row.get("priceqty") or 0)
        current = best.setdefault(str(row["item"]), {}).get(currency)
        if current is None or tier < current[0]:
            best[str(row["item"])][currency] = (tier, decimal(row["unitprice"]))
    return {item: {cur: price for cur, (_, price) in found.items()} for item, found in best.items()}


async def _stock(ctx: Ctx, item_ids: list[str]) -> dict[str, dict[str, Any]] | None:
    """item id -> {"on_hand": Decimal, "available": Decimal, "locations": {name: on hand}}; None when NetSuite couldn't say."""

    if not item_ids:
        return {}
    where = f"a.item IN ({ns.ids(item_ids)})"
    if ctx.settings.get("stock_location"):
        where += f" AND a.location = {ns.ident(ctx.settings['stock_location'])}"
    try:
        rows = await ns.suiteql(
            ctx, "SELECT a.item, a.location, BUILTIN.DF(a.location) AS location_name, a.quantityonhand, a.quantityavailable "
            f"FROM aggregateitemlocation a WHERE {where}",
        )
    except http.RateLimited:
        raise
    except IntegrationError as exc:
        logger.info("NetSuite stock lookup failed: %s", exc)
        return None
    out: dict[str, dict[str, Any]] = {str(i): {"on_hand": Decimal("0"), "available": Decimal("0"), "locations": {}} for i in item_ids}
    for row in rows:
        entry = out.get(str(row.get("item")))
        if entry is None:
            continue
        on_hand = decimal(row.get("quantityonhand"))
        entry["on_hand"] += on_hand
        entry["available"] += decimal(row.get("quantityavailable"))
        if len(entry["locations"]) < 50:
            entry["locations"][str(row.get("location_name") or row.get("location") or "?")[:80]] = str(on_hand)
    return out


def _pick_price(prices: dict[str, Decimal] | None, wanted: str | None, base: str | None) -> tuple[Decimal, str] | None:
    if not prices:
        return None
    for code in (wanted, base):
        if code and code.upper() in prices:
            return prices[code.upper()], code.upper()
    code, price = next(iter(sorted(prices.items())))
    return price, (code if code != "_" else (base or wanted or "USD"))


def _set_stock(db: AsyncSession, product: Product, quantity: Decimal, actor: uuid.UUID | None) -> bool:
    quantity = money(quantity)
    current = money(product.stock_on_hand or 0)
    if quantity == current:
        return False
    product.stock_on_hand = quantity
    db.add(StockMovement(
        id=uuid.uuid4(), organization_id=product.organization_id, product_id=product.id, kind="adjust", quantity=quantity - current,
        balance_after=quantity, reference="NetSuite", note="Stock on hand from NetSuite", created_by_user_id=actor,
    ))
    return True


async def _item_link(db: AsyncSession, org: uuid.UUID, scope: str, item_id: str) -> IntegrationRecordLink | None:
    return (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == KEY, IntegrationRecordLink.external_type == "item",
                IntegrationRecordLink.external_parent == scope, IntegrationRecordLink.external_id == item_id,
            )
        )
    ).scalar_one_or_none()


async def upsert_product(ctx: Ctx, row: dict[str, Any], prices: dict[str, Decimal] | None, stock: dict[str, Any] | None, *, inventory: bool) -> str:
    """One NetSuite item into AIVA. Returns created | updated | linked | skipped."""

    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    item_id = ns.ident(row.get("id"))
    sku = str(row.get("itemid") or "").strip()[:80] or None
    name = str(row.get("displayname") or row.get("itemid") or f"Item {item_id}").strip()[:200]
    inactive = ns.flag(row.get("isinactive"))
    item_type = str(row.get("itemtype") or "")
    mode = ctx.settings.get("items_sync", "create")
    link = await _item_link(db, org, scope, item_id)
    product = None
    outcome = "updated"
    if link is not None:
        if link.status == "removed":
            return "skipped"  # deleted in AIVA on purpose: don't bring it back
        product = await db.get(Product, link.record_id)
        if product is None or product.organization_id != org:
            link.status = "removed"
            link.data = {**(link.data or {}), "removed_at": utcnow().isoformat()}
            return "skipped"
    else:
        if sku:
            linked = select(IntegrationRecordLink.record_id).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == KEY, IntegrationRecordLink.record_type == "product",
                IntegrationRecordLink.external_type == "item", IntegrationRecordLink.external_parent == scope,
            )
            product = (
                await db.execute(
                    select(Product).where(Product.organization_id == org, func.lower(Product.sku) == sku.lower(), Product.id.not_in(linked))
                    .order_by(Product.created_at).limit(1)
                )
            ).scalar_one_or_none()
            outcome = "linked"
        if product is None:
            if mode != "create" or inactive:
                return "skipped"
            taken = sku and (await db.execute(select(Product.id).where(Product.organization_id == org, Product.sku == sku))).first()
            product = Product(id=uuid.uuid4(), organization_id=org, name=name, sku=None if taken else sku, unit_price=Decimal("0"),
                              currency=(await accounting.company_info(ctx)).get("currency") or "USD", is_active=True)
            db.add(product)
            await db.flush()  # before any stock movement that refers to it
            outcome = "created"
        link = IntegrationRecordLink(id=uuid.uuid4(), organization_id=org, provider=KEY, record_type="product", record_id=product.id,
                                     external_type="item", external_parent=scope, external_id=item_id, status="linked")
        db.add(link)
    product.name = name
    if row.get("description"):
        product.description = str(row["description"])[:4000]
    if sku and outcome != "created" and product.sku != sku:
        clash = (await db.execute(select(Product.id).where(Product.organization_id == org, Product.sku == sku, Product.id != product.id))).first()
        if not clash:
            product.sku = sku
    product.is_active = not inactive
    info = await accounting.company_info(ctx)
    price = _pick_price(prices, product.currency, info.get("currency"))
    if price is not None:
        product.unit_price = money(price[0])
        if len(price[1]) == 3:
            product.currency = price[1]
    is_inventory = item_type in INVENTORY_TYPES
    stock_on = inventory and ctx.settings.get("stock_sync", True) and is_inventory
    if stock_on and outcome == "created":
        product.track_inventory = True
    if stock_on and stock is not None and product.track_inventory:
        _set_stock(db, product, stock["on_hand"], None)
    link.external_url = ns.ui_url(scope, "item", item_id)
    link.status = "linked"
    link.last_error = None
    link.last_synced_at = utcnow()
    link.data = {
        **{k: v for k, v in (link.data or {}).items() if k not in {"locations"}},
        "name": name, "sku": sku, "type": item_type, "inactive": inactive, "modified": row.get("modified"),
        "price": str(price[0]) if price else (link.data or {}).get("price"), "currency": price[1] if price else (link.data or {}).get("currency"),
        "inventory": is_inventory,
        **({"on_hand": str(stock["on_hand"]), "available": str(stock["available"]), "locations": stock["locations"], "stock_at": utcnow().isoformat()}
           if stock is not None and is_inventory else {}),
    }
    await db.flush()
    return outcome


async def import_items(ctx: Ctx, *, force: bool = False, pages: int = 1) -> int:
    """Read changed NetSuite items in (modified, id) order; the position is saved after every page."""

    db, connection = ctx.db, ctx.connection
    handled = 0
    inventory = await accounting.plan_allows(db, connection.organization_id, {}, "inventory")
    for _ in range(max(1, pages)):
        await accounting._lock(db, f"aiva-ns-items:{connection.id}")  # one importer per account at a time
        current = await _fresh_state(db, connection)
        checked = accounting._parse_dt(current.get("items_checked_at"))
        if current.get("items_caught_up") and checked and utcnow() - checked < ITEMS_EVERY and not force:
            await db.commit()
            return handled
        types = ", ".join(ns.text(t) for t in adapter.ITEM_TYPES)
        where = f"i.itemtype IN ({types})"
        cursor, last_id = current.get("items_cursor"), current.get("items_last_id") or "0"
        modified = "TO_CHAR(i.lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS')"
        if cursor:
            where += f" AND ({modified} > {ns.text(cursor, 19)} OR ({modified} = {ns.text(cursor, 19)} AND i.id > {ns.ident(last_id)}))"
        rows = await ns.suiteql(ctx, f"SELECT {_ITEM_COLUMNS} FROM item i WHERE {where} ORDER BY {modified}, i.id", limit=ITEM_PAGE)
        rows = [r for r in rows if r.get("id") and r.get("modified")]
        ids = [str(r["id"]) for r in rows]
        prices = await _prices(ctx, ids) if ids else {}
        stock_ids = [str(r["id"]) for r in rows if r.get("itemtype") in INVENTORY_TYPES]
        stock = (await _stock(ctx, stock_ids) if stock_ids and inventory and ctx.settings.get("stock_sync", True) else {}) or {}
        counts = {"created": 0, "updated": 0, "linked": 0, "skipped": 0}
        for row in rows:
            try:
                async with db.begin_nested():
                    outcome = await upsert_product(ctx, row, prices.get(str(row["id"])), stock.get(str(row["id"])), inventory=inventory)
            except IntegrityError:
                logger.warning("NetSuite item %s couldn't be saved as a product", row.get("id"), exc_info=True)
                outcome = "skipped"
            counts[outcome] = counts.get(outcome, 0) + 1
        caught_up = len(rows) < ITEM_PAGE
        patch: dict[str, Any] = {"items_caught_up": caught_up, "items_checked_at": utcnow().isoformat(),
                                 "items_total": int(current.get("items_total") or 0) + counts["created"] + counts["linked"]}
        if rows:
            patch.update(items_cursor=str(rows[-1]["modified"])[:19], items_last_id=str(rows[-1]["id"]))
        await accounting.patch_state(db, connection, **patch)
        if counts["created"] or counts["linked"]:
            core.log(db, connection.organization_id, KEY, "items_imported",
                     f"Items from NetSuite: {counts['created']} new product{'s' if counts['created'] != 1 else ''}, {counts['linked']} matched by SKU, "
                     f"{counts['updated']} updated.", level="success", connection=connection, detail=counts)
        await db.commit()
        handled += len(rows)
        if caught_up:
            break
    return handled


async def refresh_stock(ctx: Ctx, *, force: bool = False, product_ids: list[uuid.UUID] | None = None) -> int:
    """Stock on hand for linked inventory items not checked for a while (stock changes don't touch the item)."""

    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    link = IntegrationRecordLink
    stale = (utcnow() - (timedelta(0) if force else STOCK_EVERY)).isoformat()
    query = select(link).where(
        link.organization_id == org, link.provider == KEY, link.record_type == "product", link.external_type == "item", link.external_parent == scope,
        link.status == "linked", link.data["inventory"].astext == "true",
    )
    if product_ids is not None:
        query = query.where(link.record_id.in_(product_ids))
    else:
        query = query.where(or_(link.data["stock_at"].astext.is_(None), link.data["stock_at"].astext < stale))
    links = (await db.execute(query.order_by(link.data["stock_at"].astext.asc().nulls_first()).limit(STOCK_BATCH))).scalars().all()
    if not links:
        await accounting.patch_state(db, connection, stock_checked_at=utcnow().isoformat())
        return 0
    found = await _stock(ctx, [ln.external_id for ln in links])
    changed = 0
    now = utcnow().isoformat()
    if found is None:  # NetSuite couldn't say (no inventory feature or permission): look again later
        await accounting.patch_state(db, connection, stock_checked_at=now)
        for ln in links:
            ln.data = {**(ln.data or {}), "stock_at": now}
        await db.commit()
        return 0
    for ln in links:
        entry = found.get(ln.external_id) or {"on_hand": Decimal("0"), "available": Decimal("0"), "locations": {}}
        product = await db.get(Product, ln.record_id)
        if product is not None and product.organization_id == org and product.track_inventory:
            if _set_stock(db, product, entry["on_hand"], None):
                changed += 1
        ln.data = {**(ln.data or {}), "on_hand": str(entry["on_hand"]), "available": str(entry["available"]), "locations": entry["locations"], "stock_at": now}
    await accounting.patch_state(db, connection, stock_checked_at=now)
    await db.commit()
    return changed


# ---------------------------------------------------------------------------
# Sales orders
# ---------------------------------------------------------------------------


def lines_from(raw_lines: list[dict[str, Any]], currency: str, item_map: dict[str, Any]) -> list[Line]:
    out = []
    for raw in raw_lines:
        quantity = decimal(raw.get("quantity") or 0)
        unit = money(raw.get("unit_price"), currency)
        discount_percent = decimal(raw.get("discount_percent") or 0)
        tax_percent = decimal(raw.get("tax_percent") or 0)
        gross = money(quantity * unit, currency)
        discount = money(gross * discount_percent / 100, currency)
        net = gross - discount
        tax = money(net * tax_percent / 100, currency)
        product_id = str(raw.get("product_id")) if raw.get("product_id") else None
        out.append(Line(
            name=str(raw.get("name") or "Item")[:255], description=str(raw.get("description") or "")[:4000], quantity=quantity, unit_price=unit,
            discount_percent=discount_percent, tax_percent=tax_percent, gross=gross, discount=discount, net=net, tax=tax, product_id=product_id,
            item_ref=str(item_map[product_id]) if product_id and item_map.get(product_id) else None,
        ))
    return out


def label_of(record_type: str, record: Any) -> str:
    if record_type == "sales_order":
        return f"SO-{record.number}"
    if record_type == "quote":
        return f"Q-{record.number}"
    return f"deal “{record.name}”"


async def order_source(db: AsyncSession, record_type: str, record: Any) -> dict[str, Any]:
    """What AIVA knows about the order: customer account, date, currency, lines, references."""

    if record_type == "sales_order":
        raw = list(record.line_items or [])
        if money(record.shipping) > 0:
            raw.append({"name": "Shipping", "description": "Shipping", "quantity": 1, "unit_price": str(record.shipping)})
        return {"account_id": record.account_id, "date": record.order_date, "due": record.due_date, "currency": record.currency, "lines": raw,
                "po": record.customer_po, "memo": f"AIVA SO-{record.number} · {record.subject}", "owner": record.owner_user_id,
                "contact_id": record.contact_id, "opportunity_id": record.opportunity_id}
    if record_type == "quote":
        raw = [{"name": ln.name, "description": ln.description, "quantity": str(ln.quantity), "unit_price": str(ln.unit_price),
                "discount_percent": str(ln.discount_percent), "tax_percent": str(ln.tax_percent), "product_id": str(ln.product_id) if ln.product_id else None}
               for ln in record.line_items]
        accepted = record.accepted_at.date() if record.accepted_at else date.today()
        return {"account_id": record.account_id, "date": accepted, "due": None, "currency": record.currency, "lines": raw, "po": None,
                "memo": f"AIVA Q-{record.number} · {record.name}", "owner": record.owner_user_id, "contact_id": record.contact_id,
                "opportunity_id": record.opportunity_id}
    closed = record.closed_at.date() if record.closed_at else date.today()
    raw = [{"name": record.name, "description": record.description or record.name, "quantity": 1, "unit_price": str(record.amount or 0)}]
    return {"account_id": record.account_id, "date": closed, "due": None, "currency": record.currency, "lines": raw, "po": None,
            "memo": f"AIVA deal · {record.name}", "owner": record.owner_user_id, "contact_id": record.primary_contact_id, "opportunity_id": record.id}


async def _order_body(ctx: Ctx, source: dict[str, Any], customer_id: str) -> dict[str, Any]:
    settings = ctx.settings
    currency = str(source["currency"] or "USD").upper()
    lines = lines_from(source["lines"], currency, settings.get("item_map") or {})
    if not lines:
        raise IntegrationError("This order has no lines to send.", 409)
    body: dict[str, Any] = {
        "entity": {"id": ns.ident(customer_id)},
        "tranDate": source["date"].isoformat(),
        "memo": source["memo"][:999],
        "item": {"items": await adapter.item_lines(ctx, lines, settings)},
        **adapter.classification(settings),
    }
    if source.get("due"):
        body["shipDate"] = source["due"].isoformat()
    if source.get("po"):
        body["otherRefNum"] = str(source["po"])[:45]
    if settings.get("order_form"):
        body["customForm"] = {"id": ns.ident(settings["order_form"])}
    info = await accounting.company_info(ctx)
    currency_id = (info.get("currencies") or {}).get(currency)
    if info.get("multi_currency") and currency_id:
        body["currency"] = {"id": str(currency_id)}
    return body


_ORDER_QUERY = (
    "SELECT t.id, t.tranid, t.status, t.foreigntotal, c.symbol AS currency, t.externalid, "
    "TO_CHAR(t.lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS') AS modified FROM transaction t LEFT JOIN currency c ON c.id = t.currency "
    "WHERE t.type = 'SalesOrd' AND {where}"
)


async def read_orders(ctx: Ctx, order_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not order_ids:
        return {}
    rows = await ns.suiteql(ctx, _ORDER_QUERY.format(where=f"t.id IN ({ns.ids(order_ids)})"))
    return {str(r["id"]): r for r in rows if r.get("id")}


def _order_data(link: IntegrationRecordLink, row: dict[str, Any]) -> dict[str, Any]:
    code = _code(row.get("status"))
    currency = str(row.get("currency") or "").upper() or None
    return {
        **(link.data or {}), "number": row.get("tranid"), "ns_status": code, "status_name": SO_STATUS.get(code, code or "Unknown"),
        "total": str(money(row.get("foreigntotal"), currency)), "currency": currency, "modified": row.get("modified"), "checked_at": utcnow().isoformat(),
    }


async def _adopt(ctx: Ctx, record_type: str, record: Any) -> IntegrationRecordLink | None:
    """An order that earlier went over as its quote or deal takes that NetSuite sales order over."""

    db, org = ctx.db, ctx.connection.organization_id
    scope = accounting.scope_of(ctx.connection)
    sources: list[tuple[str, Any]] = []
    if record_type == "sales_order":
        sources = [("quote", record.quote_id), ("opportunity", record.opportunity_id)]
    elif record_type == "quote":
        sources = [("opportunity", record.opportunity_id)]
    for kind, value in sources:
        if value is None:
            continue
        found = await accounting.record_link(db, org, KEY, kind, value, ORDER, scope, lock=True)
        if found is not None and found.status == "linked":
            found.data = {**(found.data or {}), "adopted_from": f"{kind}:{value}", "ref": (found.data or {}).get("ref") or ns.external_id(ORDER_PREFIX, value)}
            found.record_type = record_type
            found.record_id = record.id
            found.sync_hash = None
            await db.flush()
            return found
    return None


async def push_order(ctx: Ctx, record_type: str, record: Any, *, by_user: uuid.UUID | None = None, force: bool = False) -> IntegrationRecordLink:
    """Create or update the NetSuite sales order for an AIVA sales order, accepted quote or won deal. Safe to call twice."""

    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    label = label_of(record_type, record)
    lock_name = f"aiva-ns-order:{record.id}"
    await accounting._lock(db, lock_name)
    link = await accounting.record_link(db, org, KEY, record_type, record.id, ORDER, scope)
    if link is None:
        link = await _adopt(ctx, record_type, record)
    source = await order_source(db, record_type, record)
    try:
        if not source["account_id"]:
            raise IntegrationError(f"Add an account to {label} first: NetSuite needs a customer.", 409)
        customer = await accounting.ensure_customer(ctx, source["account_id"], str(source["currency"] or "USD").upper())
        body = await _order_body(ctx, source, customer.external_id)
    except (http.RateLimited, http.BudgetSpent):
        raise
    except IntegrationError as exc:
        if link is None:
            link = IntegrationRecordLink(id=uuid.uuid4(), organization_id=org, provider=KEY, record_type=record_type, record_id=record.id,
                                         external_type=ORDER, external_parent=scope, external_id=f"pending:{record.id}", status="pending",
                                         data={"attempted": False, "label": label})
            db.add(link)
        if accounting._failed(link, str(exc)):
            core.log(db, org, KEY, "order_failed", f"Couldn't send {label} to NetSuite: {exc}", level="error", connection=connection, user_id=by_user,
                     detail={"record_type": record_type, "record_id": str(record.id)})
        await db.commit()
        raise
    digest = hashlib.sha256(json.dumps({"body": body, "status": ctx.settings.get("order_status")}, sort_keys=True, default=str).encode()).hexdigest()
    if link is not None and link.status == "linked" and not force and link.sync_hash == digest:
        link.last_synced_at = utcnow()
        return link
    if link is not None and link.status == "linked" and (link.data or {}).get("ns_status") not in EDITABLE | {None}:
        link.last_synced_at = utcnow()
        if force:
            raise IntegrationError(f"NetSuite is already {SO_STATUS.get((link.data or {}).get('ns_status'), 'working on')} this order, so AIVA won't change it.", 409)
        return link
    created = link is None or str(link.external_id).startswith("pending:")
    if link is None:
        link = IntegrationRecordLink(id=uuid.uuid4(), organization_id=org, provider=KEY, record_type=record_type, record_id=record.id,
                                     external_type=ORDER, external_parent=scope, external_id=f"pending:{record.id}", status="pending",
                                     data={"attempted": False})
        db.add(link)
    attempted = bool((link.data or {}).get("attempted"))
    ref = (link.data or {}).get("ref") or ns.external_id(ORDER_PREFIX, record.id)
    if created:
        # Saved before calling: an interrupted create is looked up by its external ID next time.
        link.data = {**(link.data or {}), "attempted": True, "ref": ref, "label": label}
    await db.commit()
    await accounting._lock(db, lock_name)
    try:
        if created:
            target = await ns.find_by_external_id(ctx, "salesOrder", ref) if attempted else None
            if target is None:
                try:
                    target = await ns.create_record(ctx, "salesOrder", {**body, "externalId": ref, "orderStatus": {"id": ctx.settings.get("order_status") or "A"}})
                except IntegrationError as exc:
                    if exc.status_code == 429 or not ns.duplicate_external_id(exc):
                        raise
                    target = await ns.find_by_external_id(ctx, "salesOrder", ref)
                    if target is None:
                        raise
        else:
            target = link.external_id
            current = (await read_orders(ctx, [target])).get(target)
            if current is None:
                raise IntegrationError("That sales order no longer exists in NetSuite.", 409)
            if _code(current.get("status")) not in EDITABLE:
                link.data = _order_data(link, current)
                link.last_synced_at = utcnow()
                await db.commit()
                return link
            await ns.update_record(ctx, "salesOrder", target, body, replace=("item",))
        row = (await read_orders(ctx, [str(target)])).get(str(target))
    except (http.RateLimited, http.BudgetSpent):
        raise
    except IntegrationError as exc:
        if accounting._failed(link, str(exc)):
            core.log(db, org, KEY, "order_failed", f"Couldn't send {label} to NetSuite: {exc}", level="error", connection=connection, user_id=by_user,
                     detail={"record_type": record_type, "record_id": str(record.id)})
        await db.commit()
        raise
    link.external_id = str(target)
    link.external_url = ns.ui_url(scope, "salesOrder", str(target))
    link.status = "linked"
    link.sync_hash = digest
    link.last_synced_at = utcnow()
    accounting._ok(link)
    link.data = {k: v for k, v in (_order_data(link, row) if row else dict(link.data or {})).items() if k != "attempted"}
    link.data = {**link.data, "label": label, "customer_id": customer.external_id}
    try:
        await db.flush()
    except IntegrityError as exc:
        await _recover(db, connection)
        raise IntegrationError("That NetSuite sales order is already linked to another AIVA record.", 409) from exc
    number = (link.data or {}).get("number")
    verb = "Sent" if created else "Updated"
    core.log(db, org, KEY, "order_sent" if created else "order_updated", f"{verb} {label} to NetSuite" + (f" as {number}" if number else "") + ".",
             level="success", connection=connection, user_id=by_user, detail={"record_type": record_type, "record_id": str(record.id), "external_id": str(target)})
    if created and source["account_id"]:
        db.add(Activity(
            id=uuid.uuid4(), organization_id=org, account_id=source["account_id"], contact_id=source.get("contact_id"),
            opportunity_id=source.get("opportunity_id"), activity_type="system", subject=f"{label} sent to NetSuite" + (f" as {number}" if number else "")[:300],
            body=f"NetSuite status: {(link.data or {}).get('status_name') or 'received'}.", occurred_at=utcnow(),
            external_id=f"{KEY}:order:{target}"[:500], activity_metadata={"integration": KEY, "kind": "sales_order", "record_type": record_type,
                                                                         "record_id": str(record.id)},
        ))
    await db.commit()
    return link


def _due(link) -> Any:
    now = utcnow().isoformat()
    return or_(link.data["retry_at"].astext.is_(None), link.data["retry_at"].astext <= now)


async def order_candidates(ctx: Ctx, limit: int = ORDER_BATCH) -> list[tuple[str, Any]]:
    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    settings = ctx.settings
    sources = set(settings.get("orders_from") or [])
    since = accounting._sync_from(settings, connection)
    since_at = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
    link = IntegrationRecordLink
    found: list[tuple[str, Any]] = []

    def joined(kind: str, model: Any):
        return and_(link.organization_id == model.organization_id, link.provider == KEY, link.record_type == kind, link.record_id == model.id,
                    link.external_type == ORDER, link.external_parent == scope)

    if "sales_order" in sources:
        rows = (
            await db.execute(
                select(SalesOrder).outerjoin(link, joined("sales_order", SalesOrder)).where(
                    SalesOrder.organization_id == org, SalesOrder.status.in_(("confirmed", "fulfilled")), SalesOrder.account_id.is_not(None),
                    or_(SalesOrder.confirmed_at >= since_at, and_(SalesOrder.confirmed_at.is_(None), SalesOrder.order_date >= since)),
                    or_(
                        link.id.is_(None),
                        and_(link.status == "linked", SalesOrder.updated_at > link.last_synced_at, link.data["ns_status"].astext.in_(tuple(EDITABLE))),
                        and_(link.status.in_(("pending", "error")), _due(link)),
                    ),
                ).order_by(SalesOrder.updated_at.asc()).limit(limit)
            )
        ).scalars().all()
        found += [("sales_order", r) for r in rows]
    if "quote" in sources and len(found) < limit:
        converted = select(SalesOrder.quote_id).where(SalesOrder.organization_id == org, SalesOrder.quote_id.is_not(None), SalesOrder.status != "cancelled")
        rows = (
            await db.execute(
                select(Quote).outerjoin(link, joined("quote", Quote)).where(
                    Quote.organization_id == org, Quote.status == "accepted", Quote.account_id.is_not(None), Quote.accepted_at >= since_at,
                    Quote.id.not_in(converted),
                    or_(link.id.is_(None), and_(link.status.in_(("pending", "error")), _due(link))),
                ).order_by(Quote.accepted_at.asc()).limit(limit - len(found))
            )
        ).scalars().all()
        found += [("quote", r) for r in rows]
    if "deal" in sources and len(found) < limit:
        quoted = select(Quote.opportunity_id).where(Quote.organization_id == org, Quote.opportunity_id.is_not(None), Quote.status == "accepted")
        ordered = select(SalesOrder.opportunity_id).where(SalesOrder.organization_id == org, SalesOrder.opportunity_id.is_not(None),
                                                          SalesOrder.status != "cancelled")
        rows = (
            await db.execute(
                select(Opportunity).join(PipelineStage, PipelineStage.id == Opportunity.stage_id).outerjoin(link, joined("opportunity", Opportunity)).where(
                    Opportunity.organization_id == org, PipelineStage.category == "won", Opportunity.amount > 0, Opportunity.closed_at >= since_at,
                    Opportunity.id.not_in(quoted), Opportunity.id.not_in(ordered),
                    or_(link.id.is_(None), and_(link.status.in_(("pending", "error")), _due(link))),
                ).order_by(Opportunity.closed_at.asc()).limit(limit - len(found))
            )
        ).scalars().all()
        found += [("opportunity", r) for r in rows]
    return found


async def push_orders(ctx: Ctx) -> int:
    done = 0
    for record_type, record in await order_candidates(ctx):
        try:
            await push_order(ctx, record_type, record)
            done += 1
        except (http.RateLimited, http.BudgetSpent):
            raise
        except IntegrationError:
            pass
        await ctx.db.commit()
    return done


async def apply_order_status(ctx: Ctx, link: IntegrationRecordLink, row: dict[str, Any]) -> bool:
    """Bring the link (and the AIVA sales order, when that's on) in line with NetSuite. True if AIVA changed."""

    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    before = (link.data or {}).get("ns_status")
    link.data = _order_data(link, row)
    code = link.data["ns_status"]
    link.last_synced_at = utcnow()
    if code == before:
        return False
    label = (link.data or {}).get("label") or "An order"
    core.log(db, org, KEY, "order_status", f"{label} is {SO_STATUS.get(code, code).lower()} in NetSuite.", level="info", connection=connection,
             detail={"record_type": link.record_type, "record_id": str(link.record_id), "status": code})
    if link.record_type != "sales_order" or not ctx.settings.get("order_status_back", True):
        return False
    order = await db.get(SalesOrder, link.record_id)
    if order is None or order.organization_id != org:
        return False
    now = utcnow()
    changed = None
    if code in FULFILLED and order.status == "confirmed":
        order.status = "fulfilled"
        order.fulfilled_at = now
        if ctx.settings.get("stock_sync", True):
            order.stock_applied = True  # NetSuite moved the stock, and AIVA's stock levels come from NetSuite
        changed = "fulfilled"
    elif code == "C" and order.status in {"draft", "confirmed"}:
        order.status = "cancelled"
        order.cancelled_at = now
        changed = "cancelled"
    if changed is None:
        return False
    subject = f"SO-{order.number} was {changed} in NetSuite"
    if order.account_id:
        db.add(Activity(
            id=uuid.uuid4(), organization_id=org, account_id=order.account_id, contact_id=order.contact_id, opportunity_id=order.opportunity_id,
            activity_type="system", subject=subject[:300], body=f"NetSuite status: {SO_STATUS.get(code, code)}.", occurred_at=now,
            external_id=f"{KEY}:order-status:{link.external_id}:{code}"[:500],
            activity_metadata={"integration": KEY, "kind": "sales_order_status", "sales_order_id": str(order.id), "status": code},
        ))
    if order.owner_user_id:
        db.add(Notification(id=uuid.uuid4(), organization_id=org, user_id=order.owner_user_id, kind="sales_order", title=subject[:200],
                            body=f"NetSuite status: {SO_STATUS.get(code, code)}.", link=f"/sales-orders/{order.id}"))
    await db.flush()
    link.last_synced_at = utcnow()  # the AIVA order changed because of NetSuite: nothing to send back
    return True


async def refresh_orders(ctx: Ctx, *, force: bool = False) -> int:
    """Sales orders AIVA sent that changed in NetSuite since the last look, in (modified, id) order."""

    db, connection = ctx.db, ctx.connection
    org = connection.organization_id
    scope = accounting.scope_of(connection)
    has_orders = (
        await db.execute(
            select(IntegrationRecordLink.id).where(IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == KEY,
                                                   IntegrationRecordLink.external_type == ORDER, IntegrationRecordLink.external_parent == scope,
                                                   IntegrationRecordLink.status == "linked").limit(1)
        )
    ).first()
    if not has_orders:
        return 0
    current = await _fresh_state(db, connection)
    checked = accounting._parse_dt(current.get("orders_checked_at"))
    if not force and checked and utcnow() - checked < ORDERS_EVERY:
        return 0
    modified = "TO_CHAR(t.lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS')"
    where = f"t.externalid LIKE {ns.text(ORDER_PREFIX + '-%')}"
    cursor, last_id = current.get("orders_cursor"), current.get("orders_last_id") or "0"
    if cursor:
        where += f" AND ({modified} > {ns.text(cursor, 19)} OR ({modified} = {ns.text(cursor, 19)} AND t.id > {ns.ident(last_id)}))"
    rows = await ns.suiteql(ctx, _ORDER_QUERY.format(where=where) + f" ORDER BY {modified}, t.id", limit=ORDER_PAGE)
    rows = [r for r in rows if r.get("id") and r.get("modified")]
    changed = 0
    if rows:
        links = (
            await db.execute(
                select(IntegrationRecordLink).where(
                    IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == KEY, IntegrationRecordLink.external_type == ORDER,
                    IntegrationRecordLink.external_parent == scope, IntegrationRecordLink.external_id.in_([str(r["id"]) for r in rows]),
                )
            )
        ).scalars().all()
        by_id = {ln.external_id: ln for ln in links}
        for row in rows:
            found = by_id.get(str(row["id"]))
            if found is not None and await apply_order_status(ctx, found, row):
                changed += 1
    patch: dict[str, Any] = {"orders_checked_at": utcnow().isoformat() if len(rows) < ORDER_PAGE else None}
    if rows:
        patch.update(orders_cursor=str(rows[-1]["modified"])[:19], orders_last_id=str(rows[-1]["id"]))
    await accounting.patch_state(db, connection, **patch)
    await db.commit()
    return changed


# ===========================================================================
# For the routes
# ===========================================================================


async def context(db: AsyncSession, organization_id: uuid.UUID, budget: http.Budget | None = None) -> Ctx:
    connection = await core.active_connection(db, organization_id, KEY)
    return await accounting.context(db, connection, budget)


def link_out(link: IntegrationRecordLink | None) -> dict[str, Any] | None:
    if link is None:
        return None
    data = link.data or {}
    pending = str(link.external_id).startswith("pending:")
    return {
        "id": str(link.id), "record_type": link.record_type, "record_id": str(link.record_id), "external_id": None if pending else link.external_id,
        "external_url": link.external_url, "status": link.status, "number": data.get("number"), "ns_status": data.get("ns_status"),
        "status_name": data.get("status_name"), "total": data.get("total"), "currency": data.get("currency"), "name": data.get("name"),
        "sku": data.get("sku"), "type": data.get("type"), "price": data.get("price"), "on_hand": data.get("on_hand"), "available": data.get("available"),
        "locations": data.get("locations") or {}, "inventory": bool(data.get("inventory")), "inactive": bool(data.get("inactive")),
        "adopted_from": data.get("adopted_from"), "last_synced_at": link.last_synced_at, "checked_at": data.get("checked_at") or data.get("stock_at"),
        "last_error": link.last_error, "retry_at": data.get("retry_at"),
    }

