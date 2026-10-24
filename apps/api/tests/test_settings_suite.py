"""End-to-end tests for the workspace settings suite.

Covers settings namespaces, security, add-ons, credits, data requests,
territories, support access, MCP, outbound (domains, snippets, sequences,
tracking), data management (picklists, goals, removal requests,
suppression), the ideal customer profile (personas, intent, signals) and
conversation intelligence. Runs against a real PostgreSQL database with a
brand-new workspace (skipped when the database isn't reachable). Nothing
external is called: mail goes to the outbox and DNS lookups are mocked.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

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


def signup(client, company: str = "Suite Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@suite.example"
    try:
        response = client.post(
            f"{API}/auth/signup",
            json={"full_name": "Sam Owner", "email": email, "password": PASSWORD, "company_name": company},
        )
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
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"]},
    }


@pytest.fixture(scope="module")
def ws(client):
    workspace = signup(client)
    changed = client.post(f"{API}/billing/change-plan", json={"plan": "enterprise"}, headers=workspace["headers"])
    assert changed.status_code == 200, changed.text
    return workspace


def invite(client, ws, role: str, name: str) -> dict:
    email = f"{role}-{uuid.uuid4().hex[:8]}@suite.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": name, "role": role}, headers=ws["headers"])
    assert invited.status_code == 201, invited.text
    token = invited.json()["invite_url"].split("token=")[1]
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    assert joined.status_code == 200, joined.text
    return {
        "user_id": joined.json()["user_id"],
        "email": email,
        "headers": {"Authorization": f"Bearer {joined.json()['access_token']}", "X-Organization-ID": ws["org"]},
    }


def run_sql(statement: str, **params):
    from sqlalchemy import text

    from app.db.session import engine

    async def go():
        async with engine.begin() as connection:
            result = await connection.execute(text(statement), params)
            try:
                return result.mappings().all()
            except Exception:  # noqa: BLE001 - statements without rows
                return None

    return asyncio.run(go())


def run_service(fn):
    """Run ``fn(db)`` against a fresh session (scheduler-style)."""

    from app.db.session import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            result = await fn(db)
            await db.commit()
            return result

    return asyncio.run(go())


def settings(client, ws, namespace: str, data: dict, secrets: dict | None = None, headers=None):
    response = client.put(
        f"{API}/workspace-settings/{namespace}", json={"data": data, "secrets": secrets or {}}, headers=headers or ws["headers"]
    )
    assert response.status_code == 200, response.text
    return response.json()


def new_contact(client, ws, **fields) -> dict:
    payload = {"first_name": "Nora", "last_name": "Quinn", "email": f"nora-{uuid.uuid4().hex[:6]}@prospect.example", **fields}
    response = client.post(f"{API}/contacts", json=payload, headers=ws["headers"])
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Settings framework
# ---------------------------------------------------------------------------


def test_settings_namespaces_defaults_validation_and_secrets(client, ws):
    h = ws["headers"]
    sending = client.get(f"{API}/workspace-settings/sending", headers=h)
    assert sending.status_code == 200, sending.text
    assert sending.json()["data"]["daily_limit_per_mailbox"] == 200
    assert client.get(f"{API}/workspace-settings/nope", headers=h).status_code == 404

    bad = client.put(f"{API}/workspace-settings/sending", json={"data": {"daily_limit_per_mailbox": 0}}, headers=h)
    assert bad.status_code == 422

    saved = settings(client, ws, "enrichment", {"overwrite": True}, secrets={"hunter_api_key": "hk_test_123"})
    assert saved["data"]["overwrite"] is True
    assert "hk_test_123" not in str(saved)
    again = client.get(f"{API}/workspace-settings/enrichment", headers=h).json()
    assert "hk_test_123" not in str(again)
    assert again["secrets"]["hunter_api_key"] is True
    cleared = settings(client, ws, "enrichment", {}, secrets={"hunter_api_key": None})
    assert cleared["secrets"]["hunter_api_key"] is False

    # Members can read but not change admin namespaces.
    member = invite(client, ws, "member", "Mia Member")
    assert client.get(f"{API}/workspace-settings/sending", headers=member["headers"]).status_code == 200
    assert client.put(f"{API}/workspace-settings/sending", json={"data": {"daily_limit_per_mailbox": 10}}, headers=member["headers"]).status_code == 403


def test_security_password_rule_ip_allowlist_and_api_keys(client, ws):
    h = ws["headers"]
    # Locking yourself out is refused.
    locked = client.put(f"{API}/workspace-settings/security", json={"data": {"ip_allowlist": ["203.0.113.0/24"]}}, headers=h)
    assert locked.status_code == 409, locked.text
    assert client.put(f"{API}/workspace-settings/security", json={"data": {"ip_allowlist": ["not-an-ip"]}}, headers=h).status_code == 422

    overview = client.get(f"{API}/security/overview", headers=h)
    assert overview.status_code == 200, overview.text

    settings(client, ws, "security", {"password_min_length": 14, "allowed_email_domains": ["suite.example"]})
    outsider = client.post(f"{API}/admin/users/invite", json={"email": "x@elsewhere.example", "full_name": "Out Sider", "role": "member"}, headers=h)
    assert outsider.status_code in (400, 422), outsider.text
    short = client.post(f"{API}/auth/password/change", json={"current_password": PASSWORD, "new_password": "Short#20261"}, headers=h)
    assert short.status_code in (400, 422), short.text

    key = client.post(f"{API}/api-keys", json={"name": "Robot", "scope": "read"}, headers=h).json()
    api_headers = {"Authorization": f"Bearer {key['key']}"}
    assert client.get(f"{API}/accounts", headers=api_headers).status_code == 200
    settings(client, ws, "security", {"allow_api_keys": False})
    assert client.get(f"{API}/accounts", headers=api_headers).status_code == 403
    settings(client, ws, "security", {"allow_api_keys": True, "allowed_email_domains": [], "password_min_length": 10})
    assert client.get(f"{API}/accounts", headers=api_headers).status_code == 200

    me = client.get(f"{API}/auth/me", headers=h).json()
    assert "mfa_enforced" in me and "addons" in me["subscription"]


def test_require_mfa_blocks_users_without_mfa(client):
    other = signup(client, "MFA Co")
    h = other["headers"]
    # The owner has no MFA yet, so turning it on would lock them out.
    response = client.put(f"{API}/workspace-settings/security", json={"data": {"require_mfa": True}}, headers=h)
    assert response.status_code == 409, response.text
    assert "two-step" in response.json()["detail"]
    # Once required (here set directly), members without MFA can only reach sign-in and MFA setup.
    run_sql(
        "insert into workspace_settings (id, organization_id, namespace, data, secrets, created_at, updated_at) "
        "values (gen_random_uuid(), :org, 'security', '{\"require_mfa\": true}'::jsonb, '{}'::jsonb, now(), now()) "
        "on conflict (organization_id, namespace) do update set data = excluded.data",
        org=other["org"],
    )
    from app.api.dependencies.auth import invalidate_security_cache

    invalidate_security_cache(uuid.UUID(other["org"]))
    blocked = client.get(f"{API}/accounts", headers=h)
    assert blocked.status_code == 403, blocked.text
    me = client.get(f"{API}/auth/me", headers=h).json()
    assert me["mfa_enforced"] is True


# ---------------------------------------------------------------------------
# Plan, add-ons, credits and AI words
# ---------------------------------------------------------------------------


def test_addons_credits_and_ai_usage(client, ws):
    h = ws["headers"]
    listing = client.get(f"{API}/billing/addons", headers=h)
    assert listing.status_code == 200, listing.text
    keys = {a["key"] for a in listing.json()["items"]}
    assert {"credit_pack", "ai_words", "conversation_intelligence", "advanced_data"} <= keys

    before = client.get(f"{API}/credits/usage", headers=h).json()
    bought = client.post(f"{API}/billing/addons/credit_pack", json={"quantity": 2}, headers=h)
    assert bought.status_code == 200, bought.text
    after = client.get(f"{API}/credits/usage", headers=h).json()
    assert after["allowance"]["credits_total"] == before["allowance"]["credits_total"] + 10000
    assert after["remaining"] == after["allowance"]["credits_total"] - after["used"]

    csv_export = client.get(f"{API}/credits/usage.csv", headers=h)
    assert csv_export.status_code == 200 and csv_export.text.startswith("Date")

    ai = client.get(f"{API}/ai-usage", headers=h)
    assert ai.status_code == 200, ai.text
    assert ai.json()["allowance"] > 0

    assert client.delete(f"{API}/billing/addons/credit_pack", headers=h).status_code == 200
    assert client.post(f"{API}/billing/addons/unknown", json={}, headers=h).status_code == 404


def test_trial_buys_feature_addon(client):
    other = signup(client, "Addon Co")
    h = other["headers"]
    assert client.post(f"{API}/billing/change-plan", json={"plan": "starter"}, headers=h).status_code == 200
    assert client.get(f"{API}/conversations", headers=h).status_code == 402
    bought = client.post(f"{API}/billing/addons/conversation_intelligence", json={}, headers=h)
    assert bought.status_code == 200, bought.text
    assert client.get(f"{API}/conversations", headers=h).status_code == 200
    me = client.get(f"{API}/auth/me", headers=h).json()
    assert "conversation_intelligence" in me["subscription"]["features"]
    assert "conversation_intelligence" in me["subscription"]["addons"]


def test_data_requests_flow(client, ws):
    h = ws["headers"]
    contact = new_contact(client, ws, phone=None)
    created = client.post(f"{API}/data-requests", json={"record_type": "contact", "record_id": contact["id"], "fields": ["phone", "job_title"], "note": "Need a direct line"}, headers=h)
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    # A second request for the same person merges into the open one.
    duplicate = client.post(f"{API}/data-requests", json={"record_type": "contact", "record_id": contact["id"], "fields": ["email", "phone"]}, headers=h)
    assert duplicate.status_code == 201 and duplicate.json()["id"] == request_id
    assert duplicate.json()["fields"] == ["phone", "job_title", "email"]

    listing = client.get(f"{API}/data-requests", headers=h).json()
    assert any(r["id"] == request_id for r in listing["items"])

    fulfilled = client.post(f"{API}/data-requests/{request_id}/fulfil", json={"values": {"phone": "+1 469 555 0199", "job_title": "VP Operations"}}, headers=h)
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json()["status"] == "fulfilled"
    record = client.get(f"{API}/contacts/{contact['id']}", headers=h).json()
    assert record["phone"] == "+1 469 555 0199" and record["job_title"] == "VP Operations"

    second = client.post(f"{API}/data-requests", json={"record_type": "contact", "record_id": contact["id"], "fields": ["mobile"]}, headers=h).json()
    closed = client.post(f"{API}/data-requests/{second['id']}/not-found", json={"note": "No mobile anywhere"}, headers=h)
    assert closed.status_code == 200 and closed.json()["status"] == "not_found"


# ---------------------------------------------------------------------------
# Users & teams: licenses, territories, support access, stages
# ---------------------------------------------------------------------------


def test_licenses_overview(client, ws):
    response = client.get(f"{API}/licenses", headers=ws["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["seats_active"] >= 1
    assert any(u["id"] == ws["user_id"] for u in body["users"])


def test_territories_auto_assign_round_robin(client, ws):
    h = ws["headers"]
    rep_a = invite(client, ws, "member", "Ari Rep")
    rep_b = invite(client, ws, "member", "Bea Rep")
    payload = {
        "name": "Texas",
        "rules": [{"field": "state", "operator": "in", "values": ["TX", "Texas"]}],
        "member_user_ids": [rep_a["user_id"], rep_b["user_id"]],
        "assignment": "round_robin",
    }
    preview = client.post(f"{API}/territories/preview", json=payload, headers=h)
    assert preview.status_code == 200, preview.text
    created = client.post(f"{API}/territories", json=payload, headers=h)
    assert created.status_code == 201, created.text
    territory_id = created.json()["id"]
    assert client.post(f"{API}/territories", json={**payload, "rules": [{"field": "nope", "operator": "in", "values": ["x"]}]}, headers=h).status_code == 422

    first = client.post(f"{API}/accounts", json={"name": "Lone Star Cooling", "billing_state": "TX"}, headers=h).json()
    second = client.post(f"{API}/accounts", json={"name": "Austin Air", "billing_state": "Texas"}, headers=h).json()
    outside = client.post(f"{API}/accounts", json={"name": "Ohio Heat", "billing_state": "OH"}, headers=h).json()
    assert first["territory_id"] == territory_id and second["territory_id"] == territory_id
    assert outside["territory_id"] is None
    owners = {first["owner_user_id"], second["owner_user_id"]}
    assert owners == {rep_a["user_id"], rep_b["user_id"]}

    applied = client.post(f"{API}/territories/apply", json={"reassign_owners": False}, headers=h)
    assert applied.status_code == 200, applied.text
    listing = client.get(f"{API}/territories", headers=h).json()
    texas = next(t for t in listing["items"] if t["id"] == territory_id)
    assert texas["accounts"] >= 2
    assert client.delete(f"{API}/territories/{territory_id}", headers=h).status_code == 204


def test_support_access_grant_and_revoke(client, ws):
    h = ws["headers"]
    staff = signup(client, "AIVA Support Desk")
    run_sql("update users set is_platform_staff = true where id = :id", id=staff["user_id"])
    staff_headers = {**staff["headers"], "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/accounts", headers=staff_headers).status_code in (401, 403)
    full = client.post(f"{API}/support-access", json={"access_level": "full", "hours": 1}, headers=h).json()
    # Staff without two-step verification can't use a grant at all.
    assert client.get(f"{API}/accounts", headers=staff_headers).status_code in (401, 403)
    run_sql("update users set mfa_enabled = true where id = :id", id=staff["user_id"])
    assert client.post(f"{API}/accounts", json={"name": "Fixed by support"}, headers=staff_headers).status_code == 201
    # Even full access can't touch keys, people, billing or security.
    assert client.post(f"{API}/api-keys", json={"name": "Backdoor", "scope": "read_write"}, headers=staff_headers).status_code == 403
    assert client.put(f"{API}/workspace-settings/security", json={"data": {"require_mfa": False}}, headers=staff_headers).status_code == 403
    assert client.post(f"{API}/support-access/{full['id']}/revoke", headers=h).status_code == 200

    granted = client.post(f"{API}/support-access", json={"access_level": "read_only", "hours": 2, "reason": "Import help"}, headers=h)
    assert granted.status_code == 201, granted.text
    assert client.get(f"{API}/accounts", headers=staff_headers).status_code == 200
    assert client.post(f"{API}/accounts", json={"name": "Support wrote this"}, headers=staff_headers).status_code == 403
    listing = client.get(f"{API}/support-access", headers=h).json()
    assert listing["active"]["id"] == granted.json()["id"]

    assert client.post(f"{API}/support-access/{granted.json()['id']}/revoke", headers=h).status_code == 200
    assert client.get(f"{API}/accounts", headers=staff_headers).status_code in (401, 403)


def test_contact_stages_validation_and_move(client, ws):
    h = ws["headers"]
    contact = new_contact(client, ws)
    assert contact["stage"] == "cold"
    assert client.patch(f"{API}/contacts/{contact['id']}", json={"stage": "made_up"}, headers=h).status_code == 422
    updated = client.patch(f"{API}/contacts/{contact['id']}", json={"stage": "interested"}, headers=h)
    assert updated.status_code == 200 and updated.json()["stage"] == "interested"

    usage = client.get(f"{API}/stages", headers=h).json()
    assert next(s for s in usage["contact"] if s["key"] == "interested")["count"] >= 1
    current = client.get(f"{API}/workspace-settings/stages", headers=h).json()["data"]
    without = [s for s in current["contact"] if s["key"] != "interested"]
    refused = client.put(f"{API}/workspace-settings/stages", json={"data": {"contact": without}}, headers=h)
    assert refused.status_code == 409, refused.text
    moved = client.post(f"{API}/stages/move", json={"object_type": "contact", "from_stage": "interested", "to_stage": "replied"}, headers=h)
    assert moved.status_code == 200 and moved.json()["moved"] >= 1
    settings(client, ws, "stages", {"contact": without})
    settings(client, ws, "stages", {"contact": current["contact"]})


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def test_mcp_json_rpc(client, ws):
    h = ws["headers"]
    key = client.post(f"{API}/api-keys", json={"name": "Claude Desktop", "scope": "read"}, headers=h).json()
    mcp = {"Authorization": f"Bearer {key['key']}", "Accept": "application/json, text/event-stream"}
    init = client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "pytest", "version": "1"}}}, headers=mcp)
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "aiva-crm"
    assert client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=mcp).status_code == 202

    tools = client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=mcp).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "search_records" in names and "create_lead" not in names  # writes are off by default

    client.post(f"{API}/accounts", json={"name": "Zephyr Mechanical"}, headers=h)
    found = client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_records", "arguments": {"query": "Zephyr"}}}, headers=mcp).json()
    assert found["result"]["isError"] is False
    assert "Zephyr Mechanical" in found["result"]["content"][0]["text"]

    denied = client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "create_lead", "arguments": {"first_name": "No"}}}, headers=mcp).json()
    assert denied["result"]["isError"] is True

    settings(client, ws, "mcp", {"allow_write": True})
    write_key = client.post(f"{API}/api-keys", json={"name": "Writer", "scope": "read_write"}, headers=h).json()
    writer = {"Authorization": f"Bearer {write_key['key']}"}
    created = client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "create_lead", "arguments": {"first_name": "Mcp", "last_name": "Lead", "company": "Agent Co"}}}, headers=writer).json()
    assert created["result"]["isError"] is False, created

    batch = client.post(f"{API}/mcp", json=[{"jsonrpc": "2.0", "id": 6, "method": "ping"}, {"jsonrpc": "2.0", "id": 7, "method": "nope"}], headers=mcp).json()
    assert batch[0]["result"] == {} and batch[1]["error"]["code"] == -32601
    assert client.get(f"{API}/mcp", headers=mcp).status_code == 405
    overview = client.get(f"{API}/mcp/overview", headers=h).json()
    assert any(c["tool"] == "search_records" for c in overview["calls"])
    settings(client, ws, "mcp", {"enabled": False})
    assert client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "id": 8, "method": "ping"}, headers=mcp).status_code == 403
    settings(client, ws, "mcp", {"enabled": True, "allow_write": False})


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------


def test_domains_with_mocked_dns(client, ws, monkeypatch):
    from app.services import outbound

    records = {
        ("acme-sends.example", "TXT"): ["v=spf1 include:_spf.google.com ~all"],
        ("_dmarc.acme-sends.example", "TXT"): ["v=DMARC1; p=quarantine"],
        ("google._domainkey.acme-sends.example", "TXT"): ["v=DKIM1; k=rsa; p=MIIB"],
        ("acme-sends.example", "MX"): ["10 aspmx.l.google.com."],
    }

    async def fake_lookup(name, record_type):
        return records.get((name.rstrip("."), record_type), [])

    monkeypatch.setattr(outbound, "dns_lookup", fake_lookup)
    h = ws["headers"]
    created = client.post(f"{API}/outbound/domains", json={"domain": "https://Acme-Sends.example/", "is_default": True}, headers=h)
    assert created.status_code == 201, created.text
    domain = created.json()
    assert domain["domain"] == "acme-sends.example"
    assert domain["status"] == "verified", domain
    assert client.post(f"{API}/outbound/domains", json={"domain": "acme-sends.example"}, headers=h).status_code == 409

    records.pop(("_dmarc.acme-sends.example", "TXT"))
    rechecked = client.post(f"{API}/outbound/domains/{domain['id']}/check", headers=h).json()
    assert rechecked["checks"]["dmarc"]["status"] != "pass"

    assert client.get(f"{API}/outbound/overview", headers=h).status_code == 200
    assert client.get(f"{API}/outbound/mailboxes", headers=h).status_code == 200
    diagnose = client.get(f"{API}/outbound/diagnose", headers=h)
    assert diagnose.status_code == 200, diagnose.text
    assert client.delete(f"{API}/outbound/domains/{domain['id']}", headers=h).status_code == 204


def test_snippets(client, ws):
    h = ws["headers"]
    created = client.post(f"{API}/snippets", json={"name": "Pricing", "shortcut": "price", "body": "Hi {{first_name}}, plans start at $49.", "category": "Sales"}, headers=h)
    assert created.status_code == 201, created.text
    snippet = created.json()
    contact = new_contact(client, ws, first_name="Pia")
    used = client.post(f"{API}/snippets/{snippet['id']}/use", json={"record_type": "contact", "record_id": contact["id"]}, headers=h)
    assert used.status_code == 200, used.text
    assert used.json()["text"].startswith("Hi Pia,")
    listing = client.get(f"{API}/snippets", headers=h).json()
    assert any(s["id"] == snippet["id"] and s["usage_count"] == 1 for s in listing["items"])
    assert client.post(f"{API}/snippets", json={"name": "Dup", "shortcut": "price", "body": "x"}, headers=h).status_code == 409
    assert client.delete(f"{API}/snippets/{snippet['id']}", headers=h).status_code == 204


def test_sequences_end_to_end(client, ws):
    from app.models.outbound import SequenceMessage
    from app.services import outbound

    h = ws["headers"]
    settings(client, ws, "sending", {"send_days": [0, 1, 2, 3, 4, 5, 6], "window_start_hour": 0, "window_end_hour": 24, "min_seconds_between": 0})
    first = new_contact(client, ws, first_name="Ola")
    second = new_contact(client, ws, first_name="Ben")
    role = new_contact(client, ws, first_name="Info", email=f"info@role-{uuid.uuid4().hex[:5]}.example")

    sequence = client.post(f"{API}/sequences", json={"name": "Cold outreach", "description": "Three touches"}, headers=h)
    assert sequence.status_code == 201, sequence.text
    sequence_id = sequence.json()["id"]
    step_one = client.post(f"{API}/sequences/{sequence_id}/steps", json={"kind": "auto_email", "subject": "Quick question, {{first_name}}", "body": "Hi {{first_name}},\n\nSee https://aiva.example/pricing"}, headers=h)
    assert step_one.status_code == 201, step_one.text
    client.post(f"{API}/sequences/{sequence_id}/steps", json={"kind": "call", "delay_days": 0, "note": "Call about the email"}, headers=h)
    client.post(f"{API}/sequences/{sequence_id}/steps", json={"kind": "auto_email", "delay_days": 0, "body": "Following up"}, headers=h)

    preview = client.post(f"{API}/sequences/{sequence_id}/steps/{step_one.json()['id']}/preview", json={"record_type": "contact", "record_id": first["id"]}, headers=h)
    assert preview.status_code == 200 and preview.json()["subject"] == "Quick question, Ola"

    # Draft sequences don't accept people.
    assert client.post(f"{API}/sequences/{sequence_id}/enroll", json={"record_type": "contact", "record_ids": [first["id"]]}, headers=h).status_code == 409
    assert client.post(f"{API}/sequences/{sequence_id}/status", json={"status": "active"}, headers=h).status_code == 200
    enrolled = client.post(f"{API}/sequences/{sequence_id}/enroll", json={"record_type": "contact", "record_ids": [first["id"], second["id"], role["id"]]}, headers=h)
    assert enrolled.status_code == 200, enrolled.text
    assert len(enrolled.json()["enrolled"]) == 2
    assert enrolled.json()["skipped"][0]["reason"].startswith("Shared inbox")
    assert client.get(f"{API}/contacts/{first['id']}", headers=h).json()["stage"] == "approaching"

    # Step 1: the first email goes out.
    run_service(outbound.process_due)
    enrollments = client.get(f"{API}/sequences/{sequence_id}/enrollments", headers=h).json()["items"]
    assert all(e["step"] == 2 for e in enrollments), enrollments
    tokens = run_sql("select token, to_email, subject from sequence_messages where sequence_id = :id and kind = 'auto_email'", id=sequence_id)
    assert len(tokens) == 2 and tokens[0]["subject"].startswith("Quick question")

    # Step 2: a call task is created and the enrollment waits for it.
    run_service(outbound.process_due)
    tasks = client.get(f"{API}/tasks", params={"source": "sequence"}, headers=h).json()
    task_items = tasks["items"] if isinstance(tasks, dict) else tasks
    call_tasks = [t for t in task_items if t["title"].startswith("Call ")]
    assert len(call_tasks) == 2, task_items

    # Click tracking redirects and records the click.
    token = next(t["token"] for t in tokens if t["to_email"] == first["email"])
    link = outbound.click_url(token, "https://aiva.example/pricing")
    path = link.split("/api/v1", 1)[1] if "/api/v1" in link else link.split("/api/aiva", 1)[1]
    clicked = client.get(f"{API}{path}", follow_redirects=False)
    assert clicked.status_code == 302 and clicked.headers["location"] == "https://aiva.example/pricing"
    assert client.get(f"{API}/public/click/{token}", params={"u": "https://evil.example", "s": "bad"}, follow_redirects=False).status_code == 400

    # A reply stops the sequence for that person.
    def reply(db):
        message = SimpleNamespace(from_address=first["email"], subject="Re: Quick question", text="Sounds good", date=datetime.now(timezone.utc))
        return outbound.handle_inbound(db, uuid.UUID(ws["org"]), message)

    assert run_service(reply) == "reply"
    record = client.get(f"{API}/sequences/for/contact/{first['id']}", headers=h).json()
    assert record["items"][0]["status"] == "replied"
    assert client.get(f"{API}/contacts/{first['id']}", headers=h).json()["stage"] == "replied"

    # Unsubscribing stops the other one and adds them to the suppression list.
    other_token = next(t["token"] for t in tokens if t["to_email"] == second["email"])
    assert client.post(f"{API}/public/unsubscribe/{other_token}").status_code in (200, 303)
    suppression = client.get(f"{API}/suppression", headers=h).json()
    assert any(s["value"] == second["email"] for s in suppression["items"])
    again = client.post(f"{API}/sequences/{sequence_id}/enroll", json={"record_type": "contact", "record_ids": [second["id"]]}, headers=h).json()
    assert again["enrolled"] == []

    detail = client.get(f"{API}/sequences/{sequence_id}", headers=h).json()
    assert detail["stats"]["sent"] == 2 and detail["stats"]["replied"] == 1
    duplicate = client.post(f"{API}/sequences/{sequence_id}/duplicate", headers=h)
    assert duplicate.status_code == 201
    copy = client.get(f"{API}/sequences/{duplicate.json()['id']}", headers=h).json()
    assert copy["status"] == "draft" and len(copy["steps"]) == 3
    assert SequenceMessage is not None


def test_bounce_and_meeting_stop_sequences(client, ws):
    from app.services import outbound

    h = ws["headers"]
    settings(client, ws, "sending", {"send_days": [0, 1, 2, 3, 4, 5, 6], "window_start_hour": 0, "window_end_hour": 24, "min_seconds_between": 0})
    bouncer = new_contact(client, ws, first_name="Bo")
    booker = new_contact(client, ws, first_name="Mae")
    sequence = client.post(f"{API}/sequences", json={"name": "Bounce test"}, headers=h).json()
    client.post(f"{API}/sequences/{sequence['id']}/steps", json={"kind": "auto_email", "subject": "Hello", "body": "Hi"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/steps", json={"kind": "auto_email", "delay_days": 3, "body": "Bump"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/status", json={"status": "active"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/enroll", json={"record_type": "contact", "record_ids": [bouncer["id"], booker["id"]]}, headers=h)
    run_service(outbound.process_due)

    def bounce(db):
        message = SimpleNamespace(from_address="mailer-daemon@googlemail.com", subject="Delivery Status Notification (Failure)", text=f"Your message to {bouncer['email']} couldn't be delivered.", date=None)
        return outbound.handle_inbound(db, uuid.UUID(ws["org"]), message)

    assert run_service(bounce) == "bounce"
    assert client.get(f"{API}/contacts/{bouncer['id']}", headers=h).json()["stage"] == "bad_data"

    meeting = client.post(
        f"{API}/meetings",
        json={"title": "Intro call", "start_at": "2030-01-10T15:00:00Z", "end_at": "2030-01-10T15:30:00Z", "attendees": [{"type": "email", "email": booker["email"], "name": "Mae"}]},
        headers=h,
    )
    assert meeting.status_code == 201, meeting.text
    statuses = {e["email"]: e["status"] for e in client.get(f"{API}/sequences/{sequence['id']}/enrollments", headers=h).json()["items"]}
    assert statuses[bouncer["email"]] == "bounced" and statuses[booker["email"]] == "meeting_booked"


def test_dialer_outcomes_drive_call_logging(client, ws):
    h = ws["headers"]
    settings(client, ws, "dialer", {"outcomes": [{"key": "connected", "label": "Connected", "connected": True}, {"key": "gatekeeper", "label": "Gatekeeper", "follow_up": True}], "require_notes": True})
    calls = client.get(f"{API}/calls", headers=h).json()
    assert [o["key"] for o in calls["outcome_options"]] == ["connected", "gatekeeper"]
    assert client.post(f"{API}/dialer/test", headers=h).status_code in (400, 409)
    settings(client, ws, "dialer", {"outcomes": [{"key": "connected", "label": "Connected", "connected": True}, {"key": "no_answer", "label": "No answer", "follow_up": True}], "require_notes": False})


# ---------------------------------------------------------------------------
# Data management
# ---------------------------------------------------------------------------


def test_picklists_sync_custom_fields(client, ws):
    h = ws["headers"]
    picklist = client.post(f"{API}/picklists", json={"name": "Regions", "values": [{"value": "North"}, {"value": "South"}]}, headers=h)
    assert picklist.status_code == 201, picklist.text
    picklist_id = picklist.json()["id"]
    field = client.post(f"{API}/custom-fields", json={"object_type": "account", "label": "Sales region", "field_type": "picklist", "picklist_id": picklist_id}, headers=h)
    assert field.status_code == 201, field.text
    assert field.json()["options"] == ["North", "South"]
    client.put(f"{API}/picklists/{picklist_id}", json={"name": "Regions", "values": [{"value": "North"}, {"value": "South"}, {"value": "West"}]}, headers=h)
    fields = client.get(f"{API}/custom-fields", params={"object_type": "account"}, headers=h).json()
    items = fields["items"] if isinstance(fields, dict) else fields
    region = next(f for f in items if f["id"] == field.json()["id"])
    assert region["options"] == ["North", "South", "West"]
    assert client.delete(f"{API}/picklists/{picklist_id}", headers=h).status_code == 409


def test_goals_track_progress(client, ws):
    h = ws["headers"]
    goal = client.post(f"{API}/goals", json={"name": "New leads", "metric": "leads_created", "period": "month", "target": 50, "assignee_type": "workspace"}, headers=h)
    assert goal.status_code == 201, goal.text
    listing = client.get(f"{API}/goals", headers=h).json()
    mine = next(g for g in listing["items"] if g["id"] == goal.json()["id"])
    assert mine["actual"] >= 1 and 0 < mine["percent"] <= 100
    assert client.post(f"{API}/goals", json={"name": "Bad", "metric": "made_up", "target": 1}, headers=h).status_code == 422
    assert client.delete(f"{API}/goals/{goal.json()['id']}", headers=h).status_code == 204


def test_removal_requests_erase_and_suppress(client, ws):
    h = ws["headers"]
    email = f"erase-{uuid.uuid4().hex[:6]}@gone.example"
    contact = new_contact(client, ws, email=email)
    lead = client.post(f"{API}/leads", json={"first_name": "Era", "last_name": "Se", "email": email}, headers=h)
    assert lead.status_code in (201, 409), lead.text
    request = client.post(f"{API}/removal-requests", json={"email": email, "full_name": "Era Se", "regulation": "gdpr"}, headers=h)
    assert request.status_code == 201, request.text
    detail = client.get(f"{API}/removal-requests/{request.json()['id']}", headers=h).json()
    assert len(detail["matches"]["contacts"]) >= 1
    done = client.post(f"{API}/removal-requests/{request.json()['id']}/complete", json={}, headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"
    assert client.get(f"{API}/contacts/{contact['id']}", headers=h).status_code == 404
    blocked = client.post(f"{API}/contacts", json={"first_name": "Era", "last_name": "Again", "email": email}, headers=h)
    assert blocked.status_code == 409


def test_suppression_list(client, ws):
    h = ws["headers"]
    added = client.post(f"{API}/suppression", json={"values": ["blocked.example", "one@person.example", "not valid"]}, headers=h)
    assert added.status_code == 201, added.text
    assert added.json()["added"] == 2
    listing = client.get(f"{API}/suppression", headers=h).json()
    entry = next(s for s in listing["items"] if s["value"] == "blocked.example")
    assert entry["kind"] == "domain"
    assert client.delete(f"{API}/suppression/{entry['id']}", headers=h).status_code == 204


def test_enrichment_builtin_provider(client, ws):
    h = ws["headers"]
    providers = client.get(f"{API}/enrichment/providers", headers=h)
    assert providers.status_code == 200, providers.text
    contact = new_contact(client, ws, email=f"dana@{uuid.uuid4().hex[:6]}-hvac.example", job_title="VP of Sales")
    run = client.post(f"{API}/enrichment/run", json={"record_type": "contact", "record_id": contact["id"]}, headers=h)
    assert run.status_code == 200, run.text
    record = client.get(f"{API}/contacts/{contact['id']}", headers=h).json()
    assert record.get("seniority") or record.get("department") or run.json()["updated"]


def test_scoring_for_contacts(client, ws):
    h = ws["headers"]
    rule = client.post(f"{API}/lead-scoring/rules", json={"name": "Has VP title", "object_type": "contact", "field": "job_title", "operator": "contains", "value": "VP", "points": 15}, headers=h)
    assert rule.status_code == 201, rule.text
    contact = new_contact(client, ws, job_title="VP Finance")
    assert client.get(f"{API}/contacts/{contact['id']}", headers=h).json()["score"] == 15
    listing = client.get(f"{API}/lead-scoring/rules", params={"object_type": "contact"}, headers=h).json()
    items = listing["items"] if isinstance(listing, dict) else listing
    assert all(r["object_type"] == "contact" for r in items)


# ---------------------------------------------------------------------------
# Ideal customer profile
# ---------------------------------------------------------------------------


def test_personas_intent_and_signals(client, ws):
    h = ws["headers"]
    persona = client.post(f"{API}/personas", json={"name": "Ops leaders", "title_keywords": ["operations", "COO"], "exclude_keywords": ["assistant"]}, headers=h)
    assert persona.status_code == 201, persona.text
    tested = client.post(f"{API}/personas/test", json={"title": "VP of Operations"}, headers=h).json()
    assert any(p["id"] == persona.json()["id"] for p in tested["personas"])
    assert client.post(f"{API}/personas/test", json={"title": "Operations Assistant"}, headers=h).json()["personas"] == []
    contact = new_contact(client, ws, job_title="Chief Operating Officer (COO)")
    people = client.get(f"{API}/personas/{persona.json()['id']}/people", headers=h).json()
    assert any(p["id"] == contact["id"] for p in people["items"])
    for_record = client.get(f"{API}/personas/for/contact/{contact['id']}", headers=h).json()
    assert for_record["items"][0]["name"] == "Ops leaders"

    topic = client.post(f"{API}/intent/topics", json={"name": "Pricing", "url_patterns": ["/pricing"], "weight": 3}, headers=h)
    assert topic.status_code == 201, topic.text
    assert client.get(f"{API}/intent", headers=h).status_code == 200

    rule = client.post(f"{API}/signals/rules", json={"name": "COO joined", "kind": "persona_match", "config": {"persona_id": persona.json()["id"]}, "create_task": True}, headers=h)
    assert rule.status_code == 201, rule.text
    assert client.post(f"{API}/signals/rules", json={"name": "Bad", "kind": "nope"}, headers=h).status_code == 422
    fired = client.post(f"{API}/signals/rules/{rule.json()['id']}/run", headers=h)
    assert fired.status_code == 200, fired.text
    assert fired.json()["fired"] >= 1
    again = client.post(f"{API}/signals/rules/{rule.json()['id']}/run", headers=h).json()
    assert again["fired"] == 0  # deduplicated
    events = client.get(f"{API}/signals/events", headers=h).json()
    event = next(e for e in events["items"] if e["rule_id"] == rule.json()["id"])
    assert client.post(f"{API}/signals/events/{event['id']}", json={"status": "actioned"}, headers=h).status_code == 200


# ---------------------------------------------------------------------------
# Conversation intelligence
# ---------------------------------------------------------------------------

VTT = """WEBVTT

1
00:00:01.000 --> 00:00:06.000
<v Sam Owner>Thanks for joining. What's driving the search for a new CRM?

2
00:00:06.500 --> 00:00:15.000
<v Nora Quinn>Our budget is about 40,000 dollars and we're comparing you with Salesforce. My card is 4111 1111 1111 1111.

3
00:00:15.500 --> 00:00:20.000
<v Sam Owner>Great. Next step: I'll send a proposal by Friday.
"""


def test_conversations_processing_reviews_and_prompts(client, ws):
    h = ws["headers"]
    tracker = client.post(f"{API}/conversation-trackers", json={"name": "Competitors", "category": "competition", "keywords": ["salesforce", "hubspot"]}, headers=h)
    assert tracker.status_code == 201, tracker.text
    account = client.post(f"{API}/accounts", json={"name": "Quinn Industries"}, headers=h).json()
    deal = client.post(f"{API}/opportunities", json={"name": "Quinn CRM", "account_id": account["id"], "amount": 1000}, headers=h)
    assert deal.status_code == 201, deal.text
    prompt = client.post(f"{API}/field-prompts", json={"name": "Budget", "object_type": "opportunity", "field_key": "amount", "prompt": "What budget did they mention?", "keywords": ["budget"]}, headers=h)
    assert prompt.status_code == 201, prompt.text

    created = client.post(f"{API}/conversations", json={"title": "Discovery with Quinn", "transcript_text": VTT, "account_id": account["id"], "opportunity_id": deal.json()["id"]}, headers=h)
    assert created.status_code == 201, created.text
    conversation = created.json()
    assert conversation["status"] == "ready"
    assert "4111" not in str(conversation["transcript"])
    assert conversation["tracker_hits"][tracker.json()["id"]]["count"] >= 1
    assert {s["speaker"] for s in conversation["talk_time"]["speakers"]} == {"Sam Owner", "Nora Quinn"}
    assert conversation["summary"]
    suggestion = next(s for s in conversation["field_suggestions"] if s["field_key"] == "amount")
    assert float(suggestion["value"]) == 40000
    applied = client.post(f"{API}/conversations/{conversation['id']}/suggestions/{suggestion['id']}", json={"action": "apply"}, headers=h)
    assert applied.status_code == 200, applied.text
    assert float(client.get(f"{API}/opportunities/{deal.json()['id']}", headers=h).json()["amount"]) == 40000

    upload = client.post(f"{API}/conversations/upload", data={"title": "Uploaded call"}, files={"file": ("call.srt", "1\n00:00:01,000 --> 00:00:03,000\nRep: Hello there\n\n2\n00:00:03,500 --> 00:00:05,000\nBuyer: Hi, we use HubSpot today\n", "text/plain")}, headers=h)
    assert upload.status_code == 201, upload.text
    assert upload.json()["tracker_hits"][tracker.json()["id"]]["count"] == 1

    scorecard = client.post(f"{API}/scorecards", json={"name": "Discovery", "questions": [{"text": "Asked about budget?", "kind": "yes_no", "weight": 2}, {"text": "Clear next step?", "kind": "rating"}]}, headers=h)
    assert scorecard.status_code == 201, scorecard.text
    questions = scorecard.json()["questions"]
    review = client.post(
        f"{API}/conversations/{conversation['id']}/reviews",
        json={"scorecard_id": scorecard.json()["id"], "answers": {questions[0]["id"]: True, questions[1]["id"]: 4}, "comment": "Nice discovery"},
        headers=h,
    )
    assert review.status_code == 201, review.text
    assert 0 < review.json()["reviews"][0]["score"] <= 100

    listing = client.get(f"{API}/conversations", headers=h).json()
    assert listing["stats"]["count"] >= 2 and listing["trackers"][0]["mentions"] >= 2
    assert client.get(f"{API}/team-meetings", headers=h).status_code == 200
    assert client.delete(f"{API}/conversations/{upload.json()['id']}", headers=h).status_code == 204


def test_ai_context_feeds_drafts(client, ws):
    h = ws["headers"]
    settings(client, ws, "ai_context", {"company_overview": "AIVA helps HVAC firms sell.", "words_to_avoid": ["synergy"], "signature": "— Sam at AIVA"})
    contact = new_contact(client, ws)
    draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": contact["id"], "intent": "book a demo"}, headers=h)
    assert draft.status_code == 200, draft.text
    assert "— Sam at AIVA" in draft.json()["body"]
    assert "synergy" not in draft.json()["body"].lower()


def test_addon_cancel_and_resume_without_double_charge(client):
    other = signup(client, "Resume Co")
    h = other["headers"]
    assert client.post(f"{API}/billing/change-plan", json={"plan": "starter"}, headers=h).status_code == 200
    invoices_before = len(client.get(f"{API}/billing", headers=h).json()["invoices"])
    assert client.post(f"{API}/billing/addons/conversation_intelligence", json={}, headers=h).status_code == 200
    assert client.delete(f"{API}/billing/addons/conversation_intelligence", headers=h).status_code == 200
    # Still usable until the end of the paid month, and resuming doesn't bill again.
    assert client.get(f"{API}/conversations", headers=h).status_code == 200
    assert client.post(f"{API}/billing/addons/conversation_intelligence", json={}, headers=h).status_code == 200
    invoices_after = len(client.get(f"{API}/billing", headers=h).json()["invoices"])
    assert invoices_after == invoices_before + 1


def test_sharing_defaults_apply_to_new_items(client, ws):
    h = ws["headers"]
    settings(client, ws, "sharing", {"templates_shared": False, "reports_shared": False})
    template = client.post(f"{API}/email-templates", json={"name": "Private by default", "subject": "Hi", "body": "Hello"}, headers=h)
    assert template.status_code == 201, template.text
    assert template.json()["is_shared"] is False
    explicit = client.post(f"{API}/email-templates", json={"name": "Shared on purpose", "subject": "Hi", "body": "Hello", "is_shared": True}, headers=h)
    assert explicit.json()["is_shared"] is True
    settings(client, ws, "sharing", {"templates_shared": True, "reports_shared": False})


def test_bounce_setting_can_keep_people_in_sequence(client, ws):
    from app.services import outbound

    h = ws["headers"]
    settings(client, ws, "sending", {"send_days": [0, 1, 2, 3, 4, 5, 6], "window_start_hour": 0, "window_end_hour": 24, "min_seconds_between": 0})
    settings(client, ws, "sequences", {"stop_on_bounce": False})
    person = new_contact(client, ws, first_name="Soft")
    sequence = client.post(f"{API}/sequences", json={"name": "Soft bounce"}, headers=h).json()
    client.post(f"{API}/sequences/{sequence['id']}/steps", json={"kind": "auto_email", "subject": "Hello", "body": "Hi"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/steps", json={"kind": "auto_email", "delay_days": 2, "body": "Bump"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/status", json={"status": "active"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/enroll", json={"record_type": "contact", "record_ids": [person["id"]]}, headers=h)
    run_service(outbound.process_due)

    def bounce(db):
        message = SimpleNamespace(from_address="postmaster@mail.example", subject="Undeliverable: Hello", text=f"Delivery to {person['email']} failed temporarily.", date=None)
        return outbound.handle_inbound(db, uuid.UUID(ws["org"]), message)

    assert run_service(bounce) == "bounce"
    enrollment = client.get(f"{API}/sequences/{sequence['id']}/enrollments", headers=h).json()["items"][0]
    assert enrollment["status"] == "active"
    assert client.get(f"{API}/sequences/{sequence['id']}", headers=h).json()["stats"]["bounced"] == 1
    settings(client, ws, "sequences", {"stop_on_bounce": True})


def test_conversation_links_lead_and_reprocess_keeps_decisions(client, ws):
    h = ws["headers"]
    account = client.post(f"{API}/accounts", json={"name": "Infer Co"}, headers=h).json()
    deal = client.post(f"{API}/opportunities", json={"name": "Infer deal", "account_id": account["id"], "amount": 500}, headers=h).json()
    lead = client.post(f"{API}/leads", json={"first_name": "Lena", "last_name": "Lead", "email": f"lena-{uuid.uuid4().hex[:5]}@lead.example"}, headers=h).json()
    client.post(f"{API}/field-prompts", json={"name": "Budget again", "object_type": "opportunity", "field_key": "amount", "prompt": "What budget did they mention?", "keywords": ["budget"]}, headers=h)
    created = client.post(f"{API}/conversations", json={"title": "Infer call", "transcript_text": VTT, "opportunity_id": deal["id"], "lead_id": lead["id"]}, headers=h).json()
    assert created["account_id"] == account["id"]
    assert created["lead_name"] == "Lena Lead"
    suggestion = next(s for s in created["field_suggestions"] if s["field_key"] == "amount")
    client.post(f"{API}/conversations/{created['id']}/suggestions/{suggestion['id']}", json={"action": "dismiss"}, headers=h)
    again = client.post(f"{API}/conversations/{created['id']}/reprocess", headers=h).json()
    statuses = [s["status"] for s in again["field_suggestions"] if s["prompt_id"] == suggestion["prompt_id"]]
    assert statuses == ["dismissed"]


def test_forwarded_for_cannot_bypass_ip_allowlist(client):
    other = signup(client, "Allowlist Co")
    h = other["headers"]
    run_sql(
        "insert into workspace_settings (id, organization_id, namespace, data, secrets, created_at, updated_at) "
        "values (gen_random_uuid(), :org, 'security', '{\"ip_allowlist\": [\"203.0.113.0/24\"]}'::jsonb, '{}'::jsonb, now(), now()) "
        "on conflict (organization_id, namespace) do update set data = excluded.data",
        org=other["org"],
    )
    from app.api.dependencies.auth import invalidate_security_cache

    invalidate_security_cache(uuid.UUID(other["org"]))
    assert client.get(f"{API}/accounts", headers=h).status_code == 403
    assert client.get(f"{API}/accounts", headers={**h, "X-Forwarded-For": "203.0.113.99"}).status_code == 403


def test_manual_email_never_goes_to_suppressed_people(client, ws):
    h = ws["headers"]
    person = new_contact(client, ws, first_name="Quiet")
    sequence = client.post(f"{API}/sequences", json={"name": "Manual only"}, headers=h).json()
    client.post(f"{API}/sequences/{sequence['id']}/steps", json={"kind": "manual_email", "subject": "Hi", "body": "Hello"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/status", json={"status": "active"}, headers=h)
    client.post(f"{API}/sequences/{sequence['id']}/enroll", json={"record_type": "contact", "record_ids": [person["id"]]}, headers=h)
    enrollment = client.get(f"{API}/sequences/{sequence['id']}/enrollments", headers=h).json()["items"][0]
    client.post(f"{API}/suppression", json={"values": [person["email"]]}, headers=h)
    sent = client.post(f"{API}/sequence-enrollments/{enrollment['id']}/send", json={}, headers=h)
    assert sent.status_code == 409, sent.text
    after = client.get(f"{API}/sequences/{sequence['id']}/enrollments", headers=h).json()["items"][0]
    assert after["status"] == "unsubscribed"


def test_mcp_only_sees_people_in_this_workspace(client, ws):
    h = ws["headers"]
    stranger = signup(client, "Elsewhere Co")
    settings(client, ws, "mcp", {"allow_write": True})
    key = client.post(f"{API}/api-keys", json={"name": "Agent", "scope": "read_write"}, headers=h).json()
    mcp = {"Authorization": f"Bearer {key['key']}"}
    assign = client.post(
        f"{API}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "create_task", "arguments": {"title": "Call back", "assign_to_email": stranger["email"]}}},
        headers=mcp,
    ).json()
    assert assign["result"]["isError"] is True
    deals = client.post(
        f"{API}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_open_deals", "arguments": {"owner_email": stranger["email"]}}}, headers=mcp
    ).json()
    assert deals["result"]["isError"] is True
    bad = client.post(f"{API}/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_open_deals", "arguments": {"limit": "abc"}}}, headers=mcp)
    assert bad.status_code == 200 and bad.json()["result"]["isError"] is True
    settings(client, ws, "mcp", {"allow_write": False})
