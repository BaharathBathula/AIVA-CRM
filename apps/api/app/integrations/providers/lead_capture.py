"""Turning captured form answers (WordPress forms, Facebook lead ads) into AIVA leads.

Both integrations hand over a list of answers (key, label, value). This module:

* maps answers to lead fields, using the admin's mapping first and then sensible
  auto-detection (name, first/last name, email, phone, company, job title, message);
* never stores answers that look like secrets (passwords, card numbers);
* de-duplicates: by the provider's own id (a Facebook lead id is only imported
  once) and by email, where the workspace chooses to update the existing open lead,
  skip the submission, or always create a new lead;
* creates the lead with scoring, workflows, assignment rules, chat alerts and
  outgoing webhooks running exactly as they do for any other new lead.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.core import IntegrationError
from app.models.lead import Lead
from app.models.membership import OrganizationMembership

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TARGETS: dict[str, str] = {
    "full_name": "Full name",
    "first_name": "First name",
    "last_name": "Last name",
    "email": "Email",
    "phone": "Phone",
    "company_name": "Company",
    "job_title": "Job title",
    "notes": "Message (notes)",
    "ignore": "Don't import",
}
DUPLICATE_MODES = ("update", "skip", "create")

# Never stored, whatever the mapping says.
SENSITIVE = re.compile(r"(pass(word|wd)?|pwd|card_?(number|no)|credit_?card|cc_?num|cvv|cvc|security_?code|ssn|social_?security|iban|routing|account_?number|pin_?code|^pin$)")
NOISE_KEYS = re.compile(
    r"^(_.*|g-recaptcha-response|h-captcha-response|cf-turnstile-response|recaptcha|captcha|nonce|_wpnonce|action|submit|"
    r"wpforms\[.*\]|gform_.*|is_submit_.*|state_\d+|honeypot|hp|website_url_hp)$",
    re.I,
)

# Auto-detection on a normalised key or label (first match wins).
AUTO: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"(^|_)e_?mail(_address)?($|_)|^your_email$|^work_email$")),
    ("first_name", re.compile(r"(^|_)(first|given|fore)_?name($|_)|^fname$|^first$|names_first_name")),
    ("last_name", re.compile(r"(^|_)(last|family|sur)_?name($|_)|^lname$|^last$|names_last_name")),
    ("full_name", re.compile(r"^(your_)?(full_)?name$|^(contact|customer|client|your)_(full_)?name$|^names?$|^full_name$")),
    ("phone", re.compile(r"(phone|^tel$|telephone|mobile|^cell|whatsapp)")),
    ("company_name", re.compile(r"(^|_)(company|organi[sz]ation|business|employer|firm)(_name)?($|_)")),
    ("job_title", re.compile(r"(job_?title|^position$|^role$|^designation$|^job_role$|your_title)")),
    ("notes", re.compile(r"(message|comment|enquiry|inquiry|details|description|^notes?$|question|how_can_we_help|requirements|your_message)")),
)


def normalise(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:80]


@dataclass
class Answer:
    key: str
    label: str
    value: str
    type: str = ""


@dataclass
class Capture:
    """Everything needed to create or update one lead."""

    provider: str  # integration key, for logs
    lead_source: str
    answers: list[Answer]
    mapping: dict[str, str] = field(default_factory=dict)
    owner_user_id: uuid.UUID | None = None
    on_duplicate: str = "update"
    tags: list[str] = field(default_factory=list)
    attribution: dict[str, Any] = field(default_factory=dict)
    external_key: str | None = None  # attribution key for the provider's own id
    external_id: str | None = None
    origin: str = ""  # shown in notes, e.g. "Contact form on example.com"


@dataclass
class Outcome:
    result: str  # created | updated | skipped | duplicate
    lead: Lead | None
    mapped: dict[str, str]
    ignored: list[str]


def is_sensitive(answer: Answer) -> bool:
    return bool(SENSITIVE.search(normalise(answer.key)) or SENSITIVE.search(normalise(answer.label)) or answer.type in {"password", "credit-card", "creditcard", "payment"})


def is_noise(key: str) -> bool:
    return bool(NOISE_KEYS.match(key))


def auto_target(answer: Answer) -> str | None:
    for candidate in (normalise(answer.key), normalise(answer.label)):
        if not candidate:
            continue
        for target, pattern in AUTO:
            if pattern.search(candidate):
                return target
    if EMAIL_RE.match(answer.value.strip()):
        return "email"
    return None


def resolve_mapping(answers: list[Answer], mapping: dict[str, str]) -> dict[str, str]:
    """Which target each answer fills (explicit mapping first, then auto-detection)."""

    used: set[str] = set()
    resolved: dict[str, str] = {}
    for answer in answers:
        explicit = mapping.get(answer.key)
        target = explicit if explicit else auto_target(answer)
        if target and target not in {"notes", "ignore"} and not target.startswith("cf.") and target in used and not explicit:
            target = None  # a second email/phone goes to the notes instead
        if target:
            used.add(target)
        resolved[answer.key] = target or ""
    return resolved


def split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[1]


def apply_answers(answers: list[Answer], mapping: dict[str, str]) -> tuple[dict[str, str], dict[str, Any], list[str], list[str], dict[str, str]]:
    """Returns (lead values, custom field values, message lines, extra "label: value" lines, resolved mapping)."""

    resolved = resolve_mapping(answers, mapping)
    values: dict[str, str] = {}
    custom: dict[str, Any] = {}
    message: list[str] = []
    extra: list[str] = []
    for answer in answers:
        value = answer.value.strip()
        if not value or is_sensitive(answer):
            continue
        target = resolved.get(answer.key) or ""
        if target == "ignore":
            continue
        if target == "full_name":
            first, last = split_name(value)
            values.setdefault("first_name", first)
            values.setdefault("last_name", last)
        elif target == "email":
            if EMAIL_RE.match(value) and "email" not in values:
                values["email"] = value.lower()[:320]
            else:
                extra.append(f"{answer.label}: {value}")
        elif target in {"first_name", "last_name"}:
            values[target] = value[:120]
        elif target == "phone":
            values.setdefault("phone", value[:50])
        elif target == "company_name":
            values.setdefault("company_name", value[:250])
        elif target == "job_title":
            values.setdefault("job_title", value[:150])
        elif target == "notes":
            message.append(value if len(message) == 0 and normalise(answer.label) in {"message", "your_message", "comments", "comment"} else f"{answer.label}: {value}")
        elif target.startswith("cf."):
            custom[target[3:]] = value
        else:
            extra.append(f"{answer.label}: {value}")
    return values, custom, message, extra, resolved


async def valid_owner(db: AsyncSession, organization_id: uuid.UUID, user_id: Any) -> uuid.UUID | None:
    if not user_id:
        return None
    try:
        wanted = uuid.UUID(str(user_id))
    except ValueError as exc:
        raise IntegrationError("Choose a lead owner from the list.", 422) from exc
    found = (
        await db.execute(
            select(OrganizationMembership.user_id).where(
                OrganizationMembership.organization_id == organization_id, OrganizationMembership.user_id == wanted, OrganizationMembership.status == "active"
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise IntegrationError("The lead owner must be an active member of this workspace.", 422)
    return found


def clean_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()[:40]
        if text and text.lower() not in {t.lower() for t in out}:
            out.append(text)
    return out[:10]


def clean_mapping(raw: Any, custom_keys: set[str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, target in list(raw.items())[:200]:
        key = str(key)[:200]
        target = str(target or "")
        if not target:
            continue
        if target.startswith("cf."):
            if target[3:] not in custom_keys:
                raise IntegrationError(f"There's no lead field called {target[3:]}.", 422)
        elif target not in TARGETS:
            raise IntegrationError(f"Unknown lead field: {target}.", 422)
        out[key] = target
    return out


async def plan_for(db: AsyncSession, organization_id: uuid.UUID) -> tuple[frozenset[str], bool]:
    from app.models.editions import Subscription
    from app.services.billing import plan_state
    from app.services.metering import addon_features

    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == organization_id))).scalar_one_or_none()
    state = plan_state(subscription)
    return frozenset(set(state.features) | set(await addon_features(db, organization_id))), bool(state.read_only)


async def prepare(db: AsyncSession, organization_id: uuid.UUID, feature: str = "web_forms") -> None:
    """Check the plan and load the workspace's automation rules so the new lead is
    scored, assigned and announced like any other."""

    from app.services.automation import load_automation_context

    features, read_only = await plan_for(db, organization_id)
    if read_only:
        raise IntegrationError("This workspace is read-only (its plan ended), so new leads aren't accepted.", 403)
    if feature not in features:
        raise IntegrationError("Lead capture isn't included in this workspace's plan.", 403)
    db.sync_session.info["automation_disabled"] = False
    await load_automation_context(db, organization_id, features=features)


def _note_block(capture: Capture, message: list[str], extra: list[str]) -> str | None:
    lines = [*message, *extra]
    if not lines:
        return None
    return "\n".join(lines)[:20000]


async def save(db: AsyncSession, organization_id: uuid.UUID, capture: Capture, *, dry_run: bool = False) -> Outcome:
    """Create (or update) the lead for a capture. The caller commits."""

    from app.services.custom_fields import FieldValueError, apply_values, fields_for

    values, custom, message, extra, resolved = apply_answers(capture.answers, capture.mapping)
    mapped = {k: v for k, v in resolved.items() if v and v != "ignore"}
    ignored = [k for k, v in resolved.items() if v == "ignore"]

    if capture.external_key and capture.external_id:
        existing = (
            await db.execute(
                select(Lead.id).where(
                    Lead.organization_id == organization_id,
                    Lead.custom_fields["_attribution"][capture.external_key].astext == capture.external_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return Outcome("duplicate", await db.get(Lead, existing), mapped, ignored)

    if not values.get("email") and not values.get("last_name") and not values.get("first_name") and not values.get("phone"):
        raise IntegrationError("The submission had no name, email or phone to create a lead from. Check the field mapping.", 422)

    lead_fields = await fields_for(db, organization_id, "lead")
    try:
        custom_values = apply_values(lead_fields, {}, custom, creating=False)
    except FieldValueError as exc:
        extra.append(f"(Not saved to custom fields: {exc})")
        extra.extend(f"{k}: {v}" for k, v in custom.items())
        custom_values = {}

    email = values.get("email")
    notes = _note_block(capture, message, extra)
    attribution = {k: v for k, v in {**capture.attribution, **({capture.external_key: capture.external_id} if capture.external_key and capture.external_id else {})}.items() if v not in (None, "", [])}
    if capture.tags:
        attribution["tags"] = capture.tags

    if dry_run:
        return Outcome("created", None, mapped, ignored)

    if email and capture.on_duplicate != "create":
        lead = (
            await db.execute(
                select(Lead)
                .where(Lead.organization_id == organization_id, func.lower(Lead.email) == email, Lead.converted_at.is_(None))
                .order_by(Lead.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if lead is not None:
            if capture.on_duplicate == "skip":
                return Outcome("skipped", lead, mapped, ignored)
            _merge(lead, values, custom_values, notes, attribution, capture)
            return Outcome("updated", lead, mapped, ignored)

    last_name = values.get("last_name") or (email.split("@")[0] if email else values.get("phone") or "Unknown")
    lead = Lead(
        id=uuid.uuid4(),
        organization_id=organization_id,
        first_name=(values.get("first_name") or "")[:120],
        last_name=str(last_name)[:120],
        email=email,
        phone=values.get("phone"),
        company_name=values.get("company_name"),
        job_title=values.get("job_title"),
        notes=notes,
        source=(capture.lead_source or capture.provider)[:80],
        status="new",
        score=0,
        owner_user_id=capture.owner_user_id,
        custom_fields={**custom_values, "_attribution": attribution},
    )
    db.add(lead)
    await db.flush()
    return Outcome("created", lead, mapped, ignored)


def _merge(lead: Lead, values: dict[str, str], custom_values: dict[str, Any], notes: str | None, attribution: dict[str, Any], capture: Capture) -> None:
    """Fill in what the existing lead is missing and keep the new message."""

    if not lead.first_name and values.get("first_name"):
        lead.first_name = values["first_name"][:120]
    placeholder = {"", "unknown", (lead.email or "").split("@")[0].lower()}
    if values.get("last_name") and (lead.last_name or "").strip().lower() in placeholder:
        lead.last_name = values["last_name"][:120]
    for name in ("phone", "company_name", "job_title"):
        if values.get(name) and not getattr(lead, name):
            setattr(lead, name, values[name])
    if notes:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        heading = f"New submission{(' via ' + capture.origin) if capture.origin else ''} ({stamp}):"
        lead.notes = (f"{lead.notes}\n\n" if lead.notes else "") + f"{heading}\n{notes}"
        lead.notes = lead.notes[-20000:]
    current = dict(lead.custom_fields or {})
    for key, value in custom_values.items():
        current.setdefault(key, value)
    previous = dict(current.get("_attribution") or {})
    history = list(previous.get("submissions") or [])[-9:]
    history.append({k: v for k, v in attribution.items() if k != "submissions"} | {"at": datetime.now(timezone.utc).isoformat()})
    previous["submissions"] = history
    if capture.tags:
        previous["tags"] = clean_tags([*(previous.get("tags") or []), *capture.tags])
    current["_attribution"] = previous
    lead.custom_fields = current
