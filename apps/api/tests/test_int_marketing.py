"""Email marketing integrations: Mailchimp, ActiveCampaign, Brevo, Constant Contact, Klaviyo.

No provider is ever called: ``_em_http.send`` (key-based tools) and
``core.request`` (Constant Contact's OAuth) are replaced by a fake that answers
like the providers do and records every call.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Marketing#2026"
# Built at runtime so the fake key never looks like a real one to secret scanners.
MC_KEY = "0123456789abcdef" * 2 + "-us21"
MC = "https://us21.api.mailchimp.com/3.0"
AC = "https://acme.api-us1.com/api/3"
BREVO = "https://api.brevo.com/v3"
KLAVIYO = "https://a.klaviyo.com/api"
CC = "https://api.cc.email/v3"
CC_LIST = "02ec29a8-13c2-11ef-adba-6fa8f4277c8b"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Marketing Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@mkt.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Mo Owner", "email": email, "password": PASSWORD, "company_name": company})
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


def mc_hash(email: str) -> str:
    return hashlib.md5(email.lower().encode()).hexdigest()


class Fake:
    """Answers provider calls from registered routes (newest route wins) and records them."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern[str], Any]] = []
        self.calls: list[dict[str, Any]] = []

    def on(self, method: str, pattern: str, handler: Any) -> None:
        self.routes.insert(0, (method, re.compile(pattern), handler))

    def find(self, method: str, fragment: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and fragment in c["url"]]

    def reset(self) -> None:
        self.calls.clear()

    def respond(self, method: str, url: str, kwargs: dict[str, Any]) -> httpx.Response:
        call = {"method": method, "url": url, "json": kwargs.get("json"), "params": kwargs.get("params") or {}, "headers": kwargs.get("headers") or {}, "auth": kwargs.get("auth")}
        self.calls.append(call)
        for route_method, pattern, handler in self.routes:
            if route_method == method and pattern.search(url):
                result = handler(call) if callable(handler) else handler
                break
        else:
            result = (404, {"detail": f"fake has no route for {method} {url}"})
        status, body = result if isinstance(result, tuple) else (200, result)
        request = httpx.Request(method, url)
        if body is None:
            return httpx.Response(status, content=b"", request=request)
        return httpx.Response(status, json=body, request=request)

    async def send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return self.respond(method, url, kwargs)

    async def core_request(self, db, connection, method, url, *, token=None, retry_auth=True, timeout=25, **kwargs):
        from app.integrations.core import IntegrationError

        response = self.respond(method, url, kwargs)
        if response.status_code >= 400:
            raise IntegrationError(f"Constant Contact said: {response.text[:100]}", 400)
        return response


@pytest.fixture
def fake(monkeypatch) -> Fake:
    from app.integrations import core
    from app.integrations.providers import _em_http

    provider = Fake()
    monkeypatch.setattr(_em_http, "send", provider.send)
    monkeypatch.setattr(core, "request", provider.core_request)
    return provider


def contact(client, h, first: str, email: str | None, **extra) -> dict:
    body = {"first_name": first, "last_name": "Tester", **({"email": email} if email else {}), **extra}
    response = client.post(f"{API}/contacts", json=body, headers=h)
    assert response.status_code in (200, 201), response.text
    return response.json()


def lead(client, h, first: str, email: str, **extra) -> dict:
    response = client.post(f"{API}/leads", json={"first_name": first, "last_name": "Lead", "email": email, **extra}, headers=h)
    assert response.status_code in (200, 201), response.text
    return response.json()


def suppressed(client, h, email: str) -> dict | None:
    rows = client.get(f"{API}/suppression", params={"search": email}, headers=h).json()
    items = rows.get("items", rows) if isinstance(rows, dict) else rows
    return next((r for r in items if r.get("value") == email), None)


def opted_out(org: str, email: str) -> bool:
    from sqlalchemy import select

    from app.models.contact import Contact
    from app.models.lead import Lead

    async def go(db):
        values = []
        for model in (Contact, Lead):
            values += list((await db.execute(select(model.email_opt_out).where(model.organization_id == uuid.UUID(org), model.email == email))).scalars())
        return values

    found = run_service(go)
    return bool(found) and all(found)


def links(org: str, provider: str) -> dict[str, str]:
    from sqlalchemy import select

    from app.models.integration_hub import IntegrationRecordLink

    async def go(db):
        rows = (await db.execute(select(IntegrationRecordLink).where(IntegrationRecordLink.organization_id == uuid.UUID(org), IntegrationRecordLink.provider == provider))).scalars()
        return {r.external_id: r.status for r in rows}

    return run_service(go)


def timeline(client, h, contact_id: str) -> list[str]:
    response = client.get(f"{API}/contacts/{contact_id}/activities", headers=h)
    assert response.status_code == 200, response.text
    data = response.json()
    items = data.get("items", []) if isinstance(data, dict) else data
    return [a["subject"] for a in items]


def webhook_path(client, h, key: str) -> str:
    url = client.get(f"{API}/integration-hub/{key}", headers=h).json()["connection"]["webhook_url"]
    return urlparse(url).path.replace("/api/aiva/", "/api/v1/", 1)


# ---------------------------------------------------------------------------
# Mailchimp
# ---------------------------------------------------------------------------


def mailchimp_routes(fake: Fake, *, member_status: dict[str, str] | None = None, changed: dict[str, list[str]] | None = None) -> dict[str, Any]:
    members = member_status if member_status is not None else {}
    changed = changed if changed is not None else {"unsubscribed": [], "cleaned": []}
    fake.on("GET", rf"{re.escape(MC)}/$", {"account_id": "acc-1", "account_name": "Acme Marketing", "email": "owner@acme.example"})
    fake.on("GET", rf"{re.escape(MC)}/ping$", {"health_status": "Everything's Chimpy!"})
    fake.on("GET", rf"{re.escape(MC)}/lists$", {"lists": [{"id": "aud1", "name": "Newsletter", "stats": {"member_count": 10}}]})
    fake.on("GET", r"/lists/aud1/merge-fields$", {"merge_fields": [{"tag": "FNAME", "name": "First"}, {"tag": "LNAME", "name": "Last"}, {"tag": "COMPANY", "name": "Company"}]})

    def put_member(call):
        email = call["json"]["email_address"]
        return {"id": mc_hash(email), "email_address": email, "status": members.get(email, "subscribed")}

    fake.on("PUT", r"/lists/aud1/members/[0-9a-f]{32}$", put_member)
    fake.on("POST", r"/lists/aud1/members/[0-9a-f]{32}/tags$", (204, None))
    fake.on("PATCH", r"/lists/aud1/members/[0-9a-f]{32}$", lambda call: {"status": "unsubscribed"})

    def get_member(call):
        hashed = call["url"].rsplit("/", 1)[1]
        email = next((e for e in members if mc_hash(e) == hashed), None)
        if email is None:
            return (404, {"detail": "Resource Not Found"})
        return {"id": hashed, "email_address": email, "status": members[email], "tags": [{"name": "AIVA"}], "stats": {"avg_open_rate": 0.5}}

    fake.on("GET", r"/lists/aud1/members/[0-9a-f]{32}$", get_member)
    fake.on("GET", r"/lists/aud1/members/[0-9a-f]{32}/activity-feed$", {"activity": [{"activity_type": "open", "created_at_timestamp": "2026-10-20T10:00:00+00:00", "campaign_title": "October news"}]})
    fake.on("GET", r"/lists/aud1/members$", lambda call: {"members": [{"email_address": e, "status": call["params"]["status"], "last_changed": "2026-10-20T10:00:00+00:00"} for e in changed.get(call["params"]["status"], [])]})
    sent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    fake.on("GET", rf"{re.escape(MC)}/campaigns$", {"campaigns": [{"id": "c1", "settings": {"title": "October news", "subject_line": "News"}, "send_time": sent, "emails_sent": 100, "report_summary": {"unique_opens": 40, "open_rate": 0.4, "subscriber_clicks": 10, "click_rate": 0.1}}]})
    fake.on("GET", r"/reports/c1/email-activity$", lambda call: {"emails": [{"email_address": e, "activity": [{"action": "open", "timestamp": sent}, {"action": "click", "timestamp": sent, "url": "https://acme.example/offer"}]} for e in members.get("_engaged", "").split(",") if e]})
    return {"members": members, "changed": changed}


def connect_mailchimp(client, h, fake: Fake) -> None:
    assert client.put(f"{API}/integration-hub/mailchimp/toggle", json={"enabled": True}, headers=h).status_code == 200
    bad = client.put(f"{API}/integration-hub/mailchimp/credentials", json={"values": {"api_key": "not-a-key"}}, headers=h)
    assert bad.status_code == 422
    saved = client.put(f"{API}/integration-hub/mailchimp/credentials", json={"values": {"api_key": MC_KEY}}, headers=h)
    assert saved.status_code == 200, saved.text
    detail = saved.json()
    assert detail["status"] == "connected" and detail["connection"]["account_label"] == "Acme Marketing"
    assert MC_KEY not in str(detail)
    # Basic auth with the key, against the key's data center.
    assert fake.calls[-1]["auth"] == ("aiva", MC_KEY) and fake.calls[-1]["url"].startswith("https://us21.api.mailchimp.com/")


def test_mailchimp_sync_and_do_not_contact_both_ways(client, fake):
    ws = signup(client, "Chimp Co")
    h = ws["headers"]
    mailchimp_routes(fake, member_status={"carol@chimp.example": "unsubscribed", "_engaged": "alice@chimp.example"},
                             changed={"unsubscribed": ["dave@chimp.example"], "cleaned": ["ghost@nowhere.example"]})
    connect_mailchimp(client, h, fake)

    alice = contact(client, h, "Alice", "alice@chimp.example")
    contact(client, h, "Carol", "carol@chimp.example")
    contact(client, h, "Dave", "dave@chimp.example")
    contact(client, h, "Blocked", "blocked@chimp.example")
    contact(client, h, "NoEmail", None)
    lead(client, h, "Bob", "bob@chimp.example")
    assert client.post(f"{API}/suppression", json={"values": ["blocked@chimp.example"]}, headers=h).status_code == 201

    # Settings are checked.
    assert client.put(f"{API}/integration-hub/mailchimp/settings", json={"config": {"field_map": {"COMPANY": "password"}}}, headers=h).status_code == 422
    assert client.put(f"{API}/integration-hub/mailchimp/settings", json={"config": {"status_for_new": "maybe"}}, headers=h).status_code == 422
    lists = client.get(f"{API}/email-marketing/mailchimp/lists", headers=h)
    assert lists.status_code == 200 and lists.json()["items"][0]["id"] == "aud1"
    fields = client.get(f"{API}/email-marketing/mailchimp/fields", params={"list_id": "aud1"}, headers=h).json()["items"]
    assert [f["key"] for f in fields] == ["COMPANY"]
    saved = client.put(
        f"{API}/integration-hub/mailchimp/settings",
        json={"config": {"list_id": "aud1", "list_name": "Newsletter", "tags": ["AIVA"], "field_map": {"COMPANY": "company"}, "record_types": ["contact", "lead"]}},
        headers=h,
    )
    assert saved.status_code == 200, saved.text

    fake.reset()
    result = client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h)
    assert result.status_code == 200, result.text
    assert result.json()["ok"], result.json()

    put_emails = {c["json"]["email_address"] for c in fake.find("PUT", "/lists/aud1/members/")}
    # Dave unsubscribed in Mailchimp (pulled first), Blocked is on AIVA's list: neither is sent.
    assert put_emails == {"alice@chimp.example", "carol@chimp.example", "bob@chimp.example"}
    body = next(c["json"] for c in fake.find("PUT", "/lists/aud1/members/") if c["json"]["email_address"] == "alice@chimp.example")
    assert body["status_if_new"] == "subscribed" and body["merge_fields"]["FNAME"] == "Alice" and "COMPANY" in body["merge_fields"]
    assert "tags" not in body and fake.find("POST", "/tags")

    # Tool → AIVA: Dave (polled), Ghost (cleaned) and Carol (already unsubscribed in Mailchimp).
    assert suppressed(client, h, "dave@chimp.example")["reason"] == "unsubscribed"
    assert suppressed(client, h, "ghost@nowhere.example")["reason"] == "bounced"
    assert suppressed(client, h, "carol@chimp.example") is not None
    assert opted_out(ws["org"], "dave@chimp.example") and opted_out(ws["org"], "carol@chimp.example")
    status = links(ws["org"], "mailchimp")
    assert status["alice@chimp.example"] == "linked" and status["carol@chimp.example"] == "unsubscribed"

    # Opens and clicks of synced people land on their timeline, once.
    subjects = timeline(client, h, alice["id"])
    assert sum("Opened “October news”" in s for s in subjects) == 1 and sum("Clicked a link" in s for s in subjects) == 1

    detail = client.get(f"{API}/integration-hub/mailchimp", headers=h).json()
    assert detail["extra"]["sync"]["counts"]["in_audience"] == 2 and detail["extra"]["campaigns"][0]["title"] == "October news"
    assert detail["extra"]["webhook"]["active"] is False  # AIVA's address isn't public in tests: polling instead

    # Nothing changed: nothing is sent again.
    fake.reset()
    client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h)
    assert fake.find("PUT", "/lists/aud1/members/") == []

    # AIVA → tool: Alice joins the do-not-contact list, so she's unsubscribed in Mailchimp.
    client.post(f"{API}/suppression", json={"values": ["alice@chimp.example"]}, headers=h)
    fake.reset()
    client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h)
    patched = fake.find("PATCH", f"/members/{mc_hash('alice@chimp.example')}")
    assert patched and patched[0]["json"] == {"status": "unsubscribed"}
    assert links(ws["org"], "mailchimp")["alice@chimp.example"] == "unsubscribed"
    assert not fake.find("PUT", "/lists/aud1/members/")


def test_mailchimp_webhook_is_checked_with_the_api(client, fake):
    ws = signup(client, "Hook Co")
    h = ws["headers"]
    state = mailchimp_routes(fake, member_status={"frank@hook.example": "subscribed"})
    connect_mailchimp(client, h, fake)
    client.put(f"{API}/integration-hub/mailchimp/settings", json={"config": {"list_id": "aud1", "list_name": "Newsletter"}}, headers=h)
    contact(client, h, "Frank", "frank@hook.example")
    path = webhook_path(client, h, "mailchimp")

    assert client.get(path).status_code == 200  # Mailchimp checks the URL with a GET
    form = {"type": "unsubscribe", "data[email]": "frank@hook.example", "data[list_id]": "aud1"}
    # A forged claim: Mailchimp still says subscribed, so nothing happens.
    assert client.post(path, data=form).status_code == 200
    assert suppressed(client, h, "frank@hook.example") is None
    # Another audience: ignored without asking Mailchimp.
    fake.reset()
    client.post(path, data={**form, "data[list_id]": "other"})
    assert fake.calls == []
    # Real unsubscribe.
    state["members"]["frank@hook.example"] = "unsubscribed"
    assert client.post(path, data=form).status_code == 200
    assert suppressed(client, h, "frank@hook.example")["reason"] == "unsubscribed"
    assert opted_out(ws["org"], "frank@hook.example")
    assert client.post(path.rsplit("/", 1)[0] + "/not-the-token", data=form).status_code == 404
    # Switched off: the hub ignores webhooks.
    client.put(f"{API}/integration-hub/mailchimp/toggle", json={"enabled": False}, headers=h)
    assert client.post(path, data=form).json().get("ignored")


# ---------------------------------------------------------------------------
# ActiveCampaign
# ---------------------------------------------------------------------------


def test_activecampaign_bulk_import_and_url_safety(client, fake):
    ws = signup(client, "AC Co")
    h = ws["headers"]
    fake.on("GET", rf"{re.escape(AC)}/users/me$", {"user": {"email": "ops@acme.example", "username": "ops"}})
    fake.on("GET", rf"{re.escape(AC)}/lists$", {"lists": [{"id": "7", "name": "Customers"}]})
    fake.on("GET", rf"{re.escape(AC)}/fields$", {"fields": [{"id": "3", "title": "Industry", "perstag": "INDUSTRY"}]})
    fake.on("POST", rf"{re.escape(AC)}/import/bulk_import$", {"success": 1, "queued_contacts": 1, "batchId": "batch-1"})
    fake.on("GET", rf"{re.escape(AC)}/contacts$", lambda call: {"contacts": [{"id": "55", "email": "gone@ac.example", "udate": "2026-10-20T10:00:00-05:00"}]} if call["params"].get("status") == "2" else {"contacts": []})
    fake.on("GET", rf"{re.escape(AC)}/campaigns$", {"campaigns": []})
    client.put(f"{API}/integration-hub/activecampaign/toggle", json={"enabled": True}, headers=h)
    for url in ("https://169.254.169.254", "https://evil.example.com", "http://acme.api-us1.com", "https://acme.api-us1.com:8443"):
        refused = client.put(f"{API}/integration-hub/activecampaign/credentials", json={"values": {"api_url": url, "api_key": "k" * 40}}, headers=h)
        assert refused.status_code == 422, url
    assert not fake.calls  # nothing was called for unsafe URLs
    saved = client.put(f"{API}/integration-hub/activecampaign/credentials", json={"values": {"api_url": "acme.api-us1.com/", "api_key": "k" * 40}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert fake.calls[-1]["headers"].get("Api-Token") == "k" * 40
    client.put(f"{API}/integration-hub/activecampaign/settings", json={"config": {"list_id": "7", "list_name": "Customers", "tags": ["CRM"], "field_map": {"3": "industry"}, "lead_statuses": ["new", "qualified"]}}, headers=h)
    contact(client, h, "Gina", "gina@ac.example")
    contact(client, h, "Gone", "gone@ac.example")
    lead(client, h, "Unqualified", "unq@ac.example", status="unqualified")

    fake.reset()
    assert client.post(f"{API}/email-marketing/activecampaign/sync", json={}, headers=h).json()["ok"]
    imports = fake.find("POST", "/import/bulk_import")
    people = [c for call in imports for c in call["json"]["contacts"]]
    assert {p["email"] for p in people} == {"gina@ac.example"}  # gone unsubscribed (polled); unqualified lead filtered out
    gina = people[0]
    assert gina["subscribe"] == [{"listid": 7}] and gina["tags"] == ["CRM"] and gina["fields"] == [{"id": 3, "value": ""}]
    assert suppressed(client, h, "gone@ac.example") is not None


# ---------------------------------------------------------------------------
# Brevo
# ---------------------------------------------------------------------------


def test_brevo_import_webhook_and_engagement(client, fake):
    ws = signup(client, "Brevo Co")
    h = ws["headers"]
    blacklisted: dict[str, bool] = {}
    fake.on("GET", rf"{re.escape(BREVO)}/account$", {"email": "team@brevo.example", "companyName": "Brevo Co"})
    fake.on("GET", rf"{re.escape(BREVO)}/contacts/lists$", {"lists": [{"id": 4, "name": "Main"}]})
    fake.on("POST", rf"{re.escape(BREVO)}/contacts/import$", (202, {"processId": 78}))
    fake.on("GET", rf"{re.escape(BREVO)}/contacts$", {"contacts": [], "count": 0})
    fake.on("GET", rf"{re.escape(BREVO)}/emailCampaigns$", {"campaigns": []})
    fake.on("GET", rf"{re.escape(BREVO)}/contacts/[^/]+/campaignStats$", {"opened": []})
    fake.on("GET", rf"{re.escape(BREVO)}/contacts/[^/]+$", lambda call: {"email": "hana@brevo.example", "emailBlacklisted": blacklisted.get("hana", False), "listIds": [4], "listUnsubscribed": []})
    client.put(f"{API}/integration-hub/brevo/toggle", json={"enabled": True}, headers=h)
    assert client.put(f"{API}/integration-hub/brevo/credentials", json={"values": {"api_key": "xkeysib-" + "a" * 40}}, headers=h).status_code == 200
    # Brevo has no tags or double opt-in statuses here.
    assert client.put(f"{API}/integration-hub/brevo/settings", json={"config": {"tags": ["X"]}}, headers=h).status_code == 422
    assert client.put(f"{API}/integration-hub/brevo/settings", json={"config": {"status_for_new": "pending"}}, headers=h).status_code == 422
    client.put(f"{API}/integration-hub/brevo/settings", json={"config": {"list_id": "4", "list_name": "Main", "field_map": {"COMPANY": "company"}}}, headers=h)
    hana = contact(client, h, "Hana", "hana@brevo.example")

    fake.reset()
    client.post(f"{API}/email-marketing/brevo/sync", json={}, headers=h)
    imported = fake.find("POST", "/contacts/import")
    assert imported and imported[0]["json"]["listIds"] == [4] and imported[0]["json"]["jsonBody"][0]["attributes"]["FIRSTNAME"] == "Hana"
    assert imported[0]["json"]["updateExistingContacts"] is True and imported[0]["headers"]["api-key"].startswith("xkeysib-")

    path = webhook_path(client, h, "brevo")
    # Open: goes on the timeline (no API check needed).
    client.post(path, json={"event": "opened", "email": "hana@brevo.example", "camp_id": 12, "campaign name": "Spring sale"})
    client.post(path, json={"event": "opened", "email": "hana@brevo.example", "camp_id": 12, "campaign name": "Spring sale"})
    assert sum("Spring sale" in s for s in timeline(client, h, hana["id"])) == 1
    # Unsubscribe claim not confirmed by Brevo: ignored. Confirmed: suppressed.
    client.post(path, json={"event": "unsubscribe", "email": "hana@brevo.example"})
    assert suppressed(client, h, "hana@brevo.example") is None
    blacklisted["hana"] = True
    client.post(path, json={"event": "unsubscribe", "email": "hana@brevo.example"})
    assert suppressed(client, h, "hana@brevo.example") is not None
    assert client.post(path, content=b"x" * (300 * 1024), headers={"content-type": "application/json"}).status_code == 413


# ---------------------------------------------------------------------------
# Klaviyo
# ---------------------------------------------------------------------------


def test_klaviyo_subscribe_mode_skips_unsubscribed_and_sends_events(client, fake):
    ws = signup(client, "Klaviyo Co")
    h = ws["headers"]
    fake.on("GET", rf"{re.escape(KLAVIYO)}/accounts$", {"data": [{"id": "acct", "attributes": {"contact_information": {"organization_name": "Kla Co"}}}]})
    fake.on("GET", rf"{re.escape(KLAVIYO)}/lists$", {"data": [{"id": "Lst1", "attributes": {"name": "VIP"}}], "links": {"next": None}})
    fake.on("POST", rf"{re.escape(KLAVIYO)}/profile-bulk-import-jobs$", (202, {"data": {"id": "job1"}}))
    fake.on("POST", rf"{re.escape(KLAVIYO)}/profile-subscription-bulk-create-jobs$", (202, None))
    fake.on("POST", rf"{re.escape(KLAVIYO)}/events$", (202, None))
    fake.on("GET", rf"{re.escape(KLAVIYO)}/campaigns$", {"data": []})

    def profiles(call):
        if "any(email" in call["params"].get("filter", ""):
            return {"data": [{"id": "P1", "attributes": {"email": "ivy@kla.example", "subscriptions": {"email": {"marketing": {"consent": "UNSUBSCRIBED"}}}}}]}
        return {"data": [], "links": {"next": None}}

    fake.on("GET", rf"{re.escape(KLAVIYO)}/profiles$", profiles)
    client.put(f"{API}/integration-hub/klaviyo/toggle", json={"enabled": True}, headers=h)
    assert client.put(f"{API}/integration-hub/klaviyo/credentials", json={"values": {"api_key": "wrong"}}, headers=h).status_code == 422
    assert client.put(f"{API}/integration-hub/klaviyo/credentials", json={"values": {"api_key": "pk_" + "b" * 30}}, headers=h).status_code == 200
    assert fake.calls[-1]["headers"]["Authorization"] == "Klaviyo-API-Key pk_" + "b" * 30 and fake.calls[-1]["headers"]["revision"]
    client.put(f"{API}/integration-hub/klaviyo/settings", json={"config": {"list_id": "Lst1", "list_name": "VIP", "subscribe_mode": "subscribe", "events": ["contact_created"], "record_types": ["contact"]}}, headers=h)
    contact(client, h, "Ivy", "ivy@kla.example", phone="+14695550100")
    contact(client, h, "Jon", "jon@kla.example", phone="214 555 0100")

    fake.reset()
    client.post(f"{API}/email-marketing/klaviyo/sync", json={}, headers=h)
    job = fake.find("POST", "/profile-bulk-import-jobs")[0]["json"]["data"]
    imported = {p["attributes"]["email"]: p["attributes"] for p in job["attributes"]["profiles"]["data"]}
    assert set(imported) == {"ivy@kla.example", "jon@kla.example"} and job["relationships"]["lists"]["data"][0]["id"] == "Lst1"
    assert imported["ivy@kla.example"]["phone_number"] == "+14695550100" and "phone_number" not in imported["jon@kla.example"]
    subscribed = [p["attributes"]["email"] for call in fake.find("POST", "/profile-subscription-bulk-create-jobs") for p in call["json"]["data"]["attributes"]["profiles"]["data"]]
    assert subscribed == ["jon@kla.example"]  # Ivy unsubscribed in Klaviyo: never re-subscribed
    assert suppressed(client, h, "ivy@kla.example") is not None

    # CRM events start from when they were turned on; a new contact is sent next run.
    contact(client, h, "Kim", "kim@kla.example")
    fake.reset()
    client.post(f"{API}/email-marketing/klaviyo/sync", json={}, headers=h)
    events = fake.find("POST", "/events")
    assert len(events) == 1
    event = events[0]["json"]["data"]["attributes"]
    assert event["metric"]["data"]["attributes"]["name"] == "AIVA Contact Created" and event["profile"]["data"]["attributes"]["email"] == "kim@kla.example"
    assert event["unique_id"].startswith("aiva-contact_created-")


# ---------------------------------------------------------------------------
# Constant Contact (OAuth)
# ---------------------------------------------------------------------------


def test_constant_contact_oauth_import_and_unsubscribe(client, fake, monkeypatch):
    from app.integrations import core, identity

    ws = signup(client, "CC Co")
    h = ws["headers"]
    client.put(f"{API}/integration-hub/constant_contact/toggle", json={"enabled": True}, headers=h)
    client.put(f"{API}/integration-hub/apps/constant_contact", json={"client_id": "cc-client", "client_secret": "cc-secret"}, headers=h)
    started = client.post(f"{API}/integration-hub/constant_contact/connect", json={}, headers=h)
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorize_url"]).query)
    assert "contact_data" in query["scope"][0] and query["code_challenge_method"] == ["S256"]

    async def fake_token(family, app_creds, data):
        return {"access_token": "cc-access", "refresh_token": "cc-refresh", "expires_in": 86400}

    async def fake_identify(family, key, tokens, app_creds):
        return {"label": "CC Org", "external_id": "acct-1"}

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": query["state"][0], "code": "c"}, headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert "connected=constant_contact" in back.headers["location"]

    contacts_db = {"lee@cc.example": {"contact_id": "11111111-2222-3333-4444-555555555555", "email_address": {"address": "lee@cc.example", "permission_to_send": "implicit"},
                                       "first_name": "Lee", "list_memberships": [CC_LIST], "created_at": "2026-01-01T00:00:00Z"}}
    fake.on("GET", rf"{re.escape(CC)}/contact_lists$", {"lists": [{"list_id": CC_LIST, "name": "Customers", "membership_count": 3}]})
    fake.on("POST", rf"{re.escape(CC)}/activities/contacts_json_import$", {"activity_id": "act-1", "state": "initialized"})
    fake.on("GET", rf"{re.escape(CC)}/contacts$", lambda call: {"contacts": [contacts_db[call["params"]["email"]]] if call["params"].get("email") in contacts_db else []} if "email" in call["params"] else {"contacts": []})
    fake.on("PUT", rf"{re.escape(CC)}/contacts/[0-9a-f-]{{36}}$", lambda call: call["json"])
    fake.on("GET", rf"{re.escape(CC)}/reports/summary_reports/email_campaign_summaries$", {"bulk_email_campaign_summaries": []})
    fake.on("GET", rf"{re.escape(CC)}/emails$", {"campaigns": []})
    assert client.get(f"{API}/email-marketing/constant_contact/lists", headers=h).json()["items"][0]["id"] == CC_LIST
    client.put(f"{API}/integration-hub/constant_contact/settings", json={"config": {"list_id": CC_LIST, "list_name": "Customers"}}, headers=h)
    lee = contact(client, h, "Lee", "lee@cc.example", job_title="CTO")

    fake.reset()
    client.post(f"{API}/email-marketing/constant_contact/sync", json={}, headers=h)
    imported = fake.find("POST", "/activities/contacts_json_import")[0]["json"]
    assert imported["list_ids"] == [CC_LIST] and imported["import_data"][0] == {"email": "lee@cc.example", "first_name": "Lee", "last_name": "Tester", "job_title": "CTO"}

    # Unsubscribe from the record: AIVA's list and Constant Contact (re-saved with permission unsubscribed).
    fake.reset()
    done = client.post(f"{API}/email-marketing/records/contact/{lee['id']}/unsubscribe", headers=h)
    assert done.status_code == 200, done.text
    put = fake.find("PUT", "/contacts/")[0]["json"]
    assert put["email_address"] == {"address": "lee@cc.example", "permission_to_send": "unsubscribed"} and put["update_source"] == "Account"
    assert put["list_memberships"] == [CC_LIST] and "created_at" not in put
    assert suppressed(client, h, "lee@cc.example") is not None and opted_out(ws["org"], "lee@cc.example")
    refused = client.post(f"{API}/email-marketing/records/contact/{lee['id']}/subscribe", json={"key": "constant_contact"}, headers=h)
    assert refused.status_code == 409


# ---------------------------------------------------------------------------
# Bounded runs, record panel and permissions
# ---------------------------------------------------------------------------


def test_sync_is_bounded_and_resumes(client, fake, monkeypatch):
    from app.integrations.providers import _em_sync

    ws = signup(client, "Bounded Co")
    h = ws["headers"]
    mailchimp_routes(fake)
    connect_mailchimp(client, h, fake)
    client.put(f"{API}/integration-hub/mailchimp/settings", json={"config": {"list_id": "aud1", "record_types": ["contact"]}}, headers=h)
    for index in range(7):
        contact(client, h, f"P{index}", f"p{index}@bounded.example")
    monkeypatch.setattr(_em_sync.ADAPTERS["mailchimp"], "batch_size", 2)
    monkeypatch.setattr(_em_sync, "MAX_RECORDS_PER_RUN", 4)

    fake.reset()
    first = client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h).json()
    assert first["summary"]["sent"] == 4 and first["backlog"] is True
    second = client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h).json()
    assert second["summary"]["sent"] == 3
    assert len({c["json"]["email_address"] for c in fake.find("PUT", "/lists/aud1/members/")}) == 7

    # "Send everyone again" re-sends people even though nothing changed.
    monkeypatch.setattr(_em_sync, "MAX_RECORDS_PER_RUN", 100)
    again = client.post(f"{API}/email-marketing/mailchimp/sync", json={"resend_everyone": True}, headers=h).json()
    assert again["summary"]["sent"] == 7

    # The scheduler only runs what's due.
    async def due(db):
        return await _em_sync.run_due(db)

    fake.reset()
    run_service(due)
    assert not fake.find("PUT", "/lists/aud1/members/")


def test_record_panel_permissions_and_subscribe(client, fake):
    ws = signup(client, "Panel Co")
    h = ws["headers"]
    state = mailchimp_routes(fake, member_status={})
    connect_mailchimp(client, h, fake)
    client.put(f"{API}/integration-hub/mailchimp/settings", json={"config": {"list_id": "aud1", "list_name": "Newsletter", "record_types": ["contact"], "contact_tags": ["vip"]}}, headers=h)
    mia = contact(client, h, "Mia", "mia@panel.example")

    view = client.get(f"{API}/email-marketing/records/contact/{mia['id']}", headers=h)
    assert view.status_code == 200, view.text
    tool = view.json()["tools"][0]
    assert tool["key"] == "mailchimp" and tool["member"] is None and tool["link"] is None

    # Mia isn't tagged vip, so the sync leaves her out; Subscribe adds her anyway.
    added = client.post(f"{API}/email-marketing/records/contact/{mia['id']}/subscribe", json={"key": "mailchimp"}, headers=h)
    assert added.status_code == 200, added.text
    state["members"]["mia@panel.example"] = "subscribed"
    tool = client.get(f"{API}/email-marketing/records/contact/{mia['id']}", headers=h).json()["tools"][0]
    assert tool["member"]["status"] == "subscribed" and tool["link"]["status"] == "linked" and tool["member"]["activity"]

    # A read-only teammate can look but not change anything.
    email = f"viewer-{uuid.uuid4().hex[:8]}@panel.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Val Viewer", "role": "viewer"}, headers=h).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    viewer = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/email-marketing/records/contact/{mia['id']}", headers=viewer).json()["can_change"] is False
    assert client.post(f"{API}/email-marketing/records/contact/{mia['id']}/unsubscribe", headers=viewer).status_code == 403
    assert client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=viewer).status_code == 403
    assert client.get(f"{API}/email-marketing/mailchimp/lists", headers=viewer).status_code == 403
    detail = client.get(f"{API}/integration-hub/mailchimp", headers=viewer).json()
    assert detail["extra"].get("webhook_url") is None and detail["connection"]["webhook_url"] is None
    assert client.get(f"{API}/email-marketing/records/contact/{uuid.uuid4()}", headers=h).status_code == 404

    # Disconnecting forgets the audience links (a new account starts fresh).
    assert client.delete(f"{API}/integration-hub/mailchimp/connection", headers=h).status_code == 200
    assert links(ws["org"], "mailchimp") == {}


def test_webhook_registration_rate_limits_and_refused_keys(client, fake, monkeypatch):
    from app.integrations.providers import _em_sync

    ws = signup(client, "Hooks Co")
    h = ws["headers"]
    mailchimp_routes(fake)
    fake.on("GET", r"/lists/aud1/webhooks$", {"webhooks": []})
    fake.on("POST", r"/lists/aud1/webhooks$", {"id": "wh123"})
    fake.on("DELETE", r"/lists/aud1/webhooks/wh123$", (204, None))
    connect_mailchimp(client, h, fake)
    client.put(f"{API}/integration-hub/mailchimp/settings", json={"config": {"list_id": "aud1"}}, headers=h)
    contact(client, h, "Quinn", "quinn@hooks.example")
    monkeypatch.setattr(_em_sync, "public_address", lambda: True)

    # With a public address, AIVA registers its webhook for unsubscribes and cleaned addresses only.
    client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h)
    created = fake.find("POST", "/lists/aud1/webhooks")[0]["json"]
    assert created["events"]["unsubscribe"] and created["events"]["cleaned"] and not created["events"]["subscribe"]
    assert "/api/aiva/public/integrations/mailchimp/webhook/" in created["url"] and created["sources"]["api"] is False
    assert client.get(f"{API}/integration-hub/mailchimp", headers=h).json()["extra"]["webhook"]["active"] is True

    # Rate limited: the run stops and the person is sent next time.
    contact(client, h, "Rae", "rae@hooks.example")
    fake.on("PUT", r"/lists/aud1/members/[0-9a-f]{32}$", (429, {"title": "Too Many Requests", "detail": "slow down"}))
    limited = client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h).json()
    assert limited["ok"] is False and "slow down" in limited["message"].lower()
    assert links(ws["org"], "mailchimp").get("rae@hooks.example") in (None, "error")
    fake.on("PUT", r"/lists/aud1/members/[0-9a-f]{32}$", lambda call: {"id": "x", "status": "subscribed"})
    fake.reset()
    assert client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h).json()["ok"]
    assert "rae@hooks.example" in {c["json"]["email_address"] for c in fake.find("PUT", "/lists/aud1/members/")}

    # A revoked key: the connection shows the problem and the scheduler backs off for an hour.
    fake.on("GET", rf"{re.escape(MC)}/campaigns$", (401, {"title": "API Key Invalid", "detail": "Your API key may be invalid, or you've attempted to access the wrong datacenter."}))
    fake.on("GET", r"/lists/aud1/members$", (401, {"title": "API Key Invalid", "detail": "Your API key may be invalid."}))
    refused = client.post(f"{API}/email-marketing/mailchimp/sync", json={}, headers=h).json()
    assert refused["ok"] is False and "refused the API key" in refused["message"]
    detail = client.get(f"{API}/integration-hub/mailchimp", headers=h).json()
    assert detail["status"] == "error" and "refused" in detail["extra"]["sync"]["last_error"]
    next_run = datetime.fromisoformat(detail["extra"]["sync"]["next_run_at"])
    assert next_run > datetime.now(timezone.utc) + timedelta(minutes=50)

    # Disconnecting removes AIVA's webhook from Mailchimp.
    fake.reset()
    assert client.delete(f"{API}/integration-hub/mailchimp/connection", headers=h).status_code == 200
    assert fake.find("DELETE", "/lists/aud1/webhooks/wh123")
