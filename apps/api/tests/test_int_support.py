"""Help desk integrations: Zendesk, Freshdesk, Intercom, Help Scout and Jira Service
Management. Desk APIs are never called: every adapter's HTTP helper is replaced
by a fake that answers like the desk does.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Support#2026"
ZD_SECRET = "zendesk-signing-secret"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Help Desk Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@support.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Sam Owner", "email": email, "password": PASSWORD, "company_name": company})
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


def invite(client, ws: dict, role: str, name: str) -> dict:
    email = f"{name.split()[0].lower()}-{uuid.uuid4().hex[:6]}@support.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": name, "role": role}, headers=ws["headers"])
    assert invited.status_code in (200, 201), invited.text
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited.json()["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    return {"user_id": joined["user_id"], "headers": {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}}


def run_service(fn):
    from app.db.session import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            result = await fn(db)
            await db.commit()
            return result

    return asyncio.run(go())


def local_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.replace("/api/aiva/", "/api/v1/", 1)


class FakeHTTP:
    """Stands in for the help desk HTTP helper: routes (method, url regex) to answers."""

    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, Any, int, dict]] = []
        self.calls: list[tuple[str, str, dict]] = []

    def on(self, method: str, pattern: str, answer: Any, status: int = 200, headers: dict | None = None) -> None:
        self.routes.append((method, re.compile(pattern), answer, status, headers or {}))

    async def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        for route_method, pattern, answer, status, headers in reversed(self.routes):
            if route_method == method and pattern.search(url):
                body = answer(method, url, kwargs) if callable(answer) else answer
                if isinstance(body, httpx.Response):
                    return body
                if body is None:
                    return httpx.Response(status, headers=headers, request=httpx.Request(method, url))
                return httpx.Response(status, json=body, headers=headers, request=httpx.Request(method, url))
        raise AssertionError(f"Unexpected desk call: {method} {url}")

    def count(self, method: str, pattern: str) -> int:
        return sum(1 for m, u, _ in self.calls if m == method and re.search(pattern, u))

    def last(self, method: str, pattern: str) -> tuple[str, str, dict]:
        for call in reversed(self.calls):
            if call[0] == method and re.search(pattern, call[1]):
                return call
        raise AssertionError(f"No {method} call matching {pattern}")


@pytest.fixture()
def fake(monkeypatch):
    from app.integrations.providers import _hd_common, _hd_freshdesk, _hd_helpscout, _hd_intercom, _hd_jira, _hd_zendesk

    double = FakeHTTP()
    for module in (_hd_common, _hd_zendesk, _hd_freshdesk, _hd_intercom, _hd_helpscout, _hd_jira):
        monkeypatch.setattr(module, "http", double)
    _hd_common._CACHE.clear()
    _hd_common._WEBHOOK_HITS.clear()
    return double


def turn_on(client, ws: dict, key: str) -> None:
    response = client.put(f"{API}/integration-hub/{key}/toggle", json={"enabled": True}, headers=ws["headers"])
    assert response.status_code == 200, response.text


def contact_with(client, ws: dict, email: str, account_id: str | None = None) -> dict:
    body = {"first_name": "Cora", "last_name": "Customer", "email": email}
    if account_id:
        body["account_id"] = account_id
    response = client.post(f"{API}/contacts", json=body, headers=ws["headers"])
    assert response.status_code == 201, response.text
    return response.json()


def objects(org: str, key: str) -> list[dict]:
    from sqlalchemy import select

    from app.models.integration_hub import IntegrationObject

    async def go(db):
        rows = (await db.execute(select(IntegrationObject).where(IntegrationObject.organization_id == uuid.UUID(org), IntegrationObject.provider == key))).scalars().all()
        return [{"id": r.external_id, "status": r.status, "contact_id": str(r.contact_id) if r.contact_id else None, "account_id": str(r.account_id) if r.account_id else None,
                 "lead_id": str(r.lead_id) if r.lead_id else None, "case": str(r.record_id) if r.record_type == "case" else None, "email": r.email} for r in rows]

    return run_service(go)


# ---------------------------------------------------------------------------
# Zendesk
# ---------------------------------------------------------------------------

ZD = r"https://acme\.zendesk\.com/api/v2"


def zd_ticket(ticket_id: int, *, status: str = "open", requester_id: int = 501, subject: str = "Login broken", created: datetime | None = None) -> dict:
    return {"id": ticket_id, "subject": subject, "status": status, "priority": "high", "requester_id": requester_id, "description": "I can't sign in.",
            "created_at": (created or NOW).isoformat().replace("+00:00", "Z"), "updated_at": NOW.isoformat().replace("+00:00", "Z"), "via": {"channel": "email"}}


def zendesk_desk(fake: FakeHTTP, tickets: dict[int, dict], users: dict[int, dict]) -> None:
    fake.on("GET", rf"{ZD}/users/me\.json", {"user": {"id": 1, "name": "Ana Agent", "role": "admin"}})
    fake.on("POST", rf"{ZD}/webhooks$", {"webhook": {"id": "01WEBHOOK"}}, 201)
    fake.on("GET", rf"{ZD}/webhooks/01WEBHOOK/signing_secret", {"signing_secret": {"secret": ZD_SECRET, "algorithm": "SHA256"}})
    fake.on("POST", rf"{ZD}/triggers$", {"trigger": {"id": 77}}, 201)
    fake.on("DELETE", rf"{ZD}/(triggers|webhooks)/", None, 204)

    def one(method, url, kwargs):
        ticket_id = int(re.search(r"/tickets/(\d+)\.json", url).group(1))
        if ticket_id not in tickets:
            return httpx.Response(404, json={"error": "RecordNotFound"}, request=httpx.Request(method, url))
        return {"ticket": tickets[ticket_id], "users": list(users.values())}

    fake.on("GET", rf"{ZD}/tickets/\d+\.json", one)
    fake.on("GET", rf"{ZD}/tickets/\d+/comments/count\.json", {"count": {"value": 3}})

    def search(method, url, kwargs):
        email = kwargs["params"]["query"].split("requester:")[1]
        found = [t for t in tickets.values() if (users.get(t["requester_id"]) or {}).get("email") == email]
        return {"results": [{**t, "result_type": "ticket"} for t in found], "users": list(users.values())}

    fake.on("GET", rf"{ZD}/search\.json", search)

    def create(method, url, kwargs):
        body = kwargs["json"]["ticket"]
        new_id = 9000 + len(tickets)
        users.setdefault(600 + new_id, {"id": 600 + new_id, "email": body["requester"]["email"], "name": body["requester"].get("name")})
        tickets[new_id] = {**zd_ticket(new_id, status="new", requester_id=600 + new_id, subject=body["subject"]), "priority": body.get("priority")}
        return {"ticket": tickets[new_id]}

    fake.on("POST", rf"{ZD}/tickets\.json", create, 201)


def connect_zendesk(client, ws: dict, fake: FakeHTTP, tickets: dict, users: dict, token: str = "zd-token") -> dict:
    zendesk_desk(fake, tickets, users)
    turn_on(client, ws, "zendesk")
    saved = client.put(f"{API}/integration-hub/zendesk/credentials",
                       json={"values": {"subdomain": "https://acme.zendesk.com/", "email": "ana@acme.example", "api_token": token}}, headers=ws["headers"])
    assert saved.status_code == 200, saved.text
    return saved.json()


def zd_sign(body: bytes, stamp: str, secret: str = ZD_SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), stamp.encode() + body, hashlib.sha256).digest()).decode()


def test_zendesk_connect_creates_signed_webhook_and_trigger(client, fake):
    ws = signup(client)
    detail = connect_zendesk(client, ws, fake, {}, {})
    assert detail["status"] == "connected" and detail["connection"]["account_label"] == "acme.zendesk.com"
    assert detail["extra"]["webhook"]["status"] == "active" and detail["extra"]["webhook"]["signed"] is True
    trigger = fake.last("POST", r"/triggers$")[2]["json"]["trigger"]
    assert trigger["actions"][0]["value"][0] == "01WEBHOOK" and "{{ticket.id}}" in trigger["actions"][0]["value"][1]
    assert ZD_SECRET not in json.dumps(detail) and "zd-token" not in json.dumps(detail)
    assert fake.last("GET", r"/users/me")[2]["auth"] == ("ana@acme.example/token", "zd-token")
    # Disconnecting removes what AIVA created in Zendesk.
    assert client.delete(f"{API}/integration-hub/zendesk/connection", headers=ws["headers"]).status_code == 200
    assert fake.count("DELETE", r"/triggers/77") == 1 and fake.count("DELETE", r"/webhooks/01WEBHOOK") == 1


def test_zendesk_bad_credentials_and_member_limits(client, fake):
    ws = signup(client)
    turn_on(client, ws, "zendesk")
    fake.on("GET", rf"{ZD}/users/me\.json", {"user": {"id": None, "role": "end-user"}})
    bad = client.put(f"{API}/integration-hub/zendesk/credentials", json={"values": {"subdomain": "acme", "email": "x@acme.example", "api_token": "nope"}},
                     headers=ws["headers"])
    assert bad.status_code == 400
    weird = client.put(f"{API}/integration-hub/zendesk/credentials", json={"values": {"subdomain": "acme.evil.com/x", "email": "x@acme.example", "api_token": "t"}},
                       headers=ws["headers"])
    assert weird.status_code == 422
    member = invite(client, ws, "member", "Mel Member")
    assert client.post(f"{API}/helpdesk/zendesk/sync", headers=member["headers"]).status_code == 403
    assert client.put(f"{API}/integration-hub/zendesk/settings", json={"config": {"create_cases": True}}, headers=member["headers"]).status_code == 403


def test_zendesk_webhook_signature_and_matching(client, fake):
    ws = signup(client)
    account = client.post(f"{API}/accounts", json={"name": "Globex", "domain": "globex.example"}, headers=ws["headers"]).json()
    contact = contact_with(client, ws, "cora@globex.example", account["id"])
    users = {501: {"id": 501, "email": "Cora@Globex.example", "name": "Cora Customer"}, 502: {"id": 502, "email": "pat@globex.example", "name": "Pat"}}
    tickets = {101: zd_ticket(101), 102: zd_ticket(102, requester_id=502, subject="Invoice question")}
    detail = connect_zendesk(client, ws, fake, tickets, users)
    url = local_path(detail["connection"]["webhook_url"])

    body = json.dumps({"ticket_id": "101", "source": "aiva-trigger"}).encode()
    stamp = NOW.isoformat().replace("+00:00", "Z")
    bad = client.post(url, content=body, headers={"Content-Type": "application/json", "X-Zendesk-Webhook-Signature": "AAAA", "X-Zendesk-Webhook-Signature-Timestamp": stamp})
    assert bad.status_code == 401
    unsigned = client.post(url, content=body, headers={"Content-Type": "application/json"})
    assert unsigned.status_code == 401
    old = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    replay = client.post(url, content=body, headers={"Content-Type": "application/json", "X-Zendesk-Webhook-Signature": zd_sign(body, old),
                                                     "X-Zendesk-Webhook-Signature-Timestamp": old})
    assert replay.status_code == 401
    assert not objects(ws["org"], "zendesk")

    good = client.post(url, content=body, headers={"Content-Type": "application/json", "X-Zendesk-Webhook-Signature": zd_sign(body, stamp),
                                                   "X-Zendesk-Webhook-Signature-Timestamp": stamp})
    assert good.status_code == 200 and good.json()["updated"] == 1
    body2 = json.dumps({"ticket_id": "102"}).encode()
    client.post(url, content=body2, headers={"Content-Type": "application/json", "X-Zendesk-Webhook-Signature": zd_sign(body2, stamp),
                                             "X-Zendesk-Webhook-Signature-Timestamp": stamp})
    rows = {r["id"]: r for r in objects(ws["org"], "zendesk")}
    assert rows["101"]["contact_id"] == contact["id"] and rows["101"]["account_id"] == account["id"] and rows["101"]["email"] == "cora@globex.example"
    # No contact for Pat, but the company domain matches the account.
    assert rows["102"]["contact_id"] is None and rows["102"]["account_id"] == account["id"]

    # The account shows both tickets; the contact shows its own.
    on_account = client.get(f"{API}/helpdesk/records/account/{account['id']}", headers=ws["headers"]).json()
    assert {t["external_id"] for t in on_account["items"]} == {"101", "102"}
    assert on_account["contacts"][0]["email"] == "cora@globex.example"
    on_contact = client.get(f"{API}/helpdesk/records/contact/{contact['id']}", headers=ws["headers"]).json()
    assert [t["number"] for t in on_contact["items"]] == ["#101"] and on_contact["items"][0]["status_label"] == "Open"


def test_record_tickets_live_lookup_and_create(client, fake):
    ws = signup(client)
    contact = contact_with(client, ws, "dana@initech.example")
    users = {501: {"id": 501, "email": "dana@initech.example", "name": "Dana"}}
    tickets = {201: zd_ticket(201, status="pending")}
    connect_zendesk(client, ws, fake, tickets, users)

    first = client.get(f"{API}/helpdesk/records/contact/{contact['id']}", headers=ws["headers"]).json()
    assert [t["external_id"] for t in first["items"]] == ["201"] and first["items"][0]["status"] == "pending"
    assert fake.count("GET", r"/search\.json") == 1
    # Looked up live at most every couple of minutes.
    client.get(f"{API}/helpdesk/records/contact/{contact['id']}", headers=ws["headers"])
    assert fake.count("GET", r"/search\.json") == 1

    made = client.post(f"{API}/helpdesk/tickets", json={"provider": "zendesk", "record_type": "contact", "record_id": contact["id"], "subject": "Renewal paperwork",
                                                       "body": "Please send the W-9.", "priority": "urgent"}, headers=ws["headers"])
    assert made.status_code == 201, made.text
    ticket = made.json()["ticket"]
    assert ticket["contact_id"] == contact["id"] and ticket["priority"] == "urgent"
    sent = fake.last("POST", r"/tickets\.json")[2]["json"]["ticket"]
    assert sent["requester"]["email"] == "dana@initech.example" and sent["comment"]["public"] is False and "aiva" in sent["tags"]
    timeline = client.get(f"{API}/activities?contact_id={contact['id']}", headers=ws["headers"])
    assert timeline.status_code == 200, timeline.text
    assert any("Zendesk ticket" in a["subject"] and "Renewal paperwork" in a["subject"] for a in timeline.json())
    bad = client.post(f"{API}/helpdesk/tickets", json={"provider": "zendesk", "record_type": "contact", "record_id": contact["id"], "subject": "x", "body": "y",
                                                      "priority": "panic"}, headers=ws["headers"])
    assert bad.status_code == 422
    off = client.post(f"{API}/helpdesk/tickets", json={"provider": "freshdesk", "record_type": "contact", "record_id": contact["id"], "subject": "x", "body": "y"},
                      headers=ws["headers"])
    assert off.status_code == 409


def test_tickets_follow_record_visibility(client, fake):
    ws = signup(client, "Visible Co")
    role = client.post(f"{API}/admin/roles", json={"name": "Restricted rep", "permissions": ["records.read", "records.write"]}, headers=ws["headers"]).json()
    rep = invite(client, ws, role["key"], "Rhea Rep")
    account = client.post(f"{API}/accounts", json={"name": "Hidden Corp", "domain": "hidden.example"}, headers=ws["headers"]).json()
    contact_with(client, ws, "vip@hidden.example", account["id"])
    connect_zendesk(client, ws, fake, {301: zd_ticket(301)}, {501: {"id": 501, "email": "vip@hidden.example", "name": "VIP"}})
    assert client.get(f"{API}/helpdesk/records/account/{account['id']}", headers=ws["headers"]).json()["items"]
    assert client.put(f"{API}/org/visibility", json={"mode": "hierarchy"}, headers=ws["headers"]).status_code == 200
    assert client.get(f"{API}/helpdesk/records/account/{account['id']}", headers=rep["headers"]).status_code == 404
    # Members don't see the admin-only parts of the integration page.
    detail = client.get(f"{API}/integration-hub/zendesk", headers=rep["headers"]).json()
    assert detail["extra"].get("webhook_url") is None and "recent" not in detail["extra"]


def test_escalation_mirrors_status_and_can_resolve_case(client, fake):
    ws = signup(client)
    contact = contact_with(client, ws, "erin@umbrella.example")
    users = {501: {"id": 501, "email": "erin@umbrella.example", "name": "Erin"}}
    tickets: dict[int, dict] = {}
    detail = connect_zendesk(client, ws, fake, tickets, users)
    case = client.post(f"{API}/cases", json={"subject": "Data export fails", "description": "Export times out.", "priority": "high", "contact_id": contact["id"]},
                       headers=ws["headers"])
    assert case.status_code == 201, case.text
    case = case.json()

    escalated = client.post(f"{API}/helpdesk/cases/{case['id']}/escalate", json={"provider": "zendesk"}, headers=ws["headers"])
    assert escalated.status_code == 201, escalated.text
    item = escalated.json()["item"]
    ticket_id = int(item["external_id"])
    sent = fake.last("POST", r"/tickets\.json")[2]["json"]["ticket"]
    assert sent["subject"] == "Data export fails" and sent["priority"] == "high" and "CS-" in sent["comment"]["body"]
    again = client.post(f"{API}/helpdesk/cases/{case['id']}/escalate", json={"provider": "zendesk"}, headers=ws["headers"])
    assert again.status_code == 409

    # The ticket is solved in Zendesk: the case hears about it (comment), and with the setting on, resolves.
    assert client.put(f"{API}/integration-hub/zendesk/settings", json={"config": {"sync_case_status": True}}, headers=ws["headers"]).status_code == 200
    tickets[ticket_id]["status"] = "solved"
    body = json.dumps({"ticket_id": str(ticket_id)}).encode()
    stamp = NOW.isoformat().replace("+00:00", "Z")
    hook = client.post(local_path(detail["connection"]["webhook_url"]), content=body,
                       headers={"Content-Type": "application/json", "X-Zendesk-Webhook-Signature": zd_sign(body, stamp), "X-Zendesk-Webhook-Signature-Timestamp": stamp})
    assert hook.status_code == 200
    after = client.get(f"{API}/cases/{case['id']}", headers=ws["headers"]).json()
    assert after["status"] == "resolved"
    comments = " ".join(c.get("body", "") for c in after.get("comments", []))
    assert "is now Solved" in comments and "Escalated to Zendesk ticket" in comments

    linked = client.get(f"{API}/helpdesk/cases/{case['id']}?refresh=true", headers=ws["headers"]).json()
    assert linked["items"][0]["status"] == "solved" and linked["items"][0]["comment_count"] == 3
    gone = client.delete(f"{API}/helpdesk/cases/{case['id']}/links/{linked['items'][0]['link_id']}", headers=ws["headers"])
    assert gone.status_code == 204
    assert client.get(f"{API}/helpdesk/cases/{case['id']}", headers=ws["headers"]).json()["items"] == []


def test_link_existing_ticket_to_case(client, fake):
    ws = signup(client)
    users = {501: {"id": 501, "email": "finn@hooli.example", "name": "Finn"}}
    tickets = {404: zd_ticket(404)}
    connect_zendesk(client, ws, fake, tickets, users)
    case = client.post(f"{API}/cases", json={"subject": "Duplicate charge"}, headers=ws["headers"]).json()
    other = client.post(f"{API}/cases", json={"subject": "Another"}, headers=ws["headers"]).json()
    nothing = client.post(f"{API}/helpdesk/cases/{case['id']}/link", json={"provider": "zendesk", "reference": "not a ticket"}, headers=ws["headers"])
    assert nothing.status_code == 422
    linked = client.post(f"{API}/helpdesk/cases/{case['id']}/link", json={"provider": "zendesk", "reference": "https://acme.zendesk.com/agent/tickets/404"},
                         headers=ws["headers"])
    assert linked.status_code == 201, linked.text
    assert linked.json()["item"]["number"] == "#404"
    taken = client.post(f"{API}/helpdesk/cases/{other['id']}/link", json={"provider": "zendesk", "reference": "404"}, headers=ws["headers"])
    assert taken.status_code == 409
    missing = client.post(f"{API}/helpdesk/cases/{other['id']}/link", json={"provider": "zendesk", "reference": "999"}, headers=ws["headers"])
    assert missing.status_code == 404


def test_background_sync_cursor_and_new_cases(client, fake):
    from app.integrations.providers import helpdesk

    ws = signup(client)
    users = {501: {"id": 501, "email": "gail@stark.example", "name": "Gail"}}
    token = f"zd-{uuid.uuid4().hex[:8]}"
    connect_zendesk(client, ws, fake, {}, users, token=token)
    assert client.put(f"{API}/integration-hub/zendesk/settings", json={"config": {"create_cases": True}}, headers=ws["headers"]).status_code == 200
    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    pages = [
        {"tickets": [zd_ticket(1, created=NOW - timedelta(days=3)), zd_ticket(2, created=later)], "users": list(users.values()),
         "after_cursor": "CUR1", "end_of_stream": False},
        {"tickets": [zd_ticket(3, status="deleted"), zd_ticket(4, status="solved", created=later)], "users": [], "after_cursor": "CUR2",
         "end_of_stream": True},
    ]
    served: list[dict] = []

    def export(method, url, kwargs):
        # run_due serves every workspace; answer by cursor so only this test's pages matter.
        auth = kwargs.get("auth") or ("", "")
        if auth[1] != token:
            return {"tickets": [], "users": [], "after_cursor": None, "end_of_stream": True}
        served.append(kwargs["params"])
        return pages[1] if kwargs["params"].get("cursor") == "CUR1" else pages[0]

    fake.on("GET", rf"{ZD}/incremental/tickets/cursor\.json", export)
    handled = run_service(lambda db: helpdesk.run_due(db))
    assert handled >= 3
    assert "start_time" in served[0] and served[1]["cursor"] == "CUR1"
    rows = {r["id"]: r for r in objects(ws["org"], "zendesk")}
    assert set(rows) == {"1", "2", "4"}  # deleted tickets are skipped
    # Only the new, open ticket opened an AIVA case (not the backlog, not a solved one).
    assert rows["2"]["case"] and not rows["1"]["case"] and not rows["4"]["case"]
    # Synced a moment ago: the next run leaves it alone.
    def ours() -> int:
        return sum(1 for m, u, k in fake.calls if "/incremental/" in u and (k.get("auth") or ("", ""))[1] == token)

    calls = ours()
    run_service(lambda db: helpdesk.run_due(db))
    assert ours() == calls

    fake.on("GET", rf"{ZD}/incremental/tickets/cursor\.json", httpx.Response(429, headers={"Retry-After": "120"}, request=httpx.Request("GET", "https://x")))
    result = client.post(f"{API}/helpdesk/zendesk/sync", headers=ws["headers"])
    assert result.status_code == 200 and result.json()["stored"] == 0


# ---------------------------------------------------------------------------
# Freshdesk
# ---------------------------------------------------------------------------

FD = r"https://acme\.freshdesk\.com/api/v2"


def test_freshdesk_unsigned_webhook_rereads_and_optional_secret(client, fake):
    ws = signup(client)
    contact = contact_with(client, ws, "hal@wayne.example")
    fake.on("GET", rf"{FD}/agents/me", {"id": 1, "contact": {"name": "Fred Agent", "email": "fred@acme.example"}})
    fake.on("GET", rf"{FD}/ticket_fields", [{"name": "status", "choices": {"2": ["Open"], "3": ["Pending"], "6": ["Waiting on vendor"]}}])
    state = {"status": 2}
    fake.on("GET", rf"{FD}/tickets/55$", lambda m, u, k: {"id": 55, "subject": "Broken widget", "status": state["status"], "priority": 3,
                                                           "requester": {"email": "hal@wayne.example", "name": "Hal"}, "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()})
    turn_on(client, ws, "freshdesk")
    saved = client.put(f"{API}/integration-hub/freshdesk/credentials", json={"values": {"domain": "acme", "api_key": "fd-key"}}, headers=ws["headers"])
    assert saved.status_code == 200, saved.text
    assert fake.last("GET", r"/agents/me")[2]["auth"] == ("fd-key", "X")
    url = local_path(saved.json()["connection"]["webhook_url"])

    # The body is only a hint: AIVA reads ticket 55 back from Freshdesk.
    hook = client.post(url, json={"ticket_id": "55", "subject": "forged subject", "status": 5})
    assert hook.status_code == 200 and hook.json()["updated"] == 1
    row = objects(ws["org"], "freshdesk")[0]
    assert row["status"] == "open" and row["contact_id"] == contact["id"]

    secret = client.post(f"{API}/helpdesk/freshdesk/webhook-secret", headers=ws["headers"]).json()["secret"]
    assert client.post(url, json={"ticket_id": "55"}).status_code == 401
    assert client.post(url, json={"ticket_id": "55"}, headers={"X-AIVA-Webhook-Secret": "wrong"}).status_code == 401
    state["status"] = 6
    assert client.post(url, json={"ticket_id": "55"}, headers={"X-AIVA-Webhook-Secret": secret}).status_code == 200
    detail = client.get(f"{API}/integration-hub/freshdesk", headers=ws["headers"]).json()
    assert detail["extra"]["webhook"]["signed"] is True and secret not in json.dumps(detail)
    assert detail["extra"]["recent"][0]["status_label"] == "Waiting on vendor"
    # Odd input never breaks the endpoint.
    assert client.post(url, content=b"not json", headers={"Content-Type": "application/json", "X-AIVA-Webhook-Secret": secret}).status_code == 400
    assert client.post(url, json={"ticket_id": "55"}, headers={"X-AIVA-Webhook-Secret": "ünïcode".encode("latin-1")}).status_code in (400, 401)


# ---------------------------------------------------------------------------
# Intercom
# ---------------------------------------------------------------------------

IC = r"https://api\.eu\.intercom\.io"


def test_intercom_signed_webhooks_conversations_and_leads(client, fake):
    ws = signup(client)
    contact = contact_with(client, ws, "ivy@acme-co.example")
    fake.on("GET", rf"{IC}/me$", {"type": "admin", "id": "991", "name": "Ina Admin", "email": "ina@acme.example",
                                    "app": {"id_code": "abc123", "name": "Acme Support", "region": "EU"}})
    conversation = {"type": "conversation", "id": "123456", "title": None, "state": "open", "priority": "priority", "created_at": int(NOW.timestamp()),
                    "updated_at": int(NOW.timestamp()), "source": {"subject": "", "body": "<p>Need help with SSO</p>", "author": {"type": "user", "email": "ivy@acme-co.example", "name": "Ivy"}},
                    "contacts": {"contacts": [{"id": "c1"}]}, "conversation_parts": {"total_count": 4}}
    fake.on("GET", rf"{IC}/conversations/123456", conversation)
    fake.on("GET", rf"{IC}/contacts/lead-9", {"id": "lead-9", "role": "lead", "email": "newbie@prospect.example", "name": "Nia Newbie", "phone": "+1 512 555 0100",
                                               "companies": {"data": [{"name": "Prospect Inc"}]}})
    turn_on(client, ws, "intercom")
    # An EU workspace's token used against the US API: Intercom says where it lives.
    fake.on("GET", r"https://api\.intercom\.io/me$", {"type": "admin", "id": "991", "app": {"id_code": "abc123", "name": "Acme Support", "region": "EU"}})
    wrong_region = client.put(f"{API}/integration-hub/intercom/credentials", json={"values": {"access_token": "ic-token", "region": "us"}}, headers=ws["headers"])
    assert wrong_region.status_code == 422 and "EU" in wrong_region.json()["detail"]
    saved = client.put(f"{API}/integration-hub/intercom/credentials", json={"values": {"access_token": "ic-token", "region": "eu", "client_secret": "ic-secret"}},
                       headers=ws["headers"])
    assert saved.status_code == 200, saved.text
    assert fake.last("GET", r"/me$")[2]["headers"]["Intercom-Version"]
    url = local_path(saved.json()["connection"]["webhook_url"])

    def sign(body: bytes, secret: str = "ic-secret") -> str:
        return "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()

    body = json.dumps({"type": "notification_event", "topic": "conversation.user.created", "data": {"item": {"type": "conversation", "id": "123456"}}}).encode()
    assert client.post(url, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature": sign(body, "nope")}).status_code == 401
    assert client.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    ok = client.post(url, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature": sign(body)})
    assert ok.status_code == 200 and ok.json()["updated"] == 1
    rows = objects(ws["org"], "intercom")
    assert rows[0]["contact_id"] == contact["id"]
    # The live lookup searches Intercom too.
    fake.on("POST", rf"{IC}/contacts/search", {"data": [{"id": "c1"}]})
    fake.on("POST", rf"{IC}/conversations/search", {"conversations": [conversation]})
    items = client.get(f"{API}/helpdesk/records/contact/{contact['id']}?refresh=true", headers=ws["headers"]).json()
    item = next(t for t in items["items"] if t["provider"] == "intercom")
    assert item["priority"] == "high" and item["comment_count"] == 5 and item["url"].startswith("https://app.eu.intercom.com/a/apps/abc123/")

    ping = json.dumps({"type": "notification_event", "topic": "ping", "data": {"item": {}}}).encode()
    assert client.post(url, content=ping, headers={"Content-Type": "application/json", "X-Hub-Signature": sign(ping)}).json()["ignored"]

    lead_event = json.dumps({"type": "notification_event", "topic": "contact.lead.created", "data": {"item": {"type": "contact", "id": "lead-9"}}}).encode()
    off = client.post(url, content=lead_event, headers={"Content-Type": "application/json", "X-Hub-Signature": sign(lead_event)})
    assert off.json()["updated"] == 0
    assert client.put(f"{API}/integration-hub/intercom/settings", json={"config": {"create_leads": True}}, headers=ws["headers"]).status_code == 200
    on = client.post(url, content=lead_event, headers={"Content-Type": "application/json", "X-Hub-Signature": sign(lead_event)})
    assert on.json()["updated"] == 1
    leads = client.get(f"{API}/leads", headers=ws["headers"]).json()
    leads = leads if isinstance(leads, list) else leads.get("items", [])
    assert any(l.get("email") == "newbie@prospect.example" and l.get("source") == "Intercom" for l in leads)  # noqa: E741
    # The same contact arriving again doesn't create a second lead.
    assert client.post(url, content=lead_event, headers={"Content-Type": "application/json", "X-Hub-Signature": sign(lead_event)}).json()["updated"] == 0


# ---------------------------------------------------------------------------
# Help Scout
# ---------------------------------------------------------------------------

HS = r"https://api\.helpscout\.net/v2"


def test_help_scout_token_webhook_and_create(client, fake):
    ws = signup(client)
    contact = contact_with(client, ws, "jo@vandelay.example")
    tokens = {"n": 0}

    def token(method, url, kwargs):
        tokens["n"] += 1
        assert kwargs["data"]["grant_type"] == "client_credentials"
        return {"access_token": f"hs-token-{tokens['n']}", "expires_in": 172800}

    fake.on("POST", r"helpscout\.net/v2/oauth2/token", token)
    fake.on("GET", rf"{HS}/users/me", {"id": 7, "firstName": "Hugo", "lastName": "Scout", "email": "hugo@acme.example"})
    fake.on("GET", rf"{HS}/mailboxes", {"_embedded": {"mailboxes": [{"id": 11, "name": "Support", "email": "help@acme.example"}]}})
    fake.on("POST", rf"{HS}/webhooks$", None, 201, {"Resource-ID": "3141"})
    fake.on("DELETE", rf"{HS}/webhooks/3141", None, 204)
    convo = {"id": 777, "number": 1234, "subject": "Shipping delay", "status": "active", "mailboxId": 11, "threads": 2,
             "primaryCustomer": {"email": "jo@vandelay.example", "first": "Jo", "last": "V"}, "createdAt": NOW.isoformat(), "userUpdatedAt": NOW.isoformat(),
             "_links": {"web": {"href": "https://secure.helpscout.net/conversation/777/1234"}}}
    fake.on("GET", rf"{HS}/conversations/777", convo)
    fake.on("POST", rf"{HS}/conversations$", None, 201, {"Resource-ID": "777"})
    turn_on(client, ws, "help_scout")
    saved = client.put(f"{API}/integration-hub/help_scout/credentials", json={"values": {"app_id": "app-1", "app_secret": "app-secret"}}, headers=ws["headers"])
    assert saved.status_code == 200, saved.text
    webhook = fake.last("POST", r"/webhooks$")[2]["json"]
    secret = webhook["secret"]
    assert webhook["url"].endswith(saved.json()["connection"]["webhook_url"].rsplit("/", 1)[1]) and len(secret) <= 40
    assert saved.json()["extra"]["webhook"]["status"] == "active" and secret not in json.dumps(saved.json())
    url = local_path(saved.json()["connection"]["webhook_url"])

    body = json.dumps({"id": 777, "subject": "anything"}).encode()
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha1).digest()).decode()
    assert client.post(url, content=body, headers={"Content-Type": "application/json", "X-HelpScout-Signature": "bad"}).status_code == 401
    good = client.post(url, content=body, headers={"Content-Type": "application/json", "X-HelpScout-Signature": signature, "X-HelpScout-Event": "convo.created"})
    assert good.status_code == 200 and good.json()["updated"] == 1
    assert objects(ws["org"], "help_scout")[0]["contact_id"] == contact["id"]

    no_mailbox = client.post(f"{API}/helpdesk/tickets", json={"provider": "help_scout", "record_type": "contact", "record_id": contact["id"], "subject": "Hi", "body": "Hello"},
                             headers=ws["headers"])
    assert no_mailbox.status_code == 422
    made = client.post(f"{API}/helpdesk/tickets", json={"provider": "help_scout", "record_type": "contact", "record_id": contact["id"], "subject": "Hi", "body": "Hello",
                                                       "mailbox_id": "11"}, headers=ws["headers"])
    assert made.status_code == 201, made.text
    assert made.json()["ticket"]["number"] == "#1234"
    sent = fake.last("POST", r"/conversations$")[2]["json"]
    assert sent["mailboxId"] == 11 and sent["threads"][0]["type"] == "customer"
    # The token is reused while it's valid.
    assert tokens["n"] == 2  # one to validate, one stored for the connection


# ---------------------------------------------------------------------------
# Jira Service Management
# ---------------------------------------------------------------------------

JIRA = r"https://acme\.atlassian\.net"


def jira_issue(issue_id: str = "10001", key: str = "SUP-12", status: str = "Waiting for support", category: str = "indeterminate", project: str = "SUP") -> dict:
    return {"id": issue_id, "key": key, "fields": {"summary": "VPN access", "status": {"name": status, "statusCategory": {"key": category}},
                                                     "priority": {"name": "High"}, "created": "2026-10-01T10:00:00.000+0000", "updated": "2026-10-02T10:00:00.000+0000",
                                                     "reporter": {"emailAddress": "kim@cyberdyne.example", "displayName": "Kim"}, "project": {"key": project},
                                                     "comment": {"total": 2}}}


def test_jira_connect_escalate_and_signed_webhook(client, fake):
    ws = signup(client)
    contact = contact_with(client, ws, "kim@cyberdyne.example")
    fake.on("GET", rf"{JIRA}/rest/api/3/myself", {"accountId": "acc-agent", "displayName": "Jules Agent", "emailAddress": "jules@acme.example"})
    fake.on("GET", rf"{JIRA}/rest/servicedeskapi/servicedesk$", {"values": [{"id": "1", "projectName": "Support", "projectKey": "SUP"}]})
    fake.on("GET", rf"{JIRA}/rest/servicedeskapi/servicedesk/1/requesttype", {"values": [{"id": "10", "name": "Get IT help"}, {"id": "11", "name": "Report a bug"}]})
    fake.on("GET", rf"{JIRA}/rest/api/3/user/search", [])
    fake.on("POST", rf"{JIRA}/rest/servicedeskapi/customer$", {"accountId": "acc-kim"})
    fake.on("POST", rf"{JIRA}/rest/servicedeskapi/servicedesk/1/customer", None, 204)
    fake.on("POST", rf"{JIRA}/rest/servicedeskapi/request$", {"issueId": "10001", "issueKey": "SUP-12"}, 201)
    state = {"issue": jira_issue()}
    fake.on("GET", rf"{JIRA}/rest/api/3/issue/", lambda m, u, k: state["issue"])
    turn_on(client, ws, "jira_service_management")
    bad_site = client.put(f"{API}/integration-hub/jira_service_management/credentials",
                          json={"values": {"site": "jira.example.com", "email": "jules@acme.example", "api_token": "t"}}, headers=ws["headers"])
    assert bad_site.status_code == 422
    saved = client.put(f"{API}/integration-hub/jira_service_management/credentials",
                       json={"values": {"site": "acme", "email": "jules@acme.example", "api_token": "jira-token"}}, headers=ws["headers"])
    assert saved.status_code == 200, saved.text
    extra = saved.json()["extra"]
    assert extra["options"]["service_desk_id"] == "1" and extra["options"]["request_type_id"] == "10"
    assert client.put(f"{API}/integration-hub/jira_service_management/settings", json={"config": {"request_type_id": "11"}}, headers=ws["headers"]).status_code == 200

    case = client.post(f"{API}/cases", json={"subject": "VPN access", "contact_id": contact["id"]}, headers=ws["headers"]).json()
    escalated = client.post(f"{API}/helpdesk/cases/{case['id']}/escalate", json={"provider": "jira_service_management"}, headers=ws["headers"])
    assert escalated.status_code == 201, escalated.text
    assert escalated.json()["item"]["number"] == "SUP-12" and escalated.json()["item"]["status"] == "pending"
    request = fake.last("POST", r"/servicedeskapi/request$")[2]["json"]
    assert request["raiseOnBehalfOf"] == "acc-kim" and request["requestTypeId"] == "11" and request["serviceDeskId"] == "1"

    url = local_path(saved.json()["connection"]["webhook_url"])
    body = json.dumps({"webhookEvent": "jira:issue_updated", "issue": {"id": "10001", "key": "SUP-12"}}).encode()
    # No secret yet: accepted, but only the id is used and the issue is read back.
    state["issue"] = jira_issue(status="Resolved", category="done")
    assert client.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 200
    secret = client.post(f"{API}/helpdesk/jira_service_management/webhook-secret", headers=ws["headers"]).json()["secret"]
    assert client.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    signed = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(url, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature": signed}).status_code == 200
    after = client.get(f"{API}/cases/{case['id']}", headers=ws["headers"]).json()
    assert "is now Resolved" in " ".join(c.get("body", "") for c in after.get("comments", []))
    # Issues from other projects are ignored.
    state["issue"] = jira_issue(issue_id="20002", key="OPS-1", project="OPS")
    body = json.dumps({"issue": {"id": "20002"}}).encode()
    signed = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(url, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature": signed}).json()["updated"] == 0


def test_desks_listing_and_plan_gate(client, fake):
    ws = signup(client)
    desks = client.get(f"{API}/helpdesk/desks", headers=ws["headers"]).json()["desks"]
    assert {d["key"] for d in desks} == {"zendesk", "freshdesk", "intercom", "help_scout", "jira_service_management"}
    assert not any(d["ready"] for d in desks)
