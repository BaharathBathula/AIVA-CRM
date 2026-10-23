"""Look up any CRM record by type and id, for links from documents, chat and pins."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.collaboration import Document
from app.models.contact import Contact
from app.models.editions import SalesInvoice
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.opportunity import Opportunity
from app.models.platform import Case, Product, Quote
from app.models.supply import PriceBook, PurchaseOrder, SalesOrder, Vendor
from app.models.task import Task
from app.models.work import Appointment, Project


def _person(record: Any) -> str:
    return f"{record.first_name or ''} {record.last_name or ''}".strip() or getattr(record, "email", None) or "Unnamed"


# type: (model, singular label, label function, link function)
RECORD_TYPES: dict[str, tuple[type, str, Any, Any]] = {
    "lead": (Lead, "Lead", lambda r: _person(r), lambda r: f"/leads/{r.id}"),
    "contact": (Contact, "Contact", lambda r: _person(r), lambda r: f"/contacts/{r.id}"),
    "account": (Account, "Account", lambda r: r.name, lambda r: f"/accounts/{r.id}"),
    "opportunity": (Opportunity, "Deal", lambda r: r.name, lambda r: f"/opportunities/{r.id}"),
    "quote": (Quote, "Quote", lambda r: f"Q-{r.number} · {r.name}", lambda r: f"/quotes/{r.id}"),
    "case": (Case, "Case", lambda r: f"CS-{r.number} · {r.subject}", lambda r: f"/cases/{r.id}"),
    "invoice": (SalesInvoice, "Invoice", lambda r: f"INV-{r.number}", lambda r: f"/invoices/{r.id}"),
    "sales_order": (SalesOrder, "Sales order", lambda r: f"SO-{r.number} · {r.subject}", lambda r: f"/sales-orders/{r.id}"),
    "purchase_order": (PurchaseOrder, "Purchase order", lambda r: f"PO-{r.number} · {r.subject}", lambda r: f"/purchase-orders/{r.id}"),
    "vendor": (Vendor, "Vendor", lambda r: r.name, lambda r: f"/vendors/{r.id}"),
    "price_book": (PriceBook, "Price book", lambda r: r.name, lambda r: f"/price-books/{r.id}"),
    "product": (Product, "Product", lambda r: r.name, lambda r: "/products"),
    "project": (Project, "Project", lambda r: f"PRJ-{r.number} · {r.name}", lambda r: f"/projects/{r.id}"),
    "appointment": (Appointment, "Appointment", lambda r: f"APT-{r.number} · {r.title}", lambda r: f"/appointments?open={r.id}"),
    "meeting": (Meeting, "Meeting", lambda r: r.title, lambda r: f"/meetings?open={r.id}"),
    "task": (Task, "Task", lambda r: r.title, lambda r: "/tasks"),
    "document": (Document, "Document", lambda r: r.name, lambda r: f"/documents/{r.id}"),
}


async def resolve(db: AsyncSession, organization_id: uuid.UUID, record_type: str, record_id: Any) -> dict[str, str]:
    """Returns {"type", "id", "label", "href", "kind"} or raises 404."""

    entry = RECORD_TYPES.get(record_type)
    if entry is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Records of type “{record_type}” can't be linked.")
    model, kind, label, link = entry
    try:
        key = record_id if isinstance(record_id, uuid.UUID) else uuid.UUID(str(record_id))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} not found.") from exc
    record = await db.get(model, key)
    if record is None or record.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} not found.")
    return {"type": record_type, "id": str(record.id), "label": str(label(record))[:200], "href": link(record), "kind": kind}


async def resolve_many(db: AsyncSession, organization_id: uuid.UUID, pairs: list[tuple[str, Any]]) -> dict[tuple[str, str], dict[str, str]]:
    """Best-effort lookup of many records; missing ones are left out."""

    found: dict[tuple[str, str], dict[str, str]] = {}
    for record_type, record_id in pairs:
        key = (record_type, str(record_id))
        if key in found:
            continue
        try:
            found[key] = await resolve(db, organization_id, record_type, record_id)
        except HTTPException:
            continue
    return found
