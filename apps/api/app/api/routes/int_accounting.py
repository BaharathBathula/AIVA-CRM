"""Integration endpoints: accounting (QuickBooks Online, Xero, FreshBooks, Sage Accounting).

* ``/accounting/status``: which accounting system the workspace uses.
* ``/accounting/{key}/...`` (admins): live lists for the settings (products,
  accounts, tax codes, terms), choosing the organisation/business, "Sync now",
  retrying failed invoices, the webhook key for a workspace's own app, and
  customer search (people who manage invoices).
* ``/accounting/invoices/{id}``: an invoice's sync status and payments; send it
  now, or read its payments again.
* ``/accounting/accounts/{id}``: the account's customer, balance and recent
  invoices; link it to an existing customer, unlink it, refresh.

Provider logic lives in ``app/integrations/providers/accounting.py`` and the
``_acct_*`` adapters. Webhooks arrive through the integrations hub.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _acct_http as http
from app.integrations.providers import accounting
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationObject, IntegrationRecordLink
from app.services.automation import record_audit
from app.services.records import resolve

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

FEATURE = [Depends(require_feature(accounting.FEATURE))]


def _key(key: str) -> str:
    if key not in accounting.KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown accounting system.")
    return key


def _http(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _fail(db: AsyncSession, exc: IntegrationError) -> HTTPException:
    """Keep what the attempt recorded (activity, a link's error) and report it."""

    await db.commit()
    return _http(exc)


def _link_out(link: IntegrationRecordLink | None) -> dict[str, Any] | None:
    if link is None:
        return None
    data = link.data or {}
    pending = str(link.external_id).startswith("pending:")
    return {
        "id": str(link.id),
        "provider": link.provider,
        "provider_name": accounting.name_of(link.provider),
        "external_id": None if pending else link.external_id,
        "external_url": link.external_url,
        "status": link.status,
        "number": data.get("number"),
        "name": data.get("name"),
        "remote_status": data.get("remote_status"),
        "total": data.get("total"),
        "balance": data.get("balance"),
        "currency": data.get("currency"),
        "total_mismatch": data.get("total_mismatch"),
        "last_synced_at": link.last_synced_at,
        "checked_at": data.get("checked_at"),
        "last_error": link.last_error,
        "retry_at": data.get("retry_at"),
        "linked_by": data.get("linked_by"),
        "open_balance": data.get("open_balance"),
        "overdue_balance": data.get("overdue_balance"),
        "overdue_count": data.get("overdue_count"),
        "invoices": data.get("invoices") or [],
        "refreshed_at": data.get("refreshed_at"),
    }


async def _active(db: AsyncSession, principal: Principal) -> dict[str, Any] | None:
    key = await accounting.active_key(db, principal.organization_id)
    if key is None:
        return None
    connection = await core.connection_for(db, principal.organization_id, key)
    company = accounting.state(connection).get("company") if connection is not None else None
    return {
        "key": key,
        "name": accounting.name_of(key),
        "connected": bool(connection and connection.status == "connected"),
        "status": connection.status if connection else "not_connected",
        "company": (company or {}).get("name") if isinstance(company, dict) else None,
        "home_url": accounting.adapter(key).home_url(connection.config or {}) if connection is not None else None,
        "sync_mode": (await accounting.settings_for(db, principal.organization_id, key))["sync_mode"],
    }


# ===========================================================================
# Workspace
# ===========================================================================


@router.get("/accounting/status", dependencies=FEATURE)
async def accounting_status(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return {"active": await _active(db, principal), "can_push": principal.can("sales.invoices"), "can_manage": principal.can("admin.settings")}


@router.get("/accounting/{key}/options", dependencies=FEATURE)
async def accounting_options(
    key: str, refresh: bool = False, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)
):
    """Products/services, sales accounts, tax codes and terms from the accounting system (cached a few minutes)."""

    key = _key(key)
    try:
        connection = await core.active_connection(db, principal.organization_id, key)
        cached = accounting.state(connection).get("options")
        at = accounting._parse_dt(accounting.state(connection).get("options_at"))
        if cached and at and not refresh and accounting.utcnow() - at < accounting.OPTIONS_TTL:
            return {**cached, "cached_at": at}
        ctx = await accounting.context(db, connection, http.Budget(calls=8, seconds=40))
        options = await accounting.adapter(key).options(ctx)
        await accounting.patch_state(db, connection, options=options, options_at=accounting.utcnow().isoformat())
        await db.commit()
    except (IntegrationError, http.BudgetSpent) as exc:
        if isinstance(exc, http.BudgetSpent):
            exc = IntegrationError("That took too long. Try again in a moment.", 504)
        raise await _fail(db, exc) from exc
    return {**options, "cached_at": accounting.utcnow()}


class CompanyChoice(BaseModel):
    id: str = Field(min_length=1, max_length=100)


@router.put("/accounting/{key}/company", dependencies=FEATURE)
async def choose_company(key: str, payload: CompanyChoice, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Pick which Xero organisation / FreshBooks or Sage business AIVA uses."""

    key = _key(key)
    try:
        connection = await core.active_connection(db, principal.organization_id, key)
    except IntegrationError as exc:
        raise _http(exc) from exc
    choice = next((c for c in accounting.company_choices(key, connection) if c["id"] == payload.id), None)
    if choice is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't one of the companies shared with AIVA. Reconnect to share it.")
    field = {"xero": ("tenant_id", "tenant_name"), "freshbooks": ("account_id", "business_name"), "sage_accounting": ("business_id", "business_name")}[key]
    patch: dict[str, Any] = {field[0]: choice["id"], field[1]: choice["name"]}
    if key == "freshbooks":
        business = next((b for b in (connection.config or {}).get("businesses") or [] if str(b.get("account_id")) == choice["id"]), {})
        patch["business_id"] = business.get("id")
    await accounting.patch_config(db, connection, **patch)
    connection.account_label = choice["name"][:320]
    await accounting.patch_state(db, connection, cursor=(accounting.utcnow() - timedelta(minutes=5)).isoformat(), company=None, company_checked_at=None,
                                 options=None, options_at=None, callbacks={} if key == "freshbooks" else None)
    core.log(db, principal.organization_id, key, "company_chosen", f"AIVA now uses {choice['name']}.", user_id=principal.user_id, connection=connection)
    record_audit(db, organization_id=principal.organization_id, action="integration_company", entity_type="integration", entity_label=f"{accounting.name_of(key)}: {choice['name']}")
    if key == "freshbooks":
        try:
            await accounting._register_freshbooks(await accounting.context(db, connection, http.Budget(calls=6, seconds=30)))
        except (IntegrationError, http.BudgetSpent):
            pass
    await db.commit()
    return {"ok": True, "company": choice}


@router.post("/accounting/{key}/sync", dependencies=FEATURE)
async def sync_now(key: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    key = _key(key)
    try:
        connection = await core.active_connection(db, principal.organization_id, key)
    except IntegrationError as exc:
        raise _http(exc) from exc
    paused = accounting._parse_dt(accounting.state(connection).get("paused_until"))
    if paused and paused > accounting.utcnow():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"{accounting.name_of(key)} asked AIVA to slow down. Try again after {paused:%H:%M} UTC.")
    db.sync_session.info["automation_disabled"] = True
    connection_id = connection.id
    try:
        counts = await accounting.sync_connection(db, connection, http.Budget(calls=accounting.RUN_CALLS[key], seconds=40), everything=True)
        finished = True
    except http.BudgetSpent:
        await db.commit()
        counts, finished = {}, False
    except http.RateLimited as exc:
        await db.rollback()
        connection = await core.connection_for(db, principal.organization_id, key)
        if connection is not None and connection.id == connection_id:
            await accounting.patch_state(db, connection, paused_until=(accounting.utcnow() + timedelta(seconds=exc.retry_after)).isoformat())
        core.log(db, principal.organization_id, key, "rate_limited", str(exc), level="warning", user_id=principal.user_id)
        await db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    core.log(db, principal.organization_id, key, "synced", "Synced now" + ("" if finished else " (more to do; AIVA carries on in the background)") + ".",
             user_id=principal.user_id, detail=counts)
    await db.commit()
    return {"ok": True, "finished": finished, "counts": counts}


@router.post("/accounting/{key}/retry", dependencies=FEATURE)
async def retry_failed(key: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Try failed invoices again on the next run instead of waiting for their backoff."""

    key = _key(key)
    rows = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == principal.organization_id, IntegrationRecordLink.provider == key,
                IntegrationRecordLink.record_type == accounting.INVOICE, IntegrationRecordLink.last_error.is_not(None),
            ).limit(1000)
        )
    ).scalars().all()
    for link in rows:
        link.data = {**(link.data or {}), "retry_at": None, "errors": 0}
    core.log(db, principal.organization_id, key, "retry", f"{len(rows)} invoice{'s' if len(rows) != 1 else ''} will be tried again.", user_id=principal.user_id)
    await db.commit()
    return {"ok": True, "count": len(rows)}


class WebhookKey(BaseModel):
    value: str | None = Field(default=None, max_length=300)


@router.put("/accounting/{key}/webhook-key", dependencies=FEATURE)
async def set_webhook_key(key: str, payload: WebhookKey, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """The webhook verifier token (QuickBooks) or webhooks key (Xero) of the workspace's own app."""

    key = _key(key)
    if key not in accounting.SERVER_WEBHOOK_KEYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{accounting.name_of(key)} doesn't use a webhook key.")
    toggle = await core.toggle_row(db, principal.organization_id, key)
    if toggle is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Turn {accounting.name_of(key)} on first.")
    core.set_secret(toggle, accounting.WORKSPACE_WEBHOOK_SECRET, (payload.value or "").strip() or None)
    core.log(db, principal.organization_id, key, "webhook_key", "Webhook key saved." if payload.value else "Webhook key removed.", user_id=principal.user_id)
    record_audit(db, organization_id=principal.organization_id, action="integration_webhook_key", entity_type="integration", entity_label=accounting.name_of(key))
    await db.commit()
    return {"ok": True, "workspace_key": bool(payload.value)}


@router.get("/accounting/{key}/customers", dependencies=FEATURE)
async def search_customers(
    key: str, q: str = Query(min_length=2, max_length=100), principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)
):
    key = _key(key)
    try:
        connection = await core.active_connection(db, principal.organization_id, key)
        ctx = await accounting.context(db, connection, http.Budget(calls=4, seconds=20))
        rows = await accounting.adapter(key).search_customers(ctx, q)
    except (IntegrationError, http.BudgetSpent) as exc:
        if isinstance(exc, http.BudgetSpent):
            exc = IntegrationError("That took too long. Try again in a moment.", 504)
        raise await _fail(db, exc) from exc
    return {"items": [{"id": r.id, "name": r.name, "email": r.email, "url": r.url} for r in rows]}


# ===========================================================================
# Invoices
# ===========================================================================


async def _invoice(db: AsyncSession, principal: Principal, invoice_id: uuid.UUID) -> SalesInvoice:
    await resolve(db, principal.organization_id, "invoice", invoice_id)  # record-level access
    invoice = await db.get(SalesInvoice, invoice_id)
    if invoice is None or invoice.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found.")
    return invoice


async def _invoice_out(db: AsyncSession, principal: Principal, invoice: SalesInvoice) -> dict[str, Any]:
    active = await _active(db, principal)
    link = None
    if active is not None:
        connection = await core.connection_for(db, principal.organization_id, active["key"])
        if connection is not None:
            link = await accounting.record_link(db, principal.organization_id, active["key"], accounting.INVOICE, invoice.id, "invoice",
                                                accounting.scope_of(connection))
    payments = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == principal.organization_id, IntegrationObject.object_type == "payment",
                IntegrationObject.record_type == accounting.INVOICE, IntegrationObject.record_id == invoice.id,
            ).order_by(IntegrationObject.occurred_at.desc().nulls_last()).limit(50)
        )
    ).scalars().all()
    reason = None
    if invoice.status == "draft":
        reason = "Send the invoice in AIVA first."
    elif invoice.status == "void":
        reason = "This invoice is void."
    elif not invoice.account_id:
        reason = "Add an account to the invoice first."
    return {
        "active": active,
        "link": accounting_link_out(link),
        "payments": [accounting.payment_out(p) for p in payments],
        "invoice_status": invoice.status,
        "can_push": principal.can("sales.invoices") and active is not None and active["connected"] and reason is None,
        "blocked_reason": reason,
    }


def accounting_link_out(link: IntegrationRecordLink | None) -> dict[str, Any] | None:
    return _link_out(link)


@router.get("/accounting/invoices/{invoice_id}", dependencies=FEATURE)
async def invoice_status(invoice_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return await _invoice_out(db, principal, await _invoice(db, principal, invoice_id))


@router.post("/accounting/invoices/{invoice_id}/push", dependencies=FEATURE)
async def push_invoice(invoice_id: uuid.UUID, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)):
    invoice = await _invoice(db, principal, invoice_id)
    db.sync_session.info["automation_disabled"] = True
    try:
        connection = await accounting.connection_of(db, principal.organization_id)
        ctx = await accounting.context(db, connection)
        await accounting.push_invoice(ctx, invoice, by_user=principal.user_id, force=True)
    except http.BudgetSpent as exc:
        raise await _fail(db, IntegrationError("That took too long. AIVA will finish in the background.", 504)) from exc
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    record_audit(db, organization_id=principal.organization_id, action="invoice_synced", entity_type="invoice", entity_id=invoice.id,
                 entity_label=f"INV-{invoice.number}")
    await db.commit()
    return await _invoice_out(db, principal, invoice)


@router.post("/accounting/invoices/{invoice_id}/refresh", dependencies=FEATURE)
async def refresh_invoice(invoice_id: uuid.UUID, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)):
    invoice = await _invoice(db, principal, invoice_id)
    db.sync_session.info["automation_disabled"] = True
    try:
        connection = await accounting.connection_of(db, principal.organization_id)
        link = await accounting.record_link(db, principal.organization_id, connection.provider, accounting.INVOICE, invoice.id, "invoice",
                                            accounting.scope_of(connection))
        if link is None or str(link.external_id).startswith("pending:"):
            raise IntegrationError(f"INV-{invoice.number} isn't in {accounting.name_of(connection.provider)} yet.", 409)
        await accounting.refresh_invoice(await accounting.context(db, connection), link)
    except http.BudgetSpent as exc:
        raise await _fail(db, IntegrationError("That took too long. Try again in a moment.", 504)) from exc
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _invoice_out(db, principal, invoice)


# ===========================================================================
# Accounts
# ===========================================================================


async def _account_out(db: AsyncSession, principal: Principal, account_id: uuid.UUID) -> dict[str, Any]:
    active = await _active(db, principal)
    link = None
    if active is not None:
        connection = await core.connection_for(db, principal.organization_id, active["key"])
        if connection is not None:
            link = await accounting.record_link(db, principal.organization_id, active["key"], "account", account_id, "customer",
                                                accounting.scope_of(connection))
    payments = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == principal.organization_id, IntegrationObject.object_type == "payment",
                IntegrationObject.account_id == account_id, IntegrationObject.provider.in_(accounting.KEYS), IntegrationObject.status == "received",
            ).order_by(IntegrationObject.occurred_at.desc().nulls_last()).limit(10)
        )
    ).scalars().all()
    return {
        "active": active,
        "link": _link_out(link if link is not None and link.status == "linked" else None),
        "payments": [accounting.payment_out(p) for p in payments],
        "can_link": principal.can("sales.invoices") and active is not None and active["connected"],
    }


@router.get("/accounting/accounts/{account_id}", dependencies=FEATURE)
async def account_status(account_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    await resolve(db, principal.organization_id, "account", account_id)
    return await _account_out(db, principal, account_id)


class LinkPayload(BaseModel):
    customer_id: str | None = Field(default=None, max_length=200)
    create: bool = False


@router.post("/accounting/accounts/{account_id}/link", dependencies=FEATURE)
async def link_account(
    account_id: uuid.UUID, payload: LinkPayload, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)
):
    """Link the account to an existing customer, or find/create one now."""

    await resolve(db, principal.organization_id, "account", account_id)
    try:
        connection = await accounting.connection_of(db, principal.organization_id)
        ctx = await accounting.context(db, connection)
        key = connection.provider
        if payload.customer_id:
            remote = await accounting.adapter(key).get_customer(ctx, payload.customer_id.strip())
            if remote is None:
                raise IntegrationError(f"That customer wasn't found in {accounting.name_of(key)}.", 404)
            existing = await accounting.record_link(db, principal.organization_id, key, "account", account_id, "customer", accounting.scope_of(connection))
            if existing is not None and existing.external_id != remote.id:
                existing.status = "removed"
                await db.delete(existing)
                await db.flush()
            link = await accounting.save_customer_link(ctx, account_id, remote, how="manual")
        else:
            link = await accounting.ensure_customer(ctx, account_id, None)
        await accounting.refresh_account(ctx, link)
    except http.BudgetSpent as exc:
        raise await _fail(db, IntegrationError("That took too long. Try again in a moment.", 504)) from exc
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    core.log(db, principal.organization_id, key, "customer_linked", f"Linked the account to {accounting.name_of(key)} customer “{(link.data or {}).get('name')}”.",
             user_id=principal.user_id, connection=connection, detail={"account_id": str(account_id), "customer_id": link.external_id})
    await db.commit()
    return await _account_out(db, principal, account_id)


@router.delete("/accounting/accounts/{account_id}/link", dependencies=FEATURE)
async def unlink_account(account_id: uuid.UUID, principal: Principal = Depends(require_permission("sales.invoices")), db: AsyncSession = Depends(get_db)):
    await resolve(db, principal.organization_id, "account", account_id)
    key = await accounting.active_key(db, principal.organization_id)
    if key is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No accounting system is turned on.")
    connection = await core.connection_for(db, principal.organization_id, key)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing is linked.")
    link = await accounting.record_link(db, principal.organization_id, key, "account", account_id, "customer", accounting.scope_of(connection))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing is linked.")
    await db.delete(link)
    core.log(db, principal.organization_id, key, "customer_unlinked", "Unlinked an account from its customer; the next invoice matches or creates one again.",
             user_id=principal.user_id, connection=connection, detail={"account_id": str(account_id)})
    await db.commit()
    return await _account_out(db, principal, account_id)


@router.post("/accounting/accounts/{account_id}/refresh", dependencies=FEATURE)
async def refresh_account(account_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    await resolve(db, principal.organization_id, "account", account_id)
    try:
        connection = await accounting.connection_of(db, principal.organization_id)
        link = await accounting.record_link(db, principal.organization_id, connection.provider, "account", account_id, "customer",
                                            accounting.scope_of(connection))
        if link is None or link.status != "linked":
            raise IntegrationError(f"This account isn't linked to a {accounting.name_of(connection.provider)} customer yet.", 409)
        refreshed_at = accounting._parse_dt((link.data or {}).get("refreshed_at"))
        if refreshed_at is None or accounting.utcnow() - refreshed_at > timedelta(seconds=30):
            await accounting.refresh_account(await accounting.context(db, connection, http.Budget(calls=6, seconds=30)), link)
    except http.BudgetSpent as exc:
        raise await _fail(db, IntegrationError("That took too long. Try again in a moment.", 504)) from exc
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _account_out(db, principal, account_id)
