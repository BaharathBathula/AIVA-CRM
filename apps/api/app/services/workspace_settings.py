"""Typed settings namespaces stored in ``workspace_settings``.

Each namespace has a Pydantic model with defaults, so reading a namespace
that was never saved returns sensible values, and saving validates input.
Secret values (API keys, tokens) live in a separate, encrypted column and
are never returned; the API only reports which secrets are set.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.models.governance import WorkspaceSetting

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def clean_domain(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://", "", value).split("/")[0].split(":")[0].removeprefix("www.").removeprefix("@")
    if not DOMAIN_RE.match(value):
        raise ValueError(f"{value or 'That'} isn't a valid domain like example.com.")
    return value


def _ip_list(values: list[str]) -> list[str]:
    out = []
    for value in values:
        value = str(value).strip()
        if not value:
            continue
        try:
            network = str(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(f"{value} isn't an IP address or range like 203.0.113.0/24.") from exc
        if network not in out:
            out.append(network)
    return out


def _domains(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if str(value).strip():
            domain = clean_domain(str(value))
            if domain not in out:
                out.append(domain)
    return out


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------


class SecuritySettings(BaseModel):
    require_mfa: bool = False
    ip_allowlist: list[str] = Field(default_factory=list, max_length=100)
    allowed_email_domains: list[str] = Field(default_factory=list, max_length=50)
    password_min_length: int = Field(default=10, ge=10, le=64)
    allow_api_keys: bool = True

    @field_validator("ip_allowlist")
    @classmethod
    def _ips(cls, values: list[str]) -> list[str]:
        return _ip_list(values)

    @field_validator("allowed_email_domains")
    @classmethod
    def _domain_list(cls, values: list[str]) -> list[str]:
        return _domains(values)


class LicenseSettings(BaseModel):
    default_role: str = Field(default="member", max_length=60)
    auto_add_seats: bool = True
    credit_limit_default: int | None = Field(default=None, ge=0, le=10_000_000)
    credit_limits: dict[str, int] = Field(default_factory=dict)
    ai_word_limit_default: int | None = Field(default=None, ge=0, le=100_000_000)
    ai_word_limits: dict[str, int] = Field(default_factory=dict)

    @field_validator("credit_limits", "ai_word_limits")
    @classmethod
    def _limits(cls, values: dict[str, int]) -> dict[str, int]:
        out = {}
        for key, value in values.items():
            uuid.UUID(str(key))
            if value is None:
                continue
            if int(value) < 0:
                raise ValueError("Limits can't be negative.")
            out[str(key)] = int(value)
        return out


class SharingSettings(BaseModel):
    document_visibility: Literal["organization", "private"] = "organization"
    templates_shared: bool = True
    sequences_shared: bool = True
    snippets_shared: bool = True
    reports_shared: bool = False
    meeting_minutes: int = Field(default=30, ge=5, le=480)
    task_due_days: int = Field(default=2, ge=0, le=60)
    import_owner: Literal["importer", "territory"] = "importer"


class DialerOutcome(BaseModel):
    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=60)
    connected: bool = False
    follow_up: bool = False


DEFAULT_OUTCOMES = [
    DialerOutcome(key="connected", label="Connected", connected=True),
    DialerOutcome(key="left_voicemail", label="Left voicemail", follow_up=True),
    DialerOutcome(key="no_answer", label="No answer", follow_up=True),
    DialerOutcome(key="busy", label="Busy", follow_up=True),
    DialerOutcome(key="wrong_number", label="Wrong number"),
    DialerOutcome(key="scheduled_callback", label="Scheduled callback", connected=True, follow_up=True),
]


class DialerSettings(BaseModel):
    calling_method: Literal["device", "twilio"] = "device"
    outcomes: list[DialerOutcome] = Field(default_factory=lambda: [o.model_copy() for o in DEFAULT_OUTCOMES], min_length=1, max_length=30)
    require_notes: bool = False
    auto_follow_up: bool = True
    follow_up_days: int = Field(default=2, ge=0, le=60)
    twilio_account_sid: str | None = Field(default=None, max_length=64)
    twilio_from_number: str | None = Field(default=None, max_length=30)
    record_calls: bool = False

    @field_validator("outcomes")
    @classmethod
    def _unique(cls, values: list[DialerOutcome]) -> list[DialerOutcome]:
        keys = [v.key for v in values]
        if len(keys) != len(set(keys)):
            raise ValueError("Each call outcome needs its own key.")
        return values


class ProspectingSettings(BaseModel):
    duplicate_rule: Literal["email", "email_or_name_company", "off"] = "email"
    block_duplicates: bool = False
    auto_enrich_new: bool = False
    default_contact_stage: str = Field(default="cold", max_length=40)
    sequence_stage: str = Field(default="approaching", max_length=40)
    replied_stage: str = Field(default="replied", max_length=40)
    finished_stage: str = Field(default="unresponsive", max_length=40)
    check_suppression_on_import: bool = True
    block_role_addresses: bool = False


class SendingProtection(BaseModel):
    daily_limit_per_mailbox: int = Field(default=200, ge=1, le=5000)
    hourly_limit_per_mailbox: int = Field(default=40, ge=1, le=1000)
    min_seconds_between: int = Field(default=60, ge=0, le=3600)
    bounce_pause_percent: float = Field(default=5.0, ge=0.5, le=50)
    bounce_window: int = Field(default=100, ge=20, le=1000)
    skip_role_addresses: bool = True
    send_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Monday = 0
    window_start_hour: int = Field(default=8, ge=0, le=23)
    window_end_hour: int = Field(default=18, ge=1, le=24)
    timezone: str = Field(default="America/Chicago", max_length=60)
    holidays: list[str] = Field(default_factory=list, max_length=60)
    mailbox_limits: dict[str, int] = Field(default_factory=dict)

    @field_validator("send_days")
    @classmethod
    def _days(cls, values: list[int]) -> list[int]:
        days = sorted({int(v) for v in values if 0 <= int(v) <= 6})
        if not days:
            raise ValueError("Pick at least one sending day.")
        return days

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{value} isn't a time zone like America/Chicago.") from exc
        return value

    @field_validator("holidays")
    @classmethod
    def _holidays(cls, values: list[str]) -> list[str]:
        from datetime import date

        out = []
        for value in values:
            out.append(date.fromisoformat(str(value).strip()).isoformat())
        return sorted(set(out))


class TrackingSettings(BaseModel):
    open_tracking: bool = True
    click_tracking: bool = True
    unsubscribe_link: bool = True
    unsubscribe_text: str = Field(default="Not interested? Unsubscribe here.", max_length=200)
    tracking_domain: str | None = Field(default=None, max_length=255)

    @field_validator("tracking_domain")
    @classmethod
    def _domain(cls, value: str | None) -> str | None:
        return clean_domain(value) if value and value.strip() else None


class SequenceDefaults(BaseModel):
    stop_on_reply: bool = True
    stop_on_meeting: bool = True
    stop_on_bounce: bool = True
    one_active_sequence: bool = True
    allow_reenroll: bool = False
    thread_follow_ups: bool = True
    task_due_hours: int = Field(default=24, ge=1, le=336)


class EnrichmentProvider(BaseModel):
    key: Literal["builtin", "website", "hunter", "pdl"]
    enabled: bool = True


class EnrichmentSettings(BaseModel):
    providers: list[EnrichmentProvider] = Field(
        default_factory=lambda: [
            EnrichmentProvider(key="builtin"),
            EnrichmentProvider(key="website"),
            EnrichmentProvider(key="hunter", enabled=False),
            EnrichmentProvider(key="pdl", enabled=False),
        ]
    )
    overwrite: bool = False
    auto_enrich_new: bool = False
    fields: list[str] = Field(default_factory=lambda: ["company", "website", "industry", "phone", "job_title", "seniority", "department", "linkedin", "employees", "location", "email"])

    @field_validator("providers")
    @classmethod
    def _providers(cls, values: list[EnrichmentProvider]) -> list[EnrichmentProvider]:
        seen: list[EnrichmentProvider] = []
        for value in values:
            if value.key not in {s.key for s in seen}:
                seen.append(value)
        for key in ("builtin", "website", "hunter", "pdl"):
            if key not in {s.key for s in seen}:
                seen.append(EnrichmentProvider(key=key, enabled=False))
        return seen


class Stage(BaseModel):
    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=60)
    # open | positive | negative | closed
    category: Literal["open", "positive", "negative", "closed"] = "open"
    color: str = Field(default="gray", max_length=20)


DEFAULT_CONTACT_STAGES = [
    Stage(key="cold", label="Cold", category="open", color="gray"),
    Stage(key="approaching", label="Approaching", category="open", color="blue"),
    Stage(key="replied", label="Replied", category="positive", color="teal"),
    Stage(key="interested", label="Interested", category="positive", color="green"),
    Stage(key="not_interested", label="Not interested", category="negative", color="orange"),
    Stage(key="unresponsive", label="Unresponsive", category="negative", color="amber"),
    Stage(key="do_not_contact", label="Do not contact", category="closed", color="red"),
    Stage(key="bad_data", label="Bad data", category="closed", color="red"),
    Stage(key="changed_job", label="Changed job", category="closed", color="purple"),
]
DEFAULT_ACCOUNT_STAGES = [
    Stage(key="lead", label="Lead", category="open", color="gray"),
    Stage(key="prospect", label="Prospect", category="open", color="blue"),
    Stage(key="customer", label="Customer", category="positive", color="green"),
    Stage(key="partner", label="Partner", category="positive", color="purple"),
    Stage(key="churned", label="Churned", category="negative", color="red"),
    Stage(key="inactive", label="Inactive", category="closed", color="gray"),
]


class StageSettings(BaseModel):
    contact: list[Stage] = Field(default_factory=lambda: [s.model_copy() for s in DEFAULT_CONTACT_STAGES], min_length=1, max_length=40)
    account: list[Stage] = Field(default_factory=lambda: [s.model_copy() for s in DEFAULT_ACCOUNT_STAGES], min_length=1, max_length=40)

    @field_validator("contact", "account")
    @classmethod
    def _unique(cls, values: list[Stage]) -> list[Stage]:
        keys = [v.key for v in values]
        if len(keys) != len(set(keys)):
            raise ValueError("Each stage needs its own key.")
        return values


class WebsiteSettings(BaseModel):
    identify_on_form_submit: bool = True
    respect_do_not_track: bool = True
    exclude_paths: list[str] = Field(default_factory=list, max_length=100)
    exclude_ips: list[str] = Field(default_factory=list, max_length=100)
    retention_days: int = Field(default=395, ge=30, le=1825)

    @field_validator("exclude_ips")
    @classmethod
    def _ips(cls, values: list[str]) -> list[str]:
        return _ip_list(values)

    @field_validator("exclude_paths")
    @classmethod
    def _paths(cls, values: list[str]) -> list[str]:
        out = []
        for value in values:
            value = value.strip()
            if value:
                out.append(value if value.startswith("/") else f"/{value}")
        return out


class Competitor(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    differentiator: str = Field(default="", max_length=1000)


class Faq(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(default="", max_length=2000)


class AiContextSettings(BaseModel):
    company_overview: str = Field(default="", max_length=4000)
    products: str = Field(default="", max_length=4000)
    value_props: str = Field(default="", max_length=4000)
    ideal_customer: str = Field(default="", max_length=4000)
    competitors: list[Competitor] = Field(default_factory=list, max_length=30)
    tone: str = Field(default="Friendly, plain and professional.", max_length=500)
    words_to_avoid: list[str] = Field(default_factory=list, max_length=100)
    signature: str = Field(default="", max_length=1000)
    faqs: list[Faq] = Field(default_factory=list, max_length=50)
    use_for: list[Literal["drafts", "summaries", "agents", "ask", "conversations"]] = Field(
        default_factory=lambda: ["drafts", "summaries", "agents", "ask", "conversations"]
    )


class RecordingSettings(BaseModel):
    record_mode: Literal["external", "all", "manual", "none"] = "external"
    notetaker_name: str = Field(default="AIVA Notetaker", max_length=80)
    consent_notice: str = Field(
        default="This meeting may be recorded and transcribed by AIVA so we can share accurate notes. Let us know if you'd prefer it isn't.",
        max_length=1000,
    )
    add_consent_to_invites: bool = True
    retention_days: int = Field(default=365, ge=7, le=3650)
    keep_audio: bool = False
    transcription_provider: Literal["none", "deepgram", "assemblyai"] = "none"
    language: str = Field(default="en", max_length=10)
    auto_summarize: bool = True
    redact_numbers: bool = True


class ConversationAccess(BaseModel):
    view: Literal["participants", "team", "everyone"] = "everyone"
    managers_see_reports: bool = True
    download: Literal["owner", "managers", "everyone"] = "managers"
    delete: Literal["owner", "admins"] = "owner"
    reviewers_see_all: bool = True


class TeamMeetingSettings(BaseModel):
    record_internal: bool = False
    internal_domains: list[str] = Field(default_factory=list, max_length=30)
    skip_title_keywords: list[str] = Field(default_factory=lambda: ["1:1", "personal", "interview", "performance"], max_length=50)
    share_notes_with_attendees: bool = True

    @field_validator("internal_domains")
    @classmethod
    def _domain_list(cls, values: list[str]) -> list[str]:
        return _domains(values)


class McpSettings(BaseModel):
    enabled: bool = True
    allow_write: bool = False
    disabled_tools: list[str] = Field(default_factory=list, max_length=50)


NAMESPACES: dict[str, type[BaseModel]] = {
    "security": SecuritySettings,
    "licenses": LicenseSettings,
    "sharing": SharingSettings,
    "dialer": DialerSettings,
    "prospecting": ProspectingSettings,
    "sending": SendingProtection,
    "tracking": TrackingSettings,
    "sequences": SequenceDefaults,
    "enrichment": EnrichmentSettings,
    "stages": StageSettings,
    "website": WebsiteSettings,
    "ai_context": AiContextSettings,
    "recording": RecordingSettings,
    "conversation_access": ConversationAccess,
    "team_meetings": TeamMeetingSettings,
    "mcp": McpSettings,
}

# Secrets each namespace may hold (encrypted, write-only).
SECRETS: dict[str, tuple[str, ...]] = {
    "dialer": ("twilio_auth_token",),
    "enrichment": ("hunter_api_key", "pdl_api_key"),
    "recording": ("deepgram_api_key", "assemblyai_api_key"),
}

# Who may change each namespace.
PERMISSIONS: dict[str, str] = {
    "security": "admin.settings",
    "licenses": "admin.users",
    "sharing": "admin.settings",
    "dialer": "outbound.manage",
    "prospecting": "outbound.manage",
    "sending": "outbound.manage",
    "tracking": "outbound.manage",
    "sequences": "outbound.manage",
    "enrichment": "admin.settings",
    "stages": "admin.settings",
    "website": "marketing.manage",
    "ai_context": "admin.settings",
    "recording": "admin.settings",
    "conversation_access": "admin.settings",
    "team_meetings": "admin.settings",
    "mcp": "admin.settings",
}


class SettingsError(ValueError):
    pass


def _format_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first.get("loc", ()) if not isinstance(part, int))
    message = str(first.get("msg", "Invalid value.")).removeprefix("Value error, ")
    return f"{where.replace('_', ' ')}: {message}" if where else message


async def _row(db: AsyncSession, organization_id: uuid.UUID, namespace: str) -> WorkspaceSetting | None:
    return (
        await db.execute(
            select(WorkspaceSetting).where(WorkspaceSetting.organization_id == organization_id, WorkspaceSetting.namespace == namespace)
        )
    ).scalar_one_or_none()


def parse(namespace: str, data: dict[str, Any] | None) -> BaseModel:
    model = NAMESPACES[namespace]
    try:
        return model(**(data or {}))
    except ValidationError:
        # Stored data from an older version that no longer validates: fall back to defaults field by field.
        clean = {}
        for key, value in (data or {}).items():
            try:
                model(**{key: value})
                clean[key] = value
            except ValidationError:
                continue
        return model(**clean)


async def get(db: AsyncSession, organization_id: uuid.UUID, namespace: str) -> Any:
    row = await _row(db, organization_id, namespace)
    return parse(namespace, row.data if row else None)


async def get_many(db: AsyncSession, organization_id: uuid.UUID, namespaces: list[str]) -> dict[str, Any]:
    rows = {
        row.namespace: row
        for row in (
            await db.execute(
                select(WorkspaceSetting).where(WorkspaceSetting.organization_id == organization_id, WorkspaceSetting.namespace.in_(namespaces))
            )
        ).scalars()
    }
    return {ns: parse(ns, rows[ns].data if ns in rows else None) for ns in namespaces}


async def save(
    db: AsyncSession,
    organization_id: uuid.UUID,
    namespace: str,
    changes: dict[str, Any],
    user_id: uuid.UUID | None,
    secrets: dict[str, str | None] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Merge ``changes`` into the namespace, validate, store. Returns (settings, audit diff)."""

    if namespace not in NAMESPACES:
        raise SettingsError("Unknown settings section.")
    row = await _row(db, organization_id, namespace)
    before = parse(namespace, row.data if row else None).model_dump(mode="json")
    merged = {**before, **{k: v for k, v in (changes or {}).items() if k in NAMESPACES[namespace].model_fields}}
    try:
        value = NAMESPACES[namespace](**merged)
    except ValidationError as exc:
        raise SettingsError(_format_error(exc)) from exc
    after = value.model_dump(mode="json")
    if row is None:
        row = WorkspaceSetting(id=uuid.uuid4(), organization_id=organization_id, namespace=namespace, data={}, secrets={})
        db.add(row)
    row.data = after
    row.updated_by_user_id = user_id
    diff = {k: {"from": before.get(k), "to": after.get(k)} for k in after if before.get(k) != after.get(k)}
    allowed = SECRETS.get(namespace, ())
    if secrets:
        stored = dict(row.secrets or {})
        for key, secret in secrets.items():
            if key not in allowed:
                continue
            if secret is None or not str(secret).strip():
                if key in stored:
                    stored.pop(key)
                    diff[key] = {"from": "set", "to": "removed"}
            else:
                stored[key] = encrypt(str(secret).strip())
                diff[key] = {"from": "hidden", "to": "updated"}
        row.secrets = stored
    return value, diff


async def secret(db: AsyncSession, organization_id: uuid.UUID, namespace: str, key: str) -> str | None:
    row = await _row(db, organization_id, namespace)
    if row is None:
        return None
    value = (row.secrets or {}).get(key)
    return decrypt(value) if value else None


async def secrets_set(db: AsyncSession, organization_id: uuid.UUID, namespace: str) -> dict[str, bool]:
    row = await _row(db, organization_id, namespace)
    stored = (row.secrets or {}) if row else {}
    return {key: bool(stored.get(key)) for key in SECRETS.get(namespace, ())}


def stage_keys(stages: StageSettings, object_type: str) -> list[str]:
    return [s.key for s in getattr(stages, object_type)]
