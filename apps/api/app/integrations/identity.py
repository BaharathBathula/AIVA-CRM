"""Who connected: the account behind a fresh OAuth token, per provider family.

Returns ``label`` (an email or workspace name), ``external_id``, and optionally
``config`` / ``secrets`` to store on the connection, or replacement tokens
(Facebook swaps the short-lived token for a long-lived one).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.catalog import FAMILIES
from app.integrations.core import IntegrationError

GRAPH = "https://graph.facebook.com/v21.0"


async def _get(url: str, token: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, **kwargs)
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Couldn't read the connected account ({exc.__class__.__name__}).", 502) from exc
    if response.status_code >= 400:
        raise IntegrationError(f"Couldn't read the connected account ({response.status_code}).", 502)
    return response.json()


async def identify(family: str, key: str, tokens: dict[str, Any], app: dict[str, Any]) -> dict[str, Any]:
    token = tokens.get("access_token") or ""
    if family == "google":
        info = await _get("https://openidconnect.googleapis.com/v1/userinfo", token)
        return {"label": info.get("email"), "external_id": info.get("sub"), "config": {"name": info.get("name")}}
    if family == "microsoft":
        info = await _get("https://graph.microsoft.com/v1.0/me", token)
        email = info.get("mail") or info.get("userPrincipalName")
        return {"label": email, "external_id": info.get("id"), "config": {"name": info.get("displayName")}}
    if family == "slack":
        team = tokens.get("team") or {}
        return {
            "label": team.get("name") or "Slack workspace",
            "external_id": team.get("id"),
            "config": {"team_name": team.get("name"), "bot_user_id": tokens.get("bot_user_id"), "app_id": tokens.get("app_id")},
        }
    if family == "zoom":
        info = await _get("https://api.zoom.us/v2/users/me", token)
        return {"label": info.get("email"), "external_id": info.get("id"), "config": {"name": f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()}}
    if family == "dropbox":
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post("https://api.dropboxapi.com/2/users/get_current_account", headers={"Authorization": f"Bearer {token}"})
            info = response.json() if response.status_code < 400 else {}
        except (httpx.HTTPError, ValueError):
            info = {}
        return {"label": info.get("email") or "Dropbox account", "external_id": info.get("account_id") or tokens.get("account_id")}
    if family == "docusign":
        environment = (app.get("options") or {}).get("environment") or get_settings().docusign_environment or "demo"
        host = "account.docusign.com" if environment == "production" else "account-d.docusign.com"
        info = await _get(f"https://{host}/oauth/userinfo", token)
        accounts = info.get("accounts") or []
        account = next((a for a in accounts if a.get("is_default")), accounts[0] if accounts else {})
        if not account:
            raise IntegrationError("That DocuSign login has no eSignature account.", 400)
        return {
            "label": f"{account.get('account_name') or 'DocuSign'} ({info.get('email')})",
            "external_id": account.get("account_id"),
            "config": {"account_id": account.get("account_id"), "base_uri": account.get("base_uri"), "environment": environment, "email": info.get("email")},
        }
    if family == "calendly":
        info = (await _get("https://api.calendly.com/users/me", token)).get("resource") or {}
        return {
            "label": info.get("email") or info.get("name"),
            "external_id": info.get("uri"),
            "config": {"user_uri": info.get("uri"), "organization_uri": info.get("current_organization"), "scheduling_url": info.get("scheduling_url"), "name": info.get("name")},
        }
    if family == "facebook":
        # Swap for a long-lived token; page tokens made from it don't expire.
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{GRAPH}/oauth/access_token",
                    params={"grant_type": "fb_exchange_token", "client_id": app["client_id"], "client_secret": app["client_secret"], "fb_exchange_token": token},
                )
            long_lived = response.json() if response.status_code < 400 else {}
        except (httpx.HTTPError, ValueError):
            long_lived = {}
        token = long_lived.get("access_token") or token
        info = await _get(f"{GRAPH}/me", token, params={"fields": "id,name"})
        return {
            "label": info.get("name") or "Facebook account",
            "external_id": info.get("id"),
            "access_token": token,
            "expires_in": long_lived.get("expires_in", tokens.get("expires_in")),
        }
    if family == "constant_contact":
        info = await _get("https://api.cc.email/v3/account/summary", token)
        return {
            "label": info.get("organization_name") or info.get("contact_email") or "Constant Contact account",
            "external_id": info.get("encoded_account_id"),
            "config": {"email": info.get("contact_email"), "organization_name": info.get("organization_name")},
        }
    if family == "intuit":
        realm = str((tokens.get("_callback") or {}).get("realmId") or "")
        if not realm.isdigit():
            raise IntegrationError("QuickBooks didn't say which company was chosen. Connect again and pick a company.", 400)
        environment = (app.get("options") or {}).get("environment") or get_settings().quickbooks_environment or "production"
        base = "https://sandbox-quickbooks.api.intuit.com" if environment == "sandbox" else "https://quickbooks.api.intuit.com"
        info = (await _get(f"{base}/v3/company/{realm}/companyinfo/{realm}", token, params={"minorversion": "75"})).get("CompanyInfo") or {}
        return {
            "label": info.get("CompanyName") or f"QuickBooks company {realm}",
            "external_id": realm,
            "config": {"realm_id": realm, "environment": environment, "api_base": base, "company_name": info.get("CompanyName"), "country": info.get("Country")},
        }
    if family == "xero":
        tenants = await _get_list("https://api.xero.com/connections", token)
        orgs = [t for t in tenants if t.get("tenantType") == "ORGANISATION"] or tenants
        if not orgs:
            raise IntegrationError("That Xero login didn't share any organisation with AIVA. Connect again and tick an organisation.", 400)
        first = orgs[0]
        return {
            "label": first.get("tenantName") or "Xero organisation",
            "external_id": first.get("tenantId"),
            "config": {"tenant_id": first.get("tenantId"), "tenant_name": first.get("tenantName"),
                       "tenants": [{"id": t.get("tenantId"), "name": t.get("tenantName")} for t in orgs][:20]},
        }
    if family == "freshbooks":
        info = (await _get("https://api.freshbooks.com/auth/api/v1/users/me", token)).get("response") or {}
        memberships = [m.get("business") or {} for m in info.get("business_memberships") or []]
        business = next((b for b in memberships if b.get("account_id")), {})
        if not business:
            raise IntegrationError("That FreshBooks login has no business with accounting.", 400)
        return {
            "label": business.get("name") or info.get("email") or "FreshBooks business",
            "external_id": business.get("account_id"),
            "config": {"account_id": business.get("account_id"), "business_id": business.get("id"), "business_name": business.get("name"), "email": info.get("email"),
                       "businesses": [{"account_id": b.get("account_id"), "id": b.get("id"), "name": b.get("name")} for b in memberships if b.get("account_id")][:20]},
        }
    if family == "sage":
        businesses = await _get_list("https://api.accounting.sage.com/v3.1/businesses", token)
        if not businesses:
            raise IntegrationError("That Sage login has no Sage Accounting business.", 400)
        first = next((b for b in businesses if b.get("is_lead") or b.get("owner")), businesses[0])
        return {
            "label": first.get("name") or "Sage business",
            "external_id": first.get("id"),
            "config": {"business_id": first.get("id"), "business_name": first.get("name"),
                       "businesses": [{"id": b.get("id"), "name": b.get("name")} for b in businesses][:20]},
        }
    if family == "linkedin":
        info = await _get("https://api.linkedin.com/v2/userinfo", token)
        return {"label": info.get("email") or info.get("name") or "LinkedIn member", "external_id": info.get("sub"), "config": {"name": info.get("name")}}
    raise IntegrationError(f"Unsupported provider {FAMILIES.get(family, family)}.", 400)


async def _get_list(url: str, token: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Like ``_get`` for endpoints that answer with a JSON array (or ``{"$items": [...]}``)."""

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, **kwargs)
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Couldn't read the connected account ({exc.__class__.__name__}).", 502) from exc
    if response.status_code >= 400:
        raise IntegrationError(f"Couldn't read the connected account ({response.status_code}).", 502)
    data = response.json()
    if isinstance(data, dict):
        data = data.get("$items") or data.get("items") or []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
