"""A small Google Ads API (REST) client.

* Every call sends the OAuth token of the workspace's Google Ads connection, the
  developer token (the workspace's own, or the server's ``GOOGLE_ADS_DEVELOPER_TOKEN``)
  and, for accounts reached through a manager account, ``login-customer-id``.
* Quota errors (HTTP 429 / ``RESOURCE_EXHAUSTED``) and brief server errors are retried
  once or twice after the delay Google asks for, if it is short; otherwise
  :class:`AdsBusy` tells the caller when to come back.
* Errors are turned into sentences people can act on (developer token not approved,
  no access to the account, not an Ads user...).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.integration_hub import IntegrationConnection

logger = logging.getLogger("aiva.integrations.google_ads")

API_VERSION = "v26"
BASE = "https://googleads.googleapis.com"
KEY = "google_ads"
CUSTOMER_ID = re.compile(r"^\d{10}$")
MAX_INLINE_WAIT = 10  # seconds a call may wait for quota before handing back


class AdsBusy(IntegrationError):
    """Google asked us to slow down; ``retry_after`` says for how long."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message, 429)
        self.retry_after = retry_after


def clean_customer_id(value: Any) -> str | None:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return digits if CUSTOMER_ID.match(digits) else None


def pretty_customer_id(value: str | None) -> str:
    digits = clean_customer_id(value) or ""
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if digits else ""


async def developer_token(db: AsyncSession, organization_id: uuid.UUID) -> tuple[str | None, str | None]:
    """The developer token to use and where it came from ("workspace" or "server")."""

    toggle = await core.toggle_row(db, organization_id, KEY)
    own = core.secret(toggle, "developer_token") if toggle is not None else None
    if own:
        return own, "workspace"
    server = (get_settings().google_ads_developer_token or "").strip()
    return (server, "server") if server else (None, None)


async def http(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """The single network call (tests replace this)."""

    try:
        async with httpx.AsyncClient(timeout=kwargs.pop("timeout", 40)) as client:
            return await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Network error calling Google Ads: %r", exc)
        raise IntegrationError("Couldn't reach Google Ads. Try again in a moment.", 502) from exc


def _failure(body: Any) -> tuple[str, str, int | None]:
    """(error code, message, retry seconds) from a Google Ads error body."""

    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "", "", None
    message = str(error.get("message") or "")
    code = ""
    retry: int | None = None
    for detail in error.get("details") or []:
        for item in (detail or {}).get("errors") or []:
            codes = item.get("errorCode") or {}
            if codes and not code:
                code = str(next(iter(codes.values())))
            message = str(item.get("message") or message)
            quota = ((item.get("details") or {}).get("quotaErrorDetails") or {}).get("retryDelay")
            if quota:
                retry = _seconds(quota)
    return code or str(error.get("status") or ""), message, retry


def _seconds(value: Any) -> int | None:
    match = re.match(r"^(\d+)(?:\.\d+)?s$", str(value or ""))
    return int(match.group(1)) if match else None


FRIENDLY = {
    "DEVELOPER_TOKEN_NOT_APPROVED": "The Google Ads developer token only has test access, so it can't reach this live account. Apply for Basic access in the Google Ads API Center (or use a test account).",
    "DEVELOPER_TOKEN_PROHIBITED": "Google won't accept this developer token for this sign-in app. Use the developer token that belongs to the same Google Cloud project as the sign-in app.",
    "DEVELOPER_TOKEN_INVALID": "The Google Ads developer token isn't valid. Copy it again from the API Center.",
    "USER_PERMISSION_DENIED": "The connected Google user can't access this Google Ads account. If it is managed through a manager account, pick it under that manager.",
    "CUSTOMER_NOT_ENABLED": "This Google Ads account isn't active (it may be cancelled or not finished setting up).",
    "NOT_ADS_USER": "The connected Google account isn't a Google Ads user. Reconnect with the Google account that manages your ads.",
    "CUSTOMER_NOT_FOUND": "Google Ads couldn't find that account.",
    "OAUTH_TOKEN_REVOKED": "Google Ads access was revoked. Reconnect Google Ads.",
}


async def call(
    db: AsyncSession,
    connection: IntegrationConnection,
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    login_customer_id: str | None = None,
    retries: int = 2,
) -> dict[str, Any]:
    token, _ = await developer_token(db, connection.organization_id)
    if not token:
        raise IntegrationError("Add a Google Ads developer token first (Google Ads → Tools → API Center).", 409)
    url = f"{BASE}/{API_VERSION}/{path.lstrip('/')}"
    refreshed = False
    attempt = 0
    while True:
        bearer = await core.access_token(db, connection)
        headers = {"Authorization": f"Bearer {bearer}", "developer-token": token, "Accept": "application/json"}
        if login_customer_id:
            headers["login-customer-id"] = login_customer_id
        response = await http(method, url, headers=headers, json=json)
        if response.status_code == 401 and not refreshed and connection.refresh_token:
            refreshed = True
            await core.refresh_access_token(db, connection)
            continue
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        if response.status_code < 400:
            connection.last_used_at = core.utcnow()
            return body if isinstance(body, dict) else {}
        code, message, retry = _failure(body)
        busy = response.status_code == 429 or code in {"RESOURCE_EXHAUSTED", "RESOURCE_TEMPORARILY_EXHAUSTED"} or response.status_code in {500, 503}
        if busy:
            wait = retry if retry is not None else (2 ** attempt)
            if attempt < retries and wait <= MAX_INLINE_WAIT:
                attempt += 1
                await asyncio.sleep(wait)
                continue
            raise AdsBusy("Google Ads asked AIVA to slow down; it will try again shortly.", max(wait, 60))
        friendly = FRIENDLY.get(code)
        if response.status_code == 401 or code in {"OAUTH_TOKEN_REVOKED", "NOT_ADS_USER"}:
            connection.status = "needs_reauth" if response.status_code == 401 or code == "OAUTH_TOKEN_REVOKED" else connection.status
        text = friendly or f"Google Ads said: {message or code or response.status_code}"
        connection.last_error = text[:1000]
        raise IntegrationError(text, 400 if response.status_code < 500 else 502)


async def search(
    db: AsyncSession,
    connection: IntegrationConnection,
    customer_id: str,
    query: str,
    *,
    login_customer_id: str | None = None,
    max_rows: int = 10000,
) -> list[dict[str, Any]]:
    """Run a GAQL query, following pages up to ``max_rows`` rows."""

    rows: list[dict[str, Any]] = []
    page_token: str | None = None
    for _ in range(50):
        payload: dict[str, Any] = {"query": query}
        if page_token:
            payload["pageToken"] = page_token
        body = await call(db, connection, "POST", f"customers/{customer_id}/googleAds:search", json=payload, login_customer_id=login_customer_id)
        rows.extend(body.get("results") or [])
        page_token = body.get("nextPageToken")
        if not page_token or len(rows) >= max_rows:
            break
    return rows[:max_rows]
