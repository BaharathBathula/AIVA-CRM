"""Web forms: field definitions and turning submissions into leads or cases."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.editions import Integration, OutboundMessage, Subscription, WebForm, WebFormSubmission
from app.models.lead import Lead
from app.models.platform import Case, Notification
from app.services.billing import plan_state
from app.services.custom_fields import FieldValueError, apply_values, fields_for

LEAD_FIELDS = {
    "first_name": "First name",
    "last_name": "Last name",
    "email": "Email",
    "phone": "Phone",
    "company_name": "Company",
    "job_title": "Job title",
    "notes": "Message",
}
CASE_FIELDS = {
    "name": "Your name",
    "email": "Email",
    "phone": "Phone",
    "company": "Company",
    "subject": "Subject",
    "description": "How can we help?",
    "priority": "Priority",
}
INPUT_TYPES = ("text", "email", "tel", "textarea", "select", "checkbox", "number", "date")
ATTRIBUTION_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "gbraid", "wbraid", "ga_client_id", "referrer", "page")
# Ad click IDs and the GA client ID are only kept when they look like one.
ATTRIBUTION_PATTERNS = {
    "gclid": re.compile(r"^[A-Za-z0-9_\-.]{8,200}$"),
    "gbraid": re.compile(r"^[A-Za-z0-9_\-.]{8,200}$"),
    "wbraid": re.compile(r"^[A-Za-z0-9_\-.]{8,200}$"),
    "ga_client_id": re.compile(r"^\d{1,20}\.\d{1,20}$"),
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def new_slug() -> str:
    return secrets.token_urlsafe(9).replace("-", "a").replace("_", "b").lower()


def default_fields(target: str) -> list[dict[str, Any]]:
    if target == "case":
        return [
            {"key": "name", "label": "Your name", "type": "text", "required": True, "maps_to": "name", "step": 1},
            {"key": "email", "label": "Email", "type": "email", "required": True, "maps_to": "email", "step": 1},
            {"key": "subject", "label": "Subject", "type": "text", "required": True, "maps_to": "subject", "step": 1},
            {"key": "description", "label": "How can we help?", "type": "textarea", "required": True, "maps_to": "description", "step": 1},
        ]
    return [
        {"key": "first_name", "label": "First name", "type": "text", "required": True, "maps_to": "first_name", "step": 1},
        {"key": "last_name", "label": "Last name", "type": "text", "required": True, "maps_to": "last_name", "step": 1},
        {"key": "email", "label": "Work email", "type": "email", "required": True, "maps_to": "email", "step": 1},
        {"key": "company_name", "label": "Company", "type": "text", "required": False, "maps_to": "company_name", "step": 1},
        {"key": "notes", "label": "How can we help?", "type": "textarea", "required": False, "maps_to": "notes", "step": 1},
    ]


def validate_fields(target: str, fields: list[dict[str, Any]], *, allow_steps: bool) -> list[dict[str, Any]]:
    targets = CASE_FIELDS if target == "case" else LEAD_FIELDS
    cleaned: list[dict[str, Any]] = []
    keys: set[str] = set()
    for index, raw in enumerate(fields, start=1):
        label = str(raw.get("label") or "").strip()
        if not label:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Field {index} needs a label.")
        input_type = raw.get("type") or "text"
        if input_type not in INPUT_TYPES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Field {index}: unknown input type.")
        maps_to = raw.get("maps_to") or ""
        if maps_to and maps_to not in targets and not maps_to.startswith("cf."):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Field {index}: choose which {target} field it fills.")
        key = re.sub(r"[^a-z0-9_]", "_", (raw.get("key") or label).lower())[:40] or f"field_{index}"
        while key in keys:
            key = f"{key}_{index}"
        keys.add(key)
        options = [str(o).strip() for o in raw.get("options") or [] if str(o).strip()]
        if input_type == "select" and not options:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Field {index}: add options for the dropdown.")
        step = int(raw.get("step") or 1)
        cleaned.append(
            {
                "key": key,
                "label": label[:120],
                "type": input_type,
                "required": bool(raw.get("required")),
                "maps_to": maps_to,
                "options": options,
                "placeholder": str(raw.get("placeholder") or "")[:120],
                "help": str(raw.get("help") or "")[:200],
                "step": max(1, min(10, step)) if allow_steps else 1,
            }
        )
    if not cleaned:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one field.")
    mapped = {field["maps_to"] for field in cleaned}
    required = {"email"} if target == "case" else {"last_name"}
    if target == "case" and "subject" not in mapped and "description" not in mapped:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Map a field to the case subject or description.")
    missing = required - mapped
    if missing and target == "lead" and "email" not in mapped:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Map a field to the lead's last name or email.")
    if missing and target == "case":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Case forms need an email field so you can reply.")
    return cleaned


def public_form(form: WebForm, organization_name: str) -> dict[str, Any]:
    settings = form.settings or {}
    return {
        "slug": form.slug,
        "name": form.name,
        "target": form.target,
        "title": settings.get("title") or form.name,
        "description": settings.get("description") or "",
        "submit_label": settings.get("submit_label") or "Submit",
        "success_message": settings.get("success_message") or "Thanks! We'll be in touch soon.",
        "redirect_url": settings.get("redirect_url") or None,
        "accent": settings.get("accent") or None,
        "organization_name": organization_name,
        "fields": [
            {k: field.get(k) for k in ("key", "label", "type", "required", "options", "placeholder", "help", "step")}
            for field in form.fields or []
        ],
        "steps": max([int(f.get("step") or 1) for f in form.fields or []] or [1]),
    }


def _clean(value: Any, input_type: str) -> Any:
    if input_type == "checkbox":
        return value in (True, "true", "on", "1", 1, "yes")
    if value is None:
        return None
    text = str(value).strip()
    return text[:5000] if text else None


async def submit(
    db: AsyncSession,
    form: WebForm,
    data: dict[str, Any],
    attribution: dict[str, Any],
    *,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    if not form.is_active:
        raise HTTPException(status.HTTP_410_GONE, "This form isn't accepting responses.")
    if str(data.get("_hp") or "").strip():
        # Honeypot filled in: a bot. Pretend it worked.
        return {"ok": True}

    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == form.organization_id))).scalar_one_or_none()
    state = plan_state(subscription)
    if state.read_only:
        raise HTTPException(status.HTTP_410_GONE, "This form isn't accepting responses right now.")
    if form.target == "case" and "web_to_case" not in state.features:
        raise HTTPException(status.HTTP_410_GONE, "This form isn't accepting responses right now.")

    values: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for field in form.fields or []:
        value = _clean(data.get(field["key"]), field["type"])
        if field.get("required") and value in (None, "", False):
            errors[field["key"]] = f"{field['label']} is required."
            continue
        if value not in (None, "") and field["type"] == "email" and not EMAIL_RE.match(str(value)):
            errors[field["key"]] = "Enter a valid email address."
            continue
        if value not in (None, "") and field["type"] == "select" and field.get("options") and value not in field["options"]:
            errors[field["key"]] = f"Choose a {field['label'].lower()} from the list."
            continue
        target = field.get("maps_to") or ""
        if target.startswith("cf."):
            custom[target[3:]] = value
        elif target:
            values[target] = value
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": "Check the highlighted fields.", "fields": errors})

    attribution = {
        k: str(v)[:500]
        for k, v in attribution.items()
        if k in ATTRIBUTION_KEYS and v and (k not in ATTRIBUTION_PATTERNS or ATTRIBUTION_PATTERNS[k].match(str(v)))
    }
    settings = form.settings or {}
    owner_id = uuid.UUID(settings["owner_user_id"]) if settings.get("owner_user_id") else form.created_by_user_id
    record_type, record_id = form.target, None
    link = None

    if form.target == "lead":
        email = (values.get("email") or "").lower() or None
        last_name = values.get("last_name") or (email.split("@")[0] if email else "Unknown")
        lead_fields = await fields_for(db, form.organization_id, "lead")
        try:
            custom_values = apply_values(lead_fields, {}, custom, creating=False)
        except FieldValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        extra_notes = [f"{field['label']}: {data.get(field['key'])}" for field in form.fields or [] if not field.get("maps_to") and data.get(field["key"])]
        notes = "\n".join([values.get("notes") or "", *extra_notes]).strip() or None
        lead = Lead(
            id=uuid.uuid4(),
            organization_id=form.organization_id,
            first_name=(values.get("first_name") or "")[:100],
            last_name=str(last_name)[:100],
            email=email,
            phone=values.get("phone"),
            company_name=values.get("company_name"),
            job_title=values.get("job_title"),
            notes=notes,
            source=settings.get("lead_source") or ("google_ads" if attribution.get("gclid") or attribution.get("gbraid") or attribution.get("wbraid") else "web_form"),
            status="new",
            score=0,
            owner_user_id=owner_id,
            custom_fields={**custom_values, **({"_attribution": {**attribution, "form": form.name}} if attribution else {"_attribution": {"form": form.name}})},
        )
        db.add(lead)
        record_id = lead.id
        link = f"/leads/{lead.id}"
        label = f"{lead.first_name} {lead.last_name}".strip()
    else:
        email = (values.get("email") or "").lower()
        contact = (
            await db.execute(
                select(Contact).where(Contact.organization_id == form.organization_id, func.lower(Contact.email) == email).limit(1)
            )
        ).scalar_one_or_none()
        from app.api.routes.service import _apply_sla, _policy_for  # noqa: PLC0415 - reuse SLA rules

        number = (
            await db.execute(select(func.coalesce(func.max(Case.number), 1000)).where(Case.organization_id == form.organization_id))
        ).scalar_one() + 1
        priority = values.get("priority") if values.get("priority") in {"low", "medium", "high", "urgent"} else "medium"
        details = [values.get("description") or ""]
        if not contact:
            details.append(f"\nFrom: {values.get('name') or ''} <{email}> {values.get('phone') or ''} {values.get('company') or ''}".rstrip())
        now = datetime.now(timezone.utc)
        case = Case(
            id=uuid.uuid4(),
            organization_id=form.organization_id,
            number=number,
            subject=(values.get("subject") or (values.get("description") or "Web request")[:80])[:250],
            description="\n".join(part for part in details if part).strip() or None,
            status="new",
            priority=priority,
            channel="web",
            contact_id=contact.id if contact else None,
            account_id=contact.account_id if contact else None,
            created_by_user_id=None,
            linked_article_ids=[],
            tags=["web form"],
            custom_fields={"_requester": {"name": values.get("name"), "email": email, "phone": values.get("phone"), "company": values.get("company")}},
        )
        case.created_at = now
        _apply_sla(case, await _policy_for(db, form.organization_id, priority), now)
        db.add(case)
        record_id = case.id
        link = f"/cases/{case.id}"
        label = f"CS-{number}: {case.subject}"

    db.add(
        WebFormSubmission(
            id=uuid.uuid4(),
            organization_id=form.organization_id,
            form_id=form.id,
            data={k: v for k, v in data.items() if not k.startswith("_")},
            attribution=attribution,
            record_type=record_type,
            record_id=record_id,
            ip_address=ip,
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    form.submission_count = (form.submission_count or 0) + 1
    form.last_submission_at = datetime.now(timezone.utc)
    notify = settings.get("notify_user_id") or (str(owner_id) if owner_id and form.target == "lead" else None)
    if notify:
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=form.organization_id,
                user_id=uuid.UUID(str(notify)),
                kind="form",
                title=f"New {form.target} from “{form.name}”"[:200],
                body=label,
                link=link,
                is_read=False,
            )
        )
    if "chat_integrations" in state.features:
        chats = (
            await db.execute(
                select(Integration).where(
                    Integration.organization_id == form.organization_id,
                    Integration.kind.in_(("slack", "teams")),
                    Integration.is_active.is_(True),
                )
            )
        ).scalars().all()
        for chat in chats:
            if "form_submitted" in (chat.events or []):
                db.add(
                    OutboundMessage(
                        id=uuid.uuid4(),
                        organization_id=form.organization_id,
                        channel=chat.kind,
                        event="form_submitted",
                        payload={"title": f"Web form: {form.name}", "text": label, "link": link},
                        status="pending",
                    )
                )
    return {"ok": True, "record_type": record_type, "record_id": str(record_id)}
