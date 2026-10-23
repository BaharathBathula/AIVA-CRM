"""Record visibility for teams and the reporting hierarchy (Enterprise).

A workspace chooses who can see records owned by someone else:

* ``all`` (default): everyone sees every lead, account, contact and deal;
* ``team``: people see their own records, their teams' records (including
  sub-teams of teams they lead) and unowned records;
* ``hierarchy``: people see their own records, those of everyone who reports
  to them (directly or indirectly) and unowned records.

Members whose role has "View all records" are never restricted. The filter
is applied to every ORM query in the request through ``with_loader_criteria``,
so lists, detail pages, search, reports and exports all agree.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.models.account import Account
from app.models.contact import Contact
from app.models.editions import TeamMember
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.platform import Team

MODES = ("all", "team", "hierarchy")
RESTRICTED_MODELS = (Lead, Account, Contact, Opportunity)


async def team_ids_for(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Teams the user belongs to, plus every sub-team of teams they manage or lead."""

    teams = (await db.execute(select(Team).where(Team.organization_id == organization_id))).scalars().all()
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for team in teams:
        if team.parent_team_id:
            children.setdefault(team.parent_team_id, []).append(team.id)
    primary = (
        await db.execute(
            select(OrganizationMembership.team_id).where(
                OrganizationMembership.organization_id == organization_id, OrganizationMembership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    member_rows = (
        await db.execute(select(TeamMember.team_id, TeamMember.role).where(TeamMember.organization_id == organization_id, TeamMember.user_id == user_id))
    ).all()
    mine = {team_id for team_id, _ in member_rows}
    if primary:
        mine.add(primary)
    leading = {team_id for team_id, role in member_rows if role == "lead"} | {t.id for t in teams if t.manager_user_id == user_id}
    stack = list(leading)
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in mine:
                mine.add(child)
                stack.append(child)
    return mine


async def team_member_ids(db: AsyncSession, organization_id: uuid.UUID, team_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not team_ids:
        return set()
    primary = (
        await db.execute(
            select(OrganizationMembership.user_id).where(
                OrganizationMembership.organization_id == organization_id, OrganizationMembership.team_id.in_(team_ids)
            )
        )
    ).scalars().all()
    extra = (await db.execute(select(TeamMember.user_id).where(TeamMember.team_id.in_(team_ids)))).scalars().all()
    managers = (await db.execute(select(Team.manager_user_id).where(Team.id.in_(team_ids), Team.manager_user_id.is_not(None)))).scalars().all()
    return set(primary) | set(extra) | set(managers)


async def subordinate_ids(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID) -> set[uuid.UUID]:
    rows = (
        await db.execute(
            select(OrganizationMembership.user_id, OrganizationMembership.reports_to_user_id).where(
                OrganizationMembership.organization_id == organization_id
            )
        )
    ).all()
    reports: dict[uuid.UUID, list[uuid.UUID]] = {}
    for member, manager in rows:
        if manager:
            reports.setdefault(manager, []).append(member)
    found: set[uuid.UUID] = set()
    stack = [user_id]
    while stack:
        current = stack.pop()
        for member in reports.get(current, []):
            if member not in found and member != user_id:
                found.add(member)
                stack.append(member)
    return found


async def visible_owner_ids(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    mode: str,
) -> set[uuid.UUID] | None:
    if mode == "team":
        teams = await team_ids_for(db, organization_id, user_id)
        return {user_id} | await team_member_ids(db, organization_id, teams)
    if mode == "hierarchy":
        return {user_id} | await subordinate_ids(db, organization_id, user_id)
    return None


def restrict_session(db: Any, owners: set[uuid.UUID] | None) -> None:
    session = db.sync_session if hasattr(db, "sync_session") else db
    if owners is None:
        session.info.pop("visible_owners", None)
    else:
        session.info["visible_owners"] = tuple(owners)


@event.listens_for(Session, "do_orm_execute")
def _apply_visibility(state: ORMExecuteState) -> None:
    owners = state.session.info.get("visible_owners")
    if owners is None or not state.is_select or state.execution_options.get("skip_visibility"):
        return
    options = [
        with_loader_criteria(
            model,
            or_(model.owner_user_id.in_(owners), model.owner_user_id.is_(None)),
            include_aliases=True,
        )
        for model in RESTRICTED_MODELS
    ]
    state.statement = state.statement.options(*options)
