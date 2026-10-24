"""Waterfall enrichment: try data sources in order until each gap is filled.

Sources (Settings → Data management → Waterfall enrichment):

* **builtin** – free rules: company and website from a business email,
  seniority and department from a title, phone format, industry.
* **website** – free: reads the company's home page for a description,
  phone number, LinkedIn page and contact email.
* **hunter** – Hunter.io email finder and domain search (your API key).
* **pdl** – People Data Labs person and company enrichment (your API key).
* **apollo**, **zoominfo** – Apollo.io and ZoomInfo, through the workspace's
  connection in Integrations (turned on and connected there).

Outside providers cost one credit for each record they fill something in
on (five when they find a mobile number). Only empty fields are filled
unless the workspace turns on overwriting.
"""

from __future__ import annotations

import html
import logging
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.services import metering, workspace_settings
from app.services.enrichment import FREE_EMAIL_DOMAINS, department, email_domain, enrich, seniority

logger = logging.getLogger("aiva.enrichment")

PROVIDERS = {
    "builtin": {"label": "AIVA rules", "cost": 0, "needs_key": None, "description": "Company and website from business emails, seniority and department from titles, tidy phone numbers."},
    "website": {"label": "Company website", "cost": 0, "needs_key": None, "description": "Reads the company's home page for a description, phone number, LinkedIn page and contact email."},
    "hunter": {"label": "Hunter.io", "cost": 1, "needs_key": "hunter_api_key", "description": "Finds work email addresses from a name and company domain, plus company details."},
    "pdl": {"label": "People Data Labs", "cost": 1, "needs_key": "pdl_api_key", "description": "Job titles, mobile numbers, LinkedIn profiles, locations and company firmographics."},
    "apollo": {
        "label": "Apollo.io", "cost": 1, "needs_key": None, "integration": "apollo",
        "description": "Work emails, titles, seniority, phones and company data from Apollo. Connect Apollo in Integrations.",
    },
    "zoominfo": {
        "label": "ZoomInfo", "cost": 1, "needs_key": None, "integration": "zoominfo",
        "description": "Contact and company data from ZoomInfo: titles, direct dials, firmographics. Connect ZoomInfo in Integrations.",
    },
}
PAID = {key for key, spec in PROVIDERS.items() if spec["cost"]}
FIELD_LABELS = {
    "email": "Email", "phone": "Phone", "mobile": "Mobile", "job_title": "Job title", "company": "Company", "website": "Website",
    "industry": "Industry", "employees": "Employees", "location": "Location", "seniority": "Seniority", "department": "Department",
    "linkedin": "LinkedIn", "description": "Description",
}
USER_AGENT = "Mozilla/5.0 (compatible; AIVA-CRM-Enrichment/1.0; +https://aiva.example/bot)"


class ProviderError(Exception):
    pass


# ---------------------------------------------------------------------------
# Reading and writing record fields
# ---------------------------------------------------------------------------


def _cf(record: Any, key: str) -> Any:
    return (record.custom_fields or {}).get(key)


def current(record: Any, field: str) -> Any:
    if isinstance(record, Account):
        mapping = {
            "website": record.website, "industry": record.industry, "phone": record.phone, "employees": record.employee_count,
            "description": record.description, "company": record.name,
            "location": ", ".join(x for x in (record.billing_city, record.billing_state, record.billing_country) if x) or None,
            "linkedin": _cf(record, "linkedin"),
        }
        return mapping.get(field)
    mapping = {
        "email": record.email, "phone": record.phone, "job_title": record.job_title,
        "mobile": getattr(record, "mobile", None) or _cf(record, "mobile"),
        "company": getattr(record, "company_name", None) or _cf(record, "company"),
        "linkedin": getattr(record, "linkedin_url", None) or _cf(record, "linkedin"),
        "department": getattr(record, "department", None) or _cf(record, "department"),
        "seniority": _cf(record, "seniority") or ((_cf(record, "_enrichment") or {}).get("seniority")),
        "location": _cf(record, "location"),
        "website": _cf(record, "website"),
    }
    return mapping.get(field)


def write(record: Any, field: str, value: Any) -> bool:
    if value in (None, "", []):
        return False
    cf = dict(record.custom_fields or {})
    if isinstance(record, Account):
        if field == "website":
            record.website = str(value)[:500]
            record.domain = record.domain or (urlsplit(str(value) if "//" in str(value) else f"https://{value}").hostname or "").removeprefix("www.") or None
        elif field == "industry":
            record.industry = str(value)[:150]
        elif field == "phone":
            record.phone = str(value)[:50]
        elif field == "employees":
            try:
                record.employee_count = int(str(value).replace(",", "").split("-")[0].strip())
            except ValueError:
                return False
        elif field == "description":
            record.description = str(value)[:5000]
        elif field == "location":
            parts = [p.strip() for p in str(value).split(",")]
            if len(parts) >= 3:
                record.billing_city, record.billing_state, record.billing_country = parts[0][:120], parts[1][:120], parts[-1][:120]
            else:
                cf["location"] = str(value)[:200]
        elif field == "linkedin":
            cf["linkedin"] = str(value)[:500]
        else:
            return False
        record.custom_fields = cf
        return True
    if field == "email":
        record.email = str(value).lower()[:320]
    elif field == "phone":
        record.phone = str(value)[:50]
    elif field == "job_title":
        record.job_title = str(value)[:200]
    elif field == "mobile":
        if hasattr(record, "mobile"):
            record.mobile = str(value)[:50]
        else:
            cf["mobile"] = str(value)[:50]
    elif field == "company":
        if hasattr(record, "company_name"):
            record.company_name = str(value)[:200]
        else:
            cf["company"] = str(value)[:200]
    elif field == "linkedin":
        if hasattr(record, "linkedin_url"):
            record.linkedin_url = str(value)[:500]
        else:
            cf["linkedin"] = str(value)[:500]
    elif field == "department":
        if hasattr(record, "department"):
            record.department = str(value)[:150]
        else:
            cf["department"] = str(value)[:150]
    elif field in {"seniority", "location", "website"}:
        cf[field] = str(value)[:300]
    else:
        return False
    record.custom_fields = cf
    return True


def _domain_for(record: Any) -> str | None:
    if isinstance(record, Account):
        if record.domain:
            return record.domain.lower()
        if record.website:
            return (urlsplit(record.website if "//" in record.website else f"https://{record.website}").hostname or "").removeprefix("www.") or None
        return None
    domain = email_domain(getattr(record, "email", None))
    if domain and domain not in FREE_EMAIL_DOMAINS:
        return domain
    website = _cf(record, "website")
    if website:
        return (urlsplit(website if "//" in website else f"https://{website}").hostname or "").removeprefix("www.") or None
    return None


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def builtin_values(record: Any) -> dict[str, Any]:
    """What the free rules can tell, without changing the record."""

    out: dict[str, Any] = {}
    if isinstance(record, (Lead, Contact)):
        domain = _domain_for(record)
        if domain:
            out["website"] = f"https://{domain}"
            if isinstance(record, Lead):
                out["company"] = domain.split(".")[0].replace("-", " ").title()
        if record.job_title:
            level = seniority(record.job_title)
            dept = department(record.job_title)
            if level:
                out["seniority"] = level
            if dept:
                out["department"] = dept
    else:
        probe = Account(name=record.name, domain=record.domain, website=record.website, industry=record.industry, phone=record.phone, custom_fields={})
        result = enrich(probe)
        for key, value in (result.get("applied") or {}).items():
            out[{"website": "website", "industry": "industry", "phone": "phone"}.get(key, key)] = value
    return {k: v for k, v in out.items() if k in FIELD_LABELS}


TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META_RE = re.compile(r"<meta\s+[^>]*>", re.I)
ATTR_RE = re.compile(r'([a-zA-Z:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')
TEL_RE = re.compile(r'href=["\']tel:([+0-9 ()./-]{7,})["\']', re.I)
MAILTO_RE = re.compile(r'href=["\']mailto:([^"\'?]+@[^"\'?]+)["\']', re.I)
LINKEDIN_RE = re.compile(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/[A-Za-z0-9_\-%.]+/?', re.I)


def parse_homepage(body: str) -> dict[str, Any]:
    """Pull company facts from a home page's HTML."""

    out: dict[str, Any] = {}
    metas: dict[str, str] = {}
    for tag in META_RE.findall(body[:400_000]):
        attrs = {m.group(1).lower(): html.unescape(m.group(3) if m.group(3) is not None else (m.group(4) or "")) for m in ATTR_RE.finditer(tag)}
        key = attrs.get("name") or attrs.get("property")
        if key and attrs.get("content"):
            metas[key.lower()] = attrs["content"].strip()
    description = metas.get("description") or metas.get("og:description")
    if description:
        out["description"] = re.sub(r"\s+", " ", description)[:1000]
    name = metas.get("og:site_name")
    if not name:
        title = TITLE_RE.search(body)
        if title:
            name = re.split(r"\s+[|\-–—:]\s+", html.unescape(re.sub(r"\s+", " ", title.group(1))).strip())[0]
    if name:
        out["company"] = name[:200]
    phone = TEL_RE.search(body)
    if phone:
        out["phone"] = re.sub(r"\s+", " ", phone.group(1)).strip()
    mail = MAILTO_RE.search(body)
    if mail:
        out["email"] = mail.group(1).strip().lower()
    linkedin = LINKEDIN_RE.search(body)
    if linkedin:
        out["linkedin"] = linkedin.group(0).rstrip("/")
    return out


async def website_values(record: Any) -> dict[str, Any]:
    domain = _domain_for(record)
    if not domain:
        raise ProviderError("No company domain to look up.")
    url = f"https://{domain}/"
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Couldn't open {domain}: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        raise ProviderError(f"{domain} answered {response.status_code}.")
    if "html" not in response.headers.get("content-type", "html"):
        raise ProviderError(f"{domain} didn't return a web page.")
    values = parse_homepage(response.text[:600_000])
    values["website"] = str(response.url).split("?")[0] if response.url else url
    if isinstance(record, (Lead, Contact)):
        # A contact's own email isn't the company's info@ address.
        values.pop("email", None)
        values.pop("description", None)
        values.pop("phone", None)
    else:
        values.pop("email", None)
        values.pop("company", None)
    return {k: v for k, v in values.items() if v}


async def hunter_values(record: Any, api_key: str) -> dict[str, Any]:
    domain = _domain_for(record)
    async with httpx.AsyncClient(timeout=15) as client:
        if isinstance(record, (Lead, Contact)):
            if not domain or not record.first_name or not record.last_name:
                raise ProviderError("Hunter needs a first name, last name and company domain.")
            response = await client.get(
                "https://api.hunter.io/v2/email-finder",
                params={"domain": domain, "first_name": record.first_name, "last_name": record.last_name, "api_key": api_key},
            )
        else:
            if not domain:
                raise ProviderError("Hunter needs the company's domain.")
            response = await client.get("https://api.hunter.io/v2/domain-search", params={"domain": domain, "limit": 1, "api_key": api_key})
    body = response.json() if response.content else {}
    if response.status_code == 401:
        raise ProviderError("Hunter rejected the API key.")
    if response.status_code == 429:
        raise ProviderError("Hunter's rate limit was reached; try again later.")
    if response.status_code >= 400:
        raise ProviderError(((body.get("errors") or [{}])[0]).get("details") or f"Hunter answered {response.status_code}.")
    data = body.get("data") or {}
    if isinstance(record, (Lead, Contact)):
        out = {"email": data.get("email"), "job_title": data.get("position"), "linkedin": data.get("linkedin_url"), "phone": data.get("phone_number")}
        if data.get("company"):
            out["company"] = data["company"]
        if (data.get("score") or 0) < 50:
            out.pop("email", None)
    else:
        out = {"industry": data.get("industry"), "description": data.get("description"), "linkedin": data.get("linkedin"), "employees": data.get("headcount")}
        location = ", ".join(x for x in (data.get("city"), data.get("state"), data.get("country")) if x)
        if location:
            out["location"] = location
    return {k: v for k, v in out.items() if v}


async def pdl_values(record: Any, api_key: str) -> dict[str, Any]:
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        if isinstance(record, (Lead, Contact)):
            params: dict[str, Any] = {"min_likelihood": 6}
            if record.email:
                params["email"] = record.email
            if record.first_name and record.last_name:
                params["first_name"], params["last_name"] = record.first_name, record.last_name
            company = getattr(record, "company_name", None) or _domain_for(record)
            if company:
                params["company"] = company
            if "email" not in params and "company" not in params:
                raise ProviderError("People Data Labs needs an email, or a name and company.")
            response = await client.get("https://api.peopledatalabs.com/v5/person/enrich", params=params)
        else:
            domain = _domain_for(record)
            if not domain and not record.name:
                raise ProviderError("People Data Labs needs a company name or website.")
            response = await client.get("https://api.peopledatalabs.com/v5/company/enrich", params={"website": domain} if domain else {"name": record.name})
    body = response.json() if response.content else {}
    if response.status_code == 404:
        return {}
    if response.status_code in (401, 403):
        raise ProviderError("People Data Labs rejected the API key.")
    if response.status_code == 402:
        raise ProviderError("Your People Data Labs account is out of credits.")
    if response.status_code >= 400:
        raise ProviderError((body.get("error") or {}).get("message") or f"People Data Labs answered {response.status_code}.")
    if isinstance(record, (Lead, Contact)):
        data = body.get("data") or {}
        out = {
            "job_title": (data.get("job_title") or "").title() or None,
            "mobile": data.get("mobile_phone"),
            "linkedin": f"https://{data['linkedin_url']}" if data.get("linkedin_url") and not str(data["linkedin_url"]).startswith("http") else data.get("linkedin_url"),
            "location": data.get("location_name"),
            "company": (data.get("job_company_name") or "").title() or None,
            "email": data.get("work_email") if isinstance(data.get("work_email"), str) else None,
            "seniority": ", ".join(data.get("job_title_levels") or []) or None,
        }
    else:
        location = body.get("location") or {}
        out = {
            "industry": (body.get("industry") or "").title() or None,
            "employees": body.get("employee_count"),
            "linkedin": f"https://{body['linkedin_url']}" if body.get("linkedin_url") and not str(body["linkedin_url"]).startswith("http") else body.get("linkedin_url"),
            "description": body.get("summary"),
            "website": f"https://{body['website']}" if body.get("website") and not str(body["website"]).startswith("http") else body.get("website"),
            "location": ", ".join(x.title() for x in (location.get("locality"), location.get("region"), location.get("country")) if x) or None,
        }
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# The waterfall
# ---------------------------------------------------------------------------


async def run(
    db: AsyncSession,
    principal: Any,
    record_type: str,
    record: Any,
    *,
    wanted: set[str] | None = None,
    charge: bool = True,
    apply: bool = True,
) -> dict[str, Any]:
    org = principal.organization_id
    settings = await workspace_settings.get(db, org, "enrichment")
    fields = set(wanted) if wanted else set(settings.fields)
    found: dict[str, Any] = {}
    applied: dict[str, Any] = {}
    tried: list[dict[str, Any]] = []
    credits = 0

    def missing() -> set[str]:
        return {f for f in fields if f not in found and (settings.overwrite or current(record, f) in (None, ""))}

    for provider in settings.providers:
        if not provider.enabled:
            continue
        gaps = missing()
        if not gaps:
            break
        spec = PROVIDERS[provider.key]
        entry: dict[str, Any] = {"provider": provider.key, "label": spec["label"], "status": "ok", "fields": [], "credits": 0}
        try:
            if provider.key == "builtin":
                values = builtin_values(record)
            elif provider.key == "website":
                values = await website_values(record)
            elif spec.get("integration"):
                if "waterfall_enrichment" not in principal.features:
                    raise ProviderError("Waterfall enrichment isn't included in your plan.")
                if charge:
                    balance = await metering.credit_balance(db, org, principal.user_id)
                    if balance["remaining"] < 1:
                        raise ProviderError("Out of credits for this month.")
                values = await integration_values(db, org, provider.key, record)
            else:
                if provider.key in PAID and "waterfall_enrichment" not in principal.features:
                    raise ProviderError("Waterfall enrichment isn't included in your plan.")
                key = await workspace_settings.secret(db, org, "enrichment", spec["needs_key"])
                if not key:
                    raise ProviderError("Add an API key to use this provider.")
                if charge:
                    balance = await metering.credit_balance(db, org, principal.user_id)
                    if balance["remaining"] < 1:
                        raise ProviderError("Out of credits for this month.")
                values = await (hunter_values(record, key) if provider.key == "hunter" else pdl_values(record, key))
        except ProviderError as exc:
            entry.update(status="skipped", message=str(exc))
            tried.append(entry)
            continue
        except (httpx.HTTPError, ValueError) as exc:
            entry.update(status="error", message=f"{spec['label']} couldn't be reached ({exc.__class__.__name__}).")
            tried.append(entry)
            continue
        new = {k: v for k, v in values.items() if k in gaps}
        found.update(new)
        entry["fields"] = sorted(new)
        if spec["cost"] and new:
            cost = 5 if "mobile" in new else spec["cost"]
            entry["credits"] = cost
            credits += cost
            if charge:
                await metering.charge_credits(
                    db, org, principal.user_id, cost, "enrichment", record_type=record_type, record_id=record.id,
                    description=f"{spec['label']}: {', '.join(FIELD_LABELS.get(f, f) for f in sorted(new))}", check=False,
                )
        if not new:
            entry["status"] = "nothing_new"
        tried.append(entry)

    if apply:
        for field, value in found.items():
            if write(record, field, value):
                applied[field] = value
        if applied:
            cf = dict(record.custom_fields or {})
            log = dict(cf.get("_enrichment") or {})
            log["waterfall"] = {"fields": sorted(applied), "sources": [t["provider"] for t in tried if t["fields"]], "at": metering.utcnow().isoformat()}
            cf["_enrichment"] = log
            record.custom_fields = cf
    return {"found": found, "applied": applied, "tried": tried, "credits": credits}


async def integration_values(db: AsyncSession, organization_id: uuid.UUID, provider: str, record: Any) -> dict[str, Any]:
    """Apollo / ZoomInfo through the workspace's connection in Integrations."""

    from app.integrations.core import IntegrationError
    from app.integrations.providers import sales_intel

    try:
        return await sales_intel.waterfall_values(db, organization_id, provider, record)
    except IntegrationError as exc:
        raise ProviderError(str(exc)) from exc


async def integration_ready(db: AsyncSession, organization_id: uuid.UUID, provider: str) -> bool:
    from app.integrations import core

    if not await core.is_enabled(db, organization_id, provider):
        return False
    connection = await core.connection_for(db, organization_id, provider)
    return bool(connection and connection.status == "connected")


async def test_provider(db: AsyncSession, organization_id: uuid.UUID, provider: str) -> dict[str, Any]:
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ProviderError("Unknown provider.")
    if spec.get("integration"):
        from app.integrations import core
        from app.integrations.core import IntegrationError
        from app.integrations.providers import sales_intel

        try:
            connection = await core.active_connection(db, organization_id, provider)
            return {"ok": True, "message": await sales_intel.test(db, provider, connection, None)}
        except IntegrationError as exc:
            return {"ok": False, "message": str(exc)}
    if provider == "builtin":
        return {"ok": True, "message": "Built-in rules are always available."}
    if provider == "website":
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
                response = await client.get("https://example.com/")
            return {"ok": response.status_code < 400, "message": "AIVA can read company websites." if response.status_code < 400 else f"Got {response.status_code}."}
        except httpx.HTTPError as exc:
            return {"ok": False, "message": f"AIVA can't reach the internet from here ({exc.__class__.__name__})."}
    key = await workspace_settings.secret(db, organization_id, "enrichment", spec["needs_key"])
    if not key:
        return {"ok": False, "message": "Add an API key first."}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if provider == "hunter":
                response = await client.get("https://api.hunter.io/v2/account", params={"api_key": key})
                if response.status_code == 200:
                    data = (response.json() or {}).get("data") or {}
                    searches = ((data.get("requests") or {}).get("searches") or {})
                    return {"ok": True, "message": f"Connected as {data.get('email', 'your account')}. {searches.get('available', 0) - searches.get('used', 0)} searches left this month."}
            else:
                response = await client.get("https://api.peopledatalabs.com/v5/person/enrich", params={"email": "test@example.com"}, headers={"X-Api-Key": key})
                if response.status_code in (200, 404):
                    return {"ok": True, "message": "People Data Labs accepted the API key."}
        return {"ok": False, "message": f"{spec['label']} rejected the key ({response.status_code})."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"{spec['label']} couldn't be reached ({exc.__class__.__name__})."}


def provider_list() -> list[dict[str, Any]]:
    return [{"key": k, **v} for k, v in PROVIDERS.items()]

