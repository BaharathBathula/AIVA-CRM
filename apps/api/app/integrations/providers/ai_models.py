"""OpenAI API and Azure OpenAI: AIVA's AI features on the workspace's own models.

How a request is routed (``run``, called from ``services.ai.generate``):

1. The workspace has at most one AI model provider switched on (turning one on
   turns the other off). Nothing on → AIVA's default (Claude when the server has
   ``ANTHROPIC_API_KEY``, otherwise the built-in rules) exactly as before.
2. The plan must include ``ai_models``, the provider must be connected, and the
   feature must map to a model (its own, or the default). A feature can also be
   left on AIVA's default.
3. HIPAA workspaces only use the provider after an admin acknowledges a BAA
   covers it; until then AIVA's default is used.
4. The monthly token or spend limit is checked; at the limit the "at limit"
   policy applies (AIVA's default, or built-in rules only).
5. The call runs with the configured timeout, retries 429/5xx within it, and is
   bounded by the output cap. On failure the "on error" policy applies.

Every call (success or failure) is recorded as an ``IntegrationObject`` of type
``ai_call`` (feature, model, person, tokens, latency) in its own transaction, so
usage survives the caller rolling back. Prompts and answers are never stored or
logged. Cost is estimated when read, from the editable price table.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import BigInteger, cast, delete, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _ai_client as client
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationToggle

logger = logging.getLogger("aiva.integrations.ai_models")

KEYS = ("openai", "azure_openai")
NAMES = {"openai": "OpenAI", "azure_openai": "Azure OpenAI"}
OBJECT = "ai_call"
PLAN_FEATURE = "ai_models"

# The AI features that can run on a workspace model (keys match AI word metering).
FEATURES: dict[str, tuple[str, str]] = {
    "summaries": ("Record summaries", "Summaries on leads, contacts, accounts, deals and cases."),
    "drafts": ("Email and case drafts", "Drafted emails and case replies."),
    "ask": ("Ask AIVA", "Answers to questions about your CRM data."),
    "agents": ("AI agents", "Lead qualifier, deal coach, case triage and follow-up writer suggestions."),
    "conversations": ("Call and conversation notes", "Summaries and action items from calls and meetings."),
    "field_prompts": ("Field prompts", "CRM field values picked out of what was said in a call."),
}
TEST_FEATURE = "test"
USE_AIVA = "aiva"  # a feature mapped to this keeps AIVA's default model

EFFORTS = ("none", "minimal", "low", "medium", "high")
# Extra output allowance for reasoning models: their thinking counts against the cap.
HEADROOM = {"": 2048, "none": 0, "minimal": 256, "low": 1024, "medium": 2048, "high": 4096}
POLICIES = ("aiva_default", "rules_only")
LIMIT_TYPES = ("none", "tokens", "spend")
REGIONS = ("default", "eu")
API_MODES = ("v1", "deployments")

# Picked as the default model when OpenAI is connected (first one the key can use).
PREFERRED_DEFAULTS = ("gpt-6-luna", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini", "gpt-6.1-sol", "gpt-5", "gpt-4.1", "gpt-4o")

# USD per 1M tokens (input, output): OpenAI's standard list prices. Editable per
# workspace; Azure prices depend on the deployment type and region, so a deployment
# without its own price uses the price of the model it runs, when known.
DEFAULT_PRICES: dict[str, tuple[str, str]] = {
    "gpt-6-astra": ("10.00", "50.00"),
    "gpt-6.1-sol": ("2.00", "10.00"),
    "gpt-6-luna": ("0.10", "0.50"),
    "gpt-5": ("1.25", "10.00"),
    "gpt-5-mini": ("0.25", "2.00"),
    "gpt-5-nano": ("0.05", "0.40"),
    "gpt-4.1": ("2.00", "8.00"),
    "gpt-4.1-mini": ("0.40", "1.60"),
    "gpt-4.1-nano": ("0.10", "0.40"),
    "gpt-4o": ("2.50", "10.00"),
    "gpt-4o-mini": ("0.15", "0.60"),
    "o3": ("2.00", "8.00"),
    "o4-mini": ("1.10", "4.40"),
}

DEFAULTS: dict[str, Any] = {
    "default_model": "",
    "feature_models": {},
    "temperature": None,
    "max_output_tokens": 2000,
    "reasoning_effort": "low",
    "timeout_seconds": 30,
    "limit_type": "none",
    "monthly_token_limit": None,
    "monthly_spend_limit": None,
    "at_limit": "aiva_default",
    "on_error": "aiva_default",
    "redact_emails": True,
    "redact_phones": True,
    "hipaa_acknowledged": False,
    "hipaa_acknowledged_at": None,
    "hipaa_acknowledged_by": None,
    "prices": {},
    "region": "default",
    "api_mode": "v1",
    "api_version": client.AZURE_DEFAULT_API_VERSION,
}
SETTING_KEYS = frozenset(DEFAULTS) - {"hipaa_acknowledged_at", "hipaa_acknowledged_by"}
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
OPENAI_ORG = re.compile(r"^org-[A-Za-z0-9]{1,64}$")
OPENAI_PROJECT = re.compile(r"^proj_[A-Za-z0-9]{1,64}$")
RETENTION_DAYS = 400


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def period_key(moment: datetime | None = None) -> str:
    return (moment or utcnow()).strftime("%Y-%m")


def month_bounds(period: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(period, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise IntegrationError("Use a month like 2026-10.", 422) from exc
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def other(key: str) -> str:
    return "azure_openai" if key == "openai" else "openai"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def settings_for(config: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """The provider's settings with defaults filled in (stored values are already clean)."""

    stored = config or {}
    out = {name: stored.get(name, default) for name, default in DEFAULTS.items()}
    out["feature_models"] = {f: str(v) for f, v in (stored.get("feature_models") or {}).items() if f in FEATURES and v}
    out["prices"] = dict(stored.get("prices") or {})
    if key == "azure_openai" and "reasoning_effort" not in stored:
        out["reasoning_effort"] = ""  # deployments may not be reasoning models
    return out


def prices_for(settings: dict[str, Any]) -> dict[str, tuple[Decimal, Decimal]]:
    table = {name: (Decimal(i), Decimal(o)) for name, (i, o) in DEFAULT_PRICES.items()}
    for name, value in (settings.get("prices") or {}).items():
        try:
            table[name] = (Decimal(str(value["input"])), Decimal(str(value["output"])))
        except (KeyError, TypeError, InvalidOperation):
            continue
    return table


def price_of(table: dict[str, tuple[Decimal, Decimal]], *names: str | None) -> tuple[Decimal, Decimal] | None:
    """Exact name first (a deployment, a model), then the longest known model the name starts with
    (so ``gpt-4o-mini-2024-07-18`` is priced as ``gpt-4o-mini``)."""

    for name in names:
        if name and name in table:
            return table[name]
    for name in names:
        if not name:
            continue
        lowered = name.lower()
        matches = [k for k in table if lowered.startswith(k.lower()) and (len(lowered) == len(k) or lowered[len(k)] in "-:._")]
        if matches:
            return table[max(matches, key=len)]
    return None


def _cost(price: tuple[Decimal, Decimal] | None, input_tokens: int, output_tokens: int) -> Decimal | None:
    if price is None:
        return None
    return (price[0] * input_tokens + price[1] * output_tokens) / Decimal(1_000_000)


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise IntegrationError(f"{name} must be on or off.", 422)


def _int(value: Any, name: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise IntegrationError(f"{name} must be a whole number.", 422) from exc
    if not low <= number <= high:
        raise IntegrationError(f"{name} must be between {low:,} and {high:,}.", 422)
    return number


def _money(value: Any, name: str, high: Decimal) -> str:
    try:
        amount = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation as exc:
        raise IntegrationError(f"{name} must be an amount in US dollars.", 422) from exc
    if not amount.is_finite() or amount < 0 or amount > high:
        raise IntegrationError(f"{name} must be between 0 and {high:,}.", 422)
    return str(amount.quantize(Decimal("0.0001")))


def _model_name(value: Any, what: str) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    if not client.NAME.fullmatch(name):
        raise IntegrationError(f"“{name[:60]}” isn't a valid {what}.", 422)
    return name


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    """Validates the options an admin saves. Returns only what was sent, cleaned."""

    if personal:
        raise IntegrationError(f"{NAMES[key]} is set up for the whole workspace.", 400)
    what = "model" if key == "openai" else "deployment name"
    unknown = sorted(set(config) - SETTING_KEYS)
    if unknown:
        raise IntegrationError(f"Unknown setting: {unknown[0]}.", 422)
    out: dict[str, Any] = {}
    if "default_model" in config:
        out["default_model"] = _model_name(config["default_model"], what)
    if "feature_models" in config:
        raw = config["feature_models"] or {}
        if not isinstance(raw, dict):
            raise IntegrationError("Feature models must map each feature to a model.", 422)
        mapped: dict[str, str] = {}
        for feature, value in raw.items():
            if feature not in FEATURES:
                raise IntegrationError(f"Unknown AI feature: {feature}.", 422)
            choice = str(value or "").strip()
            mapped[feature] = USE_AIVA if choice == USE_AIVA else _model_name(choice, what)
        out["feature_models"] = {f: v for f, v in mapped.items() if v}
    if "temperature" in config:
        value = config["temperature"]
        if value in (None, ""):
            out["temperature"] = None
        else:
            try:
                number = round(float(value), 2)
            except (TypeError, ValueError) as exc:
                raise IntegrationError("Temperature must be a number between 0 and 2.", 422) from exc
            if not 0 <= number <= 2:
                raise IntegrationError("Temperature must be between 0 and 2.", 422)
            out["temperature"] = number
    if "max_output_tokens" in config:
        out["max_output_tokens"] = _int(config["max_output_tokens"], "The output limit", 256, 32000)
    if "reasoning_effort" in config:
        effort = str(config["reasoning_effort"] or "")
        if effort and effort not in EFFORTS:
            raise IntegrationError("Reasoning effort must be none, minimal, low, medium or high (or the model's default).", 422)
        out["reasoning_effort"] = effort
    if "timeout_seconds" in config:
        out["timeout_seconds"] = _int(config["timeout_seconds"], "The timeout", 5, 120)
    if "limit_type" in config:
        if config["limit_type"] not in LIMIT_TYPES:
            raise IntegrationError("The monthly limit must be none, tokens or spend.", 422)
        out["limit_type"] = config["limit_type"]
    if "monthly_token_limit" in config:
        value = config["monthly_token_limit"]
        out["monthly_token_limit"] = None if value in (None, "") else _int(value, "The monthly token limit", 1000, 100_000_000_000)
    if "monthly_spend_limit" in config:
        value = config["monthly_spend_limit"]
        out["monthly_spend_limit"] = None if value in (None, "") else _money(value, "The monthly spend limit", Decimal(10_000_000))
    for name in ("at_limit", "on_error"):
        if name in config:
            if config[name] not in POLICIES:
                raise IntegrationError("Choose AIVA's default model or built-in rules only.", 422)
            out[name] = config[name]
    for name, label in (("redact_emails", "Redact email addresses"), ("redact_phones", "Redact phone numbers")):
        if name in config:
            out[name] = _bool(config[name], label)
    stored = await core.workspace_config(db, principal.organization_id, key)
    if "hipaa_acknowledged" in config and _bool(config["hipaa_acknowledged"], "The HIPAA acknowledgement") != bool(stored.get("hipaa_acknowledged")):
        acknowledged = bool(config["hipaa_acknowledged"])
        out["hipaa_acknowledged"] = acknowledged
        out["hipaa_acknowledged_at"] = utcnow().isoformat() if acknowledged else None
        out["hipaa_acknowledged_by"] = str(principal.user_id) if acknowledged else None
        from app.services.automation import record_audit

        record_audit(
            db, organization_id=principal.organization_id, action="update", entity_type="integration",
            entity_label=f"{NAMES[key]}: HIPAA BAA {'acknowledged' if acknowledged else 'acknowledgement withdrawn'}",
            changes={"hipaa_acknowledged": {"to": acknowledged}},
        )
    if "prices" in config:
        raw = config["prices"] or {}
        if not isinstance(raw, dict) or len(raw) > 60:
            raise IntegrationError("Prices must list up to 60 models.", 422)
        prices: dict[str, dict[str, str]] = {}
        for name, value in raw.items():
            model = _model_name(name, what)
            if not model or value is None:
                continue
            if not isinstance(value, dict):
                raise IntegrationError(f"Give {model} an input and an output price.", 422)
            prices[model] = {
                "input": _money(value.get("input"), f"The input price for {model}", Decimal(1000)),
                "output": _money(value.get("output"), f"The output price for {model}", Decimal(1000)),
            }
        out["prices"] = prices
    if "region" in config:
        if key != "openai" or config["region"] not in REGIONS:
            raise IntegrationError("Data residency must be default or eu (OpenAI only).", 422)
        out["region"] = config["region"]
    if "api_mode" in config:
        if key != "azure_openai" or config["api_mode"] not in API_MODES:
            raise IntegrationError("The Azure API must be v1 or deployments.", 422)
        out["api_mode"] = config["api_mode"]
    if "api_version" in config:
        version = str(config["api_version"] or client.AZURE_DEFAULT_API_VERSION).strip()
        if key != "azure_openai" or not client.API_VERSION.fullmatch(version):
            raise IntegrationError("The API version looks like 2024-10-21 (or 2025-04-01-preview).", 422)
        out["api_version"] = version
    merged = settings_for({**stored, **out}, key)
    if merged["limit_type"] == "tokens" and not merged["monthly_token_limit"]:
        raise IntegrationError("Enter the monthly token limit.", 422)
    if merged["limit_type"] == "spend" and not merged["monthly_spend_limit"]:
        raise IntegrationError("Enter the monthly spend limit.", 422)
    return out


# ---------------------------------------------------------------------------
# Data controls
# ---------------------------------------------------------------------------

EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w/:.-])\+?(?:\d[\s().-]{0,3}){9,15}\d(?![\w/-])")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ENCRYPTED = re.compile(r"enc:v1:[A-Za-z0-9_\-=]+")
MASKED = re.compile(r"•{3,}\S{0,4}")


def _phone(match: re.Match[str]) -> str:
    text = match.group(0)
    digits = sum(ch.isdigit() for ch in text)
    if digits < 10 or digits > 15 or ISO_DATE.search(text):
        return text
    return "[phone]"


def scrub(text: str, *, emails: bool, phones: bool) -> str:
    """Encrypted and masked values never leave AIVA; emails and phone numbers are
    replaced when the workspace asks for it."""

    text = ENCRYPTED.sub("[encrypted]", text)
    text = MASKED.sub("[hidden]", text)
    if emails:
        text = EMAIL.sub("[email]", text)
    if phones:
        text = PHONE.sub(_phone, text)
    return text


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@dataclass
class Route:
    key: str
    connection: IntegrationConnection
    settings: dict[str, Any]
    model: str
    feature: str


@dataclass
class Decision:
    key: str | None
    reason: str  # ready | off | plan | not_connected | no_model | aiva | hipaa | limit
    route: Route | None = None
    policy: str = "aiva_default"
    settings: dict[str, Any] = field(default_factory=dict)


REASONS = {
    "ready": "AI features use this provider.",
    "off": "No AI model provider is switched on.",
    "plan": "Bring-your-own AI models isn't included in this workspace's plan.",
    "not_connected": "The provider isn't connected.",
    "no_model": "Choose a default model (or deployment) for AI features.",
    "aiva": "This feature uses AIVA's default model.",
    "hipaa": "This workspace is in HIPAA mode: an admin must confirm a BAA covers this provider before CRM data is sent to it.",
    "limit": "This month's usage limit has been reached.",
}


def target_for(key: str, connection: IntegrationConnection, settings: dict[str, Any]) -> client.Target:
    config = connection.config or {}
    cache = str(connection.organization_id)
    if key == "openai":
        return client.Target(
            "openai", api_key=core.secret(connection, "api_key"), organization=config.get("organization") or None,
            project=config.get("project") or None, region=settings["region"], cache_key=cache,
        )
    return client.Target(
        "azure_openai", api_key=core.secret(connection, "api_key"), endpoint=config.get("endpoint"),
        api_mode=settings["api_mode"], api_version=settings["api_version"], tenant_id=config.get("tenant_id") or None,
        client_id=config.get("client_id") or None, client_secret=core.secret(connection, "client_secret"), cache_key=cache,
    )


async def active_toggle(db: AsyncSession, organization_id: uuid.UUID) -> IntegrationToggle | None:
    rows = (
        await db.execute(
            select(IntegrationToggle).where(
                IntegrationToggle.organization_id == organization_id, IntegrationToggle.provider.in_(KEYS), IntegrationToggle.enabled.is_(True)
            )
        )
    ).scalars().all()
    if not rows:
        return None
    return max(rows, key=lambda r: r.updated_at or datetime.min.replace(tzinfo=timezone.utc))


async def _hipaa(db: AsyncSession, organization_id: uuid.UUID, features: frozenset[str]) -> bool:
    from app.models.organization import Organization

    organization = await db.get(Organization, organization_id)
    return bool(organization and (organization.settings or {}).get("hipaa_mode")) and "hipaa" in features


async def decide(db: AsyncSession, organization_id: uuid.UUID, feature: str | None, *, check_limit: bool = True) -> Decision:
    """Which model a feature uses right now, and why."""

    from app.integrations.providers.lead_capture import plan_for

    toggle = await active_toggle(db, organization_id)
    if toggle is None:
        return Decision(None, "off")
    key = toggle.provider
    settings = settings_for(toggle.config, key)
    features, _ = await plan_for(db, organization_id)
    if PLAN_FEATURE not in features:
        return Decision(key, "plan", settings=settings)
    connection = await core.connection_for(db, organization_id, key)
    if connection is None or connection.status == "needs_reauth":
        return Decision(key, "not_connected", settings=settings)
    choice = settings["feature_models"].get(feature or "", "") if feature in FEATURES else ""
    if choice == USE_AIVA:
        return Decision(key, "aiva", settings=settings)
    model = choice or settings["default_model"]
    if not model:
        return Decision(key, "no_model", settings=settings)
    if await _hipaa(db, organization_id, features) and not settings["hipaa_acknowledged"]:
        return Decision(key, "hipaa", settings=settings)
    if check_limit and settings["limit_type"] != "none":
        state = await limit_state(db, organization_id, settings)
        if state["reached"]:
            return Decision(key, "limit", policy=settings["at_limit"], settings=settings)
    return Decision(key, "ready", Route(key, connection, settings, model, feature or "default"), settings=settings)


@dataclass
class Outcome:
    text: str | None = None
    key: str | None = None
    model: str | None = None
    # None: no workspace model applies (use AIVA's default as before);
    # "aiva_default" / "rules_only": the workspace model didn't answer, follow this policy.
    fallback: str | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


def output_budget(route: Route, requested: int) -> int:
    """What the model may write: the feature's need plus room to reason, within the cap."""

    cap = int(route.settings["max_output_tokens"])
    visible = max(16, min(int(requested), cap))
    effort = route.settings["reasoning_effort"] or ""
    reasoning = client.is_reasoning_model(route.model) or (route.key == "azure_openai" and bool(effort))
    return min(cap, visible + (HEADROOM.get(effort, 1024) if reasoning else 0))


async def call(route: Route, *, system: str, prompt: str, max_tokens: int) -> client.Generation:
    settings = route.settings
    target = target_for(route.key, route.connection, settings)
    clean_prompt = scrub(prompt, emails=settings["redact_emails"], phones=settings["redact_phones"])
    clean_system = scrub(system, emails=False, phones=False)
    timeout = float(settings["timeout_seconds"])
    return await asyncio.wait_for(
        client.generate(
            target, model=route.model, system=clean_system, prompt=clean_prompt, max_tokens=output_budget(route, max_tokens),
            temperature=settings["temperature"], reasoning_effort=settings["reasoning_effort"] or None, timeout=timeout,
        ),
        timeout=timeout + 1,
    )


async def run(
    db: AsyncSession | None, organization_id: uuid.UUID, feature: str, *, system: str, prompt: str, max_tokens: int, user_id: uuid.UUID | None
) -> Outcome:
    """Runs a feature on the workspace's model when one applies. Never raises."""

    try:
        if db is None:
            from app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as own:
                return await _run(own, organization_id, feature, system=system, prompt=prompt, max_tokens=max_tokens, user_id=user_id)
        with db.no_autoflush:
            return await _run(db, organization_id, feature, system=system, prompt=prompt, max_tokens=max_tokens, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - AI must never break the feature that asked
        logger.warning("AI model routing failed (%s); using AIVA's default model.", type(exc).__name__)
        return Outcome()


async def _run(db: AsyncSession, organization_id: uuid.UUID, feature: str, *, system: str, prompt: str, max_tokens: int, user_id: uuid.UUID | None) -> Outcome:
    decision = await decide(db, organization_id, feature)
    if decision.reason == "limit":
        await _note_once(organization_id, decision.key or "openai", "limit_reached",
                         f"Monthly AI usage limit reached; AI features now use {'built-in rules' if decision.policy == 'rules_only' else 'AIVA’s default model'} until next month.")
        return Outcome(fallback=decision.policy, error=REASONS["limit"])
    route = decision.route
    if route is None:
        return Outcome()
    error: client.AiCallError | None = None
    generation: client.Generation | None = None
    started = time.monotonic()
    try:
        generation = await call(route, system=system, prompt=prompt, max_tokens=max_tokens)
    except client.AiCallError as exc:
        error = exc
    except asyncio.TimeoutError:
        error = client.AiCallError(f"{NAMES[route.key]} didn't answer within {route.settings['timeout_seconds']} seconds.", kind="timeout")
    except IntegrationError as exc:
        error = client.AiCallError(str(exc), kind="bad_request")
    usage = await record(organization_id, route, feature, user_id, generation, error, latency_ms=int((time.monotonic() - started) * 1000))
    if generation is not None:
        _touch(route.connection)
        return Outcome(generation.text, route.key, route.model, usage=usage)
    assert error is not None
    if error.kind == "auth":
        route.connection.status = "error"
        route.connection.last_error = error.message[:500]
    await _note_once(organization_id, route.key, f"call_failed:{error.kind}", f"{FEATURES.get(feature, (feature,))[0]}: {error.message}", level="warning")
    return Outcome(fallback=route.settings["on_error"], error=error.message, key=route.key, model=route.model, usage=usage)


_TOUCHED: dict[uuid.UUID, float] = {}


def _touch(connection: IntegrationConnection) -> None:
    now = time.monotonic()
    if now - _TOUCHED.get(connection.id, 0) > 600:
        _TOUCHED[connection.id] = now
        connection.last_used_at = utcnow()
        if connection.status == "error":
            connection.status = "connected"
            connection.last_error = None


_NOTED: dict[tuple[uuid.UUID, str, str], float] = {}


async def _note_once(organization_id: uuid.UUID, key: str, action: str, message: str, *, level: str = "warning") -> None:
    """An activity-log line, at most once every 15 minutes per kind (never the prompt)."""

    marker = (organization_id, key, action)
    now = time.monotonic()
    if now - _NOTED.get(marker, -1e9) < 900:
        return
    _NOTED[marker] = now
    from app.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            core.log(session, organization_id, key, action.split(":")[0], message[:500], level=level)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Couldn't write the AI model activity log (%s).", type(exc).__name__)


async def record(
    organization_id: uuid.UUID, route: Route, feature: str, user_id: uuid.UUID | None,
    generation: client.Generation | None, error: client.AiCallError | None, *, latency_ms: int,
) -> dict[str, Any]:
    """Stores one call's usage (never the prompt or answer) in its own transaction."""

    from app.db.session import AsyncSessionLocal

    data = {
        "feature": feature,
        "model": route.model,
        "base_model": generation.model if generation else None,
        "user_id": str(user_id) if user_id else None,
        "input_tokens": generation.input_tokens if generation else 0,
        "output_tokens": generation.output_tokens if generation else 0,
        "reasoning_tokens": generation.reasoning_tokens if generation else 0,
        "latency_ms": generation.latency_ms if generation else latency_ms,
        "attempts": generation.attempts if generation else None,
        "error_kind": error.kind if error else None,
        "error": error.message[:300] if error else None,
    }
    now = utcnow()
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                IntegrationObject(
                    id=uuid.uuid4(), organization_id=organization_id, provider=route.key, object_type=OBJECT,
                    external_id=uuid.uuid4().hex, external_parent=period_key(now), title=FEATURES.get(feature, (feature,))[0][:500],
                    status="ok" if generation else "error", occurred_at=now, data=data,
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - usage bookkeeping must not break the answer
        logger.warning("Couldn't record AI model usage (%s).", type(exc).__name__)
    price = price_of(prices_for(route.settings), route.model, data["base_model"])
    cost = _cost(price, data["input_tokens"], data["output_tokens"])
    return {**{k: data[k] for k in ("input_tokens", "output_tokens", "reasoning_tokens", "latency_ms")}, "cost": float(cost) if cost is not None else None}


# ---------------------------------------------------------------------------
# Usage and limits
# ---------------------------------------------------------------------------


async def _usage_rows(db: AsyncSession, organization_id: uuid.UUID, period: str) -> list[tuple[Any, ...]]:
    start, end = month_bounds(period)
    data = IntegrationObject.data
    tokens_in = cast(data["input_tokens"].astext, BigInteger)
    tokens_out = cast(data["output_tokens"].astext, BigInteger)
    # Grouped by output column name: bound JSON keys would differ between SELECT and GROUP BY.
    dims = [
        IntegrationObject.provider.label("u_provider"), data["model"].astext.label("u_model"), data["base_model"].astext.label("u_base"),
        data["feature"].astext.label("u_feature"), data["user_id"].astext.label("u_user"), IntegrationObject.status.label("u_status"),
    ]
    query = (
        select(*dims, func.count(), func.coalesce(func.sum(tokens_in), 0), func.coalesce(func.sum(tokens_out), 0))
        .where(
            IntegrationObject.organization_id == organization_id, IntegrationObject.provider.in_(KEYS), IntegrationObject.object_type == OBJECT,
            IntegrationObject.occurred_at >= start, IntegrationObject.occurred_at < end,
        )
        .group_by(*[literal_column(d.name) for d in dims])
    )
    return list((await db.execute(query)).all())


async def _price_tables(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, dict[str, tuple[Decimal, Decimal]]]:
    return {key: prices_for(settings_for(await core.workspace_config(db, organization_id, key), key)) for key in KEYS}


async def month_totals(db: AsyncSession, organization_id: uuid.UUID, period: str | None = None) -> dict[str, Any]:
    tables = await _price_tables(db, organization_id)
    tokens, cost, unpriced = 0, Decimal(0), 0
    for provider, model, base, _f, _u, _s, count, t_in, t_out in await _usage_rows(db, organization_id, period or period_key()):
        tokens += int(t_in) + int(t_out)
        price = price_of(tables[provider], model, base)
        if price is None:
            unpriced += int(count) if (int(t_in) + int(t_out)) else 0
        else:
            cost += _cost(price, int(t_in), int(t_out)) or 0
    return {"tokens": tokens, "cost": cost, "unpriced_calls": unpriced}


async def limit_state(db: AsyncSession, organization_id: uuid.UUID, settings: dict[str, Any]) -> dict[str, Any]:
    """The month's use against the limit. The limit covers both providers (the
    workspace's own models), so switching provider mid-month doesn't reset it."""

    kind = settings["limit_type"]
    totals = await month_totals(db, organization_id)
    if kind == "tokens" and settings["monthly_token_limit"]:
        limit = int(settings["monthly_token_limit"])
        used: Any = totals["tokens"]
        return {"type": kind, "limit": limit, "used": used, "remaining": max(0, limit - used), "reached": used >= limit, "unpriced_calls": totals["unpriced_calls"]}
    if kind == "spend" and settings["monthly_spend_limit"]:
        limit_d = Decimal(str(settings["monthly_spend_limit"]))
        spent = totals["cost"]
        return {
            "type": kind, "limit": float(limit_d), "used": round(float(spent), 4), "remaining": round(float(max(Decimal(0), limit_d - spent)), 4),
            "reached": spent >= limit_d, "unpriced_calls": totals["unpriced_calls"],
        }
    return {"type": "none", "limit": None, "used": totals["tokens"], "spent": round(float(totals["cost"]), 4), "remaining": None, "reached": False, "unpriced_calls": totals["unpriced_calls"]}


async def usage(db: AsyncSession, organization_id: uuid.UUID, period: str | None = None, *, only_user: uuid.UUID | None = None) -> dict[str, Any]:
    """Tokens and estimated cost for the month by model, feature and person."""

    from app.services.lookups import names_for

    period = period or period_key()
    tables = await _price_tables(db, organization_id)
    rows = await _usage_rows(db, organization_id, period)
    if only_user is not None:
        rows = [r for r in rows if r[4] == str(only_user)]
    totals = {"requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost": Decimal(0), "unpriced_calls": 0}
    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_feature: dict[str, dict[str, Any]] = {}
    by_user: dict[str | None, dict[str, Any]] = {}

    def bucket(store: dict, key: Any, seed: dict[str, Any]) -> dict[str, Any]:
        return store.setdefault(key, {**seed, "requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost": Decimal(0), "priced": True})

    for provider, model, base, feature, user, status, count, t_in, t_out in rows:
        count, t_in, t_out = int(count), int(t_in), int(t_out)
        price = price_of(tables[provider], model, base)
        cost = _cost(price, t_in, t_out) or Decimal(0)
        entries = [
            bucket(by_model, (provider, model or ""), {"provider": provider, "provider_label": NAMES[provider], "model": model or "", "price": [float(p) for p in price] if price else None}),
            bucket(by_feature, feature or "", {"feature": feature or "", "label": FEATURES.get(feature or "", ("Test prompts" if feature == TEST_FEATURE else (feature or "Other"),))[0]}),
            bucket(by_user, user, {"user_id": user}),
            totals,
        ]
        for entry in entries:
            entry["requests"] += count
            entry["errors"] += count if status == "error" else 0
            entry["input_tokens"] += t_in
            entry["output_tokens"] += t_out
            entry["cost"] += cost
            if price is None and (t_in or t_out):
                if "priced" in entry:
                    entry["priced"] = False
                else:
                    entry["unpriced_calls"] += count
    names = await names_for(db, organization_id, "user", [uuid.UUID(u) for u in by_user if u])
    for entry in by_user.values():
        entry["name"] = names.get(entry["user_id"] or "", "Workspace (agents)") if entry["user_id"] else "Workspace (agents and automations)"

    def finish(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in items:
            item["tokens"] = item["input_tokens"] + item["output_tokens"]
            item["cost"] = round(float(item["cost"]), 4)
        return sorted(items, key=lambda i: (-i["cost"], -i["tokens"]))

    toggle = await active_toggle(db, organization_id)
    settings = settings_for(toggle.config if toggle else {}, toggle.provider if toggle else "openai")
    return {
        "period": period,
        "totals": {**totals, "tokens": totals["input_tokens"] + totals["output_tokens"], "cost": round(float(totals["cost"]), 4)},
        "by_model": finish(list(by_model.values())),
        "by_feature": finish(list(by_feature.values())),
        "by_user": finish(list(by_user.values())),
        "limit": await limit_state(db, organization_id, settings) if only_user is None else None,
        "only_mine": only_user is not None,
    }


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def _resource(endpoint: str) -> str:
    return (urlsplit(endpoint).hostname or endpoint).split(".")[0]


def _call_error(exc: client.AiCallError) -> IntegrationError:
    return IntegrationError(exc.message, 422 if exc.kind in ("auth", "not_found", "bad_request") else 502)


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    settings = settings_for(await core.workspace_config(db, organization_id, key), key)
    now = utcnow().isoformat()
    if key == "openai":
        api_key = values.get("api_key") or ""
        organization = values.get("organization") or ""
        project = values.get("project") or ""
        if not api_key.startswith("sk-") or len(api_key) < 20 or any(ch.isspace() for ch in api_key):
            raise IntegrationError("That doesn't look like an OpenAI API key (they start with sk-).", 422)
        if organization and not OPENAI_ORG.fullmatch(organization):
            raise IntegrationError("The organization ID looks like org-… (Settings → Organization → General).", 422)
        if project and not OPENAI_PROJECT.fullmatch(project):
            raise IntegrationError("The project ID looks like proj_… (Settings → Project → General).", 422)
        target = client.Target("openai", api_key=api_key, organization=organization or None, project=project or None, region=settings["region"])
        try:
            models = await client.list_openai_models(target)
        except client.AiCallError as exc:
            raise _call_error(exc) from exc
        label = f"OpenAI · {project}" if project else (f"OpenAI · {organization}" if organization else "OpenAI API")
        return {"label": label, "external_id": project or organization or "", "config": {"models": models[:300], "models_listed_at": now}}
    endpoint = client.azure_endpoint(values.get("endpoint"))
    api_key = values.get("api_key") or ""
    tenant, client_id, secret = values.get("tenant_id") or "", values.get("client_id") or "", values.get("client_secret") or ""
    if not api_key and not (tenant and client_id and secret):
        raise IntegrationError("Enter an API key, or all three Entra ID values (tenant ID, client ID and client secret).", 422)
    if not api_key:
        if not client.TENANT.fullmatch(tenant):
            raise IntegrationError("The Entra tenant ID is a GUID (or your tenant's domain).", 422)
        if not GUID.fullmatch(client_id):
            raise IntegrationError("The Entra client ID is the app registration's Application (client) ID, a GUID.", 422)
    cache = f"{organization_id}:check"
    target = client.Target(
        "azure_openai", api_key=api_key or None, endpoint=endpoint, api_mode=settings["api_mode"], api_version=settings["api_version"],
        tenant_id=tenant or None, client_id=client_id or None, client_secret=secret or None, cache_key=cache,
    )
    try:
        models = await client.check_azure(target)
    except client.AiCallError as exc:
        raise _call_error(exc) from exc
    finally:
        client.forget_tokens(cache)
    return {
        "label": f"Azure OpenAI · {_resource(endpoint)}",
        "external_id": urlsplit(endpoint).hostname or endpoint,
        "config": {"endpoint": endpoint, "auth": "key" if api_key else "entra", "models": models[:300], "models_listed_at": now},
    }


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    client.forget_tokens(str(connection.organization_id))
    toggle = await core.toggle_row(db, connection.organization_id, key)
    if toggle is None:
        return
    config = dict(toggle.config or {})
    if key == "openai" and not config.get("default_model"):
        models = list((connection.config or {}).get("models") or [])
        pick = next((m for m in PREFERRED_DEFAULTS if m in models), None) or next((m for m in models if "mini" in m), None) or (models[0] if models else "")
        if pick:
            config["default_model"] = pick
            toggle.config = config
            core.log(db, connection.organization_id, key, "default_model", f"AI features will use {pick} unless you choose another model.", connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    client.forget_tokens(str(connection.organization_id))


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    """Only one provider runs AIVA's AI at a time: switching one on switches the other off."""

    client.forget_tokens(str(organization_id))
    if not enabled:
        return
    rival = await core.toggle_row(db, organization_id, other(key))
    if rival is not None and rival.enabled:
        rival.enabled = False
        core.log(db, organization_id, other(key), "switched_off", f"{NAMES[other(key)]} was turned off: {NAMES[key]} is now the workspace's AI model provider.")
        core.log(db, organization_id, key, "provider_switched", f"{NAMES[key]} replaced {NAMES[other(key)]} as the workspace's AI model provider.")
        from app.services.automation import record_audit

        record_audit(db, organization_id=organization_id, action="integration_off", entity_type="integration", entity_label=core.definition(other(key)).name)


async def list_models(db: AsyncSession, key: str, connection: IntegrationConnection, *, refresh: bool) -> dict[str, Any]:
    """Models (OpenAI) or base models and configured deployments (Azure), cached on the connection."""

    config = dict(connection.config or {})
    settings = settings_for(await core.workspace_config(db, connection.organization_id, key), key)
    if refresh or not config.get("models"):
        target = target_for(key, connection, settings)
        try:
            models = await (client.list_openai_models(target) if key == "openai" else client.check_azure(target))
        except client.AiCallError as exc:
            raise _call_error(exc) from exc
        config["models"] = models[:300]
        config["models_listed_at"] = utcnow().isoformat()
        connection.config = config
    configured = sorted({settings["default_model"], *[v for v in settings["feature_models"].values() if v != USE_AIVA]} - {""})
    return {"models": config.get("models") or [], "listed_at": config.get("models_listed_at"), "configured": configured}


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    """Checks the credentials, lists models, and checks every model the features use."""

    if not principal.can("admin.settings"):
        raise IntegrationError(f"Only an admin can test {NAMES[key]}.", 403)
    listed = await list_models(db, key, connection, refresh=True)
    models, configured = listed["models"], listed["configured"]
    settings = settings_for(await core.workspace_config(db, connection.organization_id, key), key)
    if key == "openai":
        missing = [m for m in configured if m not in models and not m.startswith("ft:")]
        if missing:
            raise IntegrationError(f"Connected, but this key can't use {', '.join(missing)}. Pick another model or give the project access.", 409)
        chosen = f" Features use {', '.join(configured)}." if configured else " Choose a default model next."
        return f"Connected to OpenAI: {len(models)} text models available.{chosen}"
    if not configured:
        return f"Connected to {_resource(connection.config.get('endpoint') or '')}. Map AI features to a deployment next."
    target = target_for(key, connection, settings)
    working, problems = [], []
    for deployment in configured[:8]:
        try:
            await client.generate(target, model=deployment, system="Reply with OK.", prompt="OK?", max_tokens=16, temperature=None, reasoning_effort=None, timeout=20)
            working.append(deployment)
        except client.AiCallError as exc:
            if exc.kind in ("incomplete", "filtered"):
                working.append(deployment)  # the deployment answered; the reply was just cut short
            elif exc.kind == "auth":
                raise _call_error(exc) from exc
            else:
                problems.append(f"{deployment}: {exc.message}")
    if problems:
        raise IntegrationError("Some deployments didn't answer. " + " ".join(problems), 409)
    auth = "Microsoft Entra ID" if not core.secret(connection, "api_key") else "an API key"
    return f"Connected to {_resource(connection.config.get('endpoint') or '')} with {auth}. Deployments answering: {', '.join(working)}."


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    manage = principal.can("admin.settings")
    toggle = await core.toggle_row(db, org, key)
    settings = settings_for(toggle.config if toggle else {}, key)
    connection = await core.connection_for(db, org, key)
    rival = await core.toggle_row(db, org, other(key))
    decision = await decide(db, org, None)
    from app.services import ai

    features_out = []
    for feature, (label, description) in FEATURES.items():
        chosen = settings["feature_models"].get(feature, "")
        features_out.append({
            "key": feature, "label": label, "description": description, "model": chosen,
            "effective": "AIVA default" if chosen == USE_AIVA else (chosen or settings["default_model"] or None),
        })
    from app.integrations.providers.lead_capture import plan_for

    plan_features, _ = await plan_for(db, org)
    hipaa_workspace = await _hipaa(db, org, plan_features)
    config = (connection.config or {}) if connection else {}
    return {
        "settings": settings,
        "features": features_out,
        "models": config.get("models") or [],
        "models_listed_at": config.get("models_listed_at"),
        "auth": config.get("auth") if key == "azure_openai" else "key",
        "endpoint": config.get("endpoint") if key == "azure_openai" else None,
        "active": decision.key == key and decision.reason == "ready",
        "state": {"provider": decision.key, "reason": decision.reason, "message": REASONS.get(decision.reason, "")},
        "other": {"key": other(key), "name": NAMES[other(key)], "enabled": bool(rival and rival.enabled)},
        "default_source": ai.default_source(),
        "hipaa": {"workspace": hipaa_workspace, "acknowledged": bool(settings["hipaa_acknowledged"]), "acknowledged_at": settings["hipaa_acknowledged_at"]},
        "usage": await usage(db, org, only_user=None if manage else principal.user_id),
        "prices": {name: [float(i), float(o)] for name, (i, o) in prices_for(settings).items()},
        "default_prices": {name: [float(Decimal(i)), float(Decimal(o))] for name, (i, o) in DEFAULT_PRICES.items()},
        "efforts": list(EFFORTS),
        "azure": {"default_api_version": client.AZURE_DEFAULT_API_VERSION, "hosts": list(client.AZURE_HOST_SUFFIXES)} if key == "azure_openai" else None,
        "can_manage": manage,
    }


async def run_due(db: AsyncSession) -> int:
    """Daily: drop usage rows older than ~13 months."""

    global _PRUNED_AT
    if time.monotonic() - _PRUNED_AT < 86400:
        return 0
    _PRUNED_AT = time.monotonic()
    result = await db.execute(
        delete(IntegrationObject).where(
            IntegrationObject.provider.in_(KEYS), IntegrationObject.object_type == OBJECT, IntegrationObject.occurred_at < utcnow() - timedelta(days=RETENTION_DAYS)
        )
    )
    await db.commit()
    return int(result.rowcount or 0)


_PRUNED_AT = -1e9
