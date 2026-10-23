"""Merge fields for email templates, campaigns, journeys and custom buttons.

Templates use ``{{first_name}}`` style placeholders with an optional
fallback: ``{{first_name|there}}``. Custom field values are available as
``{{cf.<key>}}``.
"""

from __future__ import annotations

import html
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import is_encrypted
from app.models.account import Account
from app.models.organization import Organization
from app.models.user import User

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*(?:\|\s*([^}]*?)\s*)?\}\}")

MERGE_FIELDS = [
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("full_name", "Full name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("company", "Company"),
    ("job_title", "Job title"),
    ("owner_name", "Record owner"),
    ("owner_email", "Owner's email"),
    ("sender_name", "Your name"),
    ("organization_name", "Your company"),
    ("today", "Today's date"),
    ("unsubscribe_url", "Unsubscribe link"),
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y")
    if isinstance(value, date):
        return value.strftime("%b %d, %Y")
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def render(template: str | None, context: dict[str, Any]) -> str:
    if not template:
        return ""

    def replace(match: re.Match[str]) -> str:
        key, fallback = match.group(1), match.group(2)
        value = context.get(key)
        if key.startswith("cf."):
            value = (context.get("cf") or {}).get(key[3:])
            if is_encrypted(value):
                value = None  # encrypted values never leave AIVA in emails
        text = _text(value)
        return text if text else (fallback or "")

    return PLACEHOLDER.sub(replace, template)


def unsubscribe_url(token: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/u/{token}"


def tracking_pixel_url(token: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/api/aiva/public/email/open/{token}.gif"


async def record_context(
    db: AsyncSession,
    record: Any,
    *,
    sender: User | None = None,
    organization: Organization | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    first = getattr(record, "first_name", None) or ""
    last = getattr(record, "last_name", None) or ""
    company = getattr(record, "company_name", None)
    if company is None and getattr(record, "account_id", None):
        account = await db.get(Account, record.account_id)
        company = account.name if account else None
    if company is None and isinstance(record, Account):
        company = record.name
    owner = None
    owner_id = getattr(record, "owner_user_id", None)
    if owner_id:
        owner = await db.get(User, owner_id)
    if organization is None and getattr(record, "organization_id", None):
        organization = await db.get(Organization, record.organization_id)
    return {
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}".strip() or getattr(record, "name", "") or "",
        "email": getattr(record, "email", None) or "",
        "phone": getattr(record, "phone", None) or "",
        "company": company or "",
        "job_title": getattr(record, "job_title", None) or "",
        "owner_name": owner.full_name if owner else "",
        "owner_email": owner.email if owner else "",
        "sender_name": sender.full_name if sender else (owner.full_name if owner else ""),
        "organization_name": organization.name if organization else "",
        "today": date.today(),
        "unsubscribe_url": unsubscribe_url(token) if token else "",
        "cf": dict(getattr(record, "custom_fields", None) or {}),
    }


def text_to_html(body: str, *, token: str | None = None, footer: str | None = None) -> str:
    """Plain text email → simple, readable HTML with links, a footer and an open pixel."""

    escaped = html.escape(body or "")
    linked = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', escaped)
    paragraphs = "".join(
        f"<p style=\"margin:0 0 14px\">{part.replace(chr(10), '<br>')}</p>" for part in linked.split("\n\n") if part.strip()
    )
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:#18171c">',
        paragraphs,
    ]
    if footer:
        parts.append(f'<p style="margin:24px 0 0;font-size:12px;color:#7a7784">{footer}</p>')
    if token:
        parts.append(f'<img src="{tracking_pixel_url(token)}" width="1" height="1" alt="" style="display:block;border:0">')
    parts.append("</div>")
    return "".join(parts)


def new_send_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex[:16]
