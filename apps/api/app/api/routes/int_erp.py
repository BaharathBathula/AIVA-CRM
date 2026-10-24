"""Integration endpoints: ERP (Oracle NetSuite).

NetSuite is also the workspace's accounting system, so invoices, payments,
customer links and balances use the accounting endpoints
(``/accounting/...`` with key ``netsuite``). These add the ERP side:

* ``/erp/status``: whether NetSuite is on and connected, and which records
  become sales orders.
* ``/erp/netsuite/items/import`` and ``/erp/netsuite/items/restart`` (admins):
  read changed items now, or read every item again from the start.
* ``/erp/products/{id}``: a product's NetSuite item (price, stock by location);
  ``/refresh`` reads it again now.
* ``/erp/orders/{record_type}/{id}``: the NetSuite sales order for an AIVA
  sales order, quote or deal; ``/push`` sends it now, ``/refresh`` reads its
  status again.

Every record endpoint checks the record first (``records.resolve``), so people
only see NetSuite details of records they can open.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _acct_http as http
from app.integrations.providers import _ns_api as ns
from app.integrations.providers import accounting, netsuite
from app.models.opportunity import Opportunity
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Quote
from app.models.supply import SalesOrder
from app.services.automation import record_audit
from app.services.records import resolve

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

FEATURE = [Depends(require_feature(netsuite.FEATURE))]
KEY = netsuite.KEY
MODELS: dict[str, type] = {"sales_order": SalesOrder, "quote": Quote, "opportunity": Opportunity}
PUSH_PERMISSION = {"sales_order": "sales.orders", "quote": "sales.quotes", "opportunity": "records.write"}
SOURCE = {"sales_order": "sales_order", "quote": "quote", "opportunity": "deal"}


async def _fail(db: AsyncSession, exc: IntegrationError | http.BudgetSpent) -> HTTPException:
    """Keep what the attempt recorded (a link's error, the activity log) and report it."""

    if isinstance(exc, http.BudgetSpent):
        exc = IntegrationError("That took too long. AIVA will finish in the background.", 504)
    await db.commit()
    return HTTPException(exc.status_code, str(exc))


async def _status(db: AsyncSession, principal: Principal) -> dict[str, Any]:
    org = principal.organization_id
    enabled = await core.is_enabled(db, org, KEY)
    connection = await core.connection_for(db, org, KEY)
    settings = await netsuite.settings_for(db, org)
    account = accounting.scope_of(connection) if connection is not None else None
    return {
        "enabled": enabled,
        "connected": bool(enabled and connection is not None and connection.status == "connected"),
        "status": connection.status if connection is not None else "not_connected",
        "account_id": account,
        "sandbox": ns.is_sandbox(account) if account else False,
        "home_url": accounting.adapter(KEY).home_url(connection.config or {}) if connection is not None else None,
        "orders_from": list(settings.get("orders_from") or []),
        "order_mode": settings.get("order_mode"),
        "items_sync": settings.get("items_sync"),
        "stock_sync": bool(settings.get("stock_sync")),
        "can_manage": principal.can("admin.settings"),
    }


# ===========================================================================
# Workspace
# ===========================================================================


@router.get("/erp/status", dependencies=FEATURE)
async def erp_status(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return await _status(db, principal)


@router.post("/erp/netsuite/items/import", dependencies=FEATURE)
async def import_items(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Read changed NetSuite items now (a few pages; the scheduler carries on with the rest)."""

    db.sync_session.info["automation_disabled"] = True
    try:
        ctx = await netsuite.context(db, principal.organization_id, http.Budget(calls=30, seconds=40))
        if ctx.settings.get("items_sync") == "off":
            raise IntegrationError("Importing items is off. Turn it on in the NetSuite settings first.", 409)
        count = await netsuite.import_items(ctx, force=True, pages=5)
    except http.BudgetSpent as exc:
        raise await _fail(db, exc) from exc
    except http.RateLimited as exc:
        raise await _fail(db, exc) from exc
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    current = accounting.state(ctx.connection)
    core.log(db, principal.organization_id, KEY, "items_checked", f"Checked {count} changed item{'s' if count != 1 else ''} in NetSuite.",
             user_id=principal.user_id, connection=ctx.connection)
    await db.commit()
    return {"ok": True, "count": count, "caught_up": bool(current.get("items_caught_up"))}


@router.post("/erp/netsuite/items/restart", dependencies=FEATURE)
async def restart_items(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Read every NetSuite item again from the beginning (products keep their links)."""

    try:
        connection = await core.active_connection(db, principal.organization_id, KEY)
    except IntegrationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    await accounting.patch_state(db, connection, items_cursor=None, items_last_id=None, items_caught_up=False, items_checked_at=None, stock_checked_at=None)
    core.log(db, principal.organization_id, KEY, "items_restart", "Every NetSuite item will be read again.", user_id=principal.user_id, connection=connection)
    record_audit(db, organization_id=principal.organization_id, action="integration_items_restart", entity_type="integration", entity_label="Oracle NetSuite")
    await db.commit()
    return {"ok": True}


# ===========================================================================
# Products
# ===========================================================================


async def _product_out(db: AsyncSession, principal: Principal, product_id: uuid.UUID) -> dict[str, Any]:
    status_info = await _status(db, principal)
    link = None
    connection = await core.connection_for(db, principal.organization_id, KEY)
    if connection is not None:
        link = await accounting.record_link(db, principal.organization_id, KEY, "product", product_id, "item", accounting.scope_of(connection))
    return {
        "erp": status_info,
        "link": netsuite.link_out(link if link is not None and link.status == "linked" else None),
        "can_refresh": principal.can("sales.products") and status_info["connected"],
    }


@router.get("/erp/products/{product_id}", dependencies=FEATURE)
async def product_item(product_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    await resolve(db, principal.organization_id, "product", product_id)
    return await _product_out(db, principal, product_id)


@router.post("/erp/products/{product_id}/refresh", dependencies=FEATURE)
async def refresh_product(product_id: uuid.UUID, principal: Principal = Depends(require_permission("sales.products")), db: AsyncSession = Depends(get_db)):
    """Read the product's NetSuite item, price and stock again now."""

    await resolve(db, principal.organization_id, "product", product_id)
    db.sync_session.info["automation_disabled"] = True
    try:
        ctx = await netsuite.context(db, principal.organization_id, http.Budget(calls=6, seconds=30))
        link = await accounting.record_link(db, principal.organization_id, KEY, "product", product_id, "item", accounting.scope_of(ctx.connection))
        if link is None or link.status != "linked":
            raise IntegrationError("This product isn't linked to a NetSuite item.", 409)
        rows = await ns.suiteql(ctx, f"SELECT {netsuite._ITEM_COLUMNS} FROM item i WHERE i.id = {ns.ident(link.external_id)}", limit=1)
        if not rows:
            raise IntegrationError("That item no longer exists in NetSuite.", 404)
        inventory = await accounting.plan_allows(db, principal.organization_id, {}, "inventory")
        prices = await netsuite._prices(ctx, [link.external_id])
        stock = await netsuite._stock(ctx, [link.external_id]) if inventory and rows[0].get("itemtype") in netsuite.INVENTORY_TYPES else {}
        await netsuite.upsert_product(ctx, rows[0], prices.get(link.external_id), (stock or {}).get(link.external_id), inventory=inventory)
    except (IntegrationError, http.BudgetSpent) as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _product_out(db, principal, product_id)


# ===========================================================================
# Sales orders (from AIVA sales orders, accepted quotes and won deals)
# ===========================================================================


def _kind(record_type: str) -> str:
    if record_type not in MODELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Only sales orders, quotes and deals become NetSuite sales orders.")
    return record_type


async def _record(db: AsyncSession, principal: Principal, record_type: str, record_id: uuid.UUID) -> Any:
    await resolve(db, principal.organization_id, record_type, record_id)  # record-level access
    record = await db.get(MODELS[record_type], record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return record


SOURCE_LABEL = {"sales_order": "sales orders", "quote": "accepted quotes", "deal": "won deals"}


async def _blocked(db: AsyncSession, record_type: str, record: Any, orders_from: list[str]) -> str | None:
    """Why this record can't become a NetSuite sales order (the same rules as the automatic sync)."""

    source = SOURCE[record_type]
    if source not in orders_from:
        return f"NetSuite isn't set to create sales orders from {SOURCE_LABEL[source]} (Integrations → Oracle NetSuite → Sales orders)."
    org = record.organization_id
    if record_type == "sales_order" and record.status not in {"confirmed", "fulfilled"}:
        return "Confirm the sales order first."
    if record_type == "quote":
        if record.status != "accepted":
            return "Only accepted quotes become sales orders."
        converted = await db.scalar(
            select(SalesOrder.id).where(SalesOrder.organization_id == org, SalesOrder.quote_id == record.id, SalesOrder.status != "cancelled").limit(1)
        )
        if converted is not None:
            return "This quote is already an AIVA sales order; send that order to NetSuite instead."
    if record_type == "opportunity":
        stage = await db.get(PipelineStage, record.stage_id) if record.stage_id else None
        if stage is None or stage.category != "won":
            return "Only won deals become sales orders."
        if not record.amount or record.amount <= 0:
            return "Add the deal's amount first."
        quoted = await db.scalar(
            select(Quote.id).where(Quote.organization_id == org, Quote.opportunity_id == record.id, Quote.status == "accepted").limit(1)
        )
        ordered = await db.scalar(
            select(SalesOrder.id).where(SalesOrder.organization_id == org, SalesOrder.opportunity_id == record.id, SalesOrder.status != "cancelled").limit(1)
        )
        if quoted is not None or ordered is not None:
            return "This deal already has an accepted quote or a sales order; send that to NetSuite instead."
    if not record.account_id:
        return "Add an account first: NetSuite needs a customer."
    return None


async def _order_out(db: AsyncSession, principal: Principal, record_type: str, record: Any) -> dict[str, Any]:
    status_info = await _status(db, principal)
    link = None
    connection = await core.connection_for(db, principal.organization_id, KEY)
    if connection is not None:
        link = await accounting.record_link(db, principal.organization_id, KEY, record_type, record.id, netsuite.ORDER, accounting.scope_of(connection))
    reason = await _blocked(db, record_type, record, status_info["orders_from"])
    allowed = SOURCE[record_type] in status_info["orders_from"]
    return {
        "erp": status_info,
        "link": netsuite.link_out(link),
        "source_on": allowed,
        "blocked_reason": reason,
        "can_push": principal.can(PUSH_PERMISSION[record_type]) and status_info["connected"] and reason is None,
    }


@router.get("/erp/orders/{record_type}/{record_id}", dependencies=FEATURE)
async def order_status(record_type: str, record_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")),
                       db: AsyncSession = Depends(get_db)):
    record_type = _kind(record_type)
    return await _order_out(db, principal, record_type, await _record(db, principal, record_type, record_id))


@router.post("/erp/orders/{record_type}/{record_id}/push", dependencies=FEATURE)
async def push_order(record_type: str, record_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")),
                     db: AsyncSession = Depends(get_db)):
    """Send (or update) the NetSuite sales order now."""

    record_type = _kind(record_type)
    if not principal.can(PUSH_PERMISSION[record_type]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission to send this to NetSuite.")
    record = await _record(db, principal, record_type, record_id)
    reason = await _blocked(db, record_type, record, list((await netsuite.settings_for(db, principal.organization_id)).get("orders_from") or []))
    if reason:
        raise HTTPException(status.HTTP_409_CONFLICT, reason)
    db.sync_session.info["automation_disabled"] = True
    try:
        ctx = await netsuite.context(db, principal.organization_id)
        link = await netsuite.push_order(ctx, record_type, record, by_user=principal.user_id, force=True)
    except (IntegrationError, http.BudgetSpent) as exc:
        raise await _fail(db, exc) from exc
    record_audit(db, organization_id=principal.organization_id, action="erp_order_sent", entity_type=record_type, entity_id=record.id,
                 entity_label=f"NetSuite {(link.data or {}).get('number') or link.external_id}")
    await db.commit()
    return await _order_out(db, principal, record_type, record)


@router.post("/erp/orders/{record_type}/{record_id}/refresh", dependencies=FEATURE)
async def refresh_order(record_type: str, record_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")),
                        db: AsyncSession = Depends(get_db)):
    """Read the NetSuite sales order's status again now."""

    record_type = _kind(record_type)
    record = await _record(db, principal, record_type, record_id)
    db.sync_session.info["automation_disabled"] = True
    try:
        ctx = await netsuite.context(db, principal.organization_id, http.Budget(calls=4, seconds=20))
        link = await accounting.record_link(db, principal.organization_id, KEY, record_type, record.id, netsuite.ORDER, accounting.scope_of(ctx.connection))
        if link is None or link.status != "linked":
            raise IntegrationError("This isn't in NetSuite yet.", 409)
        row = (await netsuite.read_orders(ctx, [link.external_id])).get(link.external_id)
        if row is None:
            raise IntegrationError("That sales order no longer exists in NetSuite.", 404)
        await netsuite.apply_order_status(ctx, link, row)
    except (IntegrationError, http.BudgetSpent) as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _order_out(db, principal, record_type, record)
