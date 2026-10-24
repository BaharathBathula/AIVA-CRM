"""Payments: Stripe, PayPal, Square and Chargebee.

Provider APIs are never called: ``_pay_common.http`` is replaced by a fake that
answers by method and URL pattern and records every call. Webhooks are posted
to the hub's public URL exactly as the providers would (signed where they sign).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Payments#2026"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Pay Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@pay.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Paula Owner", "email": email, "password": PASSWORD, "company_name": company})
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    if response.status_code >= 500:
        pytest.skip("PostgreSQL not available for integration tests.")
    assert response.status_code == 201, response.text
    data = response.json()
    return {
        "org": data["organization_id"],
        "user_id": data["user_id"],
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"], "X-Session-ID": data["session_id"]},
    }


def invite(client, ws: dict, role: str) -> dict:
    email = f"{role}-{uuid.uuid4().hex[:8]}@pay.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": f"{role.title()} Person", "role": role}, headers=ws["headers"]).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}


def run_service(fn):
    from app.db.session import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            result = await fn(db)
            await db.commit()
            return result

    return asyncio.run(go())


def make_invoice(ws: dict, total: str = "1000.00", currency: str = "USD", company: str = "Globex Corporation") -> dict:
    from app.models.account import Account
    from app.models.contact import Contact
    from app.models.editions import SalesInvoice

    async def go(db):
        org = uuid.UUID(ws["org"])
        number = int(time.time() * 1000) % 100_000_000
        account = Account(id=uuid.uuid4(), organization_id=org, name=f"{company} {uuid.uuid4().hex[:4]}", owner_user_id=uuid.UUID(ws["user_id"]))
        contact = Contact(id=uuid.uuid4(), organization_id=org, first_name="Gina", last_name="Payer", email=f"gina-{uuid.uuid4().hex[:6]}@globex.example",
                          account_id=account.id, owner_user_id=uuid.UUID(ws["user_id"]))
        db.add(account)
        await db.flush()
        db.add(contact)
        await db.flush()
        invoice = SalesInvoice(
            id=uuid.uuid4(), organization_id=org, number=number, account_id=account.id, contact_id=contact.id, owner_user_id=uuid.UUID(ws["user_id"]),
            status="sent", issue_date=date.today(), due_date=date.today() + timedelta(days=30), currency=currency,
            subtotal=Decimal(total), total=Decimal(total), amount_paid=Decimal("0"), line_items=[{"name": "Service", "quantity": "1", "unit_price": total}],
        )
        db.add(invoice)
        return {"id": str(invoice.id), "account_id": str(account.id), "contact_id": str(contact.id), "number": number, "account_name": account.name}

    return run_service(go)


def invoice_state(invoice_id: str) -> dict:
    from app.models.editions import SalesInvoice

    async def go(db):
        row = await db.get(SalesInvoice, uuid.UUID(invoice_id))
        return {"status": row.status, "amount_paid": Decimal(row.amount_paid), "paid_at": row.paid_at}

    return run_service(go)


class FakeProviders:
    """Stands in for ``_pay_common.http``."""

    def __init__(self):
        self.routes: list[tuple[str, re.Pattern[str], Any]] = []
        self.calls: list[dict[str, Any]] = []

    def on(self, method: str, pattern: str, answer: Any) -> None:
        self.routes.insert(0, (method, re.compile(pattern), answer))

    def find(self, method: str, fragment: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and fragment in c["url"]]

    async def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        for route_method, pattern, answer in self.routes:
            if route_method == method and pattern.search(url):
                value = answer(call) if callable(answer) else answer
                status, body = value if isinstance(value, tuple) else (200, value)
                return httpx.Response(status, json=body, request=httpx.Request(method, url))
        return httpx.Response(404, json={"error": {"message": f"no fake for {method} {url}"}}, request=httpx.Request(method, url))


@pytest.fixture
def fake(monkeypatch) -> FakeProviders:
    from app.integrations.providers import _pay_common, _pay_paypal, _pay_square, _pay_stripe

    providers = FakeProviders()
    monkeypatch.setattr(_pay_common, "http", providers)
    for module in (_pay_stripe, _pay_paypal, _pay_square):
        monkeypatch.setattr(module, "_public_https", lambda: True)
    return providers


def webhook_path(client, ws, key) -> str:
    detail = client.get(f"{API}/integration-hub/{key}", headers=ws["headers"]).json()
    url = detail["connection"]["webhook_url"]
    return API + url.split("/api/aiva", 1)[1]


def form(call: dict) -> dict[str, str]:
    return dict(call.get("data") or [])


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def test_minor_units_follow_the_currency():
    from app.integrations.providers import _pay_common as pay

    assert pay.to_minor(Decimal("1000.00"), "USD") == 100000
    assert pay.to_minor(Decimal("1000"), "JPY") == 1000
    assert pay.to_minor(Decimal("12.50"), "KWD") == 12500
    assert pay.from_minor(12345, "USD") == Decimal("123.45")
    assert pay.from_minor(1500, "jpy") == Decimal("1500.00")
    assert pay.decimal_value(Decimal("10"), "USD") == "10.00"


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


def stripe_sign(secret: str, body: bytes, stamp: int | None = None) -> str:
    stamp = stamp or int(time.time())
    return f"t={stamp},v1=" + hmac.new(secret.encode(), str(stamp).encode() + b"." + body, hashlib.sha256).hexdigest()


def test_stripe_links_payments_refunds_and_signatures(client, fake):
    ws = signup(client, "Stripe Co")
    h = ws["headers"]
    fake.on("GET", r"/v1/account$", {"id": "acct_1", "settings": {"dashboard": {"display_name": "Stripe Co"}}, "default_currency": "usd", "country": "US", "charges_enabled": True})
    fake.on("GET", r"/v1/webhook_endpoints$", {"data": []})
    fake.on("POST", r"/v1/webhook_endpoints$", {"id": "we_1", "secret": "whsec_test_secret"})
    assert client.put(f"{API}/integration-hub/stripe/toggle", json={"enabled": True}, headers=h).status_code == 200
    bad = client.put(f"{API}/integration-hub/stripe/credentials", json={"values": {"secret_key": "pk_test_abcdefghijklmnop"}}, headers=h)
    assert bad.status_code == 422 and "publishable" in bad.json()["detail"]
    saved = client.put(f"{API}/integration-hub/stripe/credentials", json={"values": {"secret_key": "sk_test_" + "a" * 24}}, headers=h)
    assert saved.status_code == 200, saved.text
    detail = saved.json()
    assert detail["status"] == "connected" and detail["extra"]["webhook_ready"] and detail["extra"]["account"]["mode"] == "test"
    assert "sk_test_" not in json.dumps(detail) and "whsec_test_secret" not in json.dumps(detail)
    created_hook = form(fake.find("POST", "/v1/webhook_endpoints")[0])
    assert created_hook["enabled_events[0]"] == "checkout.session.completed" and created_hook["url"].endswith(detail["connection"]["webhook_url"].rsplit("/", 1)[1])

    invoice = make_invoice(ws)
    fake.on("POST", r"/v1/prices$", {"id": "price_1"})
    fake.on("POST", r"/v1/payment_links$", {"id": "plink_1", "url": "https://buy.stripe.com/test_1", "active": True})
    made = client.post(f"{API}/payments/invoices/{invoice['id']}/links", json={"provider": "stripe"}, headers=h)
    assert made.status_code == 200, made.text
    link = made.json()["links"][0]
    assert link["url"] == "https://buy.stripe.com/test_1" and link["amount"] == "1000.00" and link["state"] == "open"
    price = form(fake.find("POST", "/v1/prices")[0])
    assert price["unit_amount"] == "100000" and price["currency"] == "usd"
    plink = form(fake.find("POST", "/v1/payment_links")[0])
    assert plink["payment_intent_data[metadata][aiva_invoice_id]"] == invoice["id"] and plink["restrictions[completed_sessions][limit]"] == "1"

    def intent(pid: str, cents: int, charge: str) -> dict:
        return {"id": pid, "status": "succeeded", "amount_received": cents, "currency": "usd", "livemode": False, "created": int(time.time()),
                "latest_charge": charge, "payment_method_types": ["card"], "metadata": {"aiva_organization_id": ws["org"], "aiva_invoice_id": invoice["id"]}}

    path = webhook_path(client, ws, "stripe")
    fake.on("GET", r"/v1/payment_intents/pi_1$", intent("pi_1", 40000, "ch_1"))
    event = json.dumps({"id": "evt_1", "type": "payment_intent.succeeded", "livemode": False, "data": {"object": {"id": "pi_1"}}}).encode()

    # Wrong secret, stale timestamp and a missing header are all refused.
    assert client.post(path, content=event, headers={"Stripe-Signature": stripe_sign("whsec_wrong", event)}).status_code == 400
    assert client.post(path, content=event, headers={"Stripe-Signature": stripe_sign("whsec_test_secret", event, int(time.time()) - 3600)}).status_code == 400
    assert client.post(path, content=event).status_code == 400
    assert invoice_state(invoice["id"])["amount_paid"] == 0

    ok = client.post(path, content=event, headers={"Stripe-Signature": stripe_sign("whsec_test_secret", event)})
    assert ok.status_code == 200, ok.text
    state = invoice_state(invoice["id"])
    assert state["amount_paid"] == Decimal("400.00") and state["status"] == "sent"
    # A replay (Stripe retries) changes nothing.
    client.post(path, content=event, headers={"Stripe-Signature": stripe_sign("whsec_test_secret", event)})
    assert invoice_state(invoice["id"])["amount_paid"] == Decimal("400.00")

    fake.on("GET", r"/v1/payment_intents/pi_2$", intent("pi_2", 60000, "ch_2"))
    session = json.dumps({"id": "evt_2", "type": "checkout.session.completed", "livemode": False,
                          "data": {"object": {"id": "cs_1", "payment_status": "paid", "payment_intent": "pi_2"}}}).encode()
    assert client.post(path, content=session, headers={"Stripe-Signature": stripe_sign("whsec_test_secret", session)}).status_code == 200
    state = invoice_state(invoice["id"])
    assert state["status"] == "paid" and state["amount_paid"] == Decimal("1000.00") and state["paid_at"] is not None

    # A refund made in Stripe: re-read from the API, applied once.
    fake.on("GET", r"/v1/refunds$", {"data": [{"id": "re_1", "status": "succeeded", "payment_intent": "pi_1", "amount": 15000, "currency": "usd", "created": int(time.time())}]})
    refunded = json.dumps({"id": "evt_3", "type": "charge.refunded", "livemode": False, "data": {"object": {"id": "ch_1"}}}).encode()
    for _ in range(2):
        assert client.post(path, content=refunded, headers={"Stripe-Signature": stripe_sign("whsec_test_secret", refunded)}).status_code == 200
    state = invoice_state(invoice["id"])
    assert state["amount_paid"] == Decimal("850.00") and state["status"] == "sent" and state["paid_at"] is None

    # A refund started from AIVA (admins only).
    listing = client.get(f"{API}/payments/invoices/{invoice['id']}", headers=h).json()
    second = next(p for p in listing["payments"] if p["external_id"] == "pi_2")
    assert listing["invoice"]["balance"] == "150.00" and len([p for p in listing["payments"] if p["type"] == "refund"]) == 1
    member = invite(client, ws, "member")
    assert client.post(f"{API}/payments/refunds", json={"payment_id": second["id"], "amount": "100"}, headers=member).status_code == 403
    too_much = client.post(f"{API}/payments/refunds", json={"payment_id": second["id"], "amount": "700"}, headers=h)
    assert too_much.status_code == 422
    fake.on("POST", r"/v1/refunds$", {"id": "re_2", "status": "succeeded"})
    done = client.post(f"{API}/payments/refunds", json={"payment_id": second["id"], "amount": "100", "reason": "Discount"}, headers=h)
    assert done.status_code == 200, done.text
    assert form(fake.find("POST", "/v1/refunds")[0])["amount"] == "10000"
    assert invoice_state(invoice["id"])["amount_paid"] == Decimal("750.00")

    # The account timeline and the owner's notifications have the payments.
    from sqlalchemy import select

    from app.models.activity import Activity
    from app.models.platform import Notification

    async def check(db):
        activities = (await db.execute(select(Activity).where(Activity.account_id == uuid.UUID(invoice["account_id"])))).scalars().all()
        notes = (await db.execute(select(Notification).where(Notification.user_id == uuid.UUID(ws["user_id"]), Notification.kind == "payment"))).scalars().all()
        return [a.subject for a in activities], len(notes)

    subjects, notes = run_service(check)
    assert any(s.startswith("Paid USD 400.00") for s in subjects) and any(s.startswith("Refunded USD 150.00") for s in subjects) and notes >= 3

    hub = client.get(f"{API}/integration-hub/stripe", headers=h).json()
    assert hub["extra"]["stats"]["payments_30d"] == 2 and hub["extra"]["recent"][0]["provider"] == "stripe"
    assert client.get(f"{API}/integration-hub/stripe", headers=member).json()["extra"]["webhook_url"] is None


def test_payment_links_need_invoice_rights_and_a_balance(client, fake):
    ws = signup(client, "Rights Co")
    h = ws["headers"]
    fake.on("GET", r"/v1/account$", {"id": "acct_2"})
    fake.on("GET", r"/v1/webhook_endpoints$", {"data": []})
    fake.on("POST", r"/v1/webhook_endpoints$", {"id": "we_2", "secret": "whsec_2"})
    client.put(f"{API}/integration-hub/stripe/toggle", json={"enabled": True}, headers=h)
    client.put(f"{API}/integration-hub/stripe/credentials", json={"values": {"secret_key": "rk_test_" + "b" * 24}}, headers=h)
    invoice = make_invoice(ws)
    viewer = invite(client, ws, "viewer")
    assert client.get(f"{API}/payments/invoices/{invoice['id']}", headers=viewer).status_code == 200
    assert client.post(f"{API}/payments/invoices/{invoice['id']}/links", json={"provider": "stripe"}, headers=viewer).status_code == 403
    assert client.post(f"{API}/payments/invoices/{invoice['id']}/links", json={"provider": "square"}, headers=h).status_code == 409  # not on
    other = signup(client, "Other Co")
    assert client.get(f"{API}/payments/invoices/{invoice['id']}", headers=other["headers"]).status_code == 404
    assert client.get(f"{API}/payments/accounts/{invoice['account_id']}", headers=other["headers"]).status_code == 404
    paid = make_invoice(ws, total="0.00")
    assert client.post(f"{API}/payments/invoices/{paid['id']}/links", json={"provider": "stripe"}, headers=h).status_code == 409
    # Switched off: webhooks are ignored, nothing changes.
    client.put(f"{API}/integration-hub/stripe/toggle", json={"enabled": False}, headers=h)
    path = webhook_path(client, ws, "stripe")
    body = b'{"type":"payment_intent.succeeded","data":{"object":{"id":"pi_x"}}}'
    off = client.post(path, content=body, headers={"Stripe-Signature": stripe_sign("whsec_2", body)})
    assert off.status_code == 200 and off.json().get("ignored")


# ---------------------------------------------------------------------------
# Square
# ---------------------------------------------------------------------------


def square_sign(key: str, url: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(key.encode(), url.encode() + body, hashlib.sha256).digest()).decode()


def test_square_signed_webhooks_payments_and_refunds(client, fake):
    ws = signup(client, "Square Co")
    h = ws["headers"]
    fake.on("GET", r"/v2/merchants/me$", {"merchant": {"id": "MERCH1", "business_name": "Square Co", "currency": "USD", "country": "US"}})
    fake.on("GET", r"/v2/locations/main$", {"location": {"id": "LOC1", "name": "Main", "status": "ACTIVE", "currency": "USD", "capabilities": ["CREDIT_CARD_PROCESSING"]}})
    client.put(f"{API}/integration-hub/square/toggle", json={"enabled": True}, headers=h)
    saved = client.put(f"{API}/integration-hub/square/credentials",
                       json={"values": {"access_token": "EAAA" + "x" * 40, "environment": "sandbox", "webhook_signature_key": "sq-signature-key"}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert saved.json()["extra"]["webhook_ready"] and saved.json()["extra"]["account"]["location_name"] == "Main"
    assert fake.find("GET", "connect.squareupsandbox.com/v2/merchants/me")

    eur = make_invoice(ws, currency="EUR")
    wrong = client.post(f"{API}/payments/invoices/{eur['id']}/links", json={"provider": "square"}, headers=h)
    assert wrong.status_code == 422 and "EUR" in wrong.json()["detail"]

    invoice = make_invoice(ws)
    fake.on("POST", r"/v2/online-checkout/payment-links$", {"payment_link": {"id": "PL1", "url": "https://square.link/u/abc", "order_id": "ORDER1"}})
    assert client.post(f"{API}/payments/invoices/{invoice['id']}/links", json={"provider": "square"}, headers=h).status_code == 200
    sent = fake.find("POST", "/v2/online-checkout/payment-links")[0]["json"]
    assert sent["quick_pay"]["price_money"] == {"amount": 100000, "currency": "USD"} and sent["quick_pay"]["location_id"] == "LOC1"

    path = webhook_path(client, ws, "square")
    full_url = client.get(f"{API}/integration-hub/square", headers=h).json()["connection"]["webhook_url"]
    fake.on("GET", r"/v2/payments/PAY1$", {"payment": {"id": "PAY1", "status": "COMPLETED", "order_id": "ORDER1", "amount_money": {"amount": 100000, "currency": "USD"},
                                                      "created_at": "2026-10-24T12:00:00Z", "receipt_url": "https://squareup.com/receipt/preview/PAY1"}})
    fake.on("DELETE", r"/v2/online-checkout/payment-links/PL1$", {})
    body = json.dumps({"merchant_id": "MERCH1", "type": "payment.updated", "event_id": "e1", "data": {"type": "payment", "id": "PAY1"}}).encode()
    assert client.post(path, content=body, headers={"x-square-hmacsha256-signature": square_sign("wrong", full_url, body)}).status_code == 403
    assert client.post(path, content=body, headers={"x-square-hmacsha256-signature": square_sign("sq-signature-key", "https://evil.example/hook", body)}).status_code == 403
    assert invoice_state(invoice["id"])["amount_paid"] == 0
    ok = client.post(path, content=body, headers={"x-square-hmacsha256-signature": square_sign("sq-signature-key", full_url, body)})
    assert ok.status_code == 200, ok.text
    assert invoice_state(invoice["id"])["status"] == "paid"
    assert fake.find("DELETE", "/v2/online-checkout/payment-links/PL1")

    fake.on("GET", r"/v2/refunds/R1$", {"refund": {"id": "R1", "status": "COMPLETED", "payment_id": "PAY1", "amount_money": {"amount": 20000, "currency": "USD"}}})
    refund = json.dumps({"merchant_id": "MERCH1", "type": "refund.updated", "data": {"type": "refund", "id": "R1"}}).encode()
    for _ in range(2):
        assert client.post(path, content=refund, headers={"x-square-hmacsha256-signature": square_sign("sq-signature-key", full_url, refund)}).status_code == 200
    state = invoice_state(invoice["id"])
    assert state["amount_paid"] == Decimal("800.00") and state["status"] == "sent"


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------


def test_paypal_invoices_are_verified_with_paypal_and_reconciled(client, fake):
    ws = signup(client, "PayPal Co")
    h = ws["headers"]
    fake.on("POST", r"/v1/oauth2/token$", {"access_token": "pp-token", "expires_in": 32000, "app_id": "APP-1", "scope": "openid https://uri.paypal.com/services/invoicing"})
    fake.on("POST", r"/v1/notifications/webhooks$", {"id": "WH-1"})
    client.put(f"{API}/integration-hub/paypal/toggle", json={"enabled": True}, headers=h)
    saved = client.put(f"{API}/integration-hub/paypal/credentials",
                       json={"values": {"client_id": "A" * 40, "client_secret": "B" * 40, "environment": "sandbox"}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert saved.json()["extra"]["webhook_ready"] and fake.find("POST", "api-m.sandbox.paypal.com/v1/oauth2/token")
    assert "B" * 40 not in json.dumps(saved.json())

    invoice = make_invoice(ws)
    pp_id = "INV2-ABCD-EFGH-IJKL-MNOP"
    fake.on("POST", r"/v2/invoicing/invoices$", (201, {"id": pp_id, "status": "DRAFT"}))
    fake.on("POST", rf"/v2/invoicing/invoices/{pp_id}/send$", {"href": "https://www.sandbox.paypal.com/invoice/p/#ABCD", "rel": "payer-view"})
    made = client.post(f"{API}/payments/invoices/{invoice['id']}/links", json={"provider": "paypal"}, headers=h)
    assert made.status_code == 200, made.text
    created = fake.find("POST", "/v2/invoicing/invoices")[0]["json"]
    assert created["items"][0]["unit_amount"] == {"currency_code": "USD", "value": "1000.00"} and created["detail"]["reference"] == invoice["id"]
    assert created["primary_recipients"][0]["billing_info"]["email_address"].startswith("gina-")
    assert fake.find("POST", "/send")[0]["json"] == {"send_to_invoicer": False, "send_to_recipient": False}

    paid = {"id": pp_id, "status": "PAID", "payments": {"transactions": [
        {"payment_id": "CAP-1", "type": "PAYPAL", "payment_date": "2026-10-24", "amount": {"currency_code": "USD", "value": "1000.00"}},
        {"payment_id": "EXT-1", "type": "EXTERNAL", "payment_date": "2026-10-24", "amount": {"currency_code": "USD", "value": "50.00"}},
    ]}}
    fake.on("GET", rf"/v2/invoicing/invoices/{pp_id}$", paid)
    path = webhook_path(client, ws, "paypal")
    event = json.dumps({"id": "WH-EV-1", "event_type": "INVOICING.INVOICE.PAID", "resource": {"invoice": {"id": pp_id}}}).encode()
    headers = {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
               "paypal-transmission-id": "t-1", "paypal-transmission-sig": "sig", "paypal-transmission-time": "2026-10-24T12:00:00Z"}
    # No PayPal headers: refused without asking PayPal.
    assert client.post(path, content=event).status_code == 400
    assert not fake.find("POST", "verify-webhook-signature")
    fake.on("POST", r"/v1/notifications/verify-webhook-signature$", {"verification_status": "FAILURE"})
    assert client.post(path, content=event, headers=headers).status_code == 400
    assert invoice_state(invoice["id"])["amount_paid"] == 0
    fake.on("POST", r"/v1/notifications/verify-webhook-signature$", {"verification_status": "SUCCESS"})
    assert client.post(path, content=event, headers=headers).status_code == 200
    verify_call = fake.find("POST", "verify-webhook-signature")[-1]["json"]
    assert verify_call["webhook_id"] == "WH-1" and verify_call["transmission_id"] == "t-1" and verify_call["webhook_event"]["id"] == "WH-EV-1"
    state = invoice_state(invoice["id"])
    assert state["status"] == "paid" and state["amount_paid"] == Decimal("1000.00")  # the EXTERNAL entry isn't PayPal's money

    refunded = {**paid, "status": "PARTIALLY_REFUNDED", "refunds": {"refund_amount": {"currency_code": "USD", "value": "250.00"}, "transactions": [
        {"refund_id": "RF-1", "type": "PAYPAL", "refund_date": "2026-10-25", "amount": {"currency_code": "USD", "value": "250.00"}}]}}
    fake.on("GET", rf"/v2/invoicing/invoices/{pp_id}$", refunded)
    for _ in range(2):
        assert client.post(path, content=event, headers=headers).status_code == 200
    state = invoice_state(invoice["id"])
    assert state["amount_paid"] == Decimal("750.00") and state["status"] == "sent"


# ---------------------------------------------------------------------------
# Chargebee
# ---------------------------------------------------------------------------


def test_chargebee_events_are_reread_and_raise_tasks(client, fake):
    ws = signup(client, "Chargebee Co")
    h = ws["headers"]
    fake.on("GET", r"acme-test\.chargebee\.com/api/v2/customers$", {"list": []})
    fake.on("GET", r"acme-test\.chargebee\.com/api/v2/subscriptions$", {"list": []})
    client.put(f"{API}/integration-hub/chargebee/toggle", json={"enabled": True}, headers=h)
    bad = client.put(f"{API}/integration-hub/chargebee/credentials", json={"values": {"site": "acme test!", "api_key": "live_" + "k" * 30}}, headers=h)
    assert bad.status_code == 422
    saved = client.put(f"{API}/integration-hub/chargebee/credentials", json={"values": {"site": "https://acme-test.chargebee.com", "api_key": "test_" + "k" * 30}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert saved.json()["connection"]["config"]["site"] == "acme-test"
    basic = fake.find("GET", "acme-test.chargebee.com")[0]["auth"]
    assert basic == ("test_" + "k" * 30, "")

    invoice = make_invoice(ws, company="Initech")
    path = webhook_path(client, ws, "chargebee")
    now = int(time.time())
    subscription = {"id": "sub_1", "customer_id": "cus_1", "status": "cancelled", "currency_code": "USD", "mrr": 50000, "billing_period": 1,
                    "billing_period_unit": "month", "subscription_items": [{"item_price_id": "pro-monthly", "item_type": "plan", "amount": 50000}],
                    "created_at": now - 86400 * 90, "updated_at": now, "cancelled_at": now, "cancel_reason_code": "Too expensive"}
    customer = {"id": "cus_1", "company": invoice["account_name"], "email": "billing@initech.example", "updated_at": now}
    fake.on("GET", r"/api/v2/events/ev_1$", {"event": {"id": "ev_1", "event_type": "subscription_cancelled", "occurred_at": now,
                                                        "content": {"subscription": subscription, "customer": customer}}})
    forged = {"id": "ev_1", "event_type": "subscription_activated", "content": {"subscription": {**subscription, "status": "active", "mrr": 1}}}
    assert client.post(path, json=forged).status_code == 200
    billing = client.get(f"{API}/payments/accounts/{invoice['account_id']}", headers=h).json()
    items = billing["chargebee"]["items"]
    assert len(items) == 1 and items[0]["status"] == "cancelled" and items[0]["amount"] == "500.00" and items[0]["data"]["plan"] == "pro-monthly"
    assert billing["chargebee"]["customer_id"] == "cus_1"

    from sqlalchemy import select

    from app.models.task import Task

    async def tasks(db):
        rows = (await db.execute(select(Task).where(Task.account_id == uuid.UUID(invoice["account_id"])))).scalars().all()
        return [(t.title, t.assigned_to_user_id) for t in rows]

    found = run_service(tasks)
    assert len(found) == 1 and found[0][0].startswith("Subscription cancelled") and str(found[0][1]) == ws["user_id"]
    client.post(path, json=forged)  # the same event again: no second task
    assert len(run_service(tasks)) == 1

    # An id Chargebee doesn't know is ignored; a body without one is refused.
    fake.on("GET", r"/api/v2/events/ev_fake$", (404, {"message": "Sorry, we couldn't find that resource", "api_error_code": "resource_not_found"}))
    assert client.post(path, json={"id": "ev_fake", "content": {"subscription": {**subscription, "id": "sub_evil"}}}).json().get("ignored")
    assert client.post(path, json={"event_type": "subscription_created"}).status_code == 400

    # Background sync pages through changes with Chargebee's offset.
    fake.on("GET", r"acme-test\.chargebee\.com/api/v2/subscriptions$",
            {"list": [{"subscription": {**subscription, "id": "sub_2", "status": "active", "mrr": 12000, "updated_at": now + 5}, "customer": customer}], "next_offset": "[\"1\",\"2\"]"})
    synced = client.post(f"{API}/payments/chargebee/sync", headers=h).json()
    assert synced["handled"] == 1 and synced["more"] is True
    billing = client.get(f"{API}/payments/accounts/{invoice['account_id']}", headers=h).json()
    assert billing["chargebee"]["mrr"] == [{"currency": "USD", "amount": "120.00"}]
    assert client.get(f"{API}/integration-hub/chargebee", headers=h).json()["extra"]["subscriptions"]["counts"] == {"cancelled": 1, "active": 1}


def test_settings_are_checked(client):
    ws = signup(client, "Settings Co")
    h = ws["headers"]
    client.put(f"{API}/integration-hub/chargebee/toggle", json={"enabled": True}, headers=h)
    assert client.put(f"{API}/integration-hub/chargebee/settings", json={"config": {"renewal_days": 500}}, headers=h).status_code == 422
    ok = client.put(f"{API}/integration-hub/chargebee/settings", json={"config": {"renewal_days": "45", "task_on_renewal": True, "notify_owner": False}}, headers=h)
    assert ok.status_code == 200 and ok.json()["workspace_config"]["renewal_days"] == 45
    assert client.put(f"{API}/integration-hub/stripe/settings", json={"config": {"link_message": "x" * 600}}, headers=h).status_code == 422


def test_stripe_signature_helper_rejects_odd_headers():
    from app.integrations.providers import _pay_stripe

    body = b"{}"
    good = stripe_sign("whsec_1", body)
    assert _pay_stripe.valid_signature("whsec_1", body, good)
    assert not _pay_stripe.valid_signature("whsec_1", body, good.replace("t=", "t=²"))
    assert not _pay_stripe.valid_signature("whsec_1", body, "t=1,v1=ünïcode")
    assert not _pay_stripe.valid_signature(None, body, good)


def test_missed_webhooks_are_caught_up_and_links_can_be_emailed(client, fake, monkeypatch):
    from app.integrations.providers import payments
    from app.services import mailer

    ws = signup(client, "Catch-up Co")
    h = ws["headers"]
    fake.on("GET", r"/v1/account$", {"id": "acct_3"})
    fake.on("GET", r"/v1/webhook_endpoints$", {"data": []})
    fake.on("POST", r"/v1/webhook_endpoints$", {"id": "we_3", "secret": "whsec_3"})
    client.put(f"{API}/integration-hub/stripe/toggle", json={"enabled": True}, headers=h)
    client.put(f"{API}/integration-hub/stripe/credentials", json={"values": {"secret_key": "sk_test_" + "c" * 24}}, headers=h)
    invoice = make_invoice(ws, total="250.00")
    fake.on("POST", r"/v1/prices$", {"id": "price_3"})
    fake.on("POST", r"/v1/payment_links$", {"id": "plink_3", "url": "https://buy.stripe.com/test_3"})
    client.post(f"{API}/payments/invoices/{invoice['id']}/links", json={"provider": "stripe", "message": "Thanks for your business"}, headers=h)
    assert form(fake.find("POST", "/v1/payment_links")[-1])["after_completion[hosted_confirmation][custom_message]"] == "Thanks for your business"

    # Emailing the link goes out from the sender's mailbox and lands on the timeline.
    sent: list[dict] = []

    async def fake_config(db, org, user_id):
        return object()

    async def fake_send(config, **kwargs):
        sent.append(kwargs)
        return "smtp"

    monkeypatch.setattr(mailer, "sender_config", fake_config)
    monkeypatch.setattr(mailer, "send", fake_send)
    emailed = client.post(f"{API}/payments/invoices/{invoice['id']}/email", json={"provider": "stripe"}, headers=h)
    assert emailed.status_code == 200, emailed.text
    assert sent[0]["to"].startswith("gina-") and "https://buy.stripe.com/test_3" in sent[0]["text"] and "USD 250.00" in sent[0]["html_body"]
    assert emailed.json()["links"][0]["emailed_at"]
    assert client.post(f"{API}/payments/invoices/{invoice['id']}/email", json={"provider": "paypal"}, headers=h).status_code == 409

    # The checkout completed, but the webhook never arrived: the scheduler finds it.
    from sqlalchemy import update

    from app.models.integration_hub import IntegrationRecordLink

    async def age(db):
        await db.execute(update(IntegrationRecordLink).where(IntegrationRecordLink.external_id == "plink_3")
                         .values(created_at=IntegrationRecordLink.created_at - timedelta(minutes=10), last_synced_at=None))

    run_service(age)
    fake.on("GET", r"/v1/checkout/sessions$", {"data": [{"id": "cs_3", "payment_status": "paid", "payment_intent": "pi_3"}]})
    fake.on("GET", r"/v1/payment_intents/pi_3$", {"id": "pi_3", "status": "succeeded", "amount_received": 25000, "currency": "usd", "livemode": False,
                                                  "created": int(time.time()), "metadata": {"aiva_organization_id": ws["org"], "aiva_invoice_id": invoice["id"]}})
    assert run_service(payments.run_due) >= 1
    assert invoice_state(invoice["id"])["status"] == "paid"
    session_calls = len(fake.find("GET", "/v1/checkout/sessions"))
    run_service(age)
    run_service(payments.run_due)  # the link is closed now: no more checks
    assert len(fake.find("GET", "/v1/checkout/sessions")) == session_calls
    listing = client.get(f"{API}/payments/invoices/{invoice['id']}", headers=h).json()
    assert listing["links"][0]["state"] == "paid" and listing["payments"][0]["external_id"] == "pi_3"
    checked = client.post(f"{API}/payments/invoices/{invoice['id']}/check", headers=h)
    assert checked.status_code == 200 and checked.json()["message"] == "Nothing new."


def test_two_payments_at_the_same_moment_both_count(client):
    """Webhooks for two different payments on one invoice can arrive together."""
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.integrations.providers import _pay_common as pay
    from app.models.editions import SalesInvoice
    from app.models.integration_hub import IntegrationConnection

    ws = signup(client, "Race Co")
    invoice = make_invoice(ws, total="900.00")
    org = uuid.UUID(ws["org"])

    async def setup(db):
        db.add(IntegrationConnection(id=uuid.uuid4(), organization_id=org, provider="stripe", status="connected", account_label="Stripe"))

    run_service(setup)

    async def pay_once(external_id: str, amount: str, delay: float):
        async with AsyncSessionLocal() as db:
            connection = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == org, IntegrationConnection.provider == "stripe"))).scalar_one()
            row = await db.get(SalesInvoice, uuid.UUID(invoice["id"]))
            await asyncio.sleep(delay)
            await pay.apply_payment(db, connection, row, external_id=external_id, amount=Decimal(amount), currency="USD")
            await asyncio.sleep(0.2)  # hold the row lock a moment
            await db.commit()

    async def both():
        await asyncio.gather(pay_once("pi_a", "400.00", 0), pay_once("pi_b", "500.00", 0.05))

    asyncio.run(both())
    state = invoice_state(invoice["id"])
    assert state["amount_paid"] == Decimal("900.00") and state["status"] == "paid"


def test_chargebee_matches_accounts_by_exact_host_only():
    """"ann@x.co" must not attach to the account whose website is box.com."""
    import asyncio

    from app.db.session import AsyncSessionLocal
    from app.integrations.providers import _pay_chargebee
    from app.models.account import Account
    from app.models.integration_hub import IntegrationConnection
    from app.models.organization import Organization

    async def main():
        async with AsyncSessionLocal() as db:
            db.sync_session.info["automation_disabled"] = True
            org = Organization(id=uuid.uuid4(), name="Host Co", slug=f"host-{uuid.uuid4().hex[:8]}")
            db.add(org)
            await db.flush()
            box = Account(id=uuid.uuid4(), organization_id=org.id, name="Box", website="https://www.box.com/pricing")
            acme = Account(id=uuid.uuid4(), organization_id=org.id, name="Acme", domain="www.acme.io")
            connection = IntegrationConnection(id=uuid.uuid4(), organization_id=org.id, provider="chargebee", status="connected", config={})
            db.add_all([box, acme, connection])
            await db.flush()
            assert await _pay_chargebee.match_account(db, connection, {"id": "c1", "email": "ann@x.co"}) is None
            assert await _pay_chargebee.match_account(db, connection, {"id": "c2", "email": "ann@box.com"}) == box.id
            assert await _pay_chargebee.match_account(db, connection, {"id": "c3", "email": "bo@acme.io"}) == acme.id
            await db.rollback()

    asyncio.run(main())
