"""Inventory, invoices and QuickBooks export."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.editions import SalesInvoice, StockMovement
from app.models.organization import Organization
from app.models.platform import Notification, Product, Quote
from app.services.automation import record_audit
from app.services.lookups import names_for

router = APIRouter(tags=["Operations"])

CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Inventory
# ===========================================================================


def stock_out(product: Product) -> dict[str, Any]:
    stock = float(product.stock_on_hand or 0)
    reorder = float(product.reorder_level or 0)
    return {
        "id": str(product.id),
        "name": product.name,
        "sku": product.sku,
        "family": product.family,
        "unit_price": float(product.unit_price),
        "unit_cost": float(product.unit_cost) if product.unit_cost is not None else None,
        "track_inventory": product.track_inventory,
        "stock_on_hand": stock,
        "reorder_level": reorder,
        "status": "out" if product.track_inventory and stock <= 0 else "low" if product.track_inventory and stock <= reorder else "ok",
        "stock_value": round(stock * float(product.unit_cost if product.unit_cost is not None else product.unit_price), 2),
        "is_active": product.is_active,
    }


@router.get("/inventory", dependencies=[Depends(require_feature("inventory"))])
async def inventory(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    products = (
        await db.execute(select(Product).where(Product.organization_id == principal.organization_id).order_by(Product.name))
    ).scalars().all()
    rows = [stock_out(p) for p in products]
    tracked = [r for r in rows if r["track_inventory"]]
    return {
        "items": rows,
        "summary": {
            "tracked": len(tracked),
            "low": len([r for r in tracked if r["status"] == "low"]),
            "out": len([r for r in tracked if r["status"] == "out"]),
            "stock_value": round(sum(r["stock_value"] for r in tracked), 2),
        },
        "can_manage": principal.can("sales.inventory"),
    }


class InventorySettings(BaseModel):
    track_inventory: bool
    reorder_level: Decimal = Field(default=Decimal("0"), ge=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)


async def _product(db: AsyncSession, principal: Principal, product_id: uuid.UUID) -> Product:
    product = await db.get(Product, product_id)
    if product is None or product.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


@router.patch("/inventory/products/{product_id}", dependencies=[Depends(require_feature("inventory"))])
async def update_inventory_settings(
    product_id: uuid.UUID,
    payload: InventorySettings,
    principal: Principal = Depends(require_permission("sales.inventory")),
    db: AsyncSession = Depends(get_db),
):
    product = await _product(db, principal, product_id)
    product.track_inventory = payload.track_inventory
    product.reorder_level = payload.reorder_level
    product.unit_cost = payload.unit_cost
    await db.commit()
    return stock_out(product)


class MovementPayload(BaseModel):
    kind: str = Field(pattern="^(receive|adjust|return|count)$")
    quantity: Decimal = Field(max_digits=14, decimal_places=2)
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


def _low_stock_notice(db: AsyncSession, product: Product, user_id: uuid.UUID | None) -> None:
    if user_id is None or not product.track_inventory:
        return
    if Decimal(product.stock_on_hand) <= Decimal(product.reorder_level):
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=product.organization_id,
                user_id=user_id,
                kind="inventory",
                title=f"Low stock: {product.name}"[:200],
                body=f"{float(product.stock_on_hand):g} left (reorder at {float(product.reorder_level):g}).",
                link="/inventory",
                is_read=False,
            )
        )


@router.post("/inventory/products/{product_id}/movements", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("inventory"))])
async def add_movement(
    product_id: uuid.UUID,
    payload: MovementPayload,
    principal: Principal = Depends(require_permission("sales.inventory")),
    db: AsyncSession = Depends(get_db),
):
    product = await _product(db, principal, product_id)
    if not product.track_inventory:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn on stock tracking for this product first.")
    quantity = Decimal(payload.quantity)
    current = Decimal(product.stock_on_hand or 0)
    if payload.kind in {"receive", "return"}:
        if quantity <= 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter how many units arrived.")
        change = quantity
    elif payload.kind == "count":
        if quantity < 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A stock count can't be negative.")
        change = quantity - current
    else:
        if quantity == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a positive or negative adjustment.")
        change = quantity
    if current + change < 0:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only {float(current):g} in stock.")
    product.stock_on_hand = current + change
    movement = StockMovement(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        product_id=product.id,
        kind="adjust" if payload.kind == "count" else payload.kind,
        quantity=change,
        balance_after=product.stock_on_hand,
        reference=payload.reference,
        note=payload.note or ("Stock count" if payload.kind == "count" else None),
        created_by_user_id=principal.user_id,
    )
    db.add(movement)
    _low_stock_notice(db, product, principal.user_id)
    await db.commit()
    return stock_out(product)


@router.get("/inventory/movements", dependencies=[Depends(require_feature("inventory"))])
async def list_movements(
    product_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(StockMovement, Product.name).join(Product, Product.id == StockMovement.product_id).where(
        StockMovement.organization_id == principal.organization_id
    )
    if product_id:
        query = query.where(StockMovement.product_id == product_id)
    rows = (await db.execute(query.order_by(StockMovement.created_at.desc()).limit(200))).all()
    users = await names_for(db, principal.organization_id, "user", [m.created_by_user_id for m, _ in rows])
    return [
        {
            "id": str(m.id),
            "product_id": str(m.product_id),
            "product_name": name,
            "kind": m.kind,
            "quantity": float(m.quantity),
            "balance_after": float(m.balance_after),
            "reference": m.reference,
            "note": m.note,
            "by": users.get(str(m.created_by_user_id)),
            "created_at": m.created_at,
        }
        for m, name in rows
    ]


async def apply_quote_sale(db: AsyncSession, quote: Quote, actor: uuid.UUID | None) -> list[str]:
    """Take accepted quote lines out of stock. Returns warnings for products that ran short."""

    warnings = []
    for line in quote.line_items:
        if line.product_id is None:
            continue
        product = await db.get(Product, line.product_id)
        if product is None or not product.track_inventory:
            continue
        quantity = Decimal(line.quantity)
        before = Decimal(product.stock_on_hand or 0)
        product.stock_on_hand = before - quantity
        if product.stock_on_hand < 0:
            warnings.append(f"{product.name} is now {float(product.stock_on_hand):g} (backordered)")
        db.add(
            StockMovement(
                id=uuid.uuid4(),
                organization_id=quote.organization_id,
                product_id=product.id,
                kind="sale",
                quantity=-quantity,
                balance_after=product.stock_on_hand,
                reference=f"Q-{quote.number}",
                note=f"Quote “{quote.name}” accepted",
                created_by_user_id=actor,
            )
        )
        _low_stock_notice(db, product, quote.owner_user_id or actor)
    return warnings


# ===========================================================================
# Invoices
# ===========================================================================


async def invoice_out(db: AsyncSession, invoice: SalesInvoice, detail: bool = False) -> dict[str, Any]:
    org = invoice.organization_id
    accounts = await names_for(db, org, "account", [invoice.account_id])
    contacts = await names_for(db, org, "contact", [invoice.contact_id])
    owners = await names_for(db, org, "user", [invoice.owner_user_id])
    today = date.today()
    effective = invoice.status
    if invoice.status == "sent" and invoice.due_date and invoice.due_date < today:
        effective = "overdue"
    data = {
        "id": str(invoice.id),
        "number": invoice.number,
        "display_number": f"INV-{invoice.number}",
        "status": effective,
        "quote_id": str(invoice.quote_id) if invoice.quote_id else None,
        "account_id": str(invoice.account_id) if invoice.account_id else None,
        "account_name": accounts.get(str(invoice.account_id)),
        "contact_id": str(invoice.contact_id) if invoice.contact_id else None,
        "contact_name": contacts.get(str(invoice.contact_id)),
        "owner_name": owners.get(str(invoice.owner_user_id)),
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal": float(invoice.subtotal),
        "discount_total": float(invoice.discount_total),
        "tax_total": float(invoice.tax_total),
        "total": float(invoice.total),
        "amount_paid": float(invoice.amount_paid),
        "balance": float(money(invoice.total) - money(invoice.amount_paid)),
        "paid_at": invoice.paid_at,
        "exported_at": invoice.exported_at,
        "created_at": invoice.created_at,
    }
    if detail:
        data["line_items"] = invoice.line_items
        data["terms"] = invoice.terms
        data["notes"] = invoice.notes
        organization = await db.get(Organization, org)
        data["organization_name"] = organization.name if organization else ""
        if invoice.account_id:
            account = await db.get(Account, invoice.account_id)
            data["bill_to"] = {
                "name": account.name,
                "city": account.billing_city,
                "state": account.billing_state,
                "postal_code": account.billing_postal_code,
                "country": account.billing_country,
            } if account else None
    return data


@router.get("/invoices", dependencies=[Depends(require_feature("cpq"))])
async def list_invoices(
    status_filter: str | None = Query(default=None, alias="status"),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(SalesInvoice).where(SalesInvoice.organization_id == principal.organization_id)
    if status_filter == "overdue":
        query = query.where(SalesInvoice.status == "sent", SalesInvoice.due_date < date.today())
    elif status_filter:
        query = query.where(SalesInvoice.status == status_filter)
    invoices = (await db.execute(query.order_by(SalesInvoice.number.desc()).limit(500))).scalars().all()
    items = [await invoice_out(db, invoice) for invoice in invoices]
    all_invoices = (
        await db.execute(select(SalesInvoice).where(SalesInvoice.organization_id == principal.organization_id))
    ).scalars().all()
    today = date.today()
    return {
        "items": items,
        "summary": {
            "outstanding": float(sum(money(i.total) - money(i.amount_paid) for i in all_invoices if i.status == "sent")),
            "overdue": float(sum(money(i.total) - money(i.amount_paid) for i in all_invoices if i.status == "sent" and i.due_date and i.due_date < today)),
            "paid_30d": float(sum(money(i.amount_paid) for i in all_invoices if i.paid_at and i.paid_at >= utcnow() - timedelta(days=30))),
            "drafts": len([i for i in all_invoices if i.status == "draft"]),
        },
    }


async def _next_number(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return (
        await db.execute(select(func.coalesce(func.max(SalesInvoice.number), 1000)).where(SalesInvoice.organization_id == organization_id))
    ).scalar_one() + 1


class FromQuote(BaseModel):
    due_in_days: int = Field(default=30, ge=0, le=365)


@router.post("/invoices/from-quote/{quote_id}", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("cpq"))])
async def invoice_from_quote(
    quote_id: uuid.UUID,
    payload: FromQuote,
    principal: Principal = Depends(require_permission("sales.invoices")),
    db: AsyncSession = Depends(get_db),
):
    quote = await db.get(Quote, quote_id)
    if quote is None or quote.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found.")
    if quote.status != "accepted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Invoice a quote once the customer has accepted it.")
    existing = (
        await db.execute(select(SalesInvoice).where(SalesInvoice.quote_id == quote.id, SalesInvoice.status != "void"))
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This quote is already on INV-{existing.number}.")
    invoice = SalesInvoice(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        number=await _next_number(db, principal.organization_id),
        quote_id=quote.id,
        account_id=quote.account_id,
        contact_id=quote.contact_id,
        opportunity_id=quote.opportunity_id,
        owner_user_id=quote.owner_user_id or principal.user_id,
        status="draft",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=payload.due_in_days),
        currency=quote.currency,
        subtotal=quote.subtotal,
        discount_total=quote.discount_total,
        tax_total=quote.tax_total,
        total=quote.total,
        amount_paid=Decimal("0"),
        terms=quote.terms or f"Net {payload.due_in_days}",
        notes=quote.notes,
        line_items=[
            {
                "name": line.name,
                "description": line.description,
                "product_id": str(line.product_id) if line.product_id else None,
                "quantity": float(line.quantity),
                "unit_price": float(line.unit_price),
                "discount_percent": float(line.discount_percent),
                "tax_percent": float(line.tax_percent),
                "line_total": float(line.line_total),
            }
            for line in quote.line_items
        ],
    )
    db.add(invoice)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="invoice", entity_id=invoice.id, entity_label=f"INV-{invoice.number}")
    await db.commit()
    return await invoice_out(db, invoice, detail=True)


async def _invoice(db: AsyncSession, principal: Principal, invoice_id: uuid.UUID) -> SalesInvoice:
    invoice = await db.get(SalesInvoice, invoice_id)
    if invoice is None or invoice.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found.")
    return invoice


@router.get("/invoices/{invoice_id}", dependencies=[Depends(require_feature("cpq"))])
async def get_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    return await invoice_out(db, await _invoice(db, principal, invoice_id), detail=True)


class InvoiceUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(draft|sent|void)$")
    due_date: date | None = None
    terms: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=5000)


@router.patch("/invoices/{invoice_id}", dependencies=[Depends(require_feature("cpq"))])
async def update_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceUpdate,
    principal: Principal = Depends(require_permission("sales.invoices")),
    db: AsyncSession = Depends(get_db),
):
    invoice = await _invoice(db, principal, invoice_id)
    if invoice.status in {"paid", "void"} and payload.status not in (None, invoice.status):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This invoice is {invoice.status}.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(invoice, key, value)
    await db.commit()
    return await invoice_out(db, invoice, detail=True)


class PaymentPayload(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    paid_on: date | None = None


@router.post("/invoices/{invoice_id}/payments", dependencies=[Depends(require_feature("cpq"))])
async def record_payment(
    invoice_id: uuid.UUID,
    payload: PaymentPayload,
    principal: Principal = Depends(require_permission("sales.invoices")),
    db: AsyncSession = Depends(get_db),
):
    invoice = await _invoice(db, principal, invoice_id)
    if invoice.status in {"void", "draft"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Send the invoice before recording a payment." if invoice.status == "draft" else "This invoice is void.")
    balance = money(invoice.total) - money(invoice.amount_paid)
    if money(payload.amount) > balance:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The balance is only {balance:,.2f}.")
    invoice.amount_paid = money(invoice.amount_paid) + money(payload.amount)
    if invoice.amount_paid >= money(invoice.total):
        invoice.status = "paid"
        invoice.paid_at = datetime.combine(payload.paid_on, time(12), tzinfo=timezone.utc) if payload.paid_on else utcnow()
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="payment",
        entity_type="invoice",
        entity_id=invoice.id,
        entity_label=f"INV-{invoice.number}",
        changes={"amount": float(payload.amount)},
    )
    await db.commit()
    return await invoice_out(db, invoice, detail=True)


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.invoices")),
    db: AsyncSession = Depends(get_db),
):
    invoice = await _invoice(db, principal, invoice_id)
    if invoice.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft invoices can be deleted. Void it instead.")
    await db.delete(invoice)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# QuickBooks Online export
# ===========================================================================


def _csv_response(rows: list[list[Any]], filename: str) -> Response:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/quickbooks/customers.csv", dependencies=[Depends(require_feature("quickbooks"))])
async def quickbooks_customers(
    principal: Principal = Depends(require_permission("records.export")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    account_ids = (
        await db.execute(select(SalesInvoice.account_id).where(SalesInvoice.organization_id == org, SalesInvoice.account_id.is_not(None)).distinct())
    ).scalars().all()
    accounts = (await db.execute(select(Account).where(Account.id.in_(account_ids or [uuid.uuid4()])).order_by(Account.name))).scalars().all()
    primary = {}
    for contact in (await db.execute(select(Contact).where(Contact.account_id.in_([a.id for a in accounts] or [uuid.uuid4()])))).scalars():
        if contact.account_id not in primary or contact.is_primary:
            primary[contact.account_id] = contact
    rows: list[list[Any]] = [["Name", "Company", "Email", "Phone", "Website", "Billing City", "Billing State", "Billing ZIP", "Billing Country"]]
    for account in accounts:
        contact = primary.get(account.id)
        rows.append([
            account.name,
            account.name,
            contact.email if contact else "",
            account.phone or (contact.phone if contact else "") or "",
            account.website or "",
            account.billing_city or "",
            account.billing_state or "",
            account.billing_postal_code or "",
            account.billing_country or "",
        ])
    return _csv_response(rows, "quickbooks-customers.csv")


@router.get("/quickbooks/invoices.csv", dependencies=[Depends(require_feature("quickbooks"))])
async def quickbooks_invoices(
    include_exported: bool = False,
    principal: Principal = Depends(require_permission("records.export")),
    db: AsyncSession = Depends(get_db),
):
    query = select(SalesInvoice).where(SalesInvoice.organization_id == principal.organization_id, SalesInvoice.status.in_(("sent", "paid")))
    if not include_exported:
        query = query.where(SalesInvoice.exported_at.is_(None))
    invoices = (await db.execute(query.order_by(SalesInvoice.number))).scalars().all()
    accounts = await names_for(db, principal.organization_id, "account", [i.account_id for i in invoices])
    rows: list[list[Any]] = [[
        "InvoiceNo", "Customer", "InvoiceDate", "DueDate", "Terms", "Memo", "Item(Product/Service)",
        "ItemDescription", "ItemQuantity", "ItemRate", "ItemAmount", "TaxAmount", "Currency",
    ]]
    now = utcnow()
    for invoice in invoices:
        for line in invoice.line_items or []:
            gross = Decimal(str(line.get("quantity", 0))) * Decimal(str(line.get("unit_price", 0)))
            net = money(line.get("line_total", gross))
            tax = money(net * Decimal(str(line.get("tax_percent", 0))) / 100)
            rows.append([
                f"INV-{invoice.number}",
                accounts.get(str(invoice.account_id), ""),
                invoice.issue_date.strftime("%m/%d/%Y"),
                invoice.due_date.strftime("%m/%d/%Y") if invoice.due_date else "",
                invoice.terms or "",
                (invoice.notes or "")[:200],
                line.get("name", ""),
                line.get("description") or "",
                f"{line.get('quantity', 0):g}",
                f"{money(net / Decimal(str(line.get('quantity') or 1))):.2f}",
                f"{net:.2f}",
                f"{tax:.2f}",
                invoice.currency,
            ])
        invoice.exported_at = now
    await db.commit()
    return _csv_response(rows, "quickbooks-invoices.csv")
