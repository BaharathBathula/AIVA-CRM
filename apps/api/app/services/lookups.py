"""Resolve display names for referenced records in one query per kind."""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.opportunity import Opportunity
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Team
from app.models.user import User


async def names_for(
    db: AsyncSession,
    organization_id: uuid.UUID,
    kind: str,
    ids: Iterable[uuid.UUID | str | None],
) -> dict[str, str]:
    keys = set()
    for value in ids:
        if value is None:
            continue
        try:
            keys.add(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
        except ValueError:
            continue
    if not keys:
        return {}

    if kind == "user":
        rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(keys)))
    elif kind == "account":
        rows = await db.execute(
            select(Account.id, Account.name).where(Account.id.in_(keys), Account.organization_id == organization_id)
        )
    elif kind == "contact":
        result = await db.execute(
            select(Contact.id, Contact.first_name, Contact.last_name).where(
                Contact.id.in_(keys), Contact.organization_id == organization_id
            )
        )
        return {str(i): f"{f} {l}".strip() for i, f, l in result.all()}
    elif kind == "lead":
        from app.models.lead import Lead

        result = await db.execute(
            select(Lead.id, Lead.first_name, Lead.last_name).where(Lead.id.in_(keys), Lead.organization_id == organization_id)
        )
        return {str(i): f"{f or ''} {l or ''}".strip() or "Unnamed lead" for i, f, l in result.all()}
    elif kind == "opportunity":
        rows = await db.execute(
            select(Opportunity.id, Opportunity.name).where(
                Opportunity.id.in_(keys), Opportunity.organization_id == organization_id
            )
        )
    elif kind == "stage":
        rows = await db.execute(
            select(PipelineStage.id, PipelineStage.name).where(
                PipelineStage.id.in_(keys), PipelineStage.organization_id == organization_id
            )
        )
    elif kind == "pipeline":
        rows = await db.execute(
            select(Pipeline.id, Pipeline.name).where(Pipeline.id.in_(keys), Pipeline.organization_id == organization_id)
        )
    elif kind == "team":
        rows = await db.execute(
            select(Team.id, Team.name).where(Team.id.in_(keys), Team.organization_id == organization_id)
        )
    else:
        return {}
    return {str(i): name for i, name in rows.all()}
