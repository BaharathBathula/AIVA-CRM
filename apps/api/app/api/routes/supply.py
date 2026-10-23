"""Vendors, price books, sales orders and purchase orders.

Order flow: an accepted quote becomes a sales order, the order is confirmed,
fulfilled (stock leaves the shelf) and invoiced. Purchasing: a purchase order
is raised with a vendor, issued, and goods are received against it, which
puts stock back on the shelf and updates the product's cost.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.editions import SalesInvoice, StockMovement
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.platform import Notification, Product, Quote
from app.models.supply import PriceBook, PriceBookEntry, PurchaseOrder, SalesOrder, Vendor
from app.services.automation import record_audit
from app.services.lookups import names_for

router = APIRouter(tags=["Orders and purchasing"])

CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id(value: Any) -> str | None:
    return str(value) if value else None


async def _owned(db: AsyncSession, model: type, record_id: Any, organization_id: uuid.UUID, label: str):
    if record_id is None:
        return None
    record = await db.get(model, record_id)
    if record is None or record.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found.")
    return record


async def _check_links(db: AsyncSession, organization_id: uuid.UUID, data: dict[str, Any]) -> None:
    for key, model, label in (
        ("account_id", Account, "The account"),
        ("contact_id", Contact, "The contact"),
        ("opportunity_id", Opportunity, "The opportunity"),
        ("price_book_id", PriceBook, "The price book"),
        ("vendor_id", Vendor, "The vendor"),
        ("sales_order_id", SalesOrder, "The sales order"),
    ):
        if data.get(key) is None:
            continue
        record = await db.get(model, data[key])
        if record is None or record.organization_id != organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label} isn't in this workspace.")


# ===========================================================================
# Line items shared by sales and purchase orders
# ===========================================================================


class LinePayload(BaseModel):
    product_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    sku: str | None = Field(default=None, max_length=80)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=12, decimal_places=2)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    tax_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class Address(BaseModel):
    street: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=30)
    country: str | None = Field(default=None, max_length=120)


async def build_lines(
    db: AsyncSession,
    organization_id: uuid.UUID,
    items: list[LinePayload],
    previous: list[dict] | None = None,
    keep_received: bool = False,
) -> list[dict]:
    product_ids = {item.product_id for item in items if item.product_id}
    products: dict[uuid.UUID, Product] = {}
    if product_ids:
        rows = (
            await db.execute(select(Product).where(Product.id.in_(product_ids), Product.organization_id == organization_id))
        ).scalars().all()
        products = {p.id: p for p in rows}
        missing = product_ids - set(products)
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "One of the products isn't in this workspace.")
    lines: list[dict] = []
    for index, item in enumerate(items):
        product = products.get(item.product_id) if item.product_id else None
        line = {
            "product_id": _id(item.product_id),
            "name": item.name.strip(),
            "description": (item.description or "").strip() or None,
            "sku": item.sku or (product.sku if product else None),
            "quantity": float(item.quantity),
            "unit_price": float(money(item.unit_price)),
            "discount_percent": float(item.discount_percent),
            "tax_percent": float(item.tax_percent),
        }
        if keep_received:
            received = 0.0
            if previous and index < len(previous) and previous[index].get("product_id") == line["product_id"]:
                received = float(previous[index].get("received_quantity") or 0)
            if received > line["quantity"]:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{line['name']}: {received:g} already received, so the quantity can't go below that.",
                )
            line["received_quantity"] = received
        lines.append(line)
    return lines


def apply_totals(record: SalesOrder | PurchaseOrder) -> None:
    subtotal = discount_total = tax_total = Decimal("0")
    lines = []
    for line in record.line_items or []:
        gross = money(Decimal(str(line["quantity"])) * Decimal(str(line["unit_price"])))
        discount = money(gross * Decimal(str(line.get("discount_percent", 0))) / 100)
        net = gross - discount
        tax = money(net * Decimal(str(line.get("tax_percent", 0))) / 100)
        lines.append({**line, "line_total": float(net)})
        subtotal += gross
        discount_total += discount
        tax_total += tax
    record.line_items = lines
    record.subtotal = money(subtotal)
    record.discount_total = money(discount_total)
    record.tax_total = money(tax_total)
    record.total = money(subtotal - discount_total + tax_total + money(record.shipping))


async def _next_number(db: AsyncSession, model: type, organization_id: uuid.UUID) -> int:
    current = (
        await db.execute(select(func.coalesce(func.max(model.number), 1000)).where(model.organization_id == organization_id))
    ).scalar_one()
    return int(current) + 1


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


# ===========================================================================
# Vendors
# ===========================================================================


class VendorPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=80)
    contact_name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=300)
    gl_account: str | None = Field(default=None, max_length=60)
    tax_id: str | None = Field(default=None, max_length=60)
    payment_terms: str | None = Field(default=None, max_length=60)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    street: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=30)
    country: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)
    is_active: bool = True
    owner_user_id: uuid.UUID | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and ("@" not in value or " " in value):
            raise ValueError("Enter a valid email address.")
        return value.lower() or None

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class VendorUpdate(VendorPayload):
    name: str | None = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]
    currency: str | None = Field(default=None, min_length=3, max_length=3)  # type: ignore[assignment]
    is_active: bool | None = None  # type: ignore[assignment]


async def vendor_out(db: AsyncSession, vendor: Vendor, stats: dict | None = None) -> dict[str, Any]:
    owners = await names_for(db, vendor.organization_id, "user", [vendor.owner_user_id])
    data = {
        "id": str(vendor.id),
        "name": vendor.name,
        "category": vendor.category,
        "contact_name": vendor.contact_name,
        "email": vendor.email,
        "phone": vendor.phone,
        "website": vendor.website,
        "gl_account": vendor.gl_account,
        "tax_id": vendor.tax_id,
        "payment_terms": vendor.payment_terms,
        "currency": vendor.currency,
        "street": vendor.street,
        "city": vendor.city,
        "state": vendor.state,
        "postal_code": vendor.postal_code,
        "country": vendor.country,
        "notes": vendor.notes,
        "is_active": vendor.is_active,
        "owner_user_id": _id(vendor.owner_user_id),
        "owner_name": owners.get(str(vendor.owner_user_id)),
        "created_at": vendor.created_at,
        "updated_at": vendor.updated_at,
    }
    if stats is not None:
        data.update(stats)
    return data


async def _vendor_stats(db: AsyncSession, organization_id: uuid.UUID, vendor_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not vendor_ids:
        return {}
    rows = (
        await db.execute(
            select(PurchaseOrder.vendor_id, PurchaseOrder.status, func.count(), func.coalesce(func.sum(PurchaseOrder.total), 0))
            .where(PurchaseOrder.organization_id == organization_id, PurchaseOrder.vendor_id.in_(vendor_ids))
            .group_by(PurchaseOrder.vendor_id, PurchaseOrder.status)
        )
    ).all()
    stats: dict[uuid.UUID, dict] = {v: {"open_orders": 0, "order_count": 0, "total_spend": 0.0} for v in vendor_ids}
    for vendor_id, order_status, count, total in rows:
        entry = stats[vendor_id]
        entry["order_count"] += count
        if order_status in {"issued", "partially_received"}:
            entry["open_orders"] += count
        if order_status in {"issued", "partially_received", "received"}:
            entry["total_spend"] += float(total)
    return stats


@router.get("/vendors", dependencies=[Depends(require_feature("purchase_orders"))])
async def list_vendors(
    search: str | None = Query(default=None, max_length=200),
    active: bool | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Vendor).where(Vendor.organization_id == principal.organization_id)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(Vendor.name.ilike(like), Vendor.email.ilike(like), Vendor.category.ilike(like), Vendor.contact_name.ilike(like)))
    if active is not None:
        query = query.where(Vendor.is_active.is_(active))
    vendors = (await db.execute(query.order_by(Vendor.name).limit(1000))).scalars().all()
    stats = await _vendor_stats(db, principal.organization_id, [v.id for v in vendors])
    return {
        "items": [await vendor_out(db, v, stats.get(v.id)) for v in vendors],
        "can_manage": principal.can("sales.purchasing"),
    }


@router.post("/vendors", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("purchase_orders"))])
async def create_vendor(
    payload: VendorPayload,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    data["name"] = data["name"].strip()
    vendor = Vendor(id=uuid.uuid4(), organization_id=principal.organization_id, **data)
    if vendor.owner_user_id is None:
        vendor.owner_user_id = principal.user_id
    db.add(vendor)
    await db.commit()
    return await vendor_out(db, vendor, {"open_orders": 0, "order_count": 0, "total_spend": 0.0})


@router.get("/vendors/{vendor_id}", dependencies=[Depends(require_feature("purchase_orders"))])
async def get_vendor(
    vendor_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    vendor = await _owned(db, Vendor, vendor_id, principal.organization_id, "Vendor")
    stats = await _vendor_stats(db, principal.organization_id, [vendor.id])
    orders = (
        await db.execute(
            select(PurchaseOrder)
            .where(PurchaseOrder.organization_id == principal.organization_id, PurchaseOrder.vendor_id == vendor.id)
            .order_by(PurchaseOrder.number.desc())
            .limit(200)
        )
    ).scalars().all()
    supplied: dict[str, dict] = {}
    for order in orders:
        if order.status == "cancelled":
            continue
        for line in order.line_items or []:
            key = line.get("product_id") or line["name"]
            entry = supplied.setdefault(key, {"product_id": line.get("product_id"), "name": line["name"], "quantity": 0.0, "last_cost": None})
            entry["quantity"] += float(line.get("quantity") or 0)
            if entry["last_cost"] is None:
                entry["last_cost"] = line.get("unit_price")
    data = await vendor_out(db, vendor, stats.get(vendor.id))
    data["purchase_orders"] = [await purchase_order_out(db, o) for o in orders]
    data["products"] = sorted(supplied.values(), key=lambda e: -e["quantity"])[:50]
    data["can_manage"] = principal.can("sales.purchasing")
    return data


@router.patch("/vendors/{vendor_id}", dependencies=[Depends(require_feature("purchase_orders"))])
async def update_vendor(
    vendor_id: uuid.UUID,
    payload: VendorUpdate,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    vendor = await _owned(db, Vendor, vendor_id, principal.organization_id, "Vendor")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key in {"name", "currency", "is_active"} and value is None:
            continue
        setattr(vendor, key, value.strip() if key == "name" else value)
    await db.commit()
    stats = await _vendor_stats(db, principal.organization_id, [vendor.id])
    return await vendor_out(db, vendor, stats.get(vendor.id))


@router.delete("/vendors/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("purchase_orders"))])
async def delete_vendor(
    vendor_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    vendor = await _owned(db, Vendor, vendor_id, principal.organization_id, "Vendor")
    used = (
        await db.execute(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.vendor_id == vendor.id))
    ).scalar_one()
    if used:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{vendor.name} has {used} purchase order{'s' if used != 1 else ''}. Mark the vendor inactive instead.",
        )
    await db.delete(vendor)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Price books
# ===========================================================================


class Tier(BaseModel):
    from_qty: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    to_qty: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    discount_percent: Decimal = Field(ge=0, le=100)


class EntryPayload(BaseModel):
    product_id: uuid.UUID
    list_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    tiers: list[Tier] = Field(default_factory=list, max_length=20)


class PriceBookPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    pricing_model: str = Field(default="flat", pattern="^(flat|differential)$")
    valid_from: date | None = None
    valid_until: date | None = None
    is_active: bool = True


class PriceBookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    pricing_model: str | None = Field(default=None, pattern="^(flat|differential)$")
    valid_from: date | None = None
    valid_until: date | None = None
    is_active: bool | None = None


def _check_dates(valid_from: date | None, valid_until: date | None) -> None:
    if valid_from and valid_until and valid_until < valid_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The end date must be on or after the start date.")


def _check_tiers(product_name: str, tiers: list[Tier], model: str) -> list[dict]:
    if model == "flat" and tiers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Volume tiers need the “Differential” pricing model. Switch the price book's model or remove the tiers.",
        )
    ordered = sorted(tiers, key=lambda t: t.from_qty)
    previous_end: Decimal | None = None
    for index, tier in enumerate(ordered):
        if tier.to_qty is not None and tier.to_qty < tier.from_qty:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{product_name}: a tier ends before it starts.")
        if index and (previous_end is None or tier.from_qty <= previous_end):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{product_name}: quantity tiers overlap.")
        previous_end = tier.to_qty
    return [
        {"from_qty": float(t.from_qty), "to_qty": float(t.to_qty) if t.to_qty is not None else None, "discount_percent": float(t.discount_percent)}
        for t in ordered
    ]


def tier_discount(tiers: list[dict], quantity: Decimal) -> Decimal:
    for tier in tiers or []:
        start = Decimal(str(tier["from_qty"]))
        end = Decimal(str(tier["to_qty"])) if tier.get("to_qty") is not None else None
        if quantity >= start and (end is None or quantity <= end):
            return Decimal(str(tier["discount_percent"]))
    return Decimal("0")


async def price_book_out(db: AsyncSession, book: PriceBook, detail: bool = False) -> dict[str, Any]:
    entries = (
        await db.execute(
            select(PriceBookEntry, Product)
            .join(Product, Product.id == PriceBookEntry.product_id)
            .where(PriceBookEntry.price_book_id == book.id)
            .order_by(Product.name)
        )
    ).all()
    today = date.today()
    current = book.is_active and (book.valid_from is None or book.valid_from <= today) and (book.valid_until is None or book.valid_until >= today)
    data = {
        "id": str(book.id),
        "name": book.name,
        "description": book.description,
        "currency": book.currency,
        "pricing_model": book.pricing_model,
        "valid_from": book.valid_from,
        "valid_until": book.valid_until,
        "is_active": book.is_active,
        "is_current": current,
        "entry_count": len(entries),
        "created_at": book.created_at,
        "updated_at": book.updated_at,
    }
    if detail:
        data["entries"] = [
            {
                "id": str(entry.id),
                "product_id": str(product.id),
                "product_name": product.name,
                "sku": product.sku,
                "standard_price": float(product.unit_price),
                "list_price": float(entry.list_price),
                "tiers": entry.tiers or [],
            }
            for entry, product in entries
        ]
    return data


@router.get("/price-books", dependencies=[Depends(require_feature("price_books"))])
async def list_price_books(
    active: bool | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(PriceBook).where(PriceBook.organization_id == principal.organization_id)
    if active is not None:
        query = query.where(PriceBook.is_active.is_(active))
    books = (await db.execute(query.order_by(PriceBook.name))).scalars().all()
    return {"items": [await price_book_out(db, b) for b in books], "can_manage": principal.can("sales.products")}


async def _unique_book_name(db: AsyncSession, organization_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> None:
    query = select(PriceBook.id).where(PriceBook.organization_id == organization_id, func.lower(PriceBook.name) == name.lower())
    if exclude:
        query = query.where(PriceBook.id != exclude)
    if (await db.execute(query)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"A price book called “{name}” already exists.")


@router.post("/price-books", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("price_books"))])
async def create_price_book(
    payload: PriceBookPayload,
    principal: Principal = Depends(require_permission("sales.products")),
    db: AsyncSession = Depends(get_db),
):
    _check_dates(payload.valid_from, payload.valid_until)
    name = payload.name.strip()
    await _unique_book_name(db, principal.organization_id, name)
    book = PriceBook(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        owner_user_id=principal.user_id,
        **{**payload.model_dump(), "name": name, "currency": payload.currency.upper()},
    )
    db.add(book)
    await db.commit()
    return await price_book_out(db, book, detail=True)


@router.get("/price-books/{book_id}", dependencies=[Depends(require_feature("price_books"))])
async def get_price_book(
    book_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned(db, PriceBook, book_id, principal.organization_id, "Price book")
    data = await price_book_out(db, book, detail=True)
    data["can_manage"] = principal.can("sales.products")
    return data


@router.patch("/price-books/{book_id}", dependencies=[Depends(require_feature("price_books"))])
async def update_price_book(
    book_id: uuid.UUID,
    payload: PriceBookUpdate,
    principal: Principal = Depends(require_permission("sales.products")),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned(db, PriceBook, book_id, principal.organization_id, "Price book")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name"):
        changes["name"] = changes["name"].strip()
        await _unique_book_name(db, principal.organization_id, changes["name"], exclude=book.id)
    if changes.get("pricing_model") == "flat" and book.pricing_model == "differential":
        tiered = (
            await db.execute(select(PriceBookEntry).where(PriceBookEntry.price_book_id == book.id))
        ).scalars().all()
        for entry in tiered:
            entry.tiers = []
    for key, value in changes.items():
        if key in {"name", "currency", "pricing_model", "is_active"} and value is None:
            continue
        setattr(book, key, value.upper() if key == "currency" else value)
    _check_dates(book.valid_from, book.valid_until)
    await db.commit()
    return await price_book_out(db, book, detail=True)


@router.put("/price-books/{book_id}/entries", dependencies=[Depends(require_feature("price_books"))])
async def replace_entries(
    book_id: uuid.UUID,
    payload: list[EntryPayload],
    principal: Principal = Depends(require_permission("sales.products")),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned(db, PriceBook, book_id, principal.organization_id, "Price book")
    if len({e.product_id for e in payload}) != len(payload):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Each product can appear once in a price book.")
    products = {
        p.id: p
        for p in (
            await db.execute(
                select(Product).where(Product.organization_id == principal.organization_id, Product.id.in_([e.product_id for e in payload] or [uuid.uuid4()]))
            )
        ).scalars()
    }
    if len(products) != len(payload):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One of the products isn't in this workspace.")
    existing = {
        e.product_id: e
        for e in (await db.execute(select(PriceBookEntry).where(PriceBookEntry.price_book_id == book.id))).scalars()
    }
    keep = set()
    for item in payload:
        tiers = _check_tiers(products[item.product_id].name, item.tiers, book.pricing_model)
        entry = existing.get(item.product_id)
        if entry is None:
            entry = PriceBookEntry(
                id=uuid.uuid4(), organization_id=principal.organization_id, price_book_id=book.id, product_id=item.product_id
            )
            db.add(entry)
        entry.list_price = money(item.list_price)
        entry.tiers = tiers
        keep.add(item.product_id)
    for product_id, entry in existing.items():
        if product_id not in keep:
            await db.delete(entry)
    book.updated_at = utcnow()
    await db.commit()
    return await price_book_out(db, book, detail=True)


@router.delete("/price-books/{book_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("price_books"))])
async def delete_price_book(
    book_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.products")),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned(db, PriceBook, book_id, principal.organization_id, "Price book")
    await db.delete(book)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/price-books/{book_id}/price", dependencies=[Depends(require_feature("price_books"))])
async def price_for_product(
    book_id: uuid.UUID,
    product_id: uuid.UUID,
    quantity: Decimal = Query(default=Decimal("1"), gt=0),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned(db, PriceBook, book_id, principal.organization_id, "Price book")
    product = await _owned(db, Product, product_id, principal.organization_id, "Product")
    entry = (
        await db.execute(select(PriceBookEntry).where(PriceBookEntry.price_book_id == book.id, PriceBookEntry.product_id == product.id))
    ).scalars().first()
    if entry is None:
        return {"in_book": False, "unit_price": float(product.unit_price), "discount_percent": 0.0}
    return {"in_book": True, "unit_price": float(entry.list_price), "discount_percent": float(tier_discount(entry.tiers, quantity))}


async def book_prices(db: AsyncSession, book_id: uuid.UUID | None) -> dict[str, PriceBookEntry]:
    if book_id is None:
        return {}
    rows = (await db.execute(select(PriceBookEntry).where(PriceBookEntry.price_book_id == book_id))).scalars().all()
    return {str(e.product_id): e for e in rows}


class QuotePriceBook(BaseModel):
    price_book_id: uuid.UUID | None = None


@router.post("/quotes/{quote_id}/price-book", dependencies=[Depends(require_feature("cpq", "price_books"))])
async def apply_price_book_to_quote(
    quote_id: uuid.UUID,
    payload: QuotePriceBook,
    principal: Principal = Depends(require_permission("sales.quotes")),
    db: AsyncSession = Depends(get_db),
):
    """Set the quote's price book and re-price every catalog line from it."""

    from app.api.routes.sales import quote_out, recalculate

    quote = await _owned(db, Quote, quote_id, principal.organization_id, "Quote")
    if quote.status in {"accepted", "declined"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This quote is {quote.status}. Duplicate it to change pricing.")
    if payload.price_book_id:
        book = await _owned(db, PriceBook, payload.price_book_id, principal.organization_id, "Price book")
        if not book.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "That price book is inactive.")
    quote.price_book_id = payload.price_book_id
    entries = await book_prices(db, payload.price_book_id)
    repriced = 0
    for line in quote.line_items:
        if line.product_id is None:
            continue
        entry = entries.get(str(line.product_id))
        if entry is not None:
            line.unit_price = entry.list_price
            line.discount_percent = tier_discount(entry.tiers, Decimal(line.quantity))
            repriced += 1
        elif payload.price_book_id is None:
            product = await db.get(Product, line.product_id)
            if product is not None:
                line.unit_price = product.unit_price
                line.discount_percent = Decimal("0")
                repriced += 1
    recalculate(quote)
    await db.commit()
    await db.refresh(quote)
    data = await quote_out(db, quote, detail=True)
    data["repriced_lines"] = repriced
    return data


# ===========================================================================
# Sales orders
# ===========================================================================

SO_STATUSES = ("draft", "confirmed", "fulfilled", "cancelled")


class SalesOrderPayload(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    price_book_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    order_date: date | None = None
    due_date: date | None = None
    customer_po: str | None = Field(default=None, max_length=80)
    carrier: str | None = Field(default=None, max_length=80)
    tracking_number: str | None = Field(default=None, max_length=120)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    shipping: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    billing_address: Address = Field(default_factory=Address)
    shipping_address: Address = Field(default_factory=Address)
    terms: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=5000)
    line_items: list[LinePayload] = Field(default_factory=list, max_length=500)


class SalesOrderUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    order_date: date | None = None
    due_date: date | None = None
    customer_po: str | None = Field(default=None, max_length=80)
    carrier: str | None = Field(default=None, max_length=80)
    tracking_number: str | None = Field(default=None, max_length=120)
    shipping: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    billing_address: Address | None = None
    shipping_address: Address | None = None
    terms: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=5000)
    line_items: list[LinePayload] | None = Field(default=None, max_length=500)


class SalesOrderStatus(BaseModel):
    status: str = Field(pattern="^(confirmed|fulfilled|cancelled)$")
    carrier: str | None = Field(default=None, max_length=80)
    tracking_number: str | None = Field(default=None, max_length=120)
    allow_backorder: bool = False


async def sales_order_out(db: AsyncSession, order: SalesOrder, detail: bool = False) -> dict[str, Any]:
    org = order.organization_id
    accounts = await names_for(db, org, "account", [order.account_id])
    contacts = await names_for(db, org, "contact", [order.contact_id])
    opps = await names_for(db, org, "opportunity", [order.opportunity_id])
    owners = await names_for(db, org, "user", [order.owner_user_id])
    overdue = order.status == "confirmed" and order.due_date is not None and order.due_date < date.today()
    data = {
        "id": str(order.id),
        "number": order.number,
        "display_number": f"SO-{order.number}",
        "subject": order.subject,
        "status": order.status,
        "is_overdue": overdue,
        "quote_id": _id(order.quote_id),
        "account_id": _id(order.account_id),
        "account_name": accounts.get(str(order.account_id)),
        "contact_id": _id(order.contact_id),
        "contact_name": contacts.get(str(order.contact_id)),
        "opportunity_id": _id(order.opportunity_id),
        "opportunity_name": opps.get(str(order.opportunity_id)),
        "price_book_id": _id(order.price_book_id),
        "owner_user_id": _id(order.owner_user_id),
        "owner_name": owners.get(str(order.owner_user_id)),
        "order_date": order.order_date,
        "due_date": order.due_date,
        "customer_po": order.customer_po,
        "carrier": order.carrier,
        "tracking_number": order.tracking_number,
        "currency": order.currency,
        "subtotal": float(order.subtotal),
        "discount_total": float(order.discount_total),
        "tax_total": float(order.tax_total),
        "shipping": float(order.shipping),
        "total": float(order.total),
        "line_count": len(order.line_items or []),
        "stock_applied": order.stock_applied,
        "confirmed_at": order.confirmed_at,
        "fulfilled_at": order.fulfilled_at,
        "cancelled_at": order.cancelled_at,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }
    if detail:
        quote = await db.get(Quote, order.quote_id) if order.quote_id else None
        invoices = (
            await db.execute(
                select(SalesInvoice).where(SalesInvoice.organization_id == org, SalesInvoice.sales_order_id == order.id).order_by(SalesInvoice.number)
            )
        ).scalars().all()
        pos = (
            await db.execute(
                select(PurchaseOrder).where(PurchaseOrder.organization_id == org, PurchaseOrder.sales_order_id == order.id).order_by(PurchaseOrder.number)
            )
        ).scalars().all()
        organization = await db.get(Organization, org)
        data.update(
            {
                "line_items": order.line_items or [],
                "billing_address": order.billing_address or {},
                "shipping_address": order.shipping_address or {},
                "terms": order.terms,
                "notes": order.notes,
                "quote": {"id": str(quote.id), "display_number": f"Q-{quote.number}", "name": quote.name} if quote else None,
                "invoices": [
                    {"id": str(i.id), "display_number": f"INV-{i.number}", "status": i.status, "total": float(i.total)} for i in invoices
                ],
                "purchase_orders": [
                    {"id": str(p.id), "display_number": f"PO-{p.number}", "status": p.status, "total": float(p.total)} for p in pos
                ],
                "organization_name": organization.name if organization else "",
            }
        )
    return data


async def _account_addresses(db: AsyncSession, account_id: uuid.UUID | None) -> tuple[dict, dict]:
    if not account_id:
        return {}, {}
    account = await db.get(Account, account_id)
    if account is None:
        return {}, {}
    street = ", ".join(filter(None, [account.billing_address_line1, account.billing_address_line2])) or None
    address = {
        "street": street,
        "city": account.billing_city,
        "state": account.billing_state,
        "postal_code": account.billing_postal_code,
        "country": account.billing_country,
    }
    return address, dict(address)


@router.get("/sales-orders", dependencies=[Depends(require_feature("sales_orders"))])
async def list_sales_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    account_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, max_length=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(SalesOrder).where(SalesOrder.organization_id == org)
    if status_filter == "overdue":
        query = query.where(SalesOrder.status == "confirmed", SalesOrder.due_date < date.today())
    elif status_filter in SO_STATUSES:
        query = query.where(SalesOrder.status == status_filter)
    if account_id:
        query = query.where(SalesOrder.account_id == account_id)
    if search:
        term = search.strip()
        conditions = [SalesOrder.subject.ilike(f"%{term}%"), SalesOrder.customer_po.ilike(f"%{term}%")]
        number = term.lower().removeprefix("so-")
        if number.isdigit():
            conditions.append(SalesOrder.number == int(number))
        query = query.where(or_(*conditions))
    orders = (await db.execute(query.order_by(SalesOrder.number.desc()).limit(500))).scalars().all()
    every = (
        await db.execute(select(SalesOrder.status, SalesOrder.total, SalesOrder.due_date, SalesOrder.fulfilled_at).where(SalesOrder.organization_id == org))
    ).all()
    today = date.today()
    since = utcnow() - timedelta(days=30)
    return {
        "items": [await sales_order_out(db, o) for o in orders],
        "summary": {
            "to_fulfil": float(sum(money(t) for s, t, _, _ in every if s == "confirmed")),
            "to_fulfil_count": len([1 for s, *_ in every if s == "confirmed"]),
            "overdue": len([1 for s, _, d, _ in every if s == "confirmed" and d and d < today]),
            "fulfilled_30d": float(sum(money(t) for s, t, _, f in every if s == "fulfilled" and f and f >= since)),
            "drafts": len([1 for s, *_ in every if s == "draft"]),
        },
        "can_manage": principal.can("sales.orders"),
    }


@router.post("/sales-orders", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("sales_orders"))])
async def create_sales_order(
    payload: SalesOrderPayload,
    principal: Principal = Depends(require_permission("sales.orders")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    data = payload.model_dump(exclude={"line_items", "billing_address", "shipping_address"})
    await _check_links(db, org, data)
    if data.get("opportunity_id") and not data.get("account_id"):
        data["account_id"] = (await db.get(Opportunity, data["opportunity_id"])).account_id
    billing, shipping = payload.billing_address.model_dump(), payload.shipping_address.model_dump()
    if not any(billing.values()) and data.get("account_id"):
        billing, default_shipping = await _account_addresses(db, data["account_id"])
        if not any(shipping.values()):
            shipping = default_shipping
    order = SalesOrder(
        id=uuid.uuid4(),
        organization_id=org,
        number=await _next_number(db, SalesOrder, org),
        **{
            **data,
            "subject": data["subject"].strip(),
            "currency": data["currency"].upper(),
            "order_date": data.get("order_date") or date.today(),
            "owner_user_id": data.get("owner_user_id") or principal.user_id,
        },
        billing_address=billing,
        shipping_address=shipping,
        status="draft",
    )
    order.line_items = await build_lines(db, org, payload.line_items)
    apply_totals(order)
    db.add(order)
    await db.commit()
    return await sales_order_out(db, order, detail=True)


@router.post("/sales-orders/from-quote/{quote_id}", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("sales_orders"))])
async def sales_order_from_quote(
    quote_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.orders")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    quote = await _owned(db, Quote, quote_id, org, "Quote")
    if quote.status != "accepted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Create a sales order once the customer has accepted the quote.")
    existing = (
        await db.execute(select(SalesOrder).where(SalesOrder.quote_id == quote.id, SalesOrder.status != "cancelled"))
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This quote is already on SO-{existing.number}.")
    stock_moved = (
        await db.execute(
            select(func.count()).select_from(StockMovement).where(
                StockMovement.organization_id == org, StockMovement.reference == f"Q-{quote.number}", StockMovement.kind == "sale"
            )
        )
    ).scalar_one()
    billing, shipping = await _account_addresses(db, quote.account_id)
    order = SalesOrder(
        id=uuid.uuid4(),
        organization_id=org,
        number=await _next_number(db, SalesOrder, org),
        subject=quote.name,
        status="draft",
        quote_id=quote.id,
        account_id=quote.account_id,
        contact_id=quote.contact_id,
        opportunity_id=quote.opportunity_id,
        price_book_id=quote.price_book_id,
        owner_user_id=quote.owner_user_id or principal.user_id,
        order_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        currency=quote.currency,
        shipping=Decimal("0"),
        billing_address=billing,
        shipping_address=shipping,
        terms=quote.terms,
        notes=quote.notes,
        stock_applied=bool(stock_moved),
        line_items=[
            {
                "product_id": _id(line.product_id),
                "name": line.name,
                "description": line.description,
                "sku": None,
                "quantity": float(line.quantity),
                "unit_price": float(line.unit_price),
                "discount_percent": float(line.discount_percent),
                "tax_percent": float(line.tax_percent),
            }
            for line in quote.line_items
        ],
    )
    apply_totals(order)
    db.add(order)
    record_audit(db, organization_id=org, action="create", entity_type="sales_order", entity_id=order.id, entity_label=f"SO-{order.number}", changes={"from_quote": f"Q-{quote.number}"})
    await db.commit()
    return await sales_order_out(db, order, detail=True)


@router.get("/sales-orders/{order_id}", dependencies=[Depends(require_feature("sales_orders"))])
async def get_sales_order(
    order_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    order = await _owned(db, SalesOrder, order_id, principal.organization_id, "Sales order")
    data = await sales_order_out(db, order, detail=True)
    data["can_manage"] = principal.can("sales.orders")
    return data


@router.patch("/sales-orders/{order_id}", dependencies=[Depends(require_feature("sales_orders"))])
async def update_sales_order(
    order_id: uuid.UUID,
    payload: SalesOrderUpdate,
    principal: Principal = Depends(require_permission("sales.orders")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    order = await _owned(db, SalesOrder, order_id, org, "Sales order")
    changes = payload.model_dump(exclude_unset=True)
    if order.status in {"fulfilled", "cancelled"}:
        allowed = {"carrier", "tracking_number", "notes"}
        if set(changes) - allowed:
            raise HTTPException(status.HTTP_409_CONFLICT, f"This order is {order.status}. Only shipping details and notes can change.")
    if "line_items" in changes and order.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Line items can only change while the order is a draft.")
    await _check_links(db, org, changes)
    if "line_items" in changes:
        order.line_items = await build_lines(db, org, payload.line_items or [])
    for key, value in changes.items():
        if key == "line_items":
            continue
        if key in {"billing_address", "shipping_address"}:
            value = value or {}
        if key == "subject" and value is None:
            continue
        if key == "shipping":
            value = money(value)
        setattr(order, key, value.strip() if key == "subject" else value)
    apply_totals(order)
    await db.commit()
    return await sales_order_out(db, order, detail=True)


async def _move_stock_out(db: AsyncSession, order: SalesOrder, actor: uuid.UUID, allow_backorder: bool) -> list[str]:
    products: dict[uuid.UUID, Product] = {}
    needed: dict[uuid.UUID, Decimal] = {}
    for line in order.line_items or []:
        if not line.get("product_id"):
            continue
        product = await db.get(Product, uuid.UUID(line["product_id"]))
        if product is None or product.organization_id != order.organization_id or not product.track_inventory:
            continue
        products[product.id] = product
        needed[product.id] = needed.get(product.id, Decimal("0")) + Decimal(str(line["quantity"]))
    short = [
        f"{products[pid].name} ({float(products[pid].stock_on_hand):g} in stock, {float(quantity):g} needed)"
        for pid, quantity in needed.items()
        if Decimal(products[pid].stock_on_hand or 0) < quantity
    ]
    if short and not allow_backorder:
        raise HTTPException(status.HTTP_409_CONFLICT, "Not enough stock: " + "; ".join(short) + ". Receive stock first or fulfil as a backorder.")
    for pid, quantity in needed.items():
        product = products[pid]
        product.stock_on_hand = Decimal(product.stock_on_hand or 0) - quantity
        db.add(
            StockMovement(
                id=uuid.uuid4(),
                organization_id=order.organization_id,
                product_id=product.id,
                kind="sale",
                quantity=-quantity,
                balance_after=product.stock_on_hand,
                reference=f"SO-{order.number}",
                note=f"Sales order “{order.subject}” fulfilled",
                created_by_user_id=actor,
            )
        )
        _low_stock_notice(db, product, order.owner_user_id or actor)
    return short


@router.post("/sales-orders/{order_id}/status", dependencies=[Depends(require_feature("sales_orders"))])
async def change_sales_order_status(
    order_id: uuid.UUID,
    payload: SalesOrderStatus,
    principal: Principal = Depends(require_permission("sales.orders")),
    db: AsyncSession = Depends(get_db),
):
    order = await _owned(db, SalesOrder, order_id, principal.organization_id, "Sales order")
    allowed = {
        "draft": {"confirmed", "cancelled"},
        "confirmed": {"fulfilled", "cancelled"},
        "fulfilled": set(),
        "cancelled": set(),
    }
    if payload.status not in allowed[order.status]:
        if order.status == "draft" and payload.status == "fulfilled":
            raise HTTPException(status.HTTP_409_CONFLICT, "Confirm the order before fulfilling it.")
        raise HTTPException(status.HTTP_409_CONFLICT, f"A {order.status} order can't be marked {payload.status}.")
    if payload.status == "confirmed" and not order.line_items:
        raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one line item before confirming.")
    warnings: list[str] = []
    now = utcnow()
    if payload.status == "confirmed":
        order.confirmed_at = now
    elif payload.status == "fulfilled":
        if not order.stock_applied:
            warnings = await _move_stock_out(db, order, principal.user_id, payload.allow_backorder)
            order.stock_applied = True
        order.fulfilled_at = now
        if payload.carrier is not None:
            order.carrier = payload.carrier
        if payload.tracking_number is not None:
            order.tracking_number = payload.tracking_number
    elif payload.status == "cancelled":
        order.cancelled_at = now
    previous = order.status
    order.status = payload.status
    record_audit(
        db,
        organization_id=order.organization_id,
        action="status",
        entity_type="sales_order",
        entity_id=order.id,
        entity_label=f"SO-{order.number}",
        changes={"status": {"from": previous, "to": payload.status}},
    )
    await db.commit()
    data = await sales_order_out(db, order, detail=True)
    data["stock_warnings"] = warnings
    return data


class InvoiceFromOrder(BaseModel):
    due_in_days: int = Field(default=30, ge=0, le=365)


@router.post("/invoices/from-sales-order/{order_id}", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("sales_orders", "cpq"))])
async def invoice_from_sales_order(
    order_id: uuid.UUID,
    payload: InvoiceFromOrder,
    principal: Principal = Depends(require_permission("sales.invoices")),
    db: AsyncSession = Depends(get_db),
):
    from app.api.routes.operations import invoice_out

    org = principal.organization_id
    order = await _owned(db, SalesOrder, order_id, org, "Sales order")
    if order.status not in {"confirmed", "fulfilled"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invoice an order once it's confirmed.")
    existing = (
        await db.execute(
            select(SalesInvoice).where(
                SalesInvoice.organization_id == org,
                SalesInvoice.status != "void",
                or_(SalesInvoice.sales_order_id == order.id, SalesInvoice.quote_id == order.quote_id)
                if order.quote_id
                else SalesInvoice.sales_order_id == order.id,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This order is already on INV-{existing.number}.")
    invoice = SalesInvoice(
        id=uuid.uuid4(),
        organization_id=org,
        number=await _next_number(db, SalesInvoice, org),
        quote_id=order.quote_id,
        sales_order_id=order.id,
        account_id=order.account_id,
        contact_id=order.contact_id,
        opportunity_id=order.opportunity_id,
        owner_user_id=order.owner_user_id or principal.user_id,
        status="draft",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=payload.due_in_days),
        currency=order.currency,
        subtotal=order.subtotal,
        discount_total=order.discount_total,
        tax_total=money(order.tax_total),
        total=order.total,
        amount_paid=Decimal("0"),
        terms=order.terms or f"Net {payload.due_in_days}",
        notes=order.notes,
        line_items=list(order.line_items or [])
        + ([{"name": "Shipping", "description": order.carrier, "product_id": None, "quantity": 1, "unit_price": float(order.shipping), "discount_percent": 0, "tax_percent": 0, "line_total": float(order.shipping)}] if money(order.shipping) > 0 else []),
    )
    db.add(invoice)
    record_audit(db, organization_id=org, action="create", entity_type="invoice", entity_id=invoice.id, entity_label=f"INV-{invoice.number}", changes={"from_sales_order": f"SO-{order.number}"})
    await db.commit()
    return await invoice_out(db, invoice, detail=True)


@router.delete("/sales-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("sales_orders"))])
async def delete_sales_order(
    order_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.orders")),
    db: AsyncSession = Depends(get_db),
):
    order = await _owned(db, SalesOrder, order_id, principal.organization_id, "Sales order")
    if order.status not in {"draft", "cancelled"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft or cancelled orders can be deleted.")
    invoiced = (
        await db.execute(select(func.count()).select_from(SalesInvoice).where(SalesInvoice.sales_order_id == order.id))
    ).scalar_one()
    if invoiced:
        raise HTTPException(status.HTTP_409_CONFLICT, "This order has an invoice. Void the invoice and keep the order for the record.")
    await db.delete(order)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Purchase orders
# ===========================================================================

PO_STATUSES = ("draft", "issued", "partially_received", "received", "cancelled")


class PurchaseOrderPayload(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    vendor_id: uuid.UUID
    sales_order_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    order_date: date | None = None
    expected_date: date | None = None
    vendor_reference: str | None = Field(default=None, max_length=80)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    shipping: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    ship_to: Address = Field(default_factory=Address)
    terms: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=5000)
    line_items: list[LinePayload] = Field(default_factory=list, max_length=500)


class PurchaseOrderUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    vendor_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    order_date: date | None = None
    expected_date: date | None = None
    vendor_reference: str | None = Field(default=None, max_length=80)
    shipping: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    ship_to: Address | None = None
    terms: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=5000)
    line_items: list[LinePayload] | None = Field(default=None, max_length=500)


class PurchaseOrderStatus(BaseModel):
    status: str = Field(pattern="^(issued|cancelled)$")


class ReceiveLine(BaseModel):
    index: int = Field(ge=0)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class ReceivePayload(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    update_cost: bool = True


async def purchase_order_out(db: AsyncSession, order: PurchaseOrder, detail: bool = False) -> dict[str, Any]:
    org = order.organization_id
    owners = await names_for(db, org, "user", [order.owner_user_id])
    vendor = await db.get(Vendor, order.vendor_id) if order.vendor_id else None
    ordered = sum(float(line.get("quantity") or 0) for line in order.line_items or [])
    received = sum(float(line.get("received_quantity") or 0) for line in order.line_items or [])
    late = order.status in {"issued", "partially_received"} and order.expected_date is not None and order.expected_date < date.today()
    data = {
        "id": str(order.id),
        "number": order.number,
        "display_number": f"PO-{order.number}",
        "subject": order.subject,
        "status": order.status,
        "is_late": late,
        "vendor_id": _id(order.vendor_id),
        "vendor_name": vendor.name if vendor else None,
        "sales_order_id": _id(order.sales_order_id),
        "owner_user_id": _id(order.owner_user_id),
        "owner_name": owners.get(str(order.owner_user_id)),
        "order_date": order.order_date,
        "expected_date": order.expected_date,
        "vendor_reference": order.vendor_reference,
        "currency": order.currency,
        "subtotal": float(order.subtotal),
        "discount_total": float(order.discount_total),
        "tax_total": float(order.tax_total),
        "shipping": float(order.shipping),
        "total": float(order.total),
        "line_count": len(order.line_items or []),
        "received_percent": round(received / ordered * 100) if ordered else 0,
        "issued_at": order.issued_at,
        "received_at": order.received_at,
        "cancelled_at": order.cancelled_at,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }
    if detail:
        so = await db.get(SalesOrder, order.sales_order_id) if order.sales_order_id else None
        organization = await db.get(Organization, org)
        data.update(
            {
                "line_items": order.line_items or [],
                "ship_to": order.ship_to or {},
                "terms": order.terms,
                "notes": order.notes,
                "vendor": await vendor_out(db, vendor) if vendor else None,
                "sales_order": {"id": str(so.id), "display_number": f"SO-{so.number}", "subject": so.subject} if so else None,
                "organization_name": organization.name if organization else "",
            }
        )
    return data


@router.get("/purchase-orders", dependencies=[Depends(require_feature("purchase_orders"))])
async def list_purchase_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    vendor_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, max_length=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(PurchaseOrder).where(PurchaseOrder.organization_id == org)
    if status_filter == "open":
        query = query.where(PurchaseOrder.status.in_(("issued", "partially_received")))
    elif status_filter in PO_STATUSES:
        query = query.where(PurchaseOrder.status == status_filter)
    if vendor_id:
        query = query.where(PurchaseOrder.vendor_id == vendor_id)
    if search:
        term = search.strip()
        conditions = [PurchaseOrder.subject.ilike(f"%{term}%"), PurchaseOrder.vendor_reference.ilike(f"%{term}%")]
        number = term.lower().removeprefix("po-")
        if number.isdigit():
            conditions.append(PurchaseOrder.number == int(number))
        query = query.where(or_(*conditions))
    orders = (await db.execute(query.order_by(PurchaseOrder.number.desc()).limit(500))).scalars().all()
    every = (
        await db.execute(
            select(PurchaseOrder.status, PurchaseOrder.total, PurchaseOrder.expected_date, PurchaseOrder.received_at).where(PurchaseOrder.organization_id == org)
        )
    ).all()
    today = date.today()
    since = utcnow() - timedelta(days=30)
    open_states = {"issued", "partially_received"}
    return {
        "items": [await purchase_order_out(db, o) for o in orders],
        "summary": {
            "open_value": float(sum(money(t) for s, t, _, _ in every if s in open_states)),
            "awaiting": len([1 for s, *_ in every if s in open_states]),
            "late": len([1 for s, _, e, _ in every if s in open_states and e and e < today]),
            "received_30d": float(sum(money(t) for s, t, _, r in every if s == "received" and r and r >= since)),
            "drafts": len([1 for s, *_ in every if s == "draft"]),
        },
        "can_manage": principal.can("sales.purchasing"),
    }


@router.post("/purchase-orders", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("purchase_orders"))])
async def create_purchase_order(
    payload: PurchaseOrderPayload,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    data = payload.model_dump(exclude={"line_items", "ship_to"})
    await _check_links(db, org, data)
    vendor = await db.get(Vendor, payload.vendor_id)
    if not vendor.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{vendor.name} is inactive.")
    order = PurchaseOrder(
        id=uuid.uuid4(),
        organization_id=org,
        number=await _next_number(db, PurchaseOrder, org),
        **{
            **data,
            "subject": data["subject"].strip(),
            "currency": data["currency"].upper(),
            "order_date": data.get("order_date") or date.today(),
            "owner_user_id": data.get("owner_user_id") or principal.user_id,
            "shipping": money(data["shipping"]),
        },
        ship_to=payload.ship_to.model_dump(),
        status="draft",
    )
    order.line_items = await build_lines(db, org, payload.line_items, keep_received=True)
    apply_totals(order)
    db.add(order)
    await db.commit()
    return await purchase_order_out(db, order, detail=True)


class ReorderPayload(BaseModel):
    vendor_id: uuid.UUID
    product_ids: list[uuid.UUID] | None = None


@router.post("/purchase-orders/reorder", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("purchase_orders", "inventory"))])
async def reorder_low_stock(
    payload: ReorderPayload,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    """Draft a purchase order for tracked products at or below their reorder level."""

    org = principal.organization_id
    vendor = await _owned(db, Vendor, payload.vendor_id, org, "Vendor")
    query = select(Product).where(Product.organization_id == org, Product.track_inventory.is_(True), Product.is_active.is_(True))
    if payload.product_ids:
        query = query.where(Product.id.in_(payload.product_ids))
    else:
        query = query.where(Product.stock_on_hand <= Product.reorder_level)
    products = (await db.execute(query.order_by(Product.name))).scalars().all()
    if not products:
        raise HTTPException(status.HTTP_409_CONFLICT, "No tracked products are at or below their reorder level.")
    lines = []
    for product in products:
        stock = Decimal(product.stock_on_hand or 0)
        reorder = Decimal(product.reorder_level or 0)
        quantity = max(reorder * 2 - stock, Decimal("1"))
        lines.append(
            {
                "product_id": str(product.id),
                "name": product.name,
                "description": None,
                "sku": product.sku,
                "quantity": float(quantity.quantize(CENT)),
                "unit_price": float(money(product.unit_cost if product.unit_cost is not None else product.unit_price)),
                "discount_percent": 0.0,
                "tax_percent": 0.0,
                "received_quantity": 0.0,
            }
        )
    order = PurchaseOrder(
        id=uuid.uuid4(),
        organization_id=org,
        number=await _next_number(db, PurchaseOrder, org),
        subject=f"Restock from {vendor.name}",
        vendor_id=vendor.id,
        owner_user_id=principal.user_id,
        order_date=date.today(),
        expected_date=date.today() + timedelta(days=7),
        currency=vendor.currency,
        shipping=Decimal("0"),
        ship_to={},
        terms=vendor.payment_terms,
        status="draft",
        line_items=lines,
    )
    apply_totals(order)
    db.add(order)
    await db.commit()
    return await purchase_order_out(db, order, detail=True)


@router.get("/purchase-orders/{order_id}", dependencies=[Depends(require_feature("purchase_orders"))])
async def get_purchase_order(
    order_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    order = await _owned(db, PurchaseOrder, order_id, principal.organization_id, "Purchase order")
    data = await purchase_order_out(db, order, detail=True)
    data["can_manage"] = principal.can("sales.purchasing")
    return data


@router.patch("/purchase-orders/{order_id}", dependencies=[Depends(require_feature("purchase_orders"))])
async def update_purchase_order(
    order_id: uuid.UUID,
    payload: PurchaseOrderUpdate,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    order = await _owned(db, PurchaseOrder, order_id, org, "Purchase order")
    changes = payload.model_dump(exclude_unset=True)
    if order.status in {"received", "cancelled"}:
        if set(changes) - {"notes", "vendor_reference"}:
            raise HTTPException(status.HTTP_409_CONFLICT, f"This purchase order is {order.status.replace('_', ' ')}. Only notes can change.")
    if order.status != "draft" and set(changes) & {"line_items", "vendor_id", "currency"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Lines and vendor can only change while the purchase order is a draft.")
    await _check_links(db, org, changes)
    if changes.get("vendor_id"):
        vendor = await db.get(Vendor, changes["vendor_id"])
        if not vendor.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{vendor.name} is inactive.")
    if "line_items" in changes:
        order.line_items = await build_lines(db, org, payload.line_items or [], previous=order.line_items, keep_received=True)
    for key, value in changes.items():
        if key == "line_items":
            continue
        if key in {"subject", "vendor_id"} and value is None:
            continue
        if key == "ship_to":
            value = value or {}
        if key == "shipping":
            value = money(value)
        setattr(order, key, value.strip() if key == "subject" else value)
    apply_totals(order)
    await db.commit()
    return await purchase_order_out(db, order, detail=True)


@router.post("/purchase-orders/{order_id}/status", dependencies=[Depends(require_feature("purchase_orders"))])
async def change_purchase_order_status(
    order_id: uuid.UUID,
    payload: PurchaseOrderStatus,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    order = await _owned(db, PurchaseOrder, order_id, principal.organization_id, "Purchase order")
    previous = order.status
    if payload.status == "issued":
        if order.status != "draft":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft purchase order can be issued.")
        if not order.line_items:
            raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one line item before issuing.")
        order.issued_at = utcnow()
    else:
        if order.status not in {"draft", "issued"}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Goods have already been received on this purchase order, so it can't be cancelled."
                if order.status in {"partially_received", "received"}
                else "This purchase order is already cancelled.",
            )
        order.cancelled_at = utcnow()
    order.status = payload.status
    record_audit(
        db,
        organization_id=order.organization_id,
        action="status",
        entity_type="purchase_order",
        entity_id=order.id,
        entity_label=f"PO-{order.number}",
        changes={"status": {"from": previous, "to": payload.status}},
    )
    await db.commit()
    return await purchase_order_out(db, order, detail=True)


@router.post("/purchase-orders/{order_id}/receive", dependencies=[Depends(require_feature("purchase_orders"))])
async def receive_purchase_order(
    order_id: uuid.UUID,
    payload: ReceivePayload,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    order = await _owned(db, PurchaseOrder, order_id, org, "Purchase order")
    if order.status not in {"issued", "partially_received"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Issue the purchase order before receiving goods." if order.status == "draft" else f"This purchase order is {order.status.replace('_', ' ')}.")
    lines = [dict(line) for line in order.line_items or []]
    totals: dict[int, Decimal] = {}
    for item in payload.lines:
        if item.index >= len(lines):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That line isn't on this purchase order.")
        totals[item.index] = totals.get(item.index, Decimal("0")) + Decimal(item.quantity)
    for index, quantity in totals.items():
        line = lines[index]
        remaining = Decimal(str(line["quantity"])) - Decimal(str(line.get("received_quantity") or 0))
        if quantity > remaining:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{line['name']}: only {float(remaining):g} still to receive."
            )
    received_items = []
    for index, quantity in totals.items():
        line = lines[index]
        line["received_quantity"] = float(Decimal(str(line.get("received_quantity") or 0)) + quantity)
        received_items.append(f"{float(quantity):g} × {line['name']}")
        if not line.get("product_id"):
            continue
        product = await db.get(Product, uuid.UUID(line["product_id"]))
        if product is None or product.organization_id != org:
            continue
        if payload.update_cost:
            net = Decimal(str(line["unit_price"])) * (1 - Decimal(str(line.get("discount_percent") or 0)) / 100)
            product.unit_cost = money(net)
        if product.track_inventory:
            product.stock_on_hand = Decimal(product.stock_on_hand or 0) + quantity
            db.add(
                StockMovement(
                    id=uuid.uuid4(),
                    organization_id=org,
                    product_id=product.id,
                    kind="receive",
                    quantity=quantity,
                    balance_after=product.stock_on_hand,
                    reference=f"PO-{order.number}",
                    note=payload.note or f"Received on purchase order “{order.subject}”",
                    created_by_user_id=principal.user_id,
                )
            )
    order.line_items = lines
    complete = all(Decimal(str(l.get("received_quantity") or 0)) >= Decimal(str(l["quantity"])) for l in lines)
    order.status = "received" if complete else "partially_received"
    if complete:
        order.received_at = utcnow()
    record_audit(
        db,
        organization_id=org,
        action="receive",
        entity_type="purchase_order",
        entity_id=order.id,
        entity_label=f"PO-{order.number}",
        changes={"received": received_items},
    )
    await db.commit()
    return await purchase_order_out(db, order, detail=True)


@router.delete("/purchase-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("purchase_orders"))])
async def delete_purchase_order(
    order_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.purchasing")),
    db: AsyncSession = Depends(get_db),
):
    order = await _owned(db, PurchaseOrder, order_id, principal.organization_id, "Purchase order")
    if order.status not in {"draft", "cancelled"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft or cancelled purchase orders can be deleted.")
    await db.delete(order)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
