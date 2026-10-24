"""Checks applied when people and companies are added or changed:
configurable stages, the do-not-contact list and duplicate rules
(Settings → Outbound → Prospecting configuration)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.governance import SuppressionEntry
from app.models.lead import Lead
from app.services import workspace_settings

ROLE_PREFIXES = ("info", "sales", "support", "admin", "contact", "hello", "office", "billing", "marketing", "noreply", "no-reply", "team", "help", "jobs", "hr")


def is_role_address(email: str | None) -> bool:
    local = (email or "").split("@")[0].lower()
    return local in ROLE_PREFIXES


async def validate_stage(db: AsyncSession, organization_id: uuid.UUID, object_type: str, key: str | None) -> None:
    if key is None:
        return
    stages = await workspace_settings.get(db, organization_id, "stages")
    allowed = workspace_settings.stage_keys(stages, object_type)
    if key not in allowed:
        labels = ", ".join(s.label for s in getattr(stages, object_type))
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"“{key}” isn't a {object_type} stage. Use one of: {labels}.")


async def suppression_for(db: AsyncSession, organization_id: uuid.UUID, email: str | None) -> SuppressionEntry | None:
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    domain = email.split("@")[-1]
    return (
        await db.execute(
            select(SuppressionEntry).where(
                SuppressionEntry.organization_id == organization_id,
                or_(
                    (SuppressionEntry.kind == "email") & (SuppressionEntry.value == email),
                    (SuppressionEntry.kind == "domain") & (SuppressionEntry.value == domain),
                ),
            )
        )
    ).scalars().first()


async def suppressed_emails(db: AsyncSession, organization_id: uuid.UUID, emails: list[str]) -> set[str]:
    emails = sorted({(e or "").strip().lower() for e in emails if e and "@" in e})
    if not emails:
        return set()
    rows = (
        await db.execute(
            select(SuppressionEntry.kind, SuppressionEntry.value).where(
                SuppressionEntry.organization_id == organization_id,
                or_(
                    (SuppressionEntry.kind == "email") & (SuppressionEntry.value.in_(emails)),
                    (SuppressionEntry.kind == "domain") & (SuppressionEntry.value.in_({e.split("@")[-1] for e in emails})),
                ),
            )
        )
    ).all()
    blocked_emails = {v for k, v in rows if k == "email"}
    blocked_domains = {v for k, v in rows if k == "domain"}
    return {e for e in emails if e in blocked_emails or e.split("@")[-1] in blocked_domains}


async def check_new_person(
    db: AsyncSession,
    organization_id: uuid.UUID,
    kind: str,
    *,
    email: str | None,
    first_name: str | None = None,
    last_name: str | None = None,
    company: str | None = None,
    account_id: uuid.UUID | None = None,
    exclude_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Raise if the person may not be added; return possible duplicates otherwise."""

    settings = await workspace_settings.get(db, organization_id, "prospecting")
    if email:
        entry = await suppression_for(db, organization_id, email)
        if entry is not None and entry.reason == "removal_request":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{email} asked to have their data removed, so they can't be added again (Settings → Removal requests).",
            )
        if settings.block_role_addresses and is_role_address(email):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{email} is a shared inbox. Add a named person instead (Prospecting configuration).")
    if settings.duplicate_rule == "off":
        return []
    matches: list[dict[str, Any]] = []
    model = Contact if kind == "contact" else Lead
    conditions = []
    if email:
        conditions.append(func.lower(model.email) == email.strip().lower())
    if settings.duplicate_rule == "email_or_name_company" and first_name and last_name:
        name_match = (func.lower(model.first_name) == first_name.strip().lower()) & (func.lower(model.last_name) == last_name.strip().lower())
        if kind == "contact" and account_id:
            conditions.append(name_match & (Contact.account_id == account_id))
        elif kind == "lead" and company:
            conditions.append(name_match & (func.lower(Lead.company_name) == company.strip().lower()))
    if not conditions:
        return []
    query = select(model).where(model.organization_id == organization_id, or_(*conditions))
    if exclude_id is not None:
        query = query.where(model.id != exclude_id)
    if kind == "contact":
        query = query.where(Contact.is_archived.is_(False))
    for row in (await db.execute(query.limit(5))).scalars():
        matches.append({"id": str(row.id), "name": f"{row.first_name} {row.last_name}".strip(), "email": row.email, "href": f"/{kind}s/{row.id}"})
    if matches and settings.block_duplicates:
        first = matches[0]
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{first['name'] or first['email']} is already in AIVA. Your workspace blocks duplicate {kind}s (Prospecting configuration).",
        )
    return matches
