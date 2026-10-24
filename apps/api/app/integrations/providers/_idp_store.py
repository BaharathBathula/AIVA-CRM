"""SCIM provisioning for Microsoft Entra ID and Okta: tokens, users, groups and group mapping.

* **Tokens.** One per workspace and identity provider: ``aiva_scim_<lookup><secret>``.
  The lookup half sits in the workspace connection's indexed ``webhook_token``
  column; only a SHA-256 of the secret half is stored (encrypted, in the
  connection's secrets). Requests are matched to a workspace by the token alone
  and the secret is compared in constant time. The token is shown once.
* **Users** are the workspace's members. SCIM ``id`` is the AIVA user id. Each
  provisioned person has an ``IntegrationRecordLink`` (record_type ``user``,
  external_type ``scim_user``, provider = the identity provider) holding their
  externalId and the attributes the provider sent, so they read back unchanged.
  ``active: false`` deactivates the membership in this workspace and revokes the
  person's sessions and API keys here; other workspaces are never touched, and
  the last owner is never deprovisioned. People who belong to other workspaces
  keep their AIVA profile (name, email): the provider's values are stored for
  SCIM only.
* **Groups** are stored as ``IntegrationRecordLink`` rows (record_type and
  external_type ``scim_group``; SCIM ``id`` = ``record_id``) with their members.
  The workspace's mapping turns groups into AIVA roles and teams.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import sha256
from app.core.permissions import SYSTEM_ROLES
from app.integrations import core
from app.integrations.catalog import INTEGRATIONS
from app.integrations.providers import _idp_scim as scim
from app.integrations.providers._idp_scim import ScimError
from app.models.editions import ApiKey, Subscription, TeamMember
from app.models.integration_hub import (
    IntegrationConnection,
    IntegrationLog,
    IntegrationRecordLink,
)
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.platform import Role, Team
from app.models.user import User
from app.services.automation import record_audit

KEYS = ("entra_id", "okta")
TOKEN_PREFIX = "aiva_scim_"
TOKEN_SECRET = "scim_token"  # name in connection.secrets (encrypted JSON: hash, created_at, created_by, hint)
PART = 43
USER = ("user", "scim_user")
GROUP = ("scim_group", "scim_group")
REQUESTS_PER_MINUTE = 1200
FAILURES_PER_MINUTE = 20
MAX_GROUPS = 5_000
DUMMY_HASH = sha256("aiva-scim-no-such-token")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def name_of(key: str) -> str:
    return INTEGRATIONS[key].name


def base_url() -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/api/aiva/public/scim/v2"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def token_meta(connection: IntegrationConnection | None) -> dict[str, Any] | None:
    raw = core.secret(connection, TOKEN_SECRET) if connection is not None else None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("hash") else None


def token_info(connection: IntegrationConnection | None) -> dict[str, Any] | None:
    """What admins see about the token (never the token or its hash)."""

    meta = token_meta(connection)
    if meta is None:
        return None
    return {
        "hint": f"{TOKEN_PREFIX}…{meta.get('hint', '')}",
        "created_at": meta.get("created_at"),
        "created_by": meta.get("created_by_name"),
        "last_used_at": connection.last_used_at.isoformat() if connection is not None and connection.last_used_at else None,
    }


def issue_token(connection: IntegrationConnection, user_id: uuid.UUID, user_name: str) -> str:
    """A new token (replacing any earlier one). Returned once; only a hash of its secret is kept."""

    lookup = secrets.token_urlsafe(32)[:PART]
    secret = secrets.token_urlsafe(32)[:PART]
    connection.webhook_token = lookup
    connection.last_used_at = None
    meta = {"hash": sha256(secret), "created_at": utcnow().isoformat(), "created_by": str(user_id), "created_by_name": user_name[:200], "hint": secret[-4:]}
    core.set_secret(connection, TOKEN_SECRET, json.dumps(meta))
    return f"{TOKEN_PREFIX}{lookup}{secret}"


def revoke_token(connection: IntegrationConnection) -> bool:
    had = token_meta(connection) is not None
    core.set_secret(connection, TOKEN_SECRET, None)
    connection.webhook_token = core.new_webhook_token()
    return had


_REQUESTS: dict[str, deque] = defaultdict(deque)
_FAILURES: dict[str, deque] = defaultdict(deque)


def _recent(buckets: dict[str, deque], key: str, window: float = 60.0) -> deque:
    now = time.monotonic()
    bucket = buckets[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(buckets) > 20_000:
        buckets.clear()
        bucket = buckets[key]
    return bucket


def _limited(buckets: dict[str, deque], key: str, limit: int, window: float = 60.0) -> bool:
    """Count this request; True when the key already made ``limit`` requests in the window."""

    bucket = _recent(buckets, key, window)
    if len(bucket) >= limit:
        return True
    bucket.append(time.monotonic())
    return False


def _failed(ip: str | None) -> None:
    _recent(_FAILURES, f"ip:{ip}").append(time.monotonic())


def _unauthorized(detail: str = "The SCIM token isn't valid. Create a new one in AIVA (Integrations).") -> ScimError:
    return ScimError(401, detail, headers={"WWW-Authenticate": 'Bearer realm="AIVA SCIM"'})


async def authenticate(db: AsyncSession, authorization: str | None, ip: str | None) -> IntegrationConnection:
    """The workspace connection a bearer token belongs to. The token alone picks the workspace."""

    if len(_recent(_FAILURES, f"ip:{ip}")) >= FAILURES_PER_MINUTE:
        raise ScimError(429, "Too many failed sign-ins to the SCIM service. Wait a minute.", headers={"Retry-After": "60"})
    raw = (authorization or "").strip()
    if not raw.lower().startswith("bearer "):
        _failed(ip)
        raise _unauthorized("Send the SCIM token as a bearer token (Authorization: Bearer aiva_scim_...).")
    token = raw[7:].strip()
    body = token[len(TOKEN_PREFIX):] if token.startswith(TOKEN_PREFIX) else ""
    connection: IntegrationConnection | None = None
    if len(body) == PART * 2:
        connection = (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.webhook_token == body[:PART],
                    IntegrationConnection.provider.in_(KEYS),
                    IntegrationConnection.user_id.is_(None),
                )
            )
        ).scalar_one_or_none()
    meta = token_meta(connection)
    expected = (meta or {}).get("hash") or DUMMY_HASH
    given = sha256(body[PART:] if body else secrets.token_hex(8))
    if not hmac.compare_digest(given.encode(), str(expected).encode()) or connection is None or meta is None:
        _failed(ip)
        raise _unauthorized()
    if _limited(_REQUESTS, str(connection.id), REQUESTS_PER_MINUTE):
        raise ScimError(429, "Too many SCIM requests for this workspace. Slow down and retry.", headers={"Retry-After": "30"})
    return connection


async def check_access(db: AsyncSession, connection: IntegrationConnection, writing: bool) -> dict[str, Any]:
    """Provisioning only works while the integration is on and the plan includes single sign-on."""

    from app.services.billing import plan_state
    from app.services.metering import addon_features

    org = connection.organization_id
    key = connection.provider
    organization = await db.get(Organization, org)
    if organization is None or not organization.is_active:
        raise ScimError(403, "This AIVA workspace is closed.")
    toggle = await core.toggle_row(db, org, key)
    if toggle is None or not toggle.enabled:
        raise ScimError(403, f"Provisioning is paused: {name_of(key)} is turned off in AIVA (Integrations).")
    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == org))).scalar_one_or_none()
    state = plan_state(subscription)
    if "sso" not in (state.features | await addon_features(db, org)):
        raise ScimError(403, "Single sign-on and provisioning aren't included in this workspace's AIVA plan.")
    if writing and state.read_only:
        raise ScimError(403, "This AIVA workspace is read-only (its plan ended), so people can't be changed.")
    now = utcnow()
    if connection.last_used_at is None or (now - connection.last_used_at).total_seconds() > 60:
        connection.last_used_at = now
    return dict(toggle.config or {})


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class Ctx:
    org: uuid.UUID
    key: str
    connection: IntegrationConnection
    settings: dict[str, Any]
    ip: str | None = None

    @property
    def label(self) -> str:
        return name_of(self.key)


def _log(db: AsyncSession, ctx: Ctx, action: str, message: str, level: str = "info", detail: dict[str, Any] | None = None) -> None:
    core.log(db, ctx.org, ctx.key, action, message, level=level, connection=ctx.connection, detail=detail)


def _audit(db: AsyncSession, ctx: Ctx, action: str, entity_type: str, entity_id: uuid.UUID | None, label: str | None, changes: dict[str, Any]) -> None:
    record_audit(
        db, organization_id=ctx.org, action=action, entity_type=entity_type, entity_id=entity_id, entity_label=label,
        changes={**changes, "source": f"{ctx.label} (SCIM)"}, actor_user_id=None, ip=ctx.ip,
    )


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Roles and teams
# ---------------------------------------------------------------------------


async def valid_roles(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, str]:
    """Roles SCIM may give (never owner): key → name."""

    roles = {k: r.name for k, r in SYSTEM_ROLES.items() if k != "owner"}
    for role in (await db.execute(select(Role).where(Role.organization_id == organization_id).order_by(Role.name))).scalars():
        if role.key != "owner":
            roles[role.key] = role.name
    return roles


async def teams(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, str]:
    rows = (await db.execute(select(Team.id, Team.name).where(Team.organization_id == organization_id).order_by(Team.name))).all()
    return {str(team_id): name for team_id, name in rows}


async def clean_mapping(db: AsyncSession, organization_id: uuid.UUID, config: dict[str, Any]) -> dict[str, Any]:
    """Validate the mapping settings (default role, group → role/team, automatic teams)."""

    roles = await valid_roles(db, organization_id)
    team_names = await teams(db, organization_id)
    out: dict[str, Any] = {}
    if "default_role" in config:
        role = str(config.get("default_role") or "member")
        if role not in roles:
            raise core.IntegrationError("Choose the role new people get (owner can't be given by provisioning).", 422)
        out["default_role"] = role
    if "auto_teams" in config:
        out["auto_teams"] = bool(config.get("auto_teams"))
    if "group_mappings" in config:
        raw = config.get("group_mappings") or []
        if not isinstance(raw, list) or len(raw) > 200:
            raise core.IntegrationError("Map at most 200 groups.", 422)
        mappings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("group_name") or "").strip()[:300]
            group_id = str(item.get("group_id") or "").strip()[:200] or None
            if not name and not group_id:
                continue
            role = str(item.get("role") or "").strip() or None
            team_id = str(item.get("team_id") or "").strip() or None
            if role is not None and role not in roles:
                raise core.IntegrationError(f"Choose an existing role for {name or group_id} (owner can't be given by provisioning).", 422)
            if team_id is not None and team_id not in team_names:
                raise core.IntegrationError(f"Choose an existing team for {name or group_id}.", 422)
            if role is None and team_id is None:
                continue
            marker = (group_id or name).lower()
            if marker in seen:
                raise core.IntegrationError(f"{name or group_id} is mapped twice.", 422)
            seen.add(marker)
            mappings.append({"group_id": group_id, "group_name": name or group_id, "role": role, "team_id": team_id})
        out["group_mappings"] = mappings
    return out


def mapping_settings(config: dict[str, Any]) -> tuple[str, list[dict[str, Any]], bool]:
    return (
        str(config.get("default_role") or "member"),
        [m for m in config.get("group_mappings") or [] if isinstance(m, dict)],
        bool(config.get("auto_teams")),
    )


def _matches(mapping: dict[str, Any], group: IntegrationRecordLink) -> bool:
    data = group.data or {}
    group_id = mapping.get("group_id")
    if group_id and group_id in {str(group.record_id), str(data.get("externalId") or "")}:
        return True
    name = str(mapping.get("group_name") or "").strip().lower()
    return bool(name) and name == str(data.get("displayName") or "").strip().lower()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def _links_for(db: AsyncSession, ctx: Ctx, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, IntegrationRecordLink]:
    ids = list({u for u in user_ids})
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == ctx.org,
                IntegrationRecordLink.provider == ctx.key,
                IntegrationRecordLink.record_type == USER[0],
                IntegrationRecordLink.external_type == USER[1],
                IntegrationRecordLink.record_id.in_(ids),
            )
        )
    ).scalars().all()
    return {row.record_id: row for row in rows}


async def _member(db: AsyncSession, ctx: Ctx, user_id: uuid.UUID) -> tuple[User, OrganizationMembership] | None:
    row = (
        await db.execute(
            select(User, OrganizationMembership)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == ctx.org, User.id == user_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


def derive_email(resource: dict[str, Any]) -> str | None:
    """The address AIVA signs the person in with: primary email, then work email, then userName."""

    emails = [e for e in resource.get("emails") or [] if scim.is_email(e.get("value"))]
    chosen = (
        next((e for e in emails if e.get("primary") is True), None)
        or next((e for e in emails if str(e.get("type") or "").lower() == "work"), None)
        or (emails[0] if emails else None)
    )
    if chosen:
        return str(chosen["value"]).strip().lower()
    username = resource.get("userName")
    return str(username).strip().lower() if scim.is_email(username) else None


def _full_name(resource: dict[str, Any]) -> str | None:
    name = resource.get("name") or {}
    joined = " ".join(x for x in (name.get("givenName"), name.get("familyName")) if x)
    return (resource.get("displayName") or name.get("formatted") or joined or "").strip()[:200] or None


def _phone(resource: dict[str, Any]) -> str | None:
    phones = resource.get("phoneNumbers") or []
    chosen = next((p for p in phones if p.get("primary") is True), None) or next((p for p in phones if str(p.get("type") or "") == "work"), None) or (phones[0] if phones else None)
    return str(chosen.get("value"))[:50] if chosen and chosen.get("value") else None


def _derived_resource(user: User) -> dict[str, Any]:
    parts = (user.full_name or "").split(" ", 1)
    resource: dict[str, Any] = {
        "userName": user.email,
        "displayName": user.full_name,
        "name": {k: v for k, v in {"formatted": user.full_name, "givenName": parts[0] or None, "familyName": parts[1] if len(parts) > 1 else None}.items() if v},
        "emails": [{"value": user.email, "type": "work", "primary": True}],
    }
    if user.job_title:
        resource["title"] = user.job_title
    if user.phone:
        resource["phoneNumbers"] = [{"value": user.phone, "type": "work"}]
    return resource


def user_resource(user: User, membership: OrganizationMembership, link: IntegrationRecordLink | None, *, base: str | None = None) -> dict[str, Any]:
    base = base or base_url()
    data = (link.data or {}) if link is not None else {}
    stored = dict(data.get("resource") or {}) or _derived_resource(user)
    stored.pop("active", None)
    stored.pop("members", None)
    schemas = [scim.USER_SCHEMA] + [k for k in stored if k.startswith("urn:")]
    out: dict[str, Any] = {"schemas": schemas, "id": str(user.id)}
    external = data.get("externalId")
    if external:
        out["externalId"] = external
    for key, value in stored.items():
        if key != "externalId" and value not in (None, "", [], {}):
            out[key] = value
    out.setdefault("userName", user.email)
    out["active"] = membership.status in {"active", "invited"}
    modified = max(filter(None, [membership.updated_at, link.updated_at if link is not None else None, membership.created_at]))
    out["meta"] = {
        "resourceType": "User",
        "created": scim.iso(membership.created_at),
        "lastModified": scim.iso(modified),
        "location": f"{base}/Users/{user.id}",
    }
    return out


async def _user_ids_for_filter(db: AsyncSession, ctx: Ctx, attr: str, value: Any) -> set[uuid.UUID] | None:
    """Candidate user ids for one ``eq`` filter (None = every member)."""

    link_base = and_(
        IntegrationRecordLink.organization_id == ctx.org,
        IntegrationRecordLink.provider == ctx.key,
        IntegrationRecordLink.record_type == USER[0],
        IntegrationRecordLink.external_type == USER[1],
    )
    unlinked = ~exists().where(link_base, IntegrationRecordLink.record_id == User.id)
    members = select(User.id).join(OrganizationMembership, OrganizationMembership.user_id == User.id).where(OrganizationMembership.organization_id == ctx.org)
    text = "" if value is None else str(value)
    lowered = text.strip().lower()
    if attr == "id":
        found = _uuid(text)
        return {found} if found else set()
    if attr == "externalid":
        rows = (await db.execute(select(IntegrationRecordLink.record_id).where(link_base, IntegrationRecordLink.external_id == text))).scalars()
        return set(rows)
    if attr == "username":
        linked = (await db.execute(select(IntegrationRecordLink.record_id).where(link_base, IntegrationRecordLink.data["userName_lc"].astext == lowered))).scalars()
        plain = (await db.execute(members.where(User.email == lowered, unlinked))).scalars()
        return set(linked) | set(plain)
    if attr == "emails.value":
        linked = (await db.execute(select(IntegrationRecordLink.record_id).where(link_base, IntegrationRecordLink.data["emails_lc"].contains([lowered])))).scalars()
        plain = (await db.execute(members.where(User.email == lowered, unlinked))).scalars()
        return set(linked) | set(plain)
    if attr == "displayname":
        linked = (await db.execute(select(IntegrationRecordLink.record_id).where(link_base, IntegrationRecordLink.data["displayName_lc"].astext == lowered))).scalars()
        plain = (await db.execute(members.where(func.lower(User.full_name) == lowered, unlinked))).scalars()
        return set(linked) | set(plain)
    if attr == "active":
        wanted = scim.truthy(value)
        statuses = ("active", "invited") if wanted else ("deactivated",)
        return set((await db.execute(members.where(OrganizationMembership.status.in_(statuses)))).scalars())
    raise ScimError(400, f"Filtering users on {attr} isn't supported. Use userName, externalId, emails or id.", "invalidFilter")


async def list_users(db: AsyncSession, ctx: Ctx, filter_text: str | None, start: int, count: int) -> dict[str, Any]:
    filters = scim.parse_filter(filter_text)
    query = (
        select(User, OrganizationMembership)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id == ctx.org)
    )
    candidates: set[uuid.UUID] | None = None
    for attr, value in filters:
        found = await _user_ids_for_filter(db, ctx, attr, value)
        candidates = found if candidates is None else candidates & found
    if candidates is not None:
        if not candidates:
            return scim.list_response([], 0, start)
        query = query.where(User.id.in_(list(candidates)))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = []
    if count > 0:
        rows = (
            await db.execute(query.order_by(OrganizationMembership.created_at, OrganizationMembership.id).offset(start - 1).limit(count))
        ).all()
    links = await _links_for(db, ctx, [user.id for user, _ in rows])
    base = base_url()
    resources = [user_resource(user, membership, links.get(user.id), base=base) for user, membership in rows]
    return scim.list_response(resources, total, start)


async def get_user(db: AsyncSession, ctx: Ctx, user_id: str) -> dict[str, Any]:
    user, membership, link = await _require_user(db, ctx, user_id)
    return user_resource(user, membership, link)


async def _require_user(db: AsyncSession, ctx: Ctx, user_id: str) -> tuple[User, OrganizationMembership, IntegrationRecordLink | None]:
    found = _uuid(user_id)
    member = await _member(db, ctx, found) if found else None
    if member is None:
        raise ScimError(404, f"User {str(user_id)[:80]} not found.")
    user, membership = member
    link = (await _links_for(db, ctx, [user.id])).get(user.id)
    return user, membership, link


async def _owner_count(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.role == "owner",
                OrganizationMembership.status == "active",
            )
        )
    ).scalar_one()


async def _protect_last_owner(db: AsyncSession, ctx: Ctx, membership: OrganizationMembership) -> None:
    if membership.role == "owner" and membership.status == "active" and await _owner_count(db, ctx.org) <= 1:
        raise ScimError(400, "This person is the workspace's only owner in AIVA, so they can't be deprovisioned. Make someone else an owner first.", "mutability")


async def _other_workspaces(db: AsyncSession, ctx: Ctx, user_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id, OrganizationMembership.organization_id != ctx.org
            )
        )
    ).scalar_one()


async def _revoke_access(db: AsyncSession, ctx: Ctx, user_id: uuid.UUID) -> tuple[int, int]:
    """Sign the person out of this workspace and revoke their API keys here."""

    from app.services.auth import revoke_workspace_sessions

    sessions = await revoke_workspace_sessions(db, user_id, ctx.org)
    keys = (
        await db.execute(
            update(ApiKey)
            .where(ApiKey.organization_id == ctx.org, ApiKey.created_by_user_id == user_id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
    ).rowcount or 0
    return sessions, keys


async def _seat_check(db: AsyncSession, ctx: Ctx) -> None:
    from app.services import workspace_settings

    licenses = await workspace_settings.get(db, ctx.org, "licenses")
    if licenses.auto_add_seats:
        return
    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == ctx.org))).scalar_one_or_none()
    if subscription is None or subscription.plan == "trial" or not subscription.seats:
        return
    taken = (
        await db.execute(
            select(func.count()).select_from(OrganizationMembership).where(
                OrganizationMembership.organization_id == ctx.org, OrganizationMembership.status.in_(("active", "invited"))
            )
        )
    ).scalar_one()
    if taken >= subscription.seats:
        raise ScimError(403, f"All {subscription.seats} AIVA seats are taken. Add seats in AIVA (Plan overview) or turn on automatic seats.")


async def _domain_check(db: AsyncSession, ctx: Ctx, email: str) -> None:
    from app.services import workspace_settings

    security = await workspace_settings.get(db, ctx.org, "security")
    domain = email.rsplit("@", 1)[-1]
    if security.allowed_email_domains and domain not in security.allowed_email_domains:
        allowed = ", ".join("@" + d for d in security.allowed_email_domains)
        raise ScimError(400, f"This AIVA workspace only allows {allowed} addresses (Settings → Security).", "invalidValue")


def _link_data(resource: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(previous or {})
    stored = {k: v for k, v in resource.items() if k not in {"active", "members", "externalId"}}
    data["resource"] = stored
    data["externalId"] = resource.get("externalId")
    data["userName_lc"] = str(resource.get("userName") or "").strip().lower()
    data["emails_lc"] = sorted({str(e.get("value")).strip().lower() for e in resource.get("emails") or [] if e.get("value")})
    data["displayName_lc"] = str(resource.get("displayName") or "").strip().lower()
    return data


async def _check_unique(db: AsyncSession, ctx: Ctx, resource: dict[str, Any], user_id: uuid.UUID | None) -> None:
    user_name = str(resource.get("userName") or "").strip().lower()
    if not user_name:
        raise ScimError(400, "userName is required.", "invalidValue")
    conditions = [
        IntegrationRecordLink.organization_id == ctx.org,
        IntegrationRecordLink.provider == ctx.key,
        IntegrationRecordLink.record_type == USER[0],
        IntegrationRecordLink.external_type == USER[1],
    ]
    if user_id is not None:
        conditions.append(IntegrationRecordLink.record_id != user_id)
    clash = (await db.execute(select(IntegrationRecordLink.id).where(*conditions, IntegrationRecordLink.data["userName_lc"].astext == user_name).limit(1))).first()
    if clash:
        raise ScimError(409, f"A user with userName {resource['userName']} already exists.", "uniqueness")
    external = resource.get("externalId")
    if external:
        clash = (await db.execute(select(IntegrationRecordLink.id).where(*conditions, IntegrationRecordLink.external_id == str(external)[:200]).limit(1))).first()
        if clash:
            raise ScimError(409, f"A user with externalId {external} already exists.", "uniqueness")


async def _save_link(db: AsyncSession, ctx: Ctx, user: User, link: IntegrationRecordLink | None, resource: dict[str, Any]) -> IntegrationRecordLink:
    external = str(resource.get("externalId") or "")[:200] or f"aiva:{user.id}"
    if link is None:
        link = IntegrationRecordLink(
            id=uuid.uuid4(), organization_id=ctx.org, provider=ctx.key, record_type=USER[0], record_id=user.id,
            external_type=USER[1], external_id=external, external_parent="", status="linked", data={},
        )
        db.add(link)
    link.external_id = external
    link.data = _link_data(resource, link.data)
    link.last_synced_at = utcnow()
    link.status = "linked"
    return link


async def _email_change_blocked(db: AsyncSession, ctx: Ctx, link_data: dict[str, Any] | None, email: str) -> str | None:
    """Why provisioning may not move this account to ``email`` (None when it may).

    The email is the person's sign-in, so a provider only changes it for accounts
    this provisioning created, and only to an address on a domain the workspace
    has verified. Anything else would let whoever controls the provider redirect
    someone else's sign-in (password resets, magic links) to a mailbox they own.
    """

    from app.integrations.providers import _idp_policy

    if not (link_data or {}).get("created_by_scim"):
        return "this account wasn't created by provisioning, so its sign-in email stays as the person set it"
    domain = _idp_policy.email_domain(email)
    if domain not in (await _idp_policy.load(db, ctx.org))["verified"]:
        return f"@{domain} isn't one of this workspace's verified domains (Single sign-on → Verified domains)"
    return None


async def _apply_profile(
    db: AsyncSession, ctx: Ctx, user: User, membership: OrganizationMembership, link_data: dict[str, Any] | None,
    resource: dict[str, Any], changes: dict[str, Any],
) -> str | None:
    """Name, title, phone and email follow the provider for the people it manages here.

    Owners, AIVA staff and people who also belong to other workspaces keep their own
    profile. Returns the email the provider asked for when the change was refused.
    """

    if membership.role == "owner" or user.is_platform_staff or await _other_workspaces(db, ctx, user.id):
        return None
    refused: str | None = None
    email = derive_email(resource)
    if email and email != user.email:
        reason = await _email_change_blocked(db, ctx, link_data, email)
        if reason:
            refused = email
            if (link_data or {}).get("email_refused") != email:
                _log(
                    db, ctx, "scim_email_kept",
                    f"Kept {user.email} as {user.full_name or user.email}'s sign-in email instead of {email}: {reason}.",
                    "warning", {"user_id": str(user.id), "requested": email},
                )
        else:
            taken = (await db.execute(select(User.id).where(User.email == email, User.id != user.id))).first()
            if taken:
                raise ScimError(409, f"Another AIVA account already uses {email}.", "uniqueness")
            changes["email"] = {"from": user.email, "to": email}
            user.email = email
    full_name = _full_name(resource)
    if full_name and full_name != user.full_name:
        changes["name"] = {"from": user.full_name, "to": full_name}
        user.full_name = full_name
    title = (resource.get("title") or "")[:150] or None
    if title != user.job_title:
        changes["job_title"] = {"from": user.job_title, "to": title}
        user.job_title = title
    phone = _phone(resource)
    if phone != user.phone and (phone or user.phone):
        user.phone = phone
    return refused


async def _set_active(db: AsyncSession, ctx: Ctx, user: User, membership: OrganizationMembership, link: IntegrationRecordLink, active: bool, changes: dict[str, Any]) -> None:
    data = dict(link.data or {})
    if membership.status == "active" and data.get("invited"):
        data.pop("invited", None)
    if not active and membership.status != "deactivated":
        await _protect_last_owner(db, ctx, membership)
        changes["status"] = {"from": membership.status, "to": "deactivated"}
        data["invited"] = membership.status == "invited" or bool(data.get("invited"))
        membership.status = "deactivated"
        sessions, keys = await _revoke_access(db, ctx, user.id)
        changes["sessions_revoked"] = sessions
        changes["api_keys_revoked"] = keys
        _log(db, ctx, "scim_user_deactivated", f"Deactivated {user.full_name or user.email}: signed out of this workspace ({sessions} session{'s' if sessions != 1 else ''}, {keys} API key{'s' if keys != 1 else ''} revoked).", "warning", {"user_id": str(user.id)})
    elif active and membership.status == "deactivated":
        await _seat_check(db, ctx)
        status = "invited" if data.get("invited") else "active"
        changes["status"] = {"from": "deactivated", "to": status}
        membership.status = status
        if status == "active":
            data.pop("invited", None)
        _log(db, ctx, "scim_user_reactivated", f"Reactivated {user.full_name or user.email}.", "success", {"user_id": str(user.id)})
    link.data = data


async def create_user(db: AsyncSession, ctx: Ctx, body: dict[str, Any]) -> dict[str, Any]:
    resource = scim.clean_user(body)
    resource.pop("members", None)
    if not str(resource.get("userName") or "").strip():
        raise ScimError(400, "userName is required.", "invalidValue")
    email = derive_email(resource)
    if not email:
        raise ScimError(400, "AIVA needs an email address: send a userName that is an email, or an emails value.", "invalidValue")
    await _check_unique(db, ctx, resource, None)
    active = resource.get("active", True)
    default_role, _, _ = mapping_settings(ctx.settings)
    roles = await valid_roles(db, ctx.org)
    role = default_role if default_role in roles else "member"

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    created = user is None  # a brand-new AIVA account, so provisioning owns its sign-in email
    membership = None
    adopted = False  # an existing member that provisioning now manages
    if user is not None:
        member = await _member(db, ctx, user.id)
        if member is not None:
            membership = member[1]
            if (await _links_for(db, ctx, [user.id])).get(user.id) is not None:
                raise ScimError(409, f"{email} is already provisioned in this workspace.", "uniqueness")
            adopted = True
    if membership is None:
        await _domain_check(db, ctx, email)
        if active:
            await _seat_check(db, ctx)
        if user is None:
            user = User(id=uuid.uuid4(), email=email, full_name=_full_name(resource) or email, password_hash=None, is_active=True)
            db.add(user)
            await db.flush()
        # Someone who already uses AIVA elsewhere is invited (they accept by signing in); new people are active.
        others = await _other_workspaces(db, ctx, user.id)
        membership = OrganizationMembership(id=uuid.uuid4(), organization_id=ctx.org, user_id=user.id, role=role, status="invited" if others else "active")
        db.add(membership)
        await db.flush()
    changes: dict[str, Any] = {"email": email, "role": membership.role, "membership": membership.status}
    await _apply_profile(db, ctx, user, membership, {"created_by_scim": created}, resource, {})
    link = await _save_link(db, ctx, user, None, resource)
    extra: dict[str, Any] = {}
    if membership.status == "invited":
        extra["invited"] = True
    if adopted is False:
        extra["role_from_scim"] = membership.role
    if created:
        extra["created_by_scim"] = True
    link.data = {**link.data, **extra}
    await db.flush()
    await _set_active(db, ctx, user, membership, link, bool(active), changes)
    await recompute(db, ctx, [user.id])
    _audit(db, ctx, "scim_provision", "user", user.id, user.full_name, changes)
    _log(db, ctx, "scim_user_created", f"Provisioned {user.full_name or email} ({email}) as {roles.get(membership.role, membership.role)}.", "success", {"user_id": str(user.id)})
    await db.flush()
    return user_resource(user, membership, link)


async def _write_user(db: AsyncSession, ctx: Ctx, user: User, membership: OrganizationMembership, link: IntegrationRecordLink | None, resource: dict[str, Any]) -> dict[str, Any]:
    resource.pop("members", None)
    if not str(resource.get("userName") or "").strip():
        raise ScimError(400, "userName is required.", "invalidValue")
    await _check_unique(db, ctx, resource, user.id)
    active = resource.pop("active", None)
    changes: dict[str, Any] = {}
    refused = await _apply_profile(db, ctx, user, membership, link.data if link is not None else None, resource, changes)
    link = await _save_link(db, ctx, user, link, resource)
    data = dict(link.data or {})
    if refused:
        data["email_refused"] = refused  # logged once per requested address
    else:
        data.pop("email_refused", None)
    link.data = data
    await db.flush()
    if active is not None:
        await _set_active(db, ctx, user, membership, link, bool(active), changes)
    if changes:
        _audit(db, ctx, "scim_update", "user", user.id, user.full_name, changes)
        if set(changes) - {"status", "sessions_revoked", "api_keys_revoked"}:
            _log(db, ctx, "scim_user_updated", f"Updated {user.full_name or user.email} ({', '.join(sorted(set(changes) - {'sessions_revoked', 'api_keys_revoked'}))}).", detail={"user_id": str(user.id)})
    await db.flush()
    return user_resource(user, membership, link)


async def replace_user(db: AsyncSession, ctx: Ctx, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    user, membership, link = await _require_user(db, ctx, user_id)
    resource = scim.clean_user(body)
    return await _write_user(db, ctx, user, membership, link, resource)


async def patch_user(db: AsyncSession, ctx: Ctx, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    user, membership, link = await _require_user(db, ctx, user_id)
    current = dict((link.data or {}).get("resource") or {}) if link is not None else _derived_resource(user)
    if link is not None and (link.data or {}).get("externalId"):
        current["externalId"] = link.data["externalId"]
    current["active"] = membership.status in {"active", "invited"}
    resource = scim.apply_patch(current, body)
    if resource.get("active") == current["active"]:
        resource.pop("active", None)  # unchanged: leave invited/active as it is
    return await _write_user(db, ctx, user, membership, link, resource)


async def delete_user(db: AsyncSession, ctx: Ctx, user_id: str) -> None:
    user, membership, link = await _require_user(db, ctx, user_id)
    await _protect_last_owner(db, ctx, membership)
    sessions, keys = await _revoke_access(db, ctx, user.id)
    team_rows = (await db.execute(delete(TeamMember).where(TeamMember.organization_id == ctx.org, TeamMember.user_id == user.id))).rowcount or 0
    await _drop_from_groups(db, ctx, {str(user.id)})
    if link is not None:
        await db.delete(link)
    await db.delete(membership)
    _audit(db, ctx, "scim_remove", "user", user.id, user.full_name, {"email": user.email, "sessions_revoked": sessions, "api_keys_revoked": keys, "teams_left": team_rows})
    _log(db, ctx, "scim_user_removed", f"Removed {user.full_name or user.email} from this workspace (their AIVA account and other workspaces are untouched).", "warning", {"user_id": str(user.id)})
    await db.flush()


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def _group_base() -> list[Any]:
    return [IntegrationRecordLink.record_type == GROUP[0], IntegrationRecordLink.external_type == GROUP[1]]


async def _groups(db: AsyncSession, ctx: Ctx) -> list[IntegrationRecordLink]:
    return list(
        (
            await db.execute(
                select(IntegrationRecordLink)
                .where(IntegrationRecordLink.organization_id == ctx.org, IntegrationRecordLink.provider == ctx.key, *_group_base())
                .order_by(IntegrationRecordLink.created_at, IntegrationRecordLink.id)
                .limit(MAX_GROUPS)
            )
        ).scalars()
    )


async def _require_group(db: AsyncSession, ctx: Ctx, group_id: str, *, lock: bool = False) -> IntegrationRecordLink:
    found = _uuid(group_id)
    link = None
    if found:
        query = select(IntegrationRecordLink).where(
            IntegrationRecordLink.organization_id == ctx.org, IntegrationRecordLink.provider == ctx.key,
            IntegrationRecordLink.record_id == found, *_group_base(),
        )
        if lock:
            # Identity providers send member changes in parallel; one at a time per group.
            query = query.with_for_update()
        link = (await db.execute(query)).scalar_one_or_none()
    if link is None:
        raise ScimError(404, f"Group {str(group_id)[:80]} not found.")
    return link


async def _valid_members(db: AsyncSession, ctx: Ctx, values: list[str]) -> tuple[list[str], int]:
    """Member ids that are people in this workspace (others are skipped, never added)."""

    wanted: list[uuid.UUID] = []
    for value in values:
        found = _uuid(value)
        if found and found not in wanted:
            wanted.append(found)
    valid: set[uuid.UUID] = set()
    for start in range(0, len(wanted), 1000):
        chunk = wanted[start:start + 1000]
        valid |= set(
            (
                await db.execute(
                    select(OrganizationMembership.user_id).where(OrganizationMembership.organization_id == ctx.org, OrganizationMembership.user_id.in_(chunk))
                )
            ).scalars()
        )
    kept = [str(u) for u in wanted if u in valid]
    return kept, len(values) - len(kept)


async def group_resource(db: AsyncSession, link: IntegrationRecordLink, *, with_names: bool, base: str | None = None) -> dict[str, Any]:
    base = base or base_url()
    data = link.data or {}
    members = list(data.get("members") or [])
    names: dict[str, str] = {}
    if with_names and members:
        ids = [u for u in (_uuid(m) for m in members) if u]
        for start in range(0, len(ids), 1000):
            rows = (
                await db.execute(
                    select(User.id, User.full_name)
                    .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
                    .where(OrganizationMembership.organization_id == link.organization_id, User.id.in_(ids[start:start + 1000]))
                )
            ).all()
            names.update({str(i): n for i, n in rows})
    out: dict[str, Any] = {"schemas": [scim.GROUP_SCHEMA], "id": str(link.record_id), "displayName": data.get("displayName") or ""}
    if data.get("externalId"):
        out["externalId"] = data["externalId"]
    out["members"] = [
        {k: v for k, v in {"value": m, "display": names.get(m), "$ref": f"{base}/Users/{m}"}.items() if v}
        for m in members
    ]
    out["meta"] = {"resourceType": "Group", "created": scim.iso(link.created_at), "lastModified": scim.iso(link.updated_at or link.created_at), "location": f"{base}/Groups/{link.record_id}"}
    return out


async def list_groups(db: AsyncSession, ctx: Ctx, filter_text: str | None, start: int, count: int, excluded: str | None, attributes: str | None) -> dict[str, Any]:
    conditions = [IntegrationRecordLink.organization_id == ctx.org, IntegrationRecordLink.provider == ctx.key, *_group_base()]
    for attr, value in scim.parse_filter(filter_text):
        text = "" if value is None else str(value)
        if attr == "displayname":
            conditions.append(IntegrationRecordLink.data["displayName_lc"].astext == text.strip().lower())
        elif attr == "externalid":
            conditions.append(IntegrationRecordLink.external_id == text)
        elif attr == "id":
            found = _uuid(text)
            if found is None:
                return scim.list_response([], 0, start)
            conditions.append(IntegrationRecordLink.record_id == found)
        elif attr == "members.value":
            conditions.append(IntegrationRecordLink.data["members"].contains([text.lower()]))
        else:
            raise ScimError(400, f"Filtering groups on {attr} isn't supported. Use displayName, externalId or id.", "invalidFilter")
    query = select(IntegrationRecordLink).where(*conditions)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = []
    if count > 0:
        rows = list((await db.execute(query.order_by(IntegrationRecordLink.created_at, IntegrationRecordLink.id).offset(start - 1).limit(count))).scalars())
    base = base_url()
    resources = [scim.project(await group_resource(db, row, with_names=False, base=base), attributes, excluded) for row in rows]
    return scim.list_response(resources, total, start)


async def get_group(db: AsyncSession, ctx: Ctx, group_id: str, excluded: str | None, attributes: str | None) -> dict[str, Any]:
    link = await _require_group(db, ctx, group_id)
    wants_members = not (excluded and "members" in excluded.lower()) and not (attributes and "members" not in attributes.lower())
    return scim.project(await group_resource(db, link, with_names=wants_members), attributes, excluded)


async def _check_group_unique(db: AsyncSession, ctx: Ctx, name: str, external: str | None, record_id: uuid.UUID | None) -> None:
    conditions = [IntegrationRecordLink.organization_id == ctx.org, IntegrationRecordLink.provider == ctx.key, *_group_base()]
    if record_id is not None:
        conditions.append(IntegrationRecordLink.record_id != record_id)
    if (await db.execute(select(IntegrationRecordLink.id).where(*conditions, IntegrationRecordLink.data["displayName_lc"].astext == name.lower()).limit(1))).first():
        raise ScimError(409, f"A group named {name} already exists.", "uniqueness")
    if external and (await db.execute(select(IntegrationRecordLink.id).where(*conditions, IntegrationRecordLink.external_id == external).limit(1))).first():
        raise ScimError(409, f"A group with externalId {external} already exists.", "uniqueness")


def _group_name(resource: dict[str, Any]) -> str:
    name = str(resource.get("displayName") or "").strip()
    if not name:
        raise ScimError(400, "displayName is required.", "invalidValue")
    if len(name) > 300:
        raise ScimError(400, "displayName is too long.", "invalidValue")
    return name


def _group_data(previous: dict[str, Any] | None, name: str, external: str | None, members: list[str]) -> dict[str, Any]:
    data = dict(previous or {})
    data.update({"displayName": name, "displayName_lc": name.lower(), "externalId": external, "members": members})
    return data


async def create_group(db: AsyncSession, ctx: Ctx, body: dict[str, Any]) -> dict[str, Any]:
    resource = scim.clean_group(body)
    name = _group_name(resource)
    external = str(resource.get("externalId") or "")[:200] or None
    await _check_group_unique(db, ctx, name, external, None)
    members, skipped = await _valid_members(db, ctx, resource.get("members") or [])
    record_id = uuid.uuid4()
    link = IntegrationRecordLink(
        id=uuid.uuid4(), organization_id=ctx.org, provider=ctx.key, record_type=GROUP[0], record_id=record_id,
        external_type=GROUP[1], external_id=external or f"aiva:{record_id}", external_parent="", status="linked",
        data=_group_data(None, name, external, members), last_synced_at=utcnow(),
    )
    db.add(link)
    await db.flush()
    changed = await recompute(db, ctx, [uuid.UUID(m) for m in members])
    _audit(db, ctx, "scim_group_create", "scim_group", record_id, name, {"members": len(members)})
    _log(db, ctx, "scim_group_created", f"Group {name} created with {len(members)} member{'s' if len(members) != 1 else ''}"
         + (f"; roles or teams changed for {changed}." if changed else ".") + (f" {skipped} unknown member id(s) skipped." if skipped else ""),
         "success", {"group_id": str(record_id)})
    await db.flush()
    return await group_resource(db, link, with_names=True)


async def _write_group(db: AsyncSession, ctx: Ctx, link: IntegrationRecordLink, resource: dict[str, Any], replace_members: bool) -> None:
    data = link.data or {}
    name = _group_name(resource)
    external = str(resource.get("externalId") or "")[:200] or None
    await _check_group_unique(db, ctx, name, external, link.record_id)
    old_members = list(data.get("members") or [])
    members = old_members
    skipped = 0
    if replace_members:
        members, skipped = await _valid_members(db, ctx, resource.get("members") or [])
    old_set, new_set = set(old_members), set(members)
    added, removed = new_set - old_set, old_set - new_set
    renamed = name != data.get("displayName")
    link.data = _group_data(data, name, external, members)
    link.external_id = external or f"aiva:{link.record_id}"
    link.last_synced_at = utcnow()
    await db.flush()
    touched = added | removed
    if renamed:
        touched |= new_set  # a name-based mapping may now match (or stop matching)
    changed = await recompute(db, ctx, [uuid.UUID(m) for m in touched]) if touched else 0
    changes: dict[str, Any] = {}
    if renamed:
        changes["name"] = {"from": data.get("displayName"), "to": name}
    if added:
        changes["members_added"] = len(added)
    if removed:
        changes["members_removed"] = len(removed)
    if changes:
        _audit(db, ctx, "scim_group_update", "scim_group", link.record_id, name, changes)
        parts = []
        if renamed:
            parts.append(f"renamed from {data.get('displayName')}")
        if added:
            parts.append(f"{len(added)} added")
        if removed:
            parts.append(f"{len(removed)} removed")
        if changed:
            parts.append(f"roles or teams changed for {changed}")
        if skipped:
            parts.append(f"{skipped} unknown member id(s) skipped")
        _log(db, ctx, "scim_group_updated", f"Group {name}: {', '.join(parts)}.", detail={"group_id": str(link.record_id)})
    await db.flush()


async def replace_group(db: AsyncSession, ctx: Ctx, group_id: str, body: dict[str, Any]) -> dict[str, Any]:
    link = await _require_group(db, ctx, group_id, lock=True)
    resource = scim.clean_group(body)
    resource.setdefault("members", [])
    await _write_group(db, ctx, link, resource, True)
    return await group_resource(db, link, with_names=True)


async def patch_group(db: AsyncSession, ctx: Ctx, group_id: str, body: dict[str, Any]) -> None:
    link = await _require_group(db, ctx, group_id, lock=True)
    data = link.data or {}
    current = {"displayName": data.get("displayName") or "", "members": list(data.get("members") or [])}
    if data.get("externalId"):
        current["externalId"] = data["externalId"]
    resource = scim.apply_patch(current, body, max_bytes=None)
    await _write_group(db, ctx, link, resource, resource.get("members") != current["members"])


async def delete_group(db: AsyncSession, ctx: Ctx, group_id: str) -> None:
    link = await _require_group(db, ctx, group_id, lock=True)
    data = link.data or {}
    members = [u for u in (_uuid(m) for m in data.get("members") or []) if u]
    name = data.get("displayName") or ""
    await db.delete(link)
    await db.flush()
    changed = await recompute(db, ctx, members) if members else 0
    _audit(db, ctx, "scim_group_delete", "scim_group", link.record_id, name, {"members": len(members)})
    _log(db, ctx, "scim_group_deleted", f"Group {name} deleted" + (f"; roles or teams changed for {changed}." if changed else "."), "warning", {"group_id": str(link.record_id)})
    await db.flush()


async def _drop_from_groups(db: AsyncSession, ctx: Ctx, user_ids: set[str]) -> None:
    for group in await _groups(db, ctx):
        members = list((group.data or {}).get("members") or [])
        kept = [m for m in members if m not in user_ids]
        if len(kept) != len(members):
            group.data = {**(group.data or {}), "members": kept}


# ---------------------------------------------------------------------------
# Group mapping → roles and teams
# ---------------------------------------------------------------------------


async def _auto_team(db: AsyncSession, ctx: Ctx, group: IntegrationRecordLink, existing: dict[str, uuid.UUID]) -> uuid.UUID:
    data = dict(group.data or {})
    known = _uuid(data.get("auto_team_id"))
    if known and str(known) in {str(v) for v in existing.values()}:
        return known
    name = str(data.get("displayName") or "Team")[:120].strip() or "Team"
    team_id = existing.get(name.lower())
    if team_id is None:
        team = Team(id=uuid.uuid4(), organization_id=ctx.org, name=name, description=f"Created from the {ctx.label} group {name}.")
        db.add(team)
        await db.flush()
        team_id = team.id
        existing[name.lower()] = team_id
        _log(db, ctx, "scim_team_created", f"Created the team {name} for the {ctx.label} group.", "success")
    group.data = {**data, "auto_team_id": str(team_id)}
    return team_id


async def recompute(db: AsyncSession, ctx: Ctx, user_ids: Iterable[uuid.UUID] | None = None) -> int:
    """Give people the role and teams their groups map to. Returns how many people changed.

    * The first mapping (in the admin's order) with a role decides the role. Owners are never changed.
    * People who leave every role-mapped group go back to the default role, but only if their
      current role came from this mapping (a role an admin set by hand is kept).
    * Teams from mapped groups (and automatic teams) are added; teams that provisioning added
      earlier are removed when the person leaves the group. The primary team follows when it
      was set by provisioning (or wasn't set).
    """

    default_role, mappings, auto_teams = mapping_settings(ctx.settings)
    scope = None if user_ids is None else {u for u in user_ids}
    if scope is not None and not scope:
        return 0
    groups = await _groups(db, ctx)
    roles = await valid_roles(db, ctx.org)
    team_rows = (await db.execute(select(Team.id, Team.name).where(Team.organization_id == ctx.org))).all()
    team_ids = {str(t) for t, _ in team_rows}
    by_name = {n.lower(): t for t, n in team_rows}

    groups_of: dict[str, list[IntegrationRecordLink]] = defaultdict(list)
    for group in groups:
        for member in (group.data or {}).get("members") or []:
            if scope is None or _uuid(member) in scope:
                groups_of[member].append(group)

    query = select(OrganizationMembership).where(OrganizationMembership.organization_id == ctx.org)
    if scope is not None:
        query = query.where(OrganizationMembership.user_id.in_(list(scope)))
    else:
        linked = select(IntegrationRecordLink.record_id).where(
            IntegrationRecordLink.organization_id == ctx.org, IntegrationRecordLink.provider == ctx.key,
            IntegrationRecordLink.record_type == USER[0], IntegrationRecordLink.external_type == USER[1],
        )
        query = query.where(OrganizationMembership.user_id.in_(linked))
    memberships = list((await db.execute(query)).scalars())
    if not memberships:
        return 0
    links = await _links_for(db, ctx, [m.user_id for m in memberships])
    existing_team_rows = (
        await db.execute(select(TeamMember).where(TeamMember.organization_id == ctx.org, TeamMember.user_id.in_([m.user_id for m in memberships])))
    ).scalars().all()
    team_members: dict[uuid.UUID, dict[str, TeamMember]] = defaultdict(dict)
    for row in existing_team_rows:
        team_members[row.user_id][str(row.team_id)] = row

    changed = 0
    for membership in memberships:
        uid = str(membership.user_id)
        groups_here = groups_of.get(uid, [])
        link = links.get(membership.user_id)
        if link is None:
            if not groups_here:
                continue
            user = await db.get(User, membership.user_id)
            if user is None:
                continue
            link = await _save_link(db, ctx, user, None, _derived_resource(user))
        data = dict(link.data or {})
        matched = [m for m in mappings if any(_matches(m, g) for g in groups_here)]
        mapped_role = next((m["role"] for m in matched if m.get("role") in roles), None)
        updates: dict[str, Any] = {}

        if membership.role != "owner":
            target = mapped_role
            if target is None and data.get("role_from_scim") and membership.role == data["role_from_scim"]:
                target = default_role if default_role in roles else None
            if target and target != membership.role:
                updates["role"] = {"from": membership.role, "to": target}
                membership.role = target
            if mapped_role or target:
                data["role_from_scim"] = membership.role

        wanted: list[str] = []
        for mapping in matched:
            team = mapping.get("team_id")
            if team and team in team_ids and team not in wanted:
                wanted.append(team)
        if auto_teams:
            for group in groups_here:
                if not any(_matches(m, group) and m.get("team_id") for m in mappings):
                    team = str(await _auto_team(db, ctx, group, by_name))
                    team_ids.add(team)
                    if team not in wanted:
                        wanted.append(team)
        before = set(data.get("teams_from_scim") or [])
        mine = team_members[membership.user_id]
        for team in wanted:
            if team not in mine:
                row = TeamMember(id=uuid.uuid4(), organization_id=ctx.org, team_id=uuid.UUID(team), user_id=membership.user_id, role="member")
                db.add(row)
                mine[team] = row
                updates.setdefault("teams_added", []).append(team)
        for team in before - set(wanted):
            row = mine.pop(team, None)
            if row is not None:
                await db.delete(row)
                updates.setdefault("teams_removed", []).append(team)
        data["teams_from_scim"] = wanted
        primary_from_scim = data.get("primary_team_from_scim")
        current_primary = str(membership.team_id) if membership.team_id else None
        if current_primary is not None and current_primary != primary_from_scim:
            data.pop("primary_team_from_scim", None)  # an admin chose this team; provisioning leaves it alone
        elif wanted:
            if current_primary != wanted[0]:
                membership.team_id = uuid.UUID(wanted[0])
                updates["primary_team"] = {"from": current_primary, "to": wanted[0]}
            data["primary_team_from_scim"] = wanted[0]
        elif primary_from_scim:
            membership.team_id = None
            data.pop("primary_team_from_scim", None)
            updates["primary_team"] = {"from": current_primary, "to": None}
        link.data = data
        if updates:
            changed += 1
            record_audit(
                db, organization_id=ctx.org, action="scim_mapping", entity_type="user", entity_id=membership.user_id,
                changes={**updates, "source": f"{ctx.label} group mapping"}, actor_user_id=None, ip=ctx.ip,
            )
    await db.flush()
    return changed


# ---------------------------------------------------------------------------
# Overview for the integration page
# ---------------------------------------------------------------------------


async def stats(db: AsyncSession, organization_id: uuid.UUID, key: str) -> dict[str, Any]:
    link_user = and_(
        IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.provider == key,
        IntegrationRecordLink.record_type == USER[0], IntegrationRecordLink.external_type == USER[1],
    )
    rows = (
        await db.execute(
            select(OrganizationMembership.status, func.count())
            .join(IntegrationRecordLink, and_(link_user, IntegrationRecordLink.record_id == OrganizationMembership.user_id))
            .where(OrganizationMembership.organization_id == organization_id)
            .group_by(OrganizationMembership.status)
        )
    ).all()
    by_status = {status: count for status, count in rows}
    groups = (
        await db.execute(
            select(func.count()).select_from(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.provider == key, *_group_base()
            )
        )
    ).scalar_one()
    since = utcnow() - timedelta(days=30)
    events = (
        await db.execute(
            select(IntegrationLog.action, func.count()).where(
                IntegrationLog.organization_id == organization_id, IntegrationLog.provider == key,
                IntegrationLog.action.like("scim_%"), IntegrationLog.created_at >= since,
            ).group_by(IntegrationLog.action)
        )
    ).all()
    last = (
        await db.execute(
            select(func.max(IntegrationLog.created_at)).where(
                IntegrationLog.organization_id == organization_id, IntegrationLog.provider == key, IntegrationLog.action.like("scim_%")
            )
        )
    ).scalar_one()
    return {
        "users": sum(by_status.values()),
        "active": by_status.get("active", 0),
        "invited": by_status.get("invited", 0),
        "deactivated": by_status.get("deactivated", 0),
        "groups": groups,
        "last_30_days": {action: count for action, count in events},
        "last_event_at": last.isoformat() if last else None,
    }


async def groups_overview(db: AsyncSession, organization_id: uuid.UUID, key: str, limit: int = 500) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(IntegrationRecordLink)
            .where(IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.provider == key, *_group_base())
            .order_by(IntegrationRecordLink.data["displayName_lc"].astext)
            .limit(limit)
        )
    ).scalars()
    return [
        {
            "id": str(row.record_id),
            "name": (row.data or {}).get("displayName") or "",
            "external_id": (row.data or {}).get("externalId"),
            "members": len((row.data or {}).get("members") or []),
            "auto_team_id": (row.data or {}).get("auto_team_id"),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


async def provisioning_log(db: AsyncSession, organization_id: uuid.UUID, key: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(IntegrationLog)
            .where(IntegrationLog.organization_id == organization_id, IntegrationLog.provider == key, IntegrationLog.action.like("scim_%"))
            .order_by(IntegrationLog.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [{"id": str(r.id), "level": r.level, "action": r.action, "message": r.message, "at": r.created_at.isoformat() if r.created_at else None} for r in rows]
