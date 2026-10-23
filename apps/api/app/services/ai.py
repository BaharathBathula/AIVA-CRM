"""Claude for drafting and summaries, with built-in fallbacks.

Every AI feature works without an API key using rules and templates. When
``ANTHROPIC_API_KEY`` is set, drafts, summaries and answers come from
Claude instead, and responses say which one was used.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("aiva.ai")

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = (
    "You are AIVA, the assistant inside a CRM used by sales and service teams. "
    "Write in plain, friendly, professional English. Be concise and specific. "
    "Never invent facts that aren't in the provided data; say so when data is missing."
)


def configured() -> bool:
    return bool(get_settings().anthropic_api_key)


def source_label() -> str:
    return "claude" if configured() else "rules"


async def complete(prompt: str, *, system: str = SYSTEM, max_tokens: int = 700) -> str | None:
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
            logger.warning("Claude request failed: %s %s", response.status_code, response.text[:300])
            return None
        parts = response.json().get("content") or []
        text = "".join(part.get("text", "") for part in parts if part.get("type") == "text").strip()
        return text or None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Claude request failed: %s", exc)
        return None
