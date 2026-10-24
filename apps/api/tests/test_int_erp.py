"""Oracle NetSuite: token-based authentication, customers, contacts, invoices, payments, items, stock and sales orders.

NetSuite is never called: ``_acct_http.TRANSPORT`` is an httpx MockTransport
playing a small NetSuite account. It checks every request's OAuth 1.0a
HMAC-SHA256 signature with its own implementation (and refuses reused nonces),
answers SuiteQL by recognising AIVA's statements, and implements the REST
record service (create → 204 + Location, PATCH, external IDs, transforms).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Netsuite#2026"
ACCOUNT = "1234567_SB1"
HOST = "1234567-sb1.suitetalk.api.netsuite.com"
BASE = f"https://{HOST}/services/rest"
OFFSET = timedelta(hours=-7)  # the fake account's time zone


def creds() -> dict[str, str]:
    """Credentials built at run time (no secret-looking literals in the source)."""

    return {"account_id": "1234567-sb1", "consumer_key": "c0" * 32, "consumer_secret": "c5" * 32, "token_id": "70" * 32, "token_secret": "75" * 32}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "ERP Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@erp.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Erin Owner", "email": email, "password": PASSWORD, "company_name": company})
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


def run_due(org: str, *, poll: bool = False):
    """The scheduler's run for one workspace; ``poll`` asks it to look for changes now (as if five minutes had passed)."""
    from app.integrations.providers import accounting

    if poll:
        connection = connection_row(org)

        async def check(db):
            await accounting.nudge(db, connection, [], check_now=True)

        run_service(check)
    return run_service(lambda db: accounting.run_due(db, uuid.UUID(org)))


def local_now() -> datetime:
    return (datetime.now(timezone.utc) + OFFSET).replace(tzinfo=None, microsecond=0)


def stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def enc(value: str) -> str:
    return quote(value, safe="-._~")


def expected_signature(method: str, url: str, oauth: dict[str, str], consumer_secret: str, token_secret: str) -> str:
    """OAuth 1.0a HMAC-SHA256, written separately from the code under test."""

    parts = urlsplit(url)
    base_url = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
    params = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in oauth.items() if k not in {"oauth_signature", "realm"}]
    normalized = "&".join(f"{k}={v}" for k, v in sorted((enc(k), enc(v)) for k, v in params))
    base = "&".join([method.upper(), enc(base_url), enc(normalized)])
    key = f"{enc(consumer_secret)}&{enc(token_secret)}"
    return base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()


def ns_error(status: int, message: str, code: str = "USER_ERROR") -> httpx.Response:
    return httpx.Response(status, json={"type": "https://www.rfc-editor.org/rfc/rfc9110.html", "title": "Error", "status": status,
                                        "o:errorDetails": [{"detail": message, "o:errorCode": code}]})


class FakeNetSuite:
    """A small NetSuite account: SuiteQL, the REST record service and TBA checks."""

    def __init__(self) -> None:
        values = creds()
        self.consumer_key, self.consumer_secret = values["consumer_key"], values["consumer_secret"]
        self.token_id, self.token_secret = values["token_id"], values["token_secret"]
        self.subsidiaries = [{"id": "1", "name": "Globex Holdings", "parent": None, "iselimination": "F", "currency": "USD"}]
        self.currencies = [{"id": "1", "symbol": "USD", "isbasecurrency": "T"}, {"id": "2", "symbol": "EUR", "isbasecurrency": "F"}]
        self.customers: dict[str, dict[str, Any]] = {}
        self.contacts: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.docs: dict[str, dict[str, Any]] = {}  # payments and credit memos
        self.links: list[dict[str, Any]] = []  # previousdoc (invoice) -> nextdoc (payment / credit)
        self.items: list[dict[str, Any]] = []
        self.prices: list[dict[str, Any]] = []
        self.stock: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.statements: list[str] = []
        self.bodies: list[tuple[str, str, dict[str, Any]]] = []
        self.creates: Counter = Counter()
        self.nonces: set[str] = set()
        self.fail_after: Counter = Counter()  # record type -> creates whose answer is "lost" (500 after saving)
        self.throttle = 0
        self.next_id = 500

    # -- helpers -----------------------------------------------------------

    def new_id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def add_item(self, item_id: str, itemid: str, itemtype: str = "InvtPart", *, name: str | None = None, price: str | None = None,
                 on_hand: dict[str, str] | None = None, inactive: bool = False, modified: datetime | None = None) -> None:
        self.items.append({"id": item_id, "itemid": itemid, "displayname": name, "description": f"{itemid} from NetSuite", "itemtype": itemtype,
                           "isinactive": "T" if inactive else "F", "modified": stamp(modified or local_now())})
        if price is not None:
            self.prices.append({"item": item_id, "unitprice": price, "priceqty": "0", "currency": "USD", "pricelevel": "1"})
            self.prices.append({"item": item_id, "unitprice": str(Decimal(price) - 1), "priceqty": "10", "currency": "USD", "pricelevel": "1"})
        for location, qty in (on_hand or {}).items():
            self.stock.append({"item": item_id, "location": location, "location_name": f"Warehouse {location}", "quantityonhand": qty, "quantityavailable": qty})

    def add_payment(self, invoice_id: str, amount: str, doc: str = "CustPymt", external: str | None = None) -> str:
        pid = self.new_id()
        self.docs[pid] = {"id": pid, "type": doc, "tranid": f"PYMT{pid}", "externalid": external, "trandate": date.today().isoformat(),
                          "modified": stamp(local_now())}
        self.links.append({"previousdoc": invoice_id, "nextdoc": pid, "linktype": "Payment", "foreignamount": amount})
        invoice = self.invoices[invoice_id]
        invoice["remaining"] = str(Decimal(invoice["remaining"]) - Decimal(amount))
        invoice["status"] = "B" if Decimal(invoice["remaining"]) <= 0 else "A"
        return pid

    def remove_payment(self, payment_id: str) -> None:
        link = next(link for link in self.links if link["nextdoc"] == payment_id)
        self.links.remove(link)
        invoice = self.invoices[link["previousdoc"]]
        invoice["remaining"] = str(Decimal(invoice["remaining"]) + Decimal(link["foreignamount"]))
        invoice["status"] = "A"
        invoice["modified"] = stamp(local_now())
        del self.docs[payment_id]

    # -- authentication ------------------------------------------------------

    def check_auth(self, request: httpx.Request) -> httpx.Response | None:
        if request.url.host != HOST:
            return httpx.Response(404)
        header = request.headers.get("authorization", "")
        if not header.startswith("OAuth "):
            return ns_error(401, "Invalid login attempt.", "INVALID_LOGIN")
        fields = {k: unquote(v) for k, v in re.findall(r'(\w+)="([^"]*)"', header)}
        if fields.get("realm") != ACCOUNT or fields.get("oauth_signature_method") != "HMAC-SHA256" or fields.get("oauth_version") != "1.0":
            return ns_error(401, "Invalid login attempt.", "INVALID_LOGIN")
        if fields.get("oauth_consumer_key") != self.consumer_key or fields.get("oauth_token") != self.token_id:
            return ns_error(401, "Invalid login attempt.", "INVALID_LOGIN")
        if fields["oauth_nonce"] in self.nonces or len(fields["oauth_nonce"]) < 6:
            return ns_error(401, "Invalid login attempt (nonce used before).", "INVALID_LOGIN")
        self.nonces.add(fields["oauth_nonce"])
        if abs(int(fields["oauth_timestamp"]) - int(datetime.now(timezone.utc).timestamp())) > 300:
            return ns_error(401, "Invalid login attempt (timestamp).", "INVALID_LOGIN")
        good = expected_signature(request.method, str(request.url), fields, self.consumer_secret, self.token_secret)
        if not hmac.compare_digest(good, fields.get("oauth_signature", "")):
            return ns_error(401, "Invalid login attempt (signature).", "INVALID_LOGIN")
        return None

    # -- entry point -----------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        denied = self.check_auth(request)
        if denied is not None:
            return denied
        if self.throttle:
            self.throttle -= 1
            return ns_error(429, "Concurrent request limit exceeded. Request blocked.", "CONCURRENCY_LIMIT_EXCEEDED")
        if path == "/services/rest/query/v1/suiteql":
            assert request.headers.get("prefer") == "transient"
            statement = json.loads(request.content)["q"]
            self.statements.append(statement)
            params = dict(request.url.params)
            rows = self.suiteql(statement)
            if isinstance(rows, httpx.Response):
                return rows
            offset, limit = int(params.get("offset", "0")), int(params.get("limit", "1000"))
            page = rows[offset:offset + limit]
            return httpx.Response(200, json={"links": [], "count": len(page), "hasMore": offset + limit < len(rows), "offset": offset,
                                             "totalResults": len(rows), "items": [{"links": [], **r} for r in page]})
        match = re.match(r"^/services/rest/record/v1/(\w+)(?:/([^/]+))?(?:/!transform/(\w+))?$", path)
        assert match, path
        record_type, ident, transform_to = match.groups()
        body = json.loads(request.content) if request.content else {}
        self.bodies.append((request.method, path, body))
        if request.method == "GET":
            return self.get_record(record_type, ident)
        if request.method == "POST" and transform_to:
            return self.transform(record_type, ident, transform_to, body)
        if request.method == "POST":
            return self.create(record_type, body)
        if request.method == "PATCH":
            return self.patch(record_type, ident, body, request.url.params.get("replace"))
        raise AssertionError(f"unexpected {request.method} {path}")

    # -- records ----------------------------------------------------------------

    def store(self, record_type: str) -> dict[str, dict[str, Any]]:
        return {"customer": self.customers, "contact": self.contacts, "invoice": self.invoices, "salesOrder": self.orders,
                "creditMemo": self.docs}[record_type]

    def get_record(self, record_type: str, ident: str) -> httpx.Response:
        rows = self.store(record_type)
        if ident.startswith("eid:"):
            found = next((r for r in rows.values() if r.get("externalid") == ident[4:]), None)
        else:
            found = rows.get(ident)
        return httpx.Response(200, json={"links": [], "id": found["id"]}) if found else ns_error(404, "Record not found.", "NONEXISTENT_ID")

    def lines(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for line in (body.get("item") or {}).get("items") or []:
            assert re.match(r"^\d+$", line["item"]["id"]), line
            out.append(line)
        return out

    def create(self, record_type: str, body: dict[str, Any]) -> httpx.Response:
        rows = self.store(record_type)
        external = body.get("externalId")
        if external and any(r.get("externalid") == external for r in rows.values()):
            return ns_error(400, "Error while accessing a resource. This record already exists.", "DUP_RCRD")
        new_id = self.new_id()
        now = stamp(local_now())
        if record_type == "customer":
            if "subsidiary" not in body:
                return ns_error(400, "Please enter value(s) for: Primary Subsidiary")
            rows[new_id] = {"id": new_id, "entityid": body["companyName"], "companyname": body["companyName"], "email": body.get("email"),
                            "isinactive": "F", "subsidiary": body["subsidiary"]["id"], "externalid": external, "body": body}
        elif record_type == "contact":
            rows[new_id] = {"id": new_id, "externalid": external, "body": body}
        elif record_type == "invoice":
            lines = self.lines(body)
            total = sum(Decimal(str(line["amount"])) for line in lines)
            rows[new_id] = {"id": new_id, "tranid": body.get("tranId") or f"INV{new_id}", "status": "A", "voided": "F", "total": str(total),
                            "remaining": str(total), "currency": "USD", "entity": body["entity"]["id"], "externalid": external,
                            "trandate": body["tranDate"], "duedate": body.get("dueDate"), "modified": now, "body": body}
        elif record_type == "salesOrder":
            lines = self.lines(body)
            total = sum(Decimal(str(line["amount"])) for line in lines)
            rows[new_id] = {"id": new_id, "tranid": f"SO{new_id}", "status": body["orderStatus"]["id"], "total": str(total), "currency": "USD",
                            "externalid": external, "modified": now, "body": body}
        self.creates[record_type] += 1
        if self.fail_after[record_type]:
            self.fail_after[record_type] -= 1
            return ns_error(500, "An unexpected error occurred.", "UNEXPECTED_ERROR")
        return httpx.Response(204, headers={"Location": f"{BASE}/record/v1/{record_type}/{new_id}"})

    def patch(self, record_type: str, ident: str, body: dict[str, Any], replace: str | None) -> httpx.Response:
        row = self.store(record_type).get(ident)
        if row is None:
            return ns_error(404, "Record not found.", "NONEXISTENT_ID")
        if record_type in {"invoice", "salesOrder"}:
            assert replace == "item", "lines must be replaced, not merged"
            total = sum(Decimal(str(line["amount"])) for line in self.lines(body))
            paid = Decimal(row.get("total", "0")) - Decimal(row.get("remaining", row.get("total", "0")))
            row["total"] = str(total)
            if record_type == "invoice":
                row["remaining"] = str(total - paid)
        row["body"] = {**row.get("body", {}), **body}
        row["modified"] = stamp(local_now())
        return httpx.Response(204)

    def transform(self, record_type: str, ident: str, to_type: str, body: dict[str, Any]) -> httpx.Response:
        assert (record_type, to_type) == ("invoice", "creditMemo")
        invoice = self.invoices[ident]
        applied = (body.get("apply") or {}).get("items") or []
        assert applied == [{"doc": {"id": ident}, "apply": True, "amount": float(invoice["remaining"])}], applied
        if any(d.get("externalid") == body.get("externalId") for d in self.docs.values()):
            return ns_error(400, "This record already exists.", "DUP_RCRD")
        credit = self.new_id()
        self.docs[credit] = {"id": credit, "type": "CustCred", "tranid": f"CM{credit}", "externalid": body.get("externalId"),
                             "trandate": date.today().isoformat(), "modified": stamp(local_now())}
        self.links.append({"previousdoc": ident, "nextdoc": credit, "linktype": "Payment", "foreignamount": invoice["remaining"]})
        invoice.update(remaining="0", status="B", modified=stamp(local_now()))
        self.creates["creditMemo"] += 1
        return httpx.Response(204, headers={"Location": f"{BASE}/record/v1/creditMemo/{credit}"})

    # -- SuiteQL ---------------------------------------------------------------

    @staticmethod
    def since(statement: str, column: str) -> str | None:
        found = re.search(rf"{re.escape(column)} >= TO_DATE\('([^']+)', 'YYYY-MM-DD HH24:MI:SS'\)", statement)
        return found.group(1) if found else None

    @staticmethod
    def cursor(statement: str, alias: str) -> tuple[str, int] | None:
        found = re.search(rf"> '([0-9: -]{{19}})' OR \(TO_CHAR\({alias}\.lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS'\) = '[0-9: -]{{19}}' AND {alias}\.id > (\d+)\)", statement)
        return (found.group(1), int(found.group(2))) if found else None

    def invoice_rows(self, invoice: dict[str, Any], with_links: bool = True) -> list[dict[str, Any]]:
        head = {"id": invoice["id"], "tranid": invoice["tranid"], "status": invoice["status"], "voided": invoice["voided"],
                "foreigntotal": invoice["total"], "foreignamountunpaid": invoice["remaining"], "currency": invoice["currency"],
                "entity": invoice["entity"], "externalid": invoice["externalid"], "trandate": invoice["trandate"], "duedate": invoice["duedate"]}
        links = [link for link in self.links if link["previousdoc"] == invoice["id"]] if with_links else []
        if not links:
            return [{**head, "link_type": None, "link_amount": None, "link_id": None, "link_doc": None, "link_number": None, "link_external": None,
                     "link_date": None}]
        rows = []
        for link in links:
            doc = self.docs[link["nextdoc"]]
            rows.append({**head, "link_type": link["linktype"], "link_amount": link["foreignamount"], "link_id": doc["id"], "link_doc": doc["type"],
                         "link_number": doc["tranid"], "link_external": doc.get("externalid"), "link_date": doc["trandate"]})
        return rows

    def suiteql(self, q: str) -> list[dict[str, Any]] | httpx.Response:  # noqa: C901 - one branch per statement AIVA sends
        if "FROM dual" in q:
            return [{"now": stamp(local_now())}]
        if "FROM subsidiary s" in q:
            return list(self.subsidiaries)
        if "FROM currency c WHERE c.isinactive" in q:
            return list(self.currencies)
        if "FROM department" in q:
            return ns_error(400, "Invalid search query. Departments are not enabled.", "INVALID_PARAMETER")
        for table in ("salestaxitem", "term", "location", "classification", "pricelevel"):
            if f"FROM {table} " in q:
                return [{"id": "5", "name": f"{table} five", "rate": "8.25%"}]
        if "FROM customer c" in q:
            rows = [c for c in self.customers.values() if c["isinactive"] == "F" or "externalid =" in q or "c.id =" in q]
            if (m := re.search(r"c\.externalid = '([^']*)'", q)):
                rows = [c for c in rows if c["externalid"] == m.group(1)]
            elif (m := re.search(r"LOWER\(c\.email\) = '([^']*)'", q)):
                rows = [c for c in rows if (c.get("email") or "").lower() == m.group(1)]
            elif (m := re.search(r"UPPER\(c\.companyname\) = UPPER\('((?:[^']|'')*)'\)", q)):
                rows = [c for c in rows if c["companyname"].upper() == m.group(1).replace("''", "'").upper()]
            elif (m := re.search(r"c\.id = (\d+)", q)):
                rows = [c for c in rows if c["id"] == m.group(1)]
            elif (m := re.search(r"LIKE '%((?:[^']|'')*)%'", q)):
                rows = [c for c in rows if m.group(1).replace("''", "'") in c["companyname"].upper()]
            if (m := re.search(r"c\.subsidiary = (\d+)", q)):
                rows = [c for c in rows if c["subsidiary"] == m.group(1)]
            return [{k: v for k, v in c.items() if k != "body"} for c in rows]
        if "t.type = 'CustInvc' AND t.id = " in q:
            ident = re.search(r"t\.id = (\d+)", q).group(1)
            return self.invoice_rows(self.invoices[ident]) if ident in self.invoices else []
        if "SELECT DISTINCT l.previousdoc" in q:
            after = self.since(q, "p.lastmodifieddate")
            found = {link["previousdoc"] for link in self.links if self.docs[link["nextdoc"]]["modified"] >= after}
            return [{"id": i} for i in sorted(found) if (self.invoices[i]["externalid"] or "").startswith("AIVA-INV-")]
        if "t.type = 'CustInvc' AND t.externalid LIKE 'AIVA-INV-%'" in q:
            after = self.since(q, "t.lastmodifieddate")
            return [{"id": i["id"]} for i in self.invoices.values() if (i["externalid"] or "").startswith("AIVA-INV-") and i["modified"] >= after]
        if "t.type = 'CustInvc' AND t.entity = " in q:
            entity = re.search(r"t\.entity = (\d+)", q).group(1)
            rows = [r for i in self.invoices.values() if i["entity"] == entity for r in self.invoice_rows(i, with_links=False)]
            if "foreignamountunpaid > 0" in q:
                rows = [r for r in rows if Decimal(r["foreignamountunpaid"]) > 0 and r["voided"] == "F"]
            return rows
        if "FROM item i WHERE i.isinactive = 'F'" in q:  # the settings' item list
            return [{"id": i["id"], "itemid": i["itemid"], "displayname": i["displayname"], "itemtype": i["itemtype"]} for i in self.items if i["isinactive"] == "F"]
        if "FROM item i WHERE i.id = " in q:
            ident = re.search(r"i\.id = (\d+)", q).group(1)
            return [dict(i) for i in self.items if i["id"] == ident]
        if "FROM item i WHERE i.itemtype IN" in q:
            rows = sorted(self.items, key=lambda i: (i["modified"], int(i["id"])))
            position = self.cursor(q, "i")
            if position:
                rows = [i for i in rows if (i["modified"], int(i["id"])) > (position[0], position[1])]
            return [dict(i) for i in rows]
        if "FROM pricing p" in q:
            level = re.search(r"p\.pricelevel = (\d+)", q).group(1)
            wanted = set(re.search(r"p\.item IN \(([^)]*)\)", q).group(1).replace(" ", "").split(","))
            return [p for p in self.prices if p["item"] in wanted and p["pricelevel"] == level]
        if "FROM aggregateitemlocation a" in q:
            wanted = set(re.search(r"a\.item IN \(([^)]*)\)", q).group(1).replace(" ", "").split(","))
            rows = [s for s in self.stock if s["item"] in wanted]
            if (m := re.search(r"a\.location = (\d+)", q)):
                rows = [s for s in rows if s["location"] == m.group(1)]
            return rows
        if "t.type = 'SalesOrd'" in q:
            rows = sorted(self.orders.values(), key=lambda o: (o["modified"], int(o["id"])))
            if (m := re.search(r"t\.id IN \(([^)]*)\)", q)):
                wanted = set(m.group(1).replace(" ", "").split(","))
                rows = [o for o in rows if o["id"] in wanted]
            if "t.externalid LIKE 'AIVA-SO-%'" in q:
                rows = [o for o in rows if (o["externalid"] or "").startswith("AIVA-SO-")]
                position = self.cursor(q, "t")
                if position:
                    rows = [o for o in rows if (o["modified"], int(o["id"])) > (position[0], position[1])]
            return [{"id": o["id"], "tranid": o["tranid"], "status": o["status"], "foreigntotal": o["total"], "currency": o["currency"],
                     "externalid": o["externalid"], "modified": o["modified"]} for o in rows]
        raise AssertionError(f"unexpected SuiteQL: {q}")


@pytest.fixture()
def netsuite(monkeypatch):
    from app.integrations.providers import _acct_http

    fake = FakeNetSuite()
    monkeypatch.setattr(_acct_http, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def connect(client, ws, fake: FakeNetSuite, **settings: Any) -> dict:
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/netsuite/toggle", json={"enabled": True}, headers=h).status_code == 200
    saved = client.put(f"{API}/integration-hub/netsuite/credentials", json={"values": creds()}, headers=h)
    assert saved.status_code == 200, saved.text
    if settings:
        response = client.put(f"{API}/integration-hub/netsuite/settings", json={"config": settings}, headers=h)
        assert response.status_code == 200, response.text
    return saved.json()


def make_account(client, ws, name: str | None = None, email: str | None = None) -> tuple[str, str]:
    h = ws["headers"]
    account = client.post(f"{API}/accounts", json={"name": name or f"Initech {uuid.uuid4().hex[:6]}", "billing_city": "Austin", "billing_state": "TX"},
                          headers=h)
    assert account.status_code == 201, account.text
    contact = client.post(f"{API}/contacts", json={"first_name": "Peter", "last_name": "Gibbons", "email": email or f"peter-{uuid.uuid4().hex[:6]}@initech.example",
                                                  "account_id": account.json()["id"]}, headers=h)
    assert contact.status_code == 201, contact.text
    return account.json()["id"], contact.json()["id"]


def make_invoice(ws, account_id: str, total: str = "100.00", lines: list[dict] | None = None) -> str:
    from sqlalchemy import func, select

    from app.models.editions import SalesInvoice

    invoice_id = uuid.uuid4()
    amount = Decimal(total)

    async def add(db):
        number = (await db.execute(select(func.coalesce(func.max(SalesInvoice.number), 1000)).where(SalesInvoice.organization_id == uuid.UUID(ws["org"])))).scalar_one() + 1
        db.add(SalesInvoice(
            id=invoice_id, organization_id=uuid.UUID(ws["org"]), number=number, account_id=uuid.UUID(account_id), owner_user_id=uuid.UUID(ws["user_id"]),
            status="sent", issue_date=date.today(), due_date=date.today() + timedelta(days=30), currency="USD", subtotal=amount,
            discount_total=Decimal("0"), tax_total=Decimal("0"), total=amount, amount_paid=Decimal("0"),
            line_items=lines or [{"name": "Consulting", "description": "October", "quantity": 1, "unit_price": float(amount), "discount_percent": 0,
                                  "tax_percent": 0}],
        ))

    run_service(add)
    return str(invoice_id)


def get_row(model, row_id: str):
    async def go(db):
        return await db.get(model, uuid.UUID(row_id))

    return run_service(go)


def set_row(model, row_id: str, **values: Any) -> None:
    async def go(db):
        row = await db.get(model, uuid.UUID(row_id))
        for k, v in values.items():
            setattr(row, k, v)

    run_service(go)


def connection_row(org: str):
    from sqlalchemy import select

    from app.models.integration_hub import IntegrationConnection

    async def go(db):
        return (await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == uuid.UUID(org),
                                                                     IntegrationConnection.provider == "netsuite"))).scalar_one()

    return run_service(go)


def links(org: str, record_type: str | None = None) -> list[Any]:
    from sqlalchemy import select

    from app.models.integration_hub import IntegrationRecordLink

    async def go(db):
        query = select(IntegrationRecordLink).where(IntegrationRecordLink.organization_id == uuid.UUID(org), IntegrationRecordLink.provider == "netsuite")
        if record_type:
            query = query.where(IntegrationRecordLink.record_type == record_type)
        return list((await db.execute(query)).scalars())

    return run_service(go)


def products(org: str) -> list[Any]:
    from sqlalchemy import select

    from app.models.platform import Product

    async def go(db):
        return list((await db.execute(select(Product).where(Product.organization_id == uuid.UUID(org)).order_by(Product.name))).scalars())

    return run_service(go)


# ---------------------------------------------------------------------------
# Token-based authentication and account IDs
# ---------------------------------------------------------------------------


def test_tba_signature_matches_independent_vectors():
    """Signatures computed with oauthlib 3.2.2 (HMAC-SHA256) for the same inputs."""
    from app.integrations.providers import _ns_api as ns

    values = creds()
    account = ns.Credentials(account=ACCOUNT, consumer_key=values["consumer_key"], consumer_secret=values["consumer_secret"],
                             token_id=values["token_id"], token_secret=values["token_secret"])
    cases = [
        ("POST", f"{BASE}/query/v1/suiteql?limit=5&offset=0", "4fbrsnh1grY0cLF/hLGBcFRe8ADGuIr8rxmFtdAfH8g="),
        ("GET", f"{BASE}/record/v1/salesOrder/eid:AIVA-SO-1?fields=id", "51Sh5JSJJHnK9K3MPUqXzvWCTk6sw7emfWrE/E+HcV0="),
    ]
    for method, url, signature in cases:
        header = ns.authorization(account, method, url, timestamp=1700000000, nonce="abcdef0123456789")
        fields = {k: unquote(v) for k, v in re.findall(r'(\w+)="([^"]*)"', header)}
        assert fields["oauth_signature"] == signature, method
        assert fields["realm"] == ACCOUNT and fields["oauth_signature_method"] == "HMAC-SHA256" and fields["oauth_version"] == "1.0"
        assert expected_signature(method, url, fields, values["consumer_secret"], values["token_secret"]) == signature
    # Fresh nonce and timestamp every time; the secrets never appear in the header or the repr.
    first, second = ns.authorization(account, "GET", f"{BASE}/record/v1/customer/1"), ns.authorization(account, "GET", f"{BASE}/record/v1/customer/1")
    assert first != second
    assert values["consumer_secret"] not in first and values["token_secret"] not in first
    assert values["consumer_secret"] not in repr(account) and values["consumer_key"] not in str(account)


def test_account_ids_hosts_and_suiteql_quoting():
    from app.integrations.core import IntegrationError
    from app.integrations.providers import _ns_api as ns

    for value in ("1234567_SB1", "1234567_sb1", "1234567-SB1", " 1234567-sb1 ", "https://1234567-sb1.app.netsuite.com/app/center/card.nl",
                  "1234567-sb1.suitetalk.api.netsuite.com"):
        assert ns.normalize_account(value) == ACCOUNT, value
    assert ns.normalize_account("TSTDRV2345678") == "TSTDRV2345678" and ns.normalize_account("7654321_RP") == "7654321_RP"
    for bad in ("", "evil.com", "1234567.evil.com", "https://1234567.evil.com/x.netsuite.com", "1234567_SB1@evil", "../1234567", "12 34", "1234567_XX1"):
        with pytest.raises(IntegrationError):
            ns.normalize_account(bad)
    assert ns.rest_base("1234567_SB1") == BASE and ns.app_base("1234567") == "https://1234567.app.netsuite.com"
    assert ns.ui_url(ACCOUNT, "invoice", "42") == "https://1234567-sb1.app.netsuite.com/app/accounting/transactions/custinvc.nl?id=42"
    assert ns.ui_url(ACCOUNT, "invoice", "42 OR 1=1") is None
    assert ns.text("O'Brien") == "'O''Brien'" and ns.text("a\x00b\nc") == "'abc'"
    assert ns.like("50%_off") == "'%50 off%'"
    with pytest.raises(IntegrationError):
        ns.ident("1 OR 1=1")
    with pytest.raises(IntegrationError):
        ns.ids(["1", "2; DROP"])
    assert ns.ids(["10", "2", "2"]) == "2, 10"
    with pytest.raises(IntegrationError):
        ns.stamp_text("2026-10-24'; --")
    with pytest.raises(IntegrationError):
        ns.external_id("AIVA-INV", "x'y")
    # Local time: with a known offset, a little earlier; unknown, early enough for any time zone.
    when = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    assert ns.local_time({"clock_offset": -420}, when) == datetime(2026, 10, 24, 4, 50)
    assert ns.local_time({}, when) == datetime(2026, 10, 23, 22, 0)


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------


def test_connect_validates_and_keeps_credentials_secret(client, netsuite):
    from app.integrations import core

    ws = signup(client)
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/quickbooks/toggle", json={"enabled": True}, headers=h).status_code == 200
    assert client.put(f"{API}/integration-hub/netsuite/toggle", json={"enabled": True}, headers=h).status_code == 200
    assert client.get(f"{API}/integration-hub/quickbooks", headers=h).json()["switched_on"] is False  # one accounting system at a time

    bad = client.put(f"{API}/integration-hub/netsuite/credentials", json={"values": {**creds(), "account_id": "evil.example.com"}}, headers=h)
    assert bad.status_code == 422 and "account ID" in bad.json()["detail"]
    wrong = client.put(f"{API}/integration-hub/netsuite/credentials", json={"values": {**creds(), "token_secret": "99" * 32}}, headers=h)
    assert wrong.status_code == 422 and "refused" in wrong.json()["detail"]
    calls_before = len(netsuite.calls)
    short = client.put(f"{API}/integration-hub/netsuite/credentials", json={"values": {**creds(), "consumer_key": "short"}}, headers=h)
    assert short.status_code == 422 and len(netsuite.calls) == calls_before  # checked before anything is sent

    detail = connect(client, ws, netsuite)
    assert detail["status"] == "connected" and detail["connection"]["external_account_id"] == ACCOUNT
    assert detail["connection"]["account_label"] == f"Globex Holdings ({ACCOUNT})"
    dumped = json.dumps(detail)
    for name, value in creds().items():
        if name != "account_id":
            assert value not in dumped, name
    assert detail["connection"]["config"].get("consumer_key") is None and detail["connection"]["config"]["consumer_key_hint"] == "…c0c0"
    assert detail["extra"]["erp"]["credentials"] == {"consumer_key": "…c0c0", "token_id": "…7070"}
    assert detail["extra"]["company"]["name"] == "Globex Holdings" and detail["extra"]["company"]["clock_offset"] == -420
    assert detail["extra"]["erp"]["subsidiary"] == {"id": "1", "name": "Globex Holdings"}  # the only one, chosen
    row = connection_row(ws["org"])
    assert core.secret(row, "consumer_key") == creds()["consumer_key"] and core.secret(row, "token_secret") == creds()["token_secret"]
    assert creds()["consumer_key"] not in json.dumps(row.secrets)  # encrypted at rest

    # Activity never carries credentials either.
    logs = json.dumps(client.get(f"{API}/integration-hub/netsuite", headers=h).json()["logs"])
    assert creds()["consumer_key"] not in logs and creds()["token_id"] not in logs
    assert client.post(f"{API}/integration-hub/netsuite/test", headers=h).json()["message"].startswith("Oracle NetSuite answered: Globex Holdings")
    assert client.get(f"{API}/erp/status", headers=h).json() == {
        "enabled": True, "connected": True, "status": "connected", "account_id": ACCOUNT, "sandbox": True,
        "home_url": "https://1234567-sb1.app.netsuite.com/app/center/card.nl", "orders_from": ["sales_order"], "order_mode": "auto",
        "items_sync": "create", "stock_sync": True, "can_manage": True,
    }
    options = client.get(f"{API}/accounting/netsuite/options", headers=h).json()
    assert options["locations"] == [{"id": "5", "name": "location five"}] and options["departments"] == []  # departments off in that account

    # Settings take NetSuite internal IDs only.
    bad_setting = client.put(f"{API}/integration-hub/netsuite/settings", json={"config": {"location": "5; DROP"}}, headers=h)
    assert bad_setting.status_code == 422
    assert client.put(f"{API}/integration-hub/netsuite/settings", json={"config": {"default_item": "abc"}}, headers=h).status_code == 422
    assert client.put(f"{API}/integration-hub/netsuite/settings", json={"config": {"orders_from": ["sales_order", "nope"]}}, headers=h).status_code == 422
    ok = client.put(f"{API}/integration-hub/netsuite/settings", json={"config": {"location": "5", "orders_from": ["deal", "sales_order"]}}, headers=h)
    assert ok.status_code == 200 and ok.json()["extra"]["settings"]["orders_from"] == ["sales_order", "deal"]


def test_oneworld_needs_a_subsidiary_before_creating_customers(client, netsuite):
    netsuite.subsidiaries.append({"id": "3", "name": "Globex UK", "parent": "1", "iselimination": "F", "currency": "GBP"})
    netsuite.subsidiaries.append({"id": "4", "name": "Eliminations", "parent": "1", "iselimination": "T", "currency": "USD"})
    ws = signup(client)
    h = ws["headers"]
    detail = connect(client, ws, netsuite, default_item="11")
    assert [c["id"] for c in detail["extra"]["choices"]] == ["1", "3"] and detail["extra"]["erp"]["subsidiary"] is None
    account_id, _ = make_account(client, ws, name="Umbrella Ltd")
    invoice_id = make_invoice(ws, account_id)
    run_due(ws["org"])
    pushed = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()["link"]
    assert pushed["status"] == "pending" and "subsidiary" in pushed["last_error"]
    assert not netsuite.customers
    assert client.put(f"{API}/accounting/netsuite/company", json={"id": "4"}, headers=h).status_code == 422  # eliminations aren't offered
    assert client.put(f"{API}/accounting/netsuite/company", json={"id": "3"}, headers=h).status_code == 200
    assert client.post(f"{API}/accounting/netsuite/retry", headers=h).json()["count"] == 1
    run_due(ws["org"])
    customer = next(iter(netsuite.customers.values()))
    assert customer["subsidiary"] == "3" and customer["companyname"] == "Umbrella Ltd"
    assert client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()["link"]["status"] == "linked"
    assert connection_row(ws["org"]).account_label == f"Globex Holdings ({ACCOUNT})"  # the account keeps its name


# ---------------------------------------------------------------------------
# Customers, invoices and payments
# ---------------------------------------------------------------------------


def test_invoice_is_created_once_and_payments_count_once(client, netsuite):
    from app.models.editions import SalesInvoice

    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, netsuite)
    account_id, _ = make_account(client, ws, name="Initech", email="billing@initech.example")
    invoice_id = make_invoice(ws, account_id, total="100.00")

    # No item for the line yet: the invoice waits with a clear error and backs off.
    run_due(ws["org"])
    status = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()
    assert status["link"]["status"] == "pending" and "NetSuite item" in status["link"]["last_error"] and status["link"]["retry_at"]
    assert netsuite.creates["invoice"] == 0 and netsuite.creates["customer"] == 1  # the customer was made already

    client.put(f"{API}/integration-hub/netsuite/settings", json={"config": {"default_item": "11", "numbering": "aiva"}}, headers=h)
    # The first create reaches NetSuite but the answer is lost: the next try finds it by external ID.
    netsuite.fail_after["invoice"] = 1
    assert client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h).status_code == 502
    assert netsuite.creates["invoice"] == 1
    pushed = client.post(f"{API}/accounting/invoices/{invoice_id}/push", headers=h)
    assert pushed.status_code == 200, pushed.text
    assert netsuite.creates["invoice"] == 1 and len(netsuite.invoices) == 1
    remote = next(iter(netsuite.invoices.values()))
    assert remote["externalid"] == f"AIVA-INV-{invoice_id}" and remote["tranid"].startswith("INV-")
    body = remote["body"]
    assert body["item"]["items"][0] == {"item": {"id": "11"}, "quantity": 1, "rate": 100, "amount": 100, "description": "October"}
    customer = next(iter(netsuite.customers.values()))
    assert customer["externalid"] == f"AIVA-ACC-{account_id}" and customer["email"] == "billing@initech.example"
    assert body["entity"] == {"id": customer["id"]}
    assert customer["body"]["addressBook"]["items"][0]["addressBookAddress"] == {"city": "Austin", "state": "TX"}
    link = pushed.json()["link"]
    assert link["external_url"] == f"https://1234567-sb1.app.netsuite.com/app/accounting/transactions/custinvc.nl?id={remote['id']}"

    # A card payment recorded in AIVA (40) that the bookkeeper also applies in NetSuite counts once.
    set_row(SalesInvoice, invoice_id, amount_paid=Decimal("40.00"))
    netsuite.add_payment(remote["id"], "40.00")
    run_due(ws["org"], poll=True)
    row = get_row(SalesInvoice, invoice_id)
    assert row.amount_paid == Decimal("40.00") and row.status == "sent"
    status = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()
    assert [p["amount"] for p in status["payments"]] == ["40.00"] and status["link"]["balance"] == "60.00"
    netsuite.add_payment(remote["id"], "60.00", doc="DepAppl")
    run_due(ws["org"], poll=True)
    row = get_row(SalesInvoice, invoice_id)
    assert row.amount_paid == Decimal("100.00") and row.status == "paid"
    status = client.get(f"{API}/accounting/invoices/{invoice_id}", headers=h).json()
    assert sorted(p["amount"] for p in status["payments"]) == ["40.00", "60.00"]
    assert {p["method"] for p in status["payments"]} == {None, "Deposit"}
    # Reading the same state again changes nothing (no double counting on repeat runs).
    netsuite.invoices[remote["id"]]["modified"] = stamp(local_now())
    run_due(ws["org"], poll=True)
    assert get_row(SalesInvoice, invoice_id).amount_paid == Decimal("100.00")
    # A payment removed in NetSuite takes back only what NetSuite added.
    netsuite.remove_payment(next(pid for pid, d in netsuite.docs.items() if d["type"] == "DepAppl"))
    run_due(ws["org"], poll=True)
    row = get_row(SalesInvoice, invoice_id)
    assert row.amount_paid == Decimal("40.00") and row.status == "sent"

    # The account page: balance and recent invoices from NetSuite.
    from app.models.integration_hub import IntegrationRecordLink

    customer_link = next(ln for ln in links(ws["org"], "account"))
    set_row(IntegrationRecordLink, str(customer_link.id), data={**customer_link.data, "refreshed_at": None})
    account = client.post(f"{API}/accounting/accounts/{account_id}/refresh", headers=h).json()
    assert account["link"]["open_balance"] == "60.00" and account["link"]["overdue_balance"] == "0.00"
    assert account["link"]["invoices"][0]["status"] == "open" and account["link"]["invoices"][0]["balance"] == "60.00"
    assert account["link"]["external_url"].endswith(f"/app/common/entity/custjob.nl?id={customer['id']}")


def test_paid_invoices_are_not_changed_and_voiding_credits_in_full(client, netsuite):
    from app.models.editions import SalesInvoice

    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, netsuite, default_item="11")
    account_id, _ = make_account(client, ws)
    first = make_invoice(ws, account_id, total="250.00")
    second = make_invoice(ws, account_id, total="80.00")
    third = make_invoice(ws, account_id, total="90.00")
    run_due(ws["org"])
    assert netsuite.creates["invoice"] == 3 and netsuite.creates["customer"] == 1  # one customer for all
    by_external = {i["externalid"]: i for i in netsuite.invoices.values()}
    paid = by_external[f"AIVA-INV-{first}"]
    netsuite.add_payment(paid["id"], "250.00")
    run_due(ws["org"], poll=True)
    assert get_row(SalesInvoice, first).status == "paid"

    # Changing a paid invoice in AIVA doesn't touch NetSuite.
    set_row(SalesInvoice, first, notes="Thanks!")
    patches = sum(1 for m, p, _ in netsuite.bodies if m == "PATCH")
    run_due(ws["org"])
    assert sum(1 for m, p, _ in netsuite.bodies if m == "PATCH") == patches

    # Voiding the unpaid one credits it in full (REST can't void); it then reads as void, once.
    set_row(SalesInvoice, second, status="void")
    run_due(ws["org"])
    open_one = by_external[f"AIVA-INV-{second}"]
    assert netsuite.creates["creditMemo"] == 1 and open_one["remaining"] == "0"
    credit = next(d for d in netsuite.docs.values() if d["type"] == "CustCred")
    assert credit["externalid"] == f"AIVA-VOID-{open_one['id']}"
    status = client.post(f"{API}/accounting/invoices/{second}/refresh", headers=h).json()
    assert status["link"]["remote_status"] == "void" and status["link"]["balance"] == "0.00"
    assert get_row(SalesInvoice, second).amount_paid == Decimal("0")  # a credit isn't a payment
    run_due(ws["org"])
    assert netsuite.creates["creditMemo"] == 1

    # One already partly credited in NetSuite isn't credited again in full: the accountant finishes it there.
    partial = by_external[f"AIVA-INV-{third}"]
    partial["remaining"] = "30.00"
    set_row(SalesInvoice, third, status="void")
    run_due(ws["org"])
    assert netsuite.creates["creditMemo"] == 1 and partial["remaining"] == "30.00"
    log = client.get(f"{API}/integration-hub/netsuite/logs", headers=h).json()
    items = log["items"] if isinstance(log, dict) else log
    assert any(i["action"] == "void_failed" and "already partly credited" in i["message"] for i in items)


def test_rate_limits_pause_the_account(client, netsuite):
    ws = signup(client)
    connect(client, ws, netsuite, default_item="11")
    account_id, _ = make_account(client, ws)
    make_invoice(ws, account_id)
    netsuite.throttle = 100
    calls = len(netsuite.calls)
    run_due(ws["org"])
    state = connection_row(ws["org"]).config["sync"]
    paused = datetime.fromisoformat(state["paused_until"])
    assert paused > datetime.now(timezone.utc) + timedelta(seconds=30)
    assert len(netsuite.calls) == calls + 1  # stopped at the first busy answer
    run_due(ws["org"])
    assert len(netsuite.calls) == calls + 1  # paused: not called again
    sync = client.post(f"{API}/accounting/netsuite/sync", headers=ws["headers"])
    assert sync.status_code == 429


# ---------------------------------------------------------------------------
# Items, prices and stock
# ---------------------------------------------------------------------------


def test_items_import_matches_by_sku_pages_and_keeps_its_place(client, netsuite, monkeypatch):
    from app.integrations.providers import netsuite as erp
    from app.models.editions import StockMovement
    from app.models.platform import Product

    monkeypatch.setattr(erp, "ITEM_PAGE", 2)
    ws = signup(client)
    h = ws["headers"]
    existing = uuid.uuid4()

    async def add_product(db):
        db.add(Product(id=existing, organization_id=uuid.UUID(ws["org"]), name="Old widget name", sku="widget-1", unit_price=Decimal("5"),
                       currency="USD", track_inventory=True, stock_on_hand=Decimal("3")))

    run_service(add_product)
    same = local_now() - timedelta(minutes=30)
    netsuite.add_item("21", "WIDGET-1", name="Widget", price="19.99", on_hand={"1": "12", "2": "8"}, modified=same)
    netsuite.add_item("22", "SVC-SETUP", "Service", name="Setup service", price="250.00", modified=same)
    netsuite.add_item("23", "OLD-PART", inactive=True, modified=same)
    netsuite.add_item("24", "GADGET", name="Gadget", price="5.50", on_hand={"1": "4"}, modified=same + timedelta(minutes=1))
    connect(client, ws, netsuite)

    run_due(ws["org"])  # first page: items 21 and 22 (same second, ordered by id)
    rows = {p.sku: p for p in products(ws["org"])}
    widget = rows["WIDGET-1"]  # matched by SKU (any case); NetSuite's spelling wins
    assert widget.id == existing and widget.name == "Widget" and widget.unit_price == Decimal("19.99")  # lowest tier's price
    assert widget.stock_on_hand == Decimal("20.00")
    assert rows["SVC-SETUP"].unit_price == Decimal("250.00") and rows["SVC-SETUP"].track_inventory is False
    assert "GADGET" not in rows
    state = connection_row(ws["org"]).config["sync"]
    assert state["items_cursor"] == stamp(same) and state["items_last_id"] == "22" and state["items_caught_up"] is False

    run_due(ws["org"])  # second page: 23 (inactive: skipped) and 24
    rows = {p.sku: p for p in products(ws["org"])}
    assert "OLD-PART" not in rows and rows["GADGET"].track_inventory is True and rows["GADGET"].stock_on_hand == Decimal("4.00")
    run_due(ws["org"])  # nothing left: caught up
    assert connection_row(ws["org"]).config["sync"]["items_caught_up"] is True
    assert len(products(ws["org"])) == 3 and len(links(ws["org"], "product")) == 3

    async def movements(db):
        from sqlalchemy import select

        return list((await db.execute(select(StockMovement).where(StockMovement.product_id == existing))).scalars())

    moved = run_service(movements)
    assert [(m.kind, m.quantity, m.balance_after, m.reference) for m in moved] == [("adjust", Decimal("17.00"), Decimal("20.00"), "NetSuite")]

    # The product page: item, price and stock by location; record access is checked.
    item = client.get(f"{API}/erp/products/{existing}", headers=h).json()["link"]
    assert item["external_id"] == "21" and item["on_hand"] == "20" and item["locations"] == {"Warehouse 1": "12", "Warehouse 2": "8"}
    other = signup(client, "Other Co")
    assert client.get(f"{API}/erp/products/{existing}", headers=other["headers"]).status_code in {403, 404}

    # Stock changes in NetSuite (they don't touch the item) are read every half hour.
    netsuite.stock[0]["quantityonhand"] = "2"
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    from app.models.integration_hub import IntegrationRecordLink

    for link in links(ws["org"], "product"):
        set_row(IntegrationRecordLink, str(link.id), data={**link.data, "stock_at": stale})
    run_due(ws["org"])
    assert get_row(Product, str(existing)).stock_on_hand == Decimal("10.00")

    # An item changed in NetSuite is read again; restarting reads everything again without duplicates.
    netsuite.items[0].update(displayname="Widget Pro", modified=stamp(local_now()))
    netsuite.prices[0]["unitprice"] = "24.00"
    assert client.post(f"{API}/erp/netsuite/items/import", headers=h).json()["count"] == 1
    assert get_row(Product, str(existing)).name == "Widget Pro" and get_row(Product, str(existing)).unit_price == Decimal("24.00")
    assert client.post(f"{API}/erp/netsuite/items/restart", headers=h).status_code == 200
    for _ in range(4):
        run_due(ws["org"])
    assert len(products(ws["org"])) == 3 and len(links(ws["org"], "product")) == 3

    # A product deleted in AIVA isn't brought back.
    gadget = next(p for p in products(ws["org"]) if p.sku == "GADGET")

    async def delete(db):
        await db.delete(await db.get(Product, gadget.id))

    run_service(delete)
    netsuite.items[3]["modified"] = stamp(local_now())
    client.post(f"{API}/erp/netsuite/items/import", headers=h)
    assert "GADGET" not in {p.sku for p in products(ws["org"])}


def test_imported_items_price_invoice_lines(client, netsuite):
    ws = signup(client)
    netsuite.add_item("31", "HRS", "Service", name="Consulting hours", price="150.00")
    connect(client, ws, netsuite)
    run_due(ws["org"])
    product = next(p for p in products(ws["org"]) if p.sku == "HRS")
    account_id, _ = make_account(client, ws)
    make_invoice(ws, account_id, total="270.00", lines=[
        {"name": "Hours", "description": "Workshop", "quantity": 2, "unit_price": 150, "discount_percent": 10, "tax_percent": 0, "product_id": str(product.id)},
    ])
    run_due(ws["org"])
    line = next(iter(netsuite.invoices.values()))["body"]["item"]["items"][0]
    assert line["item"] == {"id": "31"} and line["quantity"] == 2 and line["rate"] == 135 and line["amount"] == 270
    assert line["description"] == "Workshop (less 10% discount)"


# ---------------------------------------------------------------------------
# Contacts and sales orders
# ---------------------------------------------------------------------------


def test_contacts_follow_their_customer(client, netsuite):
    from app.models.contact import Contact

    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, netsuite, default_item="11")
    account_id, contact_id = make_account(client, ws, email="peter@initech.example")
    make_invoice(ws, account_id)
    run_due(ws["org"])
    run_due(ws["org"])
    contact = next(iter(netsuite.contacts.values()))
    customer = next(iter(netsuite.customers.values()))
    assert contact["externalid"] == f"AIVA-CON-{contact_id}"
    assert contact["body"]["company"] == {"id": customer["id"]} and contact["body"]["subsidiary"] == {"id": "1"}
    assert contact["body"]["email"] == "peter@initech.example"
    patches = [b for m, p, b in netsuite.bodies if m == "PATCH" and "/contact/" in p]
    assert not patches
    run_due(ws["org"])
    assert netsuite.creates["contact"] == 1  # unchanged: nothing sent
    assert client.patch(f"{API}/contacts/{contact_id}", json={"job_title": "Engineer"}, headers=h).status_code == 200
    run_due(ws["org"])
    patches = [b for m, p, b in netsuite.bodies if m == "PATCH" and "/contact/" in p]
    assert len(patches) == 1 and patches[0]["title"] == "Engineer" and netsuite.creates["contact"] == 1
    assert get_row(Contact, contact_id).job_title == "Engineer"

    # Deleted in NetSuite: the next change adds the contact again instead of failing forever.
    netsuite.contacts.clear()
    assert client.patch(f"{API}/contacts/{contact_id}", json={"job_title": "Lead engineer"}, headers=h).status_code == 200
    run_due(ws["org"])
    assert netsuite.creates["contact"] == 1 and links(ws["org"], "contact")[0].status == "pending"
    run_due(ws["org"])
    again = next(iter(netsuite.contacts.values()))
    assert netsuite.creates["contact"] == 2 and again["externalid"] == f"AIVA-CON-{contact_id}" and again["body"]["title"] == "Lead engineer"
    assert links(ws["org"], "contact")[0].status == "linked"


def make_sales_order(ws, account_id: str, *, status: str = "confirmed", lines: list[dict] | None = None, quote_id: uuid.UUID | None = None) -> str:
    from sqlalchemy import func, select

    from app.models.supply import SalesOrder

    order_id = uuid.uuid4()
    lines = lines or [{"name": "Widgets", "description": "Blue", "quantity": 3, "unit_price": 20, "discount_percent": 0, "tax_percent": 0}]
    total = sum(Decimal(str(line["quantity"])) * Decimal(str(line["unit_price"])) for line in lines)

    async def add(db):
        number = (await db.execute(select(func.coalesce(func.max(SalesOrder.number), 1000)).where(SalesOrder.organization_id == uuid.UUID(ws["org"])))).scalar_one() + 1
        db.add(SalesOrder(
            id=order_id, organization_id=uuid.UUID(ws["org"]), number=number, subject="Spring order", status=status, account_id=uuid.UUID(account_id),
            owner_user_id=uuid.UUID(ws["user_id"]), order_date=date.today(), due_date=date.today() + timedelta(days=7), customer_po="PO-778",
            currency="USD", subtotal=total, total=total, line_items=lines, quote_id=quote_id,
            confirmed_at=datetime.now(timezone.utc) if status != "draft" else None,
        ))

    run_service(add)
    return str(order_id)


def test_sales_orders_go_once_update_while_pending_and_report_back(client, netsuite):
    from app.models.platform import Notification
    from app.models.supply import SalesOrder

    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, netsuite, default_item="11", order_status="B", location="5")
    account_id, _ = make_account(client, ws)
    draft = make_sales_order(ws, account_id, status="draft")
    order_id = make_sales_order(ws, account_id)

    netsuite.fail_after.update({"customer": 1, "salesOrder": 1})  # created, but the answers are lost
    run_due(ws["org"])
    assert netsuite.creates["customer"] == 1 and netsuite.creates["salesOrder"] == 0
    waiting = client.get(f"{API}/erp/orders/sales_order/{order_id}", headers=h).json()["link"]
    assert waiting["status"] == "pending" and waiting["retry_at"] and waiting["external_id"] is None
    run_due(ws["org"])
    assert netsuite.creates["salesOrder"] == 0  # backing off
    assert client.post(f"{API}/erp/orders/sales_order/{order_id}/push", headers=h).status_code == 502
    assert netsuite.creates["customer"] == 1 and netsuite.creates["salesOrder"] == 1  # the customer was found by its external ID
    assert client.post(f"{API}/erp/orders/sales_order/{order_id}/push", headers=h).status_code == 200
    run_due(ws["org"])
    assert netsuite.creates["salesOrder"] == 1 and len(netsuite.orders) == 1  # found by external ID, not created twice
    remote = next(iter(netsuite.orders.values()))
    assert remote["externalid"] == f"AIVA-SO-{order_id}" and remote["status"] == "B"
    body = remote["body"]
    assert body["otherRefNum"] == "PO-778" and body["location"] == {"id": "5"} and body["orderStatus"] == {"id": "B"}
    assert body["item"]["items"][0]["quantity"] == 3 and body["item"]["items"][0]["amount"] == 60
    status = client.get(f"{API}/erp/orders/sales_order/{order_id}", headers=h).json()
    assert status["link"]["status"] == "linked" and status["link"]["number"] == remote["tranid"] and status["link"]["status_name"] == "Pending fulfillment"
    draft_status = client.get(f"{API}/erp/orders/sales_order/{draft}", headers=h).json()
    assert draft_status["link"] is None and draft_status["blocked_reason"] == "Confirm the sales order first."
    assert client.post(f"{API}/erp/orders/sales_order/{draft}/push", headers=h).status_code == 409

    # A change in AIVA while NetSuite still has it pending replaces the lines.
    set_row(SalesOrder, order_id, line_items=[{"name": "Widgets", "quantity": 5, "unit_price": 20, "discount_percent": 0, "tax_percent": 0}])
    run_due(ws["org"])
    assert remote["total"] == "100" and netsuite.creates["salesOrder"] == 1

    # Fulfilled and billed in NetSuite: the AIVA order follows, the owner hears about it.
    remote.update(status="G", modified=stamp(local_now() + timedelta(seconds=5)))
    pushed = client.post(f"{API}/erp/orders/sales_order/{order_id}/refresh", headers=h).json()
    assert pushed["link"]["status_name"] == "Billed"
    row = get_row(SalesOrder, order_id)
    assert row.status == "fulfilled" and row.fulfilled_at is not None and row.stock_applied is True

    async def notes(db):
        from sqlalchemy import select

        return list((await db.execute(select(Notification).where(Notification.organization_id == uuid.UUID(ws["org"]), Notification.kind == "sales_order"))).scalars())

    assert [n.title for n in run_service(notes)] == [f"SO-{row.number} was fulfilled in NetSuite"]
    # Billed orders aren't changed any more.
    set_row(SalesOrder, order_id, line_items=[{"name": "Widgets", "quantity": 9, "unit_price": 20, "discount_percent": 0, "tax_percent": 0}])
    patches = sum(1 for m, p, _ in netsuite.bodies if m == "PATCH" and "/salesOrder/" in p)
    run_due(ws["org"])
    assert sum(1 for m, p, _ in netsuite.bodies if m == "PATCH" and "/salesOrder/" in p) == patches
    forced = client.post(f"{API}/erp/orders/sales_order/{order_id}/push", headers=h)
    assert forced.status_code == 409 and "won't change" in forced.json()["detail"]


def test_accepted_quote_order_moves_to_its_sales_order(client, netsuite):
    from app.models.platform import Quote, QuoteLineItem

    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, netsuite, default_item="11", orders_from=["sales_order", "quote"])
    account_id, _ = make_account(client, ws)
    quote_id = uuid.uuid4()

    async def add_quote(db):
        db.add(Quote(id=quote_id, organization_id=uuid.UUID(ws["org"]), number=7, name="Spring deal", status="accepted", account_id=uuid.UUID(account_id),
                     currency="USD", total=Decimal("40"), accepted_at=datetime.now(timezone.utc)))
        await db.flush()
        db.add(QuoteLineItem(id=uuid.uuid4(), organization_id=uuid.UUID(ws["org"]), quote_id=quote_id, name="Thing", quantity=Decimal("2"),
                             unit_price=Decimal("20"), position=0))

    run_service(add_quote)
    run_due(ws["org"])
    assert netsuite.creates["salesOrder"] == 1
    remote = next(iter(netsuite.orders.values()))
    assert remote["externalid"] == f"AIVA-SO-{quote_id}"
    assert client.get(f"{API}/erp/orders/quote/{quote_id}", headers=h).json()["link"]["status"] == "linked"

    order_id = make_sales_order(ws, account_id, quote_id=quote_id, lines=[{"name": "Thing", "quantity": 3, "unit_price": 20, "discount_percent": 0,
                                                                        "tax_percent": 0}])
    run_due(ws["org"])
    assert netsuite.creates["salesOrder"] == 1 and remote["total"] == "60"  # the same NetSuite order, now with the order's lines
    moved = client.get(f"{API}/erp/orders/sales_order/{order_id}", headers=h).json()["link"]
    assert moved["external_id"] == remote["id"] and moved["adopted_from"] == f"quote:{quote_id}"
    assert client.get(f"{API}/erp/orders/quote/{quote_id}", headers=h).json()["link"] is None


def test_sending_by_hand_follows_the_same_rules(client, netsuite):
    """"Send to NetSuite" can't create orders the automatic sync wouldn't: sources that are off,
    deals that aren't won (or have no amount), and deals or quotes another order already covers."""

    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, netsuite, default_item="11")  # sales orders only
    account_id, _ = make_account(client, ws)
    pipeline = client.get(f"{API}/pipelines", headers=h).json()[0]
    found = sorted(client.get(f"{API}/pipelines/{pipeline['id']}/stages", headers=h).json(), key=lambda s: s["position"])
    first, won = found[0]["id"], next(s["id"] for s in found if s["category"] == "won")
    deal = client.post(f"{API}/opportunities", json={"name": "Big deal", "account_id": account_id, "amount": "500", "stage_id": first,
                                                    "pipeline_id": pipeline["id"]}, headers=h)
    assert deal.status_code == 201, deal.text
    deal_id = deal.json()["id"]

    off = client.post(f"{API}/erp/orders/opportunity/{deal_id}/push", headers=h)
    assert off.status_code == 409 and "won deals" in off.json()["detail"]
    client.put(f"{API}/integration-hub/netsuite/settings", json={"config": {"orders_from": ["sales_order", "deal"]}}, headers=h)
    open_deal = client.post(f"{API}/erp/orders/opportunity/{deal_id}/push", headers=h)
    assert open_deal.status_code == 409 and open_deal.json()["detail"] == "Only won deals become sales orders."
    assert client.get(f"{API}/erp/orders/opportunity/{deal_id}", headers=h).json()["can_push"] is False
    assert client.patch(f"{API}/opportunities/{deal_id}", json={"stage_id": won}, headers=h).status_code == 200
    covered = make_sales_order(ws, account_id)
    from app.models.supply import SalesOrder

    set_row(SalesOrder, covered, opportunity_id=uuid.UUID(deal_id))
    duplicate = client.post(f"{API}/erp/orders/opportunity/{deal_id}/push", headers=h)
    assert duplicate.status_code == 409 and "already has" in duplicate.json()["detail"]
    set_row(SalesOrder, covered, status="cancelled")
    sent = client.post(f"{API}/erp/orders/opportunity/{deal_id}/push", headers=h)
    assert sent.status_code == 200, sent.text
    assert any(o["externalid"] == f"AIVA-SO-{deal_id}" for o in netsuite.orders.values())
    assert netsuite.creates["salesOrder"] == 1


# ---------------------------------------------------------------------------
# Who can do what, and fair turns
# ---------------------------------------------------------------------------


def test_members_and_other_workspaces(client, netsuite):
    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, netsuite, default_item="11")
    account_id, _ = make_account(client, ws)
    order_id = make_sales_order(ws, account_id)
    email = f"member-{uuid.uuid4().hex[:8]}@erp.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Max Member", "role": "member"}, headers=h).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.post(f"{API}/erp/netsuite/items/import", headers=member).status_code == 403
    assert client.post(f"{API}/erp/netsuite/items/restart", headers=member).status_code == 403
    assert client.put(f"{API}/integration-hub/netsuite/settings", json={"config": {"location": "5"}}, headers=member).status_code == 403
    assert client.put(f"{API}/integration-hub/netsuite/credentials", json={"values": creds()}, headers=member).status_code == 403
    detail = client.get(f"{API}/integration-hub/netsuite", headers=member).json()
    assert "settings" not in detail["extra"] and "credentials" not in detail["extra"]["erp"]
    assert client.get(f"{API}/erp/status", headers=member).json()["can_manage"] is False
    assert client.get(f"{API}/erp/orders/sales_order/{order_id}", headers=member).status_code == 200

    other = signup(client, "Elsewhere Inc")
    for path in (f"/erp/orders/sales_order/{order_id}", f"/erp/orders/sales_order/{order_id}/refresh"):
        response = client.get(f"{API}{path}", headers=other["headers"]) if path.endswith(order_id) else client.post(f"{API}{path}", headers=other["headers"])
        assert response.status_code in {403, 404}, path
    assert client.get(f"{API}/erp/orders/invoice/{order_id}", headers=h).status_code == 404


def test_each_run_marks_the_account_so_others_get_a_turn(client, netsuite):
    from sqlalchemy import select

    from app.integrations import core
    from app.models.integration_hub import IntegrationConnection

    first, second = signup(client, "First Co"), signup(client, "Second Co")
    connect(client, first, netsuite)
    connect(client, second, netsuite)
    ids = [connection_row(first["org"]).id, connection_row(second["org"]).id]
    run_due(first["org"])

    async def order(db):
        return list((await db.execute(select(IntegrationConnection.id).where(IntegrationConnection.id.in_(ids)).order_by(core.scheduler_order()))).scalars())

    assert run_service(order) == [ids[1], ids[0]]  # the one just looked at goes to the back
    run_due(second["org"])
    assert run_service(order) == [ids[0], ids[1]]


def test_a_run_out_of_budget_keeps_its_place(client, netsuite, monkeypatch):
    from app.integrations.providers import accounting
    from app.integrations.providers import netsuite as erp

    monkeypatch.setattr(erp, "ITEM_PAGE", 2)
    for n in range(6):
        netsuite.add_item(str(40 + n), f"P-{n}", "NonInvtPart", price="1.00", modified=local_now() - timedelta(minutes=10 - n))
    ws = signup(client)
    connect(client, ws, netsuite)
    calls = {"n": 0}
    real = accounting.RUN_CALLS["netsuite"]
    # Enough calls for the invoice side and one page of items, then the budget runs out.
    monkeypatch.setitem(accounting.RUN_CALLS, "netsuite", 7)
    run_due(ws["org"])
    first = connection_row(ws["org"]).config["sync"]
    calls["n"] = len(products(ws["org"]))
    assert 0 < calls["n"] < 6 and first.get("items_cursor")
    monkeypatch.setitem(accounting.RUN_CALLS, "netsuite", real)
    for _ in range(3):
        run_due(ws["org"])
    assert sorted(p.sku for p in products(ws["org"])) == [f"P-{n}" for n in range(6)]
    assert len({(m, p) for m, p in netsuite.calls}) > 0
