"""HTTP for the email marketing integrations.

Every provider call goes through ``call`` so failures turn into one friendly
``IntegrationError`` and tests can stand in for the providers in one place
(``send`` for key-based tools, ``core.request`` for Constant Contact's OAuth).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.integration_hub import IntegrationConnection

logger = logging.getLogger("aiva.integrations.email_marketing")

TIMEOUT = 20.0


class RateLimited(IntegrationError):
    """The provider asked us to slow down; the sync stops for this run."""


class AuthFailed(IntegrationError):
    """The key was refused; the connection needs new credentials."""


async def send(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One HTTP request with a timeout (tests replace this)."""

    async with httpx.AsyncClient(timeout=kwargs.pop("timeout", TIMEOUT), trust_env=True) as client:
        return await client.request(method, url, **kwargs)


def _message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or "").strip()[:300] or f"HTTP {response.status_code}"
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return str(first.get("detail") or first.get("title") or first.get("error_message") or first.get("message") or first)[:300]
            return str(first)[:300]
        for key in ("detail", "message", "error_message", "error_description", "title", "error"):
            value = data.get(key)
            if value:
                return str(value)[:300]
    return str(data)[:300]


async def call(
    db: AsyncSession,
    connection: IntegrationConnection,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    ok_statuses: tuple[int, ...] = (),
    timeout: float = TIMEOUT,
    **kwargs: Any,
) -> httpx.Response:
    """Call a provider as this connection. Raises IntegrationError (RateLimited,
    AuthFailed) on failure, except for the statuses listed in ``ok_statuses``."""

    vendor = core.definition(connection.provider).name
    if connection.provider == "constant_contact":
        # OAuth: core adds and refreshes the bearer token.
        try:
            return await core.request(db, connection, method, url, timeout=timeout, headers=headers or {}, **kwargs)
        except IntegrationError as exc:
            if (connection.last_error or "").startswith("429"):
                raise RateLimited(f"{vendor} asked AIVA to slow down. The sync continues on the next run.", 429) from exc
            raise
    try:
        response = await send(method, url, headers=headers or {}, auth=auth, timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Network error calling %s for %s: %r", urlsplit(url).hostname, connection.provider, exc)
        raise IntegrationError(f"Couldn't reach {vendor}. Try again in a moment.", 502) from exc
    if response.status_code in ok_statuses or response.status_code < 400:
        connection.last_used_at = core.utcnow()
        return response
    message = _message(response)
    if response.status_code == 429:
        raise RateLimited(f"{vendor} asked AIVA to slow down. The sync continues on the next run.", 429)
    if response.status_code in (401, 403):
        connection.status = "error"
        connection.last_error = f"{vendor} refused the key: {message}"[:1000]
        raise AuthFailed(f"{vendor} refused the API key ({message}). Paste a new key on the {vendor} page.", 400)
    if response.status_code >= 500:
        raise IntegrationError(f"{vendor} had a problem ({message}). AIVA will try again.", 502)
    raise IntegrationError(f"{vendor} said: {message}", 400)


def json_of(response: httpx.Response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}
