"""Mail, calendar and meeting integrations: Gmail / Outlook Mail sending and inbox
sync, Google / Outlook Calendar two-way sync, Google Meet, Zoom and the Teams
channel. Provider APIs are never called: ``core.request`` (and the OAuth token
exchange and identity lookup) are replaced by fakes that record what AIVA sent.
"""

from __future__ import annotations

import asyncio
import base64
import email
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "MailCal#2026"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Mail Cal Co") -> dict:
    address = f"owner-{uuid.uuid4().hex[:10]}@mailcal.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Maya Owner", "email": address, "password": PASSWORD, "company_name": company})
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    if response.status_code >= 500:
        pytest.skip("PostgreSQL not available for integration tests.")
    assert response.status_code == 201, response.text
    data = response.json()
    return {
        "email": address,
        "org": data["organization_id"],
        "user_id": data["user_id"],
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"]},
    }


class FakeProvider:
    """Stands in for ``core.request``: routes (method, url regex) to handlers and records calls."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern[str], Callable[..., Any]]] = []
        self.calls: list[dict[str, Any]] = []

    def on(self, method: str, pattern: str, handler: Callable[..., Any] | dict | int) -> None:
        self.routes.insert(0, (method, re.compile(pattern), handler))

    def find(self, method: str, fragment: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and fragment in c["url"]]

    async def __call__(self, db, connection, method, url, *, token=None, retry_auth=True, timeout=25, **kwargs):
        from app.integrations.core import IntegrationError

        call = {"method": method, "url": url, "provider": connection.provider, **kwargs}
        self.calls.append(call)
        for route_method, pattern, handler in self.routes:
            if route_method == method and pattern.search(url):
                result = handler(call) if callable(handler) else handler
                if isinstance(result, IntegrationError):
                    raise result
                if isinstance(result, int):
                    return httpx.Response(result)
                return httpx.Response(200, json=result)
        raise AssertionError(f"Unexpected provider call {method} {url}")


@pytest.fixture
def fake(monkeypatch) -> FakeProvider:
    from app.integrations import core

    provider = FakeProvider()
    monkeypatch.setattr(core, "request", provider)
    return provider


def connect(client, ws: dict, key: str, family: str, label: str, monkeypatch, *, toggles: tuple[str, ...] = ()) -> None:
    """Switch an OAuth integration on and connect it with a mocked sign-in."""

    from app.integrations import core, identity

    h = ws["headers"]
    for name in (*toggles, key):
        assert client.put(f"{API}/integration-hub/{name}/toggle", json={"enabled": True}, headers=h).status_code == 200
    client.put(f"{API}/integration-hub/apps/{family}", json={"client_id": f"{family}-client", "client_secret": f"{family}-secret"}, headers=h)
    started = client.post(f"{API}/integration-hub/{key}/connect", json={}, headers=h)
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]

    async def fake_token(family_def, app_creds, data):
        return {"access_token": f"{key}-access", "refresh_token": f"{key}-refresh", "expires_in": 3600}

    async def fake_identify(family_key, integration_key, tokens, app_creds):
        return {"label": label, "external_id": f"{key}-user", "config": {"name": "Maya Owner"}}

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "code"}, follow_redirects=False)
    assert back.status_code == 302 and f"connected={key}" in back.headers["location"], back.headers.get("location")


def raw_mail(sender: str, to: str, subject: str, body: str, message_id: str) -> str:
    text = f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\nMessage-ID: <{message_id}>\r\nDate: Fri, 23 Oct 2026 10:00:00 +0000\r\n\r\n{body}\r\n"
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def add_contact(client, ws: dict, address: str) -> str:
    response = client.post(f"{API}/contacts", json={"first_name": "Cora", "last_name": "Buyer", "email": address}, headers=ws["headers"])
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


# ---------------------------------------------------------------------------
# Gmail / Outlook Mail
# ---------------------------------------------------------------------------


def test_gmail_mailbox_sends_through_the_api_and_respects_the_switch(client, fake, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, "gmail", "google", "maya@gmail.example", monkeypatch)
    detail = client.get(f"{API}/integration-hub/gmail", headers=h).json()
    assert detail["status"] == "connected"
    assert detail["extra"]["mailbox"]["email"] == "maya@gmail.example"
    assert detail["extra"]["mailbox"]["sync_enabled"] is True
    assert "gmail-access" not in str(detail)

    sent: dict[str, Any] = {}

    def capture(call):
        sent.update(call["json"])
        return {"id": "sent-1", "threadId": "t-1"}

    fake.on("POST", r"gmail/v1/users/me/messages/send$", capture)
    response = client.post(f"{API}/integration-hub/mail-calendar/gmail/send-test", headers=h)
    assert response.status_code == 200, response.text
    raw = base64.urlsafe_b64decode(sent["raw"] + "=" * (-len(sent["raw"]) % 4))
    message = email.message_from_bytes(raw)
    assert message["To"].endswith("<maya@gmail.example>") or message["To"] == "maya@gmail.example"
    assert "maya@gmail.example" in message["From"] and message["Subject"].startswith("AIVA is sending")

    # The old mailbox page sees the API mailbox too.
    overview = client.get(f"{API}/integrations", headers=h).json()
    assert overview["mailbox"]["email_address"] == "maya@gmail.example"

    # Switched off: nothing goes through Gmail.
    client.put(f"{API}/integration-hub/gmail/toggle", json={"enabled": False}, headers=h)
    before = len(fake.calls)
    refused = client.post(f"{API}/integration-hub/mail-calendar/gmail/send-test", headers=h)
    assert refused.status_code == 409
    assert len(fake.calls) == before

    # Personal settings: sender name and inbox reading.
    client.put(f"{API}/integration-hub/gmail/toggle", json={"enabled": True}, headers=h)
    saved = client.put(f"{API}/integration-hub/gmail/my-settings", json={"config": {"display_name": "Maya from AIVA", "sync_inbox": False}}, headers=h)
    assert saved.status_code == 200, saved.text
    assert saved.json()["extra"]["mailbox"]["display_name"] == "Maya from AIVA"
    assert saved.json()["extra"]["mailbox"]["sync_enabled"] is False

    # Disconnecting removes the API mailbox.
    gone = client.delete(f"{API}/integration-hub/gmail/connection", headers=h)
    assert gone.status_code == 200
    assert client.get(f"{API}/integrations", headers=h).json()["mailbox"] is None


def test_gmail_inbox_sync_adds_customer_mail_once(client, fake, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    add_contact(client, ws, "buyer@acme.example")
    connect(client, ws, "gmail", "google", "rep@gmail.example", monkeypatch)

    fake.on("GET", r"/messages$", {"messages": [{"id": "m1"}, {"id": "m2"}]})
    fake.on("GET", r"/profile$", {"historyId": "100", "emailAddress": "rep@gmail.example"})
    fake.on("GET", r"/messages/m1$", {"raw": raw_mail("Cora Buyer <buyer@acme.example>", "rep@gmail.example", "Pricing question", "Can you send pricing?", "m1@acme.example")})
    fake.on("GET", r"/messages/m2$", {"raw": raw_mail("news@shop.example", "rep@gmail.example", "Sale!", "50% off", "m2@shop.example")})
    first = client.post(f"{API}/integration-hub/mail-calendar/gmail/sync", headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["imported"] == 1 and first.json()["result"]["checked"] == 2
    assert fake.find("GET", "/messages")[0]["params"]["q"].startswith("in:inbox")

    # Next time only what changed since the history ID, and nothing twice.
    fake.on("GET", r"/history$", {"history": [{"messagesAdded": [{"message": {"id": "m3", "labelIds": ["INBOX", "UNREAD"]}}, {"message": {"id": "m1", "labelIds": ["INBOX"]}}]}], "historyId": "105"})
    fake.on("GET", r"/messages/m3$", {"raw": raw_mail("buyer@acme.example", "rep@gmail.example", "Re: Pricing question", "Thanks!", "m3@acme.example")})
    second = client.post(f"{API}/integration-hub/mail-calendar/gmail/sync", headers=h).json()
    assert second["result"]["imported"] == 1 and second["result"]["skipped"] == 1
    assert fake.find("GET", "/history")[-1]["params"]["startHistoryId"] == "100"

    # An expired history ID starts over from recent mail instead of failing.
    from app.integrations.core import IntegrationError

    fake.on("GET", r"/history$", lambda call: IntegrationError("Gmail said: Requested entity was not found.", 400))
    third = client.post(f"{API}/integration-hub/mail-calendar/gmail/sync", headers=h)
    assert third.status_code == 200 and third.json()["result"]["imported"] == 0
    detail = client.get(f"{API}/integration-hub/gmail", headers=h).json()
    assert detail["extra"]["stats"]["imported_total"] == 2


def test_outlook_mail_sends_mime_through_graph(client, fake, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, "outlook_mail", "microsoft", "maya@contoso.example", monkeypatch)
    captured: dict[str, Any] = {}

    def capture(call):
        captured.update(call)
        return 202

    fake.on("POST", r"graph\.microsoft\.com/v1\.0/me/sendMail$", capture)
    response = client.post(f"{API}/integration-hub/mail-calendar/outlook_mail/send-test", headers=h)
    assert response.status_code == 200, response.text
    assert captured["headers"]["Content-Type"] == "text/plain"
    mime = email.message_from_bytes(base64.b64decode(captured["content"]))
    assert "maya@contoso.example" in mime["From"]

    fake.on(
        "GET", r"mailFolders/inbox/messages/delta",
        {
            "value": [
                {"id": "a1", "internetMessageId": "<a1@acme.example>", "subject": "Hello", "from": {"emailAddress": {"address": "Buyer@Acme.example", "name": "Cora"}},
                 "toRecipients": [{"emailAddress": {"address": "maya@contoso.example"}}], "receivedDateTime": "2026-10-23T09:00:00Z", "body": {"contentType": "text", "content": "Hi"}},
                {"id": "a2", "@removed": {"reason": "deleted"}},
            ],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=abc",
        },
    )
    add_contact(client, ws, "buyer@acme.example")
    synced = client.post(f"{API}/integration-hub/mail-calendar/outlook_mail/sync", headers=h).json()
    assert synced["result"]["imported"] == 1
    assert "$filter" in fake.find("GET", "messages/delta")[0]["params"]


# ---------------------------------------------------------------------------
# Calendars, Google Meet, Zoom
# ---------------------------------------------------------------------------


def _google_event_echo(join: str | None = None):
    def handler(call):
        body = dict(call.get("json") or {})
        event_id = body.get("id") or call["url"].rsplit("/", 1)[-1]
        event = {
            "id": event_id, "iCalUID": f"{event_id}@google.com", "etag": '"1"', "updated": datetime.now(timezone.utc).isoformat(),
            "htmlLink": f"https://calendar.google.com/event?eid={event_id}", "status": "confirmed",
        }
        if join or (body.get("conferenceData") or {}).get("createRequest"):
            event["hangoutLink"] = join or "https://meet.google.com/abc-defg-hij"
            event["conferenceData"] = {"entryPoints": [{"entryPointType": "video", "uri": event["hangoutLink"]}]}
        return event

    return handler


def test_google_calendar_push_with_meet_link_update_and_cancel(client, fake, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, "google_calendar", "google", "maya@acme-sales.example", monkeypatch)
    assert client.put(f"{API}/integration-hub/google_meet/toggle", json={"enabled": True}, headers=h).status_code == 200

    options = client.get(f"{API}/integration-hub/mail-calendar/meeting-options", headers=h).json()
    by_key = {o["key"]: o for o in options["options"]}
    assert by_key["google_meet"]["available"] is True
    assert by_key["zoom"]["available"] is False and by_key["microsoft_teams"]["available"] is False
    assert options["calendar"]["key"] == "google_calendar" and options["calendar"]["invites_from_calendar"] is True

    fake.on("POST", r"/calendars/primary/events$", _google_event_echo())
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    created = client.post(
        f"{API}/meetings",
        json={
            "title": "Discovery call", "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=30)).isoformat(), "is_online": True,
            "timezone": "America/Chicago", "attendees": [{"type": "email", "email": "guest@northwind.example"}], "conference": "google_meet",
        },
        headers=h,
    )
    assert created.status_code == 201, created.text
    meeting = created.json()
    assert meeting["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert meeting["conference"]["type"] == "google_meet"
    assert meeting["calendar_sync"]["state"] == "synced" and meeting["calendar_sync"]["invites"] is True
    assert meeting["invites_sent"] == 0 and meeting["integration_warnings"] == []
    insert = fake.find("POST", "/calendars/primary/events")[0]
    assert insert["params"] == {"sendUpdates": "all", "conferenceDataVersion": 1}
    assert insert["json"]["id"] == f"aiva{uuid.UUID(meeting['id']).hex}"
    assert insert["json"]["attendees"] == [{"email": "guest@northwind.example", "displayName": "guest@northwind.example"}]
    assert insert["json"]["start"]["timeZone"] == "America/Chicago"

    fake.on("PATCH", r"/calendars/primary/events/aiva", _google_event_echo("https://meet.google.com/abc-defg-hij"))
    moved = client.patch(f"{API}/meetings/{meeting['id']}", json={"start_at": (start + timedelta(hours=1)).isoformat(), "end_at": (start + timedelta(hours=2)).isoformat()}, headers=h)
    assert moved.status_code == 200, moved.text
    patch = fake.find("PATCH", "/events/aiva")[-1]
    assert patch["params"]["sendUpdates"] == "all" and patch["json"]["start"]["dateTime"].startswith((start + timedelta(hours=1)).strftime("%Y-%m-%dT%H"))

    fake.on("DELETE", r"/calendars/primary/events/aiva", 204)
    cancelled = client.post(f"{API}/meetings/{meeting['id']}/cancel", json={"reason": "Moved to next week", "notify": True}, headers=h)
    assert cancelled.status_code == 200, cancelled.text
    assert fake.find("DELETE", "/events/aiva")[-1]["params"] == {"sendUpdates": "all"}


def test_calendar_push_failure_is_retried(client, fake, monkeypatch):
    from app.integrations.core import IntegrationError

    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, "outlook_calendar", "microsoft", "maya@contoso.example", monkeypatch)
    fake.on("POST", r"graph\.microsoft\.com/v1\.0/me/events$", lambda call: IntegrationError("Couldn't reach Microsoft Outlook Calendar (ConnectTimeout).", 502))
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
    created = client.post(
        f"{API}/meetings",
        json={"title": "Quarterly review", "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(), "attendees": [{"type": "email", "email": "cfo@fabrikam.example"}]},
        headers=h,
    ).json()
    assert created["calendar_sync"]["state"] == "pending"
    assert created["integration_warnings"] and "keep trying" in created["integration_warnings"][0]

    def ok(call):
        assert "attendees" not in call["json"]  # AIVA already emailed the invitation
        assert call["json"]["transactionId"] == created["id"]
        return {"id": "AAMk-1", "iCalUId": "040000008200E0", "changeKey": "ck1", "lastModifiedDateTime": "2026-10-23T10:00:00Z", "webLink": "https://outlook.office.com/x"}

    fake.on("POST", r"graph\.microsoft\.com/v1\.0/me/events$", ok)
    retried = client.post(f"{API}/integration-hub/mail-calendar/meetings/{created['id']}/retry", headers=h)
    assert retried.status_code == 200, retried.text
    assert retried.json()["calendar_sync"]["state"] == "synced"
    detail = client.get(f"{API}/integration-hub/outlook_calendar", headers=h).json()
    assert detail["extra"]["problems"] == []


def test_google_calendar_pull_creates_updates_and_cancels(client, fake, monkeypatch):
    from app.integrations.core import IntegrationError

    ws = signup(client)
    h = ws["headers"]
    contact_id = add_contact(client, ws, "cto@northwind.example")
    connect(client, ws, "google_calendar", "google", "maya@acme-sales.example", monkeypatch)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)

    def event(event_id: str, attendees: list[str], **extra) -> dict:
        return {
            "id": event_id, "iCalUID": f"{event_id}@google.com", "status": "confirmed", "summary": f"Call {event_id}",
            "start": {"dateTime": tomorrow.isoformat()}, "end": {"dateTime": (tomorrow + timedelta(minutes=45)).isoformat()},
            "organizer": {"email": "maya@acme-sales.example", "self": True},
            "attendees": [{"email": "maya@acme-sales.example", "self": True, "responseStatus": "accepted"}] + [{"email": a, "responseStatus": "needsAction"} for a in attendees],
            "updated": "2026-10-23T08:00:00Z", **extra,
        }

    fake.on("GET", r"/calendars/primary/events$", {
        "timeZone": "America/Chicago",
        "items": [
            event("evt1", ["cto@northwind.example"], hangoutLink="https://meet.google.com/xyz"),
            event("evt2", ["teammate@acme-sales.example"]),  # internal only: not imported in "matched" mode
            {**event("evt3", ["cto@northwind.example"]), "eventType": "outOfOffice"},
        ],
        "nextSyncToken": "sync-1",
    })
    first = client.post(f"{API}/integration-hub/mail-calendar/google_calendar/sync", headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["created"] == 1
    assert "timeMin" in fake.find("GET", "/events")[0]["params"]
    listed = client.get(f"{API}/meetings", params={"record_type": "contact", "record_id": contact_id}, headers=h).json()["items"]
    assert len(listed) == 1
    pulled = listed[0]
    assert pulled["provider"] == "google_calendar" and pulled["meeting_url"] == "https://meet.google.com/xyz"
    assert pulled["calendar_sync"]["origin"] == "pulled"
    assert {a["type"] for a in pulled["attendees"]} >= {"contact"}

    later = tomorrow + timedelta(hours=3)
    fake.on("GET", r"/calendars/primary/events$", lambda call: {
        "items": [{**event("evt1", ["cto@northwind.example"]), "start": {"dateTime": later.isoformat()}, "end": {"dateTime": (later + timedelta(minutes=30)).isoformat()},
                   "updated": "2026-10-23T09:00:00Z", "attendees": [{"email": "cto@northwind.example", "responseStatus": "accepted"}]}],
        "nextSyncToken": "sync-2",
    } if call["params"].get("syncToken") == "sync-1" else {"items": [], "nextSyncToken": "x"})
    second = client.post(f"{API}/integration-hub/mail-calendar/google_calendar/sync", headers=h).json()
    assert second["result"]["updated"] == 1
    updated = client.get(f"{API}/meetings/{pulled['id']}", headers=h).json()
    assert updated["start_at"].startswith(later.strftime("%Y-%m-%dT%H"))
    assert next(a for a in updated["attendees"] if a["email"] == "cto@northwind.example")["response"] == "accepted"

    # An expired sync token (410) means a full sync, not an error.
    fake.on("GET", r"/calendars/primary/events$", lambda call: IntegrationError("Google Calendar said: Sync token is no longer valid.", 400) if call["params"].get("syncToken") else {
        "items": [{"id": "evt1", "status": "cancelled"}], "nextSyncToken": "sync-3",
    })
    third = client.post(f"{API}/integration-hub/mail-calendar/google_calendar/sync", headers=h)
    assert third.status_code == 200, third.text
    assert third.json()["result"]["cancelled"] == 1
    assert client.get(f"{API}/meetings/{pulled['id']}", headers=h).json()["status"] == "cancelled"

    # Settings: which events to bring in, validated.
    bad = client.put(f"{API}/integration-hub/google_calendar/my-settings", json={"config": {"import_mode": "everything"}}, headers=h)
    assert bad.status_code == 422
    good = client.put(f"{API}/integration-hub/google_calendar/my-settings", json={"config": {"import_mode": "external", "invites_from_calendar": False}}, headers=h)
    assert good.status_code == 200 and good.json()["extra"]["settings"]["import_mode"] == "external"


def test_zoom_meeting_follows_the_aiva_meeting(client, fake, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, "zoom", "zoom", "maya@zoom.example", monkeypatch)
    fake.on("POST", r"api\.zoom\.us/v2/users/me/meetings$", {"id": 98765, "join_url": "https://zoom.us/j/98765?pwd=abc", "start_url": "https://zoom.us/s/secret"})
    fake.on("PATCH", r"api\.zoom\.us/v2/meetings/98765$", 204)
    fake.on("DELETE", r"api\.zoom\.us/v2/meetings/98765$", 204)
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    created = client.post(
        f"{API}/meetings",
        json={"title": "Demo", "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=45)).isoformat(), "conference": "zoom", "timezone": "Europe/London"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    meeting = created.json()
    assert meeting["meeting_url"] == "https://zoom.us/j/98765?pwd=abc" and meeting["conference"]["type"] == "zoom"
    assert "start_url" not in str(meeting) and "zoom.us/s/" not in str(meeting)
    body = fake.find("POST", "users/me/meetings")[0]["json"]
    assert body["duration"] == 45 and body["type"] == 2 and body["settings"]["waiting_room"] is True and body["timezone"] == "Europe/London"

    renamed = client.patch(f"{API}/meetings/{meeting['id']}", json={"title": "Product demo"}, headers=h)
    assert renamed.status_code == 200
    assert fake.find("PATCH", "meetings/98765")[-1]["json"]["topic"] == "Product demo"

    switched = client.patch(f"{API}/meetings/{meeting['id']}", json={"conference": "none"}, headers=h).json()
    assert switched["conference"] is None and switched["meeting_url"] is None
    assert fake.find("DELETE", "meetings/98765")

    # Without Zoom connected the option says why.
    member_view = client.get(f"{API}/integration-hub/mail-calendar/meeting-options", headers=h).json()
    assert next(o for o in member_view["options"] if o["key"] == "zoom")["available"] is True
    client.put(f"{API}/integration-hub/zoom/toggle", json={"enabled": False}, headers=h)
    off = client.get(f"{API}/integration-hub/mail-calendar/meeting-options", headers=h).json()
    assert "turned Zoom on" in next(o for o in off["options"] if o["key"] == "zoom")["reason"]


def test_teams_channel_is_bridged_to_chat_alerts(client, fake, monkeypatch):
    from app.services import chat

    ws = signup(client)
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/microsoft_teams/toggle", json={"enabled": True}, headers=h).status_code == 200
    bad = client.put(f"{API}/integration-hub/microsoft_teams/credentials", json={"values": {"webhook_url": "https://evil.example/hook"}}, headers=h)
    assert bad.status_code == 422
    good = client.put(f"{API}/integration-hub/microsoft_teams/credentials", json={"values": {"webhook_url": "https://contoso.webhook.office.com/webhookb2/abc/IncomingWebhook/def"}}, headers=h)
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "connected" and "webhook.office.com" not in str(good.json()["connection"]["secrets"])
    teams = client.get(f"{API}/integrations", headers=h).json()["teams"]
    assert teams["connected"] is True and teams["is_active"] is True

    events = client.put(f"{API}/integration-hub/microsoft_teams/settings", json={"config": {"events": ["deal_won", "lead_created", "bogus"]}}, headers=h)
    assert events.json()["extra"]["events"] == ["deal_won", "lead_created"]
    assert client.get(f"{API}/integrations", headers=h).json()["teams"]["events"] == ["deal_won", "lead_created"]

    posted: list[tuple] = []

    async def fake_post(channel, url, title, text, link=None):
        posted.append((channel, url, title))

    monkeypatch.setattr(chat, "post", fake_post)
    tested = client.post(f"{API}/integration-hub/microsoft_teams/test", headers=h)
    assert tested.status_code == 200 and posted[0][0] == "teams" and posted[0][1].startswith("https://contoso.webhook.office.com/")

    client.put(f"{API}/integration-hub/microsoft_teams/toggle", json={"enabled": False}, headers=h)
    assert client.get(f"{API}/integrations", headers=h).json()["teams"]["is_active"] is False
    client.put(f"{API}/integration-hub/microsoft_teams/toggle", json={"enabled": True}, headers=h)
    assert client.delete(f"{API}/integration-hub/microsoft_teams/connection", headers=h).status_code == 200
    assert client.get(f"{API}/integrations", headers=h).json()["teams"]["connected"] is False


def test_teams_saved_in_other_connections_is_adopted_by_the_hub(client):
    ws = signup(client)
    h = ws["headers"]
    saved = client.put(f"{API}/integrations/chat/teams", json={"webhook_url": "https://contoso.webhook.office.com/webhookb2/zzz", "events": ["deal_won"], "is_active": True}, headers=h)
    assert saved.status_code == 200, saved.text

    async def run() -> int:
        from app.db.session import AsyncSessionLocal
        from app.integrations.providers import meetings

        async with AsyncSessionLocal() as session:
            return await meetings.run_due(session)

    assert asyncio.run(run()) >= 1
    detail = client.get(f"{API}/integration-hub/microsoft_teams", headers=h).json()
    assert detail["switched_on"] is True and detail["status"] == "connected"
    assert detail["extra"]["events"] == ["deal_won"]


def test_meeting_options_require_your_own_connection(client):
    ws = signup(client)
    h = ws["headers"]
    for key in ("google_calendar", "google_meet", "outlook_calendar", "microsoft_teams"):
        client.put(f"{API}/integration-hub/{key}/toggle", json={"enabled": True}, headers=h)
    options = {o["key"]: o for o in client.get(f"{API}/integration-hub/mail-calendar/meeting-options", headers=h).json()["options"]}
    assert options["google_meet"]["available"] is False and "Connect your Google Calendar" in options["google_meet"]["reason"]
    assert options["microsoft_teams"]["setup_url"] == "/integrations/outlook_calendar"
    assert client.post(f"{API}/integration-hub/mail-calendar/gmail/send-test", headers=h).status_code == 409
    assert client.post(f"{API}/integration-hub/mail-calendar/slack/sync", headers=h).status_code == 404


def test_teams_link_comes_from_outlook_calendar(client, fake, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, "outlook_calendar", "microsoft", "maya@contoso.example", monkeypatch, toggles=("microsoft_teams",))
    options = {o["key"]: o for o in client.get(f"{API}/integration-hub/mail-calendar/meeting-options", headers=h).json()["options"]}
    assert options["microsoft_teams"]["available"] is True and options["microsoft_teams"]["account"] == "maya@contoso.example"

    def created(call):
        body = call["json"]
        assert body["isOnlineMeeting"] is True and body["onlineMeetingProvider"] == "teamsForBusiness"
        assert body["attendees"][0]["emailAddress"]["address"] == "buyer@fabrikam.example"
        assert body["start"]["timeZone"] == "Asia/Kolkata"
        return {"id": "AAMk-teams", "iCalUId": "040000-teams", "changeKey": "c1", "lastModifiedDateTime": "2026-10-23T10:00:00Z",
                "webLink": "https://outlook.office.com/e", "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/abc"}}

    fake.on("POST", r"graph\.microsoft\.com/v1\.0/me/events$", created)
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=4)
    meeting = client.post(
        f"{API}/meetings",
        json={"title": "Kickoff", "start_at": start.isoformat(), "end_at": (start + timedelta(hours=1)).isoformat(), "timezone": "Asia/Kolkata",
              "attendees": [{"type": "email", "email": "buyer@fabrikam.example"}], "conference": "microsoft_teams"},
        headers=h,
    ).json()
    assert meeting["meeting_url"] == "https://teams.microsoft.com/l/meetup-join/abc"
    assert meeting["conference"]["type"] == "microsoft_teams" and meeting["calendar_sync"]["invites"] is True

    fake.on("POST", r"/me/events/AAMk-teams/cancel$", 202)
    client.post(f"{API}/meetings/{meeting['id']}/cancel", json={"reason": "Postponed", "notify": True}, headers=h)
    assert fake.find("POST", "/cancel")[-1]["json"] == {"comment": "Postponed"}


def test_scheduler_jobs_run_and_survive_provider_errors(client, fake):
    """The background jobs' queries run, and a provider outage is recorded rather than crashing them."""

    from app.integrations.core import IntegrationError
    from app.integrations.providers import calendars, mail, meetings

    for method in ("GET", "POST", "PATCH", "DELETE"):
        fake.on(method, r".", lambda call: IntegrationError("Provider is down.", 502))

    async def run() -> list[int]:
        from app.db.session import AsyncSessionLocal

        results = []
        for job in (mail.run_due, calendars.run_due, meetings.run_due):
            async with AsyncSessionLocal() as session:
                results.append(await job(session))
        return results

    results = asyncio.run(run())
    assert all(isinstance(n, int) for n in results)


def test_live_checks_use_only_the_granted_scopes(client, fake, monkeypatch):
    ws = signup(client)
    h = ws["headers"]
    connect(client, ws, "google_calendar", "google", "maya@acme-sales.example", monkeypatch)
    client.put(f"{API}/integration-hub/google_meet/toggle", json={"enabled": True}, headers=h)
    fake.on("GET", r"/calendars/primary/events$", {"summary": "Maya Owner", "timeZone": "America/Chicago", "items": []})
    from app.integrations.core import IntegrationError

    fake.on("GET", r"/calendars/primary$", lambda call: IntegrationError("Google Calendar said: Request had insufficient authentication scopes.", 400))
    tested = client.post(f"{API}/integration-hub/google_calendar/test", headers=h)
    assert tested.status_code == 200 and "Maya Owner" in tested.json()["message"]
    meet = client.post(f"{API}/integration-hub/google_meet/test", headers=h)
    assert meet.status_code == 200 and "Google Meet" in meet.json()["message"]
    # Listing calendars needs a scope AIVA doesn't ask for: the primary calendar is offered instead.
    fake.on("GET", r"/users/me/calendarList$", lambda call: IntegrationError("Google Calendar said: insufficient scopes.", 400))
    listed = client.get(f"{API}/integration-hub/mail-calendar/google_calendar/calendars", headers=h).json()
    assert listed["items"] == [{"id": None, "name": "Primary calendar", "primary": True}]
    assert client.get(f"{API}/integration-hub/google_calendar", headers=h).json()["connection"]["last_error"] is None


def test_unsafe_links_from_a_calendar_are_dropped():
    from app.integrations.providers import calendars

    event = calendars.google_event(
        {"id": "x1", "status": "confirmed", "start": {"dateTime": "2026-11-01T10:00:00Z"}, "end": {"dateTime": "2026-11-01T11:00:00Z"},
         "hangoutLink": "javascript:alert(1)", "htmlLink": "data:text/html,hi"},
        "UTC",
    )
    assert event.join_url is None and event.html_link is None
    outlook = calendars.outlook_event(
        {"id": "o1", "start": {"dateTime": "2026-11-01T10:00:00.0000000", "timeZone": "UTC"}, "end": {"dateTime": "2026-11-01T11:00:00.0000000", "timeZone": "UTC"},
         "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/ok"}, "isOrganizer": True}
    )
    assert outlook.join_url == "https://teams.microsoft.com/l/meetup-join/ok" and outlook.start.hour == 10 and outlook.organizer_self
