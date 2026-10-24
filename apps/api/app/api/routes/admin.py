"""Administration: users, roles, permissions, teams, audit log and settings."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, client_ip, require_permission
from app.core.config import get_settings
from app.core.permissions import SYSTEM_ROLES, normalize_permissions, permission_catalog
from app.db.session import get_db
from app.models.editions import Subscription
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.platform import AuditLog, Role, Team, UserSession
from app.models.user import User
from app.services.auth import create_one_time_token, link, revoke_all_sessions, utcnow
from app.services.automation import record_audit

router = APIRouter(prefix="/admin", tags=["Administration"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _role_exists(db: AsyncSession, organization_id: uuid.UUID, key: str) -> bool:
    if key in SYSTEM_ROLES:
        return True
    return bool(
        (await db.execute(select(Role.id).where(Role.organization_id == organization_id, Role.key == key))).scalar_one_or_none()
    )


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


async def _membership(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
    membership = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found in this workspace.")
    return membership


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class InviteRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="member", max_length=60)
    team_id: uuid.UUID | None = None


class MemberUpdate(BaseModel):
    role: str | None = Field(default=None, max_length=60)
    team_id: uuid.UUID | None = None
    clear_team: bool = False
    status: str | None = Field(default=None, pattern="^(active|deactivated)$")
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=150)


@router.get("/users")
async def list_users(
    principal: Principal = Depends(require_permission("admin.users")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User, OrganizationMembership)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id == principal.organization_id)
        .order_by(User.full_name)
    )
    teams = {
        team.id: team.name
        for team in (await db.execute(select(Team).where(Team.organization_id == principal.organization_id))).scalars()
    }
    active_sessions = dict(
        (
            await db.execute(
                select(UserSession.user_id, func.count())
                .where(UserSession.revoked_at.is_(None), UserSession.expires_at > utcnow())
                .group_by(UserSession.user_id)
            )
        ).all()
    )
    return [
        {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "job_title": user.job_title,
            "role": membership.role,
            "team_id": str(membership.team_id) if membership.team_id else None,
            "team_name": teams.get(membership.team_id),
            "status": membership.status,
            "mfa_enabled": user.mfa_enabled,
            "last_login_at": user.last_login_at,
            "locked": bool(user.locked_until and user.locked_until > utcnow()),
            "active_sessions": active_sessions.get(user.id, 0),
            "joined_at": membership.created_at,
            "is_you": user.id == principal.user_id,
        }
        for user, membership in result.all()
    ]


@router.post("/users/invite", status_code=status.HTTP_201_CREATED)
async def invite_user(
    payload: InviteRequest,
    principal: Principal = Depends(require_permission("admin.users")),
    db: AsyncSession = Depends(get_db),
):
    if not await _role_exists(db, principal.organization_id, payload.role):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an existing role.")
    if payload.role == "owner" and principal.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can invite another owner.")

    email = payload.email.strip().lower()
    from app.services import workspace_settings

    security = await workspace_settings.get(db, principal.organization_id, "security")
    domain = email.split("@")[-1]
    if security.allowed_email_domains and domain not in security.allowed_email_domains:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"This workspace only invites people with {', '.join('@' + d for d in security.allowed_email_domains)} addresses (Settings → Security).",
        )
    licenses = await workspace_settings.get(db, principal.organization_id, "licenses")
    if not licenses.auto_add_seats:
        subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == principal.organization_id))).scalar_one_or_none()
        taken = (
            await db.execute(
                select(func.count()).select_from(OrganizationMembership).where(
                    OrganizationMembership.organization_id == principal.organization_id,
                    OrganizationMembership.status.in_(("active", "invited")),
                )
            )
        ).scalar_one()
        if subscription is not None and subscription.plan != "trial" and subscription.seats and taken >= subscription.seats:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"All {subscription.seats} seats are taken. Add seats in Plan overview, or turn on automatic seats in License settings.",
            )

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(id=uuid.uuid4(), email=email, full_name=payload.full_name.strip(), password_hash=None, is_active=True)
        db.add(user)
        await db.flush()

    existing = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == principal.organization_id,
                OrganizationMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"{email} is already a member of this workspace.")

    if existing is None:
        existing = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=principal.organization_id,
            user_id=user.id,
            role=payload.role,
            status="invited",
            team_id=payload.team_id,
            invited_by_user_id=principal.user_id,
        )
        db.add(existing)
    else:
        existing.role = payload.role
        existing.status = "invited"
        existing.team_id = payload.team_id

    token = await create_one_time_token(
        db,
        user_id=user.id,
        purpose="invite",
        hours=24 * 7,
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
    )
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="invite",
        entity_type="user",
        entity_id=user.id,
        entity_label=user.full_name,
        changes={"email": email, "role": payload.role},
    )
    await db.commit()

    response = {"message": f"Invitation created for {email}. It expires in 7 days.", "user_id": str(user.id)}
    if get_settings().expose_dev_links:
        response["invite_url"] = link("/accept-invite", token)
    return response


@router.patch("/users/{user_id}")
async def update_member(
    user_id: uuid.UUID,
    payload: MemberUpdate,
    request: Request,
    principal: Principal = Depends(require_permission("admin.users")),
    db: AsyncSession = Depends(get_db),
):
    membership = await _membership(db, principal.organization_id, user_id)
    user = await db.get(User, user_id)
    changes: dict = {}

    if payload.role is not None and payload.role != membership.role:
        if not await _role_exists(db, principal.organization_id, payload.role):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an existing role.")
        if "owner" in {payload.role, membership.role} and principal.role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can grant or remove the owner role.")
        if membership.role == "owner" and await _owner_count(db, principal.organization_id) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "A workspace needs at least one owner.")
        changes["role"] = {"from": membership.role, "to": payload.role}
        membership.role = payload.role

    if payload.clear_team:
        changes["team"] = {"from": str(membership.team_id) if membership.team_id else None, "to": None}
        membership.team_id = None
    elif payload.team_id is not None:
        team = await db.get(Team, payload.team_id)
        if team is None or team.organization_id != principal.organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a team in this workspace.")
        changes["team"] = {"from": str(membership.team_id) if membership.team_id else None, "to": team.name}
        membership.team_id = team.id

    if payload.status == "active" and (user.locked_until is not None or user.failed_login_count):
        # Re-activating also clears a lockout from repeated failed sign-ins.
        user.locked_until = None
        user.failed_login_count = 0
        changes["lockout"] = {"from": "locked", "to": "unlocked"}

    if payload.status is not None and payload.status != membership.status:
        if user_id == principal.user_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "You can't deactivate yourself.")
        if membership.role == "owner" and payload.status == "deactivated" and await _owner_count(db, principal.organization_id) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "A workspace needs at least one active owner.")
        changes["status"] = {"from": membership.status, "to": payload.status}
        membership.status = payload.status
        if payload.status == "deactivated":
            await revoke_all_sessions(db, user_id)

    if payload.full_name:
        user.full_name = payload.full_name.strip()
    if payload.job_title is not None:
        user.job_title = payload.job_title.strip() or None

    if changes:
        record_audit(
            db,
            organization_id=principal.organization_id,
            action="member_update",
            entity_type="user",
            entity_id=user_id,
            entity_label=user.full_name,
            changes=changes,
            ip=client_ip(request),
        )
    await db.commit()
    return {"message": "User updated."}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.users")),
    db: AsyncSession = Depends(get_db),
):
    if user_id == principal.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove yourself.")
    membership = await _membership(db, principal.organization_id, user_id)
    if membership.role == "owner" and await _owner_count(db, principal.organization_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A workspace needs at least one owner.")
    user = await db.get(User, user_id)
    await db.delete(membership)
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="member_removed",
        entity_type="user",
        entity_id=user_id,
        entity_label=user.full_name if user else None,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/reset-password")
async def send_password_reset(
    user_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.users")),
    db: AsyncSession = Depends(get_db),
):
    await _membership(db, principal.organization_id, user_id)
    user = await db.get(User, user_id)
    token = await create_one_time_token(
        db, user_id=user_id, purpose="password_reset", hours=24, created_by_user_id=principal.user_id
    )
    user.locked_until = None
    user.failed_login_count = 0
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="password_reset_sent",
        entity_type="user",
        entity_id=user_id,
        entity_label=user.full_name,
    )
    await db.commit()
    response = {"message": f"Password reset link created for {user.email}. It expires in 24 hours."}
    if get_settings().expose_dev_links:
        response["reset_url"] = link("/reset-password", token)
    return response


# ---------------------------------------------------------------------------
# Roles & permissions
# ---------------------------------------------------------------------------


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] = Field(default_factory=list)
    copy_from: str | None = None


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] | None = None


@router.get("/permissions")
async def list_permissions(_: Principal = Depends(require_permission("admin.roles"))):
    return permission_catalog()


@router.get("/roles")
async def list_roles(
    principal: Principal = Depends(require_permission("admin.users")),
    db: AsyncSession = Depends(get_db),
):
    counts = dict(
        (
            await db.execute(
                select(OrganizationMembership.role, func.count())
                .where(OrganizationMembership.organization_id == principal.organization_id)
                .group_by(OrganizationMembership.role)
            )
        ).all()
    )
    roles = [
        {
            "key": role.key,
            "name": role.name,
            "description": role.description,
            "permissions": sorted(role.permissions),
            "is_system": True,
            "member_count": counts.get(role.key, 0),
        }
        for role in SYSTEM_ROLES.values()
    ]
    custom = (
        await db.execute(select(Role).where(Role.organization_id == principal.organization_id).order_by(Role.name))
    ).scalars()
    roles += [
        {
            "key": role.key,
            "id": str(role.id),
            "name": role.name,
            "description": role.description,
            "permissions": sorted(role.permissions or []),
            "is_system": False,
            "member_count": counts.get(role.key, 0),
        }
        for role in custom
    ]
    return roles


@router.post("/roles", status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    principal: Principal = Depends(require_permission("admin.roles")),
    db: AsyncSession = Depends(get_db),
):
    key = re.sub(r"[^a-z0-9]+", "_", payload.name.lower()).strip("_")[:50] or "role"
    if key in SYSTEM_ROLES or await _role_exists(db, principal.organization_id, key):
        key = f"{key}_{uuid.uuid4().hex[:4]}"
    permissions = payload.permissions
    if payload.copy_from:
        source = SYSTEM_ROLES.get(payload.copy_from)
        if source is not None:
            permissions = sorted(source.permissions)
        else:
            role = (
                await db.execute(
                    select(Role).where(Role.organization_id == principal.organization_id, Role.key == payload.copy_from)
                )
            ).scalar_one_or_none()
            permissions = list(role.permissions or []) if role else permissions
    role = Role(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        key=key,
        name=payload.name.strip(),
        description=payload.description,
        permissions=normalize_permissions(permissions),
    )
    db.add(role)
    await db.commit()
    return {"key": role.key, "id": str(role.id)}


@router.patch("/roles/{key}")
async def update_role(
    key: str,
    payload: RoleUpdate,
    principal: Principal = Depends(require_permission("admin.roles")),
    db: AsyncSession = Depends(get_db),
):
    if key in SYSTEM_ROLES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in roles can't be edited. Duplicate it to make a custom role.")
    role = (
        await db.execute(select(Role).where(Role.organization_id == principal.organization_id, Role.key == key))
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found.")
    if payload.name is not None:
        role.name = payload.name.strip()
    if payload.description is not None:
        role.description = payload.description
    if payload.permissions is not None:
        role.permissions = normalize_permissions(payload.permissions)
    await db.commit()
    return {"message": "Role saved."}


@router.delete("/roles/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    key: str,
    principal: Principal = Depends(require_permission("admin.roles")),
    db: AsyncSession = Depends(get_db),
):
    if key in SYSTEM_ROLES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in roles can't be deleted.")
    role = (
        await db.execute(select(Role).where(Role.organization_id == principal.organization_id, Role.key == key))
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found.")
    in_use = (
        await db.execute(
            select(func.count()).select_from(OrganizationMembership).where(
                OrganizationMembership.organization_id == principal.organization_id,
                OrganizationMembership.role == key,
            )
        )
    ).scalar_one()
    if in_use:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{in_use} user(s) still have this role. Move them to another role first.")
    await db.delete(role)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


class TeamPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    manager_user_id: uuid.UUID | None = None


@router.get("/teams")
async def list_teams(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    teams = (
        await db.execute(select(Team).where(Team.organization_id == principal.organization_id).order_by(Team.name))
    ).scalars().all()
    members = (
        await db.execute(
            select(OrganizationMembership.team_id, User.id, User.full_name)
            .join(User, User.id == OrganizationMembership.user_id)
            .where(
                OrganizationMembership.organization_id == principal.organization_id,
                OrganizationMembership.team_id.is_not(None),
            )
        )
    ).all()
    names = {user_id: name for _, user_id, name in members}
    return [
        {
            "id": str(team.id),
            "name": team.name,
            "description": team.description,
            "manager_user_id": str(team.manager_user_id) if team.manager_user_id else None,
            "manager_name": names.get(team.manager_user_id) if team.manager_user_id else None,
            "members": [{"id": str(user_id), "full_name": name} for team_id, user_id, name in members if team_id == team.id],
        }
        for team in teams
    ]


@router.post("/teams", status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamPayload,
    principal: Principal = Depends(require_permission("admin.teams")),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            select(Team.id).where(Team.organization_id == principal.organization_id, func.lower(Team.name) == payload.name.strip().lower())
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "A team with this name already exists.")
    team = Team(id=uuid.uuid4(), organization_id=principal.organization_id, **payload.model_dump())
    team.name = team.name.strip()
    db.add(team)
    await db.commit()
    return {"id": str(team.id)}


@router.patch("/teams/{team_id}")
async def update_team(
    team_id: uuid.UUID,
    payload: TeamPayload,
    principal: Principal = Depends(require_permission("admin.teams")),
    db: AsyncSession = Depends(get_db),
):
    team = await db.get(Team, team_id)
    if team is None or team.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found.")
    team.name = payload.name.strip()
    team.description = payload.description
    team.manager_user_id = payload.manager_user_id
    await db.commit()
    return {"message": "Team saved."}


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    principal: Principal = Depends(require_permission("admin.teams")),
    db: AsyncSession = Depends(get_db),
):
    team = await db.get(Team, team_id)
    if team is None or team.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found.")
    await db.delete(team)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/audit-logs")
async def list_audit_logs(
    entity_type: str | None = None,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_permission("admin.audit")),
    db: AsyncSession = Depends(get_db),
):
    query = select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.actor_user_id).where(
        AuditLog.organization_id == principal.organization_id
    )
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if action:
        query = query.where(AuditLog.action == action)
    if actor_user_id:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if since:
        query = query.where(AuditLog.created_at >= since)
    if until:
        query = query.where(AuditLog.created_at <= until)
    if search:
        query = query.where(or_(AuditLog.entity_label.ilike(f"%{search}%"), User.full_name.ilike(f"%{search}%")))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(query.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    ).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(log.id),
                "created_at": log.created_at,
                "actor_user_id": str(log.actor_user_id) if log.actor_user_id else None,
                "actor_name": actor_name or "System",
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": str(log.entity_id) if log.entity_id else None,
                "entity_label": log.entity_label,
                "changes": log.changes,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
            }
            for log, actor_name in rows
        ],
    }


# ---------------------------------------------------------------------------
# Organization settings
# ---------------------------------------------------------------------------

ALLOWED_SETTINGS = {"currency", "timezone", "fiscal_year_start_month", "date_format", "session_timeout_minutes"}


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: dict | None = None


@router.get("/organization")
async def get_organization(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    member_count = (
        await db.execute(
            select(func.count()).select_from(OrganizationMembership).where(
                OrganizationMembership.organization_id == principal.organization_id,
                OrganizationMembership.status == "active",
            )
        )
    ).scalar_one()
    return {
        "id": str(organization.id),
        "name": organization.name,
        "slug": organization.slug,
        "settings": organization.settings or {},
        "created_at": organization.created_at,
        "member_count": member_count,
    }


@router.patch("/organization")
async def update_organization(
    payload: OrganizationUpdate,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    changes = {}
    if payload.name and payload.name.strip() != organization.name:
        changes["name"] = {"from": organization.name, "to": payload.name.strip()}
        organization.name = payload.name.strip()
    if payload.settings is not None:
        clean = {k: v for k, v in payload.settings.items() if k in ALLOWED_SETTINGS}
        if "fiscal_year_start_month" in clean:
            month = int(clean["fiscal_year_start_month"])
            if not 1 <= month <= 12:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Fiscal year must start in month 1–12.")
            clean["fiscal_year_start_month"] = month
        if "session_timeout_minutes" in clean:
            try:
                minutes = int(clean["session_timeout_minutes"])
            except (TypeError, ValueError):
                minutes = 0
            if not 5 <= minutes <= 240:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Session length must be between 5 and 240 minutes.")
            clean["session_timeout_minutes"] = minutes
        if "currency" in clean and not re.fullmatch(r"[A-Z]{3}", str(clean["currency"])):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use a 3-letter currency code like USD.")
        merged = {**(organization.settings or {}), **clean}
        changes["settings"] = {"from": organization.settings, "to": merged}
        organization.settings = merged
    if changes:
        record_audit(
            db,
            organization_id=principal.organization_id,
            action="settings_update",
            entity_type="organization",
            entity_id=organization.id,
            entity_label=organization.name,
            changes=changes,
        )
    await db.commit()
    return {"message": "Settings saved."}
