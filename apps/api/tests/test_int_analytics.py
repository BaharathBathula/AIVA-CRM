"""Advertising and analytics integrations: Google Ads (accounts, lead forms, offline
conversions, campaign performance), Google Analytics 4 (Measurement Protocol) and the
analytics feed Power BI and Looker Studio read with API keys.

Google is never called: the Ads and GA4 HTTP helpers and the OAuth token exchange
are replaced by fakes that record what AIVA sent.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import uuid
import zipfile
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Analytics#2026"
MANAGER = "1112223334"
CLIENT = "5556667778"
DIRECT = "9990001112"
ACTION = f"customers/{CLIENT}/conversionActions/42"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Ads Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@ads.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Ada Owner", "email": email, "password": PASSWORD, "company_name": company})
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


def local_path(url: str) -> str:
    """A public /api/aiva/... URL as the backend serves it (/api/v1/...)."""

    return urlparse(url).path.replace("/api/aiva/", "/api/v1/", 1)


class FakeAds:
    """Stands in for the Google Ads REST API."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.upload_errors: dict[int, str] = {}
        self.performance: list[dict[str, Any]] = []
        self.clicks: list[dict[str, Any]] = []

    async def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        body = kwargs.get("json") or {}
        headers = kwargs.get("headers") or {}
        self.calls.append({"method": method, "url": url, "json": body, "headers": headers})
        assert headers.get("developer-token"), "developer token missing"
        if url.endswith("customers:listAccessibleCustomers"):
            return httpx.Response(200, json={"resourceNames": [f"customers/{MANAGER}", f"customers/{DIRECT}"]})
        match = re.search(r"/customers/(\d+)/googleAds:search$", url)
        if match:
            customer = match.group(1)
            query = body.get("query", "")
            if "FROM customer_client" in query:
                assert headers.get("login-customer-id") == MANAGER
                return httpx.Response(200, json={"results": [{"customerClient": {"id": CLIENT, "descriptiveName": "Client Store", "manager": False, "currencyCode": "EUR", "timeZone": "Europe/Berlin"}}]})
            if "FROM customer " in query:
                manager = customer == MANAGER
                return httpx.Response(200, json={"results": [{"customer": {"id": customer, "descriptiveName": "Agency MCC" if manager else "Direct Shop", "manager": manager, "currencyCode": "USD", "timeZone": "America/Chicago"}}]})
            if "FROM conversion_action" in query:
                return httpx.Response(200, json={"results": [{"conversionAction": {"resourceName": ACTION, "id": "42", "name": "CRM won deal", "type": "UPLOAD_CLICKS", "category": "PURCHASE"}}]})
            if "FROM click_view" in query:
                return httpx.Response(200, json={"results": self.clicks})
            if "FROM campaign" in query:
                return httpx.Response(200, json={"results": self.performance})
            return httpx.Response(200, json={"results": []})
        if url.endswith(f"customers/{CLIENT}:uploadClickConversions"):
            assert headers.get("login-customer-id") == MANAGER
            conversions = body["conversions"]
            errors = [
                {"errorCode": {"conversionUploadError": code}, "message": code, "location": {"fieldPathElements": [{"fieldName": "conversions", "index": index}]}}
                for index, code in self.upload_errors.items() if index < len(conversions)
            ]
            response: dict[str, Any] = {"results": [({} if i in self.upload_errors else {"gclid": c.get("gclid")}) for i, c in enumerate(conversions)]}
            if errors:
                response["partialFailureError"] = {"code": 3, "message": "partial", "details": [{"@type": "type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure", "errors": errors}]}
            self.upload_errors = {}
            return httpx.Response(200, json=response)
        return httpx.Response(404, json={"error": {"message": "unknown"}})

    def uploads(self) -> list[dict[str, Any]]:
        return [c["json"] for c in self.calls if c["url"].endswith(":uploadClickConversions")]


@pytest.fixture
def ads(monkeypatch) -> FakeAds:
    from app.integrations.providers import _ads_api

    fake = FakeAds()
    monkeypatch.setattr(_ads_api, "http", fake)
    return fake


def connect_google_ads(client, ws: dict, monkeypatch) -> dict:
    from app.integrations import core, identity

    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/google_ads/toggle", json={"enabled": True}, headers=h).status_code == 200
    token = client.put(f"{API}/ads/google/developer-token", json={"token": "dev-token_123"}, headers=h)
    assert token.status_code == 200 and token.json()["set"] and token.json()["source"] == "workspace", token.text
    client.put(f"{API}/integration-hub/apps/google", json={"client_id": "google-client", "client_secret": "google-secret"}, headers=h)
    started = client.post(f"{API}/integration-hub/google_ads/connect", json={}, headers=h)
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorize_url"]).query)
    assert "https://www.googleapis.com/auth/adwords" in query["scope"][0]

    async def fake_token(family, app_creds, data):
        return {"access_token": "ads-access", "refresh_token": "ads-refresh", "expires_in": 3600}

    async def fake_identify(family, key, tokens, app_creds):
        return {"label": "marketer@ads.example", "external_id": "g-1"}

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": query["state"][0], "code": "c"}, headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert back.status_code == 302 and "connected=google_ads" in back.headers["location"], back.headers["location"]
    return client.get(f"{API}/integration-hub/google_ads", headers=h).json()


def stages(client, h) -> dict[str, str]:
    pipeline = client.get(f"{API}/pipelines", headers=h).json()[0]
    found = client.get(f"{API}/pipelines/{pipeline['id']}/stages", headers=h).json()
    return {s["category"]: s["id"] for s in sorted(found, key=lambda s: s["position"])[::-1]} | {"first": sorted(found, key=lambda s: s["position"])[0]["id"]}


def run_ads(ws: dict) -> int:
    from app.integrations.providers import google_ads

    async def go(db):
        import time

        return await google_ads.run_workspace(db, uuid.UUID(ws["org"]), time.monotonic() + 30, force_performance=True)

    return run_service(go)


def conversion_rows(ws: dict, provider: str = "google_ads") -> list[dict[str, Any]]:
    from sqlalchemy import select

    from app.models.integration_hub import IntegrationObject

    async def go(db):
        rows = (
            await db.execute(
                select(IntegrationObject)
                .where(IntegrationObject.organization_id == uuid.UUID(ws["org"]), IntegrationObject.provider == provider, IntegrationObject.object_type.in_(("conversion", "ga4_event")))
                .order_by(IntegrationObject.created_at)
            )
        ).scalars().all()
        return [{"status": r.status, "moment": (r.data or {}).get("moment"), "message": (r.data or {}).get("message"), "amount": r.amount} for r in rows]

    return run_service(go)


# ---------------------------------------------------------------------------
# Google Ads
# ---------------------------------------------------------------------------


def test_google_ads_accounts_settings_and_lead_forms(client, ads, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    detail = connect_google_ads(client, ws, monkeypatch)
    extra = detail["extra"]
    accounts = {a["id"]: a for a in extra["accounts"]}
    assert set(accounts) == {CLIENT, DIRECT}  # the manager itself isn't a target; its client is
    assert accounts[CLIENT]["login_customer_id"] == MANAGER and accounts[CLIENT]["via"] == "Agency MCC"
    assert extra["developer_token"] == {"set": True, "source": "workspace", "workspace": True}
    assert "dev-token_123" not in json.dumps(detail)
    assert extra["webhook_url"] and extra["lead_form_key"]

    bad = client.put(f"{API}/integration-hub/google_ads/settings", json={"config": {"customer_id": "123-456-7890"}}, headers=h)
    assert bad.status_code == 422
    picked = client.put(f"{API}/integration-hub/google_ads/settings", json={"config": {"customer_id": f"{CLIENT[:3]}-{CLIENT[3:6]}-{CLIENT[6:]}"}}, headers=h)
    assert picked.status_code == 200, picked.text
    settings = picked.json()["extra"]["settings"]
    assert settings["customer_id"] == CLIENT and settings["login_customer_id"] == MANAGER and settings["currency"] == "EUR"

    actions = client.get(f"{API}/ads/google/conversion-actions", params={"refresh": True}, headers=h)
    assert actions.status_code == 200 and actions.json()["items"][0]["resource_name"] == ACTION
    wrong = client.put(f"{API}/integration-hub/google_ads/settings", json={"config": {"conversions": {"deal_won": {"conversion_action": f"customers/{DIRECT}/conversionActions/1"}}}}, headers=h)
    assert wrong.status_code == 422
    unknown = client.put(f"{API}/integration-hub/google_ads/settings", json={"config": {"conversions": {"made_up": {"conversion_action": ACTION}}}}, headers=h)
    assert unknown.status_code == 422

    # Lead forms: the key must match; leads keep the click and campaign; Google's retries are ignored.
    url = local_path(extra["webhook_url"])
    form = {
        "lead_id": "TeSter-123", "api_version": "1.0", "form_id": 4321, "campaign_id": 777, "adgroup_id": 888, "creative_id": 999, "gcl_id": "Cj0KCQiA-clickid-1",
        "user_column_data": [
            {"column_name": "Full Name", "string_value": "Grace Hopper", "column_id": "FULL_NAME"},
            {"column_name": "User Email", "string_value": "grace@navy.example", "column_id": "EMAIL"},
            {"column_name": "Budget", "string_value": "50k", "column_id": "CUSTOM_BUDGET"},
        ],
    }
    assert client.post(url, json={**form, "google_key": "wrong"}).status_code == 403
    assert client.post(url, json={**form, "google_key": "ü-not-ascii"}).status_code == 403
    ok = client.post(url, json={**form, "google_key": extra["lead_form_key"]})
    assert ok.status_code == 200, ok.text
    again = client.post(url, json={**form, "google_key": extra["lead_form_key"]})
    assert again.status_code == 200
    leads = [l for l in client.get(f"{API}/leads", params={"search": "grace@navy.example"}, headers=h).json() if l["email"] == "grace@navy.example"]
    assert len(leads) == 1 and leads[0]["source"] == "google_ads" and leads[0]["first_name"] == "Grace"
    stored = run_service(lambda db: _attribution(db, leads[0]["id"]))
    assert stored["gclid"] == "Cj0KCQiA-clickid-1" and stored["google_campaign_id"] == "777" and stored["google_lead_id"] == "TeSter-123"

    # Members don't see the lead-form key or URL.
    email = f"rep-{uuid.uuid4().hex[:8]}@ads.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Rae Rep", "role": "member"}, headers=h).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    seen = client.get(f"{API}/integration-hub/google_ads", headers=member).json()
    assert extra["lead_form_key"] not in json.dumps(seen) and not seen["extra"].get("webhook_url")


async def _attribution(db, lead_id: str) -> dict[str, Any]:
    from app.models.lead import Lead

    lead = await db.get(Lead, uuid.UUID(lead_id))
    return dict((lead.custom_fields or {}).get("_attribution") or {})


def test_offline_conversions_upload_partial_failure_and_dedupe(client, ads, monkeypatch):
    ws = signup(client, "Conversions Co")
    h = ws["headers"]
    detail = connect_google_ads(client, ws, monkeypatch)
    client.put(f"{API}/integration-hub/google_ads/settings", json={"config": {"customer_id": CLIENT}}, headers=h)
    client.get(f"{API}/ads/google/conversion-actions", params={"refresh": True}, headers=h)
    mapped = client.put(
        f"{API}/integration-hub/google_ads/settings",
        json={"config": {"conversions": {"lead_qualified": {"conversion_action": ACTION, "value": "fixed", "fixed_value": 25}, "deal_won": {"conversion_action": ACTION, "value": "record"}}, "consent": "granted"}},
        headers=h,
    )
    assert mapped.status_code == 200, mapped.text

    url = local_path(detail["extra"]["webhook_url"])
    key = detail["extra"]["lead_form_key"]

    def lead_form(name: str, gclid: str) -> str:
        email = f"{name.lower()}@buyer.example"
        client.post(url, json={"google_key": key, "lead_id": f"L-{name}", "gcl_id": gclid, "user_column_data": [
            {"column_id": "FULL_NAME", "column_name": "Full Name", "string_value": f"{name} Buyer"}, {"column_id": "EMAIL", "column_name": "Email", "string_value": email},
            {"column_id": "COMPANY_NAME", "column_name": "Company", "string_value": f"{name} Corp"}]})
        return next(l["id"] for l in client.get(f"{API}/leads", params={"search": email}, headers=h).json() if l["email"] == email)

    alice = lead_form("Alice", "gclid-alice-0001")
    bob = lead_form("Bob", "gclid-bob-000002")
    plain = client.post(f"{API}/leads", json={"first_name": "No", "last_name": "Click", "email": "noclick@buyer.example"}, headers=h).json()["id"]
    for lead_id in (alice, bob, plain):
        assert client.patch(f"{API}/leads/{lead_id}", json={"status": "qualified"}, headers=h).status_code == 200

    ads.upload_errors = {1: "CLICK_NOT_FOUND"}
    run_ads(ws)
    uploads = ads.uploads()
    assert len(uploads) == 1 and uploads[0]["partialFailure"] is True
    items = uploads[0]["conversions"]
    assert [i["gclid"] for i in items] == ["gclid-alice-0001", "gclid-bob-000002"]
    first = items[0]
    assert first["conversionAction"] == ACTION and first["conversionValue"] == 25.0 and first["currencyCode"] == "EUR"
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\+00:00", first["conversionDateTime"]) and first["orderId"].startswith("aiva_")
    assert first["consent"] == {"adUserData": "GRANTED"}
    rows = conversion_rows(ws)
    assert sorted(r["status"] for r in rows) == ["pending", "sent", "skipped"]  # Bob retries later; no click for the third
    skipped = next(r for r in rows if r["status"] == "skipped")
    assert "click ID" in skipped["message"]

    # Requalifying Alice doesn't count twice.
    client.patch(f"{API}/leads/{alice}", json={"status": "contacted"}, headers=h)
    client.patch(f"{API}/leads/{alice}", json={"status": "qualified"}, headers=h)
    # Alice becomes a deal and wins it: the value is the deal amount, the click comes from her lead.
    converted = client.post(f"{API}/leads/{alice}/convert", json={"opportunity_name": "Alice deal", "opportunity_amount": "1234.50"}, headers=h)
    assert converted.status_code == 200, converted.text
    won = stages(client, h)["won"]
    assert client.patch(f"{API}/opportunities/{converted.json()['opportunity_id']}", json={"stage_id": won}, headers=h).status_code == 200
    run_ads(ws)
    second = ads.uploads()[-1]["conversions"]
    deal = [c for c in second if c.get("conversionValue") == 1234.5]
    assert deal and deal[0]["gclid"] == "gclid-alice-0001"
    rows = conversion_rows(ws)
    assert any(r["status"] == "duplicate" and r["moment"] == "lead_qualified" for r in rows)
    assert any(r["status"] == "sent" and r["moment"] == "deal_won" for r in rows)

    log = client.get(f"{API}/ads/google/conversions", headers=h).json()["items"]
    assert {i["status"] for i in log} >= {"sent", "skipped", "duplicate"}
    failed = next(i for i in log if i["status"] == "skipped")
    assert client.post(f"{API}/ads/google/conversions/{failed['id']}/retry", headers=h).json()["status"] == "pending"


def test_campaign_performance_and_click_resolution(client, ads, monkeypatch):
    ws = signup(client, "Performance Co")
    h = ws["headers"]
    detail = connect_google_ads(client, ws, monkeypatch)
    client.put(f"{API}/integration-hub/google_ads/settings", json={"config": {"customer_id": CLIENT}}, headers=h)
    from datetime import date, timedelta

    today = date.today().isoformat()
    ads.performance = [
        {"campaign": {"id": "11", "name": "Brand", "status": "ENABLED"}, "segments": {"date": today}, "metrics": {"clicks": "40", "impressions": "900", "costMicros": "120500000", "conversions": 3.0}},
        {"campaign": {"id": "11", "name": "Brand", "status": "ENABLED"}, "segments": {"date": (date.today() - timedelta(days=1)).isoformat()}, "metrics": {"clicks": "10", "impressions": "100", "costMicros": "29500000"}},
        {"campaign": {"id": "22", "name": "Generic", "status": "PAUSED"}, "segments": {"date": today}, "metrics": {"clicks": "5", "impressions": "50", "costMicros": "10000000"}},
    ]
    ads.clicks = [{"clickView": {"gclid": "gclid-carol-0003", "keywordInfo": {"text": "crm software"}}, "campaign": {"id": "11", "name": "Brand"}, "adGroup": {"id": "5", "name": "Exact"}}]
    url = local_path(detail["extra"]["webhook_url"])
    client.post(url, json={"google_key": detail["extra"]["lead_form_key"], "lead_id": "L-carol", "gcl_id": "gclid-carol-0003", "user_column_data": [{"column_id": "EMAIL", "column_name": "Email", "string_value": "carol@buyer.example"}]})
    carol = next(l["id"] for l in client.get(f"{API}/leads", params={"search": "carol@buyer.example"}, headers=h).json())
    # The lead form gives the campaign directly; drop it to exercise the click lookup.
    async def strip(db):
        from app.models.lead import Lead

        lead = await db.get(Lead, uuid.UUID(carol))
        fields = dict(lead.custom_fields)
        fields["_attribution"] = {k: v for k, v in fields["_attribution"].items() if k != "google_campaign_id"}
        lead.custom_fields = fields

    run_service(strip)
    run_ads(ws)
    stored = run_service(lambda db: _attribution(db, carol))
    assert stored["google_campaign_id"] == "11" and stored["google_keyword"] == "crm software" and stored["google_click_checked"]

    report = client.get(f"{API}/ads/google/performance", params={"days": 30}, headers=h)
    assert report.status_code == 200, report.text
    data = report.json()
    brand = next(c for c in data["campaigns"] if c["campaign_id"] == "11")
    assert brand["cost"] == 150.0 and brand["clicks"] == 50 and brand["leads"] == 1 and data["currency"] == "EUR"
    assert data["totals"]["cost"] == 160.0
    # Running again updates the same days instead of adding rows.
    run_ads(ws)
    again = client.get(f"{API}/ads/google/performance", headers=h).json()
    assert again["totals"]["cost"] == 160.0


def test_google_ads_quota_backs_off(client, ads, monkeypatch):
    ws = signup(client, "Quota Co")
    h = ws["headers"]
    connect_google_ads(client, ws, monkeypatch)
    client.put(f"{API}/integration-hub/google_ads/settings", json={"config": {"customer_id": CLIENT}}, headers=h)

    async def busy(method, url, **kwargs):
        return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [{"errors": [{"errorCode": {"quotaError": "RESOURCE_EXHAUSTED"}, "message": "Too many requests", "details": {"quotaErrorDetails": {"retryDelay": "120s"}}}]}]}})

    from app.integrations.providers import _ads_api

    monkeypatch.setattr(_ads_api, "http", busy)
    result = client.post(f"{API}/ads/google/sync", headers=h)
    assert result.status_code == 200
    connection = client.get(f"{API}/integration-hub/google_ads", headers=h).json()["connection"]
    assert connection["config"].get("busy_until")


# ---------------------------------------------------------------------------
# Google Analytics 4
# ---------------------------------------------------------------------------


class FakeGA:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.debug_messages: list[dict[str, Any]] = []

    async def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        params = kwargs.get("params") or {}
        assert params.get("api_secret") == "ga-secret-value-1" and params.get("measurement_id") == "G-TEST12345"
        if "/debug/mp/collect" in url:
            return httpx.Response(200, json={"validationMessages": self.debug_messages})
        self.sent.append(kwargs.get("json") or {})
        return httpx.Response(204)


def test_ga4_events_with_client_id_and_stand_in(client, monkeypatch):
    from app.integrations.providers import analytics

    fake = FakeGA()
    monkeypatch.setattr(analytics, "http", fake)
    ws = signup(client, "GA Co")
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/google_analytics/toggle", json={"enabled": True}, headers=h).status_code == 200
    fake.debug_messages = [{"fieldPath": "events", "description": "bad", "validationCode": "VALUE_INVALID"}]
    refused = client.put(f"{API}/integration-hub/google_analytics/credentials", json={"values": {"measurement_id": "g-test12345", "api_secret": "ga-secret-value-1"}}, headers=h)
    assert refused.status_code == 422
    fake.debug_messages = []
    assert client.put(f"{API}/integration-hub/google_analytics/credentials", json={"values": {"measurement_id": "UA-123", "api_secret": "ga-secret-value-1"}}, headers=h).status_code == 422
    saved = client.put(f"{API}/integration-hub/google_analytics/credentials", json={"values": {"measurement_id": "g-test12345", "api_secret": "ga-secret-value-1"}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert saved.json()["extra"]["measurement_id"] == "G-TEST12345" and "ga-secret-value-1" not in saved.text
    bad_name = client.put(f"{API}/integration-hub/google_analytics/settings", json={"config": {"events": {"lead_created": {"enabled": True, "event": "first_visit"}}}}, headers=h)
    assert bad_name.status_code == 422
    client.put(f"{API}/integration-hub/google_analytics/settings", json={"config": {"events": {"deal_won": {"enabled": True, "event": "close_convert_lead"}}, "send_user_id": True}}, headers=h)

    # A lead from an AIVA form with the visitor's GA client ID, and one without.
    form = client.post(f"{API}/forms", json={"name": "Demo request"}, headers=h).json()
    submitted = client.post(f"{API}/public/forms/{form['slug']}/submit", json={
        "data": {"first_name": "Gina", "last_name": "Analyst", "email": "gina@visitor.example"},
        "attribution": {"ga_client_id": "1234567890.1700000000", "gbraid": "x", "utm_source": "google"},
    })
    assert submitted.status_code == 200, submitted.text
    client.post(f"{API}/leads", json={"first_name": "Phil", "last_name": "Phone", "email": "phil@offline.example"}, headers=h)

    from app.integrations.providers import analytics as module

    run_service(lambda db: module.run_due(db))
    assert len(fake.sent) == 2
    by_client = {body["client_id"]: body for body in fake.sent}
    gina = by_client["1234567890.1700000000"]
    assert gina["events"][0]["name"] == "generate_lead" and gina["events"][0]["params"]["lead_source"] == "web_form"
    assert gina["user_id"] and "@" not in json.dumps(fake.sent)  # no emails or names
    stand_in = next(body for cid, body in by_client.items() if cid != "1234567890.1700000000")
    assert re.fullmatch(r"\d+\.\d+", stand_in["client_id"])
    rows = conversion_rows(ws, "google_analytics")
    assert [r["status"] for r in rows] == ["sent", "sent"]
    log = client.get(f"{API}/ga4/events", headers=h).json()["items"]
    assert any("stand-in" in (i["message"] or "") for i in log)

    # Without stand-ins, people with no GA client ID are skipped.
    client.put(f"{API}/integration-hub/google_analytics/settings", json={"config": {"pseudo_client_id": False}}, headers=h)
    client.post(f"{API}/leads", json={"first_name": "Quinn", "last_name": "Quiet", "email": "quinn@offline.example"}, headers=h)
    run_service(lambda db: module.run_due(db))
    assert len(fake.sent) == 2 and conversion_rows(ws, "google_analytics")[-1]["status"] == "skipped"

    test_event = client.post(f"{API}/ga4/test-event", json={"moment": "deal_won", "send": True}, headers=h)
    assert test_event.status_code == 200 and test_event.json()["valid"] and test_event.json()["sent"]
    assert test_event.json()["payload"]["events"][0]["params"]["value"] == 100.0
    assert len(fake.sent) == 3


# ---------------------------------------------------------------------------
# Analytics feed (Power BI, Looker Studio)
# ---------------------------------------------------------------------------


def api_key(client, headers: dict, scope: str = "read") -> dict:
    created = client.post(f"{API}/api-keys", json={"name": "BI", "scope": scope}, headers=headers)
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['key']}"}


def test_feed_paging_incremental_schema_and_connectors(client):
    ws = signup(client, "Feed Co")
    h = ws["headers"]
    key = api_key(client, h)
    off = client.get(f"{API}/analytics-feed/leads", headers=key)
    assert off.status_code == 409  # Power BI or Looker Studio must be on
    assert client.put(f"{API}/integration-hub/power_bi/toggle", json={"enabled": True}, headers=h).status_code == 200

    secret = client.post(f"{API}/custom-fields", json={"object_type": "lead", "label": "Tax ID", "field_type": "text", "is_encrypted": True}, headers=h)
    visible = client.post(f"{API}/custom-fields", json={"object_type": "lead", "label": "Budget", "field_type": "number"}, headers=h)
    assert secret.status_code == 201 and visible.status_code == 201, (secret.text, visible.text)
    secret_key, budget_key = secret.json()["key"], visible.json()["key"]
    for index in range(5):
        created = client.post(f"{API}/leads", json={"first_name": f"Feed{index}", "last_name": "Lead", "email": f"feed{index}@feed.example"}, headers=h)
        assert created.status_code in (200, 201), created.text
        values = client.put(f"{API}/custom-fields/values/lead/{created.json()['id']}", json={"values": {secret_key: f"SECRET-{index}", budget_key: 1000 + index}}, headers=h)
        assert values.status_code == 200, values.text

    schema = client.get(f"{API}/analytics-feed/leads/schema", headers=key).json()
    names = [c["name"] for c in schema["columns"]]
    assert f"cf_{budget_key}" in names and f"cf_{secret_key}" not in names and "notes" not in names
    assert next(c for c in schema["columns"] if c["name"] == "created_at")["type"] == "datetime"

    first = client.get(f"{API}/analytics-feed/leads", params={"limit": 3}, headers=key)
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["count"] == 3 and page["next_cursor"] and "cursor=" in page["next"] and page["next"].startswith("http")
    assert first.headers["cache-control"] == "no-store"
    rest = client.get(f"{API}/analytics-feed/leads", params={"limit": 3, "cursor": page["next_cursor"]}, headers=key).json()
    assert rest["count"] == 2 and rest["next_cursor"] is None
    everything = page["items"] + rest["items"]
    assert len({i["id"] for i in everything}) == 5
    assert all("SECRET" not in json.dumps(i) for i in everything)
    assert {i[f"cf_{budget_key}"] for i in everything} == {1000.0, 1001.0, 1002.0, 1003.0, 1004.0}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", everything[0]["created_at"])

    since = everything[-1]["updated_at"]
    client.patch(f"{API}/leads/{everything[0]['id']}", json={"status": "contacted"}, headers=h)
    changed = client.get(f"{API}/analytics-feed/leads", params={"updated_since": since}, headers=key).json()
    assert [i["id"] for i in changed["items"]] == [everything[0]["id"]]
    assert client.get(f"{API}/analytics-feed/leads", params={"cursor": "not-a-cursor"}, headers=key).status_code == 422
    assert client.get(f"{API}/analytics-feed/nothing", headers=key).status_code == 404

    deals = client.get(f"{API}/analytics-feed/pipeline_stages", headers=key).json()
    assert deals["count"] >= 3 and {"name", "category", "pipeline_name"} <= set(deals["items"][0])
    users = client.get(f"{API}/analytics-feed/users", headers=key).json()
    assert users["items"][0]["id"] == ws["user_id"]

    pq = client.get(f"{API}/analytics-feed/connectors/power-query.m", headers=h)
    assert pq.status_code == 200 and "AivaApiKey" in pq.text and "RelativePath" in pq.text
    client.put(f"{API}/integration-hub/looker_studio/toggle", json={"enabled": True}, headers=h)
    archive = client.get(f"{API}/analytics-feed/connectors/looker-studio.zip", headers=h)
    assert archive.status_code == 200
    files = zipfile.ZipFile(io.BytesIO(archive.content))
    manifest = json.loads(files.read("aiva-looker-studio-connector/appsscript.json"))
    assert manifest["dataStudio"]["authType"] == ["KEY"]
    assert "getData" in files.read("aiva-looker-studio-connector/Code.gs").decode()
    detail = client.get(f"{API}/integration-hub/power_bi", headers=h).json()
    assert "Web.Contents" in detail["extra"]["power_query"] and detail["extra"]["feed_url"].endswith("/api/aiva/analytics-feed")


def test_feed_respects_record_visibility_and_permissions(client):
    ws = signup(client, "Visibility Co")
    h = ws["headers"]
    client.put(f"{API}/integration-hub/looker_studio/toggle", json={"enabled": True}, headers=h)
    role = client.post(f"{API}/admin/roles", json={"name": "BI analyst", "permissions": ["records.read", "records.write", "records.export"]}, headers=h)
    assert role.status_code == 201, role.text
    email = f"analyst-{uuid.uuid4().hex[:8]}@feed.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Ana Lyst", "role": role.json()["key"]}, headers=h)
    assert invited.status_code in (200, 201), invited.text
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited.json()["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    analyst = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.put(f"{API}/org/visibility", json={"mode": "hierarchy"}, headers=h).status_code == 200

    owner_lead = client.post(f"{API}/leads", json={"first_name": "Owner", "last_name": "Only", "email": "owner-only@feed.example"}, headers=h).json()
    analyst_lead = client.post(f"{API}/leads", json={"first_name": "Analyst", "last_name": "Own", "email": "analyst-own@feed.example"}, headers=analyst).json()
    task = client.post(f"{API}/tasks", json={"title": "Owner's private task", "assigned_to_user_id": ws["user_id"]}, headers=h)
    assert task.status_code in (200, 201), task.text
    assert any(t["title"] == "Owner's private task" for t in client.get(f"{API}/analytics-feed/tasks", headers=api_key(client, h)).json()["items"])

    analyst_key = api_key(client, analyst)
    owner_key = api_key(client, h)
    mine = {i["id"] for i in client.get(f"{API}/analytics-feed/leads", headers=analyst_key).json()["items"]}
    assert analyst_lead["id"] in mine and owner_lead["id"] not in mine
    everyone = {i["id"] for i in client.get(f"{API}/analytics-feed/leads", headers=owner_key).json()["items"]}
    assert {analyst_lead["id"], owner_lead["id"]} <= everyone
    tasks = client.get(f"{API}/analytics-feed/tasks", headers=analyst_key).json()["items"]
    assert all(t["title"] != "Owner's private task" for t in tasks)

    # People who can't export records can't read the feed.
    viewer_email = f"viewer-{uuid.uuid4().hex[:8]}@feed.example"
    viewer_invite = client.post(f"{API}/admin/users/invite", json={"email": viewer_email, "full_name": "Vi Ewer", "role": "viewer"}, headers=h).json()
    viewer = client.post(f"{API}/auth/invitations/accept", json={"token": viewer_invite["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/analytics-feed/leads", headers=viewer_headers).status_code == 403
