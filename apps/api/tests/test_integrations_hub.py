"""Integrations hub foundation: catalog, on/off switches, OAuth connect with PKCE,
the workspace's own OAuth apps, credentials, disconnect and webhook dispatch.
Provider APIs are never called: token exchange and identity are mocked.
"""

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Integration#2026"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Hub Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@hub.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Hana Owner", "email": email, "password": PASSWORD, "company_name": company})
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


@pytest.fixture(scope="module")
def ws(client):
    return signup(client)


def test_catalog_lists_every_integration(client, ws):
    response = client.get(f"{API}/integration-hub/catalog", headers=ws["headers"])
    assert response.status_code == 200, response.text
    items = {i["key"]: i for i in response.json()["items"]}
    assert len(items) == 52
    assert items["gmail"]["category"] == "Email" and items["gmail"]["personal"] is True
    assert all(i["status"] == "off" for i in items.values())


def test_toggle_on_off_and_dependency(client, ws):
    h = ws["headers"]
    refused = client.put(f"{API}/integration-hub/google_meet/toggle", json={"enabled": True}, headers=h)
    assert refused.status_code == 409  # needs Google Calendar first
    on = client.put(f"{API}/integration-hub/google_calendar/toggle", json={"enabled": True}, headers=h)
    assert on.status_code == 200 and on.json()["enabled"] is True and on.json()["status"] == "not_connected"
    assert client.put(f"{API}/integration-hub/google_meet/toggle", json={"enabled": True}, headers=h).json()["enabled"] is True
    off = client.put(f"{API}/integration-hub/google_calendar/toggle", json={"enabled": False}, headers=h)
    assert off.json()["status"] == "off"
    assert client.get(f"{API}/integration-hub/google_meet", headers=h).json()["enabled"] is False
    logs = client.get(f"{API}/integration-hub/google_calendar/logs", headers=h).json()["items"]
    assert {l["action"] for l in logs} >= {"switched_on", "switched_off"}


def test_members_cannot_toggle(client, ws):
    email = f"member-{uuid.uuid4().hex[:8]}@hub.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Max Member", "role": "member"}, headers=ws["headers"]).json()
    token = invited["invite_url"].split("token=")[1]
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": token, "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.put(f"{API}/integration-hub/slack/toggle", json={"enabled": True}, headers=member).status_code == 403
    assert client.get(f"{API}/integration-hub/catalog", headers=member).status_code == 200


def test_oauth_connect_with_workspace_app_and_pkce(client, ws, monkeypatch):
    from app.integrations import core, identity

    h = ws["headers"]
    client.put(f"{API}/integration-hub/zoom/toggle", json={"enabled": True}, headers=h)
    missing = client.post(f"{API}/integration-hub/zoom/connect", json={}, headers=h)
    assert missing.status_code == 409 and "client ID" in missing.json()["detail"]
    saved = client.put(f"{API}/integration-hub/apps/zoom", json={"client_id": "zoom-client-123", "client_secret": "zoom-secret"}, headers=h)
    assert saved.status_code == 200 and saved.json()["source"] == "workspace"
    detail = client.get(f"{API}/integration-hub/zoom", headers=h).json()
    assert detail["oauth"]["configured"] and detail["oauth"]["workspace_client_id"] == "zoom-client-123"
    assert "zoom-secret" not in str(detail)

    started = client.post(f"{API}/integration-hub/zoom/connect", json={"return_to": "/integrations/zoom"}, headers=h)
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["authorize_url"])
    query = parse_qs(url.query)
    assert url.netloc == "zoom.us" and query["client_id"] == ["zoom-client-123"]
    assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"][0].endswith("/api/aiva/public/integrations/oauth/callback")
    state = query["state"][0]

    seen = {}

    async def fake_token(family, app_creds, data):
        seen.update(data)
        return {"access_token": "zoom-access", "refresh_token": "zoom-refresh", "expires_in": 3600, "scope": "meeting:write user:read"}

    async def fake_identify(family, key, tokens, app_creds):
        return {"label": "hana@zoom.example", "external_id": "zoom-user-1"}

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "auth-code"}, headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert back.status_code == 302 and "connected=zoom" in back.headers["location"]
    assert seen["code"] == "auth-code" and len(seen["code_verifier"]) >= 43
    # The state is single use.
    again = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "auth-code"}, headers={"X-Session-ID": h["X-Session-ID"]}, follow_redirects=False)
    assert "error=" in again.headers["location"]

    connected = client.get(f"{API}/integration-hub/zoom", headers=h).json()
    assert connected["status"] == "connected" and connected["connection"]["account_label"] == "hana@zoom.example"
    assert "zoom-access" not in str(connected)
    gone = client.delete(f"{API}/integration-hub/zoom/connection", headers=h)
    assert gone.status_code == 200 and gone.json()["status"] == "not_connected"


def test_sign_in_link_only_works_in_the_session_that_started_it(client, ws, monkeypatch):
    """A sign-in link sent to someone else can't attach their account to the sender's."""
    from app.integrations import core, identity

    h = ws["headers"]
    client.put(f"{API}/integration-hub/zoom/toggle", json={"enabled": True}, headers=h)
    client.put(f"{API}/integration-hub/apps/zoom", json={"client_id": "zoom-client-xyz", "client_secret": "zoom-secret"}, headers=h)

    no_session = {k: v for k, v in h.items() if k != "X-Session-ID"}
    assert client.post(f"{API}/integration-hub/zoom/connect", json={}, headers=no_session).status_code == 400
    forged = {**no_session, "X-Session-ID": str(uuid.uuid4())}
    assert client.post(f"{API}/integration-hub/zoom/connect", json={}, headers=forged).status_code == 400

    exchanged: list[str] = []

    async def fake_token(family, app_creds, data):
        exchanged.append(data["code"])
        return {"access_token": "victim-access", "refresh_token": "victim-refresh", "expires_in": 3600}

    async def fake_identify(family, key, tokens, app_creds):
        return {"label": "victim@zoom.example", "external_id": "victim"}

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)

    url = client.post(f"{API}/integration-hub/zoom/connect", json={}, headers=h).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    victim = signup(client, "Victim Co")
    for session in (victim["headers"]["X-Session-ID"], None):
        headers = {"X-Session-ID": session} if session else {}
        back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "victim-code"}, headers=headers, follow_redirects=False)
        assert back.status_code == 302 and "error=" in back.headers["location"]
    assert exchanged == []  # the victim's code is never exchanged
    assert client.get(f"{API}/integration-hub/zoom", headers=h).json()["connection"] is None


def test_cancelled_sign_in_returns_with_message(client, ws):
    h = ws["headers"]
    client.put(f"{API}/integration-hub/dropbox/toggle", json={"enabled": True}, headers=h)
    client.put(f"{API}/integration-hub/apps/dropbox", json={"client_id": "dbx", "client_secret": "dbx-secret"}, headers=h)
    url = client.post(f"{API}/integration-hub/dropbox/connect", json={}, headers=h).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert back.status_code == 302 and "/integrations/dropbox" in back.headers["location"] and "cancelled" in back.headers["location"]


def test_credentials_connect_and_webhook_dispatch_respects_switch(client, ws):
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/wordpress_forms/toggle", json={"enabled": True}, headers=h).status_code == 200
    detail = client.get(f"{API}/integration-hub/wordpress_forms", headers=h).json()
    assert detail["status"] == "connected" and detail["connection"]["webhook_url"]
    token = detail["connection"]["webhook_url"].rsplit("/", 1)[1]
    assert client.post(f"{API}/public/integrations/wordpress_forms/webhook/not-a-token", json={}).status_code == 404
    client.put(f"{API}/integration-hub/wordpress_forms/toggle", json={"enabled": False}, headers=h)
    ignored = client.post(f"{API}/public/integrations/wordpress_forms/webhook/{token}", json={"email": "x@y.example"})
    assert ignored.status_code in (200, 404)
    if ignored.status_code == 200:
        assert ignored.json().get("ignored")

    off = client.put(f"{API}/integration-hub/twilio_sms/credentials", json={"values": {"account_sid": "AC1", "auth_token": "t"}}, headers=h)
    assert off.status_code == 409  # switch it on first


def test_activity_feed(client, ws):
    response = client.get(f"{API}/integration-hub", headers=ws["headers"])
    assert response.status_code == 200
    assert response.json()["items"]


def test_inbound_addresses_are_for_admins_only(client):
    """A form webhook's address is what lets someone post leads, so members don't see it."""
    ws = signup(client, "Address Co")
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/wordpress_forms/toggle", json={"enabled": True}, headers=h).status_code == 200
    admin_view = client.get(f"{API}/integration-hub/wordpress_forms", headers=h).json()
    assert admin_view["connection"]["webhook_url"] and admin_view["extra"]["webhook_url"]

    email = f"member-{uuid.uuid4().hex[:8]}@hub.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Mia Member", "role": "member"}, headers=h).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    member_view = client.get(f"{API}/integration-hub/wordpress_forms", headers=member).json()
    assert member_view["connection"]["webhook_url"] is None and member_view["extra"].get("webhook_url") is None
    assert admin_view["connection"]["webhook_url"] not in str(member_view)


def test_rotated_refresh_token_survives_a_rolled_back_run(monkeypatch):
    """Providers that issue a new refresh token on every refresh (Sage, FreshBooks, Zoom)
    retire the old one: a run that refreshes and then rolls back must not lose the new one."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import delete

    from app.core.crypto import decrypt, encrypt
    from app.db.session import AsyncSessionLocal
    from app.integrations import core
    from app.models.integration_hub import IntegrationConnection
    from app.models.organization import Organization

    calls: list[str] = []

    async def fake_token(family, app_creds, data):
        calls.append(data["refresh_token"])
        if data["refresh_token"] != "ORIGINAL-refresh":
            raise AssertionError("the retired refresh token must not be reused")
        return {"access_token": "new-access", "refresh_token": "ROTATED-refresh", "expires_in": 300}

    async def fake_app(db, org, family):
        return {"client_id": "cid", "client_secret": "secret", "source": "server", "options": {}}

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(core, "oauth_app", fake_app)

    async def main():
        org_id, connection_id = uuid.uuid4(), uuid.uuid4()
        async with AsyncSessionLocal() as db:
            db.add(Organization(id=org_id, name="Token Co", slug=f"tok-{uuid.uuid4().hex[:8]}"))
            await db.flush()
            db.add(IntegrationConnection(id=connection_id, organization_id=org_id, provider="sage_accounting", status="connected",
                                         access_token=encrypt("old-access"), refresh_token=encrypt("ORIGINAL-refresh"),
                                         token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
            await db.commit()
        try:
            async with AsyncSessionLocal() as db:
                connection = await db.get(IntegrationConnection, connection_id)
                assert await core.access_token(db, connection) == "new-access"
                await db.rollback()  # e.g. the next call hit a rate limit
            async with AsyncSessionLocal() as db:
                connection = await db.get(IntegrationConnection, connection_id)
                assert decrypt(connection.refresh_token) == "ROTATED-refresh" and connection.status == "connected"
                assert await core.access_token(db, connection) == "new-access"
            assert calls == ["ORIGINAL-refresh"]
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(IntegrationConnection).where(IntegrationConnection.id == connection_id))
                await db.execute(delete(Organization).where(Organization.id == org_id))
                await db.commit()

    asyncio.run(main())


def test_scheduler_marks_keep_connection_settings():
    """Marking a connection as looked at merges one key into its settings and keeps the rest."""
    import asyncio

    from sqlalchemy import delete, select

    from app.db.session import AsyncSessionLocal
    from app.integrations import core
    from app.models.integration_hub import IntegrationConnection
    from app.models.organization import Organization

    async def main():
        org_id, connection_id = uuid.uuid4(), uuid.uuid4()
        async with AsyncSessionLocal() as db:
            db.add(Organization(id=org_id, name="Mark Co", slug=f"mark-{uuid.uuid4().hex[:8]}"))
            await db.flush()
            db.add(IntegrationConnection(id=connection_id, organization_id=org_id, provider="zendesk", status="connected",
                                         config={"subdomain": "acme", "sync": {"cursor": 5}}))
            await db.commit()
        try:
            async with AsyncSessionLocal() as db:
                await core.mark_seen_ids(db, [connection_id])
                await core.mark_seen_ids(db, [connection_id])
                config = (await db.execute(select(IntegrationConnection.config).where(IntegrationConnection.id == connection_id))).scalar_one()
            assert isinstance(config, dict) and config["subdomain"] == "acme" and config["sync"] == {"cursor": 5} and core.SCHEDULER_SEEN in config
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(IntegrationConnection).where(IntegrationConnection.id == connection_id))
                await db.execute(delete(Organization).where(Organization.id == org_id))
                await db.commit()

    asyncio.run(main())
