"""Lead capture (WordPress Forms, Facebook Lead Ads) and automation webhooks
(Zapier, Make, n8n).

Nothing here reaches the internet: outgoing webhook sends, DNS lookups and every
Graph API call are replaced with fakes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import re
import time
import uuid
import zipfile
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app

API = "/api/v1"
PASSWORD = "Automation#2026"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Capture Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@capture.example"
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


def run(coro_fn):
    """Run an async function against a fresh database session (for the scheduler job)."""

    from app.db.session import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            return await coro_fn(db)

    return asyncio.run(go())


def leads_with_email(org: str, email: str):
    from app.models.lead import Lead

    async def query(db):
        return (await db.execute(select(Lead).where(Lead.organization_id == uuid.UUID(org), Lead.email == email))).scalars().all()

    return run(query)


@pytest.fixture()
def sent(monkeypatch):
    """Capture outgoing webhooks instead of sending them; DNS always resolves publicly."""

    from app.integrations.providers import automation as hooks

    calls: list[dict] = []
    responses: list[int] = []

    async def fake_resolve(host, port):
        return ["93.184.216.34"]

    async def fake_post(url, body, headers):
        status = responses.pop(0) if responses else 200
        calls.append({"url": url, "body": body, "headers": headers, "status": status})
        ok = 200 <= status < 300
        return hooks.SendResult(ok, status, None if ok else f"HTTP {status}", gone=status == 410)

    monkeypatch.setattr(hooks, "_resolve", fake_resolve)
    monkeypatch.setattr(hooks, "_post", fake_post)
    return {"calls": calls, "responses": responses}


# ===========================================================================
# Units
# ===========================================================================


def test_signature_round_trip_and_rejections():
    from app.integrations.providers.automation import sign, verify_signature

    body = b'{"a":1}'
    now = int(time.time())
    header = sign("whsec_test", body, now)
    assert verify_signature("whsec_test", body, header)
    assert not verify_signature("whsec_other", body, header)
    assert not verify_signature("whsec_test", b'{"a":2}', header)
    assert not verify_signature("whsec_test", body, sign("whsec_test", body, now - 3600))  # too old (replay)
    assert not verify_signature("whsec_test", body, "t=abc,v1=00")


def test_event_mapping():
    from types import SimpleNamespace

    from app.integrations.providers.automation import events_for

    stage = str(uuid.uuid4())
    context = {"stages": {stage: {"name": "Closed won", "category": "won"}}}
    deal = SimpleNamespace(stage_id=stage)
    assert events_for("created", "lead", None, {}, context) == ["lead.created"]
    assert events_for("updated", "opportunity", deal, {"stage_id": {"from": "x", "to": stage}}, context) == ["deal.updated", "deal.stage_changed", "deal.won"]
    assert events_for("updated", "lead", None, {"converted_at": {"from": None, "to": "2026-01-01"}}, context) == ["lead.updated", "lead.converted"]
    assert events_for("updated", "task", None, {"status": {"from": "open", "to": "completed"}}, context) == ["task.completed"]
    assert events_for("created", "kb_article", None, {}, context) == []


def test_parse_wordpress_formats_and_mapping():
    from app.integrations.providers.lead_capture import apply_answers
    from app.integrations.providers.wordpress import form_key, parse_submission

    # The AIVA plugin's own format.
    meta, answers = parse_submission(json.dumps({
        "plugin": "contact-form-7", "form_id": "12", "form_name": "Contact",
        "fields": [
            {"key": "your-name", "label": "your-name", "value": "Grace Hopper"},
            {"key": "your-email", "label": "your-email", "value": "Grace@Navy.example"},
            {"key": "your-message", "label": "your-message", "value": "Call me"},
            {"key": "card-number", "label": "Card number", "value": "4111111111111111"},
        ],
        "utm": {"utm_source": "google"},
    }).encode(), "application/json")
    assert form_key(meta) == "contact_form_7:12" and meta["utm"]["utm_source"] == "google"
    values, _, message, extra, _ = apply_answers(answers, {})
    assert values == {"first_name": "Grace", "last_name": "Hopper", "email": "grace@navy.example"}
    assert message == ["Call me"]
    assert not any("4111" in line for line in [*message, *extra])  # card numbers are never kept

    # Elementor's form-encoded webhook.
    body = "form%5Bid%5D=abc&form%5Bname%5D=Demo&fields%5Bemail%5D%5Bvalue%5D=e%40x.example&fields%5Bemail%5D%5Btitle%5D=Email&fields%5Bname%5D%5Bvalue%5D=Linus&fields%5Bname%5D%5Btitle%5D=Name"
    meta, answers = parse_submission(body.encode(), "application/x-www-form-urlencoded")
    assert meta["form_name"] == "Demo"
    values, *_ = apply_answers(answers, {})
    assert values["email"] == "e@x.example" and values["last_name"] == "Linus"

    # Fluent Forms nested names, flat JSON; Gravity numeric keys need a mapping.
    meta, answers = parse_submission(json.dumps({"names": {"first_name": "Ana", "last_name": "Diaz"}, "email": "ana@d.example", "_wpnonce": "x"}).encode(), "application/json")
    values, *_ = apply_answers(answers, {})
    assert values == {"first_name": "Ana", "last_name": "Diaz", "email": "ana@d.example"}
    meta, answers = parse_submission(json.dumps({"1.3": "Bo", "1.6": "Li", "2": "bo@li.example", "3": "Initech"}).encode(), "application/json")
    values, *_ = apply_answers(answers, {"1.3": "first_name", "1.6": "last_name", "3": "company_name"})
    assert values == {"first_name": "Bo", "last_name": "Li", "email": "bo@li.example", "company_name": "Initech"}


# ===========================================================================
# Outgoing webhooks
# ===========================================================================


def test_subscription_delivers_signed_events(client, sent):
    from app.integrations.providers import automation as hooks
    from app.integrations.providers.automation import verify_signature

    ws = signup(client)
    h = ws["headers"]
    refused = client.post(f"{API}/integration-webhooks/subscriptions", json={"provider": "zapier", "events": ["lead.created"], "target_url": "https://hooks.zapier.example/hooks/catch/1"}, headers=h)
    assert refused.status_code == 409  # Zapier is off
    assert client.put(f"{API}/integration-hub/zapier/toggle", json={"enabled": True}, headers=h).status_code == 200
    created = client.post(
        f"{API}/integration-webhooks/subscriptions",
        json={"provider": "zapier", "events": ["lead.created", "lead.updated"], "target_url": "https://hooks.zapier.example/hooks/catch/1", "label": "Sales Zap"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    secret = created.json()["secret"]
    assert secret.startswith("whsec_") and len(created.json()["items"]) == 2
    listed = client.get(f"{API}/integration-webhooks/subscriptions", headers=h).json()["items"]
    assert len(listed) == 2 and secret not in json.dumps(listed)
    assert client.get(f"{API}/integration-hub/zapier", headers=h).json()["status"] == "connected"

    email = f"zap-{uuid.uuid4().hex[:6]}@lead.example"
    lead = client.post(f"{API}/leads", json={"first_name": "Zed", "last_name": "Zapper", "email": email, "company_name": "Zap Co"}, headers=h)
    assert lead.status_code == 201, lead.text
    queued = client.get(f"{API}/integration-webhooks/deliveries", params={"status": "pending"}, headers=h).json()["items"]
    assert [d["event"] for d in queued] == ["lead.created"]

    run(hooks.run_due)
    assert len(sent["calls"]) == 1
    call = sent["calls"][0]
    assert verify_signature(secret, call["body"], call["headers"]["X-AIVA-Signature"])
    body = json.loads(call["body"])
    assert body["event"] == "lead.created" and body["object"] == "lead" and body["data"]["email"] == email
    assert body["data"]["url"].endswith(f"/leads/{lead.json()['id']}") and "organization_id" not in body["data"]
    assert call["headers"]["X-AIVA-Event"] == "lead.created" and call["headers"]["X-AIVA-Delivery"] == body["id"]
    delivered = client.get(f"{API}/integration-webhooks/deliveries", headers=h).json()["items"]
    assert delivered[0]["status"] == "delivered" and delivered[0]["attempts"] == 1

    # An update sends lead.updated with what changed.
    client.patch(f"{API}/leads/{lead.json()['id']}", json={"job_title": "CTO"}, headers=h)
    run(hooks.run_due)
    update = json.loads(sent["calls"][-1]["body"])
    assert update["event"] == "lead.updated" and update["changes"]["job_title"] == {"from": None, "to": "CTO"}

    # Switched off: nothing new is queued.
    client.put(f"{API}/integration-hub/zapier/toggle", json={"enabled": False}, headers=h)
    client.post(f"{API}/leads", json={"first_name": "Off", "last_name": "Switch", "email": f"off-{uuid.uuid4().hex[:6]}@lead.example"}, headers=h)
    assert not client.get(f"{API}/integration-webhooks/deliveries", params={"status": "pending"}, headers=h).json()["items"]


def test_retries_backoff_pause_and_redeliver(client, sent):
    from app.integrations.providers import automation as hooks

    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/make/toggle", json={"enabled": True}, headers=h)
    sub = client.post(f"{API}/integration-webhooks/subscriptions", json={"provider": "make", "events": ["*"], "target_url": "https://hook.make.example/abc"}, headers=h).json()["items"][0]
    assert sub["event"] == "*"

    sent["responses"].extend([500])
    client.post(f"{API}/leads", json={"first_name": "Re", "last_name": "Try", "email": f"retry-{uuid.uuid4().hex[:6]}@lead.example"}, headers=h)
    run(hooks.run_due)
    pending = client.get(f"{API}/integration-webhooks/deliveries", headers=h).json()["items"][0]
    assert pending["status"] == "pending" and pending["attempts"] == 1 and pending["error"] == "HTTP 500" and pending["next_attempt_at"]
    state = client.get(f"{API}/integration-webhooks/subscriptions", headers=h).json()["items"][0]
    assert state["consecutive_failures"] == 1 and state["is_active"]

    # Not due yet: the job leaves it alone.
    before = len(sent["calls"])
    run(hooks.run_due)
    assert len(sent["calls"]) == before

    # Redeliver now works and resets the failure count.
    again = client.post(f"{API}/integration-webhooks/deliveries/{pending['id']}/redeliver", headers=h)
    assert again.status_code == 200 and again.json()["ok"], again.text
    assert json.loads(sent["calls"][-1]["body"])["redelivery_of"] == pending["id"]

    # Test event, then a 410 Gone pauses the subscription and cancels what's queued.
    tested = client.post(f"{API}/integration-webhooks/subscriptions/{sub['id']}/test", headers=h).json()
    assert tested["ok"] and tested["delivery"]["test"]
    sent["responses"].append(410)
    failed = client.post(f"{API}/integration-webhooks/subscriptions/{sub['id']}/test", headers=h).json()
    assert not failed["ok"]
    state = client.get(f"{API}/integration-webhooks/subscriptions", headers=h).json()["items"][0]
    assert state["status"] == "paused"
    resumed = client.patch(f"{API}/integration-webhooks/subscriptions/{sub['id']}", json={"is_active": True}, headers=h)
    assert resumed.status_code == 200 and resumed.json()["is_active"] and resumed.json()["consecutive_failures"] == 0

    rotated = client.post(f"{API}/integration-webhooks/subscriptions/{sub['id']}/rotate-secret", headers=h).json()
    assert rotated["secret"].startswith("whsec_")
    payload = client.get(f"{API}/integration-webhooks/deliveries/{pending['id']}", headers=h).json()
    assert payload["payload"]["event"] == "lead.created"
    assert client.delete(f"{API}/integration-webhooks/subscriptions/{sub['id']}", headers=h).status_code == 204
    assert client.get(f"{API}/integration-webhooks/subscriptions", headers=h).json()["items"] == []


def test_rest_hooks_with_api_key(client, sent):
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/n8n/toggle", json={"enabled": True}, headers=h)
    key = client.post(f"{API}/api-keys", json={"name": "n8n"}, headers=h).json()["key"]
    read_only = client.post(f"{API}/api-keys", json={"name": "ro", "scope": "read"}, headers=h).json()["key"]
    k = {"Authorization": f"Bearer {key}"}

    me = client.get(f"{API}/hooks/me", headers=k).json()
    assert me["ok"] and me["api_key"] and me["can_subscribe"] and me["workspace_id"] == ws["org"]
    events = client.get(f"{API}/hooks/events", headers=k).json()
    assert {"lead.created", "deal.won", "task.completed"} <= {e["key"] for e in events}

    made = client.post(f"{API}/hooks", json={"event": "deal.won", "hookUrl": "https://n8n.example/webhook/x", "provider": "n8n"}, headers=k)
    assert made.status_code == 201, made.text
    assert made.json()["secret"].startswith("whsec_")
    assert client.post(f"{API}/hooks", json={"event": "deal.won", "target_url": "https://n8n.example/webhook/y"}, headers={"Authorization": f"Bearer {read_only}"}).status_code == 403
    assert client.post(f"{API}/hooks", json={"event": "nope", "target_url": "https://n8n.example/webhook/y", "provider": "n8n"}, headers=k).status_code == 422
    listed = client.get(f"{API}/hooks", headers=k).json()
    assert [s["event"] for s in listed] == ["deal.won"]

    client.post(f"{API}/leads", json={"first_name": "Sam", "last_name": "Ple", "email": f"sample-{uuid.uuid4().hex[:6]}@lead.example"}, headers=h)
    samples = client.get(f"{API}/hooks/samples/lead.created", headers=k).json()
    assert samples and samples[0]["event"] == "lead.created" and samples[0]["data"]["last_name"] == "Ple"
    typed = client.get(f"{API}/hooks/samples/quote.accepted", headers=k).json()
    assert typed[0]["data"]["label"] == "Example record"

    assert client.delete(f"{API}/hooks/{made.json()['id']}", headers=k).status_code == 204
    assert client.delete(f"{API}/hooks/{made.json()['id']}", headers=k).status_code == 204  # twice is fine
    assert client.get(f"{API}/hooks", headers=k).json() == []

    # Members can't send workspace data out.
    email = f"member-{uuid.uuid4().hex[:8]}@capture.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Mo Member", "role": "member"}, headers=h).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/integration-webhooks/subscriptions", headers=member).status_code == 403


def test_target_urls_must_be_public_https(client, monkeypatch):
    import socket

    from app.integrations.providers import automation as hooks

    monkeypatch.setattr(hooks, "_private_allowed", lambda: False)
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/zapier/toggle", json={"enabled": True}, headers=h)

    def post(url):
        return client.post(f"{API}/integration-webhooks/subscriptions", json={"provider": "zapier", "events": ["lead.created"], "target_url": url}, headers=h)

    assert "https" in post("http://hooks.zapier.example/a").json()["detail"]
    assert post("https://127.0.0.1/a").status_code == 422
    assert post("https://[::1]/a").status_code == 422
    assert post("https://169.254.169.254/latest/meta-data").status_code == 422
    assert post("https://localhost/a").status_code == 422
    assert post("https://user:pw@hooks.zapier.example/a").status_code == 422

    real = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "sneaky.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    refused = post("https://sneaky.example/hook")
    assert refused.status_code == 422 and "private" in refused.json()["detail"]

    async def check():
        result = await hooks.send("https://sneaky.example/hook", b"{}", {})
        return result

    result = asyncio.run(check())
    assert not result.ok and "private" in (result.error or "")


# ===========================================================================
# WordPress Forms
# ===========================================================================


def _signed(secret: str, body: bytes) -> str:
    ts = int(time.time())
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def test_wordpress_plugin_and_submissions(client):
    ws = signup(client)
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/wordpress_forms/toggle", json={"enabled": True}, headers=h).status_code == 200
    detail = client.get(f"{API}/integration-hub/wordpress_forms", headers=h).json()
    extra = detail["extra"]
    assert extra["webhook_url"] and extra["has_secret"]
    path = urlparse(extra["webhook_url"]).path.replace("/api/aiva/", f"{API}/")

    plugin = client.get(f"{API}/lead-capture/wordpress/plugin.zip", headers=h)
    assert plugin.status_code == 200 and plugin.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(plugin.content))
    assert "aiva-crm-forms/aiva-crm-forms.php" in archive.namelist()
    config = archive.read("aiva-crm-forms/aiva-config.php").decode()
    secret = re.search(r"'secret'\s*=>\s*'([^']+)'", config).group(1)
    assert extra["webhook_url"] in config

    email = f"wp-{uuid.uuid4().hex[:6]}@site.example"
    body = json.dumps({
        "plugin": "wpforms", "form_id": "7", "form_name": "Quote request", "site_url": "https://site.example/",
        "fields": [
            {"key": "field_1_first", "label": "First name", "value": "Wanda"},
            {"key": "field_1_last", "label": "Last name", "value": "Press"},
            {"key": "field_2", "label": "Email", "value": email},
            {"key": "field_3", "label": "Organisation", "value": "Plugins Ltd"},
            {"key": "field_4", "label": "Budget", "value": "10k"},
            {"key": "field_5", "label": "Password", "type": "password", "value": "hunter2"},
        ],
    }).encode()
    ok = client.post(path, content=body, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed(secret, body)})
    assert ok.status_code == 200 and ok.json()["result"] == "created", ok.text
    [lead] = leads_with_email(ws["org"], email)
    assert (lead.first_name, lead.last_name, lead.company_name, lead.source) == ("Wanda", "Press", "Plugins Ltd", "wordpress")
    assert "Budget: 10k" in lead.notes and "hunter2" not in (lead.notes or "")
    assert lead.custom_fields["_attribution"]["form"] == "Quote request"

    # Same person again: the lead is updated, not duplicated.
    again = json.dumps({"plugin": "wpforms", "form_id": "7", "form_name": "Quote request", "fields": [
        {"key": "field_2", "label": "Email", "value": email.upper()}, {"key": "field_6", "label": "Phone", "value": "+44 20 7946 0000"}]}).encode()
    assert client.post(path, content=again, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed(secret, again)}).json()["result"] == "updated"
    [lead] = leads_with_email(ws["org"], email)
    assert lead.phone == "+44 20 7946 0000"

    # Bad signatures are refused; a static secret header works for simple senders.
    assert client.post(path, content=body, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed("wrong", body)}).status_code == 401
    form = f"your-name=Fiona+Form&your-email=fiona-{uuid.uuid4().hex[:5]}%40site.example&your-message=Hi"
    assert client.post(path, content=form, headers={"Content-Type": "application/x-www-form-urlencoded", "X-AIVA-Secret": secret}).json()["result"] == "created"
    assert client.post(path, content=form, headers={"Content-Type": "application/x-www-form-urlencoded", "X-AIVA-Secret": "nope"}).status_code == 401

    # Require signatures; map a field; switch a form off.
    seen = client.get(f"{API}/integration-hub/wordpress_forms", headers=h).json()["extra"]["seen_forms"]
    assert any(f["key"] == "wpforms:7" and any(x["key"] == "field_4" for x in f["fields"]) for f in seen)
    assert all(x["key"] != "field_5" for f in seen for x in f["fields"])  # password fields aren't remembered
    saved = client.put(f"{API}/integration-hub/wordpress_forms/settings", json={"config": {
        "require_signature": True,
        "defaults": {"lead_source": "website", "on_duplicate": "skip", "owner_user_id": ws["user_id"], "tags": ["inbound"]},
        "forms": {"wpforms:7": {"mapping": {"field_4": "job_title"}}, "form": {"enabled": False}},
    }}, headers=h)
    assert saved.status_code == 200, saved.text
    assert client.put(f"{API}/integration-hub/wordpress_forms/settings", json={"config": {"forms": {"x": {"mapping": {"a": "salary"}}}}}, headers=h).status_code == 422
    assert client.post(path, content=form, headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 401
    email2 = f"wp2-{uuid.uuid4().hex[:6]}@site.example"
    body2 = json.dumps({"plugin": "wpforms", "form_id": "7", "fields": [{"key": "field_2", "label": "Email", "value": email2}, {"key": "field_4", "label": "Budget", "value": "Founder"}]}).encode()
    assert client.post(path, content=body2, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed(secret, body2)}).json()["result"] == "created"
    [lead2] = leads_with_email(ws["org"], email2)
    assert lead2.job_title == "Founder" and lead2.source == "website" and str(lead2.owner_user_id) == ws["user_id"]
    assert lead2.custom_fields["_attribution"]["tags"] == ["inbound"]
    assert client.post(path, content=body2, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed(secret, body2)}).json()["result"] == "skipped"
    off = json.dumps({"your-email": "x@y.example"}).encode()
    assert client.post(path, content=off, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed(secret, off)}).json()["result"] == "ignored"

    # Test from the WordPress plugin, preview from AIVA, then rotate the secret.
    ping = json.dumps({"aiva_test": True, "site_url": "https://site.example/", "fields": []}).encode()
    assert client.post(path, content=ping, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed(secret, ping)}).json()["result"] == "test"
    preview = client.post(f"{API}/lead-capture/wordpress/test", json={"form_key": "wpforms:7"}, headers=h).json()
    assert preview["result"] == "preview" and preview["mapped"]["field_4"] == "job_title"
    new_secret = client.post(f"{API}/lead-capture/wordpress/secret", headers=h).json()["secret"]
    assert new_secret != secret
    assert client.post(path, content=ping, headers={"Content-Type": "application/json", "X-AIVA-Signature": _signed(secret, ping)}).status_code == 401
    assert client.post(path, content=b"x" * (300 * 1024), headers={"Content-Type": "application/json"}).status_code == 413
    recent = client.get(f"{API}/integration-hub/wordpress_forms", headers=h).json()["extra"]["recent"]
    assert {"lead_created", "lead_updated", "submission_rejected"} <= {r["action"] for r in recent}


# ===========================================================================
# Facebook Lead Ads
# ===========================================================================


def test_facebook_lead_ads_end_to_end(client, monkeypatch):
    from app.integrations import core, identity

    ws = signup(client)
    h = ws["headers"]
    page_id = str(int(time.time() * 1000) % 10**12 + 10**12)
    form_id = "77" + page_id[-6:]
    app_secret = "fb-" + uuid.uuid4().hex
    leads: dict[str, dict] = {}
    calls: list[tuple[str, str, str | None]] = []

    async def fake_request(db, connection, method, url, *, token=None, retry_auth=True, timeout=25, **kwargs):
        calls.append((method, url, token))
        request = httpx.Request(method, url)
        if url.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": [{"id": page_id, "name": "Acme Page", "category": "Software", "access_token": "PAGE-TOKEN", "tasks": ["ADVERTISE", "MANAGE"]}]}, request=request)
        if url.endswith(f"/{page_id}/subscribed_apps"):
            assert token == "PAGE-TOKEN"
            return httpx.Response(200, json={"success": True}, request=request)
        if url.endswith(f"/{page_id}/leadgen_forms"):
            return httpx.Response(200, json={"data": [{"id": form_id, "name": "Spring offer", "status": "ACTIVE", "questions": [
                {"key": "full_name", "label": "Full name", "type": "FULL_NAME"},
                {"key": "email", "label": "Email", "type": "EMAIL"},
                {"key": "what_is_your_budget?", "label": "What is your budget?", "type": "CUSTOM"}]}]}, request=request)
        if url.endswith(f"/{form_id}/leads"):
            return httpx.Response(200, json={"data": [lead for lead in leads.values() if lead.get("backfill")]}, request=request)
        lead_id = url.rsplit("/", 1)[1]
        if lead_id in leads:
            assert token == "PAGE-TOKEN"
            return httpx.Response(200, json=leads[lead_id], request=request)
        return httpx.Response(404, json={"error": {"message": "not found"}}, request=request)

    async def fake_token(family, app_creds, data):
        return {"access_token": "SHORT", "expires_in": 3600}

    async def fake_identify(family, key, tokens, app_creds):
        return {"label": "Ada on Facebook", "external_id": "fb-user", "access_token": "USER-TOKEN", "expires_in": 5184000}

    monkeypatch.setattr(core, "request", fake_request)
    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)

    client.put(f"{API}/integration-hub/apps/facebook", json={"client_id": "fb-app", "client_secret": app_secret}, headers=h)
    client.put(f"{API}/integration-hub/facebook_lead_ads/toggle", json={"enabled": True}, headers=h)
    url = client.post(f"{API}/integration-hub/facebook_lead_ads/connect", json={}, headers=h).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "c"}, headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert "connected=facebook_lead_ads" in back.headers["location"], back.headers["location"]
    detail = client.get(f"{API}/integration-hub/facebook_lead_ads", headers=h).json()
    extra = detail["extra"]
    assert [p["id"] for p in extra["pages"]] == [page_id] and extra["app_source"] == "workspace" and extra["verify_token"]
    assert "PAGE-TOKEN" not in json.dumps(detail) and "USER-TOKEN" not in json.dumps(detail)

    # Meta's verification handshake.
    hook = f"{API}/public/integrations/facebook_lead_ads/webhook/app"
    verified = client.get(hook, params={"hub.mode": "subscribe", "hub.verify_token": extra["verify_token"], "hub.challenge": "12345"})
    assert verified.status_code == 200 and verified.text == "12345"
    assert client.get(hook, params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}).status_code == 403

    subscribed = client.put(f"{API}/lead-capture/facebook/pages/{page_id}", json={"subscribed": True}, headers=h)
    assert subscribed.status_code == 200, subscribed.text
    assert subscribed.json()["extra"]["pages"][0]["subscribed"]
    assert subscribed.json()["extra"]["lead_forms"][page_id][0]["name"] == "Spring offer"
    client.put(f"{API}/integration-hub/facebook_lead_ads/settings", json={"config": {"forms": {form_id: {"mapping": {"what_is_your_budget?": "notes"}, "tags": ["spring"]}}}}, headers=h)

    email = f"fb-{uuid.uuid4().hex[:6]}@lead.example"
    leads["9001" + page_id[-4:]] = {"id": "9001" + page_id[-4:], "created_time": "2026-10-01T10:00:00+0000", "form_id": form_id, "ad_id": "1", "campaign_name": "Spring",
                                    "platform": "ig", "field_data": [{"name": "full_name", "values": ["Mia Meta"]}, {"name": "email", "values": [email]},
                                                                     {"name": "what_is_your_budget?", "values": ["$5k"]}]}
    body = json.dumps({"object": "page", "entry": [{"id": page_id, "time": int(time.time()), "changes": [
        {"field": "leadgen", "value": {"leadgen_id": "9001" + page_id[-4:], "page_id": page_id, "form_id": form_id, "created_time": int(time.time())}}]}]}).encode()
    signature = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(hook, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 401
    accepted = client.post(hook, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})
    assert accepted.status_code == 200 and accepted.json().get("created") == 1, accepted.text
    [lead] = leads_with_email(ws["org"], email)
    assert (lead.first_name, lead.last_name, lead.source) == ("Mia", "Meta", "facebook_ads")
    assert "What is your budget?: $5k" in lead.notes
    attribution = lead.custom_fields["_attribution"]
    assert attribution["facebook_lead_id"] == "9001" + page_id[-4:] and attribution["utm_source"] == "instagram" and attribution["tags"] == ["spring"]

    # Meta retries: the same lead is imported once.
    assert client.post(hook, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature}).json().get("duplicate") == 1
    assert len(leads_with_email(ws["org"], email)) == 1

    # Catch-up sync picks up a lead whose webhook never came.
    missed = f"missed-{uuid.uuid4().hex[:6]}@lead.example"
    leads["9002" + page_id[-4:]] = {"id": "9002" + page_id[-4:], "form_id": form_id, "backfill": True, "field_data": [{"name": "email", "values": [missed]}, {"name": "full_name", "values": ["Max Missed"]}]}
    synced = client.post(f"{API}/lead-capture/facebook/sync", headers=h)
    assert synced.status_code == 200 and synced.json()["imported"] == 1, synced.text
    assert len(leads_with_email(ws["org"], missed)) == 1

    # Switched off: verified notifications are ignored.
    client.put(f"{API}/integration-hub/facebook_lead_ads/toggle", json={"enabled": False}, headers=h)
    leads["9003" + page_id[-4:]] = {"id": "9003" + page_id[-4:], "form_id": form_id, "field_data": [{"name": "email", "values": ["off@lead.example"]}]}
    body = body.replace(("9001" + page_id[-4:]).encode(), ("9003" + page_id[-4:]).encode())
    signature = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(hook, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature}).status_code == 200
    assert not leads_with_email(ws["org"], "off@lead.example")

    # Disconnecting unsubscribes the page.
    client.put(f"{API}/integration-hub/facebook_lead_ads/toggle", json={"enabled": True}, headers=h)
    assert client.delete(f"{API}/integration-hub/facebook_lead_ads/connection", headers=h).status_code == 200
    assert ("DELETE", f"https://graph.facebook.com/v21.0/{page_id}/subscribed_apps", "PAGE-TOKEN") in calls


def test_sender_pins_the_checked_address_and_keeps_the_host(monkeypatch):
    """The real sender: connects to the resolved address, sends the original Host, doesn't follow redirects."""

    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.integrations.providers import automation as hooks

    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("content-length") or 0)
            seen.append({"host": self.headers.get("host"), "path": self.path, "body": self.rfile.read(length), "sig": self.headers.get("x-aiva-signature")})
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/")
            else:
                self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def fake_resolve(host, port_):
        return ["127.0.0.1"]

    monkeypatch.setattr(hooks, "_resolve", fake_resolve)
    try:
        ok = asyncio.run(hooks.send(f"http://hooks.example.test:{port}/catch?x=1", b'{"a":1}', {"Content-Type": "application/json", "X-AIVA-Signature": "t=1,v1=ab"}))
        assert ok.ok and ok.status == 204
        assert seen[0]["host"] == f"hooks.example.test:{port}" and seen[0]["path"] == "/catch?x=1" and seen[0]["body"] == b'{"a":1}'
        redirected = asyncio.run(hooks.send(f"http://hooks.example.test:{port}/redirect", b"{}", {}))
        assert not redirected.ok and "redirect" in (redirected.error or "") and len(seen) == 2
    finally:
        server.shutdown()


def test_a_slow_receiver_is_cut_off(monkeypatch):
    """A receiver that answers byte by byte can't hold the webhook queue (total deadline)."""
    import asyncio
    import time

    from app.integrations.providers import automation

    monkeypatch.setattr(automation, "TOTAL_SECONDS", 1.0)

    async def main():
        async def handler(reader, writer):
            await reader.read(65536)
            writer.write(b"HTTP/1.1 500 Oops\r\nContent-Length: 60\r\n\r\n")
            await writer.drain()
            for _ in range(60):
                await asyncio.sleep(0.2)
                writer.write(b"x")
                await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        started = time.monotonic()
        result = await automation.send(f"http://127.0.0.1:{port}/hook", b"{}", {"Content-Type": "application/json"})
        server.close()
        return result, time.monotonic() - started

    result, took = asyncio.run(main())
    assert not result.ok and took < 3 and "No answer" in (result.error or "")
