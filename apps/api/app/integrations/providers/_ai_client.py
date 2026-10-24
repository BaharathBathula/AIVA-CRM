"""HTTP for the AI model providers: OpenAI and Azure OpenAI.

* **OpenAI** uses the Responses API (``POST /v1/responses``), which OpenAI
  recommends for new work: one call shape for every current model, reasoning
  controls for reasoning models, and ``store: false`` so OpenAI doesn't keep the
  response. Base URL ``https://api.openai.com/v1`` (or ``https://eu.api.openai.com/v1``
  for EU data residency projects).
* **Azure OpenAI** uses Chat Completions, which every Azure OpenAI deployment
  serves: the v1 API (``{endpoint}/openai/v1/chat/completions`` with the deployment
  name as ``model``, no api-version) by default, or the classic deployments route
  (``{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=…``)
  for resources that haven't moved to v1. Authentication is the ``api-key`` header
  or a Microsoft Entra ID client-credentials token (scope
  ``https://cognitiveservices.azure.com/.default``), cached until shortly before it
  expires.

Nothing here logs prompts, responses or keys. Redirects are never followed and
Azure endpoints are limited to Azure OpenAI hosts over https.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.integrations.core import IntegrationError

OPENAI_BASES = {"default": "https://api.openai.com/v1", "eu": "https://eu.api.openai.com/v1"}
AZURE_HOST_SUFFIXES = (".openai.azure.com", ".cognitiveservices.azure.com", ".services.ai.azure.com")
AZURE_SCOPE = "https://cognitiveservices.azure.com/.default"
AZURE_DEFAULT_API_VERSION = "2024-10-21"  # latest GA data-plane version for the classic deployments route
ENTRA_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,99}$")
API_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}(-preview)?$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")  # model ids and deployment names
REASONING_MODEL = re.compile(r"^(?:ft:)?(?:o\d|gpt-(\d+))", re.IGNORECASE)
MAX_RETRIES = 2

# Tests swap these for an httpx.MockTransport and a sleep that only records.
TRANSPORT: httpx.AsyncBaseTransport | None = None
_sleep = asyncio.sleep


class AiCallError(Exception):
    """A provider call that didn't produce text. ``message`` is safe to show people."""

    def __init__(self, message: str, *, status: int | None = None, kind: str = "error", retry_after: float | None = None, param: str | None = None):
        super().__init__(message)
        self.message = message
        self.param = param  # the request field the provider objected to, when it says
        self.status = status
        self.kind = kind  # auth | not_found | rate_limited | unavailable | bad_request | filtered | incomplete | timeout | error
        self.retry_after = retry_after


@dataclass
class Generation:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 1


@dataclass
class Target:
    """Where and how to call: one connected provider."""

    provider: str  # openai | azure_openai
    api_key: str | None = None
    organization: str | None = None
    project: str | None = None
    region: str = "default"
    endpoint: str | None = None
    api_mode: str = "v1"  # azure: v1 | deployments
    api_version: str = AZURE_DEFAULT_API_VERSION
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    cache_key: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def azure_endpoint(raw: str | None) -> str:
    """``https://<resource>.openai.azure.com`` (or the other Azure OpenAI hosts), cleaned.

    Anything else is refused, so a key is never sent to an arbitrary address."""

    value = (raw or "").strip()
    if not value:
        raise IntegrationError("Enter your Azure OpenAI endpoint, like https://your-resource.openai.azure.com.", 422)
    parts = urlsplit(value if "://" in value else f"https://{value}")
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https":
        raise IntegrationError("The Azure OpenAI endpoint must start with https://.", 422)
    if parts.username or parts.password or parts.port not in (None, 443):
        raise IntegrationError("Use the endpoint exactly as the Azure portal shows it (no user name or port).", 422)
    label = host.split(".", 1)[0]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", label) or not any(host == label + suffix for suffix in AZURE_HOST_SUFFIXES):
        raise IntegrationError(
            "That isn't an Azure OpenAI endpoint. It should look like https://your-resource.openai.azure.com "
            "(or .cognitiveservices.azure.com / .services.ai.azure.com).",
            422,
        )
    return f"https://{host}"


def check_name(value: str | None, what: str) -> str:
    name = (value or "").strip()
    if not NAME.fullmatch(name):
        raise IntegrationError(f"“{name[:60]}” isn't a valid {what}.", 422)
    return name


def is_reasoning_model(model: str) -> bool:
    """o-series and GPT-5 onwards reason before answering (and reject ``temperature``)."""

    match = REASONING_MODEL.match(model or "")
    if not match:
        return False
    return match.group(1) is None or int(match.group(1)) >= 5


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


async def _send(method: str, url: str, *, headers: dict[str, str], json_body: Any = None, data: Any = None, timeout: float = 30) -> httpx.Response:
    kwargs: dict[str, Any] = {"timeout": httpx.Timeout(timeout, connect=min(10.0, timeout)), "follow_redirects": False, "trust_env": True}
    if TRANSPORT is not None:
        kwargs["transport"] = TRANSPORT
    async with httpx.AsyncClient(**kwargs) as client:
        return await client.request(method, url, headers=headers, json=json_body, data=data)


def _retry_after(response: httpx.Response) -> float | None:
    for name in ("retry-after-ms", "retry-after"):
        raw = response.headers.get(name)
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        return value / 1000 if name.endswith("-ms") else value
    return None


def _provider_error(response: httpx.Response, label: str) -> AiCallError:
    detail = ""
    code = ""
    param = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or "")[:300]
            code = str(error.get("code") or error.get("type") or "")
            param = str(error.get("param") or "")[:80]
    except ValueError:
        pass
    status = response.status_code
    if status in (401, 403):
        if status == 403 and "region" in detail.lower():
            return AiCallError(f"{label} refused the request from this region: {detail}", status=status, kind="auth")
        return AiCallError(f"{label} rejected the credentials. Check the key (or the Entra app's role on the resource).", status=status, kind="auth")
    if status == 404:
        return AiCallError(f"{label} couldn't find that model or deployment. Check the name on the AI models page.", status=status, kind="not_found")
    if status == 429:
        if "quota" in (detail + code).lower() or "insufficient_quota" in code:
            return AiCallError(f"{label} says the account is out of quota or credit.", status=status, kind="rate_limited", retry_after=_retry_after(response))
        return AiCallError(f"{label} is rate limiting requests right now.", status=status, kind="rate_limited", retry_after=_retry_after(response))
    if status >= 500:
        return AiCallError(f"{label} had a problem ({status}). Try again in a moment.", status=status, kind="unavailable", retry_after=_retry_after(response))
    if "content_filter" in (detail + code).lower() or "content management policy" in detail.lower():
        return AiCallError(f"{label}'s content filter blocked this request.", status=status, kind="filtered")
    return AiCallError(f"{label} refused the request: {detail or f'HTTP {status}'}", status=status, kind="bad_request", param=param or None)


def _unsupported_param(error: AiCallError) -> str | None:
    """The optional parameter a model rejected (temperature, reasoning…), if that's what failed."""

    if error.kind != "bad_request":
        return None
    text = f"{error.message} {error.param or ''}".lower()
    for param in ("temperature", "reasoning", "max_completion_tokens"):
        if param in text and any(word in text for word in ("unsupported", "not supported", "does not support", "unknown", "unrecognized", "invalid")):
            return param
    return None


async def _post_with_retries(url: str, headers: dict[str, str], payload: dict[str, Any], *, label: str, deadline: float) -> tuple[dict[str, Any], int]:
    """POST, retrying 429/5xx/connection failures with backoff inside ``deadline``.

    Optional parameters a model rejects (temperature, reasoning) are dropped and
    the call is retried once without them. Returns (body, attempts)."""

    attempts = 0
    dropped: set[str] = set()
    while True:
        attempts += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            raise AiCallError(f"{label} didn't answer in time.", kind="timeout")
        try:
            response = await _send("POST", url, headers=headers, json_body=payload, timeout=remaining)
        except httpx.TimeoutException as exc:
            raise AiCallError(f"{label} didn't answer in time.", kind="timeout") from exc
        except httpx.HTTPError as exc:
            error = AiCallError(f"Couldn't reach {label}.", kind="unavailable")
            if attempts > MAX_RETRIES:
                raise error from exc
            await _pause(attempts, None, deadline)
            continue
        if response.status_code < 300:
            try:
                return response.json(), attempts
            except ValueError as exc:
                raise AiCallError(f"{label} sent an unreadable answer.", kind="unavailable") from exc
        if 300 <= response.status_code < 400:
            raise AiCallError(f"{label} tried to redirect the request; check the endpoint.", status=response.status_code, kind="bad_request")
        error = _provider_error(response, label)
        param = _unsupported_param(error)
        if param and param not in dropped:
            dropped.add(param)
            before = dict(payload)
            if param == "reasoning":
                payload.pop("reasoning", None)
                payload.pop("reasoning_effort", None)
            elif param == "temperature":
                payload.pop("temperature", None)
            elif param == "max_completion_tokens" and "max_completion_tokens" in payload:
                payload["max_tokens"] = payload.pop("max_completion_tokens")
            if payload != before:
                continue
        if error.kind in ("rate_limited", "unavailable") and attempts <= MAX_RETRIES and "quota" not in error.message:
            await _pause(attempts, error.retry_after, deadline)
            continue
        raise error


MAX_WAIT = 8.0


async def _pause(attempt: int, retry_after: float | None, deadline: float) -> None:
    """Waits before a retry: the provider's retry-after when it gives one, else 0.5s, 1s…
    Gives up (so the feature falls back now) rather than wait past the deadline or longer
    than the provider asked."""

    wait = max(0.0, retry_after) if retry_after is not None else 0.5 * (2 ** (attempt - 1))
    if wait > MAX_WAIT or time.monotonic() + wait > deadline - 1.0:
        raise AiCallError("The model is busy and there isn't time to wait for it.", kind="rate_limited")
    await _sleep(wait)


# ---------------------------------------------------------------------------
# Microsoft Entra ID tokens (Azure OpenAI)
# ---------------------------------------------------------------------------

_TOKENS: dict[str, tuple[str, float]] = {}


async def entra_token(target: Target) -> str:
    """A client-credentials token for Azure OpenAI, cached until a minute before expiry."""

    if not (target.tenant_id and target.client_id and target.client_secret):
        raise AiCallError("Azure OpenAI needs an API key, or a tenant ID, client ID and client secret.", kind="auth")
    if not TENANT.fullmatch(target.tenant_id):
        raise AiCallError("That Entra tenant ID doesn't look right.", kind="auth")
    key = f"{target.cache_key}:{target.tenant_id}:{target.client_id}"
    cached = _TOKENS.get(key)
    if cached and cached[1] - 60 > time.monotonic():
        return cached[0]
    try:
        response = await _send(
            "POST",
            ENTRA_TOKEN_URL.format(tenant=quote(target.tenant_id, safe="")),
            headers={"Accept": "application/json"},
            data={"grant_type": "client_credentials", "client_id": target.client_id, "client_secret": target.client_secret, "scope": AZURE_SCOPE},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise AiCallError("Couldn't reach Microsoft Entra ID to sign in.", kind="unavailable") from exc
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str(response.json().get("error_description") or "")[:200]
        except ValueError:
            pass
        raise AiCallError(f"Microsoft Entra ID refused the app's credentials{': ' + detail.splitlines()[0] if detail else ''}.", status=response.status_code, kind="auth")
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise AiCallError("Microsoft Entra ID didn't return a token.", kind="auth")
    _TOKENS[key] = (token, time.monotonic() + float(body.get("expires_in") or 3600))
    return token


def forget_tokens(cache_key: str) -> None:
    for key in [k for k in _TOKENS if k.startswith(f"{cache_key}:")]:
        _TOKENS.pop(key, None)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def _openai_headers(target: Target) -> dict[str, str]:
    if not target.api_key:
        raise AiCallError("OpenAI isn't connected (no API key).", kind="auth")
    headers = {"Authorization": f"Bearer {target.api_key}", "Content-Type": "application/json", "Accept": "application/json"}
    if target.organization:
        headers["OpenAI-Organization"] = target.organization
    if target.project:
        headers["OpenAI-Project"] = target.project
    return headers


async def _azure_headers(target: Target) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if target.api_key:
        headers["api-key"] = target.api_key
    else:
        headers["Authorization"] = f"Bearer {await entra_token(target)}"
    return headers


def _openai_base(target: Target) -> str:
    return OPENAI_BASES.get(target.region, OPENAI_BASES["default"])


async def generate(
    target: Target,
    *,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float | None,
    reasoning_effort: str | None,
    timeout: float,
) -> Generation:
    """One completion. ``max_tokens`` bounds everything the model writes, reasoning
    included. Raises ``AiCallError`` with a message people can read."""

    started = time.monotonic()
    deadline = started + timeout
    model = check_name(model, "model or deployment name")
    reasoning = is_reasoning_model(model)
    limit = max(16, int(max_tokens))
    if target.provider == "openai":
        payload: dict[str, Any] = {"model": model, "instructions": system, "input": prompt, "max_output_tokens": limit, "store": False}
        if reasoning and reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if temperature is not None and not reasoning:
            payload["temperature"] = temperature
        body, attempts = await _post_with_retries(f"{_openai_base(target)}/responses", _openai_headers(target), payload, label="OpenAI", deadline=deadline)
        text = _responses_text(body)
        usage = body.get("usage") or {}
        if not text:
            reason = ((body.get("incomplete_details") or {}).get("reason") or "").replace("_", " ")
            if body.get("status") == "incomplete" and "max output tokens" in reason:
                raise AiCallError("The model used its whole output allowance (often on reasoning) before answering. Raise the output limit or pick a lighter reasoning effort.", kind="incomplete")
            if "content filter" in reason:
                raise AiCallError("OpenAI's content filter blocked the answer.", kind="filtered")
            raise AiCallError("OpenAI returned no text.", kind="incomplete")
        return Generation(
            text=text, model=str(body.get("model") or model), input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=int(((usage.get("output_tokens_details") or {}).get("reasoning_tokens")) or 0),
            latency_ms=int((time.monotonic() - started) * 1000), attempts=attempts,
        )
    # Azure OpenAI: Chat Completions on a deployment.
    endpoint = azure_endpoint(target.endpoint)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    payload = {"messages": messages, "max_completion_tokens": limit}
    if target.api_mode == "deployments":
        version = target.api_version if API_VERSION.fullmatch(target.api_version or "") else AZURE_DEFAULT_API_VERSION
        url = f"{endpoint}/openai/deployments/{quote(model, safe='')}/chat/completions?api-version={version}"
    else:
        url = f"{endpoint}/openai/v1/chat/completions"
        payload["model"] = model
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if temperature is not None and not reasoning_effort and not reasoning:
        payload["temperature"] = temperature
    body, attempts = await _post_with_retries(url, await _azure_headers(target), payload, label="Azure OpenAI", deadline=deadline)
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") if isinstance(message.get("content"), str) else ""
    if not (text or "").strip():
        if choice.get("finish_reason") == "content_filter":
            raise AiCallError("Azure's content filter blocked the answer.", kind="filtered")
        if choice.get("finish_reason") == "length":
            raise AiCallError("The deployment used its whole output allowance before answering. Raise the output limit.", kind="incomplete")
        raise AiCallError("Azure OpenAI returned no text.", kind="incomplete")
    usage = body.get("usage") or {}
    return Generation(
        text=text.strip(), model=str(body.get("model") or model), input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=int(((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0),
        latency_ms=int((time.monotonic() - started) * 1000), attempts=attempts,
    )


def _responses_text(body: dict[str, Any]) -> str:
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Listing and checks
# ---------------------------------------------------------------------------

_NOT_CHAT = (
    "audio", "realtime", "transcribe", "tts", "image", "embedding", "search", "moderation", "instruct", "dall-e", "whisper",
    "computer-use", "deep-research", "codex", "babbage", "davinci", "sora", "omni-moderation",
)


def chat_models(ids: list[str]) -> list[str]:
    """Model ids that can write text through the Responses API."""

    keep = []
    for model_id in ids:
        lowered = model_id.lower()
        lowered = lowered[3:] if lowered.startswith("ft:") else lowered
        if not (lowered.startswith(("gpt-", "chatgpt-", "chat-latest")) or re.match(r"o\d", lowered)):
            continue
        if any(word in lowered for word in _NOT_CHAT):
            continue
        keep.append(model_id)
    return sorted(set(keep), key=lambda m: (not m.startswith("gpt-"), m))


async def list_openai_models(target: Target) -> list[str]:
    try:
        response = await _send("GET", f"{_openai_base(target)}/models", headers=_openai_headers(target), timeout=20)
    except httpx.HTTPError as exc:
        raise AiCallError("Couldn't reach OpenAI.", kind="unavailable") from exc
    if response.status_code >= 400:
        error = _provider_error(response, "OpenAI")
        if response.status_code == 403:
            error = AiCallError("The key can't list models. Give it Read access to Models (or use an all-access project key).", status=403, kind="auth")
        raise error
    data = response.json().get("data") or []
    return chat_models([str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")])


async def check_azure(target: Target) -> list[str]:
    """Checks the endpoint and credentials. Returns the model names the resource lists (may be empty)."""

    endpoint = azure_endpoint(target.endpoint)
    headers = await _azure_headers(target)
    urls = (
        [f"{endpoint}/openai/v1/models", f"{endpoint}/openai/models?api-version={AZURE_DEFAULT_API_VERSION}"]
        if target.api_mode != "deployments"
        else [f"{endpoint}/openai/models?api-version={target.api_version or AZURE_DEFAULT_API_VERSION}"]
    )
    last: AiCallError | None = None
    for url in urls:
        try:
            response = await _send("GET", url, headers=headers, timeout=20)
        except httpx.HTTPError as exc:
            raise AiCallError("Couldn't reach the Azure OpenAI endpoint.", kind="unavailable") from exc
        if response.status_code < 300:
            data = response.json().get("data") or []
            return sorted({str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")})[:200]
        last = _provider_error(response, "Azure OpenAI")
        if last.kind == "auth":
            raise last
    raise last or AiCallError("Azure OpenAI didn't answer.", kind="unavailable")
