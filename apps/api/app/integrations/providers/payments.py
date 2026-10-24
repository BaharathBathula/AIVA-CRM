"""Stripe, PayPal, Square and Chargebee: payment links on invoices, payments and
refunds applied to AIVA invoices, and subscriptions on accounts.

The hub calls the hooks below with the integration key; each processor's own
module (``_pay_stripe``, ``_pay_paypal``, ``_pay_square``, ``_pay_chargebee``)
does the provider-specific work, and ``_pay_common`` holds the money logic
shared by all of them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from types import ModuleType
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _pay_chargebee, _pay_common as pay, _pay_paypal, _pay_square, _pay_stripe
from app.models.editions import SalesInvoice
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.services.records import resolve_many

logger = logging.getLogger("aiva.integrations.payments")

MODULES: dict[str, ModuleType] = {"stripe": _pay_stripe, "paypal": _pay_paypal, "square": _pay_square, "chargebee": _pay_chargebee}
CHECK_EVERY = timedelta(minutes=30)
CHECK_EVERY_OLD = timedelta(hours=24)
LINKS_PER_RUN = 12
CHARGEBEE_PER_RUN = 3


def _module(key: str) -> ModuleType:
    found = MODULES.get(key)
    if found is None:
        raise IntegrationError("Unknown payment integration.", 404)
    return found


# ---------------------------------------------------------------------------
# Hub hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    return await _module(key).validate_credentials(db, key, organization_id, values)


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await _module(key).on_connected(db, key, connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    hook = getattr(_module(key), "on_disconnect", None)
    if hook is not None:
        await hook(db, key, connection)
    if key in pay.PROCESSORS:
        # Links stay usable at the provider, but AIVA can no longer see their payments.
        open_links = (
            await db.execute(
                select(func.count()).select_from(IntegrationRecordLink).where(
                    IntegrationRecordLink.organization_id == connection.organization_id, IntegrationRecordLink.provider == key,
                    IntegrationRecordLink.external_type == pay.LINK, IntegrationRecordLink.data["state"].astext == "open",
                )
            )
        ).scalar_one()
        if open_links:
            core.log(db, connection.organization_id, key, "links_left_open",
                     f"{open_links} {pay.NAMES[key]} payment link(s) are still open at {pay.NAMES[key]}; payments on them won't reach AIVA until you reconnect.",
                     level="warning")


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    if enabled and key in pay.PROCESSORS:
        # Catch up on anything paid while it was off.
        links = (
            await db.execute(
                select(IntegrationRecordLink).where(
                    IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.provider == key,
                    IntegrationRecordLink.external_type == pay.LINK, IntegrationRecordLink.data["state"].astext == "open",
                )
            )
        ).scalars().all()
        for link in links:
            link.last_synced_at = None


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    return await _module(key).test(db, key, connection, principal)


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    module = MODULES.get(key)
    if module is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    return await module.handle_webhook(db, key, connection, request)


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    cleaned = pay.clean_common(config)
    if key == "chargebee":
        cleaned.update(_pay_chargebee.clean(config))
    return cleaned


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, key)
    settings = await pay.settings(db, org, key)
    config = (connection.config or {}) if connection else {}
    out: dict[str, Any] = {"settings": settings, "stats": await stats(db, org, key), "recent": await recent(db, org, key, 10)}
    if connection is None:
        return out
    manage = principal.can("admin.settings")
    out["webhook_url"] = core.webhook_url(key, connection.webhook_token)
    out["webhook_mode"] = config.get("webhook_mode") or ("webhook" if key == "chargebee" else None)
    if key == "stripe":
        out["account"] = {"name": config.get("account_name"), "id": config.get("account_id"), "mode": config.get("mode"), "key_type": config.get("key_type"),
                          "default_currency": config.get("default_currency"), "country": config.get("country"), "charges_enabled": config.get("charges_enabled")}
        out["webhook_ready"] = bool(config.get("webhook_endpoint_id")) and bool(core.secret(connection, "webhook_secret"))
    elif key == "paypal":
        out["account"] = {"mode": config.get("environment"), "app_id": config.get("app_id"), "client_id": config.get("client_id") if manage else None}
        out["webhook_ready"] = bool(config.get("webhook_id"))
    elif key == "square":
        out["account"] = {"name": config.get("business_name"), "mode": config.get("environment"), "location_name": config.get("location_name"),
                          "location_id": config.get("location_id"), "currency": config.get("currency"), "country": config.get("country")}
        out["webhook_ready"] = bool(core.secret(connection, "webhook_signature_key"))
        out["webhook_auto"] = bool(config.get("webhook_subscription_id"))
    elif key == "chargebee":
        out["account"] = {"site": config.get("site"), "mode": "test" if config.get("test_site") else "live"}
        out["synced_at"] = config.get("synced_at")
        out["subscriptions"] = await subscription_summary(db, org)
        out["accounts_linked"] = (
            await db.execute(
                select(func.count()).select_from(IntegrationRecordLink).where(
                    IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == "chargebee", IntegrationRecordLink.external_type == "customer",
                )
            )
        ).scalar_one()
        out["webhook_ready"] = connection.last_sync_at is not None
    return out


async def run_due(db: AsyncSession) -> int:
    """Catch up on payments whose webhook never arrived, and sync Chargebee. Bounded per run."""

    now = pay.utcnow()
    handled = 0
    links = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.provider.in_(pay.PROCESSORS), IntegrationRecordLink.external_type == pay.LINK,
                IntegrationRecordLink.external_parent == "", IntegrationRecordLink.data["state"].astext == "open",
                (IntegrationRecordLink.last_synced_at.is_(None)) | (IntegrationRecordLink.last_synced_at < now - CHECK_EVERY),
                IntegrationRecordLink.created_at < now - timedelta(minutes=2),
            ).order_by(IntegrationRecordLink.last_synced_at.asc().nulls_first()).limit(LINKS_PER_RUN * 3)
        )
    ).scalars().all()
    checked = 0
    connections: dict[tuple[uuid.UUID, str], IntegrationConnection | None] = {}
    for link in links:
        if checked >= LINKS_PER_RUN:
            break
        if link.created_at and link.created_at < now - timedelta(days=30) and link.last_synced_at and link.last_synced_at > now - CHECK_EVERY_OLD:
            continue  # older links are checked once a day
        link.last_synced_at = now
        invoice = await db.get(SalesInvoice, link.record_id)
        if invoice is None or invoice.status in {"paid", "void"}:
            link.data = {**(link.data or {}), "state": "paid" if invoice is not None and invoice.status == "paid" else "closed"}
            continue
        cache_key = (link.organization_id, link.provider)
        if cache_key not in connections:
            enabled = await core.is_enabled(db, link.organization_id, link.provider)
            connection = await core.connection_for(db, link.organization_id, link.provider) if enabled else None
            connections[cache_key] = connection if connection is not None and connection.status != "error" else None
        connection = connections[cache_key]
        if connection is None:
            continue
        checked += 1
        link_id = link.id
        try:
            handled += await MODULES[link.provider].check_link(db, connection, link)
            link.last_error = None
            await db.commit()
        except IntegrationError as exc:
            link.last_error = str(exc)[:500]
            await db.commit()
        except Exception:  # noqa: BLE001 - e.g. a webhook recorded the same payment at the same moment
            logger.exception("Checking %s payment link %s failed", link.provider, link_id)
            await db.rollback()
            # Move it to the back of the queue so one bad link can't stall the others.
            failed = await db.get(IntegrationRecordLink, link_id, populate_existing=True)
            if failed is not None:
                failed.last_synced_at = now
                failed.last_error = "Checking this link failed unexpectedly; it will be retried later."
            await db.commit()
            connections.clear()  # rolled back: reload connections on demand
    await db.commit()
    rows = (
        await db.execute(
            select(IntegrationConnection.id).where(IntegrationConnection.provider == "chargebee", IntegrationConnection.status == "connected")
            # Least recently looked at first (skipped ones included), so none waits forever.
            .order_by(core.scheduler_order()).limit(20)
        )
    ).scalars().all()
    examined: list[uuid.UUID] = []
    try:
        handled += await _sync_chargebee(db, rows, examined, now)
    finally:
        await db.rollback()
        await core.mark_seen_ids(db, examined)
    return handled


async def _sync_chargebee(db: AsyncSession, rows: list[uuid.UUID], examined: list[uuid.UUID], now: Any) -> int:
    handled = 0
    synced = 0
    for connection_id in rows:
        if synced >= CHARGEBEE_PER_RUN:
            break
        examined.append(connection_id)
        connection = await db.get(IntegrationConnection, connection_id)
        if connection is None or not _pay_chargebee.due(connection):
            continue
        if not await core.is_enabled(db, connection.organization_id, "chargebee"):
            continue
        synced += 1
        try:
            handled += await _pay_chargebee.sync(db, connection)
            connection.last_error = None
        except IntegrationError as exc:
            connection.last_error = str(exc)[:500]
            connection.config = {**(connection.config or {}), "synced_at": now.isoformat()}
            core.log(db, connection.organization_id, "chargebee", "sync_failed", str(exc), level="warning", connection=connection)
        except Exception:  # noqa: BLE001 - one site's problem mustn't stop the others
            logger.exception("Chargebee sync failed for %s", connection_id)
            await db.rollback()
            continue
        await db.commit()
    return handled


# ---------------------------------------------------------------------------
# Shared reads used by the panels and record components
# ---------------------------------------------------------------------------


async def stats(db: AsyncSession, organization_id: uuid.UUID, key: str) -> dict[str, Any]:
    since = pay.utcnow() - timedelta(days=30)
    rows = (
        await db.execute(
            select(IntegrationObject.object_type, IntegrationObject.currency, func.count(), func.coalesce(func.sum(IntegrationObject.amount), 0))
            .where(IntegrationObject.organization_id == organization_id, IntegrationObject.provider == key,
                   IntegrationObject.object_type.in_(("payment", "refund")), IntegrationObject.occurred_at >= since)
            .group_by(IntegrationObject.object_type, IntegrationObject.currency)
        )
    ).all()
    open_links = (
        await db.execute(
            select(func.count()).select_from(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.provider == key,
                IntegrationRecordLink.external_type == pay.LINK, IntegrationRecordLink.data["state"].astext == "open",
            )
        )
    ).scalar_one()
    return {
        "payments_30d": sum(int(count) for kind, _, count, _ in rows if kind == "payment"),
        "refunds_30d": sum(int(count) for kind, _, count, _ in rows if kind == "refund"),
        "received_30d": [{"currency": currency, "amount": str(Decimal(total))} for kind, currency, _, total in rows if kind == "payment" and currency],
        "open_links": int(open_links),
    }


async def recent(db: AsyncSession, organization_id: uuid.UUID, key: str | None, limit: int = 10, *, account_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    query = select(IntegrationObject).where(IntegrationObject.organization_id == organization_id, IntegrationObject.object_type.in_(("payment", "refund")))
    if key:
        query = query.where(IntegrationObject.provider == key)
    else:
        query = query.where(IntegrationObject.provider.in_(tuple(MODULES)))
    if account_id is not None:
        query = query.where(IntegrationObject.account_id == account_id)
    rows = (await db.execute(query.order_by(IntegrationObject.occurred_at.desc().nulls_last()).limit(limit))).scalars().all()
    refs = await resolve_many(db, organization_id, [("invoice", r.record_id) for r in rows if r.record_type == pay.INVOICE and r.record_id])
    return [pay.object_out(r, refs) for r in rows]


async def subscription_summary(db: AsyncSession, organization_id: uuid.UUID, account_id: uuid.UUID | None = None) -> dict[str, Any]:
    query = select(IntegrationObject).where(
        IntegrationObject.organization_id == organization_id, IntegrationObject.provider == "chargebee", IntegrationObject.object_type == "subscription",
    )
    if account_id is not None:
        query = query.where(IntegrationObject.account_id == account_id)
    rows = (await db.execute(query.order_by(IntegrationObject.external_updated_at.desc().nulls_last()).limit(500 if account_id is None else 100))).scalars().all()
    counts: dict[str, int] = {}
    mrr: dict[str, Decimal] = {}
    renewals = []
    for row in rows:
        counts[row.status or "unknown"] = counts.get(row.status or "unknown", 0) + 1
        if row.status in _pay_chargebee.LIVE_STATES and row.currency and row.amount is not None:
            mrr[row.currency] = mrr.get(row.currency, Decimal(0)) + Decimal(row.amount)
        term = (row.data or {}).get("next_billing_at") or (row.data or {}).get("current_term_end")
        if row.status in {"active", "non_renewing", "in_trial"} and term:
            renewals.append(term)
    out: dict[str, Any] = {
        "counts": counts,
        "mrr": [{"currency": currency, "amount": str(pay.money(amount))} for currency, amount in sorted(mrr.items())],
        "next_renewal": min(renewals) if renewals else None,
    }
    if account_id is not None:
        out["items"] = [pay.object_out(r) for r in rows]
    return out


async def processors(db: AsyncSession, organization_id: uuid.UUID) -> list[dict[str, Any]]:
    out = []
    for key in pay.PROCESSORS:
        enabled = await core.is_enabled(db, organization_id, key)
        connection = await core.connection_for(db, organization_id, key) if enabled else None
        config = (connection.config or {}) if connection else {}
        out.append({
            "key": key,
            "name": pay.NAMES[key],
            "enabled": enabled,
            "connected": connection is not None and connection.status == "connected",
            "mode": config.get("mode") or config.get("environment"),
            "currency": config.get("currency") if key == "square" else None,
        })
    return out


async def create_link(db: AsyncSession, principal: Any, invoice: SalesInvoice, key: str, message: str | None) -> IntegrationRecordLink:
    if key not in pay.PROCESSORS:
        raise IntegrationError("Choose Stripe, PayPal or Square.", 422)
    if invoice.status in {"paid", "void"}:
        raise IntegrationError(f"This invoice is {invoice.status}.", 409)
    amount = pay.balance(invoice)
    if amount <= 0:
        raise IntegrationError("Nothing is due on this invoice.", 409)
    connection = await core.active_connection(db, invoice.organization_id, key)
    settings = await pay.settings(db, invoice.organization_id, key)
    link = await MODULES[key].create_link(db, connection, invoice, amount, user_id=principal.user_id, message=(message or settings.get("link_message") or None))
    core.log(db, invoice.organization_id, key, "link_created", f"{pay.NAMES[key]} payment link for {pay.invoice_label(invoice)} ({pay.fmt(amount, invoice.currency)}).",
             level="success", connection=connection, user_id=principal.user_id, detail={"invoice_id": str(invoice.id)})
    connection.last_used_at = pay.utcnow()
    return link


async def close_link(db: AsyncSession, principal: Any, invoice: SalesInvoice, key: str) -> None:
    link = await pay.link_for(db, invoice.organization_id, key, invoice.id)
    if link is None:
        raise IntegrationError("There's no payment link for this invoice.", 404)
    if (link.data or {}).get("state") == "open":
        connection = await core.active_connection(db, invoice.organization_id, key)
        await MODULES[key].deactivate_link(db, connection, link)
    link.data = {**(link.data or {}), "state": "closed"}
    core.log(db, invoice.organization_id, key, "link_closed", f"Closed the {pay.NAMES[key]} payment link for {pay.invoice_label(invoice)}.", user_id=principal.user_id)


async def check_invoice(db: AsyncSession, invoice: SalesInvoice) -> int:
    """Ask each processor about this invoice now (the "Check for payments" button)."""

    applied = 0
    links = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == invoice.organization_id, IntegrationRecordLink.record_type == pay.INVOICE,
                IntegrationRecordLink.record_id == invoice.id, IntegrationRecordLink.external_type == pay.LINK,
                IntegrationRecordLink.provider.in_(pay.PROCESSORS),
            )
        )
    ).scalars().all()
    for link in links:
        if not await core.is_enabled(db, invoice.organization_id, link.provider):
            continue
        connection = await core.connection_for(db, invoice.organization_id, link.provider)
        if connection is None:
            continue
        applied += await MODULES[link.provider].check_link(db, connection, link)
        link.last_synced_at = pay.utcnow()
    # Refunds made in the provider's dashboard.
    payments = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == invoice.organization_id, IntegrationObject.object_type == "payment",
                IntegrationObject.record_type == pay.INVOICE, IntegrationObject.record_id == invoice.id,
            )
        )
    ).scalars().all()
    for payment in payments:
        if not await core.is_enabled(db, invoice.organization_id, payment.provider):
            continue
        connection = await core.connection_for(db, invoice.organization_id, payment.provider)
        if connection is None:
            continue
        if payment.provider == "stripe" and (payment.data or {}).get("charge"):
            applied += await _pay_stripe.process_refunds(db, connection, str(payment.data["charge"]))
        elif payment.provider == "square":
            found = (await _pay_square.call(connection, "GET", f"/v2/payments/{payment.external_id}")).get("payment") or {}
            for refund_id in found.get("refund_ids") or []:
                applied += 1 if await _pay_square.process_refund(db, connection, str(refund_id)) else 0
    return applied


async def refund(db: AsyncSession, payment: IntegrationObject, amount: Decimal, reason: str | None) -> str:
    if payment.object_type != "payment" or payment.provider not in pay.PROCESSORS:
        raise IntegrationError("Only Stripe, PayPal and Square payments can be refunded from AIVA.", 422)
    refunded = pay.money((payment.data or {}).get("refunded") or 0)
    left = pay.money(payment.amount or 0) - refunded
    if amount <= 0 or amount > left:
        raise IntegrationError(f"You can refund up to {pay.fmt(left, payment.currency or '')}.", 422)
    connection = await core.active_connection(db, payment.organization_id, payment.provider)
    return await MODULES[payment.provider].refund(db, connection, payment, amount, reason)
