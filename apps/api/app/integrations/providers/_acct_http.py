"""HTTP for the accounting integrations (QuickBooks Online, Xero, FreshBooks, Sage).

Unlike ``core.request`` this keeps what accounting sync needs to know:

* **Rate limits.** A 429 becomes ``RateLimited`` with the provider's
  ``Retry-After`` (seconds), so the sync can pause that company instead of
  hammering it. QuickBooks allows ~500 requests a minute per company, Xero 60 a
  minute and 5,000 a day per organisation.
* **Call budgets.** Every call made during one scheduler run counts against a
  ``Budget`` (calls and seconds), so one big company can't hold the queue.
* **Provider error formats.** QuickBooks ``Fault``, Xero ``Elements[].ValidationErrors``,
  FreshBooks ``response.errors``, Sage ``[{"$message"}]`` and NetSuite
  ``o:errorDetails[].detail`` become one readable sentence.
* **Signed requests.** Providers that don't use bearer tokens register a signer
  in ``SIGNERS`` (NetSuite token-based authentication signs every request with
  OAuth 1.0a HMAC-SHA256); the signer gets the final URL, query included.

Tests swap ``TRANSPORT`` for an ``httpx.MockTransport``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.integration_hub import IntegrationConnection

logger = logging.getLogger("aiva.integrations.accounting")

TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests only
DEFAULT_RETRY_AFTER = 60
MAX_RETRY_AFTER = 3600
# provider -> (connection, method, final url) -> Authorization header. Registered by
# adapters whose provider signs each request instead of sending a bearer token.
SIGNERS: dict[str, Callable[[IntegrationConnection, str, str], str]] = {}
# Error codes that mean "too many requests right now" even when the status isn't 429.
BUSY_CODES = {"CONCURRENCY_LIMIT_EXCEEDED", "SSS_REQUEST_LIMIT_EXCEEDED", "REQUEST_LIMIT_EXCEEDED"}


class RateLimited(IntegrationError):
    """The provider asked AIVA to slow down; try this company again after ``retry_after`` seconds."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message, 429)
        self.retry_after = retry_after


class BudgetSpent(Exception):
    """This run's share of calls or time for one company is used up; carry on next run."""


@dataclass
class Budget:
    calls: int
    seconds: float
    used: int = 0
    started: float = field(default_factory=time.monotonic)

    def take(self) -> None:
        if self.used >= self.calls or time.monotonic() - self.started > self.seconds:
            raise BudgetSpent()
        self.used += 1

    @property
    def left(self) -> int:
        return max(0, self.calls - self.used)


def unlimited() -> Budget:
    """For actions a person started (push this invoice now): generous but still bounded."""

    return Budget(calls=60, seconds=60)


def client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=TRANSPORT) if TRANSPORT is not None else httpx.AsyncClient(timeout=timeout)


def retry_after(response: httpx.Response) -> int:
    value = (response.headers.get("retry-after") or "").strip()
    try:
        seconds = int(float(value))
    except ValueError:
        seconds = DEFAULT_RETRY_AFTER
    return max(1, min(seconds, MAX_RETRY_AFTER))


def provider_message(response: httpx.Response) -> str:
    """The most useful sentence from a provider's error body."""

    try:
        data = response.json()
    except ValueError:
        return (response.text or f"HTTP {response.status_code}")[:300]
    messages: list[str] = []
    if isinstance(data, dict):
        fault = data.get("Fault") or data.get("fault")
        if isinstance(fault, dict):  # QuickBooks
            for error in fault.get("Error") or fault.get("error") or []:
                text = error.get("Detail") or error.get("detail") or error.get("Message") or error.get("message")
                if text:
                    messages.append(str(text))
        for element in data.get("Elements") or []:  # Xero validation
            for error in element.get("ValidationErrors") or []:
                if error.get("Message"):
                    messages.append(str(error["Message"]))
        if not messages and data.get("Message"):  # Xero other errors
            messages.append(str(data["Message"]))
        for detail in data.get("o:errorDetails") or []:  # NetSuite REST
            if isinstance(detail, dict) and detail.get("detail"):
                messages.append(str(detail["detail"]))
        response_part = data.get("response")
        if isinstance(response_part, dict):  # FreshBooks
            for error in response_part.get("errors") or []:
                if isinstance(error, dict) and error.get("message"):
                    messages.append(str(error["message"]))
        if not messages:
            for key in ("error_description", "message", "detail", "error", "title"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    messages.append(value)
                    break
                if isinstance(value, dict) and value.get("message"):
                    messages.append(str(value["message"]))
                    break
    elif isinstance(data, list):  # Sage
        for error in data:
            if isinstance(error, dict) and (error.get("$message") or error.get("message")):
                messages.append(str(error.get("$message") or error.get("message")))
    text = "; ".join(dict.fromkeys(m.strip() for m in messages if m and m.strip()))
    return (text or f"HTTP {response.status_code}")[:500]


def error_codes(response: httpx.Response) -> set[str]:
    """Machine-readable error codes in a NetSuite error body (``o:errorDetails[].o:errorCode``)."""

    try:
        data = response.json()
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(d.get("o:errorCode")) for d in data.get("o:errorDetails") or [] if isinstance(d, dict) and d.get("o:errorCode")}


async def call(
    db: AsyncSession,
    connection: IntegrationConnection,
    method: str,
    url: str,
    *,
    budget: Budget | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    ok_statuses: tuple[int, ...] = (),
    **kwargs: Any,
) -> httpx.Response:
    """Call a provider as this connection (bearer token with one refresh on 401, or a signed request)."""

    name = core.definition(connection.provider).name
    vendor = core.definition(connection.provider).vendor
    signer = SIGNERS.get(connection.provider)
    if signer is not None and kwargs.get("params"):
        # The signature covers the query string, so sign exactly the URL that is sent.
        url = str(httpx.URL(url).copy_merge_params(kwargs.pop("params")))
    attempt = 0
    while True:
        if budget is not None:
            budget.take()
        if signer is not None:
            authorization = signer(connection, method, url)  # fresh nonce and timestamp every attempt
        else:
            authorization = f"Bearer {await core.access_token(db, connection)}"
        request_headers = {"Accept": "application/json", **(headers or {}), "Authorization": authorization}
        try:
            async with client(timeout) as http:
                response = await http.request(method, url, headers=request_headers, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("Network error calling %s for %s: %r", url.split("?")[0], connection.provider, exc)
            raise IntegrationError(f"Couldn't reach {vendor}. Try again in a moment.", 502) from exc
        if response.status_code == 401 and attempt == 0 and (signer is not None or connection.refresh_token):
            attempt += 1
            if signer is None:
                await core.refresh_access_token(db, connection)
            continue
        break
    if response.status_code in ok_statuses:
        return response
    if response.status_code == 429 or (response.status_code >= 400 and error_codes(response) & BUSY_CODES):
        wait = retry_after(response)
        connection.last_error = f"{name} asked AIVA to slow down; trying again in {wait} seconds."[:1000]
        raise RateLimited(f"{name} is busy (too many requests). AIVA will try again in {wait} seconds.", wait)
    if response.status_code >= 400:
        message = provider_message(response)
        if response.status_code == 401:
            connection.status = "needs_reauth"
            if signer is not None:
                connection.last_error = f"{name} refused AIVA's token (it was revoked, expired or its role changed). Reconnect with a new token."
            else:
                connection.last_error = f"{name} needs to be reconnected (the sign-in expired or was revoked)."
            raise IntegrationError(connection.last_error, 409)
        if response.status_code == 403:
            raise IntegrationError(f"{name} didn't allow that: {message}", 403)
        raise IntegrationError(f"{name} said: {message}", 502 if response.status_code >= 500 else 400)
    connection.last_used_at = core.utcnow()
    return response


async def basic_post(url: str, *, auth: tuple[str, str], data: dict[str, str] | None = None, json: Any = None, timeout: float = 15) -> httpx.Response | None:
    """A best-effort POST with client credentials (token revocation). Never raises."""

    try:
        async with client(timeout) as http:
            return await http.post(url, auth=auth, data=data, json=json, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None


def refresh_token_of(connection: IntegrationConnection) -> str | None:
    return decrypt(connection.refresh_token) if connection.refresh_token else None
