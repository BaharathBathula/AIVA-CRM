"""Built-in data enrichment.

Fills gaps from what a record already says, without calling an outside
data provider: the company and website behind a business email address,
seniority and department from a job title, a consistent phone format and
country, and an industry from an account's name. Only empty fields are
filled; everything derived is also kept under ``custom_fields._enrichment``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net", "mail.com",
    "yandex.com", "zoho.com", "fastmail.com", "hey.com", "comcast.net", "att.net", "verizon.net", "sbcglobal.net",
}

SENIORITY = (
    ("Owner / C-level", ("chief", "ceo", "cfo", "cto", "coo", "cio", "cmo", "cro", "founder", "owner", "president", "managing partner", "principal")),
    ("Vice president", ("vp", "vice president", "svp", "evp")),
    ("Director", ("director", "head of")),
    ("Manager", ("manager", "lead", "supervisor", "superintendent")),
)

DEPARTMENTS = (
    ("Executive", ("ceo", "founder", "owner", "president", "managing partner", "principal", "general manager")),
    ("Sales", ("sales", "account executive", "business development", "bdr", "sdr", "broker", "agent")),
    ("Marketing", ("marketing", "brand", "growth", "communications", "content", "cmo")),
    ("Finance", ("finance", "cfo", "accountant", "accounting", "controller", "treasurer", "bookkeep")),
    ("Operations", ("operations", "coo", "logistics", "fleet", "supply", "facilities", "office manager")),
    ("Engineering & IT", ("engineer", "developer", "cto", "cio", "it ", "technology", "technical", "software", "security")),
    ("People & HR", ("hr", "human resources", "people", "talent", "recruit")),
    ("Customer service", ("support", "success", "service", "customer")),
    ("Legal & compliance", ("legal", "counsel", "compliance", "risk")),
    ("Education", ("teacher", "director of education", "principal", "dean", "school")),
    ("Healthcare", ("dentist", "doctor", "physician", "nurse", "dds", "md")),
)

INDUSTRIES = (
    ("Insurance", ("insurance", "assurance", "underwrit", "brokerage")),
    ("Healthcare", ("dental", "health", "clinic", "medical", "pharma", "hospital", "care", "therapy")),
    ("Construction", ("roofing", "hvac", "plumbing", "construction", "builders", "contractor", "electric", "remodel")),
    ("Transportation & logistics", ("logistics", "freight", "trucking", "transport", "shipping", "fleet", "delivery")),
    ("Real estate", ("realty", "real estate", "properties", "homes", "realtor", "property")),
    ("Food & beverage", ("bakery", "cafe", "coffee", "restaurant", "kitchen", "brew", "catering", "foods")),
    ("Education", ("academy", "school", "montessori", "university", "college", "learning", "education")),
    ("Health & fitness", ("fitness", "gym", "yoga", "cycling", "studio", "wellness")),
    ("Technology", ("software", "tech", "labs", "digital", "data", "cloud", "systems", "ai")),
    ("Financial services", ("bank", "capital", "financial", "credit union", "wealth", "invest")),
    ("Legal", ("law", "legal", "attorneys", "llp")),
    ("Manufacturing", ("manufactur", "industries", "fabrication", "machining")),
    ("Retail", ("store", "shop", "boutique", "retail", "outlet")),
)

COUNTRY_CODES = {
    "1": "United States / Canada", "44": "United Kingdom", "49": "Germany", "33": "France", "91": "India",
    "61": "Australia", "34": "Spain", "39": "Italy", "31": "Netherlands", "52": "Mexico", "55": "Brazil",
    "81": "Japan", "86": "China", "65": "Singapore", "971": "United Arab Emirates", "353": "Ireland",
}


def _match(text: str, table) -> str | None:
    lowered = f" {text.lower()} "
    for label, words in table:
        if any(word in lowered for word in words):
            return label
    return None


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


def company_from_domain(domain: str) -> str:
    label = domain.split(".")[0]
    words = re.split(r"[-_]", label)
    return " ".join(word.capitalize() for word in words if word)


def normalize_phone(phone: str | None) -> tuple[str | None, str | None]:
    """Return (formatted phone, country) for a phone number."""

    if not phone:
        return None, None
    digits = re.sub(r"\D", "", phone)
    extension = ""
    ext = re.search(r"(?:x|ext\.?)\s*(\d+)$", phone, re.I)
    if ext:
        extension = f" ext. {ext.group(1)}"
        digits = digits[: -len(ext.group(1))]
    if len(digits) == 10:
        return f"+1 {digits[0:3]}-{digits[3:6]}-{digits[6:]}{extension}", COUNTRY_CODES["1"]
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 {digits[1:4]}-{digits[4:7]}-{digits[7:]}{extension}", COUNTRY_CODES["1"]
    if phone.strip().startswith("+"):
        for size in (3, 2, 1):
            code = digits[:size]
            if code in COUNTRY_CODES:
                return f"+{code} {digits[size:]}{extension}", COUNTRY_CODES[code]
    return phone, None


def seniority(title: str | None) -> str | None:
    if not title:
        return None
    return _match(title, SENIORITY) or "Individual contributor"


def department(title: str | None) -> str | None:
    return _match(title, DEPARTMENTS) if title else None


def industry(name: str | None) -> str | None:
    return _match(name, INDUSTRIES) if name else None


def enrich(record: Any) -> dict[str, Any]:
    """Fill empty fields on a lead, contact or account. Returns what changed and what was learned."""

    applied: dict[str, Any] = {}
    insights: dict[str, Any] = {}

    def fill(field: str, value: Any) -> None:
        if value and not getattr(record, field, None):
            setattr(record, field, value)
            applied[field] = value

    if isinstance(record, (Lead, Contact)):
        domain = email_domain(record.email)
        if domain:
            personal = domain in FREE_EMAIL_DOMAINS
            insights["email_type"] = "Personal" if personal else "Business"
            if not personal:
                insights["company_domain"] = domain
                insights["website"] = f"https://{domain}"
                if isinstance(record, Lead):
                    fill("company_name", company_from_domain(domain))
        phone, country = normalize_phone(record.phone)
        if phone and phone != record.phone:
            record.phone = phone
            applied["phone"] = phone
        if country:
            insights["country"] = country
        if record.job_title:
            insights["seniority"] = seniority(record.job_title)
            dept = department(record.job_title)
            if dept:
                insights["department"] = dept
                if isinstance(record, Contact):
                    fill("department", dept)
        name = f"{record.first_name} {record.last_name}".strip()
        company = getattr(record, "company_name", None) or ""
        insights["linkedin_search"] = f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(f'{name} {company}'.strip())}"
    elif isinstance(record, Account):
        if record.website and not record.domain:
            domain = re.sub(r"^https?://(www\.)?", "", record.website.strip().lower()).split("/")[0]
            fill("domain", domain)
        if record.domain and not record.website:
            fill("website", f"https://{record.domain.lower()}")
        guess = industry(record.name)
        if guess:
            insights["industry_guess"] = guess
            fill("industry", guess)
        phone, country = normalize_phone(record.phone)
        if phone and phone != record.phone:
            record.phone = phone
            applied["phone"] = phone
        if country:
            insights["country"] = country
            fill("billing_country", "United States" if country.startswith("United States") else country)
        insights["linkedin_search"] = f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(record.name)}"

    insights["enriched_at"] = datetime.now(timezone.utc).isoformat()
    record.custom_fields = {**(record.custom_fields or {}), "_enrichment": insights}
    return {"applied": applied, "insights": insights}
