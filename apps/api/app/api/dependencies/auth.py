"""Authentication and permission dependencies.

Every CRM route depends on :func:`get_current_principal`, which verifies the
bearer token, confirms an active membership in the organization named by the
``X-Organization-ID`` header, resolves the member's role permissions, and
binds the acting user to the database session for audit and automation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import get_current_organization_id
from app.core.config import get_settings
from app.core.context import bind_session_context
from app.core.crypto import sha256
from app.core.permissions import SYSTEM_ROLES
from app.core.plans import ALL_FEATURES, FEATURE_BY_KEY, PLANS
from app.core.security import AuthenticationError, decode_access_claims
from app.db.session import get_db
from app.models.editions import ApiKey, Subscription
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.platform import AuditLog, Role
from app.models.user import User
from app.services.billing import plan_state
from app.services.visibility import restrict_session, visible_owner_ids

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    email: str = ""
    full_name: str = ""
    team_id: uuid.UUID | None = None
    plan: str = "enterprise"
    features: frozenset[str] = ALL_FEATURES
    read_only: bool = False
    api_key_id: uuid.UUID | None = None

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def has(self, feature: str) -> bool:
        return feature in self.features


async def resolve_role_permissions(
    db: AsyncSession,
    organization_id: uuid.UUID,
    role_key: str,
) -> frozenset[str]:
    system = SYSTEM_ROLES.get(role_key)
    if system is not None:
        return system.permissions

    result = await db.execute(
        select(Role.permissions).where(
            Role.organization_id == organization_id,
            Role.key == role_key,
        )
    )
    permissions = result.scalar_one_or_none()
    return frozenset(permissions or [])


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


API_KEY_PREFIX = "aiva_sk_"

# Requests a workspace may still make after its trial ends or its plan is
# canceled: signing in and out, reading data, and choosing a plan.
READ_ONLY_ALLOWED = ("/auth/", "/billing", "/notifications")

# Detail pages whose views are audited in HIPAA mode.
VIEW_AUDITED = {
    "leads": "lead",
    "contacts": "contact",
    "accounts": "account",
    "opportunities": "opportunity",
    "cases": "case",
}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _api_path(request: Request) -> str:
    path = request.url.path
    marker = "/api/v1"
    return path[path.index(marker) + len(marker):] if marker in path else path


async def _api_key_user(db: AsyncSession, raw: str, organization_id: uuid.UUID) -> tuple[uuid.UUID, ApiKey]:
    key = (await db.execute(select(ApiKey).where(ApiKey.key_hash == sha256(raw)))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if key is None or key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
        raise _unauthorized("That API key isn't valid. Create a new key in Settings → API keys.")
    if key.organization_id != organization_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That API key belongs to a different workspace.")
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 60:
        key.last_used_at = now
    return key.created_by_user_id, key


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    organization_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    if credentials is None:
        raise _unauthorized("Sign in to continue.")

    settings = get_settings()
    api_key: ApiKey | None = None
    token = credentials.credentials
    if token.startswith(API_KEY_PREFIX):
        user_id, api_key = await _api_key_user(db, token, organization_id)
    else:
        try:
            user_id, bound_organization = decode_access_claims(token, settings.jwt_secret_key)
        except AuthenticationError as exc:
            raise _unauthorized("Your session expired. Sign in again.") from exc
        if bound_organization is not None and bound_organization != organization_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "You signed in with your organization's single sign-on, so this session only opens that workspace.",
            )

    result = await db.execute(
        select(User, OrganizationMembership, Organization, Subscription)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == User.id,
        )
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .outerjoin(Subscription, Subscription.organization_id == Organization.id)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            OrganizationMembership.organization_id == organization_id,
        )
    )
    row = result.first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this workspace.",
        )

    user, membership, organization, subscription = row
    if membership.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your access to this workspace is deactivated.",
        )

    method = request.method.upper()
    path = _api_path(request)
    writing = method not in {"GET", "HEAD", "OPTIONS"}

    if api_key is not None:
        if not principal_api_allowed(subscription):
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "API access isn't included in this workspace's plan.")
        if api_key.scope == "read" and writing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This API key is read-only.")

    state = plan_state(subscription)
    if state.read_only and writing and not path.startswith(READ_ONLY_ALLOWED):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Your free trial has ended, so this workspace is read-only. Choose a plan in Settings → Billing to keep working."
            if state.status == "expired"
            else "This workspace's plan was canceled, so it's read-only. Choose a plan in Settings → Billing to keep working.",
        )

    org_settings = organization.settings or {}
    hipaa = bool(org_settings.get("hipaa_mode")) and "hipaa" in state.features
    if hipaa and not user.mfa_enabled and api_key is None and not path.startswith(("/auth/", "/billing")):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This workspace requires two-step verification. Set it up in Settings → Security to continue.",
        )

    permissions = await resolve_role_permissions(db, organization_id, membership.role)

    visibility = org_settings.get("record_visibility", "all")
    needed_feature = {"team": "multi_team", "hierarchy": "reporting_hierarchy"}.get(visibility)
    if needed_feature and needed_feature in state.features and "records.view_all" not in permissions:
        restrict_session(db, await visible_owner_ids(db, organization_id, user.id, visibility))

    bind_session_context(
        db,
        actor_id=user.id,
        organization_id=organization_id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    # Workflow and lead scoring rules are evaluated inside flush events,
    # which cannot run queries, so load them for this organization now.
    from app.services.automation import load_automation_context

    await load_automation_context(db, organization_id, features=state.features)

    if hipaa and method == "GET":
        parts = [part for part in path.split("/") if part]
        if len(parts) == 2 and parts[0] in VIEW_AUDITED and UUID_RE.match(parts[1]):
            db.add(
                AuditLog(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    actor_user_id=user.id,
                    action="view",
                    entity_type=VIEW_AUDITED[parts[0]],
                    entity_id=uuid.UUID(parts[1]),
                    changes={},
                    ip_address=client_ip(request),
                    user_agent=(request.headers.get("user-agent") or "")[:300] or None,
                )
            )
            await db.commit()
    elif api_key is not None and db.is_modified(api_key):
        await db.commit()

    return Principal(
        user_id=user.id,
        organization_id=organization_id,
        role=membership.role,
        permissions=permissions,
        email=user.email,
        full_name=user.full_name,
        team_id=membership.team_id,
        plan=state.plan.key,
        features=state.features,
        read_only=state.read_only,
        api_key_id=api_key.id if api_key else None,
    )


def principal_api_allowed(subscription: Subscription | None) -> bool:
    return "api_access" in plan_state(subscription).features


def upgrade_message(feature: str) -> str:
    definition = FEATURE_BY_KEY.get(feature)
    if definition is None:
        return "Your plan doesn't include this feature. See Settings → Billing."
    plan = PLANS[definition.plan]
    return f"{definition.label} is part of the {plan.name} plan. Upgrade in Settings → Billing to use it."


def require_feature(*features: str):
    """Dependency factory: the workspace's plan must include every listed feature."""

    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        for feature in features:
            if not principal.has(feature):
                raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, upgrade_message(feature))
        return principal

    return dependency


def require_permission(*required: str):
    """Dependency factory: the member must hold every listed permission."""

    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        missing = [permission for permission in required if not principal.can(permission)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role doesn't allow this action. Ask an administrator for access.",
            )
        return principal

    return dependency


async def enforce_record_permissions(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Router-level guard for the core CRM record APIs."""

    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        needed = "records.read"
    elif method == "DELETE":
        needed = "records.delete"
    else:
        needed = "records.write"

    if not principal.can(needed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role doesn't allow this action. Ask an administrator for access.",
        )
    return principal
