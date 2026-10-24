"""AI models: OpenAI API and Azure OpenAI running AIVA's AI features.

OpenAI, Azure and Microsoft Entra ID are never called: ``_ai_client.TRANSPORT`` is
an ``httpx.MockTransport`` that answers like the real APIs and records requests.
Claude (AIVA's default model) is replaced where a test needs it.
"""

import asyncio
import json
import re
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Integration#2026"
OPENAI_KEY = "sk-" + "proj-" + "TESTKEY0123456789abcdefghij"  # built at runtime so secret scanners skip it
AZURE_KEY = "azure-key-" + "0123456789abcdef" + "0123456789ab"  # built at runtime: not a real key
ENTRA_SECRET = "entra~secret~value~0123456789"
TENANT = "11111111-2222-3333-4444-555555555555"
APP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
AZURE = "https://contoso-ai.openai.azure.com"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Model Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@models.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Sam Owner", "email": email, "password": PASSWORD, "company_name": company})
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


class FakeAI:
    """OpenAI, Azure OpenAI and Entra ID in one MockTransport."""

    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []

    def on(self, method: str, pattern: str, answer):
        self.routes.insert(0, (method, pattern, answer))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for method, pattern, answer in self.routes:
            if method == request.method and re.search(pattern, url):
                result = answer(request) if callable(answer) else answer
                if isinstance(result, httpx.Response):
                    return result
                return httpx.Response(200, json=result)
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    def sent(self, part: str) -> list[httpx.Request]:
        return [r for r in self.requests if part in str(r.url)]

    def bodies(self, part: str) -> list[dict]:
        return [json.loads(r.content) for r in self.sent(part)]


MODELS = {"object": "list", "data": [{"id": m, "object": "model"} for m in ("gpt-4.1-mini", "gpt-5-mini", "text-embedding-3-small", "gpt-4o-mini", "whisper-1", "gpt-4o-realtime-preview")]}


def response_ok(text: str = "Subject: Next steps\n\nHi Cara, shall we meet Tuesday?", model: str = "gpt-5-mini-2025-08-07", tokens_in: int = 200_000, tokens_out: int = 50_000):
    return {
        "id": "resp_123", "object": "response", "status": "completed", "model": model,
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]},
        ],
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out, "output_tokens_details": {"reasoning_tokens": 64}, "total_tokens": tokens_in + tokens_out},
    }


def chat_ok(text: str = "Subject: Hello\n\nHi there from Azure.", model: str = "gpt-4o-mini-2024-07-18"):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
    }


@pytest.fixture
def fake(monkeypatch):
    from app.integrations.providers import _ai_client, ai_models

    fake = FakeAI()
    waits: list[float] = []

    async def no_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(_ai_client, "TRANSPORT", httpx.MockTransport(fake.handle))
    monkeypatch.setattr(_ai_client, "_sleep", no_sleep)
    _ai_client._TOKENS.clear()
    ai_models._NOTED.clear()
    fake.waits = waits
    return fake


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Even if the server has an Anthropic key, tests never call Claude."""

    from app.services import ai

    async def silent(prompt, *, system, max_tokens):
        return None

    monkeypatch.setattr(ai, "_claude", silent)
    monkeypatch.setattr(ai, "configured", lambda: False)


@pytest.fixture
def claude(monkeypatch):
    """AIVA's default model, answering without calling Anthropic."""

    from app.services import ai

    calls: list[str] = []

    async def fake_claude(prompt, *, system, max_tokens):
        calls.append(prompt)
        return "Written by Claude."

    monkeypatch.setattr(ai, "_claude", fake_claude)
    monkeypatch.setattr(ai, "configured", lambda: True)
    return calls


def connect_openai(client, ws, fake) -> dict:
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/openai/toggle", json={"enabled": True}, headers=h).status_code == 200
    fake.on("GET", r"api\.openai\.com/v1/models$", MODELS)
    connected = client.put(f"{API}/integration-hub/openai/credentials", json={"values": {"api_key": OPENAI_KEY, "project": "proj_abc123"}}, headers=h)
    assert connected.status_code == 200, connected.text
    assert OPENAI_KEY not in connected.text
    return connected.json()


def contact(client, h) -> dict:
    created = client.post(
        f"{API}/contacts",
        json={"first_name": "Cara", "last_name": "Client", "email": f"cara-{uuid.uuid4().hex[:6]}@client.example", "phone": "+1 (555) 123-4567"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    return created.json()


def settings(client, h, key: str, **config):
    return client.put(f"{API}/integration-hub/{key}/settings", json={"config": config}, headers=h)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_endpoint_allow_list_and_name_checks():
    from app.integrations.core import IntegrationError
    from app.integrations.providers import _ai_client as c

    assert c.azure_endpoint("contoso-ai.openai.azure.com/") == AZURE
    assert c.azure_endpoint("https://contoso.cognitiveservices.azure.com/openai/v1") == "https://contoso.cognitiveservices.azure.com"
    assert c.azure_endpoint("https://contoso.services.ai.azure.com") == "https://contoso.services.ai.azure.com"
    for bad in ("http://contoso.openai.azure.com", "https://evil.example.com", "https://contoso.openai.azure.com.evil.com", "https://a.b.openai.azure.com",
                "https://contoso.openai.azure.com:8443", "https://user@contoso.openai.azure.com", "https://openai.azure.com", "", "https://169.254.169.254"):
        with pytest.raises(IntegrationError):
            c.azure_endpoint(bad)
    assert c.chat_models([m["id"] for m in MODELS["data"]] + ["ft:gpt-4o-mini:acme::x", "o3", "dall-e-3"]) == ["gpt-4.1-mini", "gpt-4o-mini", "gpt-5-mini", "ft:gpt-4o-mini:acme::x", "o3"]
    assert c.is_reasoning_model("gpt-5-mini") and c.is_reasoning_model("o4-mini") and c.is_reasoning_model("gpt-6-luna")
    assert not c.is_reasoning_model("gpt-4.1-mini") and not c.is_reasoning_model("my-deployment")
    with pytest.raises(IntegrationError):
        c.check_name("../../etc/passwd", "deployment name")


def test_redaction_and_prices():
    from decimal import Decimal

    from app.integrations.providers import ai_models as m

    text = "Call +1 (555) 123-4567 or 020 7946 0958 x, email ann.lee+crm@acme.co.uk on 2026-10-24T10:00:00 about $1500000.0 id 550e8400-e29b-41d4-a716-446655440000 ssn enc:v1:gAAAA_b-c= masked ••••••42"
    scrubbed = m.scrub(text, emails=True, phones=True)
    assert "[phone]" in scrubbed and "555" not in scrubbed and "7946" not in scrubbed
    assert "[email]" in scrubbed and "acme.co.uk" not in scrubbed
    assert "2026-10-24T10:00:00" in scrubbed and "1500000.0" in scrubbed and "550e8400-e29b-41d4-a716-446655440000" in scrubbed
    assert "enc:v1" not in scrubbed and "••" not in scrubbed
    kept = m.scrub(text, emails=False, phones=False)
    assert "ann.lee+crm@acme.co.uk" in kept and "+1 (555) 123-4567" in kept and "enc:v1" not in kept
    table = m.prices_for({"prices": {"crm-mini": {"input": "1.00", "output": "4.00"}}})
    assert m.price_of(table, "gpt-4o-mini-2024-07-18") == (Decimal("0.15"), Decimal("0.60"))
    assert m.price_of(table, "crm-mini", "gpt-4o-mini") == (Decimal("1.00"), Decimal("4.00"))
    assert m.price_of(table, "my-deployment", "gpt-4.1-2025-04-14") == (Decimal("2.00"), Decimal("8.00"))
    assert m.price_of(table, "mystery") is None


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


def test_openai_connects_and_writes_drafts_with_data_controls(client, fake):
    ws = signup(client, "OpenAI Co")
    h = ws["headers"]
    detail = connect_openai(client, ws, fake)
    assert detail["status"] == "connected"
    assert detail["extra"]["settings"]["default_model"] == "gpt-5-mini"  # chosen from what the key can use
    assert detail["extra"]["models"] == ["gpt-4.1-mini", "gpt-4o-mini", "gpt-5-mini"]
    assert detail["extra"]["active"] is True
    listing = fake.sent("/v1/models")[0]
    assert listing.headers["authorization"] == f"Bearer {OPENAI_KEY}" and listing.headers["openai-project"] == "proj_abc123"

    person = contact(client, h)
    fake.on("POST", r"api\.openai\.com/v1/responses$", response_ok())
    draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"], "intent": "book a demo"}, headers=h)
    assert draft.status_code == 200, draft.text
    assert draft.json()["source"] == "openai" and draft.json()["model"] == "gpt-5-mini"
    assert draft.json()["subject"] == "Next steps"

    sent = fake.bodies("/v1/responses")[-1]
    assert sent["model"] == "gpt-5-mini" and sent["store"] is False
    assert sent["reasoning"] == {"effort": "low"} and "temperature" not in sent
    assert sent["max_output_tokens"] == 450 + 1024  # the draft plus room to reason, under the 2,000 cap
    assert person["email"] not in sent["input"] and "[email]" in sent["input"]
    assert "123-4567" not in sent["input"] and "[phone]" in sent["input"]
    assert sent["instructions"].startswith("You are AIVA")

    # Usage: tokens and estimated cost by model, feature and person; never the prompt.
    usage = client.get(f"{API}/ai-models/usage", headers=h).json()
    assert usage["totals"]["requests"] == 1 and usage["totals"]["input_tokens"] == 200_000 and usage["totals"]["output_tokens"] == 50_000
    assert usage["totals"]["cost"] == pytest.approx(0.25 * 0.2 + 2.0 * 0.05)
    assert usage["by_feature"][0]["feature"] == "drafts" and usage["by_user"][0]["name"] == "Sam Owner"
    assert usage["by_model"][0]["model"] == "gpt-5-mini"
    words = client.get(f"{API}/ai-usage", headers=h).json()
    assert words["model_source"] == "openai" and words["model_label"] == "OpenAI" and words["model_name"] == "gpt-5-mini"
    drafts = next(f for f in words["by_feature"] if f["feature"] == "drafts")
    assert drafts["openai"] > 0 and drafts["ai"] == drafts["openai"] and drafts["rules"] == 0
    assert words["provider_usage"]["totals"]["tokens"] == 250_000

    stored = run(_stored_usage(ws["org"]))
    assert stored and all("input" not in json.dumps(d).lower().replace("input_tokens", "") for d in stored)
    assert all(person["email"] not in json.dumps(d) and "book a demo" not in json.dumps(d) for d in stored)
    logs = client.get(f"{API}/integration-hub/openai/logs", headers=h).json()
    assert OPENAI_KEY not in json.dumps(logs) and person["email"] not in json.dumps(logs)


async def _stored_usage(org: str) -> list[dict]:
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.integration_hub import IntegrationObject

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(IntegrationObject).where(IntegrationObject.organization_id == uuid.UUID(org), IntegrationObject.object_type == "ai_call"))).scalars().all()
        return [dict(r.data) for r in rows]


def test_routing_per_feature_and_aiva_default(client, fake, claude):
    ws = signup(client, "Routing Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    saved = settings(client, h, "openai", feature_models={"summaries": "gpt-4.1-mini", "ask": "aiva"}, temperature=0.2)
    assert saved.status_code == 200, saved.text
    features = {f["key"]: f for f in saved.json()["extra"]["features"]}
    assert features["summaries"]["effective"] == "gpt-4.1-mini" and features["ask"]["effective"] == "AIVA default" and features["drafts"]["effective"] == "gpt-5-mini"

    fake.on("POST", r"/v1/responses$", lambda r: response_ok(text="A tidy summary.", model=json.loads(r.content)["model"]))
    person = contact(client, h)
    summary = client.get(f"{API}/ai/summary/contact/{person['id']}", headers=h).json()
    assert summary["source"] == "openai" and summary["model"] == "gpt-4.1-mini" and summary["summary"] == "A tidy summary."
    sent = fake.bodies("/v1/responses")[-1]
    assert sent["model"] == "gpt-4.1-mini" and sent["temperature"] == 0.2 and "reasoning" not in sent  # not a reasoning model
    assert sent["max_output_tokens"] == 350

    asked = client.post(f"{API}/ai/ask", json={"question": "How much open pipeline do we have?"}, headers=h).json()
    assert asked["source"] == "claude" and len(fake.sent("/v1/responses")) == 1  # Ask stays on AIVA's default

    status_now = client.get(f"{API}/ai-models/status", headers=h).json()
    assert status_now["features"]["summaries"] == {"source": "openai", "label": "OpenAI", "model": "gpt-4.1-mini", "reason": "ready"}
    assert status_now["features"]["ask"]["source"] == "claude" and status_now["workspace_model"] is True


def test_retries_then_falls_back_by_policy(client, fake, claude):
    ws = signup(client, "Fallback Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    person = contact(client, h)
    fake.on("POST", r"/v1/responses$", httpx.Response(503, json={"error": {"message": "overloaded"}}, headers={"retry-after": "1"}))
    draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()
    assert draft["source"] == "claude" and draft["body"] == "Written by Claude."
    assert len(fake.sent("/v1/responses")) == 3 and fake.waits == [1.0, 1.0]  # retry-after honoured, two retries
    usage = client.get(f"{API}/ai-models/usage", headers=h).json()
    assert usage["totals"]["errors"] == 1 and usage["totals"]["tokens"] == 0
    logs = client.get(f"{API}/integration-hub/openai/logs", headers=h).json()["items"]
    assert any(entry["action"] == "call_failed" and "overloaded" not in entry["message"] for entry in logs)

    assert settings(client, h, "openai", on_error="rules_only").status_code == 200
    fake.on("POST", r"/v1/responses$", httpx.Response(429, json={"error": {"message": "Rate limit reached", "type": "requests"}}, headers={"retry-after": "60"}))
    draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()
    assert draft["source"] == "rules" and "Written by Claude" not in draft["body"]
    assert len(fake.sent("/v1/responses")) == 4  # a 60s retry-after is longer than we wait: no retry, fall back now
    assert len(claude) == 1  # only the first failure went to AIVA's default


def test_bad_key_is_rejected_and_auth_failures_mark_the_connection(client, fake):
    ws = signup(client, "Bad Key Co")
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/openai/toggle", json={"enabled": True}, headers=h).status_code == 200
    bad = client.put(f"{API}/integration-hub/openai/credentials", json={"values": {"api_key": "not-a-key"}}, headers=h)
    assert bad.status_code == 422 and not fake.requests
    fake.on("GET", r"/v1/models$", httpx.Response(401, json={"error": {"message": "Incorrect API key provided: sk-proj-***ghij.", "code": "invalid_api_key"}}))
    rejected = client.put(f"{API}/integration-hub/openai/credentials", json={"values": {"api_key": OPENAI_KEY}}, headers=h)
    assert rejected.status_code == 422 and "rejected the credentials" in rejected.json()["detail"]
    assert OPENAI_KEY not in rejected.text

    connect_openai(client, ws, fake)
    fake.on("POST", r"/v1/responses$", httpx.Response(401, json={"error": {"message": "Incorrect API key"}}))
    person = contact(client, h)
    draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()
    assert draft["source"] == "rules"
    assert len(fake.sent("/v1/responses")) == 1  # auth errors aren't retried
    detail = client.get(f"{API}/integration-hub/openai", headers=h).json()
    assert detail["connection"]["status"] == "error" and "rejected the credentials" in detail["connection"]["last_error"]


def test_unsupported_parameters_are_dropped_and_retried(client, fake):
    ws = signup(client, "Params Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    assert settings(client, h, "openai", default_model="gpt-4.1-mini", temperature=0.7).status_code == 200

    def answer(request):
        body = json.loads(request.content)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature' is not supported with this model.", "type": "invalid_request_error", "param": "temperature"}})
        return response_ok(text="Fine without temperature.")

    fake.on("POST", r"/v1/responses$", answer)
    person = contact(client, h)
    summary = client.get(f"{API}/ai/summary/contact/{person['id']}", headers=h).json()
    assert summary["source"] == "openai" and summary["summary"] == "Fine without temperature."
    first, second = fake.bodies("/v1/responses")
    assert first["temperature"] == 0.7 and "temperature" not in second

    # A reasoning effort the model doesn't take (named only in "param") is dropped too.
    assert settings(client, h, "openai", default_model="gpt-5-mini", reasoning_effort="minimal").status_code == 200

    def effort(request):
        body = json.loads(request.content)
        if "reasoning" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'minimal' is not supported with this model.", "type": "invalid_request_error", "param": "reasoning.effort"}})
        return response_ok(text="Default effort.")

    fake.on("POST", r"/v1/responses$", effort)
    assert client.get(f"{API}/ai/summary/contact/{person['id']}", headers=h).json()["summary"] == "Default effort."
    assert fake.bodies("/v1/responses")[-2]["reasoning"] == {"effort": "minimal"} and "reasoning" not in fake.bodies("/v1/responses")[-1]


def test_incomplete_reasoning_answer_falls_back(client, fake):
    ws = signup(client, "Incomplete Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    fake.on("POST", r"/v1/responses$", {"id": "r", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": [{"type": "reasoning", "summary": []}], "usage": {"input_tokens": 10, "output_tokens": 1474}})
    person = contact(client, h)
    assert client.get(f"{API}/ai/summary/contact/{person['id']}", headers=h).json()["source"] == "rules"
    usage = client.get(f"{API}/ai-models/usage", headers=h).json()
    assert usage["totals"]["errors"] == 1


# ---------------------------------------------------------------------------
# Limits, plan, HIPAA
# ---------------------------------------------------------------------------


def test_monthly_token_and_spend_limits(client, fake, claude):
    ws = signup(client, "Limit Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    person = contact(client, h)
    assert settings(client, h, "openai", limit_type="tokens").status_code == 422  # a limit needs a number
    assert settings(client, h, "openai", limit_type="tokens", monthly_token_limit=300_000, at_limit="rules_only").status_code == 200
    fake.on("POST", r"/v1/responses$", response_ok())
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()["source"] == "openai"  # 250k used
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()["source"] == "openai"  # 500k used
    blocked = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()
    assert blocked["source"] == "rules" and len(fake.sent("/v1/responses")) == 2 and not claude
    detail = client.get(f"{API}/integration-hub/openai", headers=h).json()
    assert detail["extra"]["usage"]["limit"]["reached"] is True and detail["extra"]["state"]["reason"] == "limit"
    assert any(entry["action"] == "limit_reached" for entry in detail["logs"])

    # Spend: $0.30 limit, $0.15 a draft at the list price; at the limit, AIVA's default takes over.
    assert settings(client, h, "openai", limit_type="spend", monthly_spend_limit="0.40", at_limit="aiva_default").status_code == 200
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()["source"] == "openai"  # $0.45 now
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()["source"] == "claude"
    # Editing the price table changes the estimate (and the limit) straight away.
    assert settings(client, h, "openai", prices={"gpt-5-mini": {"input": "0.01", "output": "0.01"}}).status_code == 200
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()["source"] == "openai"
    assert client.get(f"{API}/ai-models/usage", headers=h).json()["totals"]["cost"] < 0.40


def test_plan_without_ai_models_uses_aiva_default(client, fake):
    ws = signup(client, "Starter Models Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    assert client.get(f"{API}/ai-models/status", headers=h).json()["default"]["source"] == "openai"
    assert client.post(f"{API}/billing/change-plan", json={"plan": "starter"}, headers=h).status_code == 200
    assert client.get(f"{API}/ai-models/status", headers=h).json()["default"]["source"] in ("claude", "rules")
    assert client.get(f"{API}/ai-models/openai/models", headers=h).status_code == 402
    assert client.post(f"{API}/ai-models/openai/test-prompt", json={"prompt": "hi"}, headers=h).status_code == 402


def test_hipaa_workspaces_need_an_acknowledged_baa(client, fake):
    """HIPAA workspaces require two-step sign-in for every API call, so routing is checked directly."""

    ws = signup(client, "Clinic Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    fake.on("POST", r"/v1/responses$", response_ok(text="From GPT."))
    run(_set_hipaa(ws["org"], True))
    try:
        reason, reply = run(_route(ws["org"]))
        assert reason == "hipaa" and reply.source == "rules" and not fake.sent("/v1/responses")
    finally:
        run(_set_hipaa(ws["org"], False))
    detail = client.get(f"{API}/integration-hub/openai", headers=h).json()["extra"]
    assert detail["hipaa"] == {"workspace": False, "acknowledged": False, "acknowledged_at": None}

    acknowledged = settings(client, h, "openai", hipaa_acknowledged=True)
    assert acknowledged.status_code == 200 and acknowledged.json()["extra"]["hipaa"]["acknowledged"] is True
    when = acknowledged.json()["extra"]["hipaa"]["acknowledged_at"]
    again = settings(client, h, "openai", hipaa_acknowledged=True, redact_emails=True)  # re-saving keeps who and when
    assert again.json()["extra"]["hipaa"]["acknowledged_at"] == when and again.json()["extra"]["settings"]["hipaa_acknowledged_by"] == ws["user_id"]
    run(_set_hipaa(ws["org"], True))
    try:
        reason, reply = run(_route(ws["org"]))
        assert reason == "ready" and reply.source == "openai" and reply.text == "From GPT."
    finally:
        run(_set_hipaa(ws["org"], False))


async def _route(org: str):
    from app.db.session import AsyncSessionLocal
    from app.integrations.providers import ai_models
    from app.services import ai

    async with AsyncSessionLocal() as db:
        decision = await ai_models.decide(db, uuid.UUID(org), "drafts")
        reply = await ai.generate("Draft a note to pat@example.com", feature="drafts", organization_id=uuid.UUID(org), db=db)
        return decision.reason, reply


async def _set_hipaa(org: str, on: bool) -> None:
    from app.db.session import AsyncSessionLocal
    from app.models.organization import Organization

    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, uuid.UUID(org))
        organization.settings = {**(organization.settings or {}), "hipaa_mode": on}
        await db.commit()


# ---------------------------------------------------------------------------
# Settings, test prompt, models, admin-only, tenants
# ---------------------------------------------------------------------------


def test_settings_validation(client, fake):
    ws = signup(client, "Settings Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    for bad in (
        {"temperature": 3}, {"max_output_tokens": 10}, {"timeout_seconds": 600}, {"reasoning_effort": "extreme"},
        {"feature_models": {"nonsense": "gpt-5-mini"}}, {"default_model": "gpt 5 / mini"}, {"at_limit": "panic"},
        {"api_mode": "v1"}, {"region": "mars"}, {"prices": {"gpt-5-mini": {"input": "-1", "output": "2"}}}, {"hipaa_acknowledged_at": "x"}, {"api_key": OPENAI_KEY},
    ):
        response = settings(client, h, "openai", **bad)
        assert response.status_code == 422, (bad, response.text)
    good = settings(client, h, "openai", region="eu", reasoning_effort="", timeout_seconds=12, max_output_tokens=900, redact_emails=False)
    assert good.status_code == 200, good.text
    saved = good.json()["extra"]["settings"]
    assert saved["region"] == "eu" and saved["timeout_seconds"] == 12 and saved["redact_emails"] is False and saved["redact_phones"] is True
    fake.on("POST", r"eu\.api\.openai\.com/v1/responses$", response_ok(text="Bonjour"))
    person = contact(client, h)
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()["body"] == "Bonjour"
    sent = fake.bodies("eu.api.openai.com/v1/responses")[-1]
    assert person["email"] in sent["input"] and "reasoning" not in sent and sent["max_output_tokens"] == 450 + 900 - 450  # capped at 900


def test_test_prompt_model_list_and_hub_test(client, fake):
    ws = signup(client, "Prompt Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    fake.on("POST", r"/v1/responses$", response_ok(text="Hello from GPT", tokens_in=12, tokens_out=5))
    answered = client.post(f"{API}/ai-models/openai/test-prompt", json={"prompt": "Say hello to bob@example.com", "feature": "summaries"}, headers=h)
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["text"] == "Hello from GPT" and body["model"] == "gpt-5-mini" and body["redacted"] is True
    assert body["usage"]["input_tokens"] == 12 and body["usage"]["cost"] is not None
    assert "bob@example.com" not in fake.bodies("/v1/responses")[-1]["input"]
    usage = client.get(f"{API}/ai-models/usage", headers=h).json()
    assert usage["by_feature"][0]["label"] == "Test prompts"

    fake.on("GET", r"/v1/models$", {"data": [{"id": "gpt-5-mini"}, {"id": "gpt-6-luna"}]})
    models = client.get(f"{API}/ai-models/openai/models", params={"refresh": True}, headers=h).json()
    assert models["models"] == ["gpt-5-mini", "gpt-6-luna"] and models["configured"] == ["gpt-5-mini"]
    tested = client.post(f"{API}/integration-hub/openai/test", headers=h)
    assert tested.status_code == 200 and "2 text models" in tested.json()["message"]
    assert settings(client, h, "openai", feature_models={"drafts": "gpt-4.1-mini"}).status_code == 200
    missing = client.post(f"{API}/integration-hub/openai/test", headers=h)
    assert missing.status_code == 409 and "gpt-4.1-mini" in missing.json()["detail"]


def test_members_cannot_manage_and_workspaces_are_isolated(client, fake):
    ws = signup(client, "Admin Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    fake.on("POST", r"/v1/responses$", response_ok())
    person = contact(client, h)
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).status_code == 200

    email = f"member-{uuid.uuid4().hex[:8]}@models.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Mia Member", "role": "member"}, headers=h).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    member = {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}
    assert settings(client, member, "openai", temperature=1).status_code == 403
    assert client.post(f"{API}/ai-models/openai/test-prompt", json={"prompt": "hi"}, headers=member).status_code == 403
    assert client.get(f"{API}/ai-models/openai/models", headers=member).status_code == 403
    assert client.post(f"{API}/integration-hub/openai/test", headers=member).status_code == 403
    mine = client.get(f"{API}/ai-models/usage", headers=member).json()
    assert mine["only_mine"] is True and mine["totals"]["requests"] == 0  # the owner's calls aren't theirs
    member_view = client.get(f"{API}/integration-hub/openai", headers=member).json()
    assert OPENAI_KEY not in json.dumps(member_view) and member_view["extra"]["usage"]["totals"]["requests"] == 0

    other = signup(client, "Other Co")
    assert client.get(f"{API}/ai-models/usage", headers=other["headers"]).json()["totals"]["requests"] == 0
    assert client.get(f"{API}/ai-models/status", headers=other["headers"]).json()["workspace_model"] is False
    assert client.post(f"{API}/ai-models/openai/test-prompt", json={"prompt": "hi"}, headers=other["headers"]).status_code == 409


# ---------------------------------------------------------------------------
# Azure OpenAI
# ---------------------------------------------------------------------------


def test_azure_entra_tokens_are_cached_and_one_provider_is_active(client, fake):
    ws = signup(client, "Azure Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)
    on = client.put(f"{API}/integration-hub/azure_openai/toggle", json={"enabled": True}, headers=h)
    assert on.status_code == 200 and on.json()["extra"]["other"] == {"key": "openai", "name": "OpenAI", "enabled": False}
    assert client.get(f"{API}/integration-hub/openai", headers=h).json()["switched_on"] is False

    tokens: list[dict] = []

    def token(request):
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        tokens.append(form)
        return {"token_type": "Bearer", "expires_in": 3599, "access_token": f"entra-token-{len(tokens)}"}

    fake.on("POST", rf"login\.microsoftonline\.com/{TENANT}/oauth2/v2\.0/token$", token)
    fake.on("GET", r"contoso-ai\.openai\.azure\.com/openai/v1/models$", {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4.1"}]})
    rejected = client.put(f"{API}/integration-hub/azure_openai/credentials", json={"values": {"endpoint": "https://contoso.example.com", "api_key": AZURE_KEY}}, headers=h)
    assert rejected.status_code == 422 and not fake.sent("example.com")
    missing = client.put(f"{API}/integration-hub/azure_openai/credentials", json={"values": {"endpoint": AZURE, "tenant_id": TENANT}}, headers=h)
    assert missing.status_code == 422
    connected = client.put(
        f"{API}/integration-hub/azure_openai/credentials",
        json={"values": {"endpoint": "contoso-ai.openai.azure.com", "tenant_id": TENANT, "client_id": APP_ID, "client_secret": ENTRA_SECRET}},
        headers=h,
    )
    assert connected.status_code == 200, connected.text
    assert ENTRA_SECRET not in connected.text
    extra = connected.json()["extra"]
    assert extra["auth"] == "entra" and extra["endpoint"] == AZURE and extra["state"]["reason"] == "no_model"
    assert tokens[0]["scope"] == "https%3A%2F%2Fcognitiveservices.azure.com%2F.default" and tokens[0]["grant_type"] == "client_credentials"

    assert settings(client, h, "azure_openai", default_model="crm-mini", feature_models={"summaries": "crm-large"}).status_code == 200
    fake.on("POST", r"contoso-ai\.openai\.azure\.com/openai/v1/chat/completions$", lambda r: chat_ok(model="gpt-4o-mini-2024-07-18" if json.loads(r.content)["model"] == "crm-mini" else "gpt-4.1-2025-04-14"))
    person = contact(client, h)
    for _ in range(2):
        draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()
        assert draft["source"] == "azure_openai" and draft["model"] == "crm-mini" and draft["subject"] == "Hello"
    summary = client.get(f"{API}/ai/summary/contact/{person['id']}", headers=h).json()
    assert summary["source"] == "azure_openai" and summary["model"] == "crm-large"
    calls = fake.sent("/openai/v1/chat/completions")
    assert len(calls) == 3 and all(c.headers["authorization"] == "Bearer entra-token-2" for c in calls) and "api-key" not in calls[0].headers
    assert len(tokens) == 2  # one when checking the credentials, one for all three calls
    body = json.loads(calls[0].content)
    assert body["model"] == "crm-mini" and body["messages"][0]["role"] == "system" and "[email]" in body["messages"][1]["content"]
    assert body["max_completion_tokens"] == 450 and "temperature" not in body and "reasoning_effort" not in body

    # An expired token is fetched again.
    from app.integrations.providers import _ai_client

    for name, (value, _expires) in list(_ai_client._TOKENS.items()):
        _ai_client._TOKENS[name] = (value, 0)
    client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h)
    assert len(tokens) == 3 and fake.sent("/chat/completions")[-1].headers["authorization"] == "Bearer entra-token-3"

    # Cost uses the price of the model each deployment runs.
    usage = client.get(f"{API}/ai-models/usage", headers=h).json()
    models = {m["model"]: m for m in usage["by_model"]}
    assert models["crm-mini"]["provider"] == "azure_openai" and models["crm-mini"]["price"] == [0.15, 0.6]
    assert models["crm-large"]["price"] == [2.0, 8.0]

    # The live test checks every deployment the features use.
    tested = client.post(f"{API}/integration-hub/azure_openai/test", headers=h)
    assert tested.status_code == 200, tested.text
    assert "crm-large" in tested.json()["message"] and "Microsoft Entra ID" in tested.json()["message"]
    fake.on("POST", r"/openai/v1/chat/completions$", lambda r: httpx.Response(404, json={"error": {"code": "DeploymentNotFound", "message": "The API deployment for this resource does not exist."}})
            if json.loads(r.content)["model"] == "crm-large" else chat_ok())
    broken = client.post(f"{API}/integration-hub/azure_openai/test", headers=h)
    assert broken.status_code == 409 and "crm-large" in broken.json()["detail"]

    # Disconnecting forgets the cached token.
    assert client.delete(f"{API}/integration-hub/azure_openai/connection", headers=h).status_code == 200
    assert not any(name.startswith(ws["org"]) for name in _ai_client._TOKENS)


def test_azure_api_key_and_classic_deployments_route(client, fake):
    ws = signup(client, "Azure Key Co")
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/azure_openai/toggle", json={"enabled": True}, headers=h).status_code == 200
    assert settings(client, h, "azure_openai", api_mode="deployments", api_version="2024-10-21", default_model="crm-mini", temperature=0.3).status_code == 200
    fake.on("GET", r"contoso-ai\.openai\.azure\.com/openai/models\?api-version=2024-10-21$", {"data": [{"id": "gpt-4o-mini"}]})
    connected = client.put(f"{API}/integration-hub/azure_openai/credentials", json={"values": {"endpoint": AZURE, "api_key": AZURE_KEY}}, headers=h)
    assert connected.status_code == 200, connected.text
    assert connected.json()["extra"]["auth"] == "key" and AZURE_KEY not in connected.text
    fake.on("POST", r"/openai/deployments/crm-mini/chat/completions\?api-version=2024-10-21$", chat_ok())
    person = contact(client, h)
    draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()
    assert draft["source"] == "azure_openai"
    call = fake.sent("/openai/deployments/crm-mini/chat/completions")[-1]
    assert call.headers["api-key"] == AZURE_KEY and "authorization" not in call.headers
    body = json.loads(call.content)
    assert "model" not in body and body["temperature"] == 0.3

    fake.on("POST", r"/chat/completions", {"choices": [{"finish_reason": "content_filter", "message": {"role": "assistant", "content": None}}], "usage": {"prompt_tokens": 5, "completion_tokens": 0}})
    assert client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()["source"] == "rules"


# ---------------------------------------------------------------------------
# Every AI feature routes
# ---------------------------------------------------------------------------


def test_agents_conversations_and_field_prompts_use_the_workspace_model(client, fake):
    ws = signup(client, "Everywhere Co")
    h = ws["headers"]
    connect_openai(client, ws, fake)

    def answer(request):
        prompt = json.loads(request.content)["input"]
        if "Reply with JSON only: {\"summary\"" in prompt:
            return response_ok(text='{"summary": "Nora wants a proposal.", "action_items": ["Sam: send proposal"]}')
        if "\"value\"" in prompt:
            return response_ok(text='{"value": "$40,000", "evidence": "Our budget is about 40,000 dollars"}')
        return response_ok(text="Agent note.")

    fake.on("POST", r"/v1/responses$", answer)
    account = client.post(f"{API}/accounts", json={"name": "Quinn Industries"}, headers=h).json()
    deal = client.post(f"{API}/opportunities", json={"name": "Quinn CRM", "account_id": account["id"], "amount": 1000}, headers=h).json()
    assert client.post(f"{API}/field-prompts", json={"name": "Budget", "object_type": "opportunity", "field_key": "amount", "prompt": "What budget did they mention?", "keywords": ["budget"]}, headers=h).status_code == 201
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:06.000\n<v Sam Owner>What's your budget?\n\n2\n00:00:06.500 --> 00:00:15.000\n<v Nora Quinn>Our budget is about 40,000 dollars.\n"
    created = client.post(f"{API}/conversations", json={"title": "Discovery", "transcript_text": vtt, "account_id": account["id"], "opportunity_id": deal["id"]}, headers=h)
    assert created.status_code == 201, created.text
    conversation = created.json()
    assert conversation["summary_source"] == "openai" and conversation["summary"] == "Nora wants a proposal."
    suggestion = next(s for s in conversation["field_suggestions"] if s["field_key"] == "amount")
    assert suggestion["source"] == "openai" and float(suggestion["value"]) == 40000
    assert client.get(f"{API}/field-prompts", headers=h).json()["ai"] is True

    async def agent_call():
        from app.db.session import AsyncSessionLocal
        from app.services import ai

        async with AsyncSessionLocal() as db:
            token = ai.current_scope.set(ai.AiScope(uuid.UUID(ws["org"]), "agents", None, db))
            try:
                return await ai.generate("Suggest a follow-up for a stale deal.", feature="agents")
            finally:
                ai.current_scope.reset(token)

    reply = run(agent_call())
    assert reply.source == "openai" and reply.text == "Agent note."
    usage = client.get(f"{API}/ai-models/usage", headers=h).json()
    assert {f["feature"] for f in usage["by_feature"]} >= {"conversations", "field_prompts", "agents"}
    agents = client.get(f"{API}/ai/agents", headers=h).json()
    assert agents["claude"] is True and agents["model"]["source"] == "openai"


def test_without_a_workspace_model_nothing_changes(client, fake):
    ws = signup(client, "Plain Co")
    h = ws["headers"]
    person = contact(client, h)
    draft = client.post(f"{API}/ai/draft", json={"object_type": "contact", "record_id": person["id"]}, headers=h).json()
    assert draft["source"] in ("rules", "claude") and not fake.requests
    status_now = client.get(f"{API}/ai-models/status", headers=h).json()
    assert status_now["workspace_model"] is False and status_now["default"]["reason"] == "off"
    assert client.get(f"{API}/ai-usage", headers=h).json()["provider_usage"] is None


def test_old_usage_is_pruned(client, fake):
    ws = signup(client, "Prune Co")
    connect_openai(client, ws, fake)

    async def main():
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app.db.session import AsyncSessionLocal
        from app.integrations.providers import ai_models
        from app.models.integration_hub import IntegrationObject

        org = uuid.UUID(ws["org"])
        old, recent = uuid.uuid4(), uuid.uuid4()
        async with AsyncSessionLocal() as db:
            for row_id, days in ((old, 500), (recent, 20)):
                db.add(IntegrationObject(id=row_id, organization_id=org, provider="openai", object_type="ai_call", external_id=row_id.hex,
                                         occurred_at=datetime.now(timezone.utc) - timedelta(days=days), data={"feature": "drafts", "input_tokens": 1, "output_tokens": 1}))
            await db.commit()
        ai_models._PRUNED_AT = -1e9
        async with AsyncSessionLocal() as db:
            assert await ai_models.run_due(db) >= 1
            assert await ai_models.run_due(db) == 0  # once a day
            left = set((await db.execute(select(IntegrationObject.id).where(IntegrationObject.id.in_((old, recent))))).scalars())
        assert left == {recent}

    run(main())
