"""End-to-end tests for vendors, price books, sales and purchase orders,
meetings, documents, services and appointments, projects, social
publishing, website visits, pins, team chat, the people directory and
SalesInbox.

Runs against a real PostgreSQL database with a brand-new workspace per run
(skipped when the database isn't reachable). Social networks are replaced
with fakes; nothing leaves the machine.
"""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Integration#2026"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Modules Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@modules.example"
    try:
        response = client.post(
            f"{API}/auth/signup",
            json={"full_name": "Morgan Owner", "email": email, "password": PASSWORD, "company_name": company},
        )
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    if response.status_code >= 500:
        pytest.skip("PostgreSQL not available for integration tests.")
    assert response.status_code == 201, response.text
    data = response.json()
    return {
        "email": email,
        "org": data["organization_id"],
        "user_id": data["user_id"],
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"]},
    }


def invite(client, ws, role: str, name: str) -> dict:
    email = f"{role}-{uuid.uuid4().hex[:8]}@modules.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": name, "role": role}, headers=ws["headers"])
    assert invited.status_code == 201, invited.text
    token = invited.json()["invite_url"].split("token=")[1]
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    assert joined.status_code == 200, joined.text
    return {
        "user_id": joined.json()["user_id"],
        "email": email,
        "name": name,
        "headers": {"Authorization": f"Bearer {joined.json()['access_token']}", "X-Organization-ID": ws["org"]},
    }


@pytest.fixture(scope="module")
def ws(client):
    return signup(client)


@pytest.fixture(scope="module")
def rep(client, ws):
    return invite(client, ws, "member", "Riley Rep")


@pytest.fixture(scope="module")
def account(client, ws):
    response = client.post(
        f"{API}/accounts",
        json={"name": "Northwind Traders", "industry": "Logistics", "billing_city": "Dallas", "billing_state": "TX", "billing_country": "US"},
        headers=ws["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def contact(client, ws, account):
    response = client.post(
        f"{API}/contacts",
        json={"first_name": "Olivia", "last_name": "Johnson", "email": "olivia@northwind.example", "account_id": account["id"]},
        headers=ws["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def product(client, ws):
    h = ws["headers"]
    created = client.post(f"{API}/products", json={"name": "Router X1", "sku": f"RX-{uuid.uuid4().hex[:6]}", "unit_price": "200"}, headers=h).json()
    assert client.patch(f"{API}/inventory/products/{created['id']}", json={"track_inventory": True, "reorder_level": "5", "unit_cost": "90"}, headers=h).status_code == 200
    client.post(f"{API}/inventory/products/{created['id']}/movements", json={"kind": "receive", "quantity": "10"}, headers=h)
    return created


def stock_of(client, ws, product_id) -> float:
    return next(i for i in client.get(f"{API}/inventory", headers=ws["headers"]).json()["items"] if i["id"] == product_id)["stock_on_hand"]


@pytest.fixture(scope="module")
def deal(client, ws, account):
    h = ws["headers"]
    pipelines = client.get(f"{API}/pipelines", headers=h).json()
    stages = client.get(f"{API}/pipelines/{pipelines[0]['id']}/stages", headers=h).json()
    stage = next(s for s in stages if s["category"] == "open")
    response = client.post(
        f"{API}/opportunities",
        json={"name": "Warehouse rollout", "account_id": account["id"], "pipeline_id": pipelines[0]["id"], "stage_id": stage["id"], "amount": "52000"},
        headers=h,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def test_new_features_are_on_the_price_list_and_gated(client):
    labels = {f["label"] for group in client.get(f"{API}/public/plans").json()["feature_groups"] for f in group["features"]}
    for label in ("Documents", "Team chat", "Price books", "Sales orders", "Vendors and purchase orders", "Services and appointments", "Projects", "Social publishing", "Website visits"):
        assert label in labels
    starter = signup(client, "Starter Modules")
    h = starter["headers"]
    assert client.post(f"{API}/billing/change-plan", json={"plan": "starter"}, headers=h).status_code == 200
    for path in ("/sales-orders", "/purchase-orders", "/vendors", "/price-books", "/projects", "/services", "/social/posts", "/visits/sites"):
        assert client.get(f"{API}{path}", headers=h).status_code == 402, path
    assert client.get(f"{API}/documents", headers=h).status_code == 200
    assert client.get(f"{API}/chat/channels", headers=h).status_code == 200
    assert client.get(f"{API}/meetings", headers=h).status_code == 200
    assert client.get(f"{API}/pins", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# Vendors and purchase orders
# ---------------------------------------------------------------------------


def test_vendor_purchase_order_and_receiving(client, ws, rep, product):
    h = ws["headers"]
    vendor = client.post(
        f"{API}/vendors",
        json={"name": "Acme Components", "email": "Orders@Acme.example", "category": "Hardware", "payment_terms": "Net 30"},
        headers=h,
    )
    assert vendor.status_code == 201, vendor.text
    vendor = vendor.json()
    assert vendor["email"] == "orders@acme.example"
    assert client.post(f"{API}/vendors", json={"name": "Nope"}, headers=rep["headers"]).status_code == 403

    po = client.post(
        f"{API}/purchase-orders",
        json={
            "subject": "Q4 router restock",
            "vendor_id": vendor["id"],
            "shipping": "25",
            "line_items": [
                {"product_id": product["id"], "name": "Router X1", "quantity": "20", "unit_price": "80", "discount_percent": "10"},
                {"name": "Freight insurance", "quantity": "1", "unit_price": "40"},
            ],
        },
        headers=h,
    )
    assert po.status_code == 201, po.text
    po = po.json()
    assert po["display_number"].startswith("PO-") and po["status"] == "draft"
    assert po["subtotal"] == 1640 and po["discount_total"] == 160 and po["total"] == 1505

    assert client.post(f"{API}/purchase-orders/{po['id']}/receive", json={"lines": [{"index": 0, "quantity": "5"}]}, headers=h).status_code == 409
    assert client.post(f"{API}/purchase-orders/{po['id']}/status", json={"status": "issued"}, headers=h).json()["status"] == "issued"
    assert client.patch(f"{API}/purchase-orders/{po['id']}", json={"line_items": []}, headers=h).status_code == 409

    before = stock_of(client, ws, product["id"])
    partial = client.post(f"{API}/purchase-orders/{po['id']}/receive", json={"lines": [{"index": 0, "quantity": "12"}], "note": "First pallet"}, headers=h)
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "partially_received" and partial.json()["received_percent"] == 57
    assert stock_of(client, ws, product["id"]) == before + 12
    inventory_item = next(i for i in client.get(f"{API}/inventory", headers=h).json()["items"] if i["id"] == product["id"])
    assert inventory_item["unit_cost"] == 72  # 80 less 10%

    too_many = client.post(f"{API}/purchase-orders/{po['id']}/receive", json={"lines": [{"index": 0, "quantity": "9"}]}, headers=h)
    assert too_many.status_code == 422 and "8" in too_many.json()["detail"]
    done = client.post(f"{API}/purchase-orders/{po['id']}/receive", json={"lines": [{"index": 0, "quantity": "8"}, {"index": 1, "quantity": "1"}]}, headers=h).json()
    assert done["status"] == "received" and done["received_percent"] == 100
    assert client.post(f"{API}/purchase-orders/{po['id']}/status", json={"status": "cancelled"}, headers=h).status_code == 409
    assert client.delete(f"{API}/purchase-orders/{po['id']}", headers=h).status_code == 409

    movements = client.get(f"{API}/inventory/movements?product_id={product['id']}", headers=h).json()
    assert any(m["reference"] == f"PO-{po['number']}" and m["kind"] == "receive" for m in movements)

    detail = client.get(f"{API}/vendors/{vendor['id']}", headers=h).json()
    assert detail["order_count"] == 1 and detail["total_spend"] == 1505
    assert detail["products"][0]["name"] == "Router X1"
    assert client.delete(f"{API}/vendors/{vendor['id']}", headers=h).status_code == 409
    assert client.patch(f"{API}/vendors/{vendor['id']}", json={"is_active": False}, headers=h).json()["is_active"] is False
    assert client.post(f"{API}/purchase-orders", json={"subject": "x", "vendor_id": vendor["id"]}, headers=h).status_code == 409
    client.patch(f"{API}/vendors/{vendor['id']}", json={"is_active": True}, headers=h)

    # Low-stock reorder drafts a PO from products at or below their reorder level.
    client.post(f"{API}/inventory/products/{product['id']}/movements", json={"kind": "count", "quantity": "3"}, headers=h)
    reorder = client.post(f"{API}/purchase-orders/reorder", json={"vendor_id": vendor["id"]}, headers=h)
    assert reorder.status_code == 201, reorder.text
    line = reorder.json()["line_items"][0]
    assert line["name"] == "Router X1" and line["quantity"] == 7 and line["unit_price"] == 72
    open_list = client.get(f"{API}/purchase-orders?status=draft", headers=h).json()
    assert open_list["summary"]["drafts"] >= 1
    assert client.delete(f"{API}/purchase-orders/{reorder.json()['id']}", headers=h).status_code == 204
    # Back to a healthy level for the sales order test.
    client.post(f"{API}/inventory/products/{product['id']}/movements", json={"kind": "count", "quantity": "30"}, headers=h)


# ---------------------------------------------------------------------------
# Price books and sales orders
# ---------------------------------------------------------------------------


def test_price_books_apply_to_quotes(client, ws, account, product):
    h = ws["headers"]
    book = client.post(f"{API}/price-books", json={"name": "Wholesale 2027", "pricing_model": "flat"}, headers=h)
    assert book.status_code == 201, book.text
    book = book.json()
    assert client.post(f"{API}/price-books", json={"name": "wholesale 2027"}, headers=h).status_code == 409
    flat_tiers = client.put(
        f"{API}/price-books/{book['id']}/entries",
        json=[{"product_id": product["id"], "list_price": "180", "tiers": [{"from_qty": "10", "discount_percent": "5"}]}],
        headers=h,
    )
    assert flat_tiers.status_code == 422
    client.patch(f"{API}/price-books/{book['id']}", json={"pricing_model": "differential"}, headers=h)
    overlap = client.put(
        f"{API}/price-books/{book['id']}/entries",
        json=[{"product_id": product["id"], "list_price": "180", "tiers": [{"from_qty": "1", "to_qty": "10", "discount_percent": "0"}, {"from_qty": "5", "discount_percent": "5"}]}],
        headers=h,
    )
    assert overlap.status_code == 422
    saved = client.put(
        f"{API}/price-books/{book['id']}/entries",
        json=[{"product_id": product["id"], "list_price": "180", "tiers": [{"from_qty": "1", "to_qty": "9", "discount_percent": "0"}, {"from_qty": "10", "discount_percent": "7.5"}]}],
        headers=h,
    ).json()
    assert saved["entry_count"] == 1 and saved["entries"][0]["standard_price"] == 200
    price = client.get(f"{API}/price-books/{book['id']}/price?product_id={product['id']}&quantity=12", headers=h).json()
    assert price == {"in_book": True, "unit_price": 180.0, "discount_percent": 7.5}

    quote = client.post(
        f"{API}/quotes",
        json={"name": "Wholesale order", "account_id": account["id"], "line_items": [{"product_id": product["id"], "name": "Router X1", "quantity": "12", "unit_price": "200"}]},
        headers=h,
    ).json()
    applied = client.post(f"{API}/quotes/{quote['id']}/price-book", json={"price_book_id": book["id"]}, headers=h)
    assert applied.status_code == 200, applied.text
    line = applied.json()["line_items"][0]
    assert line["unit_price"] == 180 and line["discount_percent"] == 7.5
    assert applied.json()["price_book_id"] == book["id"] and applied.json()["total"] == 1998
    removed = client.post(f"{API}/quotes/{quote['id']}/price-book", json={"price_book_id": None}, headers=h).json()
    assert removed["line_items"][0]["unit_price"] == 200 and removed["price_book_id"] is None


def test_sales_order_lifecycle_from_quote(client, ws, account, contact, product):
    h = ws["headers"]
    quote = client.post(
        f"{API}/quotes",
        json={"name": "Router refresh", "account_id": account["id"], "contact_id": contact["id"], "line_items": [{"product_id": product["id"], "name": "Router X1", "quantity": "4", "unit_price": "200"}]},
        headers=h,
    ).json()
    assert client.post(f"{API}/sales-orders/from-quote/{quote['id']}", headers=h).status_code == 409
    client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "sent"}, headers=h)
    client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "accepted"}, headers=h)
    after_quote = stock_of(client, ws, product["id"])

    order = client.post(f"{API}/sales-orders/from-quote/{quote['id']}", headers=h)
    assert order.status_code == 201, order.text
    order = order.json()
    assert order["display_number"].startswith("SO-") and order["total"] == 800
    assert order["stock_applied"] is True  # the accepted quote already took the stock
    assert order["billing_address"]["city"] == "Dallas"
    assert order["quote"]["display_number"] == quote["display_number"]
    assert client.post(f"{API}/sales-orders/from-quote/{quote['id']}", headers=h).status_code == 409

    assert client.post(f"{API}/sales-orders/{order['id']}/status", json={"status": "fulfilled"}, headers=h).status_code == 409
    assert client.post(f"{API}/sales-orders/{order['id']}/status", json={"status": "confirmed"}, headers=h).json()["status"] == "confirmed"
    assert client.patch(f"{API}/sales-orders/{order['id']}", json={"line_items": []}, headers=h).status_code == 409
    fulfilled = client.post(f"{API}/sales-orders/{order['id']}/status", json={"status": "fulfilled", "carrier": "UPS", "tracking_number": "1Z999"}, headers=h).json()
    assert fulfilled["status"] == "fulfilled" and fulfilled["carrier"] == "UPS"
    assert stock_of(client, ws, product["id"]) == after_quote  # no double deduction

    invoice = client.post(f"{API}/invoices/from-sales-order/{order['id']}", json={"due_in_days": 10}, headers=h)
    assert invoice.status_code == 201, invoice.text
    assert client.post(f"{API}/invoices/from-sales-order/{order['id']}", json={}, headers=h).status_code == 409
    assert client.post(f"{API}/invoices/from-quote/{quote['id']}", json={}, headers=h).status_code == 409
    detail = client.get(f"{API}/sales-orders/{order['id']}", headers=h).json()
    assert detail["invoices"][0]["display_number"] == invoice.json()["display_number"]
    assert client.post(f"{API}/sales-orders/{order['id']}/status", json={"status": "cancelled"}, headers=h).status_code == 409
    assert client.delete(f"{API}/sales-orders/{order['id']}", headers=h).status_code == 409


def test_manual_sales_order_checks_stock(client, ws, account, product):
    h = ws["headers"]
    have = stock_of(client, ws, product["id"])
    order = client.post(
        f"{API}/sales-orders",
        json={
            "subject": "Big rush order",
            "account_id": account["id"],
            "shipping": "50",
            "line_items": [{"product_id": product["id"], "name": "Router X1", "quantity": str(int(have) + 5), "unit_price": "200", "tax_percent": "8"}],
        },
        headers=h,
    )
    assert order.status_code == 201, order.text
    order = order.json()
    gross = (have + 5) * 200
    assert order["total"] == round(gross * 1.08 + 50, 2)
    assert order["shipping_address"]["city"] == "Dallas"
    client.post(f"{API}/sales-orders/{order['id']}/status", json={"status": "confirmed"}, headers=h)
    short = client.post(f"{API}/sales-orders/{order['id']}/status", json={"status": "fulfilled"}, headers=h)
    assert short.status_code == 409 and "Not enough stock" in short.json()["detail"]
    backorder = client.post(f"{API}/sales-orders/{order['id']}/status", json={"status": "fulfilled", "allow_backorder": True}, headers=h)
    assert backorder.status_code == 200 and backorder.json()["stock_warnings"]
    assert stock_of(client, ws, product["id"]) == -5
    listing = client.get(f"{API}/sales-orders?search=SO-{order['number']}", headers=h).json()
    assert [o["id"] for o in listing["items"]] == [order["id"]]
    invoice = client.post(f"{API}/invoices/from-sales-order/{order['id']}", json={}, headers=h).json()
    assert invoice["line_items"][-1]["name"] == "Shipping"
    client.post(f"{API}/inventory/products/{product['id']}/movements", json={"kind": "count", "quantity": "30"}, headers=h)
    draft = client.post(f"{API}/sales-orders", json={"subject": "Never mind"}, headers=h).json()
    assert client.post(f"{API}/sales-orders/{draft['id']}/status", json={"status": "confirmed"}, headers=h).status_code == 409
    assert client.delete(f"{API}/sales-orders/{draft['id']}", headers=h).status_code == 204


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------


def test_meetings_schedule_invite_respond_and_complete(client, ws, rep, account, contact, deal):
    h = ws["headers"]
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0, second=0, minute=0)
    created = client.post(
        f"{API}/meetings",
        json={
            "title": "Rollout planning",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "is_online": True,
            "meeting_url": "https://meet.example/abc",
            "account_id": account["id"],
            "contact_id": contact["id"],
            "opportunity_id": deal["id"],
            "attendees": [
                {"type": "user", "id": rep["user_id"]},
                {"type": "contact", "id": contact["id"]},
                {"type": "email", "email": "Guest@Partner.example", "name": "Guest"},
                {"type": "email", "email": "guest@partner.example"},
            ],
        },
        headers=h,
    )
    assert created.status_code == 201, created.text
    meeting = created.json()
    assert meeting["status"] == "scheduled" and len(meeting["attendees"]) == 3
    assert {a["email"] for a in meeting["attendees"]} == {rep["email"], "olivia@northwind.example", "guest@partner.example"}
    assert meeting["account_name"] == "Northwind Traders" and meeting["opportunity_name"] == "Warehouse rollout"
    bad = client.post(f"{API}/meetings", json={"title": "x", "start_at": start.isoformat(), "end_at": start.isoformat()}, headers=h)
    assert bad.status_code == 422

    notes = client.get(f"{API}/notifications", headers=rep["headers"]).json()
    items = notes["items"] if isinstance(notes, dict) else notes
    assert any("Rollout planning" in n["title"] for n in items)

    clash = client.post(
        f"{API}/meetings",
        json={"title": "Overlap", "start_at": (start + timedelta(minutes=30)).isoformat(), "end_at": (start + timedelta(minutes=90)).isoformat(), "attendees": [{"type": "user", "id": rep["user_id"]}]},
        headers=h,
    ).json()
    assert clash["conflicts"] and clash["conflicts"][0]["title"] == "Rollout planning"

    responded = client.post(f"{API}/meetings/{meeting['id']}/respond", json={"response": "accepted"}, headers=rep["headers"])
    assert responded.status_code == 200 and responded.json()["my_response"] == "accepted"
    assert client.patch(f"{API}/meetings/{meeting['id']}", json={"title": "Hijack"}, headers=rep["headers"]).status_code == 403

    moved = client.patch(f"{API}/meetings/{meeting['id']}", json={"start_at": (start + timedelta(hours=3)).isoformat(), "end_at": (start + timedelta(hours=4)).isoformat()}, headers=h).json()
    assert moved["conflicts"] == [] and moved["start_at"].startswith((start + timedelta(hours=3)).strftime("%Y-%m-%dT%H"))
    ics = client.get(f"{API}/meetings/{meeting['id']}/invite.ics", headers=h)
    unfolded = ics.text.replace("\r\n ", "")
    assert ics.status_code == 200 and "BEGIN:VEVENT" in unfolded and "SEQUENCE:1" in unfolded and "mailto:guest@partner.example" in unfolded

    window = client.get(f"{API}/meetings?start={start.date().isoformat()}T00:00:00Z&end={(start + timedelta(days=1)).date().isoformat()}T23:59:00Z", headers=h).json()
    assert {m["title"] for m in window["items"]} >= {"Rollout planning", "Overlap"}
    by_deal = client.get(f"{API}/meetings?record_type=opportunity&record_id={deal['id']}", headers=h).json()
    assert [m["id"] for m in by_deal["items"]] == [meeting["id"]]
    rep_view = client.get(f"{API}/meetings?mine=true", headers=rep["headers"]).json()
    assert meeting["id"] in {m["id"] for m in rep_view["items"]}

    done = client.post(
        f"{API}/meetings/{meeting['id']}/complete",
        json={"summary": "Agreed on a phased rollout.", "notes": "Phase 1 in March.", "action_items": ["Send rollout plan", " ", "Book site survey"]},
        headers=h,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed" and done.json()["tasks_created"] == 2
    timeline = client.get(f"{API}/activities?account_id={account['id']}", headers=h).json()
    rows = timeline["items"] if isinstance(timeline, dict) else timeline
    assert any(a["subject"] == "Meeting: Rollout planning" for a in rows)
    tasks = client.get(f"{API}/tasks?opportunity_id={deal['id']}", headers=h).json()
    task_rows = tasks["items"] if isinstance(tasks, dict) else tasks
    assert {"Send rollout plan", "Book site survey"} <= {t["title"] for t in task_rows}
    assert client.post(f"{API}/meetings/{meeting['id']}/cancel", json={}, headers=h).status_code == 409

    cancelled = client.post(f"{API}/meetings/{clash['id']}/cancel", json={"reason": "Double booked"}, headers=h).json()
    assert cancelled["status"] == "cancelled" and cancelled["cancel_reason"] == "Double booked"
    assert client.delete(f"{API}/meetings/{clash['id']}", headers=h).status_code == 204


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def test_documents_folders_versions_links_and_privacy(client, ws, rep, account):
    h = ws["headers"]
    folder = client.post(f"{API}/documents/folders", json={"name": "Contracts"}, headers=h).json()
    sub = client.post(f"{API}/documents/folders", json={"name": "2027", "parent_id": folder["id"]}, headers=h).json()
    assert client.post(f"{API}/documents/folders", json={"name": "contracts"}, headers=h).status_code == 409
    assert client.patch(f"{API}/documents/folders/{folder['id']}", json={"parent_id": sub["id"]}, headers=h).status_code == 409

    payload = b"%PDF-1.4 master services agreement"
    uploaded = client.post(
        f"{API}/documents",
        files={"file": ("MSA – Northwind.pdf", payload, "application/pdf")},
        data={"folder_id": sub["id"], "description": "Signed MSA", "tags": "legal, signed", "record_type": "account", "record_id": account["id"]},
        headers=h,
    )
    assert uploaded.status_code == 201, uploaded.text
    doc = uploaded.json()
    assert doc["name"] == "MSA – Northwind.pdf" and doc["size_bytes"] == len(payload) and doc["version"] == 1
    assert doc["tags"] == ["legal", "signed"] and doc["previewable"] is True
    assert doc["links"][0]["label"] == "Northwind Traders" and doc["links"][0]["href"] == f"/accounts/{account['id']}"

    download = client.get(f"{API}/documents/{doc['id']}/download", headers=h)
    assert download.status_code == 200 and download.content == payload
    assert "attachment" in download.headers["content-disposition"]
    assert "inline" in client.get(f"{API}/documents/{doc['id']}/download?inline=true", headers=h).headers["content-disposition"]

    same = client.post(f"{API}/documents/{doc['id']}/versions", files={"file": ("msa.pdf", payload, "application/pdf")}, headers=h)
    assert same.status_code == 409
    v2 = client.post(f"{API}/documents/{doc['id']}/versions", files={"file": ("msa-v2.pdf", payload + b" amended", "application/pdf")}, data={"note": "Amendment 1"}, headers=h).json()
    assert v2["version"] == 2 and [v["version"] for v in v2["versions"]] == [2, 1] and v2["versions"][0]["note"] == "Amendment 1"
    assert client.get(f"{API}/documents/{doc['id']}/download?version=1", headers=h).content == payload

    for_account = client.get(f"{API}/documents?record_type=account&record_id={account['id']}", headers=h).json()
    assert [d["id"] for d in for_account["items"]] == [doc["id"]]
    assert client.get(f"{API}/documents?search=signed", headers=h).json()["items"][0]["id"] == doc["id"]
    folders = client.get(f"{API}/documents/folders", headers=h).json()
    assert next(f for f in folders["items"] if f["id"] == sub["id"])["document_count"] == 1
    assert client.delete(f"{API}/documents/folders/{sub['id']}", headers=h).status_code == 409

    blocked = client.post(f"{API}/documents", files={"file": ("setup.exe", b"MZ", "application/octet-stream")}, headers=h)
    assert blocked.status_code == 415
    assert client.post(f"{API}/documents", files={"file": ("empty.txt", b"", "text/plain")}, headers=h).status_code == 422

    private = client.post(f"{API}/documents", files={"file": ("salary-bands.csv", b"band,min\nA,1", "text/csv")}, data={"visibility": "private"}, headers=h).json()
    rep_list = client.get(f"{API}/documents", headers=rep["headers"]).json()
    assert private["id"] not in {d["id"] for d in rep_list["items"]} and doc["id"] in {d["id"] for d in rep_list["items"]}
    assert client.get(f"{API}/documents/{private['id']}", headers=rep["headers"]).status_code == 404
    assert client.patch(f"{API}/documents/{doc['id']}", json={"name": "Mine now"}, headers=rep["headers"]).status_code == 403

    linked = client.post(f"{API}/documents/{private['id']}/links", json={"record_type": "account", "record_id": account["id"]}, headers=h).json()
    assert len(linked["links"]) == 1
    unlinked = client.delete(f"{API}/documents/{private['id']}/links/{linked['links'][0]['link_id']}", headers=h).json()
    assert unlinked["links"] == []
    assert client.delete(f"{API}/documents/{private['id']}", headers=h).status_code == 204


# ---------------------------------------------------------------------------
# Services and appointments
# ---------------------------------------------------------------------------


def _next_weekday(days_ahead: int = 3) -> date:
    day = date.today() + timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def test_services_availability_and_appointments(client, ws, rep, contact):
    h = ws["headers"]
    service = client.post(
        f"{API}/services",
        json={"name": "Site survey", "duration_minutes": 90, "buffer_minutes": 15, "price": "250", "location_type": "customer_site", "staff_user_ids": [rep["user_id"]]},
        headers=h,
    )
    assert service.status_code == 201, service.text
    service = service.json()
    assert service["staff"][0]["name"] == "Riley Rep"
    assert client.post(f"{API}/services", json={"name": "site survey"}, headers=h).status_code == 409
    assert client.post(f"{API}/services", json={"name": "Nope"}, headers=rep["headers"]).status_code == 403

    day = _next_weekday()
    slots = client.get(f"{API}/services/{service['id']}/availability?date={day.isoformat()}", headers=h).json()
    assert slots["slots"] and all(s["staff_user_id"] == rep["user_id"] for s in slots["slots"])
    first = slots["slots"][0]

    booked = client.post(
        f"{API}/appointments",
        json={"service_id": service["id"], "contact_id": contact["id"], "start_at": first["start_at"]},
        headers=h,
    )
    assert booked.status_code == 201, booked.text
    booked = booked.json()
    assert booked["title"] == "Site survey" and booked["price"] == 250 and booked["staff_name"] == "Riley Rep"
    assert booked["account_name"] == "Northwind Traders"
    start = datetime.fromisoformat(booked["start_at"].replace("Z", "+00:00"))
    assert datetime.fromisoformat(booked["end_at"].replace("Z", "+00:00")) - start == timedelta(minutes=90)

    clash = client.post(f"{API}/appointments", json={"service_id": service["id"], "contact_id": contact["id"], "start_at": (start + timedelta(minutes=95)).isoformat()}, headers=h)
    assert clash.status_code == 409 and "already booked" in clash.json()["detail"]
    anyway = client.post(f"{API}/appointments", json={"service_id": service["id"], "contact_id": contact["id"], "start_at": (start + timedelta(minutes=95)).isoformat(), "allow_overlap": True}, headers=h)
    assert anyway.status_code == 201
    after = client.get(f"{API}/services/{service['id']}/availability?date={day.isoformat()}", headers=h).json()
    assert first["start_at"] not in {s["start_at"] for s in after["slots"]}

    assert client.post(f"{API}/appointments", json={"service_id": service["id"], "start_at": first["start_at"], "allow_overlap": True}, headers=h).status_code == 422
    assert client.post(f"{API}/appointments/{booked['id']}/status", json={"status": "completed"}, headers=h).status_code == 409
    cancelled = client.post(f"{API}/appointments/{anyway.json()['id']}/status", json={"status": "cancelled", "cancel_reason": "Customer asked"}, headers=h).json()
    assert cancelled["status"] == "cancelled"
    moved = client.patch(f"{API}/appointments/{booked['id']}", json={"start_at": (start + timedelta(days=7)).isoformat()}, headers=h).json()
    assert moved["start_at"] != booked["start_at"]
    listing = client.get(f"{API}/appointments?contact_id={contact['id']}", headers=h).json()
    assert {a["status"] for a in listing["items"]} == {"scheduled", "cancelled"}
    assert client.delete(f"{API}/services/{service['id']}", headers=h).status_code == 409
    assert client.patch(f"{API}/services/{service['id']}", json={"is_active": False}, headers=h).json()["is_active"] is False
    assert client.post(f"{API}/appointments", json={"service_id": service["id"], "contact_id": contact["id"], "start_at": (start + timedelta(days=9)).isoformat()}, headers=h).status_code == 409


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def test_projects_milestones_tasks_and_progress(client, ws, rep, deal):
    h = ws["headers"]
    project = client.post(f"{API}/projects/from-opportunity/{deal['id']}", headers=h)
    assert project.status_code == 201, project.text
    project = project.json()
    assert project["display_number"].startswith("PRJ-") and project["account_name"] == "Northwind Traders" and project["budget"] == 52000
    assert client.post(f"{API}/projects/from-opportunity/{deal['id']}", headers=h).status_code == 409
    detail = client.get(f"{API}/projects/{project['id']}", headers=h).json()
    assert [m["name"] for m in detail["milestones"]] == ["Kickoff", "Go-live"]
    kickoff = detail["milestones"][0]["id"]

    agent = invite(client, ws, "support_agent", "Sam Support")
    assert client.post(f"{API}/projects/{project['id']}/tasks", json={"title": "Sneaky"}, headers=agent["headers"]).status_code == 403
    client.patch(f"{API}/projects/{project['id']}", json={"member_user_ids": [rep["user_id"], agent["user_id"]], "status": "active"}, headers=h)
    assert client.post(f"{API}/projects/{project['id']}/tasks", json={"title": "Order cables"}, headers=agent["headers"]).status_code == 201
    task = client.post(
        f"{API}/projects/{project['id']}/tasks",
        json={"title": "Kickoff call", "milestone_id": kickoff, "assigned_to_user_id": rep["user_id"], "due_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
        headers=h,
    )
    assert task.status_code == 201, task.text
    assert task.json()["is_overdue"] is True
    second = client.post(f"{API}/projects/{project['id']}/tasks", json={"title": "Configure routers"}, headers=rep["headers"]).json()
    progress = client.get(f"{API}/projects/{project['id']}", headers=h).json()
    assert progress["tasks_total"] == 3 and progress["progress"] == 0 and progress["tasks_overdue"] == 1
    client.patch(f"{API}/projects/{project['id']}/tasks/{task.json()['id']}", json={"status": "completed"}, headers=rep["headers"])
    progress = client.get(f"{API}/projects/{project['id']}", headers=h).json()
    assert progress["progress"] == 33 and progress["milestones"][0]["tasks_done"] == 1
    assert progress["can_work"] is True
    rep_tasks = client.get(f"{API}/tasks?assigned_to_user_id={rep['user_id']}", headers=rep["headers"]).json()
    assert "Configure routers" in {t["title"] for t in rep_tasks}

    client.patch(f"{API}/projects/{project['id']}/milestones/{kickoff}", json={"status": "completed"}, headers=h)
    added = client.post(f"{API}/projects/{project['id']}/milestones", json={"name": "Training"}, headers=h).json()
    assert added["position"] == 2
    completed = client.patch(f"{API}/projects/{project['id']}", json={"status": "completed"}, headers=h).json()
    assert completed["status"] == "completed" and completed["completed_at"]
    assert client.post(f"{API}/projects/{project['id']}/tasks", json={"title": "Late"}, headers=h).status_code == 409
    listing = client.get(f"{API}/projects?status=completed", headers=h).json()
    assert project["id"] in {p["id"] for p in listing["items"]}
    assert client.patch(f"{API}/projects/{project['id']}", json={"start_date": "2030-01-10", "due_date": "2030-01-01"}, headers=h).status_code == 422
    assert client.delete(f"{API}/projects/{project['id']}/tasks/{second['id']}", headers=rep["headers"]).status_code == 204


# ---------------------------------------------------------------------------
# Social
# ---------------------------------------------------------------------------


def test_social_connect_publish_and_schedule(client, ws, monkeypatch):
    from app.api.routes import engagement
    from app.services import social

    published: list[tuple] = []

    async def fake_verify(network, external_id, token):
        if token == "bad-token-xxxx":
            raise social.SocialError("Invalid OAuth access token.")
        return {"name": f"Northwind on {network}", "handle": "northwind", "id": "u-123"}

    async def fake_publish(network, external_id, token, text, link_url, image_url):
        if network == "instagram" and not image_url:
            raise social.SocialError("Instagram posts need an image.")
        published.append((network, text))
        return social.Published(f"{network}-post-{len(published)}", f"https://{network}.example/p/{len(published)}")

    monkeypatch.setattr(social, "verify", fake_verify)
    monkeypatch.setattr(social, "publish", fake_publish)
    h = ws["headers"]
    rejected = client.post(f"{API}/social/channels", json={"network": "facebook", "external_id": "123", "access_token": "bad-token-xxxx"}, headers=h)
    assert rejected.status_code == 422 and "Invalid OAuth" in rejected.json()["detail"]
    assert client.post(f"{API}/social/channels", json={"network": "facebook", "access_token": "token-1234567"}, headers=h).status_code == 422
    fb = client.post(f"{API}/social/channels", json={"network": "facebook", "external_id": "998877", "access_token": "token-1234567"}, headers=h).json()
    x = client.post(f"{API}/social/channels", json={"network": "x", "access_token": "token-abcdefg"}, headers=h).json()
    assert fb["name"] == "Northwind on facebook" and x["external_id"] == "u-123"
    channels = client.get(f"{API}/social/channels", headers=h).json()["items"]
    assert all("access_token" not in c for c in channels)

    too_long = client.post(f"{API}/social/posts", json={"text": "x" * 281, "channel_ids": [x["id"]]}, headers=h)
    assert too_long.status_code == 422 and "X" in too_long.json()["detail"]
    now = client.post(f"{API}/social/posts", json={"text": "We're live in Dallas!", "link_url": "https://northwind.example", "channel_ids": [fb["id"], x["id"]], "publish_now": True}, headers=h)
    assert now.status_code == 201, now.text
    post = now.json()
    assert post["status"] == "published" and all(c["result"]["status"] == "published" for c in post["channels"])
    assert post["channels"][0]["result"]["url"].startswith("https://")

    later = client.post(
        f"{API}/social/posts",
        json={"text": "Webinar next week", "channel_ids": [fb["id"]], "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()},
        headers=h,
    ).json()
    assert later["status"] == "scheduled"
    assert client.post(f"{API}/social/posts", json={"text": "Past", "channel_ids": [fb["id"]], "scheduled_at": "2020-01-01T00:00:00Z"}, headers=h).status_code == 422

    async def due_now():
        from sqlalchemy import update

        from app.db.session import AsyncSessionLocal
        from app.models.engagement import SocialPost

        async with AsyncSessionLocal() as db:
            await db.execute(update(SocialPost).where(SocialPost.id == uuid.UUID(later["id"])).values(scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
            await db.commit()
            return await engagement.publish_due_posts(db)

    assert asyncio.run(due_now()) >= 1
    assert client.get(f"{API}/social/posts?status=published", headers=h).json()["counts"]["published"] >= 2
    assert ("facebook", "Webinar next week") in published
    draft = client.post(f"{API}/social/posts", json={"text": "Draft idea", "channel_ids": [x["id"]]}, headers=h).json()
    assert draft["status"] == "draft"
    assert client.patch(f"{API}/social/posts/{post['id']}", json={"text": "edit"}, headers=h).status_code == 409
    assert client.delete(f"{API}/social/posts/{draft['id']}", headers=h).status_code == 204
    assert client.delete(f"{API}/social/channels/{x['id']}", headers=h).status_code == 204


# ---------------------------------------------------------------------------
# Website visits
# ---------------------------------------------------------------------------


def track(client, **event):
    return client.post(f"{API}/public/track", content=json.dumps(event), headers={"Content-Type": "text/plain", "User-Agent": "Mozilla/5.0 (Macintosh) Chrome/130.0 Safari/537.36"})


def test_website_visits_tracking_and_identification(client, ws, contact):
    h = ws["headers"]
    site = client.post(f"{API}/visits/sites", json={"name": "Marketing site", "domain": "https://www.northwind.example/home"}, headers=h)
    assert site.status_code == 201, site.text
    site = site.json()
    assert site["domain"] == "northwind.example" and site["site_key"].startswith("ws_")
    key, visitor, session = site["site_key"], "visitor-aaaaaaaa", "session-11111111"
    assert track(client, k=key, v=visitor, s=session, e="view", p="view-00000001", u="https://www.northwind.example/pricing?utm_source=newsletter", t="Pricing", r="https://www.google.com/").status_code == 204
    track(client, k=key, v=visitor, s=session, e="ping", p="view-00000001", u="https://www.northwind.example/pricing", d=42)
    track(client, k=key, v=visitor, s=session, e="ping", p="view-00000001", u="https://www.northwind.example/pricing", d=30)
    track(client, k=key, v=visitor, s=session, e="view", p="view-00000002", u="https://northwind.example/demo", t="Book a demo")
    track(client, k=key, v=visitor, s=session, e="view", p="view-00000002", u="https://northwind.example/demo", t="Book a demo")
    # Wrong domain and bots are ignored.
    track(client, k=key, v="visitor-evilevil", s=session, e="view", p="view-00000003", u="https://evil.example/")
    client.post(f"{API}/public/track", content=json.dumps({"k": key, "v": "visitor-botbotbot", "s": session, "e": "view", "u": "https://northwind.example/"}), headers={"User-Agent": "Googlebot/2.1"})

    listing = client.get(f"{API}/visits/visitors", headers=h).json()
    assert len(listing["items"]) == 1
    row = listing["items"][0]
    assert row["page_view_count"] == 2 and row["visit_count"] == 1 and row["total_seconds"] == 42 and row["is_identified"] is False
    assert listing["summary"]["top_sources"][0]["source"] == "newsletter"
    assert {p["path"] for p in listing["summary"]["top_pages"]} == {"/pricing", "/demo"}

    track(client, k=key, v=visitor, s=session, e="identify", u="https://northwind.example/demo", email="OLIVIA@northwind.example", name="Olivia Johnson")
    detail = client.get(f"{API}/visits/visitors/{row['id']}", headers=h).json()
    assert detail["contact_id"] == contact["id"] and detail["label"] == "Olivia Johnson"
    assert [p["path"] for p in detail["sessions"][0]["pages"]] == ["/pricing", "/demo"]
    record = client.get(f"{API}/visits/record/contact/{contact['id']}", headers=h).json()
    assert record["page_view_count"] == 2 and record["sessions"][0]["source"] == "newsletter"
    assert client.post(f"{API}/visits/visitors/{row['id']}/create-lead", headers=h).status_code == 409

    track(client, k=key, v="visitor-bbbbbbbb", s="session-22222222", e="view", p="view-00000009", u="https://northwind.example/careers", t="Careers")
    track(client, k=key, v="visitor-bbbbbbbb", s="session-22222222", e="identify", u="https://northwind.example/careers", email="sam.new@prospect.example", name="Sam New")
    stranger = next(v for v in client.get(f"{API}/visits/visitors?identified=true", headers=h).json()["items"] if v["email"] == "sam.new@prospect.example")
    lead = client.post(f"{API}/visits/visitors/{stranger['id']}/create-lead", headers=h)
    assert lead.status_code == 201, lead.text
    assert client.get(f"{API}/leads/{lead.json()['lead_id']}", headers=h).json()["email"] == "sam.new@prospect.example"

    client.patch(f"{API}/visits/sites/{site['id']}", json={"is_active": False}, headers=h)
    track(client, k=key, v="visitor-cccccccc", s="session-33333333", e="view", p="view-00000010", u="https://northwind.example/")
    assert len(client.get(f"{API}/visits/visitors", headers=h).json()["items"]) == 2


# ---------------------------------------------------------------------------
# Pins, chat, people and SalesInbox
# ---------------------------------------------------------------------------


def test_pins(client, ws, rep, account, deal):
    h = ws["headers"]
    pin = client.post(f"{API}/pins", json={"record_type": "account", "record_id": account["id"]}, headers=h)
    assert pin.status_code == 201 and pin.json()["label"] == "Northwind Traders" and pin.json()["href"] == f"/accounts/{account['id']}"
    again = client.post(f"{API}/pins", json={"record_type": "account", "record_id": account["id"]}, headers=h).json()
    assert again["id"] == pin.json()["id"]
    second = client.post(f"{API}/pins", json={"record_type": "opportunity", "record_id": deal["id"]}, headers=h).json()
    page = client.post(f"{API}/pins", json={"record_type": "page", "record_id": "forecast", "label": "Forecast", "href": "/forecast"}, headers=h).json()
    assert client.post(f"{API}/pins", json={"record_type": "page", "record_id": "x", "href": "https://evil.example"}, headers=h).status_code == 422
    assert client.post(f"{API}/pins", json={"record_type": "account", "record_id": str(uuid.uuid4())}, headers=h).status_code == 404
    ordered = client.put(f"{API}/pins/order", json={"ids": [page["id"], second["id"]]}, headers=h).json()["items"]
    assert [p["id"] for p in ordered] == [page["id"], second["id"], pin.json()["id"]]
    assert client.get(f"{API}/pins", headers=rep["headers"]).json()["items"] == []
    assert client.delete(f"{API}/pins/{page['id']}", headers=rep["headers"]).status_code == 404
    assert client.delete(f"{API}/pins/{page['id']}", headers=h).status_code == 204


def test_team_chat_channels_mentions_and_direct_messages(client, ws, rep, deal):
    h, rh = ws["headers"], rep["headers"]
    channels = client.get(f"{API}/chat/channels", headers=h).json()
    general = next(c for c in channels["items"] if c["name"] == "general")
    assert general["is_member"]
    sales = client.post(f"{API}/chat/channels", json={"name": "#Deal Desk!", "topic": "Pricing questions"}, headers=h)
    assert sales.status_code == 201, sales.text
    sales = sales.json()
    assert sales["name"] == "deal-desk" and sales["title"] == "#deal-desk"
    assert client.post(f"{API}/chat/channels", json={"name": "deal-desk"}, headers=h).status_code == 409

    rep_channels = client.get(f"{API}/chat/channels", headers=rh).json()["items"]
    desk = next(c for c in rep_channels if c["id"] == sales["id"])
    assert desk["is_member"] is False
    assert client.post(f"{API}/chat/channels/{sales['id']}/messages", json={"body": "hi"}, headers=rh).status_code == 403

    message = client.post(
        f"{API}/chat/channels/{sales['id']}/messages",
        json={"body": f"@[Riley Rep](user:{rep['user_id']}) can you check this deal?", "record_type": "opportunity", "record_id": deal["id"]},
        headers=h,
    )
    assert message.status_code == 201, message.text
    assert message.json()["mentions"] == [rep["user_id"]] and message.json()["record"]["label"] == "Warehouse rollout"
    notices = client.get(f"{API}/notifications", headers=rh).json()
    items = notices["items"] if isinstance(notices, dict) else notices
    assert any("mentioned you in #deal-desk" in n["title"] for n in items)
    assert client.get(f"{API}/chat/unread", headers=rh).json()["unread"] >= 1
    history = client.get(f"{API}/chat/channels/{sales['id']}/messages", headers=rh).json()
    assert history["items"][-1]["body"].startswith("@[Riley Rep]")
    client.post(f"{API}/chat/channels/{sales['id']}/read", headers=rh)
    assert next(c for c in client.get(f"{API}/chat/channels", headers=rh).json()["items"] if c["id"] == sales["id"])["unread"] == 0

    since = history["server_time"]
    reply = client.post(f"{API}/chat/channels/{sales['id']}/messages", json={"body": "On it."}, headers=rh).json()
    newer = client.get(f"{API}/chat/channels/{sales['id']}/messages", params={"after": since}, headers=h).json()
    assert [m["id"] for m in newer["items"]] == [reply["id"]]
    assert client.patch(f"{API}/chat/messages/{reply['id']}", json={"body": "hack"}, headers=h).status_code == 403
    edited = client.patch(f"{API}/chat/messages/{reply['id']}", json={"body": "On it now."}, headers=rh).json()
    assert edited["edited_at"] and edited["body"] == "On it now."
    assert client.delete(f"{API}/chat/messages/{reply['id']}", headers=rh).status_code == 204

    dm = client.post(f"{API}/chat/direct", json={"user_id": rep["user_id"]}, headers=h).json()
    assert dm["kind"] == "direct" and dm["title"] == "Riley Rep"
    assert client.post(f"{API}/chat/direct", json={"user_id": ws["user_id"]}, headers=rh).json()["id"] == dm["id"]
    client.post(f"{API}/chat/channels/{dm['id']}/messages", json={"body": "Quick question"}, headers=h)
    outsider = signup(client, "Other Chat Co")
    assert client.get(f"{API}/chat/channels/{dm['id']}/messages", headers=outsider["headers"]).status_code == 404

    private = client.post(f"{API}/chat/channels", json={"name": "leadership", "is_private": True}, headers=h).json()
    assert private["id"] not in {c["id"] for c in client.get(f"{API}/chat/channels", headers=rh).json()["items"]}
    assert client.post(f"{API}/chat/channels/{private['id']}/join", headers=rh).status_code == 404
    client.post(f"{API}/chat/channels/{private['id']}/members", json={"user_ids": [rep["user_id"]]}, headers=h)
    assert private["id"] in {c["id"] for c in client.get(f"{API}/chat/channels", headers=rh).json()["items"]}
    assert client.post(f"{API}/chat/channels/{general['id']}/leave", headers=h).status_code == 409
    assert client.patch(f"{API}/chat/channels/{general['id']}", json={"name": "random"}, headers=h).status_code == 409


def test_people_directory_and_salesinbox(client, ws, rep, contact, deal):
    h = ws["headers"]
    people = client.get(f"{API}/people", headers=h).json()["items"]
    me = next(p for p in people if p["is_me"])
    assert me["role_name"] == "Owner"
    assert next(p for p in people if p["id"] == rep["user_id"])["role_name"] == "Sales representative"

    client.post(f"{API}/leads", json={"first_name": "Lena", "last_name": "Lead", "email": "lena@prospect.example", "source": "website"}, headers=h)
    for payload in (
        {"subject": "Pricing for rollout", "opportunity_id": deal["id"], "participants": [{"email": "olivia@northwind.example"}]},
        {"subject": "Hello Olivia", "contact_id": contact["id"], "participants": [{"email": "olivia@northwind.example"}]},
        {"subject": "Question about plans", "participants": [{"email": "Lena@prospect.example"}]},
        {"subject": "Newsletter", "participants": [{"email": "news@vendor.example"}]},
    ):
        created = client.post(f"{API}/email/threads", json={**payload, "last_message_at": datetime.now(timezone.utc).isoformat()}, headers=h)
        assert created.status_code == 201, created.text
    inbox = client.get(f"{API}/salesinbox", headers=h).json()
    columns = {k: [t["subject"] for t in v] for k, v in inbox["columns"].items()}
    assert "Pricing for rollout" in columns["deals"]
    assert "Hello Olivia" in columns["contacts"]
    assert "Question about plans" in columns["leads"]
    assert "Newsletter" in columns["others"]
    lead_item = next(t for t in inbox["columns"]["leads"] if t["subject"] == "Question about plans")
    assert lead_item["record"]["label"] == "Lena Lead"


def test_search_finds_new_records(client, ws):
    h = ws["headers"]
    groups = {g["type"]: g for g in client.get(f"{API}/search?q=router", headers=h).json()["groups"]}
    assert "sales_order" in groups or "purchase_order" in groups
    vendor = client.get(f"{API}/search?q=acme", headers=h).json()
    assert any(g["type"] == "vendor" for g in vendor["groups"])
