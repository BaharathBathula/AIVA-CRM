"""Shared shapes for the email marketing integrations.

Each tool (Mailchimp, ActiveCampaign, Brevo, Constant Contact, Klaviyo) has an
``Adapter`` that speaks its API; ``_em_sync`` runs the same sync for all of them.
People are identified by their lower-cased email address everywhere: one
audience member per address, however many AIVA records share it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.core import IntegrationError
from app.models.integration_hub import IntegrationConnection

KEYS = ("mailchimp", "activecampaign", "brevo", "constant_contact", "klaviyo")
FEATURE = "mass_email"
LINK_TYPE = "member"  # IntegrationRecordLink.external_type

# AIVA fields people can map to the tool's fields.
SOURCES: tuple[tuple[str, str], ...] = (
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("full_name", "Full name"),
    ("phone", "Phone"),
    ("mobile", "Mobile"),
    ("job_title", "Job title"),
    ("company", "Company"),
    ("industry", "Industry"),
    ("city", "City"),
    ("country", "Country"),
    ("owner_name", "Owner name"),
    ("owner_email", "Owner email"),
    ("record_type", "Record type (Contact or Lead)"),
    ("lead_status", "Lead status"),
    ("lead_source", "Lead source"),
    ("contact_stage", "Contact stage"),
    ("score", "Score"),
    ("aiva_url", "Link to the AIVA record"),
)
SOURCE_KEYS = {key for key, _ in SOURCES}

# CRM events Klaviyo can receive (metric name in Klaviyo).
CRM_EVENTS: dict[str, str] = {
    "lead_created": "AIVA Lead Created",
    "contact_created": "AIVA Contact Created",
    "deal_won": "AIVA Deal Won",
    "deal_lost": "AIVA Deal Lost",
    "quote_accepted": "AIVA Quote Accepted",
}

# Why AIVA stopped emailing someone, by what the tool reported.
SUPPRESSION_REASON = {"unsubscribed": "unsubscribed", "cleaned": "bounced", "bounced": "bounced", "complaint": "complaint"}

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


def normal_email(value: Any) -> str | None:
    email = str(value or "").strip().lower()
    return email if EMAIL.match(email) and len(email) <= 254 else None


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Person:
    record_type: str  # contact | lead
    record_id: Any
    email: str
    values: dict[str, Any]
    aiva_tags: list[str] = field(default_factory=list)
    opted_out: bool = False


@dataclass
class UpsertResult:
    ok: bool
    status: str = "linked"  # linked | pending | unsubscribed | cleaned | error
    ref: str | None = None  # the tool's id for the member, when it says
    error: str | None = None


@dataclass
class Change:
    """Someone the tool says must not get marketing email any more."""

    email: str
    kind: str  # unsubscribed | cleaned | bounced | complaint
    at: datetime | None = None
    note: str = ""


@dataclass
class Campaign:
    id: str
    title: str
    status: str | None = None
    sent_at: datetime | None = None
    url: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class Engagement:
    email: str
    kind: str  # open | click
    campaign_id: str
    campaign_title: str = ""
    at: datetime | None = None
    url: str | None = None


@dataclass
class WebhookEvent:
    kind: str  # unsubscribed | cleaned | bounced | complaint | open | click | other
    email: str | None
    list_id: str | None = None
    campaign_id: str | None = None
    campaign_title: str | None = None
    url: str | None = None


def settings_from(config: dict[str, Any] | None) -> dict[str, Any]:
    """The workspace's sync settings with defaults filled in."""

    config = config or {}
    return {
        "list_id": config.get("list_id") or None,
        "list_name": config.get("list_name") or None,
        "record_types": [t for t in (config.get("record_types") or ["contact", "lead"]) if t in ("contact", "lead")],
        "lead_statuses": list(config.get("lead_statuses") or []),
        "contact_tags": list(config.get("contact_tags") or []),
        "skip_converted": config.get("skip_converted", True) is not False,
        "status_for_new": config.get("status_for_new") or "subscribed",
        "subscribe_mode": config.get("subscribe_mode") or "add",
        "tags": list(config.get("tags") or []),
        "field_map": dict(config.get("field_map") or {}),
        "events": [e for e in (config.get("events") or []) if e in CRM_EVENTS],
        "sync_paused": bool(config.get("sync_paused")),
    }


class Adapter:
    """What every tool implements. Methods raise IntegrationError with a message people can act on."""

    key = ""
    name = ""
    batch_size = 200  # people per upsert call
    unsubscribe_batch = 50
    capabilities: dict[str, bool] = {}
    # Fields AIVA always fills (label shown in the mapping, AIVA source).
    builtin_fields: tuple[tuple[str, str], ...] = ()
    # Whether "list" means an audience/list people are added to (Klaviyo lists are optional).
    list_required = True
    list_label = "List"

    async def validate(self, values: dict[str, str]) -> dict[str, Any]:
        raise NotImplementedError

    async def test(self, db: AsyncSession, connection: IntegrationConnection) -> str:
        raise NotImplementedError

    async def lists(self, db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def fields(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def payload(self, person: Person, settings: dict[str, Any]) -> dict[str, Any]:
        """What AIVA would send for this person (hashed to skip unchanged people)."""
        raise NotImplementedError

    async def upsert(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], people: list[Person]) -> dict[str, UpsertResult]:
        raise NotImplementedError

    async def unsubscribe(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], emails: list[str]) -> int:
        raise NotImplementedError

    async def changes(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime | None, limit: int
    ) -> tuple[list[Change], datetime | None]:
        """Unsubscribes and bounces since ``since``; returns them and the new cursor."""
        return [], since

    async def member(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> dict[str, Any] | None:
        """The person as the tool sees them: status, lists, tags, recent activity. None when unknown."""
        raise NotImplementedError

    async def confirm(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> Change | None:
        """Re-read a webhook's claim from the API: the change if it's real, else None."""
        found = await self.member(db, connection, settings, email)
        if not found:
            return None
        status = found.get("status")
        if status in ("unsubscribed", "cleaned", "bounced", "complaint"):
            return Change(email=email, kind=status, note=found.get("status_note") or "")
        return None

    async def campaigns(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime) -> list[Campaign]:
        return []

    async def engagement(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], campaign: Campaign, since: datetime | None, limit: int
    ) -> list[Engagement]:
        return []

    async def register_webhook(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], url: str) -> dict[str, Any] | None:
        return None

    async def remove_webhook(self, db: AsyncSession, connection: IntegrationConnection, info: dict[str, Any]) -> None:
        return None

    async def parse_webhook(self, body: bytes, content_type: str, form: list[tuple[str, str]] | None) -> list[WebhookEvent]:
        return []

    async def send_events(self, db: AsyncSession, connection: IntegrationConnection, events: list[dict[str, Any]]) -> int:
        return 0


def require(value: Any, message: str) -> Any:
    if not value:
        raise IntegrationError(message, 422)
    return value
