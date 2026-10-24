"""AI for drafting, summaries and answers, with built-in fallbacks.

Every AI feature works without any model using rules and templates. Where the
text comes from, in order:

1. The workspace's own model (OpenAI API or Azure OpenAI, set up in
   Integrations → AI Models) for features mapped to it.
2. Claude, when the server has ``ANTHROPIC_API_KEY`` (AIVA's default model).
3. Built-in rules (the caller's own fallback text).

Responses say which one was used (``source``: openai | azure_openai | claude | rules).
Prompts and answers are never logged.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("aiva.ai")

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = (
    "You are AIVA, the assistant inside a CRM used by sales and service teams. "
    "Write in plain, friendly, professional English. Be concise and specific. "
    "Never invent facts that aren't in the provided data; say so when data is missing."
)

PROVIDER_LABELS = {"openai": "OpenAI", "azure_openai": "Azure OpenAI", "claude": "Claude", "rules": "Built-in rules"}
AI_SOURCES = frozenset({"openai", "azure_openai", "claude"})


@dataclass
class AiReply:
    """``text`` is None when no model answered: the caller uses its built-in fallback."""

    text: str | None
    source: str  # openai | azure_openai | claude | rules
    model: str | None = None

    @property
    def label(self) -> str:
        return PROVIDER_LABELS.get(self.source, self.source)


@dataclass
class AiScope:
    """Who an AI call is for, when the code making it doesn't know (AI agents)."""

    organization_id: uuid.UUID
    feature: str
    user_id: uuid.UUID | None = None
    db: Any = None


current_scope: ContextVar[AiScope | None] = ContextVar("ai_scope", default=None)


def configured() -> bool:
    """Whether AIVA's default model (Claude) is available on this server."""

    return bool(get_settings().anthropic_api_key)


def default_source() -> str:
    return "claude" if configured() else "rules"


def source_label() -> str:
    return default_source()


def is_ai(source: str | None) -> bool:
    return source in AI_SOURCES


async def _claude(prompt: str, *, system: str, max_tokens: int) -> str | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                API_URL,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.ai_model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if response.status_code >= 400:
            # Status only: error bodies can quote the request.
            logger.warning("Claude request failed with HTTP %s", response.status_code)
            return None
        parts = response.json().get("content") or []
        text = "".join(part.get("text", "") for part in parts if part.get("type") == "text").strip()
        return text or None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Claude request failed: %s", type(exc).__name__)
        return None


async def generate(
    prompt: str,
    *,
    system: str = SYSTEM,
    max_tokens: int = 700,
    feature: str | None = None,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    db: Any = None,
) -> AiReply:
    """Writes text for an AI feature on the right model. Never raises for model problems."""

    scope = current_scope.get()
    if scope is not None:
        organization_id = organization_id or scope.organization_id
        feature = feature or scope.feature
        user_id = user_id or scope.user_id
        db = db if db is not None else scope.db
    if organization_id is not None and feature:
        from app.integrations.providers import ai_models

        outcome = await ai_models.run(db, organization_id, feature, system=system, prompt=prompt, max_tokens=max_tokens, user_id=user_id)
        if outcome.text:
            return AiReply(outcome.text, outcome.key or "rules", outcome.model)
        if outcome.fallback == "rules_only":
            return AiReply(None, "rules")
    text = await _claude(prompt, system=system, max_tokens=max_tokens)
    return AiReply(text, "claude", get_settings().ai_model) if text else AiReply(None, "rules")


async def complete(prompt: str, *, system: str = SYSTEM, max_tokens: int = 700, **scope: Any) -> str | None:
    """The text only (kept for older callers); see ``generate``."""

    return (await generate(prompt, system=system, max_tokens=max_tokens, **scope)).text


async def available(db: Any, organization_id: uuid.UUID | None, feature: str | None = None) -> bool:
    """Whether a model (the workspace's or AIVA's default) can write for this feature now."""

    if organization_id is not None:
        state = await model_for(db, organization_id, feature)
        if state["source"] != "rules":
            return True
    return configured()


async def model_for(db: Any, organization_id: uuid.UUID, feature: str | None = None) -> dict[str, Any]:
    """Which model a feature would use right now: ``{source, label, model, reason}``."""

    fallback = default_source()
    default = {"source": fallback, "label": PROVIDER_LABELS[fallback], "model": get_settings().ai_model if fallback == "claude" else None, "reason": "default"}
    try:
        from app.integrations.providers import ai_models

        decision = await ai_models.decide(db, organization_id, feature)
    except Exception as exc:  # noqa: BLE001 - status lookups must not break pages
        logger.warning("AI model lookup failed (%s).", type(exc).__name__)
        return default
    if decision.route is not None:
        return {"source": decision.key, "label": PROVIDER_LABELS[decision.key], "model": decision.route.model, "reason": "ready"}
    if decision.reason == "limit" and decision.policy == "rules_only":
        return {"source": "rules", "label": PROVIDER_LABELS["rules"], "model": None, "reason": "limit"}
    return {**default, "reason": decision.reason}


async def source_for(db: Any, organization_id: uuid.UUID) -> str:
    return (await model_for(db, organization_id))["source"]
