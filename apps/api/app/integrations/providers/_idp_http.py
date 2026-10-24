"""Calls to Microsoft Entra ID and Okta: sign-on discovery and directory groups.

Only fixed hosts are called (login.microsoftonline.com, graph.microsoft.com, the
workspace's Okta domain), redirects aren't followed, paging is bounded, and
secrets never appear in messages. Tests swap ``TRANSPORT`` for an
``httpx.MockTransport`` and ``oidc.fetch_json`` for canned discovery documents.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.integrations.core import IntegrationError
from app.services import oidc

TIMEOUT = 15.0
MAX_PAGES = 5
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests only

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
HOST_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SERVER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENTRA_LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, transport=TRANSPORT)


# ---------------------------------------------------------------------------
# Values people paste
# ---------------------------------------------------------------------------


def clean_tenant(value: str) -> str:
    tenant = (value or "").strip().lower()
    for prefix in ("https://login.microsoftonline.com/", "login.microsoftonline.com/"):
        if tenant.startswith(prefix):
            tenant = tenant[len(prefix):].split("/")[0]
    if tenant in {"common", "organizations", "consumers"}:
        raise IntegrationError("Use your own directory (tenant) ID, not “common”: Entra admin center → Overview → Tenant ID.", 422)
    if not (GUID_RE.match(tenant) or HOST_RE.match(tenant)):
        raise IntegrationError("That isn't a tenant ID. Copy the Directory (tenant) ID from the Entra admin center → Overview.", 422)
    return tenant


def clean_okta_domain(value: str) -> str:
    domain = (value or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0].split("?")[0]
    if domain.endswith(("-admin.okta.com", "-admin.oktapreview.com")):
        domain = domain.replace("-admin.", ".", 1)  # people often paste the admin console address
    if not HOST_RE.match(domain) or domain.endswith((".local", ".internal", ".localhost")):
        raise IntegrationError("Enter your Okta domain, like acme.okta.com (without https://).", 422)
    return domain


def clean_server(value: str | None) -> str:
    server = (value or "").strip()
    if server and not SERVER_RE.match(server):
        raise IntegrationError("The authorization server ID has letters, numbers, - and _ only (for example: default).", 422)
    return server


def entra_issuer(tenant_guid: str) -> str:
    return f"{ENTRA_LOGIN}/{tenant_guid}/v2.0"


def okta_issuer(domain: str, server: str | None = None) -> str:
    return f"https://{domain}/oauth2/{server}" if server else f"https://{domain}"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def discover(issuer: str) -> dict[str, Any]:
    try:
        return await oidc.discover(issuer)
    except oidc.OAuthError as exc:
        raise IntegrationError(str(exc), 422) from exc


async def entra_tenant(tenant: str) -> tuple[str, dict[str, Any]]:
    """The tenant's GUID and its sign-on discovery document (confirms the tenant exists)."""

    doc = await discover(f"{ENTRA_LOGIN}/{tenant}/v2.0")
    issuer = str(doc.get("issuer") or "")
    match = re.match(r"^https://login\.microsoftonline\.com/([0-9a-f-]{36})/v2\.0$", issuer, re.IGNORECASE)
    if not match:
        raise IntegrationError("Microsoft didn't recognise that tenant. Check the Directory (tenant) ID.", 422)
    return match.group(1).lower(), doc


# ---------------------------------------------------------------------------
# Microsoft Graph (client credentials)
# ---------------------------------------------------------------------------


def _graph_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = {}
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or response.status_code)[:300]
    return str(data.get("error_description") or error or response.status_code).split("\r\n")[0][:300]


async def graph_token(tenant: str, client_id: str, client_secret: str) -> str:
    try:
        async with client() as http:
            response = await http.post(
                f"{ENTRA_LOGIN}/{quote(tenant)}/oauth2/v2.0/token",
                data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"},
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise IntegrationError("Couldn't reach Microsoft. Try again in a moment.", 502) from exc
    if response.status_code != 200:
        raise IntegrationError(f"Microsoft didn't accept the application ID and secret ({_graph_message(response)}).", 422)
    token = (response.json() or {}).get("access_token")
    if not token:
        raise IntegrationError("Microsoft didn't return an access token.", 502)
    return str(token)


async def graph_groups(tenant: str, client_id: str, client_secret: str, search: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    token = await graph_token(tenant, client_id, client_secret)
    params = "$select=id,displayName,description&$top=100"
    if search:
        safe = search.strip()[:100].replace("'", "''")
        params += f"&$filter=startswith(displayName,'{quote(safe)}')"
    url: str | None = f"{GRAPH}/groups?{params}"
    groups: list[dict[str, Any]] = []
    async with client() as http:
        for _ in range(MAX_PAGES):
            if not url:
                break
            try:
                response = await http.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            except httpx.HTTPError as exc:
                raise IntegrationError("Couldn't reach Microsoft Graph. Try again in a moment.", 502) from exc
            if response.status_code == 403:
                raise IntegrationError("The app registration needs the Microsoft Graph Group.Read.All application permission, with admin consent.", 422)
            if response.status_code != 200:
                raise IntegrationError(f"Microsoft Graph answered {response.status_code} ({_graph_message(response)}).", 502)
            data = response.json() or {}
            for item in data.get("value") or []:
                if item.get("id") and item.get("displayName"):
                    groups.append({"id": str(item["id"]), "name": str(item["displayName"])[:300], "description": (item.get("description") or "")[:300]})
            next_link = data.get("@odata.nextLink")
            url = next_link if isinstance(next_link, str) and next_link.startswith(f"{GRAPH}/") else None
            if len(groups) >= limit:
                break
    return sorted(groups[:limit], key=lambda g: g["name"].lower())


# ---------------------------------------------------------------------------
# Okta API (SSWS token)
# ---------------------------------------------------------------------------


def _okta_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = {}
    return str(data.get("errorSummary") or response.status_code)[:300]


def _next_link(header: str | None, host: str) -> str | None:
    for part in (header or "").split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url if urlparse(url).scheme == "https" and urlparse(url).hostname == host else None
    return None


async def okta_groups(domain: str, api_token: str, search: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    url: str | None = f"https://{domain}/api/v1/groups?limit=200"
    if search:
        url += f"&q={quote(search.strip()[:100])}"
    groups: list[dict[str, Any]] = []
    async with client() as http:
        for _ in range(MAX_PAGES):
            if not url:
                break
            try:
                response = await http.get(url, headers={"Authorization": f"SSWS {api_token}", "Accept": "application/json"})
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Couldn't reach {domain}. Try again in a moment.", 502) from exc
            if response.status_code in {401, 403}:
                raise IntegrationError(f"Okta didn't accept the API token ({_okta_message(response)}). Create one under Security → API → Tokens.", 422)
            if response.status_code != 200:
                raise IntegrationError(f"Okta answered {response.status_code} ({_okta_message(response)}).", 502)
            for item in response.json() or []:
                profile = item.get("profile") or {}
                if item.get("id") and profile.get("name"):
                    groups.append({"id": str(item["id"]), "name": str(profile["name"])[:300], "description": (profile.get("description") or "")[:300]})
            url = _next_link(response.headers.get("link"), domain)
            if len(groups) >= limit:
                break
    return sorted(groups[:limit], key=lambda g: g["name"].lower())
