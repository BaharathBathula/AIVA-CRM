"""HTTP for the integrations connected with pasted keys (Adobe Acrobat Sign,
PandaDoc, Apollo.io, ZoomInfo): one place for timeouts, friendly errors, and
noting the last error on the connection. Tests replace ``send`` to stand in
for the providers."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.integration_hub import IntegrationConnection

logger = logging.getLogger("aiva.integrations.sales")


async def send(method: str, url: str, *, timeout: float = 30, **kwargs: Any) -> httpx.Response:
    """The raw call (replaced in tests)."""

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        return await client.request(method, url, **kwargs)


def message_of(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or f"HTTP {response.status_code}")[:300]
    if isinstance(data, dict):
        for key in ("message", "detail", "error_description", "error", "errorMessage", "code"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("code")
            if isinstance(value, list) and value:
                value = value[0].get("message") if isinstance(value[0], dict) else value[0]
            if value:
                return str(value)[:300]
    return str(data)[:300]


async def call(
    vendor: str,
    method: str,
    url: str,
    *,
    connection: IntegrationConnection | None = None,
    timeout: float = 30,
    ok: tuple[int, ...] = (),
    **kwargs: Any,
) -> httpx.Response:
    """Call a provider; network problems and error answers become ``IntegrationError``.

    Statuses in ``ok`` are returned instead of raising (for "not found" answers).
    """

    try:
        response = await send(method, url, timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Network error calling %s: %r", url.split("?")[0], exc)
        raise IntegrationError(f"Couldn't reach {vendor}. Try again in a moment.", 502) from exc
    if response.status_code < 400 or response.status_code in ok:
        if connection is not None:
            connection.last_used_at = core.utcnow()
        return response
    message = message_of(response)
    if connection is not None:
        connection.last_error = f"{response.status_code}: {message}"[:1000]
    if response.status_code in (401, 403):
        raise IntegrationError(f"{vendor} refused the request ({message}). Check the key and its permissions.", 400)
    if response.status_code == 429:
        raise IntegrationError(f"{vendor}'s rate limit was reached. Try again in a minute.", 429)
    raise IntegrationError(f"{vendor} said: {message}", 502 if response.status_code >= 500 else 400)
