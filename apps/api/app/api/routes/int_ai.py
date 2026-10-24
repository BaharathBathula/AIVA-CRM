"""Integration endpoints: AI models (OpenAI API, Azure OpenAI).

Connecting, switching on/off, settings and the live test go through the hub
(``/integration-hub/{key}/...``). These endpoints add what the AI Models page
and the AI pages need: which model each feature uses, the live model list, a
test prompt, and token and cost usage.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _ai_client as client
from app.integrations.providers import ai_models
from app.services import ai

router = APIRouter(prefix="/ai-models", tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])


def _key(key: str) -> str:
    if key not in ai_models.KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown AI model provider.")
    return key


def _plan(principal: Principal) -> None:
    if not principal.has(ai_models.PLAN_FEATURE):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Bring-your-own AI models isn't included in your plan.")


@router.get("/status")
async def model_status(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    """Which model each AI feature uses right now (for the AI pages)."""

    default = await ai.model_for(db, principal.organization_id)
    features = {feature: await ai.model_for(db, principal.organization_id, feature) for feature in ai_models.FEATURES}
    return {
        "default": default,
        "features": features,
        "labels": {feature: label for feature, (label, _) in ai_models.FEATURES.items()},
        "workspace_model": default["source"] in ai_models.KEYS or any(f["source"] in ai_models.KEYS for f in features.values()),
    }


@router.get("/usage")
async def model_usage(
    period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Tokens and estimated cost on the workspace's models. Admins see everyone; others see their own."""

    everyone = principal.can("admin.settings") or principal.can("admin.users")
    try:
        return await ai_models.usage(db, principal.organization_id, period, only_user=None if everyone else principal.user_id)
    except IntegrationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/{key}/models")
async def list_models(
    key: str,
    refresh: bool = Query(default=False),
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    """Text models the key can use (OpenAI), or the resource's models and the deployments in use (Azure)."""

    _key(key)
    _plan(principal)
    try:
        connection = await core.active_connection(db, principal.organization_id, key)
        result = await ai_models.list_models(db, key, connection, refresh=refresh)
    except IntegrationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    await db.commit()
    return result


class TestPromptPayload(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    feature: str | None = Field(default=None, max_length=40)


@router.post("/{key}/test-prompt")
async def test_prompt(key: str, payload: TestPromptPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Sends a prompt through exactly what a feature would use (model, limits, data controls), without
    falling back. The prompt and answer are returned, never stored."""

    _key(key)
    _plan(principal)
    if payload.feature and payload.feature not in ai_models.FEATURES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown AI feature.")
    org = principal.organization_id
    try:
        await core.active_connection(db, org, key)
    except IntegrationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    decision = await ai_models.decide(db, org, payload.feature)
    if decision.key != key:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{ai_models.NAMES[key]} isn't the active AI model provider.")
    if decision.route is None:
        raise HTTPException(status.HTTP_409_CONFLICT, ai_models.REASONS.get(decision.reason, "This provider isn't ready."))
    route = decision.route
    settings = route.settings
    sent = ai_models.scrub(payload.prompt, emails=settings["redact_emails"], phones=settings["redact_phones"])
    started = time.monotonic()
    error: client.AiCallError | None = None
    generation: client.Generation | None = None
    try:
        generation = await ai_models.call(route, system=ai.SYSTEM, prompt=payload.prompt, max_tokens=500)
    except client.AiCallError as exc:
        error = exc
    except TimeoutError:
        error = client.AiCallError(f"{ai_models.NAMES[key]} didn't answer within {settings['timeout_seconds']} seconds.", kind="timeout")
    except IntegrationError as exc:
        error = client.AiCallError(str(exc), kind="bad_request")
    usage = await ai_models.record(org, route, ai_models.TEST_FEATURE, principal.user_id, generation, error, latency_ms=int((time.monotonic() - started) * 1000))
    if error is not None:
        core.log(db, org, key, "test_prompt_failed", f"Test prompt to {route.model} failed: {error.message}"[:500], level="error", user_id=principal.user_id)
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY if error.kind not in ("auth", "not_found", "bad_request", "filtered") else status.HTTP_422_UNPROCESSABLE_CONTENT, error.message)
    assert generation is not None
    core.log(
        db, org, key, "test_prompt", f"Test prompt answered by {route.model} ({usage['input_tokens']} in / {usage['output_tokens']} out tokens).",
        level="success", user_id=principal.user_id,
    )
    await db.commit()
    result: dict[str, Any] = {
        "text": generation.text,
        "provider": key,
        "provider_label": ai_models.NAMES[key],
        "model": route.model,
        "served_by": generation.model,
        "feature": payload.feature,
        "redacted": sent != payload.prompt,
        "usage": usage,
    }
    return result
