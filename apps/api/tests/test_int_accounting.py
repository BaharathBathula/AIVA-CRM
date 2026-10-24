"""Accounting integrations: QuickBooks Online, Xero, FreshBooks and Sage Accounting.

Provider APIs are never called: ``_acct_http.TRANSPORT`` is an httpx
MockTransport that answers like each provider does, and the OAuth token
exchange and identity lookups are replaced. Webhooks are signed exactly as the
providers sign them, and wrongly signed ones must be refused.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Accounting#2026"
REALM = "9130350000000001"
QBO = f"https://quickbooks.api.intuit.com/v3/company/{REALM}"
VERIFIER = "intuit-verifier-token-xyz"
XERO_KEY = "xero-webhook-key-abc"
TENANT = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Ledger Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@acct.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Lee Owner", "email": email, "password": PASSWORD, "company_name": company})
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


def run_service(fn):
    from app.db.session import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            result = await fn(db)
            await db.commit()
            return result

    return asyncio.run(go())


def run_due(org: str):
    """The scheduler's run, for one workspace (the test database has many)."""
    from app.integrations.providers import accounting

    return run_service(lambda db: accounting.run_due(db, uuid.UUID(org)))


def local(url: str) -> str:
    return urlparse(url).path.replace("/api/aiva/", "/api/v1/", 1)


class FakeQuickBooks:
    """A tiny QuickBooks Online: customers, invoices, payments, change feed, throttling."""

    def __init__(self) -> None:
        self.customers: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.creates = 0
        self.fail_after_create = 0
        self.throttle = 0
        self.changed: list[str] = []
        self.next_id = 100

    def _id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def add_payment(self, invoice_id: str, amount: str) -> str:
        pid = self._id()
        self.payments[pid] = {"Id": pid, "TotalAmt": float(amount), "TxnDate": date.today().isoformat(), "PaymentRefNum": f"CHK{pid}",
                              "Line": [{"Amount": float(amount), "LinkedTxn": [{"TxnId": invoice_id, "TxnType": "Invoice"}]}]}
        invoice = self.invoices[invoice_id]
        invoice["Balance"] = round(invoice["Balance"] - float(amount), 2)
        invoice.setdefault("LinkedTxn", []).append({"TxnId": pid, "TxnType": "Payment"})
        invoice["SyncToken"] = str(int(invoice["SyncToken"]) + 1)
        self.changed.append(invoice_id)
        return pid

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        self.calls.append((request.method, path))
        if self.throttle:
            self.throttle -= 1
            return httpx.Response(429, headers={"Retry-After": "120"}, json={"Fault": {"Error": [{"Message": "ThrottleExceeded"}]}})
        assert request.headers["Authorization"].startswith("Bearer ")
        params = dict(request.url.params)
        if path.endswith(f"/companyinfo/{REALM}"):
            return httpx.Response(200, json={"CompanyInfo": {"CompanyName": "Acme Books", "Country": "US"}})
        if path.endswith("/preferences"):
            return httpx.Response(200, json={"Preferences": {"CurrencyPrefs": {"HomeCurrency": {"value": "USD"}, "MultiCurrencyEnabled": False},
                                                             "SalesFormsPrefs": {"CustomTxnNumbers": True}, "TaxPrefs": {"UsingSalesTax": True}}})
        if path.endswith("/query"):
            statement = params["query"]
            if "from Customer" in statement:
                found = []
                email = re.search(r"PrimaryEmailAddr = '([^']*)'", statement)
                name = re.search(r"DisplayName = '((?:[^'\\]|\\.)*)'", statement)
                for row in self.customers.values():
                    if email and (row.get("PrimaryEmailAddr") or {}).get("Address") == email.group(1):
                        found.append(row)
                    if name and row["DisplayName"] == name.group(1).replace("\\'", "'"):
                        found.append(row)
                return httpx.Response(200, json={"QueryResponse": {"Customer": found}})
            if "from Item" in statement:
                return httpx.Response(200, json={"QueryResponse": {"Item": [{"Id": "1", "Name": "Services", "Type": "Service"}, {"Id": "7", "Name": "Hours", "Type": "Service", "Sku": "HRS"}]}})
            if "from TaxCode" in statement:
                return httpx.Response(200, json={"QueryResponse": {"TaxCode": [{"Id": "TAX", "Name": "TAX", "Taxable": True}, {"Id": "NON", "Name": "NON"}]}})
            if "from Term" in statement:
                return httpx.Response(200, json={"QueryResponse": {"Term": [{"Id": "3", "Name": "Net 30"}]}})
            if "from Invoice" in statement:
                return httpx.Response(200, json={"QueryResponse": {"Invoice": list(self.invoices.values())}})
            return httpx.Response(200, json={"QueryResponse": {}})
        if path.endswith("/customer") and request.method == "POST":
            body = json.loads(request.content)
            cid = self._id()
            self.customers[cid] = {"Id": cid, **body, "Balance": 0}
            return httpx.Response(200, json={"Customer": self.customers[cid]})
        match = re.search(r"/customer/(\w+)$", path)
        if match:
            row = self.customers.get(match.group(1))
            if row is None:
                return httpx.Response(400, json={"Fault": {"Error": [{"Message": "Object Not Found"}]}})
            balance = sum(i["Balance"] for i in self.invoices.values() if i["CustomerRef"]["value"] == row["Id"])
            return httpx.Response(200, json={"Customer": {**row, "Balance": balance}})
        if path.endswith("/invoice") and request.method == "POST":
            body = json.loads(request.content)
            if params.get("operation") == "void":
                row = self.invoices[body["Id"]]
                row.update(TotalAmt=0, Balance=0, PrivateNote="Voided", SyncToken=str(int(row["SyncToken"]) + 1))
                return httpx.Response(200, json={"Invoice": row})
            request_id = params.get("requestid")
            if request_id in self.requests:
                return httpx.Response(200, json={"Invoice": self.requests[request_id]})
            total = round(sum(line["Amount"] for line in body["Line"]), 2)
            if body.get("Id"):
                row = self.invoices[body["Id"]]
                assert body["SyncToken"] == row["SyncToken"], "stale SyncToken"
                paid = row["TotalAmt"] - row["Balance"]
                row.update({k: v for k, v in body.items() if k not in {"sparse"}}, TotalAmt=total, Balance=round(total - paid, 2),
                           SyncToken=str(int(row["SyncToken"]) + 1))
            else:
                self.creates += 1
                iid = self._id()
                row = {**body, "Id": iid, "SyncToken": "0", "DocNumber": body.get("DocNumber") or f"10{iid}", "TotalAmt": total, "Balance": total, "LinkedTxn": []}
                self.invoices[iid] = row
            self.requests[request_id] = row
            if self.fail_after_create:
                self.fail_after_create -= 1
                return httpx.Response(500, json={"Fault": {"Error": [{"Message": "Something broke"}]}})
            return httpx.Response(200, json={"Invoice": row})
        match = re.search(r"/invoice/(\w+)$", path)
        if match:
            row = self.invoices.get(match.group(1))
            if row is None:
                return httpx.Response(400, json={"Fault": {"Error": [{"Message": "Object Not Found"}]}})
            return httpx.Response(200, json={"Invoice": row})
        match = re.search(r"/payment/(\w+)$", path)
        if match:
            return httpx.Response(200, json={"Payment": self.payments[match.group(1)]})
        if path.endswith("/cdc"):
            invoices = [self.invoices[i] for i in dict.fromkeys(self.changed) if i in self.invoices]
            self.changed = []
            return httpx.Response(200, json={"CDCResponse": [{"QueryResponse": [{"Invoice": invoices}, {"Payment": []}]}]})
        if "tokens/revoke" in url:
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected QuickBooks call {request.method} {url}")


@pytest.fixture
def qbo(monkeypatch):
    from app.integrations.providers import _acct_http

    fake = FakeQuickBooks()
    monkeypatch.setattr(_acct_http, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def connect_quickbooks(client, ws, monkeypatch) -> None:
    from app.integrations import core, identity

    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/quickbooks/toggle", json={"enabled": True}, headers=h).status_code == 200
    assert client.put(f"{API}/integration-hub/apps/intuit", json={"client_id": "intuit-client", "client_secret": "intuit-secret"}, headers=h).status_code == 200
    started = client.post(f"{API}/integration-hub/quickbooks/connect", json={}, headers=h)
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorize_url"]).query)
    assert query["scope"] == ["com.intuit.quickbooks.accounting"] and "code_challenge" not in query
    seen: dict[str, Any] = {}

    async def fake_token(family, app_creds, data):
        return {"access_token": "qbo-access", "refresh_token": "qbo-refresh", "expires_in": 3600}

    real_identify = identity.identify

    async def fake_identify(family, key, tokens, app_creds):
        seen["callback"] = tokens.get("_callback")
        assert family == "intuit"
        return {
            "label": "Acme Books", "external_id": tokens["_callback"]["realmId"],
            "config": {"realm_id": tokens["_callback"]["realmId"], "environment": "production", "api_base": "https://quickbooks.api.intuit.com",
                       "company_name": "Acme Books"},
        }

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": query["state"][0], "code": "c", "realmId": REALM},
                      headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert back.status_code == 302 and "connected=quickbooks" in back.headers["location"], back.headers["location"]
    assert seen["callback"] == {"realmId": REALM}
    monkeypatch.setattr(identity, "identify", real_identify)


def make_invoice(client, ws, *, status: str = "sent", total: str = "100.00", lines: list[dict] | None = None, email: str | None = None,
                 account_name: str | None = None) -> str:
    """An account, its contact and an invoice (written directly: invoices normally come from quotes)."""

    from app.models.editions import SalesInvoice

    h = ws["headers"]
    account = client.post(f"{API}/accounts", json={"name": account_name or f"Northwind {uuid.uuid4().hex[:6]}", "billing_city": "Austin"}, headers=h)
    assert account.status_code == 201, account.text
    account_id = account.json()["id"]
    contact = client.post(f"{API}/contacts", json={"first_name": "Nora", "last_name": "Buyer", "email": email or f"nora-{uuid.uuid4().hex[:6]}@nw.example",
                                                  "account_id": account_id}, headers=h)
    assert contact.status_code == 201, contact.text
    invoice_id = uuid.uuid4()
    amount = Decimal(total)

    async def add(db):
        from sqlalchemy import func, select

        number = (await db.execute(select(func.coalesce(func.max(SalesInvoice.number), 1000)).where(SalesInvoice.organization_id == uuid.UUID(ws["org"])))).scalar_one() + 1
        db.add(SalesInvoice(
            id=invoice_id, organization_id=uuid.UUID(ws["org"]), number=number, account_id=uuid.UUID(account_id), contact_id=uuid.UUID(contact.json()["id"]),
            owner_user_id=uuid.UUID(ws["user_id"]), status=status, issue_date=date.today(), due_date=date.today() + timedelta(days=30), currency="USD",
            subtotal=amount, discount_total=Decimal("0"), tax_total=Decimal("0"), total=amount, amount_paid=Decimal("0"), terms="Net 30",
            line_items=lines or [{"name": "Consulting", "description": "October", "quantity": 1, "unit_price": float(amount), "discount_percent": 0,
                                  "tax_percent": 0, "line_total": float(amount)}],
        ))

    run_service(add)
    return str(invoice_id)


def invoice_row(invoice_id: str) -> Any:
    from app.models.editions import SalesInvoice

    async def get(db):
        return await db.get(SalesInvoice, uuid.UUID(invoice_id))

    return run_service(get)


def set_invoice(invoice_id: str, **values: Any) -> None:
    from app.models.editions import SalesInvoice

    async def go(db):
        row = await db.get(SalesInvoice, uuid.UUID(invoice_id))
        for k, v in values.items():
            setattr(row, k, v)

    run_service(go)


def connection_state(org: str, key: str) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.integration_hub import IntegrationConnection

    async def go(db):
        row = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == uuid.UUID(org),
                                                                    IntegrationConnection.provider == key))).scalar_one()
        return dict((row.config or {}).get("sync") or {})

    return run_service(go)


def add_connection(org: str, key: str, config: dict[str, Any], secrets: dict[str, str] | None = None) -> str:
    """A connected account without going through the sign-in (for the other providers)."""

    from app.core.crypto import encrypt
    from app.integrations import core
    from app.models.integration_hub import IntegrationConnection

    connection_id = uuid.uuid4()

    async def go(db):
        row = IntegrationConnection(
            id=connection_id, organization_id=uuid.UUID(org), provider=key, status="connected", account_label="Test company",
            access_token=encrypt("token-123"), refresh_token=encrypt("refresh-123"), token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            config=config, webhook_token=core.new_webhook_token(),
        )
        for name, value in (secrets or {}).items():
            core.set_secret(row, name, value)
        db.add(row)

    run_service(go)
    return str(connection_id)


# ---------------------------------------------------------------------------
# QuickBooks
# ---------------------------------------------------------------------------


def test_one_accounting_system_at_a_time_and_quickbooks_company(client, qbo, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/xero/toggle", json={"enabled": True}, headers=h).status_code == 200
    connect_quickbooks(client, ws, monkeypatch)
    xero = client.get(f"{API}/integration-hub/xero", headers=h).json()
    assert xero["switched_on"] is False
    detail = client.get(f"{API}/integration-hub/quickbooks", headers=h).json()
    assert detail["status"] == "connected"
    extra = detail["extra"]
    assert extra["company"]["name"] == "Acme Books" and extra["company"]["currency"] == "USD"
    assert extra["app_webhook_url"].endswith("/api/aiva/public/integrations/quickbooks/webhook/app")
    assert extra["settings"]["sync_from"] == date.today().isoformat()
    status = client.get(f"{API}/accounting/status", headers=h).json()
    assert status["active"]["key"] == "quickbooks" and status["active"]["connected"]
    options = client.get(f"{API}/accounting/quickbooks/options", headers=h).json()
    assert {i["id"] for i in options["items"]} == {"1", "7"} and options["tax_codes"][0]["id"] == "TAX"
    assert "qbo-access" not in json.dumps(detail)


def test_invoice_is_created_once_and_payments_mark_it_paid(client, qbo, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect_quickbooks(client, ws, monkeypatch)
    missing = make_invoice(client, ws)
    refused = client.post(f"{API}/accounting/invoices/{missing}/push", headers=h)
    assert refused.status_code == 409 and "default QuickBooks product" in refused.json()["detail"]
    assert client.put(f"{API}/integration-hub/quickbooks/settings", json={"config": {"default_item": "1", "tax_code": "TAX", "tax_code_exempt": "NON"}},
                      headers=h).status_code == 200

    invoice_id = make_invoice(client, ws, email="pay@nw.example")
    # The first try reaches QuickBooks but the answer is lost: the retry must not make a second invoice.
    qbo.fail_after_create = 1
    first = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert first.status_code in (400, 502)
    pending = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()
    assert pending["link"]["status"] == "pending" and pending["link"]["last_error"]
    again = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert again.status_code == 200, again.text
    assert qbo.creates == 1  # the retry got the invoice the lost attempt made, not a second one
    body = again.json()
    link = body["link"]
    assert link["status"] == "linked" and link["external_url"].startswith("https://app.qbo.intuit.com/app/invoice?txnId=")
    remote = qbo.invoices[link["external_id"]]
    assert remote["PrivateNote"].startswith("AIVA invoice INV-") and remote["Line"][0]["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "NON"}
    assert remote["BillEmail"] == {"Address": "pay@nw.example"}
    customer = qbo.customers[remote["CustomerRef"]["value"]]
    assert customer["PrimaryEmailAddr"]["Address"] == "pay@nw.example"

    # Unchanged: nothing is sent again.
    creates = qbo.creates
    posts = len([c for c in qbo.calls if c == ("POST", f"/v3/company/{REALM}/invoice")])
    assert client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h).status_code == 200
    assert qbo.creates == creates

    # Payments come back as differences: 40, then the remaining 60.
    qbo.add_payment(link["external_id"], "40.00")
    partly = client.post(f"{API}/accounting/invoices/{invoice_id}/refresh", headers=h).json()
    assert partly["link"]["balance"] == "60.00" and len(partly["payments"]) == 1
    row = invoice_row(invoice_id)
    assert row.amount_paid == Decimal("40.00") and row.status == "sent"
    qbo.add_payment(link["external_id"], "60.00")
    run_service(lambda db: _force_changes(db, ws["org"]))
    run_due(ws["org"])
    row = invoice_row(invoice_id)
    assert row.amount_paid == Decimal("100.00") and row.status == "paid" and row.paid_at is not None
    status = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()
    assert {p["amount"] for p in status["payments"]} == {"40.00", "60.00"}
    notes = client.get(f"{API}/notifications", headers=h).json()
    items = notes["items"] if isinstance(notes, dict) else notes
    assert any("is paid" in n["title"] for n in items)
    logs = client.get(f"{API}/integration-hub/quickbooks/logs", headers=h).json()["items"]
    assert {"invoice_sent", "payment_synced", "customer_created"} <= {entry["action"] for entry in logs}
    assert posts >= 1

    # Paid in QuickBooks: a manual send is refused and the link stays healthy.
    refused = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert refused.status_code == 409 and "paid" in refused.json()["detail"]
    assert client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()["link"]["status"] == "linked"

    # Account page: customer link with balance and recent invoices.
    account = client.post(f"{API}/accounting/accounts/{row.account_id}/refresh", headers=h).json()
    assert account["link"]["name"] and account["link"]["open_balance"] == "0.00" and account["link"]["invoices"]
    assert {p["amount"] for p in account["payments"]} == {"40.00", "60.00"}


async def _force_changes(db, org: str) -> None:
    from sqlalchemy import select

    from app.integrations.providers import accounting
    from app.models.integration_hub import IntegrationConnection

    row = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == uuid.UUID(org),
                                                                IntegrationConnection.provider.in_(accounting.KEYS)))).scalars().first()
    await accounting.patch_state(db, row, check_now=True, last_run_at=None)


def test_automatic_sync_voids_and_backs_off(client, qbo, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect_quickbooks(client, ws, monkeypatch)
    invoice_id = make_invoice(client, ws)
    # No default item yet: the automatic run records the problem and waits before trying again.
    run_due(ws["org"])
    status = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()
    assert status["link"]["status"] == "pending" and status["link"]["retry_at"]
    calls = len(qbo.calls)
    run_service(lambda db: _force_changes(db, ws["org"]))
    run_due(ws["org"])
    assert not [c for c in qbo.calls[calls:] if c[1].endswith("/invoice")]  # backing off
    client.put(f"{API}/integration-hub/quickbooks/settings", json={"config": {"default_item": "1"}}, headers=h)
    assert client.post(f"{API}/accounting/quickbooks/retry", headers=h).json()["count"] == 1
    run_service(lambda db: _force_changes(db, ws["org"]))
    run_due(ws["org"])
    status = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()
    assert status["link"]["status"] == "linked", status
    external = status["link"]["external_id"]

    set_invoice(invoice_id, status="void")
    run_service(lambda db: _force_changes(db, ws["org"]))
    run_due(ws["org"])
    assert qbo.invoices[external]["PrivateNote"] == "Voided"
    assert client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()["link"]["remote_status"] == "void"


def test_rate_limit_pauses_the_company(client, qbo, monkeypatch):
    ws = signup(client)
    connect_quickbooks(client, ws, monkeypatch)
    run_service(lambda db: _force_changes(db, ws["org"]))
    qbo.throttle = 1
    run_due(ws["org"])
    state = connection_state(ws["org"], "quickbooks")
    paused = datetime.fromisoformat(state["paused_until"])
    assert timedelta(seconds=100) < paused - datetime.now(timezone.utc) <= timedelta(seconds=121)
    calls = len(qbo.calls)
    run_due(ws["org"])
    assert len(qbo.calls) == calls  # still paused: no calls at all
    logs = client.get(f"{API}/integration-hub/quickbooks/logs", headers=ws["headers"]).json()["items"]
    assert any(entry["action"] == "rate_limited" for entry in logs)


def test_quickbooks_webhook_signature_and_nudge(client, qbo, monkeypatch):
    monkeypatch.setenv("QUICKBOOKS_WEBHOOK_VERIFIER_TOKEN", VERIFIER)
    ws = signup(client)
    h = ws["headers"]
    connect_quickbooks(client, ws, monkeypatch)
    client.put(f"{API}/integration-hub/quickbooks/settings", json={"config": {"default_item": "1"}}, headers=h)
    invoice_id = make_invoice(client, ws)
    external = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h).json()["link"]["external_id"]
    qbo.add_payment(external, "100.00")
    qbo.changed = []  # only the webhook should bring it in
    url = local(client.get(f"{API}/integration-hub/quickbooks", headers=h).json()["extra"]["app_webhook_url"])
    payload = json.dumps({"eventNotifications": [{"realmId": REALM, "dataChangeEvent": {"entities": [
        {"name": "Invoice", "id": external, "operation": "Update", "lastUpdated": "2026-10-24T10:00:00-0700"}]}}]}).encode()
    bad = client.post(url, content=payload, headers={"intuit-signature": base64.b64encode(b"nope").decode(), "Content-Type": "application/json"})
    assert bad.status_code == 401
    good_sig = base64.b64encode(hmac.new(VERIFIER.encode(), payload, hashlib.sha256).digest()).decode()
    good = client.post(url, content=payload, headers={"intuit-signature": good_sig, "Content-Type": "application/json"})
    assert good.status_code == 200
    assert f"Invoice:{external}" in connection_state(ws["org"], "quickbooks").get("nudges", [])
    run_service(lambda db: _clear_last_run(db, ws["org"]))
    run_due(ws["org"])
    assert invoice_row(invoice_id).status == "paid"
    assert not connection_state(ws["org"], "quickbooks").get("nudges")
    # A workspace whose switch is off ignores notifications.
    client.put(f"{API}/integration-hub/quickbooks/toggle", json={"enabled": False}, headers=h)
    assert client.post(url, content=payload, headers={"intuit-signature": good_sig, "Content-Type": "application/json"}).status_code == 200
    assert not connection_state(ws["org"], "quickbooks").get("nudges")


async def _clear_last_run(db, org: str) -> None:
    from sqlalchemy import select

    from app.integrations.providers import accounting
    from app.models.integration_hub import IntegrationConnection

    row = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == uuid.UUID(org),
                                                                IntegrationConnection.provider.in_(accounting.KEYS)))).scalars().first()
    await accounting.patch_state(db, row, last_run_at=None, cursor=datetime.now(timezone.utc).isoformat())


def test_quickbooks_cloudevents_are_understood():
    from app.integrations.providers import _acct_quickbooks

    events = _acct_quickbooks.parse_webhook([
        {"specversion": "1.0", "id": "e1", "type": "qbo.payment.created.v1", "intuitaccountid": REALM, "intuitentityid": "55", "time": "2026-10-24T10:00:00Z"},
        {"specversion": "1.0", "id": "e2", "type": "qbo.invoice.updated.v1", "intuitaccountid": REALM, "intuitentityid": "56"},
    ])
    assert events == [(REALM, "Payment", "55"), (REALM, "Invoice", "56")]


# ---------------------------------------------------------------------------
# Xero
# ---------------------------------------------------------------------------


class FakeXero:
    def __init__(self) -> None:
        self.contacts: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, dict[str, Any]] = {}
        self.keys: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path, dict(request.headers)))
        if path.startswith("/api.xro/2.0/"):
            assert request.headers.get("xero-tenant-id") == TENANT
        if path == "/api.xro/2.0/Organisation":
            return httpx.Response(200, json={"Organisations": [{"Name": "Kiwi Ltd", "BaseCurrency": "NZD", "CountryCode": "NZ", "ShortCode": "!abc"}]})
        if path == "/api.xro/2.0/Contacts" and request.method == "GET":
            term = request.url.params.get("searchTerm", "").lower()
            rows = [c for c in self.contacts.values() if term in c["Name"].lower() or term == (c.get("EmailAddress") or "").lower()]
            return httpx.Response(200, json={"Contacts": rows})
        if path == "/api.xro/2.0/Contacts" and request.method == "POST":
            contact = json.loads(request.content)["Contacts"][0]
            cid = str(uuid.uuid4())
            self.contacts[cid] = {**contact, "ContactID": cid, "ContactStatus": "ACTIVE"}
            return httpx.Response(200, json={"Contacts": [self.contacts[cid]]})
        if path == "/api.xro/2.0/Invoices" and request.method == "POST":
            key = request.headers.get("idempotency-key")
            if key in self.keys:
                return httpx.Response(200, json={"Invoices": [self.keys[key]]})
            invoice = json.loads(request.content)["Invoices"][0]
            total = 0.0
            for line in invoice["LineItems"]:
                total += line["Quantity"] * line["UnitAmount"] * (1 - line.get("DiscountRate", 0) / 100)
            iid = str(uuid.uuid4())
            row = {**invoice, "InvoiceID": iid, "InvoiceNumber": f"INV-X{len(self.invoices) + 1}", "Total": round(total, 2), "AmountDue": round(total, 2),
                   "CurrencyCode": invoice["CurrencyCode"], "Payments": []}
            self.invoices[iid] = row
            self.keys[key] = row
            return httpx.Response(200, json={"Invoices": [row]})
        match = re.match(r"/api.xro/2.0/Invoices/([\w-]+)$", path)
        if match and request.method == "GET":
            return httpx.Response(200, json={"Invoices": [self.invoices[match.group(1)]]})
        if path == "/api.xro/2.0/Invoices" and request.method == "GET":
            return httpx.Response(200, json={"Invoices": []})
        raise AssertionError(f"Unexpected Xero call {request.method} {request.url}")


def test_xero_invoice_uses_idempotency_key_and_settings(client, monkeypatch):
    from app.integrations.providers import _acct_http

    fake = FakeXero()
    monkeypatch.setattr(_acct_http, "TRANSPORT", httpx.MockTransport(fake))
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/xero/toggle", json={"enabled": True}, headers=h)
    add_connection(ws["org"], "xero", {"tenant_id": TENANT, "tenant_name": "Kiwi Ltd", "tenants": [{"id": TENANT, "name": "Kiwi Ltd"}]})
    lines = [{"name": "Licences", "description": "", "quantity": 10, "unit_price": 20, "discount_percent": 10, "tax_percent": 15, "line_total": 180}]
    invoice_id = make_invoice(client, ws, total="207.00", lines=lines)
    refused = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert refused.status_code == 409 and "sales account" in refused.json()["detail"]
    client.put(f"{API}/integration-hub/xero/settings", json={"config": {"income_account": "200", "tax_code": "OUTPUT2", "tax_code_exempt": "NONE"}}, headers=h)
    pushed = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert pushed.status_code == 200, pushed.text
    invoice = next(iter(fake.invoices.values()))
    line = invoice["LineItems"][0]
    assert line["DiscountRate"] == 10.0 and line["TaxType"] == "OUTPUT2" and line["AccountCode"] == "200"
    assert invoice["Status"] == "AUTHORISED" and invoice["Reference"].startswith("INV-") and invoice["LineAmountTypes"] == "Exclusive"
    posts = [c for c in fake.calls if c[0] == "POST" and c[1] == "/api.xro/2.0/Invoices"]
    assert posts and posts[0][2].get("idempotency-key")
    link = pushed.json()["link"]
    assert "shortcode=%21abc" in link["external_url"]
    # Xero totals the tax-exclusive lines without tax here, so AIVA flags the difference.
    assert link["total_mismatch"] == {"aiva": "207.00", "provider": "180.00"}


def test_xero_webhook_intent_to_receive(client, monkeypatch):
    monkeypatch.setenv("XERO_WEBHOOK_KEY", XERO_KEY)
    body = json.dumps({"events": [], "firstEventSequence": 0, "lastEventSequence": 0, "entropy": "ABCDEF"}).encode()
    good = base64.b64encode(hmac.new(XERO_KEY.encode(), body, hashlib.sha256).digest()).decode()
    url = f"{API}/public/integrations/xero/webhook/app"
    assert client.post(url, content=body, headers={"x-xero-signature": good}).status_code == 200
    assert client.post(url, content=body, headers={"x-xero-signature": "bad"}).status_code == 401
    assert client.post(url, content=body).status_code == 401


# ---------------------------------------------------------------------------
# FreshBooks
# ---------------------------------------------------------------------------


def test_freshbooks_callback_handshake_and_signed_events(client, monkeypatch):
    from app.integrations.providers import _acct_http, accounting

    confirmed: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/events/callbacks/77"):
            body = json.loads(request.content)
            confirmed.append(body)
            ok = body["callback"]["verifier"] == "right-verifier"
            return httpx.Response(200 if ok else 422, json={"response": {"result": {"callback": {"callbackid": 77, "verified": ok}}}})
        raise AssertionError(f"Unexpected FreshBooks call {request.method} {request.url}")

    monkeypatch.setattr(_acct_http, "TRANSPORT", httpx.MockTransport(handler))
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/freshbooks/toggle", json={"enabled": True}, headers=h)
    connection_id = add_connection(ws["org"], "freshbooks", {"account_id": "ab12", "sync": {"callbacks": {"77": "payment"}}})

    async def token(db):
        from app.models.integration_hub import IntegrationConnection

        return (await db.get(IntegrationConnection, uuid.UUID(connection_id))).webhook_token

    url = f"{API}/public/integrations/freshbooks/webhook/{run_service(token)}"
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    unknown = client.post(url, content=urlencode({"name": "callback.verify", "object_id": "99", "verifier": "x"}), headers=form)
    assert unknown.status_code == 404
    wrong = client.post(url, content=urlencode({"name": "callback.verify", "object_id": "77", "verifier": "guess"}), headers=form)
    assert wrong.json()["ok"] is False
    right = client.post(url, content=urlencode({"name": "callback.verify", "object_id": "77", "verifier": "right-verifier"}), headers=form)
    assert right.json()["ok"] is True and confirmed[-1] == {"callback": {"verifier": "right-verifier"}}

    params = [("name", "payment.create"), ("object_id", "555"), ("account_id", "ab12"), ("business_id", "9"), ("identity_id", "3")]
    message = json.dumps({k: v for k, v in params})
    signature = base64.b64encode(hmac.new(b"right-verifier", message.encode(), hashlib.sha256).digest()).decode()
    assert client.post(url, content=urlencode(params), headers={**form, "X-FreshBooks-Hmac-SHA256": "bad"}).status_code == 401
    ok = client.post(url, content=urlencode(params), headers={**form, "X-FreshBooks-Hmac-SHA256": signature})
    assert ok.status_code == 200
    state = connection_state(ws["org"], "freshbooks")
    assert "Payment:555" in state["nudges"] and state["check_now"] is True
    assert accounting.KEYS  # module loaded


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_dates_money_and_signatures():
    from app.integrations.providers import _acct_freshbooks, _acct_quickbooks, _acct_xero
    from app.integrations.providers._acct_types import money, parse_date

    assert parse_date("/Date(1518685950940+0000)/") == date(2018, 2, 15)
    assert parse_date("2026-10-24T00:00:00") == date(2026, 10, 24)
    assert money("10.005", "USD") == Decimal("10.01") and money("1234.5", "JPY") == Decimal("1235")
    body = b'{"eventNotifications":[]}'
    sig = base64.b64encode(hmac.new(b"k", body, hashlib.sha256).digest()).decode()
    assert _acct_quickbooks.signature_ok("k", body, sig) and not _acct_quickbooks.signature_ok("k", body, "x" + sig[1:])
    assert not _acct_quickbooks.signature_ok("k", body, "ü")  # odd input is refused, not an error
    assert _acct_xero.signature_ok("k", body, sig) and not _acct_xero.signature_ok(None, body, sig)
    params = [("name", "invoice.update"), ("object_id", "1")]
    fb = base64.b64encode(hmac.new(b"v", json.dumps(dict(params)).encode(), hashlib.sha256).digest()).decode()
    assert _acct_freshbooks.signature_ok("v", params, fb) and not _acct_freshbooks.signature_ok("v", params, fb[:-2] + "==")


def test_members_cannot_change_accounting(client, qbo, monkeypatch):
    ws = signup(client)
    connect_quickbooks(client, ws, monkeypatch)
    email = f"member-{uuid.uuid4().hex[:8]}@acct.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Max Member", "role": "member"}, headers=ws["headers"]).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/accounting/quickbooks/options", headers=member).status_code == 403
    assert client.post(f"{API}/accounting/quickbooks/sync", headers=member).status_code == 403
    assert client.put(f"{API}/accounting/quickbooks/webhook-key", json={"value": "x"}, headers=member).status_code == 403
    detail = client.get(f"{API}/integration-hub/quickbooks", headers=member).json()
    assert "settings" not in detail["extra"] and detail["extra"].get("app_webhook_url") is None
    assert client.get(f"{API}/accounting/status", headers=member).status_code == 200


# ---------------------------------------------------------------------------
# Sage and FreshBooks invoices
# ---------------------------------------------------------------------------


def test_sage_recovers_an_interrupted_invoice_and_reads_allocations(client, monkeypatch):
    from app.integrations.providers import _acct_http

    state: dict[str, Any] = {"invoices": {}, "fail_once": True, "posts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers.get("x-business") == "biz-1"
        if path == "/v3.1/businesses/biz-1":
            return httpx.Response(200, json={"id": "biz-1", "name": "Sage Biz"})
        if path == "/v3.1/financial_settings":
            return httpx.Response(200, json={"base_currency": {"id": "USD"}, "multi_currency_enabled": False})
        if path == "/v3.1/contacts" and request.method == "GET":
            return httpx.Response(200, json={"$items": []})
        if path == "/v3.1/contacts" and request.method == "POST":
            body = json.loads(request.content)["contact"]
            assert body["contact_type_ids"] == ["CUSTOMER"]
            return httpx.Response(201, json={"id": "c-1", "name": body["name"], "displayed_as": body["name"]})
        if path == "/v3.1/sales_invoices" and request.method == "POST":
            state["posts"] += 1
            body = json.loads(request.content)["sales_invoice"]
            line = body["invoice_lines"][0]
            assert line["ledger_account_id"] == "led-4000" and line["tax_rate_id"] == "rate-std" and line["discount_amount"] == 10.0
            row = {"id": "si-1", "invoice_number": "SI-1", "reference": body["reference"], "total_amount": "108.00", "outstanding_amount": "108.00",
                   "status": {"id": "UNPAID"}, "currency": {"id": "USD"}, "date": body["date"], "due_date": body.get("due_date")}
            state["invoices"]["si-1"] = row
            if state["fail_once"]:
                state["fail_once"] = False
                return httpx.Response(503, json=[{"$severity": "error", "$message": "Service unavailable"}])
            return httpx.Response(201, json=row)
        if path == "/v3.1/sales_invoices" and request.method == "GET":
            return httpx.Response(200, json={"$items": list(state["invoices"].values())})
        if path == "/v3.1/sales_invoices/si-1":
            row = dict(state["invoices"]["si-1"])
            if state.get("paid"):
                row.update(outstanding_amount="0.00", status={"id": "PAID"},
                           payments_allocations=[{"id": "pa-1", "date": date.today().isoformat(), "amount": "108.00", "displayed_as": "BACS"}])
            return httpx.Response(200, json=row)
        raise AssertionError(f"Unexpected Sage call {request.method} {request.url}")

    monkeypatch.setattr(_acct_http, "TRANSPORT", httpx.MockTransport(handler))
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/sage_accounting/toggle", json={"enabled": True}, headers=h)
    add_connection(ws["org"], "sage_accounting", {"business_id": "biz-1", "business_name": "Sage Biz", "businesses": [{"id": "biz-1", "name": "Sage Biz"}]})
    client.put(f"{API}/integration-hub/sage_accounting/settings", json={"config": {"income_account": "led-4000", "tax_code": "rate-std"}}, headers=h)
    lines = [{"name": "Plan", "description": "Annual", "quantity": 1, "unit_price": 100, "discount_percent": 10, "tax_percent": 20, "line_total": 90}]
    invoice_id = make_invoice(client, ws, total="108.00", lines=lines)
    assert client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h).status_code == 502
    pushed = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert pushed.status_code == 200, pushed.text
    assert state["posts"] == 1  # found by its reference, not created again
    assert pushed.json()["link"]["number"] == "SI-1"
    state["paid"] = True
    paid = client.post(f"{API}/accounting/invoices/{invoice_id}/refresh", headers=h).json()
    assert paid["link"]["remote_status"] == "paid" and paid["payments"][0]["reference"] == "BACS"
    assert invoice_row(invoice_id).status == "paid"


def test_freshbooks_invoice_and_payments(client, monkeypatch):
    from app.integrations.providers import _acct_http

    state: dict[str, Any] = {"invoice": None, "payments": []}

    def result(payload: dict[str, Any], code: int = 200) -> httpx.Response:
        return httpx.Response(code, json={"response": {"result": payload}})

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/api/v1/users/me":
            return httpx.Response(200, json={"response": {"email": "o@fb.example", "business_memberships": [{"business": {"account_id": "ab12", "id": 9, "name": "FB Biz"}}]}})
        if path == "/accounting/account/ab12/users/clients" and request.method == "GET":
            return result({"clients": []})
        if path == "/accounting/account/ab12/users/clients" and request.method == "POST":
            client_body = json.loads(request.content)["client"]
            return result({"client": {"id": 501, **client_body}})
        if path == "/accounting/account/ab12/invoices/invoices" and request.method == "POST":
            body = json.loads(request.content)["invoice"]
            assert body["customerid"] == 501 and body["lines"][0]["unit_cost"] == {"amount": "50.00", "code": "USD"} and body["due_offset_days"] == 30
            state["invoice"] = {"invoiceid": 9001, "invoice_number": "0000042", "amount": {"amount": "100.00", "code": "USD"},
                                "outstanding": {"amount": "100.00", "code": "USD"}, "payment_status": "unpaid", "v3_status": "sent", "vis_state": 0,
                                "create_date": body["create_date"], "currency_code": "USD"}
            return result({"invoice": state["invoice"]})
        if path == "/accounting/account/ab12/invoices/invoices/9001":
            return result({"invoice": state["invoice"]})
        if path == "/accounting/account/ab12/payments/payments":
            return result({"payments": state["payments"]})
        raise AssertionError(f"Unexpected FreshBooks call {request.method} {request.url}")

    monkeypatch.setattr(_acct_http, "TRANSPORT", httpx.MockTransport(handler))
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/freshbooks/toggle", json={"enabled": True}, headers=h)
    add_connection(ws["org"], "freshbooks", {"account_id": "ab12", "business_id": 9})
    lines = [{"name": "Hours", "description": "", "quantity": 2, "unit_price": 50, "discount_percent": 0, "tax_percent": 0, "line_total": 100}]
    invoice_id = make_invoice(client, ws, lines=lines)
    pushed = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["link"]["number"] == "0000042"
    state["payments"] = [{"id": 77, "amount": {"amount": "100.00", "code": "USD"}, "date": date.today().isoformat(), "type": "Credit Card", "vis_state": 0}]
    state["invoice"].update(outstanding={"amount": "0.00", "code": "USD"}, payment_status="paid")
    paid = client.post(f"{API}/accounting/invoices/{invoice_id}/refresh", headers=h).json()
    assert paid["payments"][0]["method"] == "Credit Card" and invoice_row(invoice_id).status == "paid"


def test_link_an_account_to_an_existing_customer(client, qbo, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect_quickbooks(client, ws, monkeypatch)
    qbo.customers["900"] = {"Id": "900", "DisplayName": "Existing Co", "PrimaryEmailAddr": {"Address": "ap@existing.example"}, "Balance": 0}
    found = client.get(f"{API}/accounting/quickbooks/customers", params={"q": "Exist"}, headers=h)
    assert found.status_code == 200
    account = client.post(f"{API}/accounts", json={"name": "Existing Company Inc"}, headers=h).json()
    linked = client.post(f"{API}/accounting/accounts/{account['id']}/link", json={"customer_id": "900"}, headers=h)
    assert linked.status_code == 200, linked.text
    assert linked.json()["link"]["name"] == "Existing Co" and linked.json()["link"]["linked_by"] == "manual"
    other = client.post(f"{API}/accounts", json={"name": "Someone Else"}, headers=h).json()
    clash = client.post(f"{API}/accounting/accounts/{other['id']}/link", json={"customer_id": "900"}, headers=h)
    assert clash.status_code == 409 and "already linked" in clash.json()["detail"]
    assert client.delete(f"{API}/accounting/accounts/{account['id']}/link", headers=h).json()["link"] is None


def test_payments_from_a_processor_and_the_books_are_not_counted_twice():
    """A card payment that the bookkeeper also records in the books counts once; removing a
    payment in the books only takes back what the accounting sync added."""
    import asyncio
    from datetime import date as day
    from decimal import Decimal

    from app.db.session import AsyncSessionLocal
    from app.integrations.providers import _acct_http as acct_http
    from app.integrations.providers import _pay_common as pay
    from app.integrations.providers import accounting
    from app.integrations.providers._acct_types import Ctx, Payment, RemoteInvoice
    from app.models.account import Account
    from app.models.editions import SalesInvoice
    from app.models.integration_hub import IntegrationConnection, IntegrationRecordLink
    from app.models.organization import Organization

    async def main():
        async with AsyncSessionLocal() as db:
            db.sync_session.info["automation_disabled"] = True
            org = Organization(id=uuid.uuid4(), name="Twice Co", slug=f"twice-{uuid.uuid4().hex[:8]}")
            db.add(org)
            await db.flush()
            account = Account(id=uuid.uuid4(), organization_id=org.id, name="Globex")
            db.add(account)
            await db.flush()
            invoice = SalesInvoice(id=uuid.uuid4(), organization_id=org.id, number=1001, account_id=account.id, status="sent", issue_date=day.today(),
                                   currency="USD", subtotal=Decimal("1000"), total=Decimal("1000"), amount_paid=Decimal("0"), line_items=[])
            stripe = IntegrationConnection(id=uuid.uuid4(), organization_id=org.id, provider="stripe", config={})
            books = IntegrationConnection(id=uuid.uuid4(), organization_id=org.id, provider="quickbooks", config={"realm_id": "123"})
            db.add_all([invoice, stripe, books])
            await db.flush()
            await pay.apply_payment(db, stripe, invoice, external_id="pi_123", amount=Decimal("400"), currency="USD")
            await db.flush()
            assert invoice.amount_paid == Decimal("400")

            link = IntegrationRecordLink(id=uuid.uuid4(), organization_id=org.id, provider="quickbooks", record_type=accounting.INVOICE, record_id=invoice.id,
                                         external_type="invoice", external_parent="123", external_id="77", status="linked", data={"provider_paid": "0"})
            db.add(link)
            await db.flush()
            ctx = Ctx(db=db, connection=books, settings={"notify_owner": False}, budget=acct_http.unlimited())

            def remote(*amounts: str) -> RemoteInvoice:
                paid = [Payment(id=str(i), amount=Decimal(a), date=day.today()) for i, a in enumerate(amounts, 1)]
                total_paid = sum((p.amount for p in paid), Decimal("0"))
                return RemoteInvoice(id="77", number="1001", url=None, total=Decimal("1000"), balance=Decimal("1000") - total_paid, currency="USD",
                                     status="paid" if total_paid >= 1000 else "partial", payments=paid)

            await accounting.apply_remote(ctx, link, invoice, remote("400"))  # the books record the card payment
            assert invoice.amount_paid == Decimal("400") and invoice.status == "sent"
            await accounting.apply_remote(ctx, link, invoice, remote("400", "600"))  # plus a bank transfer
            assert invoice.amount_paid == Decimal("1000") and invoice.status == "paid"
            await accounting.apply_remote(ctx, link, invoice, remote("400"))  # the bank transfer is removed in the books
            assert invoice.amount_paid == Decimal("400") and invoice.status == "sent"
            await accounting.apply_remote(ctx, link, invoice, remote())  # even if the books drop everything, the card payment stays
            assert invoice.amount_paid == Decimal("400")
            await db.rollback()

    asyncio.run(main())
