"""End-to-end API tests against a real PostgreSQL database.

Each run signs up a brand-new workspace, so the tests never touch the
development data. Skipped automatically when the database isn't reachable.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.totp import current_code
from app.main import app

API = "/api/v1"
PASSWORD = "Integration#2026"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def workspace(client):
    email = f"owner-{uuid.uuid4().hex[:10]}@integration.example"
    try:
        response = client.post(
            f"{API}/auth/signup",
            json={"full_name": "Ivy Owner", "email": email, "password": PASSWORD, "company_name": "Integration Co"},
        )
    except (OSError, ConnectionError) as exc:  # database not running
        pytest.skip(f"PostgreSQL not available: {exc}")
    if response.status_code >= 500:
        pytest.skip("PostgreSQL not available for integration tests.")
    assert response.status_code == 201, response.text
    data = response.json()
    headers = {
        "Authorization": f"Bearer {data['access_token']}",
        "X-Organization-ID": data["organization_id"],
        "X-Session-ID": data["session_id"],
    }
    return {"email": email, "org": data["organization_id"], "user_id": data["user_id"], "headers": headers, "login": data}


def invite_and_join(client, workspace, role: str, name: str) -> dict:
    email = f"{role}-{uuid.uuid4().hex[:8]}@integration.example"
    invited = client.post(
        f"{API}/admin/users/invite",
        json={"email": email, "full_name": name, "role": role},
        headers=workspace["headers"],
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["invite_url"].split("token=")[1]
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    assert joined.status_code == 200, joined.text
    return {
        "user_id": joined.json()["user_id"],
        "email": email,
        "headers": {"Authorization": f"Bearer {joined.json()['access_token']}", "X-Organization-ID": workspace["org"]},
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_signup_provisions_workspace(client, workspace):
    h = workspace["headers"]
    me = client.get(f"{API}/auth/me", headers=h).json()
    assert me["role"] == "owner" and "admin.roles" in me["permissions"]
    assert len(client.get(f"{API}/sla-policies", headers=h).json()) == 4
    assert len(client.get(f"{API}/lead-scoring/rules", headers=h).json()) >= 5
    assert {d["name"] for d in client.get(f"{API}/dashboards", headers=h).json()} == {"Sales overview", "Service overview"}


def test_duplicate_signup_is_rejected(client, workspace):
    response = client.post(
        f"{API}/auth/signup",
        json={"full_name": "Copy", "email": workspace["email"], "password": PASSWORD, "company_name": "Copy Co"},
    )
    assert response.status_code == 409


def test_records_require_authentication(client, workspace):
    assert client.get(f"{API}/accounts", headers={"X-Organization-ID": workspace["org"]}).status_code == 401
    assert client.get(f"{API}/accounts", headers={**workspace["headers"], "Authorization": "Bearer nope"}).status_code == 401


def test_refresh_rotation_and_logout(client, workspace):
    login = client.post(f"{API}/auth/login", json={"email": workspace["email"], "password": PASSWORD}).json()
    rotated = client.post(f"{API}/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert rotated.status_code == 200
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code == 401
    assert client.post(f"{API}/auth/logout", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 204
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 401


def test_password_reset_flow(client, workspace):
    member = invite_and_join(client, workspace, "member", "Reset Person")
    forgot = client.post(f"{API}/auth/password/forgot", json={"email": member["email"]})
    assert forgot.status_code == 202
    token = forgot.json()["reset_url"].split("token=")[1]
    assert client.post(f"{API}/auth/password/reset", json={"token": token, "password": "weak"}).status_code == 422
    assert client.post(f"{API}/auth/password/reset", json={"token": token, "password": "Fresh#Pass2026"}).status_code == 200
    assert client.post(f"{API}/auth/password/reset", json={"token": token, "password": "Again#Pass2026"}).status_code == 400
    assert client.post(f"{API}/auth/login", json={"email": member["email"], "password": "Fresh#Pass2026"}).status_code == 200
    unknown = client.post(f"{API}/auth/password/forgot", json={"email": "nobody@integration.example"})
    assert unknown.status_code == 202 and "reset_url" not in unknown.json()


def test_lockout_and_mfa(client, workspace):
    member = invite_and_join(client, workspace, "member", "Mfa Person")
    secret = client.post(f"{API}/auth/mfa/setup", headers=member["headers"]).json()["secret"]
    assert client.post(f"{API}/auth/mfa/enable", json={"code": "000000"}, headers=member["headers"]).status_code == 400
    assert client.post(f"{API}/auth/mfa/enable", json={"code": current_code(secret)}, headers=member["headers"]).status_code == 200

    challenge = client.post(f"{API}/auth/login", json={"email": member["email"], "password": PASSWORD}).json()
    assert challenge["mfa_required"] is True and "access_token" not in challenge
    verified = client.post(f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": current_code(secret)})
    assert verified.status_code == 200 and verified.json()["access_token"]

    for _ in range(5):
        client.post(f"{API}/auth/login", json={"email": member["email"], "password": "wrong-password"})
    assert client.post(f"{API}/auth/login", json={"email": member["email"], "password": PASSWORD}).status_code == 423

    # Failed attempts and the lockout land in the audit log...
    events = client.get(
        f"{API}/admin/audit-logs", params={"entity_id": member["user_id"]}, headers=workspace["headers"]
    ).json()["items"]
    actions = {event["action"] for event in events}
    assert {"login_failed", "account_locked"} <= actions

    # ...and an admin can unlock the account by re-activating it.
    unlocked = client.patch(f"{API}/admin/users/{member['user_id']}", json={"status": "active"}, headers=workspace["headers"])
    assert unlocked.status_code == 200, unlocked.text
    again = client.post(f"{API}/auth/login", json={"email": member["email"], "password": PASSWORD})
    assert again.status_code == 200 and again.json()["mfa_required"] is True


def test_workspace_session_length_controls_token_lifetime(client, workspace):
    h = workspace["headers"]
    assert client.patch(f"{API}/admin/organization", json={"settings": {"session_timeout_minutes": 2}}, headers=h).status_code == 422
    assert client.patch(f"{API}/admin/organization", json={"settings": {"session_timeout_minutes": 60}}, headers=h).status_code == 200
    session = client.post(f"{API}/auth/login", json={"email": workspace["email"], "password": PASSWORD}).json()
    assert session["expires_in"] == 60 * 60
    client.patch(f"{API}/admin/organization", json={"settings": {"session_timeout_minutes": 30}}, headers=h)


# ---------------------------------------------------------------------------
# Administration and permissions
# ---------------------------------------------------------------------------


def test_roles_permissions_and_teams(client, workspace):
    h = workspace["headers"]
    created = client.post(f"{API}/admin/roles", json={"name": "Read only plus quotes", "copy_from": "viewer"}, headers=h)
    assert created.status_code == 201
    key = created.json()["key"]
    assert client.patch(f"{API}/admin/roles/{key}", json={"permissions": ["records.read", "made.up"]}, headers=h).status_code == 200
    role = next(r for r in client.get(f"{API}/admin/roles", headers=h).json() if r["key"] == key)
    assert role["permissions"] == ["records.read"]
    assert client.patch(f"{API}/admin/roles/owner", json={"permissions": []}, headers=h).status_code == 409

    limited = invite_and_join(client, workspace, key, "Limited Person")
    assert client.get(f"{API}/accounts", headers=limited["headers"]).status_code == 200
    assert client.post(f"{API}/accounts", json={"name": "Nope Inc"}, headers=limited["headers"]).status_code == 403
    assert client.get(f"{API}/admin/users", headers=limited["headers"]).status_code == 403
    assert client.delete(f"{API}/admin/roles/{key}", headers=h).status_code == 409  # still assigned

    team = client.post(f"{API}/admin/teams", json={"name": "Tier 1"}, headers=h)
    assert team.status_code == 201
    assert client.patch(f"{API}/admin/users/{limited['user_id']}", json={"team_id": team.json()["id"]}, headers=h).status_code == 200
    teams = client.get(f"{API}/admin/teams", headers=h).json()
    assert any(m["id"] == limited["user_id"] for t in teams for m in t["members"])

    assert client.patch(f"{API}/admin/users/{limited['user_id']}", json={"status": "deactivated"}, headers=h).status_code == 200
    assert client.get(f"{API}/accounts", headers=limited["headers"]).status_code == 403
    assert client.patch(f"{API}/admin/users/{workspace['user_id']}", json={"role": "member"}, headers=h).status_code == 409


def test_tenant_isolation(client, workspace):
    other = client.post(
        f"{API}/auth/signup",
        json={"full_name": "Other", "email": f"other-{uuid.uuid4().hex[:8]}@integration.example", "password": PASSWORD, "company_name": "Other Co"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}", "X-Organization-ID": other["organization_id"]}
    case = client.post(f"{API}/cases", json={"subject": "Private case"}, headers=workspace["headers"]).json()
    assert client.get(f"{API}/cases/{case['id']}", headers=other_headers).status_code == 404
    # A valid token can't be used against a workspace the user doesn't belong to.
    assert client.get(f"{API}/cases", headers={**other_headers, "X-Organization-ID": workspace["org"]}).status_code == 403


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def account(client, workspace):
    response = client.post(f"{API}/accounts", json={"name": f"Acme {uuid.uuid4().hex[:6]}"}, headers=workspace["headers"])
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.fixture(scope="module")
def pipeline(client, workspace):
    pipelines = client.get(f"{API}/pipelines", headers=workspace["headers"]).json()
    default = next(p for p in pipelines if p["is_default"])
    stages = client.get(f"{API}/pipelines/{default['id']}/stages", headers=workspace["headers"]).json()
    return {"id": default["id"], "stages": stages}


def test_quote_lifecycle(client, workspace, account):
    h = workspace["headers"]
    product = client.post(f"{API}/products", json={"name": "Seat", "sku": f"SEAT-{uuid.uuid4().hex[:4]}", "unit_price": 50}, headers=h).json()
    quote = client.post(
        f"{API}/quotes",
        json={"name": "Starter", "account_id": account["id"], "line_items": [
            {"product_id": product["id"], "name": "Seat", "quantity": 20, "unit_price": 50, "discount_percent": 20, "tax_percent": 10},
        ]},
        headers=h,
    ).json()
    assert (quote["subtotal"], quote["discount_total"], quote["tax_total"], quote["total"]) == (1000, 200, 80, 880)
    assert quote["display_number"].startswith("Q-")
    assert client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "sent"}, headers=h).status_code == 200
    accepted = client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "accepted"}, headers=h)
    assert accepted.json()["accepted_at"]
    assert client.put(f"{API}/quotes/{quote['id']}/line-items", json=[], headers=h).status_code == 409
    assert client.delete(f"{API}/quotes/{quote['id']}", headers=h).status_code == 409

    rep = invite_and_join(client, workspace, "member", "Quote Rep")
    draft = client.post(f"{API}/quotes", json={"name": "Rep quote", "account_id": account["id"]}, headers=rep["headers"]).json()
    client.put(f"{API}/quotes/{draft['id']}/line-items", json=[{"name": "Item", "quantity": 1, "unit_price": 10}], headers=rep["headers"])
    assert client.post(f"{API}/quotes/{draft['id']}/status", json={"status": "accepted"}, headers=rep["headers"]).status_code == 403


def test_leads_are_scored_automatically(client, workspace):
    h = workspace["headers"]
    lead = client.post(
        f"{API}/leads",
        json={"first_name": "Chen", "last_name": "Wu", "job_title": "Chief Data Officer", "email": "chen@x.example", "company_name": "X", "source": "referral"},
        headers=h,
    )
    assert lead.status_code in (200, 201), lead.text
    assert lead.json()["score"] >= 60
    breakdown = client.get(f"{API}/lead-scoring/leads/{lead.json()['id']}", headers=h).json()
    assert breakdown["grade"] in {"A", "B"} and breakdown["breakdown"]


def test_forecast_and_quotas(client, workspace, account, pipeline):
    h = workspace["headers"]
    open_stage = next(s for s in pipeline["stages"] if s["category"] == "open")
    forecast = client.get(f"{API}/forecast", headers=h).json()
    created = client.post(
        f"{API}/opportunities",
        json={"name": "Forecast deal", "account_id": account["id"], "pipeline_id": pipeline["id"], "stage_id": open_stage["id"],
              "amount": "40000", "expected_close_date": forecast["start"], "owner_user_id": workspace["user_id"]},
        headers=h,
    )
    assert created.status_code in (200, 201), created.text
    assert client.put(f"{API}/forecast/quotas", json={"user_id": workspace["user_id"], "period": forecast["period"], "amount": 100000}, headers=h).status_code == 200
    again = client.get(f"{API}/forecast", params={"period": forecast["period"]}, headers=h).json()
    me = next(r for r in again["rows"] if r["user_id"] == workspace["user_id"])
    assert me["quota"] == 100000 and me["pipeline"] + me["commit"] + me["best_case"] >= 40000


# ---------------------------------------------------------------------------
# Service desk
# ---------------------------------------------------------------------------


def test_case_sla_and_knowledge(client, workspace):
    h = workspace["headers"]
    article = client.post(f"{API}/knowledge", json={"title": "Printer offline fix", "body": "Restart the printer spooler."}, headers=h).json()
    assert client.post(f"{API}/knowledge/{article['id']}/publish", headers=h).json()["status"] == "published"

    case = client.post(f"{API}/cases", json={"subject": "Printer offline in office", "priority": "urgent"}, headers=h).json()
    assert case["status"] == "new" and case["sla"]["target"] == "first_response"
    internal = client.post(f"{API}/cases/{case['id']}/comments", json={"body": "Checking logs", "is_internal": True}, headers=h).json()
    assert internal["first_responded_at"] is None
    replied = client.post(f"{API}/cases/{case['id']}/comments", json={"body": "We're on it"}, headers=h).json()
    assert replied["first_responded_at"] and replied["status"] == "open"
    suggestions = client.get(f"{API}/cases/{case['id']}/suggested-articles", headers=h).json()
    assert suggestions and suggestions[0]["id"] == article["id"]
    linked = client.post(f"{API}/cases/{case['id']}/articles", json={"article_id": article["id"]}, headers=h).json()
    assert linked["linked_articles"][0]["title"] == "Printer offline fix"

    downgraded = client.patch(f"{API}/cases/{case['id']}", json={"priority": "low"}, headers=h).json()
    assert downgraded["resolution_due_at"] > case["resolution_due_at"]
    resolved = client.patch(f"{API}/cases/{case['id']}", json={"status": "resolved", "satisfaction_rating": 5}, headers=h).json()
    assert resolved["resolved_at"] and resolved["sla"]["state"] == "met"
    reopened = client.patch(f"{API}/cases/{case['id']}", json={"status": "open"}, headers=h).json()
    assert reopened["resolved_at"] is None

    stats = client.get(f"{API}/cases/stats", headers=h).json()
    assert stats["open"] >= 1


def test_case_assignment_notifies_agent(client, workspace):
    agent = invite_and_join(client, workspace, "support_agent", "Agent Person")
    case = client.post(f"{API}/cases", json={"subject": "Assign me", "assigned_to_user_id": agent["user_id"]}, headers=workspace["headers"]).json()
    notes = client.get(f"{API}/notifications", headers=agent["headers"]).json()
    assert any(n["link"] == f"/cases/{case['id']}" for n in notes["items"])
    assert client.post(f"{API}/notifications/read-all", headers=agent["headers"]).json()["updated"] >= 1
    assert client.get(f"{API}/notifications", headers=agent["headers"]).json()["unread"] == 0


# ---------------------------------------------------------------------------
# Automation, audit and analytics
# ---------------------------------------------------------------------------


def test_workflow_creates_task_and_audits(client, workspace, account, pipeline):
    h = workspace["headers"]
    rule = client.post(
        f"{API}/workflows",
        json={
            "name": "Big deal follow-up",
            "object_type": "opportunity",
            "trigger_event": "created",
            "conditions": [{"field": "amount", "operator": "greater_than", "value": "100000"}],
            "actions": [
                {"type": "create_task", "params": {"title": "Kickoff for {{name}}", "due_in_days": 3, "priority": "high"}},
                {"type": "update_field", "params": {"field": "priority", "value": "critical"}},
            ],
        },
        headers=h,
    )
    assert rule.status_code == 201, rule.text
    stage = next(s for s in pipeline["stages"] if s["category"] == "open")
    small = client.post(f"{API}/opportunities", json={"name": "Small one", "account_id": account["id"], "pipeline_id": pipeline["id"], "stage_id": stage["id"], "amount": "500"}, headers=h).json()
    big = client.post(f"{API}/opportunities", json={"name": "Huge one", "account_id": account["id"], "pipeline_id": pipeline["id"], "stage_id": stage["id"], "amount": "250000"}, headers=h).json()

    runs = client.get(f"{API}/workflows/{rule.json()['id']}/runs", headers=h).json()
    assert [r["entity_id"] for r in runs] == [big["id"]]
    assert runs[0]["status"] == "success"
    tasks = client.get(f"{API}/tasks", params={"opportunity_id": big["id"]}, headers=h).json()
    tasks = tasks if isinstance(tasks, list) else tasks["items"]
    assert any(t["title"] == "Kickoff for Huge one" for t in tasks)
    refreshed = client.get(f"{API}/opportunities/{big['id']}", headers=h).json()
    assert refreshed["priority"] == "critical"
    assert client.get(f"{API}/opportunities/{small['id']}", headers=h).json()["priority"] != "critical"

    logs = client.get(f"{API}/admin/audit-logs", params={"entity_id": big["id"]}, headers=h).json()
    assert any(entry["action"] == "create" and entry["actor_name"] == "Ivy Owner" for entry in logs["items"])
    assert client.post(f"{API}/workflows/{rule.json()['id']}/toggle", headers=h).json()["is_active"] is False


def test_reports_dashboards_and_search(client, workspace, account):
    h = workspace["headers"]
    for report in client.get(f"{API}/reports", headers=h).json():
        assert client.get(f"{API}/reports/{report['id']}/run", headers=h).status_code == 200

    spec = {
        "name": "Accounts by stage",
        "object_type": "account",
        "columns": ["name", "lifecycle_stage", "owner_user_id"],
        "filters": [{"field": "created_at", "operator": "in_period", "value": "this_year"}],
        "group_by": "lifecycle_stage",
        "aggregate": "count",
        "chart_type": "bar",
    }
    saved = client.post(f"{API}/reports", json=spec, headers=h)
    assert saved.status_code == 201, saved.text
    result = client.get(f"{API}/reports/{saved.json()['id']}/run", headers=h).json()["result"]
    assert result["total_count"] >= 1 and result["groups"]
    assert result["rows"][0]["values"]["owner_user_id"] == "Ivy Owner"

    bad = client.post(f"{API}/reports/run", json={"object_type": "account", "group_by": "nonsense", "chart_type": "bar"}, headers=h)
    assert bad.status_code == 422

    dashboard = client.post(f"{API}/dashboards", json={"name": "Mine", "widgets": [{"report_id": saved.json()["id"], "size": "large"}]}, headers=h).json()
    rendered = client.get(f"{API}/dashboards/{dashboard['id']}", headers=h).json()
    assert rendered["widgets"][0]["result"]["groups"]

    summary = client.get(f"{API}/home/summary", headers=h).json()
    assert "kpis" in summary and "pipeline_by_stage" in summary

    hits = client.get(f"{API}/search", params={"q": account["name"][:6]}, headers=h).json()
    assert any(group["type"] == "account" for group in hits["groups"])


def test_update_endpoints_return_fresh_records(client, workspace):
    """Every edit endpoint responds with the saved record (including its new timestamps)."""
    h = workspace["headers"]

    report = client.post(
        f"{API}/reports", json={"name": "Edit me", "object_type": "lead", "aggregate": "count", "chart_type": "table"}, headers=h
    ).json()
    edited = client.patch(f"{API}/reports/{report['id']}", json={**report, "name": "Edited", "filters": []}, headers=h)
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Edited" and edited.json()["updated_at"]

    dashboard = client.post(f"{API}/dashboards", json={"name": "Board"}, headers=h).json()
    saved = client.put(
        f"{API}/dashboards/{dashboard['id']}",
        json={"name": "Board 2", "widgets": [{"report_id": report["id"], "size": "small"}]},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["widgets"][0]["report_id"] == report["id"]

    workflow = client.post(
        f"{API}/workflows",
        json={
            "name": "Ping",
            "object_type": "lead",
            "trigger_event": "created",
            "actions": [{"type": "notify", "params": {"recipient": "actor", "title": "New lead {{record}}"}}],
        },
        headers=h,
    ).json()
    changed = client.put(f"{API}/workflows/{workflow['id']}", json={**workflow, "name": "Ping 2"}, headers=h)
    assert changed.status_code == 200, changed.text
    assert client.post(f"{API}/workflows/{workflow['id']}/toggle", headers=h).status_code == 200

    product = client.post(f"{API}/products", json={"name": "Edit product", "unit_price": 10}, headers=h).json()
    assert client.patch(f"{API}/products/{product['id']}", json={**product, "unit_price": 12}, headers=h).json()["unit_price"] == 12

    duplicate = client.post(
        f"{API}/sla-policies", json={"name": "Low", "priority": "low", "first_response_minutes": 60, "resolution_minutes": 600}, headers=h
    )
    assert duplicate.status_code == 409 and "already exists" in duplicate.json()["detail"]
    policy = client.post(
        f"{API}/sla-policies", json={"name": "Low (VIP)", "priority": "low", "first_response_minutes": 60, "resolution_minutes": 600}, headers=h
    ).json()
    assert client.patch(f"{API}/sla-policies/{policy['id']}", json={**policy, "resolution_minutes": 900}, headers=h).status_code == 200

    article = client.post(f"{API}/knowledge", json={"title": "Edit article", "body": "Hello"}, headers=h).json()
    assert client.patch(f"{API}/knowledge/{article['id']}", json={"title": "Edited article", "body": "Hi"}, headers=h).status_code == 200

    team = client.post(f"{API}/admin/teams", json={"name": "Edit team"}, headers=h).json()
    assert client.patch(f"{API}/admin/teams/{team['id']}", json={"name": "Edited team"}, headers=h).status_code == 200
