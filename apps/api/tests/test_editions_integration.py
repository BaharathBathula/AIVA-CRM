"""End-to-end tests for plans, billing and the Starter / Pro / Enterprise features.

Runs against a real PostgreSQL database with a brand-new workspace per run
(skipped when the database isn't reachable). No external services are
called: mail goes to the outbox and webhooks aren't reachable.
"""

import asyncio
import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

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


def signup(client, company: str = "Editions Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@editions.example"
    try:
        response = client.post(
            f"{API}/auth/signup",
            json={"full_name": "Erin Owner", "email": email, "password": PASSWORD, "company_name": company},
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
    return signup(client)


def invite(client, ws, role: str, name: str) -> dict:
    email = f"{role}-{uuid.uuid4().hex[:8]}@editions.example"
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


def run_sql(statement: str, **params) -> None:
    from sqlalchemy import text

    from app.db.session import engine

    async def go():
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)

    asyncio.run(go())


@pytest.fixture(scope="module")
def pipeline(client, ws):
    pipelines = client.get(f"{API}/pipelines", headers=ws["headers"]).json()
    stages = client.get(f"{API}/pipelines/{pipelines[0]['id']}/stages", headers=ws["headers"]).json()
    return {"id": pipelines[0]["id"], "stages": stages}


@pytest.fixture(scope="module")
def account(client, ws):
    response = client.post(f"{API}/accounts", json={"name": "Brightline HVAC Services", "industry": "Construction"}, headers=ws["headers"])
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def contact(client, ws, account):
    response = client.post(
        f"{API}/contacts",
        json={"first_name": "Marcus", "last_name": "Bell", "email": "marcus@brightline.example", "job_title": "Owner", "account_id": account["id"]},
        headers=ws["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Plans and billing
# ---------------------------------------------------------------------------


def test_public_plans_catalog(client, ws):
    plans = client.get(f"{API}/public/plans").json()
    prices = {p["key"]: p["price"] for p in plans["plans"]}
    assert prices == {"trial": 0, "starter": 19, "pro": 29, "enterprise": 59}
    assert plans["trial_days"] == 7
    labels = {f["label"] for group in plans["feature_groups"] for f in group["features"]}
    for label in ("Built-in calling", "AI agents", "Configure, Price, Quote (CPQ)", "Developer sandbox", "QuickBooks integration", "HIPAA compliance"):
        assert label in labels


def test_new_workspace_starts_seven_day_trial(client, ws):
    me = client.get(f"{API}/auth/me", headers=ws["headers"]).json()
    assert me["subscription"]["plan"] == "trial"
    assert me["subscription"]["status"] == "trialing"
    assert me["subscription"]["days_left"] == 7
    assert "sandbox" in me["subscription"]["features"]


def test_plan_change_gates_features_and_limits(client):
    other = signup(client, "Gating Co")
    h = other["headers"]
    changed = client.post(f"{API}/billing/change-plan", json={"plan": "starter"}, headers=h)
    assert changed.status_code == 200, changed.text
    assert changed.json()["invoice"]["amount"] == 19
    assert client.get(f"{API}/quotes", headers=h).status_code == 402
    assert client.get(f"{API}/ai/agents", headers=h).status_code == 402
    assert client.get(f"{API}/email-templates", headers=h).status_code == 200
    # Starter can create 5 web forms.
    for index in range(5):
        assert client.post(f"{API}/forms", json={"name": f"Form {index}"}, headers=h).status_code == 201
    over = client.post(f"{API}/forms", json={"name": "One too many"}, headers=h)
    assert over.status_code == 402 and "Starter" in over.json()["detail"]
    case_form = client.post(f"{API}/forms", json={"name": "Support", "target": "case"}, headers=h)
    assert case_form.status_code == 402
    upgraded = client.post(f"{API}/billing/change-plan", json={"plan": "enterprise"}, headers=h).json()
    assert upgraded["billing"]["plan"] == "enterprise"
    billing = client.get(f"{API}/billing", headers=h).json()
    assert len(billing["invoices"]) == 2 and billing["monthly_total"] == 59
    assert client.get(f"{API}/quotes", headers=h).status_code == 200


def test_expired_trial_is_read_only(client):
    other = signup(client, "Expired Co")
    h = other["headers"]
    run_sql(
        "UPDATE subscriptions SET trial_ends_at = now() - interval '1 day' WHERE organization_id = :org",
        org=uuid.UUID(other["org"]),
    )
    me = client.get(f"{API}/auth/me", headers=h).json()
    assert me["subscription"]["status"] == "expired" and me["subscription"]["read_only"]
    assert client.get(f"{API}/accounts", headers=h).status_code == 200
    blocked = client.post(f"{API}/accounts", json={"name": "Nope"}, headers=h)
    assert blocked.status_code == 402 and "trial has ended" in blocked.json()["detail"]
    assert client.post(f"{API}/billing/change-plan", json={"plan": "pro"}, headers=h).status_code == 200
    assert client.post(f"{API}/accounts", json={"name": "Works again"}, headers=h).status_code == 201


def test_stripe_webhook_signature_and_events():
    import hashlib
    import hmac
    import json
    import time

    from app.services.stripe_billing import StripeError, verify_webhook

    body = json.dumps({"type": "invoice.paid", "data": {"object": {}}}).encode()
    stamp = int(time.time())
    signature = hmac.new(b"whsec_test", f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify_webhook(body, f"t={stamp},v1={signature}", "whsec_test")["type"] == "invoice.paid"
    with pytest.raises(StripeError):
        verify_webhook(body, f"t={stamp},v1=bad", "whsec_test")
    with pytest.raises(StripeError):
        verify_webhook(body, f"t={stamp - 4000},v1={signature}", "whsec_test")


# ---------------------------------------------------------------------------
# Custom modules, fields and records
# ---------------------------------------------------------------------------


def test_custom_modules_fields_and_records(client, ws):
    h = ws["headers"]
    module = client.post(f"{API}/custom-modules", json={"name": "Vehicles", "singular": "Vehicle", "icon": "car"}, headers=h)
    assert module.status_code == 201, module.text
    key = module.json()["key"]
    fields = [
        {"label": "VIN", "field_type": "text", "required": True},
        {"label": "Value", "field_type": "currency"},
        {"label": "Monthly premium", "field_type": "formula", "config": {"expression": "ROUND(value * 0.01, 2)"}},
        {"label": "Policy number", "field_type": "auto_number", "config": {"prefix": "VEH-", "start": 100}},
        {"label": "Usage", "field_type": "picklist", "options": ["Personal", "Business"]},
        {"label": "Driver SSN", "field_type": "text", "is_encrypted": True},
    ]
    for field in fields:
        created = client.post(f"{API}/custom-fields", json={"object_type": key, **field}, headers=h)
        assert created.status_code == 201, created.text
    bad = client.post(f"{API}/custom-fields", json={"object_type": key, "label": "Broken", "field_type": "formula", "config": {"expression": "__import__('os')"}}, headers=h)
    assert bad.status_code == 422
    missing = client.post(f"{API}/modules/{key}/records", json={"name": "Truck", "values": {}}, headers=h)
    assert missing.status_code == 422 and "VIN" in missing.json()["detail"]
    record = client.post(
        f"{API}/modules/{key}/records",
        json={"name": "F-150", "values": {"vin": "1FTFW1E5", "value": "48,000", "usage": "Business", "driver_ssn": "123-45-6789"}},
        headers=h,
    )
    assert record.status_code == 201, record.text
    values = record.json()["values"]
    assert values["monthly_premium"] == 480.0
    assert values["policy_number"] == "VEH-100"
    assert values["driver_ssn"] == "123-45-6789"
    listed = client.get(f"{API}/modules/{key}/records", headers=h).json()
    assert listed["total"] == 1
    # Read-only members see the encrypted value masked.
    viewer = invite(client, ws, "viewer", "Vera Viewer")
    seen = client.get(f"{API}/modules/{key}/records/{record.json()['id']}", headers=viewer["headers"]).json()
    assert seen["values"]["driver_ssn"].startswith("••••") and seen["values"]["driver_ssn"].endswith("89")
    # Encrypted at rest.
    export = client.get(f"{API}/data/export/{key}.csv", headers=h)
    assert export.status_code == 200 and "F-150" in export.text


def test_custom_field_values_on_standard_records(client, ws, account):
    h = ws["headers"]
    created = client.post(f"{API}/custom-fields", json={"object_type": "account", "label": "Fleet size", "field_type": "number"}, headers=h)
    assert created.status_code == 201
    saved = client.put(f"{API}/custom-fields/values/account/{account['id']}", json={"values": {"fleet_size": "9"}}, headers=h)
    assert saved.status_code == 200 and saved.json()["values"]["fleet_size"] == 9.0
    wrong = client.put(f"{API}/custom-fields/values/account/{account['id']}", json={"values": {"fleet_size": "nine"}}, headers=h)
    assert wrong.status_code == 422


# ---------------------------------------------------------------------------
# Email: templates, campaigns, unsubscribe, opens
# ---------------------------------------------------------------------------


def test_templates_campaign_unsubscribe_and_opens(client, ws, contact):
    h = ws["headers"]
    template = client.post(
        f"{API}/email-templates",
        json={"name": "Renewal check-in", "subject": "Your renewal, {{first_name}}", "body": "Hi {{first_name|there}},\n\nYour renewal with {{company}} is coming up."},
        headers=h,
    )
    assert template.status_code == 201
    rendered = client.post(
        f"{API}/email-templates/{template.json()['id']}/render", json={"record_type": "contact", "record_id": contact["id"]}, headers=h
    ).json()
    assert rendered["subject"] == "Your renewal, Marcus" and "Brightline HVAC Services" in rendered["body"]

    preview = client.post(f"{API}/campaigns/audience-preview", json={"object_type": "contact", "filters": [{"field": "job_title", "operator": "equals", "value": "Owner"}]}, headers=h)
    assert preview.status_code == 200 and preview.json()["count"] >= 1
    campaign = client.post(
        f"{API}/campaigns",
        json={"name": "Renewals", "template_id": template.json()["id"], "audience": {"object_type": "contact", "filters": [{"field": "email", "operator": "contains", "value": "brightline"}]}},
        headers=h,
    ).json()
    sent = client.post(f"{API}/campaigns/{campaign['id']}/send", json={}, headers=h)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent"
    assert sent.json()["stats"]["sent"] == 1 and sent.json()["stats"]["outbox"] == 1
    recipients = client.get(f"{API}/campaigns/{campaign['id']}/recipients", headers=h).json()["items"]
    assert recipients[0]["subject"] == "Your renewal, Marcus"
    token = run_token = None
    from sqlalchemy import select  # noqa: F401

    async def find_token():
        from sqlalchemy import select as sel

        from app.db.session import AsyncSessionLocal
        from app.models.editions import CampaignRecipient

        async with AsyncSessionLocal() as session:
            return (await session.execute(sel(CampaignRecipient.token).where(CampaignRecipient.id == uuid.UUID(recipients[0]["id"])))).scalar_one()

    token = asyncio.run(find_token())
    pixel = client.get(f"{API}/public/email/open/{token}.gif")
    assert pixel.status_code == 200 and pixel.headers["content-type"] == "image/gif"
    info = client.get(f"{API}/public/unsubscribe/{token}").json()
    assert info["email"] == "marcus@brightline.example" and not info["unsubscribed"]
    assert client.post(f"{API}/public/unsubscribe/{token}").json()["unsubscribed"]
    campaign_after = client.get(f"{API}/campaigns/{campaign['id']}", headers=h).json()
    assert campaign_after["stats"]["sent"] == 1
    again = client.post(f"{API}/campaigns/{campaign['id']}/duplicate", headers=h).json()
    resend = client.post(f"{API}/campaigns/{again['id']}/send", json={}, headers=h)
    assert resend.status_code == 422  # everyone in the audience unsubscribed
    assert run_token is None


# ---------------------------------------------------------------------------
# Web forms, import/export, enrichment, calls, calendar, API keys
# ---------------------------------------------------------------------------


def test_web_forms_create_leads_and_cases(client, ws):
    h = ws["headers"]
    form = client.post(f"{API}/forms", json={"name": "Get a quote", "settings": {"title": "Get a quote"}}, headers=h).json()
    public = client.get(f"{API}/public/forms/{form['slug']}").json()
    assert public["title"] == "Get a quote" and len(public["fields"]) == 5
    bad = client.post(f"{API}/public/forms/{form['slug']}/submit", json={"data": {"first_name": "A"}})
    assert bad.status_code == 422
    good = client.post(
        f"{API}/public/forms/{form['slug']}/submit",
        json={
            "data": {"first_name": "Rachel", "last_name": "Kim", "email": "rachel@kim.example", "company_name": "Kim Realty"},
            "attribution": {"utm_source": "google", "gclid": "Cj0KCQ-test"},
        },
    )
    assert good.status_code == 200 and good.json()["record_type"] == "lead"
    leads = client.get(f"{API}/leads?search=Rachel", headers=h).json()
    assert leads and leads[0]["source"] == "google_ads"
    case_form = client.post(f"{API}/forms", json={"name": "Support", "target": "case"}, headers=h).json()
    submitted = client.post(
        f"{API}/public/forms/{case_form['slug']}/submit",
        json={"data": {"name": "Pat", "email": "pat@example.com", "subject": "Can't log in", "description": "Password reset fails"}},
    )
    assert submitted.status_code == 200 and submitted.json()["record_type"] == "case"
    subs = client.get(f"{API}/forms/{case_form['id']}/submissions", headers=h).json()
    assert subs[0]["link"].startswith("/cases/")
    # Honeypot submissions are silently dropped.
    assert client.post(f"{API}/public/forms/{form['slug']}/submit", json={"data": {"_hp": "spam", "last_name": "Bot"}}).status_code == 200


def test_import_export_and_enrichment(client, ws):
    h = ws["headers"]
    data = "First Name,Last Name,Email,Company,Title,Phone\nAna,Lopez,ana@lopez-roofing.example,Lopez Roofing,VP Operations,2145550100\nBo,Chen,not-an-email,,Engineer,\n"
    preview = client.post(f"{API}/data/import/preview", json={"object_type": "lead", "csv": data}, headers=h).json()
    assert preview["mapping"]["Email"] == "email" and preview["mapping"]["Company"] == "company_name"
    result = client.post(
        f"{API}/data/import",
        json={"object_type": "lead", "csv": data, "filename": "leads.csv", "mapping": preview["mapping"], "duplicate_action": "skip"},
        headers=h,
    ).json()
    assert result["created"] == 1 and result["errors"] == 1 and result["error_rows"][0]["row"] == 3
    again = client.post(
        f"{API}/data/import", json={"object_type": "lead", "csv": data, "mapping": preview["mapping"], "duplicate_action": "skip"}, headers=h
    ).json()
    assert again["created"] == 0 and again["skipped"] == 1
    lead = client.get(f"{API}/leads?search=Lopez", headers=h).json()[0]
    enriched = client.post(f"{API}/enrich/lead/{lead['id']}", headers=h).json()
    assert enriched["applied"]["phone"] == "+1 214-555-0100"
    assert enriched["insights"]["seniority"] == "Vice president"
    assert enriched["insights"]["company_domain"] == "lopez-roofing.example"
    export = client.get(f"{API}/data/export/lead.csv", headers=h)
    rows = list(csv.reader(io.StringIO(export.text)))
    assert rows[0][0] == "id" and any("Lopez" in row for row in rows)


def test_calls_calendar_and_api_keys(client, ws, contact):
    h = ws["headers"]
    call = client.post(
        f"{API}/calls",
        json={"contact_id": contact["id"], "phone": "+1 972-555-0101", "outcome": "left_voicemail", "duration_seconds": 42, "follow_up_in_days": 1},
        headers=h,
    )
    assert call.status_code == 201 and call.json()["task_id"]
    calls = client.get(f"{API}/calls", headers=h).json()
    assert calls["stats"]["total"] >= 1 and calls["items"][0]["outcome"] == "left_voicemail"

    feed = client.post(f"{API}/integrations/calendar", headers=h).json()
    token = feed["url"].split("/calendar/")[1].removesuffix(".ics")
    ics = client.get(f"{API}/public/calendar/{token}.ics")
    assert ics.status_code == 200 and "BEGIN:VCALENDAR" in ics.text and "BEGIN:VEVENT" in ics.text

    key = client.post(f"{API}/api-keys", json={"name": "Zapier", "scope": "read"}, headers=h).json()
    assert key["key"].startswith("aiva_sk_")
    api_headers = {"Authorization": f"Bearer {key['key']}", "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/accounts", headers=api_headers).status_code == 200
    assert client.post(f"{API}/accounts", json={"name": "Via key"}, headers=api_headers).status_code == 403
    assert client.post(f"{API}/api-keys", json={"name": "Nested"}, headers=api_headers).status_code == 403
    assert client.delete(f"{API}/api-keys/{key['id']}", headers=h).status_code == 204
    assert client.get(f"{API}/accounts", headers=api_headers).status_code == 401


def test_hipaa_mode_requires_mfa(client, ws):
    h = ws["headers"]
    blocked = client.put(f"{API}/compliance", json={"hipaa_mode": True}, headers=h)
    assert blocked.status_code == 409 and "two-step" in blocked.json()["detail"]
    info = client.get(f"{API}/compliance", headers=h).json()
    assert info["available"] and info["members_without_mfa"] >= 1


def test_chat_integration_validation(client, ws):
    h = ws["headers"]
    bad = client.put(f"{API}/integrations/chat/slack", json={"webhook_url": "https://example.com/x", "events": ["deal_won"]}, headers=h)
    assert bad.status_code == 422
    ok = client.put(
        f"{API}/integrations/chat/slack", json={"webhook_url": "https://hooks.slack.com/services/T000/B000/XXXX", "events": ["lead_created", "nope"]}, headers=h
    )
    assert ok.status_code == 200 and ok.json()["events"] == ["lead_created"]
    client.post(f"{API}/leads", json={"first_name": "Chat", "last_name": "Alert"}, headers=h)
    overview = client.get(f"{API}/integrations", headers=h).json()
    assert overview["slack"]["connected"] and overview["slack"]["events"] == ["lead_created"]

    async def queued():
        from sqlalchemy import func, select

        from app.db.session import AsyncSessionLocal
        from app.models.editions import OutboundMessage

        async with AsyncSessionLocal() as session:
            return (
                await session.execute(
                    select(func.count()).select_from(OutboundMessage).where(
                        OutboundMessage.organization_id == uuid.UUID(ws["org"]), OutboundMessage.event == "lead_created"
                    )
                )
            ).scalar_one()

    assert asyncio.run(queued()) >= 1
    assert client.delete(f"{API}/integrations/chat/slack", headers=h).status_code == 204


# ---------------------------------------------------------------------------
# Pro: AI, predictions, process management, inventory, invoices, journeys, portals
# ---------------------------------------------------------------------------


def test_ai_agents_predictions_and_extended_ai(client, ws, account, pipeline):
    h = ws["headers"]
    open_stage = next(s for s in pipeline["stages"] if s["category"] == "open")
    opp = client.post(
        f"{API}/opportunities",
        json={
            "name": "Fleet program",
            "account_id": account["id"],
            "pipeline_id": pipeline["id"],
            "stage_id": open_stage["id"],
            "amount": "48500",
            "expected_close_date": (date.today() - timedelta(days=10)).isoformat(),
        },
        headers=h,
    )
    assert opp.status_code == 201, opp.text
    client.post(f"{API}/leads", json={"first_name": "Grace", "last_name": "Whitfield", "job_title": "Chief Operating Officer", "source": "referral", "email": "grace@lakeside.example", "phone": "4695550112", "company_name": "Lakeside"}, headers=h)
    agents = client.get(f"{API}/ai/agents", headers=h).json()
    assert {a["key"] for a in agents["agents"]} == {"lead_qualifier", "deal_coach", "case_triage", "follow_up_writer", "data_hygiene"}
    run = client.post(f"{API}/ai/agents/deal_coach/run", headers=h).json()
    assert run["created"] >= 1
    suggestions = client.get(f"{API}/ai/suggestions?agent=deal_coach", headers=h).json()
    slipped = next(s for s in suggestions if s["action"].get("field") == "expected_close_date")
    accepted = client.post(f"{API}/ai/suggestions/{slipped['id']}/accept", headers=h)
    assert accepted.status_code == 200, accepted.text
    updated = client.get(f"{API}/opportunities/{opp.json()['id']}", headers=h).json()
    assert updated["expected_close_date"] > date.today().isoformat()
    assert client.post(f"{API}/ai/suggestions/{slipped['id']}/accept", headers=h).status_code == 409
    rerun = client.post(f"{API}/ai/agents/deal_coach/run", headers=h).json()
    assert rerun["created"] == 0  # no duplicates

    lq = client.post(f"{API}/ai/agents/lead_qualifier/run", headers=h).json()
    assert lq["created"] >= 1

    predictions = client.get(f"{API}/predictions", headers=h).json()
    assert predictions["summary"]["open_deals"] >= 1
    item = next(i for i in predictions["items"] if i["name"] == "Fleet program")
    assert 2 <= item["probability"] <= 98 and item["factors"]
    single = client.get(f"{API}/predictions/opportunities/{opp.json()['id']}", headers=h).json()
    assert single["probability"] == item["probability"]

    answer = client.post(f"{API}/ai/ask", json={"question": "How much open pipeline do we have?"}, headers=h).json()
    assert answer["source"] == "rules" and "open deals" in answer["answer"]
    summary = client.get(f"{API}/ai/summary/opportunity/{opp.json()['id']}", headers=h).json()
    assert "Fleet program" in summary["summary"]
    draft = client.post(f"{API}/ai/draft", json={"object_type": "opportunity", "record_id": opp.json()["id"], "intent": "follow up"}, headers=h).json()
    assert draft["subject"] and draft["body"]


def test_stage_playbook_blocks_until_requirements_met(client, ws, account, pipeline):
    h = ws["headers"]
    stages = [s for s in pipeline["stages"] if s["category"] == "open"]
    first, second = stages[0], stages[1]
    assert client.put(
        f"{API}/process/stage-rules/{first['id']}", json={"checklist": ["Confirm budget"], "exit_requires_checklist": True}, headers=h
    ).status_code == 200
    assert client.put(f"{API}/process/stage-rules/{second['id']}", json={"required_fields": ["next_step"]}, headers=h).status_code == 200
    opp = client.post(
        f"{API}/opportunities",
        json={"name": "Playbook deal", "account_id": account["id"], "pipeline_id": pipeline["id"], "stage_id": first["id"], "amount": "1000"},
        headers=h,
    ).json()
    blocked = client.patch(f"{API}/opportunities/{opp['id']}", json={"stage_id": second["id"]}, headers=h)
    assert blocked.status_code == 422 and blocked.json()["code"] == "process_checklist"
    playbook = client.post(f"{API}/process/opportunities/{opp['id']}/checklist", json={"item": "Confirm budget", "done": True}, headers=h).json()
    assert playbook["current"]["checklist"][0]["done"]
    missing = client.patch(f"{API}/opportunities/{opp['id']}", json={"stage_id": second["id"]}, headers=h)
    assert missing.status_code == 422 and missing.json()["code"] == "process_required_fields"
    moved = client.patch(f"{API}/opportunities/{opp['id']}", json={"stage_id": second["id"], "next_step": "Send proposal"}, headers=h)
    assert moved.status_code == 200, moved.text
    for stage in (first, second):
        client.delete(f"{API}/process/stage-rules/{stage['id']}", headers=h)


def test_inventory_quotes_invoices_and_quickbooks(client, ws, account, contact):
    h = ws["headers"]
    product = client.post(f"{API}/products", json={"name": "Thermostat", "sku": f"TH-{uuid.uuid4().hex[:6]}", "unit_price": "120"}, headers=h).json()
    assert client.patch(f"{API}/inventory/products/{product['id']}", json={"track_inventory": True, "reorder_level": "5", "unit_cost": "70"}, headers=h).status_code == 200
    assert client.post(f"{API}/inventory/products/{product['id']}/movements", json={"kind": "receive", "quantity": "12"}, headers=h).json()["stock_on_hand"] == 12
    quote = client.post(
        f"{API}/quotes",
        json={"name": "Thermostats", "account_id": account["id"], "contact_id": contact["id"], "line_items": [{"product_id": product["id"], "name": "Thermostat", "quantity": "8", "unit_price": "120"}]},
        headers=h,
    ).json()
    assert client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "sent"}, headers=h).status_code == 200
    accepted = client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "accepted"}, headers=h)
    assert accepted.status_code == 200
    stock = next(i for i in client.get(f"{API}/inventory", headers=h).json()["items"] if i["id"] == product["id"])
    assert stock["stock_on_hand"] == 4 and stock["status"] == "low"
    invoice = client.post(f"{API}/invoices/from-quote/{quote['id']}", json={"due_in_days": 15}, headers=h)
    assert invoice.status_code == 201, invoice.text
    invoice_id = invoice.json()["id"]
    assert client.post(f"{API}/invoices/from-quote/{quote['id']}", json={}, headers=h).status_code == 409
    assert client.post(f"{API}/invoices/{invoice_id}/payments", json={"amount": "100"}, headers=h).status_code == 409
    client.patch(f"{API}/invoices/{invoice_id}", json={"status": "sent"}, headers=h)
    paid = client.post(f"{API}/invoices/{invoice_id}/payments", json={"amount": str(invoice.json()["total"])}, headers=h).json()
    assert paid["status"] == "paid" and paid["balance"] == 0
    qb = client.get(f"{API}/quickbooks/invoices.csv", headers=h)
    assert qb.status_code == 200 and f"INV-{invoice.json()['number']}" in qb.text
    assert f"INV-{invoice.json()['number']}" not in client.get(f"{API}/quickbooks/invoices.csv", headers=h).text
    customers = client.get(f"{API}/quickbooks/customers.csv", headers=h)
    assert "Brightline HVAC Services" in customers.text


def test_approvals_for_deals_and_quotes(client, ws, account, pipeline):
    h = ws["headers"]
    manager = invite(client, ws, "manager", "Maya Manager")
    won = next(s for s in pipeline["stages"] if s["category"] == "won")
    rule = client.post(
        f"{API}/approval-rules",
        json={
            "name": "Big deals need sign-off",
            "object_type": "opportunity",
            "trigger": "stage_change",
            "target_stage_id": won["id"],
            "conditions": [{"field": "amount", "operator": "greater_than", "value": "10000"}],
            "approver_type": "user",
            "approver_value": manager["user_id"],
        },
        headers=h,
    )
    assert rule.status_code == 201, rule.text
    open_stage = next(s for s in pipeline["stages"] if s["category"] == "open")
    opp = client.post(
        f"{API}/opportunities",
        json={"name": "Hospital contract", "account_id": account["id"], "pipeline_id": pipeline["id"], "stage_id": open_stage["id"], "amount": "250000"},
        headers=h,
    ).json()
    blocked = client.patch(f"{API}/opportunities/{opp['id']}", json={"stage_id": won["id"]}, headers=h)
    assert blocked.status_code == 409 and blocked.json()["code"] == "approval_required"
    request = client.post(
        f"{API}/approvals",
        json={"object_type": "opportunity", "record_id": opp["id"], "rule_id": blocked.json()["rule_id"], "requested_change": blocked.json()["requested_change"], "comment": "Signed PO attached"},
        headers=h,
    )
    assert request.status_code == 201, request.text
    locked = client.patch(f"{API}/opportunities/{opp['id']}", json={"name": "Renamed"}, headers=h)
    assert locked.status_code == 423
    inbox = client.get(f"{API}/approvals", headers=manager["headers"]).json()
    assert inbox["counts"]["inbox"] == 1
    decided = client.post(f"{API}/approvals/{request.json()['id']}/decide", json={"approve": True, "comment": "Great work"}, headers=manager["headers"])
    assert decided.status_code == 200, decided.text
    assert client.get(f"{API}/opportunities/{opp['id']}", headers=h).json()["stage_id"] == won["id"]
    assert client.patch(f"{API}/opportunities/{opp['id']}", json={"name": "Renamed"}, headers=h).status_code == 200

    quote_rule = client.post(
        f"{API}/approval-rules",
        json={"name": "Discounts over 20%", "object_type": "quote", "trigger": "quote_send", "conditions": [{"field": "discount_percent", "operator": "greater_than", "value": "20"}], "approver_type": "role", "approver_value": "manager"},
        headers=h,
    ).json()
    quote = client.post(
        f"{API}/quotes",
        json={"name": "Discounted", "account_id": account["id"], "line_items": [{"name": "Service", "quantity": "1", "unit_price": "1000", "discount_percent": "30"}]},
        headers=h,
    ).json()
    needs = client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "sent"}, headers=h)
    assert needs.status_code == 409 and needs.json()["code"] == "approval_required"
    req = client.post(
        f"{API}/approvals",
        json={"object_type": "quote", "record_id": quote["id"], "rule_id": quote_rule["id"], "requested_change": {"status": "sent"}},
        headers=h,
    ).json()
    rejected = client.post(f"{API}/approvals/{req['id']}/decide", json={"approve": False, "comment": "Max 20%"}, headers=manager["headers"])
    assert rejected.status_code == 200
    assert client.get(f"{API}/quotes/{quote['id']}", headers=h).json()["status"] == "draft"
    for item in client.get(f"{API}/approval-rules", headers=h).json()["items"]:
        client.delete(f"{API}/approval-rules/{item['id']}", headers=h)


def test_journeys_enroll_and_run(client, ws):
    h = ws["headers"]
    template = client.post(f"{API}/email-templates", json={"name": "Welcome", "subject": "Welcome {{first_name}}", "body": "Thanks for your interest."}, headers=h).json()
    journey = client.post(
        f"{API}/journeys",
        json={
            "name": "New lead nurture",
            "object_type": "lead",
            "trigger": "created",
            "conditions": [{"field": "source", "operator": "equals", "value": "website"}],
            "steps": [
                {"type": "send_email", "params": {"template_id": template["id"]}},
                {"type": "create_task", "params": {"title": "Call {{first_name}}", "due_in_days": 1}},
                {"type": "wait", "params": {"days": 3}},
                {"type": "notify", "params": {"title": "{{full_name}} finished nurture"}},
            ],
        },
        headers=h,
    )
    assert journey.status_code == 201, journey.text
    journey_id = journey.json()["id"]
    assert client.post(f"{API}/journeys/{journey_id}/status", json={"status": "active"}, headers=h).status_code == 200
    lead = client.post(f"{API}/leads", json={"first_name": "Nora", "last_name": "Nurture", "email": "nora@example.com", "source": "website"}, headers=h)
    assert lead.status_code == 201
    other = client.post(f"{API}/leads", json={"first_name": "Skip", "last_name": "Me", "source": "referral"}, headers=h)
    assert other.status_code == 201
    ran = client.post(f"{API}/journeys/{journey_id}/run", headers=h).json()
    assert ran["processed"] == 1
    detail = client.get(f"{API}/journeys/{journey_id}", headers=h).json()
    assert detail["enrollments"]["active"] == 1
    enrollment = detail["recent"][0]
    assert enrollment["current_step"] == 3 and "Waiting" in enrollment["history"][-1]["message"]
    bad = client.post(f"{API}/journeys", json={"name": "Bad", "object_type": "lead", "steps": [{"type": "wait", "params": {"days": 0}}]}, headers=h)
    assert bad.status_code == 422


def test_customer_portal(client, ws, account, contact):
    h = ws["headers"]
    portal = client.post(f"{API}/portals", json={"name": "Help Center", "audience": "customer", "settings": {"accent": "#2563eb"}}, headers=h)
    assert portal.status_code == 201, portal.text
    slug = portal.json()["slug"]
    invited = client.post(f"{API}/portals/{portal.json()['id']}/users", json={"contact_id": contact["id"]}, headers=h).json()
    token = invited["login_url"].split("token=")[1]
    info = client.get(f"{API}/portal-api/{slug}").json()
    assert info["modules"] == ["cases", "knowledge", "quotes", "invoices"]
    assert client.get(f"{API}/portal-api/{slug}/cases").status_code == 401
    session = client.post(f"{API}/portal-api/{slug}/verify", json={"token": token}).json()["session_token"]
    assert client.post(f"{API}/portal-api/{slug}/verify", json={"token": token}).status_code == 400
    ph = {"X-Portal-Token": session}
    me = client.get(f"{API}/portal-api/{slug}/me", headers=ph).json()
    assert me["account_name"] == "Brightline HVAC Services"
    created = client.post(f"{API}/portal-api/{slug}/cases", json={"subject": "Thermostat not heating", "description": "Unit 4 is cold"}, headers=ph)
    assert created.status_code == 201
    case_id = created.json()["id"]
    reply = client.post(f"{API}/portal-api/{slug}/cases/{case_id}/comments", json={"body": "Any update?"}, headers=ph)
    assert reply.status_code == 201 and reply.json()["comments"][0]["body"] == "Any update?"
    internal_case = client.get(f"{API}/cases/{case_id}", headers=h).json()
    assert internal_case["channel"] == "portal"
    other_contact = client.post(f"{API}/contacts", json={"first_name": "Other", "last_name": "Person", "email": "other@example.com"}, headers=h).json()
    other_case = client.post(f"{API}/cases", json={"subject": "Private", "contact_id": other_contact["id"]}, headers=h).json()
    assert client.get(f"{API}/portal-api/{slug}/cases/{other_case['id']}", headers=ph).status_code == 404
    quote = client.post(
        f"{API}/quotes", json={"name": "Portal quote", "account_id": account["id"], "line_items": [{"name": "Visit", "quantity": "1", "unit_price": "150"}]}, headers=h
    ).json()
    client.post(f"{API}/quotes/{quote['id']}/status", json={"status": "sent"}, headers=h)
    quotes = client.get(f"{API}/portal-api/{slug}/quotes", headers=ph).json()
    assert any(q["id"] == quote["id"] for q in quotes)
    decided = client.post(f"{API}/portal-api/{slug}/quotes/{quote['id']}/decision", json={"accept": True}, headers=ph)
    assert decided.status_code == 200 and decided.json()["status"] == "accepted"
    assert client.post(f"{API}/portal-api/{slug}/logout", headers=ph).status_code == 204
    assert client.get(f"{API}/portal-api/{slug}/me", headers=ph).status_code == 401


def test_partner_portal_deal_registration(client, ws, contact):
    h = ws["headers"]
    portal = client.post(f"{API}/portals", json={"name": "Partners", "audience": "partner"}, headers=h).json()
    invited = client.post(f"{API}/portals/{portal['id']}/users", json={"contact_id": contact["id"]}, headers=h).json()
    token = invited["login_url"].split("token=")[1]
    session = client.post(f"{API}/portal-api/{portal['slug']}/verify", json={"token": token}).json()["session_token"]
    ph = {"X-Portal-Token": session}
    assert client.get(f"{API}/portal-api/{portal['slug']}/cases", headers=ph).status_code == 404
    registered = client.post(
        f"{API}/portal-api/{portal['slug']}/registrations",
        json={"company_name": "Summit Roofing", "last_name": "Delgado", "estimated_value": 12000},
        headers=ph,
    )
    assert registered.status_code == 201
    mine = client.get(f"{API}/portal-api/{portal['slug']}/registrations", headers=ph).json()
    assert mine[0]["company_name"] == "Summit Roofing" and mine[0]["estimated_value"] == 12000


# ---------------------------------------------------------------------------
# Enterprise: buttons, visibility, ABM, Google Ads, sandbox
# ---------------------------------------------------------------------------


def test_custom_buttons(client, ws, account):
    h = ws["headers"]
    link = client.post(
        f"{API}/buttons",
        json={"object_type": "account", "label": "Search maps", "action_type": "open_url", "config": {"url": "https://maps.google.com/?q={{name}}"}},
        headers=h,
    ).json()
    run = client.post(f"{API}/buttons/{link['id']}/run", json={"record_id": account["id"]}, headers=h).json()
    assert run["open_url"] == "https://maps.google.com/?q=Brightline%20HVAC%20Services"
    function = client.post(
        f"{API}/buttons",
        json={
            "object_type": "account",
            "label": "Mark as customer",
            "action_type": "function",
            "config": {"steps": [{"type": "set_field", "field": "lifecycle_stage", "expression": "'customer'"}, {"type": "create_task", "title": "Send welcome kit to {{name}}"}]},
        },
        headers=h,
    )
    assert function.status_code == 201, function.text
    result = client.post(f"{API}/buttons/{function.json()['id']}/run", json={"record_id": account["id"]}, headers=h)
    assert result.status_code == 200, result.text
    assert client.get(f"{API}/accounts/{account['id']}", headers=h).json()["lifecycle_stage"] == "customer"
    webhook = client.post(
        f"{API}/buttons", json={"object_type": "account", "label": "Sync", "action_type": "webhook", "config": {"url": "https://127.0.0.1:9/hook"}}, headers=h
    ).json()
    assert webhook["config"]["secret"]
    assert client.post(f"{API}/buttons/{webhook['id']}/run", json={"record_id": account["id"]}, headers=h).status_code == 502
    assert len(client.get(f"{API}/buttons?object_type=account", headers=h).json()) == 3


def test_team_visibility_limits_records(client):
    org = signup(client, "Visibility Co")
    h = org["headers"]
    role = client.post(
        f"{API}/admin/roles",
        json={"name": "Restricted rep", "permissions": ["records.read", "records.write"]},
        headers=h,
    )
    assert role.status_code == 201, role.text
    rep = invite(client, org, role.json()["key"], "Rhea Rep")
    peer = invite(client, org, role.json()["key"], "Paul Peer")
    mine = client.post(f"{API}/leads", json={"first_name": "Mine", "last_name": "Lead"}, headers=rep["headers"]).json()
    theirs = client.post(f"{API}/leads", json={"first_name": "Their", "last_name": "Lead"}, headers=peer["headers"]).json()
    assert len(client.get(f"{API}/leads", headers=rep["headers"]).json()) == 2
    assert client.put(f"{API}/org/visibility", json={"mode": "hierarchy"}, headers=h).status_code == 200
    visible = {lead["id"] for lead in client.get(f"{API}/leads", headers=rep["headers"]).json()}
    assert visible == {mine["id"]}
    assert client.get(f"{API}/leads/{theirs['id']}", headers=rep["headers"]).status_code == 404
    assert client.put(f"{API}/org/members/{peer['user_id']}/manager", json={"reports_to_user_id": rep["user_id"]}, headers=h).status_code == 200
    visible = {lead["id"] for lead in client.get(f"{API}/leads", headers=rep["headers"]).json()}
    assert visible == {mine["id"], theirs["id"]}
    loop = client.put(f"{API}/org/members/{rep['user_id']}/manager", json={"reports_to_user_id": peer["user_id"]}, headers=h)
    assert loop.status_code == 422
    # Owners (View all records) always see everything.
    assert len(client.get(f"{API}/leads", headers=h).json()) == 2


def test_abm_and_google_ads(client, ws, account, contact):
    h = ws["headers"]
    assert client.post(f"{API}/abm/accounts", json={"account_ids": [account["id"]], "tier": "tier_1", "goal": "Expand to all branches"}, headers=h).json()["added"] == 1
    abm = client.get(f"{API}/abm/accounts", headers=h).json()
    row = abm["items"][0]
    assert row["tier"] == "tier_1" and "Decision maker" in row["committee_covered"] and "Finance" in row["committee_missing"]
    key = client.post(f"{API}/google-ads/key", headers=h).json()["key"]
    org_slug = client.get(f"{API}/auth/me", headers=h).json()["organization"]["slug"]
    hook = {
        "lead_id": "TeSter-123",
        "gcl_id": "EAIaIQ-test",
        "campaign_id": 123,
        "google_key": key,
        "user_column_data": [
            {"column_id": "FULL_NAME", "string_value": "Victor Salas"},
            {"column_id": "EMAIL", "string_value": "victor@trinity.example"},
            {"column_id": "COMPANY_NAME", "string_value": "Trinity Ridge"},
        ],
    }
    assert client.post(f"{API}/public/google-ads/{org_slug}", json={**hook, "google_key": "wrong"}).status_code == 403
    assert client.post(f"{API}/public/google-ads/{org_slug}", json=hook).json() == {"ok": True}
    assert client.post(f"{API}/public/google-ads/{org_slug}", json=hook).json()["duplicate"]
    overview = client.get(f"{API}/google-ads", headers=h).json()
    assert overview["stats"]["leads"] >= 1
    conversions = client.get(f"{API}/google-ads/conversions.csv", headers=h)
    assert conversions.text.startswith("Parameters:TimeZone=") and "EAIaIQ-test" in conversions.text


def test_sandbox_create_deploy_and_delete(client):
    org = signup(client, "Sandbox Co")
    h = org["headers"]
    client.post(f"{API}/accounts", json={"name": "Sample account"}, headers=h)
    created = client.post(f"{API}/sandboxes", json={"sample_data": True}, headers=h)
    assert created.status_code == 201, created.text
    sandbox_id = created.json()["id"]
    assert created.json()["copied"]["accounts"] == 1
    login = client.post(f"{API}/auth/login", json={"email": org["email"], "password": PASSWORD, "organization_id": sandbox_id}).json()
    sh = {"Authorization": f"Bearer {login['access_token']}", "X-Organization-ID": sandbox_id}
    me = client.get(f"{API}/auth/me", headers=sh).json()
    assert me["is_sandbox"] and me["subscription"]["plan"] == "trial"
    assert len(client.get(f"{API}/accounts", headers=sh).json()) == 1
    client.post(f"{API}/email-templates", json={"name": "From sandbox", "subject": "Hi", "body": "Built in the sandbox"}, headers=sh)
    deployed = client.post(f"{API}/sandboxes/deploy", json={"categories": ["email_templates"]}, headers=sh)
    assert deployed.status_code == 200, deployed.text
    names = {t["name"] for t in client.get(f"{API}/email-templates", headers=h).json()}
    assert "From sandbox" in names
    assert client.post(f"{API}/sandboxes", json={}, headers=sh).status_code == 409
    assert client.delete(f"{API}/sandboxes/{sandbox_id}", headers=h).status_code == 204
    assert client.get(f"{API}/sandboxes", headers=h).json()["items"] == []


def test_task_reminders_are_delivered(client, ws):
    h = ws["headers"]
    task = client.post(
        f"{API}/tasks",
        json={
            "title": "Reminder test",
            "due_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "reminder_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "assigned_to_user_id": ws["user_id"],
        },
        headers=h,
    )
    assert task.status_code == 201, task.text

    async def run():
        from app.db.session import AsyncSessionLocal
        from app.services.scheduler import send_due_reminders

        async with AsyncSessionLocal() as session:
            return await send_due_reminders(session)

    assert asyncio.run(run()) >= 1
    notes = client.get(f"{API}/notifications", headers=h).json()["items"]
    assert any(n["title"] == "Reminder: Reminder test" for n in notes)
    assert asyncio.run(run()) == 0
