"""Messaging integrations: Twilio SMS, WhatsApp Cloud API and Slack.

Provider APIs are never called: ``messaging.http`` (Twilio, WhatsApp) and
``core.request`` / ``core._token_request`` (Slack) are replaced with fakes that
answer like the providers do. Webhooks are signed exactly as the providers sign
them, and wrongly signed ones must be refused.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Messaging#2026"
ACCOUNT_SID = "AC" + "1" * 32
NUMBER_SID = "PN" + "2" * 32
AUTH_TOKEN = "twilio-auth-token-123"
APP_SECRET = "0123456789abcdef0123456789abcdef"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Messaging Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@msg.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Mia Owner", "email": email, "password": PASSWORD, "company_name": company})
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
    """The public webhook URL (/api/aiva/...) as the backend serves it (/api/v1/...)."""

    parsed = urlparse(url)
    return parsed.path.replace("/api/aiva/", "/api/v1/", 1) + (f"?{parsed.query}" if parsed.query else "")


class FakeHTTP:
    """Stands in for ``messaging.http``: routes (method, url regex) to canned answers."""

    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, Any, int]] = []
        self.calls: list[tuple[str, str, dict]] = []

    def on(self, method: str, pattern: str, answer: Any, status: int = 200) -> None:
        self.routes.append((method, re.compile(pattern), answer, status))

    async def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        for route_method, pattern, answer, status in reversed(self.routes):
            if route_method == method and pattern.search(url):
                body = answer(method, url, kwargs) if callable(answer) else answer
                if isinstance(body, httpx.Response):
                    return body
                return httpx.Response(status, json=body, request=httpx.Request(method, url))
        raise AssertionError(f"Unexpected provider call: {method} {url}")

    def last(self, method: str, pattern: str) -> tuple[str, str, dict]:
        for call in reversed(self.calls):
            if call[0] == method and re.search(pattern, call[1]):
                return call
        raise AssertionError(f"No {method} call matching {pattern}")


@pytest.fixture()
def fake_http(monkeypatch):
    from app.integrations import messaging

    fake = FakeHTTP()
    monkeypatch.setattr(messaging, "http", fake)
    return fake


def twilio_sign(url: str, params: dict[str, str], token: str = AUTH_TOKEN) -> str:
    from app.integrations.providers.twilio_sms import signature_for

    return signature_for(token, url, list(params.items()))


def meta_sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_normalize_phone():
    from app.integrations.messaging import keyword, normalize_phone

    assert normalize_phone("(214) 555-0100") == "+12145550100"
    assert normalize_phone("1-214-555-0100 ext. 22") == "+12145550100"
    assert normalize_phone("+44 7700 900123") == "+447700900123"
    assert normalize_phone("07700 900123", "44") == "+447700900123"
    assert normalize_phone("0044 7700 900123") == "+447700900123"
    assert normalize_phone("whatsapp:+12145550100") == "+12145550100"
    assert normalize_phone("07700 900123") is None  # can't be a US number
    assert normalize_phone("12") is None and normalize_phone(None) is None
    assert keyword(" Stop! ") == "stop" and keyword("unstop") == "start" and keyword("help") == "help" and keyword("stop by later") is None


def test_twilio_signature_round_trip():
    from app.integrations.providers.twilio_sms import signature_for, valid_signature

    url = "https://crm.example.com/api/aiva/public/integrations/twilio_sms/webhook/abc?event=status"
    params = [("MessageSid", "SM1"), ("AccountSid", ACCOUNT_SID), ("MessageStatus", "delivered")]
    signature = signature_for(AUTH_TOKEN, url, params)
    assert valid_signature(AUTH_TOKEN, url, list(reversed(params)), signature)
    assert valid_signature(AUTH_TOKEN, url.replace("crm.example.com", "crm.example.com:443"), params, signature)
    assert not valid_signature(AUTH_TOKEN, url, params + [("Body", "x")], signature)
    assert not valid_signature("other-token", url, params, signature)
    assert not valid_signature(AUTH_TOKEN, url, params, None)


# ---------------------------------------------------------------------------
# Twilio SMS
# ---------------------------------------------------------------------------


def connect_twilio(client, ws, fake_http) -> dict:
    h = ws["headers"]
    fake_http.on("GET", rf"/Accounts/{ACCOUNT_SID}\.json$", {"status": "active", "friendly_name": "Acme Support", "type": "Full"})
    fake_http.on(
        "GET", r"/IncomingPhoneNumbers\.json$",
        {"incoming_phone_numbers": [{"sid": NUMBER_SID, "phone_number": "+12145550100", "friendly_name": "Main", "capabilities": {"sms": True}, "sms_url": ""}]},
    )
    assert client.put(f"{API}/integration-hub/twilio_sms/toggle", json={"enabled": True}, headers=h).status_code == 200
    saved = client.put(
        f"{API}/integration-hub/twilio_sms/credentials",
        json={"values": {"account_sid": ACCOUNT_SID, "auth_token": AUTH_TOKEN, "from_number": "(214) 555-0100"}},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    detail = saved.json()
    assert detail["status"] == "connected"
    assert detail["connection"]["config"]["from_number"] == "+12145550100"
    assert AUTH_TOKEN not in json.dumps(detail)
    return detail


def test_twilio_bad_credentials_are_refused(client, fake_http):
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/twilio_sms/toggle", json={"enabled": True}, headers=h)
    bad_sid = client.put(f"{API}/integration-hub/twilio_sms/credentials", json={"values": {"account_sid": "XX1", "auth_token": "t", "from_number": "+12145550100"}}, headers=h)
    assert bad_sid.status_code == 422
    fake_http.on("GET", rf"/Accounts/{ACCOUNT_SID}\.json$", {"code": 20003, "message": "Authenticate", "status": 401}, status=401)
    refused = client.put(f"{API}/integration-hub/twilio_sms/credentials", json={"values": {"account_sid": ACCOUNT_SID, "auth_token": "wrong", "from_number": "+12145550100"}}, headers=h)
    assert refused.status_code == 400 and "auth token" in refused.json()["detail"]


def test_twilio_send_status_and_inbound(client, fake_http):
    ws = signup(client)
    h = ws["headers"]
    detail = connect_twilio(client, ws, fake_http)
    webhook = detail["connection"]["webhook_url"]
    assert webhook and detail["extra"]["status_callback_url"].endswith("?event=status")
    assert detail["extra"]["numbers"][0]["sid"] == NUMBER_SID and not detail["extra"]["numbers"][0]["points_here"]

    contact = client.post(f"{API}/contacts", json={"first_name": "Rosa", "last_name": "Diaz", "mobile": "(214) 555-0199"}, headers=h)
    assert contact.status_code in (200, 201), contact.text
    contact_id = contact.json()["id"]

    fake_http.on("POST", r"/Messages\.json$", {"sid": "SM100", "status": "queued", "from": "+12145550100"}, status=201)
    sent = client.post(f"{API}/messaging/send", json={"channel": "sms", "to": "214-555-0199", "body": "Hi Rosa, your order shipped."}, headers=h)
    assert sent.status_code == 200, sent.text
    message = sent.json()["message"]
    assert message["status"] == "queued" and message["to_number"] == "+12145550199" and message["contact_id"] == contact_id
    _, _, kwargs = fake_http.last("POST", r"/Messages\.json$")
    assert kwargs["data"]["From"] == "+12145550100" and kwargs["data"]["StatusCallback"].endswith("?event=status")
    assert kwargs["auth"] == (ACCOUNT_SID, AUTH_TOKEN)

    # Delivery receipt, signed by Twilio.
    status_url = f"{webhook}?event=status"
    params = {"MessageSid": "SM100", "MessageStatus": "delivered", "AccountSid": ACCOUNT_SID, "To": "+12145550199", "From": "+12145550100"}
    forged = client.post(local_path(status_url), data=params, headers={"X-Twilio-Signature": "bad"})
    assert forged.status_code == 403
    ok = client.post(local_path(status_url), data=params, headers={"X-Twilio-Signature": twilio_sign(status_url, params)})
    assert ok.status_code == 204
    # A late "sent" doesn't move it backwards.
    late = {**params, "MessageStatus": "sent"}
    client.post(local_path(status_url), data=late, headers={"X-Twilio-Signature": twilio_sign(status_url, late)})

    # A reply comes in (and Twilio retries it: kept once).
    inbound = {"MessageSid": "SM200", "AccountSid": ACCOUNT_SID, "From": "+12145550199", "To": "+12145550100", "Body": "Thanks!", "NumMedia": "0"}
    for _ in range(2):
        reply = client.post(local_path(webhook), data=inbound, headers={"X-Twilio-Signature": twilio_sign(webhook, inbound)})
        assert reply.status_code == 200 and "<Response></Response>" in reply.text

    thread = client.get(f"{API}/messaging/thread", params={"number": "+12145550199"}, headers=h).json()
    statuses = [(m["direction"], m["status"], m["body"]) for m in thread["messages"]]
    assert statuses == [("outbound", "delivered", "Hi Rosa, your order shipped."), ("inbound", "received", "Thanks!")]
    assert thread["record"]["type"] == "contact" and thread["record"]["id"] == contact_id

    # The record panel sees the same conversation; the contact's timeline has both.
    on_record = client.get(f"{API}/messaging/records/contact/{contact_id}", headers=h).json()
    assert [n["number"] for n in on_record["numbers"]] == ["+12145550199"]
    assert len(on_record["messages"]) == 2

    listing = client.get(f"{API}/messaging/conversations", headers=h).json()
    assert listing["items"][0]["number"] == "+12145550199" and listing["items"][0]["unread"] == 0  # opening the thread read it


def test_twilio_stop_and_start(client, fake_http):
    ws = signup(client)
    h = ws["headers"]
    detail = connect_twilio(client, ws, fake_http)
    webhook = detail["connection"]["webhook_url"]
    fake_http.on("POST", r"/Messages\.json$", {"sid": "SM300", "status": "queued", "from": "+12145550100"}, status=201)

    stop = {"MessageSid": "SM301", "AccountSid": ACCOUNT_SID, "From": "+13125550111", "To": "+12145550100", "Body": "STOP"}
    assert client.post(local_path(webhook), data=stop, headers={"X-Twilio-Signature": twilio_sign(webhook, stop)}).status_code == 200
    blocked = client.post(f"{API}/messaging/send", json={"channel": "sms", "to": "+13125550111", "body": "Hello?"}, headers=h)
    assert blocked.status_code == 409 and "STOP" in blocked.json()["detail"]
    suppression = client.get(f"{API}/suppression", params={"search": "3125550111"}, headers=h).json()
    assert any(item["kind"] == "phone" and item["reason"] == "unsubscribed" for item in suppression["items"])

    start = {"MessageSid": "SM302", "AccountSid": ACCOUNT_SID, "From": "+13125550111", "To": "+12145550100", "Body": "start"}
    client.post(local_path(webhook), data=start, headers={"X-Twilio-Signature": twilio_sign(webhook, start)})
    again = client.post(f"{API}/messaging/send", json={"channel": "sms", "to": "+13125550111", "body": "Welcome back"}, headers=h)
    assert again.status_code == 200 and again.json()["ok"] is True

    # Carrier says the number opted out → saved as failed, and the number is blocked.
    fake_http.on("POST", r"/Messages\.json$", {"code": 21610, "message": "Attempt to send to unsubscribed recipient"}, status=400)
    refused = client.post(f"{API}/messaging/send", json={"channel": "sms", "to": "+13125550111", "body": "One more"}, headers=h).json()
    assert refused["ok"] is False and "STOP" in refused["error"]
    assert client.post(f"{API}/messaging/send", json={"channel": "sms", "to": "+13125550111", "body": "x"}, headers=h).status_code == 409

    # A manual opt-out needs only edit rights; lifting it needs the privacy permission (owners have it).
    manual = client.post(f"{API}/messaging/opt-out", json={"number": "+13125550999", "opted_out": True}, headers=h).json()
    assert manual["opted_out"]["reason"] == "manual"
    lifted = client.post(f"{API}/messaging/opt-out", json={"number": "+13125550999", "opted_out": False}, headers=h).json()
    assert lifted["opted_out"] is None


def test_messaging_needs_switch_on(client, fake_http):
    ws = signup(client)
    h = ws["headers"]
    connect_twilio(client, ws, fake_http)
    client.put(f"{API}/integration-hub/twilio_sms/toggle", json={"enabled": False}, headers=h)
    off = client.post(f"{API}/messaging/send", json={"channel": "sms", "to": "+12145550123", "body": "Hi"}, headers=h)
    assert off.status_code == 409 and "turned off" in off.json()["detail"]
    channels = client.get(f"{API}/messaging/channels", headers=h).json()["channels"]
    assert not any(c["ready"] for c in channels)


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------

PHONE_ID = "1234567890"
WABA_ID = "9876543210"
TEMPLATE = {
    "id": "t1",
    "name": "order_update",
    "language": "en_US",
    "status": "APPROVED",
    "category": "UTILITY",
    "components": [{"type": "BODY", "text": "Hi {{1}}, your order {{2}} has shipped."}, {"type": "FOOTER", "text": "Acme"}],
}


def connect_whatsapp(client, ws, fake_http) -> dict:
    h = ws["headers"]
    fake_http.on("GET", rf"/{PHONE_ID}$", {"display_phone_number": "+1 555-010-0100", "verified_name": "Acme Support", "quality_rating": "GREEN"})
    fake_http.on("GET", rf"/{WABA_ID}$", {"id": WABA_ID, "name": "Acme WABA"})
    fake_http.on("GET", rf"/{WABA_ID}/phone_numbers$", {"data": [{"id": PHONE_ID}]})
    fake_http.on("POST", rf"/{WABA_ID}/subscribed_apps$", {"success": True})
    fake_http.on("GET", rf"/{WABA_ID}/message_templates$", {"data": [TEMPLATE, {**TEMPLATE, "id": "t2", "name": "promo", "status": "PENDING"}], "paging": {}})
    assert client.put(f"{API}/integration-hub/whatsapp/toggle", json={"enabled": True}, headers=h).status_code == 200
    saved = client.put(
        f"{API}/integration-hub/whatsapp/credentials",
        json={"values": {"phone_number_id": PHONE_ID, "business_account_id": WABA_ID, "access_token": "EAAG-secret-token", "app_secret": APP_SECRET}},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    detail = saved.json()
    assert detail["status"] == "connected" and detail["connection"]["account_label"].startswith("Acme Support")
    text = json.dumps(detail)
    assert "EAAG-secret-token" not in text and APP_SECRET not in text
    _, _, kwargs = fake_http.last("GET", rf"/{PHONE_ID}$")
    assert kwargs["params"]["appsecret_proof"] == hmac.new(APP_SECRET.encode(), b"EAAG-secret-token", hashlib.sha256).hexdigest()
    return detail


def test_whatsapp_connect_verify_receive_and_send(client, fake_http):
    ws = signup(client)
    h = ws["headers"]
    detail = connect_whatsapp(client, ws, fake_http)
    extra = detail["extra"]
    assert extra["approved_templates"] == 1 and extra["verify_token"] and extra["app_subscribed"]
    webhook = detail["connection"]["webhook_url"]

    # Meta's verification request.
    good = client.get(local_path(webhook), params={"hub.mode": "subscribe", "hub.verify_token": extra["verify_token"], "hub.challenge": "8675309"})
    assert good.status_code == 200 and good.text == "8675309"
    assert client.get(local_path(webhook), params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}).status_code == 403

    # Free-form text is refused outside the 24-hour window.
    closed = client.post(f"{API}/messaging/send", json={"channel": "whatsapp", "to": "+12145550123", "body": "Hello"}, headers=h)
    assert closed.status_code == 409 and "24 hours" in closed.json()["detail"]

    lead = client.post(f"{API}/leads", json={"first_name": "Wade", "last_name": "Wilson", "phone": "214.555.0123"}, headers=h).json()
    event = {
        "object": "whatsapp_business_account",
        "entry": [{"id": WABA_ID, "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15550100100", "phone_number_id": PHONE_ID},
            "contacts": [{"profile": {"name": "Wade"}, "wa_id": "12145550123"}],
            "messages": [{"from": "12145550123", "id": "wamid.IN1", "timestamp": "1761200000", "type": "text", "text": {"body": "Is my order on the way?"}}],
        }}]}],
    }
    body = json.dumps(event).encode()
    assert client.post(local_path(webhook), content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 401
    accepted = client.post(local_path(webhook), content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": meta_sign(body)})
    assert accepted.status_code == 200
    client.post(local_path(webhook), content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": meta_sign(body)})  # retry: kept once

    # The message's timestamp is old, so the window is closed: a template is the way.
    fake_http.on("POST", rf"/{PHONE_ID}/messages$", {"messaging_product": "whatsapp", "messages": [{"id": "wamid.OUT1"}]})
    missing = client.post(
        f"{API}/messaging/send",
        json={"channel": "whatsapp", "to": "+12145550123", "template": {"name": "order_update", "language": "en_US", "params": {"body": {"1": "Wade"}}}},
        headers=h,
    )
    assert missing.status_code == 422 and "{{2}}" in missing.json()["detail"]
    sent = client.post(
        f"{API}/messaging/send",
        json={"channel": "whatsapp", "to": "+12145550123", "template": {"name": "order_update", "language": "en_US", "params": {"body": {"1": "Wade", "2": "A-1001"}}}},
        headers=h,
    )
    assert sent.status_code == 200, sent.text
    out = sent.json()["message"]
    assert out["status"] == "sent" and out["body"] == "Hi Wade, your order A-1001 has shipped." and out["lead_id"] == lead["id"]
    _, _, kwargs = fake_http.last("POST", rf"/{PHONE_ID}/messages$")
    payload = kwargs["json"]
    assert payload["to"] == "12145550123" and payload["type"] == "template"
    assert payload["template"]["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "Wade"}, {"type": "text", "text": "A-1001"}]}]
    pending = client.post(
        f"{API}/messaging/send", json={"channel": "whatsapp", "to": "+12145550123", "template": {"name": "promo", "language": "en_US"}}, headers=h
    )
    assert pending.status_code == 409 and "pending" in pending.json()["detail"]

    # Receipts: delivered then read; a late "sent" is ignored.
    for state in ("delivered", "read", "sent"):
        receipt = {"object": "whatsapp_business_account", "entry": [{"id": WABA_ID, "changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": PHONE_ID},
            "statuses": [{"id": "wamid.OUT1", "status": state, "timestamp": "1761200100", "recipient_id": "12145550123"}],
        }}]}]}
        raw = json.dumps(receipt).encode()
        assert client.post(local_path(webhook), content=raw, headers={"Content-Type": "application/json", "X-Hub-Signature-256": meta_sign(raw)}).status_code == 200

    thread = client.get(f"{API}/messaging/thread", params={"number": "+12145550123"}, headers=h).json()
    inbound = [m for m in thread["messages"] if m["direction"] == "inbound"]
    sent_out = [m for m in thread["messages"] if m["direction"] == "outbound"]
    assert len(inbound) == 1 and inbound[0]["body"] == "Is my order on the way?"
    assert [m["status"] for m in sent_out] == ["read"]
    assert thread["record"]["type"] == "lead"

    # A fresh message opens the 24-hour window for free-form replies.
    fresh = {**event}
    fresh["entry"] = [{"id": WABA_ID, "changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": PHONE_ID},
        "messages": [{"from": "12145550123", "id": "wamid.IN2", "timestamp": str(int(time.time())), "type": "text", "text": {"body": "Thanks"}}],
    }}]}]
    raw = json.dumps(fresh).encode()
    client.post(local_path(webhook), content=raw, headers={"Content-Type": "application/json", "X-Hub-Signature-256": meta_sign(raw)})
    fake_http.on("POST", rf"/{PHONE_ID}/messages$", {"messages": [{"id": "wamid.OUT2"}]})
    reply = client.post(f"{API}/messaging/send", json={"channel": "whatsapp", "to": "+12145550123", "body": "You're welcome!"}, headers=h)
    assert reply.status_code == 200 and reply.json()["ok"] is True
    assert fake_http.last("POST", rf"/{PHONE_ID}/messages$")[2]["json"]["text"]["body"] == "You're welcome!"

    # Meta error codes become plain-language messages.
    fake_http.on("POST", rf"/{PHONE_ID}/messages$", {"error": {"message": "Re-engagement message", "code": 131047}}, status=400)
    failed = client.post(f"{API}/messaging/send", json={"channel": "whatsapp", "to": "+12145550123", "body": "Still there?"}, headers=h).json()
    assert failed["ok"] is False and "24 hours" in failed["error"]


def test_conversations_link_and_visibility_basics(client, fake_http):
    ws = signup(client)
    h = ws["headers"]
    detail = connect_twilio(client, ws, fake_http)
    webhook = detail["connection"]["webhook_url"]
    for index, (sender, text) in enumerate([("+16465550001", "Hi from an unknown number"), ("+16465550002", "Second person")]):
        params = {"MessageSid": f"SM9{index}", "AccountSid": ACCOUNT_SID, "From": sender, "To": "+12145550100", "Body": text}
        client.post(local_path(webhook), data=params, headers={"X-Twilio-Signature": twilio_sign(webhook, params)})
    listing = client.get(f"{API}/messaging/conversations", headers=h).json()
    assert {i["number"] for i in listing["items"]} == {"+16465550001", "+16465550002"}
    assert listing["unread_total"] == 2 and all(i["record"] is None for i in listing["items"])
    found = client.get(f"{API}/messaging/conversations", params={"search": "second"}, headers=h).json()
    assert [i["number"] for i in found["items"]] == ["+16465550002"]
    unread_only = client.get(f"{API}/messaging/conversations", params={"unread": True}, headers=h).json()
    assert len(unread_only["items"]) == 2

    account = client.post(f"{API}/accounts", json={"name": "Unknown Number Inc"}, headers=h)
    assert account.status_code in (200, 201), account.text
    linked = client.post(f"{API}/messaging/thread/link", json={"number": "+16465550001", "record_type": "account", "record_id": account.json()["id"]}, headers=h)
    assert linked.status_code == 200 and linked.json()["record"]["type"] == "account"
    read = client.post(f"{API}/messaging/thread/read", json={"number": "+16465550002"}, headers=h).json()
    assert read["marked"] == 1 and read["unread_total"] == 1
    # New messages from a linked number stay linked.
    params = {"MessageSid": "SM99", "AccountSid": ACCOUNT_SID, "From": "+16465550001", "To": "+12145550100", "Body": "Following up"}
    client.post(local_path(webhook), data=params, headers={"X-Twilio-Signature": twilio_sign(webhook, params)})
    on_account = client.get(f"{API}/messaging/records/account/{account.json()['id']}", headers=h).json()
    assert len(on_account["messages"]) == 2


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


class FakeSlack:
    """Stands in for ``core.request`` for the Slack Web API."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict[str, Any] = {
            "auth.test": {"ok": True, "url": "https://acme.slack.com/", "team": "Acme HQ", "user": "aiva", "user_id": "UBOT"},
            "chat.postMessage": {"ok": True, "channel": "C0123456", "ts": "1700000000.000100"},
            "chat.getPermalink": {"ok": True, "permalink": "https://acme.slack.com/archives/C0123456/p1700000000000100"},
            "auth.revoke": {"ok": True, "revoked": True},
        }

    async def __call__(self, db, connection, method, url, **kwargs):
        name = url.rsplit("/", 1)[1]
        self.calls.append((name, kwargs))
        if name == "conversations.list":
            cursor = (kwargs.get("params") or {}).get("cursor")
            if not cursor:
                body = {"ok": True, "channels": [{"id": "C0123456", "name": "sales", "is_private": False, "is_member": True, "num_members": 12}],
                        "response_metadata": {"next_cursor": "page2"}}
            else:
                body = {"ok": True, "channels": [{"id": "G0999999", "name": "deal-desk", "is_private": True, "is_member": True}], "response_metadata": {"next_cursor": ""}}
        else:
            answer = self.answers.get(name, {"ok": True})
            body = answer(kwargs) if callable(answer) else answer
        return httpx.Response(200, json=body, request=httpx.Request(method, url))

    def posts(self) -> list[dict]:
        return [kwargs["json"] for name, kwargs in self.calls if name == "chat.postMessage"]


@pytest.fixture()
def fake_slack(monkeypatch):
    from app.integrations import core

    fake = FakeSlack()

    async def token(family, app_creds, data):
        return {"ok": True, "access_token": "xoxb-test-token", "token_type": "bot", "scope": "chat:write,channels:read", "bot_user_id": "UBOT",
                "app_id": "A1", "team": {"id": "T1", "name": "Acme HQ"}}

    monkeypatch.setattr(core, "request", fake)
    monkeypatch.setattr(core, "_token_request", token)
    return fake


def connect_slack(client, ws) -> dict:
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/slack/toggle", json={"enabled": True}, headers=h).status_code == 200
    client.put(f"{API}/integration-hub/apps/slack", json={"client_id": "123.456", "client_secret": "slack-secret"}, headers=h)
    started = client.post(f"{API}/integration-hub/slack/connect", json={}, headers=h)
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "slack-code"}, headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert back.status_code == 302 and "connected=slack" in back.headers["location"]
    detail = client.get(f"{API}/integration-hub/slack", headers=h).json()
    assert detail["status"] == "connected" and detail["extra"]["mode"] == "bot"
    assert "xoxb-test-token" not in json.dumps(detail)
    return detail


def bridge_row(org: str):
    from sqlalchemy import select

    from app.models.editions import Integration

    async def go(db):
        row = (await db.execute(select(Integration).where(Integration.organization_id == uuid.UUID(org), Integration.kind == "slack"))).scalar_one_or_none()
        return None if row is None else {"active": row.is_active, "events": list(row.events or [])}

    return run_service(go)


def deliver(org: str) -> int:
    from app.services import chat

    return run_service(lambda db: chat.deliver_pending(db, organization_id=uuid.UUID(org)))


def outbound(org: str) -> list[dict]:
    from sqlalchemy import select

    from app.models.editions import OutboundMessage

    async def go(db):
        rows = (await db.execute(select(OutboundMessage).where(OutboundMessage.organization_id == uuid.UUID(org)).order_by(OutboundMessage.created_at))).scalars().all()
        return [{"event": r.event, "status": r.status, "error": r.last_error} for r in rows]

    return run_service(go)


def test_slack_bot_channels_routing_delivery_and_share(client, fake_slack):
    ws = signup(client)
    h = ws["headers"]
    connect_slack(client, ws)
    assert bridge_row(ws["org"]) == {"active": True, "events": ["lead_created", "deal_won", "case_urgent"]}

    channels = client.get(f"{API}/messaging/slack/channels", headers=h).json()
    assert [c["name"] for c in channels["items"]] == ["deal-desk", "sales"]

    saved = client.put(
        f"{API}/integration-hub/slack/settings",
        json={"config": {"default_channel_id": "C0123456", "default_channel_name": "sales", "routes": {"lead_created": "G0999999"}, "events": ["lead_created", "deal_won"],
                         "channel_names": {"C0123456": "sales", "G0999999": "deal-desk"}}},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["extra"]["events"] == ["lead_created", "deal_won"]
    assert bridge_row(ws["org"])["events"] == ["lead_created", "deal_won"]
    assert client.put(f"{API}/integration-hub/slack/settings", json={"config": {"default_channel_id": "#sales"}}, headers=h).status_code == 422

    lead = client.post(f"{API}/leads", json={"first_name": "Slack", "last_name": "Alert"}, headers=h).json()
    assert deliver(ws["org"]) >= 1
    assert outbound(ws["org"])[-1]["status"] == "sent"
    post = fake_slack.posts()[-1]
    assert post["channel"] == "G0999999" and "New lead" in post["text"]
    assert any(block["type"] == "actions" for block in post["blocks"])

    shared = client.post(f"{API}/messaging/slack/share", json={"record_type": "lead", "record_id": lead["id"], "note": "Hot one — call today"}, headers=h)
    assert shared.status_code == 200, shared.text
    assert shared.json()["permalink"].startswith("https://acme.slack.com/") and shared.json()["channel_name"] == "sales"
    share_post = fake_slack.posts()[-1]
    assert share_post["channel"] == "C0123456" and "Hot one" in share_post["blocks"][0]["text"]["text"]

    tested = client.post(f"{API}/integration-hub/slack/test", headers=h)
    assert tested.status_code == 200 and "#sales" in tested.json()["message"]

    # Switching Slack off stops alerts; queued ones fail with the reason.
    client.put(f"{API}/integration-hub/slack/toggle", json={"enabled": False}, headers=h)
    assert bridge_row(ws["org"])["active"] is False
    blocked = client.post(f"{API}/messaging/slack/share", json={"record_type": "lead", "record_id": lead["id"]}, headers=h)
    assert blocked.status_code == 409


def test_slack_revoked_token_needs_reconnect(client, fake_slack):
    ws = signup(client)
    h = ws["headers"]
    connect_slack(client, ws)
    client.put(f"{API}/integration-hub/slack/settings", json={"config": {"default_channel_id": "C0123456", "default_channel_name": "sales"}}, headers=h)
    fake_slack.answers["chat.postMessage"] = {"ok": False, "error": "token_revoked"}
    lead = client.post(f"{API}/leads", json={"first_name": "Revoked", "last_name": "Token"}, headers=h).json()
    deliver(ws["org"])
    last = outbound(ws["org"])[-1]
    assert last["status"] == "failed" and "Reconnect" in (last["error"] or "")
    assert client.get(f"{API}/integration-hub/slack", headers=h).json()["status"] == "needs_reauth"
    again = client.post(f"{API}/messaging/slack/share", json={"record_type": "lead", "record_id": lead["id"]}, headers=h)
    assert again.status_code == 409
    gone = client.delete(f"{API}/integration-hub/slack/connection", headers=h)
    assert gone.status_code == 200 and bridge_row(ws["org"])["active"] is False


def test_slack_incoming_webhook_fallback(client, monkeypatch):
    from app.services import chat

    posted: list[tuple[str, str, str]] = []

    async def fake_post(channel, url, title, text, link=None):
        posted.append((url, title, text))

    monkeypatch.setattr(chat, "post", fake_post)
    ws = signup(client)
    h = ws["headers"]
    client.put(f"{API}/integration-hub/slack/toggle", json={"enabled": True}, headers=h)
    bad = client.put(f"{API}/integration-hub/slack/credentials", json={"values": {"webhook_url": "https://example.com/hook"}}, headers=h)
    assert bad.status_code == 422
    saved = client.put(f"{API}/integration-hub/slack/credentials", json={"values": {"webhook_url": "https://hooks.slack.com/services/T1/B1/XYZ"}}, headers=h)
    assert saved.status_code == 200 and saved.json()["extra"]["mode"] == "webhook"
    assert "hooks.slack.com/services/T1" not in json.dumps(saved.json())
    assert bridge_row(ws["org"])["active"] is True
    client.post(f"{API}/leads", json={"first_name": "Webhook", "last_name": "Lead"}, headers=h)
    deliver(ws["org"])
    assert posted and posted[-1][0] == "https://hooks.slack.com/services/T1/B1/XYZ" and posted[-1][1] == "New lead"


def test_slack_hub_webhook_replaces_an_older_saved_webhook(client, monkeypatch):
    from app.services import chat

    posted: list[str] = []

    async def fake_post(channel, url, title, text, link=None):
        posted.append(url)

    monkeypatch.setattr(chat, "post", fake_post)
    ws = signup(client)
    h = ws["headers"]
    # A webhook saved the old way, before the integrations hub.
    old = client.put(f"{API}/integrations/chat/slack", json={"webhook_url": "https://hooks.slack.com/services/T0/B0/OLD", "events": ["lead_created"], "is_active": True}, headers=h)
    assert old.status_code == 200, old.text
    client.put(f"{API}/integration-hub/slack/toggle", json={"enabled": True}, headers=h)
    assert client.get(f"{API}/integration-hub/slack", headers=h).json()["extra"]["legacy_webhook"] is True

    saved = client.put(f"{API}/integration-hub/slack/credentials", json={"values": {"webhook_url": "https://hooks.slack.com/services/T1/B1/NEW"}}, headers=h)
    assert saved.status_code == 200 and saved.json()["extra"]["legacy_webhook"] is False
    client.post(f"{API}/leads", json={"first_name": "Hub", "last_name": "Webhook"}, headers=h)
    deliver(ws["org"])
    assert posted and posted[-1] == "https://hooks.slack.com/services/T1/B1/NEW"

    # Disconnecting in the hub stops Slack alerts altogether.
    assert client.delete(f"{API}/integration-hub/slack/connection", headers=h).status_code == 200
    assert bridge_row(ws["org"])["active"] is False


# ---------------------------------------------------------------------------
# Scheduled work
# ---------------------------------------------------------------------------


def test_scheduled_status_catch_up_and_template_refresh(client, fake_http):
    from sqlalchemy import text

    from app.integrations.providers import twilio_sms, whatsapp

    ws = signup(client)
    h = ws["headers"]
    connect_twilio(client, ws, fake_http)
    fake_http.on("POST", r"/Messages\.json$", {"sid": "SMCATCHUP1", "status": "queued", "from": "+12145550100"}, status=201)
    sent = client.post(f"{API}/messaging/send", json={"channel": "sms", "to": "+12145550177", "body": "Callback never came"}, headers=h).json()
    message_id = sent["message"]["id"]

    async def age(db):
        await db.execute(text("UPDATE channel_messages SET updated_at = now() - interval '47 hours' WHERE id = :id"), {"id": message_id})

    run_service(age)
    fake_http.on("GET", r"/Messages/SM[A-Za-z0-9]+\.json$", lambda method, url, kwargs: {"sid": url.rsplit("/", 1)[1][:-5], "status": "delivered"})
    assert run_service(twilio_sms.run_due) >= 1
    thread = client.get(f"{API}/messaging/thread", params={"number": "+12145550177"}, headers=h).json()
    assert thread["messages"][-1]["status"] == "delivered"

    connect_whatsapp(client, ws, fake_http)

    async def stale(db):
        await db.execute(
            text("UPDATE integration_connections SET config = jsonb_set(config, '{templates_synced_at}', '\"2020-01-01T00:00:00+00:00\"') WHERE organization_id = :org AND provider = 'whatsapp'"),
            {"org": ws["org"]},
        )

    run_service(stale)
    renamed = {**TEMPLATE, "name": "order_shipped"}
    fake_http.on("GET", rf"/{WABA_ID}/message_templates$", {"data": [renamed], "paging": {}})
    assert run_service(whatsapp.run_due) >= 1
    names = [t["name"] for t in client.get(f"{API}/integration-hub/whatsapp", headers=h).json()["extra"]["templates"]]
    assert names == ["order_shipped"]


def test_attachments_are_proxied_safely(client, fake_http):
    ws = signup(client)
    h = ws["headers"]
    detail = connect_twilio(client, ws, fake_http)
    webhook = detail["connection"]["webhook_url"]
    base = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages/MM1/Media"
    params = {
        "MessageSid": "MM1", "AccountSid": ACCOUNT_SID, "From": "+12145550188", "To": "+12145550100", "Body": "", "NumMedia": "2",
        "MediaUrl0": f"{base}/ME1", "MediaContentType0": "image/jpeg", "MediaUrl1": f"{base}/ME2", "MediaContentType1": "image/svg+xml",
    }
    client.post(local_path(webhook), data=params, headers={"X-Twilio-Signature": twilio_sign(webhook, params)})
    fake_http.on("GET", r"/Media/ME1$", httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}))
    fake_http.on("GET", r"/Media/ME2$", httpx.Response(200, content=b"<svg onload=alert(1)>", headers={"content-type": "image/svg+xml"}))
    thread = client.get(f"{API}/messaging/thread", params={"number": "+12145550188"}, headers=h).json()
    message = thread["messages"][0]
    assert [m["mime_type"] for m in message["media"]] == ["image/jpeg", "image/svg+xml"]
    assert "url" not in message["media"][0]
    picture = client.get(f"{API}/messaging/messages/{message['id']}/media/0", headers=h)
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg" and picture.headers["content-disposition"].startswith("inline")
    _, _, kwargs = fake_http.last("GET", r"/Media/ME1$")
    assert kwargs["auth"] == (ACCOUNT_SID, AUTH_TOKEN)
    svg = client.get(f"{API}/messaging/messages/{message['id']}/media/1", headers=h)
    assert svg.headers["content-type"] == "application/octet-stream" and svg.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in svg.headers["content-security-policy"]
    assert client.get(f"{API}/messaging/messages/{message['id']}/media/5", headers=h).status_code == 404
    other = signup(client)
    assert client.get(f"{API}/messaging/messages/{message['id']}/media/0", headers=other["headers"]).status_code == 404
