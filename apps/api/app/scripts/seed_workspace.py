"""Demo content for vendors, price books, sales and purchase orders, meetings,
documents, services and appointments, projects, website visits, SalesInbox,
team chat and pins.

Most records are created through the REST API in-process (no network), so
they get the same numbering, totals, stock movements, audit entries and
notifications as records people create by hand. Website visit history and
chat timestamps are written directly so they can be spread over past days.

Runs once per workspace: it records ``demo_modules_seeded`` in the
organization's settings before it starts, so a restart never duplicates
anything. Every section is independent; one that fails is reported and the
rest still run. No email is sent: invites and customer notices are off.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Product, Quote

API = "/api/v1"
MARKER = "demo_modules_seeded"


def now() -> datetime:
    return datetime.now(timezone.utc)


class SeedError(RuntimeError):
    pass


class Client:
    """Calls the API in-process as one of the demo users."""

    def __init__(self, http: httpx.AsyncClient, organization_id: uuid.UUID, users: dict[str, uuid.UUID]):
        self.http = http
        secret = get_settings().jwt_secret_key
        self.headers = {
            name: {"Authorization": f"Bearer {create_access_token(user_id, secret, expires_minutes=30)}", "X-Organization-ID": str(organization_id)}
            for name, user_id in users.items()
        }

    async def call(self, method: str, path: str, as_user: str = "AIVA Administrator", **kwargs: Any) -> Any:
        response = await self.http.request(method, f"{API}{path}", headers=self.headers[as_user], **kwargs)
        if response.status_code >= 400:
            raise SeedError(f"{method} {path} → {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else None

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.call("GET", path, **kwargs)

    async def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return await self.call("POST", path, json=json, **kwargs)

    async def put(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return await self.call("PUT", path, json=json, **kwargs)

    async def patch(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return await self.call("PATCH", path, json=json, **kwargs)


# ---------------------------------------------------------------------------
# Small generated files for the document library
# ---------------------------------------------------------------------------


def _pdf_text(value: str) -> str:
    return value.encode("ascii", "replace").decode("ascii").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def simple_pdf(title: str, lines: list[str]) -> bytes:
    """A one-page PDF with a heading and body text (no external libraries)."""

    commands = ["BT", "/F2 18 Tf", "72 720 Td", f"({_pdf_text(title)}) Tj", "/F1 11 Tf", "0 -30 Td", "15 TL"]
    for line in lines:
        commands.append(f"({_pdf_text(line)}) Tj T*" if line else "T*")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def seed_workspace(users: dict[str, uuid.UUID], organization_id: uuid.UUID) -> bool:
    """Adds demo content for the workspace modules once. Returns True if it ran."""

    org = organization_id
    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, org)
        if organization is None or (organization.settings or {}).get(MARKER):
            return False
        organization.settings = {**(organization.settings or {}), MARKER: now().isoformat()}
        await db.commit()

        accounts = (await db.execute(select(Account).where(Account.organization_id == org).order_by(Account.name))).scalars().all()
        contacts = (
            await db.execute(select(Contact).where(Contact.organization_id == org, Contact.email.is_not(None)).order_by(Contact.created_at, Contact.last_name))
        ).scalars().all()
        stages = {s.id: s.category for s in (await db.execute(select(PipelineStage))).scalars()}
        deals = (await db.execute(select(Opportunity).where(Opportunity.organization_id == org).order_by(Opportunity.amount.desc().nulls_last()))).scalars().all()
        quotes = (
            await db.execute(select(Quote).where(Quote.organization_id == org, Quote.status == "accepted").order_by(Quote.number))
        ).scalars().all()
        products = {p.sku: p for p in (await db.execute(select(Product).where(Product.organization_id == org))).scalars() if p.sku}
        leads = (
            await db.execute(select(Lead).where(Lead.organization_id == org, Lead.email.is_not(None), Lead.status != "converted").order_by(Lead.created_at))
        ).scalars().all()
        snapshot = {
            "accounts": [(a.id, a.name, a.domain) for a in accounts],
            "contacts": [(c.id, c.first_name, c.last_name, c.email, c.account_id) for c in contacts],
            "won": [(d.id, d.name, d.account_id, d.primary_contact_id, d.owner_user_id) for d in deals if stages.get(d.stage_id) == "won"],
            "open": [(d.id, d.name, d.account_id, d.primary_contact_id, d.owner_user_id) for d in deals if stages.get(d.stage_id) == "open"],
            "quotes": [q.id for q in quotes],
            "products": {sku: (p.id, p.name, Decimal(p.unit_price), Decimal(p.unit_cost or 0), p.track_inventory) for sku, p in products.items()},
            "leads": [(l.id, l.first_name, l.last_name, l.email, l.company_name) for l in leads],
        }

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://seed.internal", timeout=60) as http:
        api = Client(http, org, users)
        context: dict[str, Any] = {"org": org, "users": users, **snapshot}
        for name, section in (
            ("vendors and purchase orders", _vendors_and_purchasing),
            ("price books", _price_books),
            ("sales orders", _sales_orders),
            ("meetings", _meetings),
            ("documents", _documents),
            ("services and appointments", _services),
            ("projects", _projects),
            ("website visits", _visits),
            ("SalesInbox email", _email),
            ("team chat", _chat),
            ("pins", _pins),
        ):
            try:
                await section(api, context)
            except Exception as exc:  # noqa: BLE001 - demo content must never stop startup
                print(f"Demo content for {name} skipped: {exc}")
    return True


def _contacts_for(context: dict, account_id: uuid.UUID | None) -> list[tuple]:
    return [c for c in context["contacts"] if c[4] == account_id]


def _person(context: dict, deal: tuple) -> tuple | None:
    contact_id = deal[3]
    found = next((c for c in context["contacts"] if c[0] == contact_id), None)
    return found or next(iter(_contacts_for(context, deal[2])), None)


def _account_name(context: dict, account_id: uuid.UUID | None) -> str:
    return next((a[1] for a in context["accounts"] if a[0] == account_id), "the customer")


def _at(day_offset: int, hour_utc: int, minute: int = 0) -> datetime:
    day = date.today() + timedelta(days=day_offset)
    return datetime.combine(day, time(hour_utc, minute), tzinfo=timezone.utc)


def _weekday(day_offset: int) -> int:
    day = date.today() + timedelta(days=day_offset)
    while day.weekday() >= 5:
        day += timedelta(days=1)
        day_offset += 1
    return day_offset


def _past_weekday(days_back: int) -> int:
    offset = -days_back
    while (date.today() + timedelta(days=offset)).weekday() >= 5:
        offset -= 1
    return offset


# ---------------------------------------------------------------------------
# Vendors and purchase orders
# ---------------------------------------------------------------------------


async def _vendors_and_purchasing(api: Client, context: dict) -> None:
    users = context["users"]
    vendors = {}
    for payload in (
        {"name": "Summit Hardware Supply", "category": "Hardware", "contact_name": "Rachel Ortiz", "email": "orders@summithardware.example",
         "phone": "+1 214 555 0142", "website": "https://summithardware.example", "payment_terms": "Net 30", "gl_account": "5000 · Cost of goods",
         "street": "1800 Commerce St", "city": "Dallas", "state": "TX", "postal_code": "75201", "country": "USA", "owner_user_id": str(users["Maya Chen"]),
         "notes": "Preferred supplier for license packs and appliances. Ships within 3 business days."},
        {"name": "Atlas Cloud Hosting", "category": "Infrastructure", "contact_name": "Ben Okafor", "email": "billing@atlascloud.example",
         "phone": "+1 512 555 0177", "website": "https://atlascloud.example", "payment_terms": "Net 15", "gl_account": "6100 · Hosting",
         "city": "Austin", "state": "TX", "country": "USA", "owner_user_id": str(users["AIVA Administrator"])},
        {"name": "BrightPath Training Partners", "category": "Professional services", "contact_name": "Hannah Lee", "email": "hello@brightpath.example",
         "phone": "+1 312 555 0110", "payment_terms": "Net 45", "gl_account": "6400 · Contractors", "city": "Chicago", "state": "IL", "country": "USA",
         "owner_user_id": str(users["Tom Becker"]), "notes": "Certified trainers for admin workshops."},
    ):
        vendors[payload["name"]] = await api.post("/vendors", payload)

    products = context["products"]

    def line(sku: str, quantity: int, fallback_name: str, fallback_price: str) -> dict:
        found = products.get(sku)
        if found:
            product_id, name, price, cost, _ = found
            return {"product_id": str(product_id), "name": name, "quantity": str(quantity), "unit_price": str(cost or (price * Decimal("0.55")).quantize(Decimal("0.01")))}
        return {"name": fallback_name, "quantity": str(quantity), "unit_price": fallback_price}

    summit = vendors["Summit Hardware Supply"]["id"]
    received = await api.post(
        "/purchase-orders",
        {"subject": "Service Desk seat packs", "vendor_id": summit, "order_date": (date.today() - timedelta(days=12)).isoformat(),
         "expected_date": (date.today() - timedelta(days=5)).isoformat(), "vendor_reference": "SHS-44817", "shipping": "0",
         "line_items": [line("AIVA-SERVICE", 20, "Service Desk seat pack", "52.25")]},
    )
    await api.post(f"/purchase-orders/{received['id']}/status", {"status": "issued"})
    await api.post(f"/purchase-orders/{received['id']}/receive", {"lines": [{"index": 0, "quantity": "20"}], "note": "Delivered complete"})

    restock = await api.post(
        "/purchase-orders",
        {"subject": "Sales Cloud license restock", "vendor_id": summit, "expected_date": (date.today() + timedelta(days=4)).isoformat(),
         "vendor_reference": "SHS-45102", "shipping": "35", "terms": "Net 30. Partial shipments accepted.",
         "line_items": [line("AIVA-SALES", 40, "Sales Cloud license pack", "66"), line("AIVA-ANALYTICS", 10, "Analytics Pro add-on", "24.75")]},
    )
    await api.post(f"/purchase-orders/{restock['id']}/status", {"status": "issued"})
    await api.post(f"/purchase-orders/{restock['id']}/receive", {"lines": [{"index": 0, "quantity": "25"}], "note": "First shipment, rest on backorder"})

    hosting = await api.post(
        "/purchase-orders",
        {"subject": "Sandbox hosting capacity, next quarter", "vendor_id": vendors["Atlas Cloud Hosting"]["id"],
         "expected_date": (date.today() + timedelta(days=10)).isoformat(),
         "line_items": [{"name": "Dedicated sandbox hosting (quarter)", "quantity": "1", "unit_price": "4200"},
                        {"name": "Backup storage, 2 TB (quarter)", "quantity": "1", "unit_price": "380"}]},
    )
    await api.post(f"/purchase-orders/{hosting['id']}/status", {"status": "issued"})

    await api.post(
        "/purchase-orders",
        {"subject": "Admin training workshop delivery", "vendor_id": vendors["BrightPath Training Partners"]["id"],
         "notes": "Two workshops for new enterprise customers.",
         "line_items": [{"name": "Certified trainer day", "quantity": "4", "unit_price": "1200"},
                        {"name": "Course materials", "quantity": "24", "unit_price": "35"}]},
    )


# ---------------------------------------------------------------------------
# Price books
# ---------------------------------------------------------------------------


async def _price_books(api: Client, context: dict) -> None:
    products = context["products"]
    volume = await api.post(
        "/price-books",
        {"name": "Enterprise volume pricing", "pricing_model": "differential",
         "description": "Per-seat subscriptions get cheaper as seat counts grow. Use for deals over 25 seats.",
         "valid_from": date(date.today().year, 1, 1).isoformat()},
    )
    entries = []
    for sku in ("AIVA-SALES", "AIVA-SERVICE", "AIVA-ANALYTICS"):
        if sku in products:
            product_id, _, price, _, _ = products[sku]
            entries.append(
                {"product_id": str(product_id), "list_price": str(price),
                 "tiers": [{"from_qty": "1", "to_qty": "24", "discount_percent": "0"},
                           {"from_qty": "25", "to_qty": "99", "discount_percent": "8"},
                           {"from_qty": "100", "discount_percent": "15"}]}
            )
    if entries:
        await api.put(f"/price-books/{volume['id']}/entries", entries)

    partner = await api.post(
        "/price-books",
        {"name": "Partner and reseller", "pricing_model": "flat", "description": "Net prices for registered resellers: 20% below list."},
    )
    partner_entries = [
        {"product_id": str(product_id), "list_price": str((price * Decimal("0.8")).quantize(Decimal("0.01"))), "tiers": []}
        for product_id, _, price, _, _ in products.values()
    ]
    if partner_entries:
        await api.put(f"/price-books/{partner['id']}/entries", partner_entries)
    context["price_book_id"] = volume["id"]


# ---------------------------------------------------------------------------
# Sales orders
# ---------------------------------------------------------------------------


async def _sales_orders(api: Client, context: dict) -> None:
    # Orders from accepted quotes: one confirmed and due soon, one waiting on the customer.
    for index, quote_id in enumerate(context["quotes"][:2]):
        order = await api.post(f"/sales-orders/from-quote/{quote_id}")
        if index == 0:
            await api.patch(f"/sales-orders/{order['id']}", {"customer_po": "PO-77812", "due_date": (date.today() + timedelta(days=6)).isoformat()})
            await api.post(f"/sales-orders/{order['id']}/status", {"status": "confirmed"})
        else:
            await api.patch(f"/sales-orders/{order['id']}", {"notes": "Waiting for the customer's purchase order number before confirming."})

    # Service orders (no stock involved): delivered, past its due date, and a draft.
    products = context["products"]
    customers = [a for a in context["accounts"] if _contacts_for(context, a[0])]
    for sku, quantity, (customer_index, ordered, due, step, po, subject) in (
        ("SVC-TRAIN", 2, (0, 24, -10, "fulfilled", "4500127788", "Admin training for {name}")),
        ("SUP-PREMIER", 1, (1, 18, -3, "confirmed", "PO-2291", "Premier Support renewal for {name}")),
        ("SVC-DATA", 1, (2, 1, 21, "draft", None, "Data migration for {name}")),
    ):
        item = products.get(sku)
        if not item or len(customers) <= customer_index:
            continue
        customer = customers[customer_index]
        contact = _contacts_for(context, customer[0])[0]
        order = await api.post(
            "/sales-orders",
            {"subject": subject.format(name=customer[1]), "account_id": str(customer[0]), "contact_id": str(contact[0]),
             "order_date": (date.today() - timedelta(days=ordered)).isoformat(), "due_date": (date.today() + timedelta(days=due)).isoformat(),
             "customer_po": po, "terms": "Net 30",
             "line_items": [{"product_id": str(item[0]), "name": item[1], "quantity": str(quantity), "unit_price": str(item[2])}]},
        )
        if step in {"confirmed", "fulfilled"}:
            await api.post(f"/sales-orders/{order['id']}/status", {"status": "confirmed"})
        if step == "fulfilled":
            await api.post(f"/sales-orders/{order['id']}/status", {"status": "fulfilled", "carrier": "Delivered on site"})


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------


async def _meetings(api: Client, context: dict) -> None:
    users = context["users"]
    maya, daniel, priya, tom, aisha = (str(users[n]) for n in ("Maya Chen", "Daniel Ruiz", "Priya Raman", "Tom Becker", "Aisha Okafor"))
    admin = str(users["AIVA Administrator"])
    open_deals, won = context["open"], context["won"]

    def contact_attendee(deal: tuple | None) -> list[dict]:
        person = _person(context, deal) if deal else None
        return [{"type": "contact", "id": str(person[0])}] if person else []

    plans: list[dict] = []
    if open_deals:
        deal = open_deals[0]
        plans.append({"title": f"Pricing walkthrough · {deal[1]}", "start": _at(_weekday(1), 15), "minutes": 45, "deal": deal, "organizer": daniel,
                      "users": [maya], "online": True, "url": "https://meet.aiva.example/pricing-walkthrough",
                      "description": "Walk through the volume price book and answer questions on the order form."})
    plans.append({"title": "Weekly pipeline review", "start": _at(_weekday(2), 14), "minutes": 60, "deal": None, "organizer": maya,
                  "users": [daniel, priya, admin], "online": True, "url": "https://meet.aiva.example/pipeline-review",
                  "description": "Commit deals, slipped close dates and next steps."})
    if won:
        deal = won[0]
        plans.append({"title": f"Implementation kickoff · {_account_name(context, deal[2])}", "start": _at(_weekday(3), 16), "minutes": 60, "deal": deal,
                      "organizer": tom, "users": [aisha, daniel], "online": False, "location": "Customer office",
                      "description": "Agree on scope, milestones and owners for the rollout."})
    if len(open_deals) > 1:
        deal = open_deals[1]
        plans.append({"title": f"Security review · {deal[1]}", "start": _at(_weekday(4), 18), "minutes": 45, "deal": deal, "organizer": priya,
                      "users": [admin], "online": True, "url": "https://meet.aiva.example/security-review"})
    plans.append({"title": "Support escalations sync", "start": _at(_weekday(1), 20), "minutes": 30, "deal": None, "organizer": tom,
                  "users": [aisha], "online": True, "url": "https://meet.aiva.example/escalations"})

    for plan in plans:
        deal = plan["deal"]
        payload = {
            "title": plan["title"], "description": plan.get("description"), "start_at": plan["start"].isoformat(),
            "end_at": (plan["start"] + timedelta(minutes=plan["minutes"])).isoformat(), "is_online": plan["online"],
            "meeting_url": plan.get("url"), "location": plan.get("location"), "organizer_user_id": plan["organizer"],
            "account_id": str(deal[2]) if deal else None, "opportunity_id": str(deal[0]) if deal else None,
            "attendees": [{"type": "user", "id": u} for u in plan["users"]] + contact_attendee(deal),
            "reminder_minutes": 15, "send_invites": False,
        }
        meeting = await api.post("/meetings", payload)
        if plan["users"] and plan["users"][0] in (maya, admin):
            name = "Maya Chen" if plan["users"][0] == maya else "AIVA Administrator"
            await api.post(f"/meetings/{meeting['id']}/respond", {"response": "accepted"}, as_user=name)

    # A meeting that already happened, with its notes and follow-ups.
    if won:
        deal = won[-1]
        start = _at(_past_weekday(3), 15)
        held = await api.post(
            "/meetings",
            {"title": f"Quarterly business review · {_account_name(context, deal[2])}", "start_at": start.isoformat(),
             "end_at": (start + timedelta(hours=1)).isoformat(), "location": "Video call", "organizer_user_id": maya,
             "account_id": str(deal[2]), "opportunity_id": str(deal[0]),
             "attendees": [{"type": "user", "id": daniel}] + contact_attendee(deal), "send_invites": False},
        )
        await api.post(
            f"/meetings/{held['id']}/complete",
            {"summary": "Adoption is strong in sales; service team wants SLA reporting. Interest in adding 40 seats next quarter.",
             "notes": "Usage up 18% quarter over quarter. Champion is moving to a new role in January; introduce the new admin.",
             "action_items": ["Send SLA reporting overview", "Prepare a 40-seat expansion quote"]},
        )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def _documents(api: Client, context: dict) -> None:
    folders = {}
    for name in ("Contracts", "Proposals", "Product sheets", "Implementation"):
        folders[name] = (await api.post("/documents/folders", {"name": name}))["id"]

    async def upload(name: str, content: bytes, content_type: str, folder: str, description: str, tags: str,
                     record: tuple[str, uuid.UUID] | None = None, as_user: str = "AIVA Administrator") -> dict:
        data = {"folder_id": folders[folder], "description": description, "tags": tags}
        if record:
            data.update({"record_type": record[0], "record_id": str(record[1])})
        return await api.call("POST", "/documents", as_user=as_user, files={"file": (name, content, content_type)}, data=data)

    year = date.today().year
    won = context["won"]
    if won:
        deal = won[0]
        customer = _account_name(context, deal[2])
        msa = simple_pdf(
            f"Master Services Agreement - {customer}",
            [f"Between AIVA and {customer}.", f"Effective {date.today() - timedelta(days=40):%B %d, %Y} for an initial term of 36 months.", "",
             "1. Services. AIVA provides the subscription services listed in each order form.",
             "2. Fees. Fees are invoiced annually in advance and due within 30 days.",
             "3. Service levels. 99.9% monthly availability with service credits as described in Exhibit B.",
             "4. Data protection. Customer data is processed under the Data Processing Addendum.",
             "5. Term and renewal. Renews for 12-month periods unless either party gives 60 days' notice.", "",
             "Signed for AIVA: Maya Chen, VP of Sales", f"Signed for {customer}: authorized signatory"],
        )
        await upload(f"MSA - {customer}.pdf", msa, "application/pdf", "Contracts", "Signed master services agreement.", "legal, signed",
                     ("account", deal[2]), as_user="Maya Chen")
        plan = simple_pdf(
            f"Implementation plan - {customer}",
            ["Phase 1 (weeks 1-2): kickoff, admin setup, sandbox, data migration scope.",
             "Phase 2 (weeks 3-4): data migration, integrations, pipeline and case queues.",
             "Phase 3 (weeks 5-6): user acceptance testing, admin training, go-live.", "",
             "Owners: AIVA implementation lead (Tom Becker), customer admin, sales sponsor."],
        )
        await upload("Implementation plan.pdf", plan, "application/pdf", "Implementation", "Six-week rollout plan agreed at kickoff.",
                     "implementation", ("opportunity", deal[0]), as_user="Tom Becker")
    if context["open"]:
        deal = context["open"][0]
        proposal = simple_pdf(
            f"Proposal - {deal[1]}",
            [f"Prepared for {_account_name(context, deal[2])}.", "",
             "Recommended: AIVA Sales Cloud with Analytics Pro and Premier Support.",
             "Pricing follows the Enterprise volume price book (8% off from 25 seats).",
             "Implementation: standard six-week rollout with admin training.", "", "Valid for 30 days."],
        )
        await upload(f"Proposal - {deal[1]}.pdf", proposal, "application/pdf", "Proposals", "Proposal sent with the quote.", "proposal",
                     ("opportunity", deal[0]), as_user="Daniel Ruiz")

    price_rows = ["Product,SKU,List price,Billing"] + [
        f"{name},{sku},{price:.2f},{'per user / month' if sku in {'AIVA-SALES', 'AIVA-SERVICE', 'AIVA-ANALYTICS'} else 'see order form'}"
        for sku, (_, name, price, _, _) in sorted(context["products"].items())
    ]
    await upload(f"AIVA price list {year}.csv", ("\n".join(price_rows) + "\n").encode(), "text/csv", "Product sheets",
                 "Current list prices for every product.", "pricing")
    checklist = "\n".join([
        "Customer onboarding checklist", "",
        "[ ] Kickoff call held and owners agreed", "[ ] Admin accounts created and MFA turned on", "[ ] Users invited with roles and teams",
        "[ ] Data imported (accounts, contacts, open deals)", "[ ] Email and calendar connected", "[ ] Pipelines and stages reviewed",
        "[ ] Admin training completed", "[ ] Go-live sign-off from the sponsor",
    ])
    await upload("Onboarding checklist.txt", checklist.encode(), "text/plain", "Implementation", "Checklist used on every rollout.", "onboarding, template",
                 as_user="Tom Becker")


# ---------------------------------------------------------------------------
# Services and appointments
# ---------------------------------------------------------------------------


async def _services(api: Client, context: dict) -> None:
    users = context["users"]
    tom, aisha, daniel, priya = (str(users[n]) for n in ("Tom Becker", "Aisha Okafor", "Daniel Ruiz", "Priya Raman"))
    services = [
        await api.post("/services", {"name": "Product demo", "category": "Sales", "duration_minutes": 45, "buffer_minutes": 15, "price": "0",
                                     "location_type": "online", "staff_user_ids": [daniel, priya],
                                     "description": "A tailored walkthrough of AIVA for prospects."}),
        await api.post("/services", {"name": "Admin training session", "category": "Training", "duration_minutes": 120, "buffer_minutes": 15,
                                     "price": "750", "location_type": "online", "staff_user_ids": [tom],
                                     "description": "Hands-on training for workspace administrators."}),
        await api.post("/services", {"name": "On-site implementation workshop", "category": "Professional services", "duration_minutes": 240,
                                     "buffer_minutes": 30, "price": "2500", "location_type": "customer_site", "staff_user_ids": [tom, aisha],
                                     "description": "Half-day workshop at the customer's office to configure pipelines, queues and integrations."}),
        await api.post("/services", {"name": "Health check call", "category": "Customer success", "duration_minutes": 30, "price": "0",
                                     "location_type": "phone", "staff_user_ids": [aisha],
                                     "description": "Quarterly review of adoption, open cases and upcoming needs."}),
    ]
    people = [c for c in context["contacts"]][:6]
    if not people:
        return
    bookings = [(0, 1, 0), (1, 2, 1), (2, 4, 2), (3, 1, 3), (0, 3, 4), (3, 5, 5)]
    for service_index, day_offset, person_index in bookings:
        service = services[service_index]
        person = people[person_index % len(people)]
        day = date.today() + timedelta(days=_weekday(day_offset))
        slots = (await api.get(f"/services/{service['id']}/availability?date={day.isoformat()}")).get("slots", [])
        if not slots:
            continue
        slot = slots[min(len(slots) - 1, 2 + person_index)]
        await api.post("/appointments", {"service_id": service["id"], "contact_id": str(person[0]), "start_at": slot["start_at"],
                                         "staff_user_id": slot.get("staff_user_id"), "notify_customer": False,
                                         "notes": f"Booked with {person[1]} {person[2]}."})


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


async def _projects(api: Client, context: dict) -> None:
    users = context["users"]
    tom, aisha, daniel = (str(users[n]) for n in ("Tom Becker", "Aisha Okafor", "Daniel Ruiz"))
    won = context["won"]
    if not won:
        return
    first = await api.post(f"/projects/from-opportunity/{won[0][0]}")
    await api.patch(f"/projects/{first['id']}", {"status": "active", "priority": "high", "owner_user_id": tom, "member_user_ids": [aisha, daniel],
                                                   "start_date": (date.today() - timedelta(days=12)).isoformat(),
                                                   "due_date": (date.today() + timedelta(days=30)).isoformat()})
    detail = await api.get(f"/projects/{first['id']}")
    kickoff, golive = (m["id"] for m in detail["milestones"][:2])
    await api.patch(f"/projects/{first['id']}/milestones/{kickoff}", {"due_date": (date.today() - timedelta(days=5)).isoformat()})
    await api.patch(f"/projects/{first['id']}/milestones/{golive}", {"due_date": (date.today() + timedelta(days=30)).isoformat()})
    migration = await api.post(f"/projects/{first['id']}/milestones", {"name": "Data migration", "due_date": (date.today() + timedelta(days=12)).isoformat()})
    tasks = [
        ("Kickoff call with stakeholders", kickoff, tom, -9, True),
        ("Confirm migration scope and field mapping", kickoff, aisha, -6, True),
        ("Create admin accounts and turn on MFA", kickoff, tom, -4, True),
        ("Import accounts, contacts and open deals", migration["id"], aisha, 3, False),
        ("Connect email and calendar for all users", migration["id"], tom, 6, False),
        ("Configure case queues and SLA policies", migration["id"], aisha, -1, False),
        ("User acceptance testing", golive, daniel, 20, False),
        ("Admin training session", golive, tom, 24, False),
        ("Go-live sign-off with sponsor", golive, tom, 30, False),
    ]
    for title, milestone, owner, due_in, done in tasks:
        task = await api.post(f"/projects/{first['id']}/tasks", {"title": title, "milestone_id": milestone, "assigned_to_user_id": owner,
                                                                  "due_at": _at(due_in, 22).isoformat()})
        if done:
            await api.patch(f"/projects/{first['id']}/tasks/{task['id']}", {"status": "completed"})
    await api.patch(f"/projects/{first['id']}/milestones/{kickoff}", {"status": "completed"})

    if len(won) > 1:
        second = await api.post(f"/projects/from-opportunity/{won[1][0]}")
        await api.patch(f"/projects/{second['id']}", {"owner_user_id": aisha, "member_user_ids": [tom],
                                                        "start_date": (date.today() + timedelta(days=7)).isoformat(),
                                                        "due_date": (date.today() + timedelta(days=60)).isoformat()})
        for title, owner, due_in in (("Schedule kickoff with the customer", aisha, 5), ("Send the implementation questionnaire", aisha, 6),
                                     ("Book the on-site workshop", tom, 14)):
            await api.post(f"/projects/{second['id']}/tasks", {"title": title, "assigned_to_user_id": owner, "due_at": _at(due_in, 22).isoformat()})


# ---------------------------------------------------------------------------
# Website visits
# ---------------------------------------------------------------------------


PAGES = [
    ("/", "AIVA CRM - The CRM your team will actually use"), ("/pricing", "Pricing"), ("/product/sales", "Sales Cloud"),
    ("/product/service", "Service Desk"), ("/customers", "Customer stories"), ("/demo", "Book a demo"),
    ("/blog/forecasting-guide", "A practical guide to sales forecasting"), ("/security", "Security and compliance"),
    ("/integrations", "Integrations"), ("/blog/sla-playbook", "The support SLA playbook"),
]
SOURCES = [
    ("https://www.google.com/", None), ("https://www.google.com/", None), ("https://www.linkedin.com/", None), (None, None),
    (None, ("newsletter", "email", "october-product-update")), ("https://www.bing.com/", None), (None, ("linkedin", "paid-social", "q4-demand")),
    ("https://www.g2.com/", None),
]


async def _visits(api: Client, context: dict) -> None:
    from app.models.engagement import TrackingSite, WebPageView, WebSession, WebVisitor

    site = await api.post("/visits/sites", {"name": "Company website", "domain": "aivacrm.example"})
    rng = random.Random(20261023)
    known = [("contact", c[0], f"{c[1]} {c[2]}", c[3]) for c in context["contacts"][:6]]
    known += [("lead", l[0], f"{l[1]} {l[2]}", l[3]) for l in context["leads"][:3]]
    agents = [("desktop", "Chrome"), ("desktop", "Safari"), ("mobile", "Safari"), ("desktop", "Edge"), ("mobile", "Chrome"), ("desktop", "Firefox")]
    zones = ["America/Chicago", "America/New_York", "America/Los_Angeles", "America/Denver", "Europe/London"]
    latest = None
    async with AsyncSessionLocal() as db:
        db.sync_session.info["automation_disabled"] = True
        site_id = uuid.UUID(site["id"])
        for index in range(22):
            person = known[index] if index < len(known) else None
            device, browser = agents[index % len(agents)]
            visitor = WebVisitor(
                id=uuid.uuid4(), organization_id=context["org"], site_id=site_id,
                visitor_key=hashlib.sha1(f"demo-visitor-{index}".encode()).hexdigest()[:24],
                email=person[3].lower() if person else None, name=person[2] if person else None,
                contact_id=person[1] if person and person[0] == "contact" else None,
                lead_id=person[1] if person and person[0] == "lead" else None,
                first_seen_at=now(), last_seen_at=now(), visit_count=0, page_view_count=0, total_seconds=0,
                device=device, browser=browser, language="en-US", timezone=rng.choice(zones),
            )
            db.add(visitor)
            await db.flush()
            session_count = rng.choice([1, 1, 2, 2, 3, 4]) if person else rng.choice([1, 1, 1, 2])
            starts = sorted(now() - timedelta(days=rng.uniform(0.05, 24), minutes=rng.randint(0, 600)) for _ in range(session_count))
            for number, started in enumerate(starts):
                referrer, utm = rng.choice(SOURCES)
                path_count = rng.randint(1, 5)
                paths = [PAGES[0] if rng.random() < 0.4 else rng.choice(PAGES)]
                paths += rng.sample(PAGES[1:], k=min(path_count - 1, len(PAGES) - 1))
                if person and number == len(starts) - 1 and PAGES[5] not in paths:
                    paths.append(PAGES[5])
                landing = f"https://www.aivacrm.example{paths[0][0]}"
                if utm:
                    landing += f"?utm_source={utm[0]}&utm_medium={utm[1]}&utm_campaign={utm[2]}"
                session = WebSession(
                    id=uuid.uuid4(), organization_id=context["org"], visitor_id=visitor.id, site_id=site_id,
                    session_key=hashlib.sha1(f"demo-session-{index}-{number}".encode()).hexdigest()[:24],
                    started_at=started, last_activity_at=started, page_count=0, duration_seconds=0, referrer=referrer,
                    utm_source=utm[0] if utm else None, utm_medium=utm[1] if utm else None, utm_campaign=utm[2] if utm else None,
                    landing_url=landing,
                )
                db.add(session)
                await db.flush()
                moment = started
                for view_number, (path, title) in enumerate(paths):
                    seconds = rng.randint(8, 210)
                    url = landing if view_number == 0 else f"https://www.aivacrm.example{path}"
                    db.add(WebPageView(id=uuid.uuid4(), organization_id=context["org"], session_id=session.id, visitor_id=visitor.id,
                                       view_key=uuid.uuid4().hex[:16], url=url, path=path, title=title, viewed_at=moment, seconds=seconds))
                    session.page_count += 1
                    session.duration_seconds += seconds
                    session.exit_url = url
                    moment += timedelta(seconds=seconds + rng.randint(2, 20))
                session.last_activity_at = moment
                visitor.visit_count += 1
                visitor.page_view_count += session.page_count
                visitor.total_seconds += session.duration_seconds
                if number == 0:
                    visitor.first_seen_at = started
                    visitor.first_referrer = referrer
                    visitor.first_source = {k: v for k, v in zip(("source", "medium", "campaign"), utm)} if utm else {}
                visitor.last_seen_at = moment
                visitor.last_url = session.exit_url
                if person and number == len(starts) - 1:
                    visitor.identified_at = moment
                latest = max(latest, moment) if latest else moment
        await db.execute(update(TrackingSite).where(TrackingSite.id == site_id).values(last_hit_at=latest))
        await db.commit()


# ---------------------------------------------------------------------------
# SalesInbox
# ---------------------------------------------------------------------------


async def _email(api: Client, context: dict) -> None:
    people = {
        "Daniel Ruiz": "daniel.ruiz@aiva.local", "Priya Raman": "priya.raman@aiva.local", "Maya Chen": "maya.chen@aiva.local",
        "Tom Becker": "tom.becker@aiva.local", "Aisha Okafor": "aisha.okafor@aiva.local",
    }

    # Written directly: the demo team's @aiva.local addresses are a reserved
    # domain that the public email API (rightly) rejects.
    from app.models.email_message import EmailMessage
    from app.models.email_thread import EmailThread

    async def thread(subject: str, outside: tuple[str, str], insider: str, messages: list[tuple[str, str, int]], unread_last: bool = False, **links: Any) -> None:
        external_email, external_name = outside
        times = [now() - timedelta(hours=hours) for _, _, hours in messages]
        record = {k: v for k, v in links.items() if v}
        async with AsyncSessionLocal() as db:
            db.sync_session.info["automation_disabled"] = True
            created = EmailThread(
                id=uuid.uuid4(), organization_id=context["org"], provider="internal", subject=subject, snippet=messages[-1][1][:180],
                participants=[{"email": external_email, "name": external_name}, {"email": people[insider], "name": insider}],
                last_message_at=max(times), **record,
            )
            db.add(created)
            await db.flush()
            for number, ((direction, body, _), when) in enumerate(zip(messages, times)):
                inbound = direction == "in"
                db.add(
                    EmailMessage(
                        id=uuid.uuid4(), organization_id=context["org"], thread_id=created.id, provider="internal",
                        direction="inbound" if inbound else "outbound", subject=subject if number == 0 else f"Re: {subject}",
                        from_address=external_email if inbound else people[insider], from_name=external_name if inbound else insider,
                        to_recipients=[{"email": people[insider] if inbound else external_email}], cc_recipients=[], bcc_recipients=[],
                        body_text=body, snippet=body[:180], occurred_at=when,
                        is_read=not (inbound and unread_last and number == len(messages) - 1), is_draft=False, has_attachments=False,
                        message_metadata={}, **record,
                    )
                )
            await db.commit()

    for deal, insider in zip(context["open"][:3], ("Daniel Ruiz", "Priya Raman", "Daniel Ruiz")):
        person = _person(context, deal)
        if not person:
            continue
        first = person[1]
        await thread(
            f"Revised pricing for {deal[1]}", (person[3], f"{person[1]} {person[2]}"), insider,
            [("out", f"Hi {first},\n\nAttached is the revised quote using our volume pricing. At your seat count it comes to 8% below list. "
                     "Happy to walk through it this week.\n\nBest,\n" + insider.split()[0], 30),
             ("in", f"Thanks, this looks closer. Procurement wants net-45 terms and a cap on the renewal increase. Can you do both?\n\n{first}", 6)],
            unread_last=True, opportunity_id=deal[0], account_id=deal[2], contact_id=person[0],
        )
    contacts = context["contacts"]
    if contacts:
        person = contacts[0]
        await thread("Onboarding: SSO and user import", (person[3], f"{person[1]} {person[2]}"), "Tom Becker",
                     [("in", "Hi Tom, our IT team is ready to set up single sign-on. Do you need anything from us besides the Okta metadata?", 50),
                      ("out", "Hi! Just the metadata URL and a test user. Once SSO works I'll send the CSV template for the user import.", 47)],
                     contact_id=person[0], account_id=person[4])
    if len(contacts) > 1:
        person = contacts[1]
        await thread("Copy of last invoice", (person[3], f"{person[1]} {person[2]}"), "Aisha Okafor",
                     [("in", "Could you resend last month's invoice? Our finance team needs a copy with the PO number on it.", 3)],
                     unread_last=True, contact_id=person[0], account_id=person[4])
    for lead, insider in zip(context["leads"][:2], ("Priya Raman", "Daniel Ruiz")):
        await thread("Question about plans and pricing", (lead[3], f"{lead[1]} {lead[2]}"), insider,
                     [("in", f"Hello, we're a team of 30 at {lead[4] or 'our company'} looking to move off spreadsheets. "
                             "Does the Pro plan include web forms and journeys?", 20),
                      ("out", "Hi! Yes, both are on Pro. I'd love to show you a quick demo. Would Tuesday or Wednesday work?", 18)])
    await thread("Speaking slot at SaaS Connect", ("events@saasconnect.example", "SaaS Connect Events"), "Maya Chen",
                 [("in", "We'd love to have someone from AIVA speak on our revenue operations panel in March. Are you interested?", 72)])


# ---------------------------------------------------------------------------
# Team chat
# ---------------------------------------------------------------------------


async def _chat(api: Client, context: dict) -> None:
    from app.models.collaboration import ChatChannel, ChatMessage

    users = context["users"]
    for name in users:
        await api.get("/chat/channels", as_user=name)  # joins #general
    general = next(c for c in (await api.get("/chat/channels"))["items"] if c["name"] == "general")
    posted: list[tuple[str, float]] = []

    async def say(channel_id: str, who: str, body: str, hours_ago: float, record: tuple[str, uuid.UUID] | None = None) -> None:
        payload: dict[str, Any] = {"body": body}
        if record:
            payload.update({"record_type": record[0], "record_id": str(record[1])})
        message = await api.post(f"/chat/channels/{channel_id}/messages", payload, as_user=who)
        posted.append((message["id"], hours_ago))

    mention = lambda name: f"@[{name}](user:{users[name]})"  # noqa: E731
    await say(general["id"], "Maya Chen", "Morning all! Reminder: forecast calls move to Thursdays from next week.", 26)
    await say(general["id"], "Tom Becker", "Support update: SLA attainment was 97% last week. Thanks for jumping on the escalations, team.", 22)
    if context["won"]:
        deal = context["won"][0]
        await say(general["id"], "Daniel Ruiz", f"Closed {deal[1]}! Huge thanks to {mention('Tom Becker')} for the demo environment.", 20, ("opportunity", deal[0]))
    await say(general["id"], "AIVA Administrator", "Congrats! The implementation project is set up under Service → Projects.", 19)

    desk = await api.post("/chat/channels", {"name": "deal-desk", "topic": "Discounts, terms and approvals for open deals",
                                             "member_ids": [str(users[n]) for n in ("Maya Chen", "Daniel Ruiz", "Priya Raman")]})
    if context["open"]:
        deal = context["open"][0]
        await say(desk["id"], "Daniel Ruiz", f"{mention('Maya Chen')} customer is asking for net-45 and a renewal cap on {deal[1]}. OK to offer 5%?", 5, ("opportunity", deal[0]))
        await say(desk["id"], "Maya Chen", "Net-45 is fine. Cap at 5% only with a 2-year term. Send the quote for approval when it's ready.", 4)

    direct = await api.post("/chat/direct", {"user_id": str(users["AIVA Administrator"])}, as_user="Maya Chen")
    await say(direct["id"], "Maya Chen", "Can we add Priya to the Enterprise Sales team? She's taking over two of my accounts.", 2)

    async with AsyncSessionLocal() as db:
        for message_id, hours_ago in posted:
            when = now() - timedelta(hours=hours_ago)
            await db.execute(update(ChatMessage).where(ChatMessage.id == uuid.UUID(message_id)).values(created_at=when, updated_at=when))
        for channel_id, newest in ((general["id"], 19), (desk["id"], 4), (direct["id"], 2)):
            await db.execute(update(ChatChannel).where(ChatChannel.id == uuid.UUID(channel_id)).values(last_message_at=now() - timedelta(hours=newest)))
        await db.commit()


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


async def _pins(api: Client, context: dict) -> None:
    if context["open"]:
        await api.post("/pins", {"record_type": "opportunity", "record_id": str(context["open"][0][0])})
    if context["won"]:
        await api.post("/pins", {"record_type": "account", "record_id": str(context["won"][0][2])})
    await api.post("/pins", {"record_type": "page", "record_id": "forecast", "label": "Forecast", "href": "/forecast"})
