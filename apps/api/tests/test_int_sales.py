"""E-signature and sales intelligence integrations: Adobe Acrobat Sign, PandaDoc,
Apollo.io, ZoomInfo and LinkedIn Lead Gen Forms.

Provider APIs are never called. Calls made with pasted keys go through
``_sales_http.send`` and LinkedIn's through ``core.request``; both are replaced
with fakes that answer like the real APIs and record what AIVA sent.
"""

import asyncio
import hashlib
import hmac
import json
import re
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Integration#2026"
ADOBE = "https://api.na1.adobesign.com/api/rest/v6"
PANDA = "https://api.pandadoc.com/public/v1"
APOLLO = "https://api.apollo.io/api/v1"
ZOOMINFO = "https://api.zoominfo.com"
LINKEDIN = "https://api.linkedin.com/rest"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Sales Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@sales.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Sam Owner", "email": email, "password": PASSWORD, "company_name": company})
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
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"], "X-Session-ID": data["session_id"]},
    }


class FakeHttp:
    """Stands in for the providers: routes (method, URL prefix) to answers and records every call."""

    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.calls: list[tuple[str, str, dict]] = []

    def on(self, method: str, url: str, answer):
        self.routes.insert(0, (method, url, answer))

    def last(self, method: str, url_part: str, exact: bool = False) -> dict:
        for m, url, kwargs in reversed(self.calls):
            if m == method and (url == url_part if exact else url_part in url):
                return kwargs
        raise AssertionError(f"no {method} call to {url_part}")

    def count(self, method: str, url_part: str) -> int:
        return sum(1 for m, url, _ in self.calls if m == method and url_part in url)

    async def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, target, answer in self.routes:
            if m == method and url.startswith(target):
                result = answer(url, kwargs) if callable(answer) else answer
                if result is None:  # the answer passes this URL on to an older route
                    continue
                if isinstance(result, httpx.Response):
                    return result
                return httpx.Response(200, json=result, request=httpx.Request(method, url))
        raise AssertionError(f"Unexpected provider call: {method} {url}")


@pytest.fixture
def http(monkeypatch):
    from app.integrations.providers import _sales_http

    fake = FakeHttp()

    async def send(method, url, *, timeout=30, **kwargs):
        return await fake(method, url, **kwargs)

    monkeypatch.setattr(_sales_http, "send", send)
    return fake


def pdf(text: bytes = b"signed") -> httpx.Response:
    return httpx.Response(200, content=b"%PDF-1.7 " + text, headers={"Content-Type": "application/pdf"}, request=httpx.Request("GET", "x"))


def make_quote(client, ws) -> tuple[dict, dict]:
    h = ws["headers"]
    account = client.post(f"{API}/accounts", json={"name": f"Northwind {uuid.uuid4().hex[:5]}"}, headers=h).json()
    contact = client.post(
        f"{API}/contacts", json={"first_name": "Cara", "last_name": "Client", "email": f"cara-{uuid.uuid4().hex[:5]}@client.example", "account_id": account["id"]}, headers=h
    ).json()
    quote = client.post(
        f"{API}/quotes",
        json={"name": "Annual plan", "account_id": account["id"], "contact_id": contact["id"],
              "line_items": [{"name": "Platform licence", "quantity": "10", "unit_price": "120.00", "discount_percent": "10"}]},
        headers=h,
    )
    assert quote.status_code == 201, quote.text
    return quote.json(), contact


def poll(module_name: str, request_id: str | None = None) -> int:
    async def run():
        from sqlalchemy import func, update

        from app.db.session import AsyncSessionLocal
        from app.integrations.providers import adobe_sign, pandadoc
        from app.models.integration_hub import SignatureRequest

        module = {"adobe_sign": adobe_sign, "pandadoc": pandadoc}[module_name]
        async with AsyncSessionLocal() as db:
            if request_id:
                # Only this request is due: leftovers from earlier runs (other workspaces) wait.
                await db.execute(update(SignatureRequest).where(SignatureRequest.id != uuid.UUID(request_id)).values(last_checked_at=func.now()))
                await db.execute(update(SignatureRequest).where(SignatureRequest.id == uuid.UUID(request_id)).values(last_checked_at=None))
                await db.commit()
            return await module.run_due(db)

    return asyncio.run(run())


# ===========================================================================
# Adobe Acrobat Sign
# ===========================================================================


def connect_adobe(client, ws, http) -> dict:
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/adobe_sign/toggle", json={"enabled": True}, headers=h).status_code == 200
    http.on("GET", "https://api.adobesign.com/api/rest/v6/baseUris", {"apiAccessPoint": "https://api.na1.adobesign.com/", "webAccessPoint": "https://secure.na1.adobesign.com/"})
    http.on("GET", f"{ADOBE}/users/me", {"email": "sam@sales.example", "accountId": "acct-1"})
    saved = client.put(f"{API}/integration-hub/adobe_sign/credentials", json={"values": {"integration_key": "3AAABLblqZhB" + "x" * 40}}, headers=h)
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_adobe_sign_quote_signed_and_saved(client, http):
    ws = signup(client)
    h = ws["headers"]
    detail = connect_adobe(client, ws, http)
    assert detail["status"] == "connected" and "acrobat sign" in detail["connection"]["account_label"].lower()
    assert http.last("GET", "/users/me")["headers"]["Authorization"].startswith("Bearer 3AAABLblqZhB")
    assert "3AAABLblqZhB" not in json.dumps(detail)

    # An access point that isn't Acrobat Sign's is refused.
    bad = client.put(f"{API}/integration-hub/adobe_sign/credentials", json={"values": {"integration_key": "y" * 40, "api_access_point": "https://evil.example/"}}, headers=h)
    assert bad.status_code == 422

    quote, contact = make_quote(client, ws)
    http.on("POST", f"{ADOBE}/transientDocuments", {"transientDocumentId": "td-1"})
    http.on("POST", f"{ADOBE}/agreements", {"id": "agr-1"})
    sent = client.post(
        f"{API}/e-signatures",
        json={"provider": "adobe_sign", "record_type": "quote", "record_id": quote["id"], "quote_id": quote["id"], "subject": "Sign please",
              "recipients": [{"name": "Cara Client", "email": contact["email"]}, {"name": "Sam", "email": ws["email"], "role": "cc"}]},
        headers=h,
    )
    assert sent.status_code == 201, sent.text
    request = sent.json()
    assert request["provider"] == "adobe_sign" and request["status"] == "sent" and request["envelope_id"] == "agr-1"
    upload = http.last("POST", "/transientDocuments")
    html = upload["files"]["File"][1].decode()
    assert "{{Sig1_es_:signer1:signature}}" in html and "{{Dte1_es_:signer1:date}}" in html and upload["data"]["Mime-Type"] == "text/html"
    agreement = http.last("POST", "/agreements")["json"]
    assert agreement["state"] == "IN_PROCESS" and agreement["fileInfos"] == [{"transientDocumentId": "td-1"}]
    assert agreement["participantSetsInfo"][0]["memberInfos"][0]["email"] == contact["email"] and agreement["ccs"] == [{"email": ws["email"]}]
    assert agreement["externalId"] == {"id": request["id"]}

    # Status from the API (events and members): viewed, then signed and completed.
    http.on("GET", f"{ADOBE}/agreements/agr-1/members", {"participantSets": [{"memberInfos": [{"email": contact["email"], "id": "p-1"}], "status": "WAITING_FOR_MY_SIGNATURE"}]})
    http.on("GET", f"{ADOBE}/agreements/agr-1/events", {"events": [{"type": "EMAIL_VIEWED", "participantEmail": contact["email"], "date": "2026-10-24T09:00:00Z"}]})
    http.on("GET", f"{ADOBE}/agreements/agr-1", lambda url, kw: {"status": "OUT_FOR_SIGNATURE"} if url.endswith("agr-1") else None)
    viewed = client.post(f"{API}/e-signatures/{request['id']}/refresh", headers=h).json()
    assert viewed["status"] == "delivered" and viewed["recipients"][0]["status"] == "delivered"

    http.on("POST", f"{ADOBE}/agreements/agr-1/reminders", {"id": "rem-1"})
    assert client.post(f"{API}/e-signatures/{request['id']}/resend", headers=h).status_code == 200
    assert http.last("POST", "/reminders")["json"]["recipientParticipantIds"] == ["p-1"]

    http.on("GET", f"{ADOBE}/agreements/agr-1/members", {"participantSets": [{"memberInfos": [{"email": contact["email"], "id": "p-1"}], "status": "COMPLETED"}]})
    http.on("GET", f"{ADOBE}/agreements/agr-1/events", {"events": [{"type": "ESIGNED", "participantEmail": contact["email"], "date": "2026-10-24T09:05:00Z"}]})
    http.on("GET", f"{ADOBE}/agreements/agr-1/combinedDocument", pdf())
    http.on("GET", f"{ADOBE}/agreements/agr-1", lambda url, kw: {"status": "SIGNED"} if url.endswith("agr-1") else None)
    done = client.post(f"{API}/e-signatures/{request['id']}/refresh", headers=h).json()
    assert done["status"] == "completed" and done["signed_count"] == 1 and done["signed_document_id"]
    assert http.last("GET", "/combinedDocument")["params"] == {"attachAuditReport": "true"}
    assert client.get(f"{API}/quotes/{quote['id']}", headers=h).json()["status"] == "accepted"

    # Webhook: Acrobat Sign's address check gets its client id echoed; notifications only trigger a re-read.
    connection = client.get(f"{API}/integration-hub/adobe_sign", headers=h).json()["connection"]
    path = urlparse(connection["webhook_url"]).path.replace("/api/aiva", API)
    check = client.get(path, headers={"X-AdobeSign-ClientId": "client-123"})
    assert check.status_code == 200 and check.headers["x-adobesign-clientid"] == "client-123"
    nudge = client.post(path, json={"event": "AGREEMENT_ACTION_COMPLETED", "agreement": {"id": "agr-unknown", "status": "SIGNED"}}, headers={"X-AdobeSign-ClientId": "client-123"})
    assert nudge.status_code == 200 and nudge.headers["x-adobesign-clientid"] == "client-123" and nudge.json()["ignored"] == "unknown agreement"


def test_adobe_sign_void_and_polling(client, http):
    ws = signup(client)
    h = ws["headers"]
    connect_adobe(client, ws, http)
    account = client.post(f"{API}/accounts", json={"name": "Contoso"}, headers=h).json()
    upload = client.post(f"{API}/documents", files={"file": ("MSA.pdf", b"%PDF-1.4 msa", "application/pdf")}, data={"record_type": "account", "record_id": account["id"]}, headers=h)
    assert upload.status_code == 201, upload.text
    sources = client.get(f"{API}/e-signatures/sources", params={"record_type": "account", "record_id": account["id"]}, headers=h).json()
    assert sources["documents"][0]["signable_by"]["adobe_sign"] is True

    http.on("POST", f"{ADOBE}/transientDocuments", {"transientDocumentId": "td-2"})
    ids = iter(["agr-2", "agr-3"])
    http.on("POST", f"{ADOBE}/agreements", lambda url, kw: {"id": next(ids)})
    body = {"provider": "adobe_sign", "record_type": "account", "record_id": account["id"], "document_id": upload.json()["id"],
            "recipients": [{"name": "Bo Buyer", "email": "bo@client.example", "routing_order": 1}, {"name": "Al Approver", "email": "al@client.example", "routing_order": 2}]}
    first = client.post(f"{API}/e-signatures", json=body, headers=h).json()
    sets = http.last("POST", "/agreements")["json"]["participantSetsInfo"]
    assert [s["order"] for s in sets] == [1, 2]
    http.on("PUT", f"{ADOBE}/agreements/agr-2/state", httpx.Response(204, request=httpx.Request("PUT", "x")))
    voided = client.post(f"{API}/e-signatures/{first['id']}/void", json={"reason": "Wrong version"}, headers=h)
    assert voided.status_code == 200 and voided.json()["status"] == "voided"
    assert http.last("PUT", "agr-2/state")["json"]["state"] == "CANCELLED"

    second = client.post(f"{API}/e-signatures", json=body, headers=h).json()
    http.on("GET", f"{ADOBE}/agreements/agr-3/members", {"participantSets": []})
    http.on("GET", f"{ADOBE}/agreements/agr-3/events", {"events": [{"type": "REJECTED", "participantEmail": "bo@client.example", "comment": "Price too high"}]})
    http.on("GET", f"{ADOBE}/agreements/agr-3", lambda url, kw: {"status": "CANCELLED"} if url.endswith("agr-3") else None)
    assert poll("adobe_sign", second["id"]) >= 1
    declined = client.get(f"{API}/e-signatures/{second['id']}", headers=h).json()
    assert declined["status"] == "declined" and declined["recipients"][0]["decline_reason"] == "Price too high"


# ===========================================================================
# PandaDoc
# ===========================================================================


def connect_pandadoc(client, ws, http) -> None:
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/pandadoc/toggle", json={"enabled": True}, headers=h).status_code == 200
    http.on("GET", f"{PANDA}/templates", {"results": [{"id": "tpl1", "name": "Order form", "date_modified": "2026-10-01"}]})
    http.on("GET", f"{PANDA}/members/current", {"email": "sam@sales.example", "workspace": "ws-1", "workspace_name": "Sales Co"})
    saved = client.put(f"{API}/integration-hub/pandadoc/credentials", json={"values": {"api_key": "pd-" + "k" * 40}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert http.last("GET", "/templates")["headers"]["Authorization"] == "API-Key pd-" + "k" * 40


def test_pandadoc_quote_from_template(client, http, monkeypatch):
    from app.integrations.providers import pandadoc

    monkeypatch.setattr(pandadoc, "READY_WAIT", ())
    ws = signup(client)
    h = ws["headers"]
    connect_pandadoc(client, ws, http)
    http.on("GET", f"{PANDA}/templates/tpl1/details", {
        "name": "Order form",
        "roles": [{"name": "Client", "signing_order": 1}, {"name": "Sender", "signing_order": 2}],
        "tokens": [{"name": "Client.FirstName"}, {"name": "Quote.Total"}, {"name": "Deal.Name"}],
        "pricing": {"tables": [{"name": "Pricing Table 1"}]},
    })
    assert client.get(f"{API}/pandadoc/templates", headers=h).json()["items"][0]["id"] == "tpl1"
    roles = client.get(f"{API}/pandadoc/templates/tpl1", headers=h).json()
    assert [r["name"] for r in roles["roles"]] == ["Client", "Sender"] and roles["pricing_tables"] == ["Pricing Table 1"]

    quote, contact = make_quote(client, ws)
    body = {"provider": "pandadoc", "record_type": "quote", "record_id": quote["id"], "quote_id": quote["id"],
            "recipients": [{"name": "Cara Client", "email": contact["email"]}]}
    missing = client.post(f"{API}/e-signatures", json=body, headers=h)
    assert missing.status_code == 422 and "template" in missing.json()["detail"]
    assert client.put(f"{API}/integration-hub/pandadoc/settings", json={"config": {"reminder_days": 3}}, headers=h).status_code == 422  # set in PandaDoc
    assert client.put(f"{API}/integration-hub/pandadoc/settings", json={"config": {"quote_template_id": "tpl1"}}, headers=h).status_code == 200

    http.on("POST", f"{PANDA}/documents", {"id": "doc-1", "status": "document.uploaded"})
    http.on("GET", f"{PANDA}/documents/doc-1", lambda url, kw: {"id": "doc-1", "status": "document.draft"} if url.endswith("doc-1") else None)
    http.on("POST", f"{PANDA}/documents/doc-1/send", {"id": "doc-1", "status": "document.sent"})
    sent = client.post(f"{API}/e-signatures", json=body, headers=h)
    assert sent.status_code == 201, sent.text
    request = sent.json()
    assert request["status"] == "sent" and request["envelope_id"] == "doc-1"
    created = http.last("POST", f"{PANDA}/documents", exact=True)["json"]
    assert created["template_uuid"] == "tpl1" and created["recipients"][0]["role"] == "Client" and created["recipients"][0]["email"] == contact["email"]
    tokens = {t["name"]: t["value"] for t in created["tokens"]}
    assert tokens["Client.FirstName"] == "Cara" and tokens["Quote.Total"] and "Sender.Email" not in tokens  # only the template's tokens
    rows = created["pricing_tables"][0]["sections"][0]["rows"]
    assert created["pricing_tables"][0]["name"] == "Pricing Table 1" and rows[0]["data"]["Name"] == "Platform licence" and rows[0]["data"]["QTY"] == 10.0
    assert created["metadata"]["aiva_request_id"] == request["id"]

    # More signers than the template has roles.
    too_many = client.post(f"{API}/e-signatures", json={**body, "recipients": [{"name": f"P{i}", "email": f"p{i}@x.example"} for i in range(3)]}, headers=h)
    assert too_many.status_code == 422 and "role" in too_many.json()["detail"]

    # Completed: read back, signed copy saved, quote accepted.
    http.on("GET", f"{PANDA}/documents/doc-1/details", {"status": "document.completed", "recipients": [{"id": "r-1", "email": contact["email"], "has_completed": True}]})
    http.on("GET", f"{PANDA}/documents/doc-1/download-protected", pdf())
    done = client.post(f"{API}/e-signatures/{request['id']}/refresh", headers=h).json()
    assert done["status"] == "completed" and done["signed_document_id"] and done["signed_count"] == 1
    assert client.get(f"{API}/quotes/{quote['id']}", headers=h).json()["status"] == "accepted"


def test_pandadoc_document_upload_sends_later_and_webhook(client, http, monkeypatch):
    from app.integrations.providers import pandadoc

    monkeypatch.setattr(pandadoc, "READY_WAIT", ())
    ws = signup(client)
    h = ws["headers"]
    connect_pandadoc(client, ws, http)
    account = client.post(f"{API}/accounts", json={"name": "Fabrikam"}, headers=h).json()
    upload = client.post(f"{API}/documents", files={"file": ("Order.pdf", b"%PDF-1.4 order {signature:Signer1}", "application/pdf")},
                         data={"record_type": "account", "record_id": account["id"]}, headers=h).json()
    http.on("POST", f"{PANDA}/documents?upload", {"id": "doc-9", "status": "document.uploaded"})
    still_preparing = {"value": True}
    http.on("GET", f"{PANDA}/documents/doc-9", lambda url, kw: ({"status": "document.uploaded"} if still_preparing["value"] else {"status": "document.draft"}) if url.endswith("doc-9") else None)
    sent = client.post(
        f"{API}/e-signatures",
        json={"provider": "pandadoc", "record_type": "account", "record_id": account["id"], "document_id": upload["id"], "recipients": [{"name": "Bo Buyer", "email": "bo@client.example"}]},
        headers=h,
    )
    assert sent.status_code == 201, sent.text
    request = sent.json()
    assert request["status"] == "created"  # PandaDoc is still preparing it
    form = http.last("POST", "/documents?upload")
    assert json.loads(form["data"]["data"])["recipients"][0]["role"] == "Signer1" and form["files"]["file"][0] == "Order.pdf"

    still_preparing["value"] = False
    http.on("POST", f"{PANDA}/documents/doc-9/send", {"status": "document.sent"})
    assert poll("pandadoc", request["id"]) >= 1
    assert client.get(f"{API}/e-signatures/{request['id']}", headers=h).json()["status"] == "sent"

    # Webhook: wrong signature refused; right signature only triggers a re-read.
    async def set_key():
        from sqlalchemy import select

        from app.db.session import AsyncSessionLocal
        from app.integrations import core
        from app.models.integration_hub import IntegrationConnection

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.organization_id == uuid.UUID(ws["org"]), IntegrationConnection.provider == "pandadoc"))).scalar_one()
            core.set_secret(row, "webhook_shared_key", "shared-key-123")
            await db.commit()
            return row.webhook_token

    token = asyncio.run(set_key())
    path = f"{API}/public/integrations/pandadoc/webhook/{token}"
    payload = json.dumps([{"event": "document_state_changed", "data": {"id": "doc-9", "status": "document.completed"}}]).encode()
    assert client.post(f"{path}?signature=bad", content=payload, headers={"Content-Type": "application/json"}).status_code == 403
    signature = hmac.new(b"shared-key-123", payload, hashlib.sha256).hexdigest()
    http.on("GET", f"{PANDA}/documents/doc-9/details", {"status": "document.declined", "recipients": [{"id": "r-9", "email": "bo@client.example", "has_completed": False}]})
    ok = client.post(f"{path}?signature={signature}", content=payload, headers={"Content-Type": "application/json"})
    assert ok.status_code == 200 and ok.json()["refreshed"] == 1
    assert client.get(f"{API}/e-signatures/{request['id']}", headers=h).json()["status"] == "declined"


def test_pandadoc_signature_check():
    from app.integrations.providers import pandadoc

    body = b'[{"event":"document_state_changed"}]'
    good = hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert pandadoc.signature_ok("k", body, good)
    assert not pandadoc.signature_ok("k", body, "x" * 64)
    assert not pandadoc.signature_ok(None, body, good)
    assert not pandadoc.signature_ok("k", body, "é")  # never a 500 on odd input


# ===========================================================================
# Apollo.io and ZoomInfo
# ===========================================================================


def connect_apollo(client, ws, http) -> None:
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/apollo/toggle", json={"enabled": True}, headers=h).status_code == 200
    http.on("GET", f"{APOLLO}/auth/health", {"healthy": True, "is_logged_in": True})
    saved = client.put(f"{API}/integration-hub/apollo/credentials", json={"values": {"api_key": "apollo-key-123456"}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert http.last("GET", "/auth/health")["headers"]["x-api-key"] == "apollo-key-123456"


def test_apollo_waterfall_enrichment(client, http):
    ws = signup(client)
    h = ws["headers"]
    connect_apollo(client, ws, http)
    settings = client.get(f"{API}/workspace-settings/enrichment", headers=h).json()["data"]
    providers = [p for p in settings["providers"] if p["key"] != "apollo"]
    settings["providers"] = [{"key": "apollo", "enabled": True}] + providers
    assert client.put(f"{API}/workspace-settings/enrichment", json={"data": settings}, headers=h).status_code == 200
    listed = {p["key"]: p for p in client.get(f"{API}/enrichment/providers", headers=h).json()["providers"]}
    assert listed["apollo"]["key_set"] is True and listed["zoominfo"]["key_set"] is False

    lead = client.post(f"{API}/leads", json={"first_name": "Lena", "last_name": "Lopez", "email": "lena@acme.example", "company_name": "Acme"}, headers=h).json()
    http.on("POST", f"{APOLLO}/people/match", {"person": {"id": "ap-1", "first_name": "Lena", "last_name": "Lopez", "title": "VP Sales", "seniority": "vp",
                                                          "linkedin_url": "https://www.linkedin.com/in/lena", "organization": {"name": "Acme", "primary_domain": "acme.example"}}})
    result = client.post(f"{API}/enrichment/run", json={"record_type": "lead", "record_id": lead["id"]}, headers=h)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["applied"].get("job_title") == "VP Sales" and body["credits"] == 1
    assert http.last("POST", "/people/match")["params"]["email"] == "lena@acme.example"


def test_apollo_search_and_import(client, http):
    ws = signup(client)
    h = ws["headers"]
    connect_apollo(client, ws, http)
    assert client.post(f"{API}/prospects/apollo/search", json={"kind": "people"}, headers=h).status_code == 422  # needs a filter
    sources = client.get(f"{API}/prospects/providers", headers=h).json()
    assert {p["key"]: p["connected"] for p in sources["items"]} == {"apollo": True, "zoominfo": False} and sources["can_assign_owner"] is True

    http.on("POST", f"{APOLLO}/mixed_people/api_search", {"people": [
        {"id": "p-1", "first_name": "Ana", "last_name_obfuscated": "Sm***h", "title": "CTO", "organization": {"name": "Globex", "primary_domain": "globex.example"}},
        {"id": "p-2", "first_name": "Ben", "last_name_obfuscated": "Ki***g", "title": "CTO", "organization": {"name": "Initech", "primary_domain": "initech.example"}},
    ], "pagination": {"total_entries": 2}})
    found = client.post(f"{API}/prospects/apollo/search", json={"kind": "people", "titles": ["CTO"], "seniorities": ["c_suite", "bogus"], "employee_ranges": ["51,200"]}, headers=h)
    assert found.status_code == 200, found.text
    assert [i["id"] for i in found.json()["items"]] == ["p-1", "p-2"] and found.json()["total"] == 2
    params = http.last("POST", "/mixed_people/api_search")["params"]
    assert ("person_titles[]", "CTO") in params and ("person_seniorities[]", "c_suite") in params and ("person_seniorities[]", "bogus") not in params

    # Ben is on the do-not-contact list.
    assert client.post(f"{API}/suppression", json={"values": ["ben@initech.example"]}, headers=h).status_code == 201
    people = {
        "p-1": {"id": "p-1", "first_name": "Ana", "last_name": "Smith", "email": "ana@globex.example", "title": "CTO", "organization": {"name": "Globex", "primary_domain": "globex.example"}},
        "p-2": {"id": "p-2", "first_name": "Ben", "last_name": "King", "email": "ben@initech.example", "title": "CTO", "organization": {"name": "Initech", "primary_domain": "initech.example"}},
    }
    http.on("POST", f"{APOLLO}/people/match", lambda url, kw: {"person": people[kw["params"]["id"]]})
    imported = client.post(f"{API}/prospects/import", json={"provider": "apollo", "kind": "people", "as": "contact", "items": found.json()["items"]}, headers=h)
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert [c["name"] for c in result["created"]] == ["Ana Smith"] and result["credits"] == 1
    assert result["skipped"][0]["reason"] == "On the do-not-contact list."
    contact = client.get(f"{API}/contacts/{result['created'][0]['id']}", headers=h).json()
    assert contact["email"] == "ana@globex.example" and contact["account_id"]

    again = client.post(f"{API}/prospects/import", json={"provider": "apollo", "kind": "people", "as": "contact", "items": [found.json()["items"][0]]}, headers=h).json()
    assert again["created"] == [] and again["skipped"][0]["reason"] == "Already in AIVA."
    refound = client.post(f"{API}/prospects/apollo/search", json={"kind": "people", "titles": ["CTO"]}, headers=h).json()
    assert refound["items"][0]["in_aiva"] is True

    # Companies import as accounts.
    http.on("POST", f"{APOLLO}/mixed_companies/search", {"organizations": [{"id": "o-1", "name": "Hooli", "primary_domain": "hooli.example", "industry": "software", "estimated_num_employees": 900}]})
    http.on("GET", f"{APOLLO}/organizations/enrich", {"organization": {"id": "o-1", "name": "Hooli", "primary_domain": "hooli.example", "industry": "software", "estimated_num_employees": 900}})
    companies = client.post(f"{API}/prospects/apollo/search", json={"kind": "companies", "industries": ["software"]}, headers=h).json()["items"]
    made = client.post(f"{API}/prospects/import", json={"provider": "apollo", "kind": "companies", "as": "account", "items": companies}, headers=h).json()
    assert made["created"][0]["type"] == "account"
    assert client.post(f"{API}/prospects/import", json={"provider": "apollo", "kind": "companies", "as": "lead", "items": companies}, headers=h).status_code == 422


def test_zoominfo_pki_search_and_import(client, http):
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    seen: list[dict] = []

    def authenticate(url, kw):
        token = kw["headers"]["Authorization"].split(" ", 1)[1]
        seen.append(jwt.decode(token, public_pem, algorithms=["RS256"], audience="enterprise_api"))
        return {"jwt": "zi-access"}

    ws = signup(client)
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/zoominfo/toggle", json={"enabled": True}, headers=h).status_code == 200
    http.on("POST", f"{ZOOMINFO}/authenticate", authenticate)
    bad = client.put(f"{API}/integration-hub/zoominfo/credentials", json={"values": {"username": "u", "client_id": "c", "private_key": "not a key"}}, headers=h)
    assert bad.status_code == 422
    saved = client.put(f"{API}/integration-hub/zoominfo/credentials", json={"values": {"username": "api@sales.example", "client_id": "client-9", "private_key": private_pem}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert seen[-1]["client_id"] == "client-9" and seen[-1]["username"] == "api@sales.example" and seen[-1]["iss"] == "api-client@zoominfo.com"
    assert "BEGIN PRIVATE KEY" not in saved.text

    http.on("POST", f"{ZOOMINFO}/search/contact", {"totalResults": 1, "data": [{"id": 77, "firstName": "Cy", "lastName": "Doe", "jobTitle": "CFO", "company": {"id": 5, "name": "Umbrella"}}]})
    found = client.post(f"{API}/prospects/zoominfo/search", json={"kind": "people", "titles": ["CFO"], "seniorities": ["c_suite"], "employee_ranges": ["51,200", "201,500"]}, headers=h)
    assert found.status_code == 200, found.text
    sent = http.last("POST", "/search/contact")
    assert sent["headers"]["Authorization"] == "Bearer zi-access"
    assert sent["json"]["managementLevel"] == "C Level Exec" and sent["json"]["employeeRangeMin"] == "51" and sent["json"]["employeeRangeMax"] == "500"
    http.on("POST", f"{ZOOMINFO}/enrich/contact", {"success": True, "data": {"result": [{"data": [{"id": 77, "firstName": "Cy", "lastName": "Doe", "email": "cy@umbrella.example",
                                                                                                  "jobTitle": "CFO", "companyName": "Umbrella", "companyWebsite": "www.umbrella.example"}]}]}})
    imported = client.post(f"{API}/prospects/import", json={"provider": "zoominfo", "kind": "people", "as": "lead", "items": found.json()["items"]}, headers=h).json()
    assert imported["created"][0]["type"] == "lead"
    lead = client.get(f"{API}/leads/{imported['created'][0]['id']}", headers=h).json()
    assert lead["email"] == "cy@umbrella.example" and lead["source"] == "zoominfo"
    assert http.last("POST", "/enrich/contact")["json"]["matchPersonInput"] == [{"personId": "77"}]


def test_prospects_permissions(client, http):
    ws = signup(client)
    h = ws["headers"]
    connect_apollo(client, ws, http)
    email = f"member-{uuid.uuid4().hex[:8]}@sales.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Max Member", "role": "member"}, headers=h).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    # Members can't import records on someone else's behalf or touch the webhooks.
    forbidden = client.post(f"{API}/prospects/import", json={"provider": "apollo", "kind": "people", "as": "lead", "owner_user_id": ws["user_id"], "items": [{"id": "p-1"}]}, headers=member)
    assert forbidden.status_code == 403
    assert client.post(f"{API}/adobe-sign/webhook", headers=member).status_code == 403
    assert client.post(f"{API}/linkedin-leads/sync", headers=member).status_code == 403


# ===========================================================================
# LinkedIn Lead Gen Forms
# ===========================================================================


class FakeLinkedIn:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.responses = [
            {"id": "resp-1", "submittedAt": 1_793_000_000_000, "versionedLeadGenFormUrn": "urn:li:versionedLeadGenForm:(urn:li:leadGenForm:3162,1)",
             "leadMetadata": {"sponsoredLeadMetadata": {"campaign": "urn:li:sponsoredCampaign:367"}},
             "formResponse": {"answers": [
                 {"questionId": 1, "answerDetails": {"textQuestionAnswer": {"answer": "Lia"}}},
                 {"questionId": 2, "answerDetails": {"textQuestionAnswer": {"answer": "Park"}}},
                 {"questionId": 3, "answerDetails": {"textQuestionAnswer": {"answer": "lia@wayne.example"}}},
                 {"questionId": 4, "answerDetails": {"multipleChoiceAnswer": {"options": [2]}}},
             ]}},
            {"id": "resp-test", "testLead": True, "formResponse": {"answers": []}},
        ]

    async def __call__(self, db, connection, method, url, **kwargs):
        self.calls.append((url, kwargs))
        assert kwargs["headers"]["X-Restli-Protocol-Version"] == "2.0.0" and re.fullmatch(r"\d{6}", kwargs["headers"]["LinkedIn-Version"])
        if "/adAccounts?" in url:
            body = {"elements": [{"id": 5011, "name": "Wayne ads", "status": "ACTIVE"}, {"id": "bad", "name": "x"}]}
        elif "/leadForms?" in url:
            body = {"elements": [{"id": 3162, "name": "Demo request", "state": "PUBLISHED", "content": {"questions": [
                {"questionId": 1, "name": "firstName", "predefinedField": "FIRST_NAME", "question": {"localized": {"en_US": "First name"}}},
                {"questionId": 2, "name": "lastName", "predefinedField": "LAST_NAME", "question": {"localized": {"en_US": "Last name"}}},
                {"questionId": 3, "name": "email", "predefinedField": "EMAIL", "question": {"localized": {"en_US": "Email"}}},
                {"questionId": 4, "name": "teamSize", "question": {"localized": {"en_US": "Team size"}},
                 "questionDetails": {"multipleChoiceQuestionDetails": {"options": [{"id": 1, "text": {"localized": {"en_US": "1-10"}}}, {"id": 2, "text": {"localized": {"en_US": "11-50"}}}]}}},
            ]}}]}
        elif "/leadFormResponses?" in url:
            body = {"elements": self.responses, "paging": {"count": 100, "start": 0}}
        else:
            raise AssertionError(f"Unexpected LinkedIn call: {url}")
        return httpx.Response(200, json=body, request=httpx.Request(method, url))


def test_linkedin_lead_gen_sync(client, monkeypatch):
    from app.integrations import core, identity

    ws = signup(client)
    h = ws["headers"]
    fake = FakeLinkedIn()
    monkeypatch.setattr(core, "request", fake)
    assert client.put(f"{API}/integration-hub/linkedin_lead_gen/toggle", json={"enabled": True}, headers=h).status_code == 200
    assert client.put(f"{API}/integration-hub/apps/linkedin", json={"client_id": "li-client", "client_secret": "li-secret"}, headers=h).status_code == 200
    started = client.post(f"{API}/integration-hub/linkedin_lead_gen/connect", json={}, headers=h)
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["authorize_url"])
    assert url.netloc == "www.linkedin.com" and "r_marketing_leadgen_automation" in parse_qs(url.query)["scope"][0]
    state = parse_qs(url.query)["state"][0]

    async def fake_token(family, app_creds, data):
        return {"access_token": "li-access", "refresh_token": "li-refresh", "expires_in": 5_000_000}

    async def fake_identify(family, key, tokens, app_creds):
        return {"label": "sam@sales.example", "external_id": "li-member"}

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "c"}, headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert "connected=linkedin_lead_gen" in back.headers["location"], back.headers["location"]

    detail = client.get(f"{API}/integration-hub/linkedin_lead_gen", headers=h).json()
    assert [a["id"] for a in detail["extra"]["accounts"]] == ["5011"]
    assert client.post(f"{API}/linkedin-leads/sync", headers=h).status_code == 409  # nothing chosen yet
    chosen = client.put(f"{API}/linkedin-leads/accounts/5011", json={"sync": True}, headers=h)
    assert chosen.status_code == 200, chosen.text
    form = chosen.json()["extra"]["lead_forms"]["3162"]
    assert [q["key"] for q in form["questions"]] == ["first_name", "last_name", "email", "teamSize"]  # what field mapping uses
    forms_url = next(u for u, _ in fake.calls if "/leadForms?" in u)
    assert "owner=(sponsoredAccount:urn%3Ali%3AsponsoredAccount%3A5011)" in forms_url

    synced = client.post(f"{API}/linkedin-leads/sync", headers=h)
    assert synced.status_code == 200, synced.text
    assert synced.json()["imported"] == 1
    responses_url = next(u for u, _ in fake.calls if "/leadFormResponses?" in u)
    assert "leadType=(leadType:SPONSORED)" in responses_url and re.search(r"submittedAtTimeRange=\(start:\d+,end:\d+\)", responses_url)
    leads = client.get(f"{API}/leads", params={"search": "lia@wayne.example"}, headers=h).json()
    lead = (leads["items"] if isinstance(leads, dict) else leads)[0]
    assert lead["first_name"] == "Lia" and lead["source"] == "linkedin_ads" and "Team size: 11-50" in (lead.get("notes") or "")

    again = client.post(f"{API}/linkedin-leads/sync", headers=h).json()
    assert again["imported"] == 0  # imported once, by LinkedIn response id
