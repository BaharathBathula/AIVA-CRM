"""Shared shapes for the accounting adapters (one module per provider).

An adapter turns AIVA's invoice into the provider's and reads invoices,
payments and balances back. The sync engine in ``accounting.py`` decides what
to send and when; adapters only talk to their provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.providers import _acct_http as http
from app.models.integration_hub import IntegrationConnection

CENT = Decimal("0.01")
# Currencies without minor units (ISO 4217 exponent 0) round to whole numbers.
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF", "PYG", "RWF", "KMF", "GNF", "VUV", "XPF", "BIF", "DJF"}


def money(value: Any, currency: str | None = None) -> Decimal:
    exponent = Decimal("1") if (currency or "").upper() in ZERO_DECIMAL else CENT
    try:
        return Decimal(str(value if value not in (None, "") else 0)).quantize(exponent, rounding=ROUND_HALF_UP)
    except Exception:  # noqa: BLE001 - garbage from a provider counts as zero
        return Decimal("0").quantize(exponent)


def decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0))
    except Exception:  # noqa: BLE001
        return Decimal("0")


_XERO_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")


def parse_date(value: Any) -> date | None:
    """ISO dates, ISO timestamps and Xero's ``/Date(1518685950940+0000)/``."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    found = _XERO_DATE.match(text)
    if found:
        return datetime.fromtimestamp(int(found.group(1)) / 1000, tz=timezone.utc).date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


@dataclass
class Line:
    name: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    tax_percent: Decimal
    gross: Decimal
    discount: Decimal
    net: Decimal
    tax: Decimal
    product_id: str | None = None
    sku: str | None = None
    item_ref: str | None = None  # the provider's item/service for this line, when mapped

    @property
    def taxable(self) -> bool:
        return self.tax_percent > 0


@dataclass
class InvoiceData:
    id: str
    reference: str  # "INV-1001": AIVA's number, kept on the provider's invoice
    issue_date: date
    due_date: date | None
    currency: str
    terms: str | None
    notes: str | None
    lines: list[Line]
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    email: str | None = None


@dataclass
class CustomerData:
    name: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    website: str | None = None
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None
    currency: str | None = None
    ref: str | None = None  # the AIVA account's id, for providers that keep an external ID (NetSuite)


@dataclass
class Payment:
    id: str
    amount: Decimal
    date: date | None
    reference: str | None = None
    method: str | None = None
    currency: str | None = None


@dataclass
class RemoteInvoice:
    id: str
    number: str | None
    url: str | None
    total: Decimal
    balance: Decimal
    currency: str | None
    # open | paid | void | draft | deleted
    status: str
    payments: list[Payment] = field(default_factory=list)
    due_date: date | None = None
    issue_date: date | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # kept on the link (QuickBooks SyncToken...)

    @property
    def paid(self) -> Decimal:
        return max(Decimal("0"), self.total - self.balance) if self.status != "void" else Decimal("0")


@dataclass
class RemoteCustomer:
    id: str
    name: str
    url: str | None = None
    email: str | None = None


@dataclass
class Summary:
    """What an account owes, for the account page."""

    currency: str | None
    open_balance: Decimal
    overdue_balance: Decimal
    overdue_count: int
    invoices: list[dict[str, Any]]


@dataclass
class Ctx:
    """One connection's working context: database, connection, settings and call budget."""

    db: AsyncSession
    connection: IntegrationConnection
    settings: dict[str, Any]
    budget: http.Budget

    @property
    def config(self) -> dict[str, Any]:
        return dict(self.connection.config or {})

    async def call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return await http.call(self.db, self.connection, method, url, budget=self.budget, **kwargs)


Fetch = Callable[..., Awaitable[httpx.Response]]
