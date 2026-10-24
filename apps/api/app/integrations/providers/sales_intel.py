"""Apollo.io and ZoomInfo: enrichment and prospect import.

* Connecting: Apollo with an API key (``x-api-key`` header; a master key for
  people search), ZoomInfo with PKI authentication: AIVA signs a short RS256
  JWT with the workspace's private key, swaps it at ``/authenticate`` for an
  access token and keeps that (encrypted) for 55 minutes.
* Enrichment: both join the waterfall (Settings → Data management → Waterfall
  enrichment) like Hunter.io and People Data Labs. People are matched by email,
  or name plus company domain; companies by domain. Like the other outside
  providers they cost one AIVA credit per record they fill something in on
  (five with a mobile number), on top of the provider's own credits.
* Prospects: search people and companies with the same filters for both
  (titles, seniority, locations, company size, industry, keywords) and import
  the ones you pick as leads, contacts (with their account) or accounts. Each
  imported person is enriched first (that's where emails come from), people on
  the do-not-contact list or already in AIVA are skipped, and each import is
  capped and charged one credit per record created.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _sales_http
from app.models.account import Account
from app.models.contact import Contact
from app.models.integration_hub import IntegrationConnection, IntegrationRecordLink
from app.models.lead import Lead

KEYS = ("apollo", "zoominfo")
VENDOR = {"apollo": "Apollo.io", "zoominfo": "ZoomInfo"}
APOLLO = "https://api.apollo.io/api/v1"
ZOOMINFO = "https://api.zoominfo.com"
ZOOMINFO_TOKEN_LIFE = timedelta(minutes=55)
MAX_IMPORT = 50
MAX_PER_PAGE = 25
SENIORITIES = ("owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry", "intern")
ZI_LEVEL = {
    "owner": "C Level Exec", "founder": "C Level Exec", "c_suite": "C Level Exec", "partner": "C Level Exec",
    "vp": "VP Level Exec", "head": "VP Level Exec", "director": "Director", "manager": "Manager",
    "senior": "Non Manager", "entry": "Non Manager", "intern": "Non Manager",
}
EMPLOYEE_RANGES = ("1,10", "11,50", "51,200", "201,500", "501,1000", "1001,5000", "5001,10000", "10001,1000000")
ZI_CONTACT_FIELDS = [
    "id", "firstName", "lastName", "email", "phone", "mobilePhone", "jobTitle", "managementLevel", "jobFunction",
    "companyId", "companyName", "companyWebsite", "city", "state", "country", "externalUrls",
]
ZI_COMPANY_FIELDS = ["id", "name", "website", "phone", "industries", "employeeCount", "revenue", "city", "state", "country", "descriptionList", "socialMediaUrls"]
DEFAULTS = {"lead_source": None, "owner_user_id": None, "import_as": "lead"}


# ---------------------------------------------------------------------------
# Talking to the providers
# ---------------------------------------------------------------------------


def _apollo_headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "Accept": "application/json", "Content-Type": "application/json", "Cache-Control": "no-cache"}


async def _apollo(connection: IntegrationConnection | None, method: str, path: str, *, api_key: str | None = None, ok: tuple[int, ...] = (), **kwargs: Any) -> Any:
    key = api_key or (core.secret(connection, "api_key") if connection is not None else None)
    if not key:
        raise IntegrationError("Paste an Apollo API key first.", 409)
    return await _sales_http.call(VENDOR["apollo"], method, f"{APOLLO}{path}", connection=connection, headers=_apollo_headers(key), ok=ok, **kwargs)


def client_jwt(username: str, client_id: str, private_key: str, *, now: float | None = None) -> str:
    """The short-lived JWT ZoomInfo's PKI authentication expects, signed with the private key."""

    import jwt

    issued = int(now or time.time())
    claims = {"aud": "enterprise_api", "iss": "api-client@zoominfo.com", "iat": issued, "exp": issued + 300, "client_id": client_id, "username": username}
    try:
        return jwt.encode(claims, private_key, algorithm="RS256")
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise IntegrationError("That private key couldn't be read. Paste the whole PEM, including the BEGIN and END lines.", 422) from exc


async def _zoominfo_login(username: str, client_id: str, private_key: str) -> str:
    response = await _sales_http.call(
        VENDOR["zoominfo"], "POST", f"{ZOOMINFO}/authenticate",
        headers={"Authorization": f"Bearer {client_jwt(username, client_id, private_key)}", "Accept": "application/json"},
    )
    token = response.json().get("jwt")
    if not token:
        raise IntegrationError("ZoomInfo didn't return an access token.", 502)
    return str(token)


async def _zoominfo_token(connection: IntegrationConnection, *, force: bool = False) -> str:
    config = connection.config or {}
    cached = core.secret(connection, "access_jwt")
    expires = config.get("jwt_expires_at")
    if cached and expires and not force and expires > core.utcnow().isoformat():
        return cached
    private_key = core.secret(connection, "private_key")
    if not private_key or not config.get("username") or not config.get("client_id"):
        raise IntegrationError("Paste your ZoomInfo username, client ID and private key first.", 409)
    token = await _zoominfo_login(config["username"], config["client_id"], private_key)
    core.set_secret(connection, "access_jwt", token)
    connection.config = {**config, "jwt_expires_at": (core.utcnow() + ZOOMINFO_TOKEN_LIFE).isoformat()}
    return token


async def _zoominfo(connection: IntegrationConnection, path: str, body: dict[str, Any]) -> dict[str, Any]:
    for attempt in (0, 1):
        token = await _zoominfo_token(connection, force=bool(attempt))
        response = await _sales_http.call(
            VENDOR["zoominfo"], "POST", f"{ZOOMINFO}{path}", connection=connection, ok=(401,),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}, json=body,
        )
        if response.status_code != 401:
            return response.json()
    raise IntegrationError("ZoomInfo refused the access token. Check the username, client ID and private key.", 400)


# ---------------------------------------------------------------------------
# Normalising people and companies
# ---------------------------------------------------------------------------


def _domain(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    host = urlsplit(text if "//" in text else f"https://{text}").hostname or ""
    host = host.removeprefix("www.")
    return host if "." in host else None


def _join(*parts: Any) -> str | None:
    return ", ".join(str(p) for p in parts if p) or None


def apollo_person(raw: dict[str, Any]) -> dict[str, Any]:
    org = raw.get("organization") or {}
    phones = raw.get("phone_numbers") or []
    mobile = next((p.get("sanitized_number") or p.get("raw_number") for p in phones if str(p.get("type") or "").lower() == "mobile"), None)
    phone = next((p.get("sanitized_number") or p.get("raw_number") for p in phones), None)
    email = raw.get("email")
    if email and ("not_unlocked" in str(email) or str(raw.get("email_status") or "").lower() in {"unavailable"}):
        email = None
    return {
        "id": str(raw.get("id") or ""),
        "first_name": raw.get("first_name") or "",
        "last_name": raw.get("last_name") or raw.get("last_name_obfuscated") or "",
        "name": raw.get("name") or _join(raw.get("first_name"), raw.get("last_name") or raw.get("last_name_obfuscated")),
        "title": raw.get("title"),
        "seniority": raw.get("seniority"),
        "department": ", ".join(raw.get("departments") or []) or None,
        "email": str(email).lower() if email else None,
        "email_status": raw.get("email_status"),
        "has_email": bool(email) or bool(raw.get("has_email")),
        "phone": phone,
        "mobile": mobile,
        "linkedin": raw.get("linkedin_url"),
        "location": _join(raw.get("city"), raw.get("state"), raw.get("country")),
        "company": org.get("name") or raw.get("organization_name"),
        "domain": _domain(org.get("primary_domain") or org.get("website_url")),
        "company_id": str(org.get("id") or raw.get("organization_id") or "") or None,
    }


def apollo_company(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or ""),
        "name": raw.get("name") or "",
        "domain": _domain(raw.get("primary_domain") or raw.get("website_url")),
        "website": raw.get("website_url"),
        "industry": raw.get("industry"),
        "employees": raw.get("estimated_num_employees"),
        "phone": (raw.get("primary_phone") or {}).get("sanitized_number") if isinstance(raw.get("primary_phone"), dict) else raw.get("phone"),
        "linkedin": raw.get("linkedin_url"),
        "description": raw.get("short_description"),
        "location": _join(raw.get("city"), raw.get("state"), raw.get("country")),
    }


def zoominfo_person(raw: dict[str, Any]) -> dict[str, Any]:
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    urls = raw.get("externalUrls") or []
    linkedin = next((u.get("url") for u in urls if isinstance(u, dict) and "linkedin" in str(u.get("type") or u.get("url") or "").lower()), None)
    level = raw.get("managementLevel")
    if isinstance(level, list):
        level = ", ".join(str(x) for x in level)
    return {
        "id": str(raw.get("id") or ""),
        "first_name": raw.get("firstName") or "",
        "last_name": raw.get("lastName") or "",
        "name": _join(raw.get("firstName"), raw.get("lastName")),
        "title": raw.get("jobTitle"),
        "seniority": level,
        "department": raw.get("jobFunction") if isinstance(raw.get("jobFunction"), str) else None,
        "email": str(raw["email"]).lower() if raw.get("email") else None,
        "has_email": bool(raw.get("email") or raw.get("hasEmail")),
        "phone": raw.get("phone"),
        "mobile": raw.get("mobilePhone"),
        "linkedin": linkedin,
        "location": _join(raw.get("city"), raw.get("state"), raw.get("country")),
        "company": raw.get("companyName") or company.get("name"),
        "domain": _domain(raw.get("companyWebsite") or company.get("website")),
        "company_id": str(raw.get("companyId") or company.get("id") or "") or None,
    }


def zoominfo_company(raw: dict[str, Any]) -> dict[str, Any]:
    industries = raw.get("industries") or []
    industry = industries[0].get("name") if industries and isinstance(industries[0], dict) else (industries[0] if industries else raw.get("industry"))
    socials = raw.get("socialMediaUrls") or []
    descriptions = raw.get("descriptionList") or []
    return {
        "id": str(raw.get("id") or ""),
        "name": raw.get("name") or "",
        "domain": _domain(raw.get("website")),
        "website": raw.get("website"),
        "industry": industry,
        "employees": raw.get("employeeCount"),
        "phone": raw.get("phone"),
        "linkedin": next((s.get("url") for s in socials if isinstance(s, dict) and "linkedin" in str(s.get("type") or s.get("url") or "").lower()), None),
        "description": descriptions[0].get("description") if descriptions and isinstance(descriptions[0], dict) else None,
        "location": _join(raw.get("city"), raw.get("state"), raw.get("country")),
    }


# ---------------------------------------------------------------------------
# Enrichment (used by the waterfall and by imports)
# ---------------------------------------------------------------------------


async def enrich_person(db: AsyncSession, connection: IntegrationConnection, key: str, *, person_id: str | None = None, email: str | None = None,
                        first_name: str | None = None, last_name: str | None = None, company: str | None = None, domain: str | None = None,
                        linkedin: str | None = None) -> dict[str, Any] | None:
    if key == "apollo":
        params: dict[str, Any] = {"reveal_personal_emails": "false"}
        for name, value in (("id", person_id), ("email", email), ("first_name", first_name), ("last_name", last_name),
                            ("organization_name", company), ("domain", domain), ("linkedin_url", linkedin)):
            if value:
                params[name] = value
        response = await _apollo(connection, "POST", "/people/match", params=params, ok=(404,))
        person = response.json().get("person") if response.status_code < 400 else None
        return apollo_person(person) if person else None
    match: dict[str, Any] = {}
    if person_id:
        match["personId"] = person_id
    if email:
        match["emailAddress"] = email
    if first_name:
        match["firstName"] = first_name
    if last_name:
        match["lastName"] = last_name
    if company:
        match["companyName"] = company
    if domain:
        match["companyWebsite"] = domain
    if linkedin:
        match["externalURL"] = linkedin
    data = await _zoominfo(connection, "/enrich/contact", {"matchPersonInput": [match], "outputFields": ZI_CONTACT_FIELDS})
    results = ((data.get("data") or {}).get("result") or []) if isinstance(data.get("data"), dict) else []
    found = (results[0].get("data") or []) if results else []
    return zoominfo_person(found[0]) if found else None


async def enrich_company(db: AsyncSession, connection: IntegrationConnection, key: str, *, company_id: str | None = None, domain: str | None = None,
                         name: str | None = None) -> dict[str, Any] | None:
    if key == "apollo":
        if not domain:
            return None
        response = await _apollo(connection, "GET", "/organizations/enrich", params={"domain": domain}, ok=(404,))
        org = response.json().get("organization") if response.status_code < 400 else None
        return apollo_company(org) if org else None
    match: dict[str, Any] = {}
    if company_id:
        match["companyId"] = company_id
    if domain:
        match["companyWebsite"] = domain
    if name and not domain:
        match["companyName"] = name
    if not match:
        return None
    data = await _zoominfo(connection, "/enrich/company", {"matchCompanyInput": [match], "outputFields": ZI_COMPANY_FIELDS})
    results = ((data.get("data") or {}).get("result") or []) if isinstance(data.get("data"), dict) else []
    found = (results[0].get("data") or []) if results else []
    return zoominfo_company(found[0]) if found else None


async def waterfall_values(db: AsyncSession, organization_id: uuid.UUID, key: str, record: Any) -> dict[str, Any]:
    """What Apollo / ZoomInfo know about a lead, contact or account, in waterfall field names.
    Raises IntegrationError when the integration is off or not connected."""

    connection = await core.active_connection(db, organization_id, key)
    if isinstance(record, Account):
        domain = _domain(record.domain or record.website)
        if not domain and key == "apollo":
            raise IntegrationError(f"{VENDOR[key]} needs the company's website.", 422)
        company = await enrich_company(db, connection, key, domain=domain, name=record.name)
        if not company:
            return {}
        await _remember(db, organization_id, key, "account", record.id, "organization", company["id"])
        return _clean({
            "industry": company.get("industry"), "employees": company.get("employees"), "phone": company.get("phone"), "linkedin": company.get("linkedin"),
            "description": company.get("description"), "website": company.get("website") or (f"https://{company['domain']}" if company.get("domain") else None),
            "location": company.get("location"),
        })
    email = getattr(record, "email", None)
    company_name = getattr(record, "company_name", None)
    domain = None
    if email and "@" in email:
        domain = email.split("@")[-1].lower()
    if not email and not (record.first_name and record.last_name and (company_name or domain)):
        raise IntegrationError(f"{VENDOR[key]} needs an email, or a name and company.", 422)
    person = await enrich_person(
        db, connection, key, email=email, first_name=record.first_name or None, last_name=record.last_name or None,
        company=company_name, domain=domain, linkedin=getattr(record, "linkedin_url", None),
    )
    if not person:
        return {}
    record_type = "lead" if isinstance(record, Lead) else "contact"
    if person.get("id"):
        await _remember(db, organization_id, key, record_type, record.id, "person", person["id"])
    return _clean({
        "email": person.get("email") if not email else None, "job_title": person.get("title"), "phone": person.get("phone"),
        "mobile": person.get("mobile"), "linkedin": person.get("linkedin"), "location": person.get("location"),
        "company": person.get("company"), "seniority": person.get("seniority"), "department": person.get("department"),
        "website": f"https://{person['domain']}" if person.get("domain") else None,
    })


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v not in (None, "", [], 0)}


async def _remember(db: AsyncSession, organization_id: uuid.UUID, key: str, record_type: str, record_id: uuid.UUID, external_type: str, external_id: str | None) -> None:
    """Link an AIVA record to the provider's person / company (so imports know it's already here)."""

    if not external_id:
        return
    existing = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id,
                IntegrationRecordLink.provider == key,
                IntegrationRecordLink.external_type == external_type,
                IntegrationRecordLink.external_parent == "",
                or_(IntegrationRecordLink.external_id == external_id, (IntegrationRecordLink.record_type == record_type) & (IntegrationRecordLink.record_id == record_id)),
            )
        )
    ).scalars().first()
    if existing is not None:
        existing.last_synced_at = core.utcnow()
        return
    db.add(
        IntegrationRecordLink(
            id=uuid.uuid4(), organization_id=organization_id, provider=key, record_type=record_type, record_id=record_id,
            external_type=external_type, external_id=external_id[:200], external_parent="", last_synced_at=core.utcnow(),
        )
    )


# ---------------------------------------------------------------------------
# Prospect search
# ---------------------------------------------------------------------------


def clean_filters(raw: dict[str, Any]) -> dict[str, Any]:
    def words(name: str, limit: int = 10, size: int = 100) -> list[str]:
        value = raw.get(name) or []
        if isinstance(value, str):
            value = [v for v in value.split(",")]
        if not isinstance(value, list):
            raise IntegrationError(f"“{name}” must be a list.", 422)
        out = []
        for item in value[:limit]:
            text = str(item or "").strip()[:size]
            if text and text.lower() not in {o.lower() for o in out}:
                out.append(text)
        return out

    seniorities = [s for s in words("seniorities", 11, 20) if s in SENIORITIES]
    ranges = [r for r in words("employee_ranges", 8, 20) if r in EMPLOYEE_RANGES]
    try:
        page = max(1, min(int(raw.get("page") or 1), 100))
    except (TypeError, ValueError):
        page = 1
    return {
        "keywords": str(raw.get("keywords") or "").strip()[:200],
        "titles": words("titles"),
        "seniorities": seniorities,
        "locations": words("locations"),
        "company_locations": words("company_locations"),
        "industries": words("industries"),
        "employee_ranges": ranges,
        "company_name": str(raw.get("company_name") or "").strip()[:200],
        "domains": [d for d in (_domain(x) for x in words("domains", 20, 200)) if d],
        "page": page,
    }


def _employee_bounds(ranges: list[str]) -> tuple[int | None, int | None]:
    if not ranges:
        return None, None
    lows = [int(r.split(",")[0]) for r in ranges]
    highs = [int(r.split(",")[1]) for r in ranges]
    return min(lows), max(highs)


async def search(db: AsyncSession, connection: IntegrationConnection, key: str, kind: str, filters: dict[str, Any]) -> dict[str, Any]:
    page = filters["page"]
    if key == "apollo":
        if kind == "people":
            params: list[tuple[str, Any]] = [("page", page), ("per_page", MAX_PER_PAGE)]
            params += [("person_titles[]", t) for t in filters["titles"]]
            params += [("person_seniorities[]", s) for s in filters["seniorities"]]
            params += [("person_locations[]", x) for x in filters["locations"]]
            params += [("organization_locations[]", x) for x in filters["company_locations"]]
            params += [("organization_num_employees_ranges[]", r) for r in filters["employee_ranges"]]
            params += [("q_organization_domains_list[]", d) for d in filters["domains"]]
            if filters["keywords"]:
                params.append(("q_keywords", filters["keywords"]))
            data = (await _apollo(connection, "POST", "/mixed_people/api_search", params=params)).json()
            items = [apollo_person(p) for p in data.get("people") or []]
        else:
            params = [("page", page), ("per_page", MAX_PER_PAGE)]
            params += [("organization_locations[]", x) for x in filters["company_locations"] or filters["locations"]]
            params += [("organization_num_employees_ranges[]", r) for r in filters["employee_ranges"]]
            params += [("q_organization_keyword_tags[]", t) for t in filters["industries"]]
            if filters["company_name"] or filters["keywords"]:
                params.append(("q_organization_name", filters["company_name"] or filters["keywords"]))
            data = (await _apollo(connection, "POST", "/mixed_companies/search", params=params)).json()
            items = [apollo_company(c) for c in (data.get("organizations") or []) + (data.get("accounts") or [])]
        pagination = data.get("pagination") or {}
        total = pagination.get("total_entries") or data.get("total_entries") or len(items)
    else:
        low, high = _employee_bounds(filters["employee_ranges"])
        body: dict[str, Any] = {"rpp": MAX_PER_PAGE, "page": page}
        if low is not None:
            body["employeeRangeMin"] = str(low)
            body["employeeRangeMax"] = str(high)
        if filters["industries"]:
            body["industryKeywords"] = ",".join(filters["industries"])
        if kind == "people":
            if filters["titles"]:
                body["jobTitle"] = ",".join(filters["titles"])
            levels = sorted({ZI_LEVEL[s] for s in filters["seniorities"]})
            if levels:
                body["managementLevel"] = ",".join(levels)
            if filters["locations"]:
                body["country"] = ",".join(filters["locations"])
            if filters["domains"]:
                body["companyWebsite"] = ",".join(filters["domains"])
            if filters["keywords"]:
                body["fullName" if " " in filters["keywords"] else "companyName"] = filters["keywords"]
            data = await _zoominfo(connection, "/search/contact", body)
            items = [zoominfo_person(p) for p in data.get("data") or []]
        else:
            if filters["company_name"] or filters["keywords"]:
                body["companyName"] = filters["company_name"] or filters["keywords"]
            if filters["company_locations"] or filters["locations"]:
                body["country"] = ",".join(filters["company_locations"] or filters["locations"])
            data = await _zoominfo(connection, "/search/company", body)
            items = [zoominfo_company(c) for c in data.get("data") or []]
        total = data.get("totalResults") or len(items)
    items = [i for i in items if i.get("id")]
    await _mark_known(db, connection.organization_id, key, kind, items)
    return {"items": items, "page": page, "per_page": MAX_PER_PAGE, "total": int(total or 0)}


async def _mark_known(db: AsyncSession, organization_id: uuid.UUID, key: str, kind: str, items: list[dict[str, Any]]) -> None:
    """Flag results that are already in AIVA (imported before or same email/domain) or on the do-not-contact list."""

    from app.services.prospecting import suppressed_emails

    ids = [i["id"] for i in items]
    linked = {
        row.external_id: (row.record_type, str(row.record_id))
        for row in (
            await db.execute(
                select(IntegrationRecordLink).where(
                    IntegrationRecordLink.organization_id == organization_id,
                    IntegrationRecordLink.provider == key,
                    IntegrationRecordLink.external_type == ("person" if kind == "people" else "organization"),
                    IntegrationRecordLink.external_id.in_(ids or [""]),
                )
            )
        ).scalars()
    }
    if kind == "people":
        emails = [i["email"] for i in items if i.get("email")]
        blocked = await suppressed_emails(db, organization_id, emails)
        known_emails = set()
        if emails:
            lowered = [e.lower() for e in emails]
            known_emails |= {e.lower() for e in (await db.execute(select(Lead.email).where(Lead.organization_id == organization_id, func.lower(Lead.email).in_(lowered)))).scalars() if e}
            known_emails |= {e.lower() for e in (await db.execute(select(Contact.email).where(Contact.organization_id == organization_id, func.lower(Contact.email).in_(lowered)))).scalars() if e}
        for item in items:
            item["in_aiva"] = item["id"] in linked or bool(item.get("email") and item["email"] in known_emails)
            item["record"] = {"type": linked[item["id"]][0], "id": linked[item["id"]][1]} if item["id"] in linked else None
            item["suppressed"] = bool(item.get("email") and item["email"] in blocked)
    else:
        domains = [i["domain"] for i in items if i.get("domain")]
        known = set()
        if domains:
            known = {d.lower() for d in (await db.execute(select(Account.domain).where(Account.organization_id == organization_id, func.lower(Account.domain).in_(domains)))).scalars() if d}
        for item in items:
            item["in_aiva"] = item["id"] in linked or bool(item.get("domain") and item["domain"] in known)
            item["record"] = {"type": linked[item["id"]][0], "id": linked[item["id"]][1]} if item["id"] in linked else None
            item["suppressed"] = False


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


async def _account_for(db: AsyncSession, organization_id: uuid.UUID, owner: uuid.UUID | None, person: dict[str, Any]) -> Account | None:
    domain = person.get("domain")
    name = person.get("company")
    if not domain and not name:
        return None
    query = select(Account).where(Account.organization_id == organization_id, Account.is_archived.is_(False))
    query = query.where(func.lower(Account.domain) == domain) if domain else query.where(func.lower(Account.name) == str(name).lower())
    account = (await db.execute(query.limit(1))).scalar_one_or_none()
    if account is not None:
        return account
    account = Account(
        id=uuid.uuid4(), organization_id=organization_id, name=str(name or domain)[:250], domain=domain,
        website=f"https://{domain}" if domain else None, owner_user_id=owner, custom_fields={},
    )
    db.add(account)
    await db.flush()
    return account


async def import_items(
    db: AsyncSession,
    principal: Any,
    key: str,
    kind: str,
    items: list[dict[str, Any]],
    *,
    as_type: str,
    owner_user_id: uuid.UUID | None,
    lead_source: str | None,
) -> dict[str, Any]:
    """Create leads / contacts / accounts from search results. The caller commits."""

    from fastapi import HTTPException

    from app.services import metering, prospecting

    org = principal.organization_id
    if len(items) > MAX_IMPORT:
        raise IntegrationError(f"Import up to {MAX_IMPORT} at a time.", 422)
    if (kind == "people") != (as_type in {"lead", "contact"}):
        raise IntegrationError("People import as leads or contacts; companies import as accounts.", 422)
    connection = await core.active_connection(db, org, key)
    balance = await metering.credit_balance(db, org, principal.user_id)
    if balance["remaining"] < 1:
        raise IntegrationError("Out of credits for this month, so nothing was imported.", 402)
    remaining = balance["remaining"]
    owner = owner_user_id or principal.user_id
    source = (lead_source or key)[:80]
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    credits = 0
    for raw in items:
        external_id = str(raw.get("id") or "")[:200]
        label = str(raw.get("name") or raw.get("company") or external_id)[:200]
        if not external_id:
            skipped.append({"name": label, "reason": "Missing id."})
            continue
        if remaining - credits < 1:
            skipped.append({"name": label, "reason": "Out of credits."})
            continue
        already = (
            await db.execute(
                select(IntegrationRecordLink.id).where(
                    IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key,
                    IntegrationRecordLink.external_type == ("person" if kind == "people" else "organization"),
                    IntegrationRecordLink.external_parent == "", IntegrationRecordLink.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if already is not None:
            skipped.append({"name": label, "reason": "Already in AIVA."})
            continue
        try:
            if kind == "people":
                person = await enrich_person(db, connection, key, person_id=external_id) or {}
                person = {**{k: v for k, v in raw.items() if k in {"first_name", "last_name", "title", "company", "domain", "linkedin", "location"} and v}, **{k: v for k, v in person.items() if v}}
            else:
                person = await enrich_company(db, connection, key, company_id=external_id, domain=_domain(raw.get("domain")), name=raw.get("name")) or {}
                person = {**{k: v for k, v in raw.items() if k in {"name", "domain", "website", "industry", "employees", "location"} and v}, **{k: v for k, v in person.items() if v}}
        except IntegrationError as exc:
            if exc.status_code in (400, 429) or "refused" in str(exc):
                raise
            skipped.append({"name": label, "reason": str(exc)[:200]})
            continue

        if kind == "people":
            email = (person.get("email") or "").lower() or None
            first = str(person.get("first_name") or "")[:120]
            last = str(person.get("last_name") or "")[:120]
            if "*" in last:
                last = ""
            if not last and not email:
                skipped.append({"name": label, "reason": f"{VENDOR[key]} didn't return a full name or email."})
                continue
            if email and await prospecting.suppression_for(db, org, email) is not None:
                skipped.append({"name": label, "reason": "On the do-not-contact list."})
                continue
            try:
                duplicates = await prospecting.check_new_person(
                    db, org, as_type, email=email, first_name=first, last_name=last or None, company=person.get("company"),
                )
            except HTTPException as exc:
                skipped.append({"name": label, "reason": str(exc.detail)[:200]})
                continue
            if duplicates:
                skipped.append({"name": label, "reason": "Already in AIVA."})
                continue
            attribution = {"integration": key, "external_id": external_id, "imported_at": core.utcnow().isoformat()}
            extras = {k: v for k, v in {"linkedin": person.get("linkedin"), "seniority": person.get("seniority"), "location": person.get("location")}.items() if v}
            if as_type == "lead":
                record: Any = Lead(
                    id=uuid.uuid4(), organization_id=org, first_name=first, last_name=last or (email.split("@")[0] if email else "Unknown"),
                    email=email, phone=person.get("phone") or person.get("mobile"), company_name=(person.get("company") or None),
                    job_title=(str(person.get("title"))[:150] if person.get("title") else None), source=source, status="new", score=0,
                    owner_user_id=owner, custom_fields={**extras, "_attribution": attribution},
                )
            else:
                account = await _account_for(db, org, owner, person)
                record = Contact(
                    id=uuid.uuid4(), organization_id=org, account_id=account.id if account else None, owner_user_id=owner,
                    first_name=first, last_name=last or (email.split("@")[0] if email else "Unknown"), email=email,
                    phone=person.get("phone"), mobile=person.get("mobile"), job_title=(str(person.get("title"))[:150] if person.get("title") else None),
                    linkedin_url=(str(person.get("linkedin"))[:500] if person.get("linkedin") else None),
                    custom_fields={k: v for k, v in {**extras, "_attribution": attribution}.items() if k != "linkedin"},
                )
            db.add(record)
            await db.flush()
            record_type = as_type
            name = f"{record.first_name} {record.last_name}".strip()
        else:
            domain = _domain(person.get("domain") or person.get("website"))
            existing = None
            if domain:
                existing = (await db.execute(select(Account.id).where(Account.organization_id == org, func.lower(Account.domain) == domain).limit(1))).scalar_one_or_none()
            if existing is not None:
                skipped.append({"name": label, "reason": "Already in AIVA."})
                continue
            employees = person.get("employees")
            record = Account(
                id=uuid.uuid4(), organization_id=org, name=str(person.get("name") or domain or label)[:250], domain=domain,
                website=(person.get("website") or (f"https://{domain}" if domain else None)), industry=(str(person.get("industry"))[:150] if person.get("industry") else None),
                phone=(str(person.get("phone"))[:50] if person.get("phone") else None),
                employee_count=int(employees) if isinstance(employees, (int, float)) and employees > 0 else None,
                description=(str(person.get("description"))[:5000] if person.get("description") else None), owner_user_id=owner,
                custom_fields={k: v for k, v in {"linkedin": person.get("linkedin"), "location": person.get("location"),
                                                 "_attribution": {"integration": key, "external_id": external_id, "imported_at": core.utcnow().isoformat()}}.items() if v},
            )
            db.add(record)
            await db.flush()
            record_type = "account"
            name = record.name
        await _remember(db, org, key, record_type, record.id, "person" if kind == "people" else "organization", external_id)
        await metering.charge_credits(
            db, org, principal.user_id, 1, "enrichment", record_type=record_type, record_id=record.id,
            description=f"{VENDOR[key]} import: {name}"[:300], check=False,
        )
        credits += 1
        created.append({"id": str(record.id), "type": record_type, "name": name, "href": f"/{record_type}s/{record.id}" if record_type != "opportunity" else None})
    if created:
        core.log(
            db, org, key, "imported", f"Imported {len(created)} {('lead' if as_type == 'lead' else as_type)}{'s' if len(created) != 1 else ''} from {VENDOR[key]}"
            + (f" ({len(skipped)} skipped)." if skipped else "."), level="success", connection=connection, user_id=principal.user_id,
            detail={"created": len(created), "skipped": len(skipped), "as": as_type},
        )
    return {"created": created, "skipped": skipped, "credits": credits}


# ---------------------------------------------------------------------------
# Hub hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    if key == "apollo":
        api_key = (values.get("api_key") or "").strip()
        if len(api_key) < 10:
            raise IntegrationError("That doesn't look like an Apollo API key.", 422)
        health = (await _apollo(None, "GET", "/auth/health", api_key=api_key)).json()
        if health.get("is_logged_in") is False:
            raise IntegrationError("Apollo didn't accept that API key.", 400)
        return {"label": "Apollo.io", "config": {}}
    username = (values.get("username") or "").strip()
    client_id = (values.get("client_id") or "").strip()
    private_key = (values.get("private_key") or "").strip()
    if not username or not client_id or "PRIVATE KEY" not in private_key:
        raise IntegrationError("Paste the ZoomInfo username, client ID and the whole private key (PEM).", 422)
    await _zoominfo_login(username, client_id, private_key)  # proves the keys work
    return {"label": f"ZoomInfo ({username})", "config": {"username": username, "client_id": client_id, "jwt_expires_at": None}}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if key == "zoominfo":
        core.set_secret(connection, "access_jwt", None)  # get a fresh token with the new keys on first use
    core.log(db, connection.organization_id, key, "ready",
             f"{VENDOR[key]} is connected. Add it to the waterfall enrichment order, or search for prospects on this page.", connection=connection)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    if key == "apollo":
        health = (await _apollo(connection, "GET", "/auth/health")).json()
        if health.get("is_logged_in") is False:
            raise IntegrationError("Apollo didn't accept the API key.", 400)
        return "Apollo accepted the API key."
    await _zoominfo_token(connection, force=True)
    return "ZoomInfo accepted the keys and issued an access token."


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    from app.integrations.providers import lead_capture

    if personal:
        raise IntegrationError(f"{VENDOR[key]} is set up for the whole workspace.", 422)
    cleaned: dict[str, Any] = {}
    for name, value in config.items():
        if name == "lead_source":
            cleaned[name] = str(value or "").strip()[:80] or None
        elif name == "owner_user_id":
            owner = await lead_capture.valid_owner(db, principal.organization_id, value)
            cleaned[name] = str(owner) if owner else None
        elif name == "import_as":
            if value not in {"lead", "contact"}:
                raise IntegrationError("People import as leads or contacts.", 422)
            cleaned[name] = value
        else:
            raise IntegrationError(f"Unknown setting “{name}”.", 422)
    return cleaned


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    from app.services import metering, workspace_settings

    org = principal.organization_id
    config = await core.workspace_config(db, org, key)
    enrichment = await workspace_settings.get(db, org, "enrichment")
    order = [p.key for p in enrichment.providers]
    position = order.index(key) + 1 if key in order else None
    in_waterfall = next((p.enabled for p in enrichment.providers if p.key == key), False)
    imported = (
        await db.execute(
            select(func.count()).select_from(IntegrationRecordLink).where(IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key)
        )
    ).scalar_one()
    balance = await metering.credit_balance(db, org, principal.user_id)
    return {
        "defaults": {k: config.get(k, v) for k, v in DEFAULTS.items()},
        "waterfall": {"enabled": bool(in_waterfall), "position": position, "providers": len(order)},
        "linked_records": int(imported or 0),
        "credits_remaining": balance["remaining"],
        "max_import": MAX_IMPORT,
        "seniorities": list(SENIORITIES),
        "employee_ranges": list(EMPLOYEE_RANGES),
        "notes": {
            "apollo": "People search is free in Apollo; importing enriches each person (Apollo credits) to get their email.",
            "zoominfo": "Search is free; importing enriches each person with ZoomInfo (ZoomInfo credits). Locations filter by country.",
        }[key],
    }
