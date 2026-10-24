"""Demo content for the integrations hub.

Turns on the integrations a typical team starts with, so the Integrations page
shows a realistic mix of on and off: Gmail, Google Calendar and Meet, and Zoom
(each person connects their own account), Slack (waiting for an admin to add
the AIVA app), WordPress Forms and Zapier. Everything goes through the REST API
in-process, so switches, webhook URLs and the activity log behave exactly as
if an admin had clicked them.

WordPress Forms then receives a plugin test and three real submissions on its
webhook URL, which become website leads through the normal lead-capture path.

Nothing here talks to an outside service: no accounts are connected, and no
outgoing webhook subscriptions are created (they would post demo events to a
URL nobody reads).

Runs once per workspace (``demo_integrations_seeded`` in organization settings).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.db.session import AsyncSessionLocal
from app.models.organization import Organization
from app.scripts.seed_workspace import API, Client, now

MARKER = "demo_integrations_seeded"

SWITCHED_ON = ("gmail", "google_calendar", "google_meet", "zoom", "slack", "wordpress_forms", "zapier")

SUBMISSIONS = (
    {
        "form_name": "Request a demo",
        "plugin": "contact-form-7",
        "site_url": "https://www.aiva-demo.example",
        "page_url": "https://www.aiva-demo.example/demo",
        "utm_source": "google",
        "utm_medium": "cpc",
        "utm_campaign": "crm-switch",
        "your-name": "Elena Petrova",
        "your-email": "elena.petrova@brightline-freight.example",
        "phone": "+1 512 555 0148",
        "company": "Brightline Freight",
        "your-message": "We run 40 sales reps on spreadsheets. Looking for a demo of pipelines and sequences next week.",
    },
    {
        "form_name": "Contact us",
        "plugin": "wpforms",
        "site_url": "https://www.aiva-demo.example",
        "page_url": "https://www.aiva-demo.example/contact",
        "first_name": "Marcus",
        "last_name": "Webb",
        "email": "marcus.webb@harborview-clinics.example",
        "company": "Harborview Clinics",
        "job_title": "Operations Director",
        "message": "Do you support HIPAA mode and a patient-facing portal?",
    },
    {
        "form_name": "Pricing question",
        "plugin": "gravity-forms",
        "site_url": "https://www.aiva-demo.example",
        "page_url": "https://www.aiva-demo.example/pricing",
        "utm_source": "linkedin",
        "name": "Aisha Okafor",
        "email": "aisha@kestrel-analytics.example",
        "company": "Kestrel Analytics",
        "message": "What does the Pro plan cost for 12 seats billed yearly?",
    },
)


async def seed_integrations(users: dict[str, uuid.UUID], organization_id: uuid.UUID) -> bool:
    """Adds demo content for the integrations hub once. Returns True if it ran."""

    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, organization_id)
        if organization is None or (organization.settings or {}).get(MARKER):
            return False
        organization.settings = {**(organization.settings or {}), MARKER: now().isoformat()}
        await db.commit()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://seed.internal", timeout=60) as http:
        api = Client(http, organization_id, users)
        for name, section in (("switches", _switches), ("WordPress submissions", _wordpress)):
            try:
                await section(api, http)
            except Exception as exc:  # noqa: BLE001 - demo content must never stop startup
                print(f"Integrations demo: {name} skipped ({exc})")
    return True


async def _switches(api: Client, http: httpx.AsyncClient) -> None:
    catalog = {item["key"]: item for item in (await api.get("/integration-hub/catalog"))["items"]}
    for key in SWITCHED_ON:
        item = catalog.get(key)
        if item and item["available"] and not item["switched_on"]:
            await api.put(f"/integration-hub/{key}/toggle", {"enabled": True})


async def _wordpress(api: Client, http: httpx.AsyncClient) -> None:
    detail: dict[str, Any] = await api.get("/integration-hub/wordpress_forms")
    url = (detail.get("connection") or {}).get("webhook_url")
    if not url:
        return
    path = f"{API}/public/integrations/wordpress_forms/webhook/{url.rsplit('/', 1)[1]}"
    test = await http.post(path, json={"aiva_test": "1", "site_url": "https://www.aiva-demo.example", "plugin": "aiva-wordpress"})
    test.raise_for_status()
    for submission in SUBMISSIONS:
        response = await http.post(path, json=submission)
        response.raise_for_status()
