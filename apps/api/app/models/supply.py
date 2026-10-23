"""Vendors, price books, sales orders and purchase orders.

Line items on orders are stored as JSON snapshots (like invoices), so an
order keeps the prices it was agreed at even if the catalog changes later.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _json_dict, _json_list, _money, _user_fk, _when


def _fk(table: str, ondelete: str = "SET NULL", nullable: bool = True):
    return mapped_column(ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable, index=True)


class Vendor(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "vendors"
    __table_args__ = (Index("ix_vendors_org_name", "organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    gl_account: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(60), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    street: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = _bool(True)
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()


class PriceBook(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "price_books"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_price_books_org_name"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    # flat: one list price per product. differential: list price plus quantity tiers.
    pricing_model: Mapped[str] = mapped_column(String(20), nullable=False, default="flat", server_default="flat")
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = _bool(True)
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()


class PriceBookEntry(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "price_book_entries"
    __table_args__ = (UniqueConstraint("price_book_id", "product_id", name="uq_price_book_entries_product"),)

    price_book_id: Mapped[uuid.UUID] = _fk("price_books", "CASCADE", nullable=False)
    product_id: Mapped[uuid.UUID] = _fk("products", "CASCADE", nullable=False)
    list_price: Mapped[Decimal] = _money()
    # [{"from_qty": 10, "to_qty": 49, "discount_percent": 5}, ...]
    tiers: Mapped[list[dict]] = _json_list()


class SalesOrder(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "sales_orders"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_sales_orders_org_number"),)

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    # draft | confirmed | fulfilled | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft", index=True)
    quote_id: Mapped[uuid.UUID | None] = _fk("quotes")
    opportunity_id: Mapped[uuid.UUID | None] = _fk("opportunities")
    account_id: Mapped[uuid.UUID | None] = _fk("accounts")
    contact_id: Mapped[uuid.UUID | None] = _fk("contacts")
    price_book_id: Mapped[uuid.UUID | None] = _fk("price_books")
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    customer_po: Mapped[str | None] = mapped_column(String(80), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    subtotal: Mapped[Decimal] = _money()
    discount_total: Mapped[Decimal] = _money()
    tax_total: Mapped[Decimal] = _money()
    shipping: Mapped[Decimal] = _money()
    total: Mapped[Decimal] = _money()
    billing_address: Mapped[dict] = _json_dict()
    shipping_address: Mapped[dict] = _json_dict()
    line_items: Mapped[list[dict]] = _json_list()
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True once stock has left the shelf for this order (here or when its quote was accepted).
    stock_applied: Mapped[bool] = _bool(False)
    confirmed_at: Mapped[datetime | None] = _when()
    fulfilled_at: Mapped[datetime | None] = _when()
    cancelled_at: Mapped[datetime | None] = _when()


class PurchaseOrder(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_purchase_orders_org_number"),)

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    # draft | issued | partially_received | received | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft", index=True)
    vendor_id: Mapped[uuid.UUID | None] = _fk("vendors")
    sales_order_id: Mapped[uuid.UUID | None] = _fk("sales_orders")
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    vendor_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    subtotal: Mapped[Decimal] = _money()
    discount_total: Mapped[Decimal] = _money()
    tax_total: Mapped[Decimal] = _money()
    shipping: Mapped[Decimal] = _money()
    total: Mapped[Decimal] = _money()
    ship_to: Mapped[dict] = _json_dict()
    line_items: Mapped[list[dict]] = _json_list()
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime | None] = _when()
    received_at: Mapped[datetime | None] = _when()
    cancelled_at: Mapped[datetime | None] = _when()


__all__ = ["Vendor", "PriceBook", "PriceBookEntry", "SalesOrder", "PurchaseOrder"]
