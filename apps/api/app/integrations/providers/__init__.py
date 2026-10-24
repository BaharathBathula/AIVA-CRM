"""Provider modules and the hooks the hub calls on them.

Each module may define any of these (all async, all optional):

``validate_credentials(db, key, organization_id, values) -> dict``
    For integrations connected with pasted keys: check them with the provider and
    return ``{"label", "external_id", "config"}`` for the connection. Raise
    ``IntegrationError`` with a helpful message when they don't work.
``on_connected(db, key, connection) -> None``
    Right after a connection is created or refreshed (subscribe to webhooks,
    create the mailbox record, list pages...).
``on_disconnect(db, key, connection) -> None``
    Before a connection is removed (unsubscribe webhooks, revoke tokens).
``on_toggle(db, key, organization_id, enabled) -> None``
    After an admin switches the integration on or off.
``test(db, key, connection, principal) -> str``
    A harmless live check; returns what happened, in a sentence.
``detail(db, key, principal) -> dict``
    Extra data for the integration's page (channels, templates, webhook URLs...).
``handle_webhook(db, key, connection, request) -> Response``
    An inbound webhook. ``connection`` is None for app-wide URLs
    (``/webhook/app``). Only called while the integration is on.
``run_due(db) -> int``
    Scheduler work (sync, polling, retries). Return how many items were handled.
"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType
from typing import Any

logger = logging.getLogger("aiva.integrations")

MODULE_FOR: dict[str, str] = {
    "gmail": "mail",
    "outlook_mail": "mail",
    "google_calendar": "calendars",
    "outlook_calendar": "calendars",
    "google_meet": "calendars",
    "microsoft_teams": "meetings",
    "zoom": "meetings",
    "slack": "slack",
    "whatsapp": "whatsapp",
    "twilio_sms": "twilio_sms",
    "calendly": "calendly",
    "google_drive": "cloud_files",
    "onedrive": "cloud_files",
    "dropbox": "cloud_files",
    "docusign": "docusign",
    "wordpress_forms": "wordpress",
    "facebook_lead_ads": "facebook",
    "zapier": "automation",
    "make": "automation",
    "n8n": "automation",
}

ALL_MODULES = tuple(sorted(set(MODULE_FOR.values())))


def module(key: str) -> ModuleType | None:
    name = MODULE_FOR.get(key)
    return importlib.import_module(f"app.integrations.providers.{name}") if name else None


def hook(key: str, name: str):
    found = module(key)
    return getattr(found, name, None) if found else None


async def call(key: str, name: str, *args: Any, default: Any = None) -> Any:
    function = hook(key, name)
    if function is None:
        return default
    return await function(*args)
