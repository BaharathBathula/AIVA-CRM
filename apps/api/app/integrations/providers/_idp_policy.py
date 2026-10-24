"""Workspace identity policy: verified email domains and "Require single sign-on".

Stored in ``workspace_settings`` under the ``identity`` namespace (admin-only,
never part of ``organizations.settings``, which every member can read)::

    {
      "require_sso": false,
      "domains": [{"domain", "token", "status", "added_at", "added_by", "checked_at", "verified_at", "error"}],
      "verified": ["acme.com"]          # verified domains, kept for quick lookups
    }

A domain is verified by publishing a DNS TXT record ``aiva-verification=<token>``
on the domain itself (or on ``_aiva-verification.<domain>``).

Require single sign-on applies to members whose email domain is one of the
workspace's verified domains. It's only enforced while it can actually work:
the plan includes single sign-on, the workspace's sign-on connection is on and
routes that domain. Owners keep password sign-in as a break-glass; API keys are
unaffected. Other workspaces the person belongs to are never affected.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import WorkspaceSetting
from app.models.identity import SsoConnection
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.user import User

NAMESPACE = "identity"
MAX_DOMAINS = 20
RECORD_PREFIX = "aiva-verification="
DNS_TIMEOUT = 5.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def email_domain(email: str | None) -> str:
    return (email or "").rsplit("@", 1)[-1].strip().lower()


# ---------------------------------------------------------------------------
# Stored settings
# ---------------------------------------------------------------------------


async def row(db: AsyncSession, organization_id: uuid.UUID, *, create: bool = False) -> WorkspaceSetting | None:
    found = (
        await db.execute(select(WorkspaceSetting).where(WorkspaceSetting.organization_id == organization_id, WorkspaceSetting.namespace == NAMESPACE))
    ).scalar_one_or_none()
    if found is None and create:
        found = WorkspaceSetting(id=uuid.uuid4(), organization_id=organization_id, namespace=NAMESPACE, data={}, secrets={})
        db.add(found)
    return found


async def load(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, Any]:
    found = await row(db, organization_id)
    return normalize(found.data if found else None)


def normalize(data: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(data or {})
    domains = [dict(d) for d in data.get("domains") or [] if isinstance(d, dict) and d.get("domain")]
    return {
        "require_sso": bool(data.get("require_sso")),
        "domains": domains,
        "verified": sorted({d["domain"] for d in domains if d.get("status") == "verified"}),
        "require_sso_changed_at": data.get("require_sso_changed_at"),
        "require_sso_changed_by": data.get("require_sso_changed_by"),
    }


async def save(db: AsyncSession, organization_id: uuid.UUID, data: dict[str, Any], user_id: uuid.UUID | None) -> dict[str, Any]:
    clean = normalize(data)
    found = await row(db, organization_id, create=True)
    found.data = clean
    found.updated_by_user_id = user_id
    return clean


def new_token() -> str:
    return secrets.token_urlsafe(24)


def record_value(token: str) -> str:
    return f"{RECORD_PREFIX}{token}"


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


class DnsUnavailable(Exception):
    """No DNS resolver library on this server."""


async def lookup_txt(name: str) -> list[str]:
    """TXT strings published at ``name`` (empty when there are none). Tests replace this."""

    try:
        import dns.asyncresolver
        import dns.exception
        import dns.resolver
    except ImportError as exc:  # pragma: no cover - dnspython ships with email-validator
        raise DnsUnavailable("Domain checks need the dnspython package on the AIVA server (pip install dnspython).") from exc

    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = DNS_TIMEOUT
    resolver.timeout = DNS_TIMEOUT / 2
    try:
        answer = await asyncio.wait_for(resolver.resolve(name, "TXT"), timeout=DNS_TIMEOUT + 1)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout, asyncio.TimeoutError) as exc:
        raise TimeoutError(f"DNS didn't answer for {name}.") from exc
    values: list[str] = []
    for item in answer:
        strings = getattr(item, "strings", None) or []
        values.append(b"".join(strings).decode("utf-8", "replace"))
    return values[:50]


async def check_domain(domain: str, token: str) -> tuple[bool, str]:
    """Look for ``aiva-verification=<token>`` at the domain (or ``_aiva-verification.<domain>``)."""

    expected = record_value(token)
    seen_other = False
    for name in (domain, f"_aiva-verification.{domain}"):
        try:
            values = await lookup_txt(name)
        except DnsUnavailable as exc:
            return False, str(exc)
        except TimeoutError:
            return False, f"DNS for {domain} didn't answer. Try again in a few minutes."
        for value in values:
            text = value.strip().strip('"')
            if secrets.compare_digest(text.encode(), expected.encode()):
                return True, f"Found the TXT record on {name}."
            if text.startswith(RECORD_PREFIX):
                seen_other = True
    if seen_other:
        return False, "Found an aiva-verification record, but its value doesn't match. Copy the value shown here exactly."
    return False, f"No TXT record {expected[:22]}… on {domain} yet. DNS changes can take up to an hour to appear."


# ---------------------------------------------------------------------------
# Require single sign-on
# ---------------------------------------------------------------------------


async def _sso_ready(db: AsyncSession, organization_id: uuid.UUID, domain: str) -> bool:
    """Single sign-on would actually work for this domain right now."""

    from app.services.billing import get_subscription, plan_state

    connection = (await db.execute(select(SsoConnection).where(SsoConnection.organization_id == organization_id))).scalar_one_or_none()
    if connection is None or not connection.is_active or domain not in (connection.domains or []):
        return False
    state = plan_state(await get_subscription(db, organization_id))
    return "sso" in state.features and not state.read_only


async def sso_required(db: AsyncSession, user: User, memberships: list[tuple[OrganizationMembership, Organization]]) -> dict[uuid.UUID, str]:
    """Workspaces (id → name) among ``memberships`` where this person must sign in with single sign-on."""

    from app.services.oidc import PUBLIC_EMAIL_DOMAINS

    domain = email_domain(user.email)
    if not domain or domain in PUBLIC_EMAIL_DOMAINS:
        return {}
    # Owners keep password sign-in (break-glass). Every other role is covered, custom roles
    # included; AIVA support never has a membership (it opens workspaces through a grant).
    candidates = {org.id: org.name for membership, org in memberships if membership.role != "owner"}
    if not candidates:
        return {}
    rows = (
        await db.execute(
            select(WorkspaceSetting.organization_id, WorkspaceSetting.data).where(
                WorkspaceSetting.organization_id.in_(list(candidates)), WorkspaceSetting.namespace == NAMESPACE
            )
        )
    ).all()
    required: dict[uuid.UUID, str] = {}
    for organization_id, data in rows:
        policy = normalize(data)
        if policy["require_sso"] and domain in policy["verified"] and await _sso_ready(db, organization_id, domain):
            required[organization_id] = candidates[organization_id]
    return required


async def sso_required_for(db: AsyncSession, user: User, organization_id: uuid.UUID, role: str) -> bool:
    organization = await db.get(Organization, organization_id)
    if organization is None:
        return False
    membership = OrganizationMembership(organization_id=organization_id, user_id=user.id, role=role, status="active")
    return organization_id in await sso_required(db, user, [(membership, organization)])


def blocked_message(user: User, workspace_names: list[str]) -> str:
    domain = email_domain(user.email)
    where = workspace_names[0] if len(workspace_names) == 1 else "your workspace"
    return (
        f"{where} requires single sign-on for @{domain} accounts. On the sign-in page choose "
        f"“Log In with your Organization” and enter your work email."
    )
