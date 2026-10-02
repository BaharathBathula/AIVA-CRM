import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.schemas.opportunity import (
    OpportunityCreate,
    OpportunityUpdate,
)


async def validate_opportunity_owner(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    result = await db.execute(
        select(
            OrganizationMembership.id
        ).where(
            OrganizationMembership.organization_id
            == organization_id,
            OrganizationMembership.user_id
            == user_id,
        )
    )

    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Opportunity owner must belong "
                "to the organization."
            ),
        )


async def get_valid_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Account:
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.organization_id
            == organization_id,
        )
    )

    account = result.scalar_one_or_none()

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Opportunity account does not "
                "belong to this organization."
            ),
        )

    return account


async def get_valid_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Contact:
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.organization_id
            == organization_id,
        )
    )

    contact = result.scalar_one_or_none()

    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Primary contact does not "
                "belong to this organization."
            ),
        )

    if contact.account_id != account_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Primary contact must belong "
                "to the opportunity account."
            ),
        )

    return contact


async def get_valid_pipeline(
    db: AsyncSession,
    organization_id: uuid.UUID,
    pipeline_id: uuid.UUID,
) -> Pipeline:
    result = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id,
            Pipeline.organization_id
            == organization_id,
        )
    )

    pipeline = result.scalar_one_or_none()

    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Pipeline does not belong "
                "to this organization."
            ),
        )

    if not pipeline.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline is inactive.",
        )

    return pipeline


async def get_valid_stage(
    db: AsyncSession,
    organization_id: uuid.UUID,
    stage_id: uuid.UUID,
) -> PipelineStage:
    result = await db.execute(
        select(PipelineStage).where(
            PipelineStage.id == stage_id,
            PipelineStage.organization_id
            == organization_id,
        )
    )

    stage = result.scalar_one_or_none()

    if stage is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Pipeline stage does not belong "
                "to this organization."
            ),
        )

    if not stage.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline stage is inactive.",
        )

    return stage


async def get_default_pipeline(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> Pipeline:
    result = await db.execute(
        select(Pipeline)
        .where(
            Pipeline.organization_id
            == organization_id,
            Pipeline.is_default.is_(True),
            Pipeline.is_active.is_(True),
        )
        .order_by(
            Pipeline.created_at.asc()
        )
        .limit(1)
    )

    pipeline = result.scalar_one_or_none()

    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No active default sales "
                "pipeline is configured."
            ),
        )

    return pipeline


async def get_first_open_stage(
    db: AsyncSession,
    organization_id: uuid.UUID,
    pipeline_id: uuid.UUID,
) -> PipelineStage:
    result = await db.execute(
        select(PipelineStage)
        .where(
            PipelineStage.organization_id
            == organization_id,
            PipelineStage.pipeline_id
            == pipeline_id,
            PipelineStage.category
            == "open",
            PipelineStage.is_active.is_(True),
        )
        .order_by(
            PipelineStage.position.asc()
        )
        .limit(1)
    )

    stage = result.scalar_one_or_none()

    if stage is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Pipeline has no active "
                "open stage."
            ),
        )

    return stage


async def resolve_pipeline_and_stage(
    db: AsyncSession,
    organization_id: uuid.UUID,
    pipeline_id: uuid.UUID | None,
    stage_id: uuid.UUID | None,
) -> tuple[Pipeline, PipelineStage]:
    if (
        pipeline_id is None
        and stage_id is None
    ):
        pipeline = await get_default_pipeline(
            db,
            organization_id,
        )

        stage = await get_first_open_stage(
            db,
            organization_id,
            pipeline.id,
        )

        return pipeline, stage

    if (
        pipeline_id is not None
        and stage_id is None
    ):
        pipeline = await get_valid_pipeline(
            db,
            organization_id,
            pipeline_id,
        )

        stage = await get_first_open_stage(
            db,
            organization_id,
            pipeline.id,
        )

        return pipeline, stage

    if (
        pipeline_id is None
        and stage_id is not None
    ):
        stage = await get_valid_stage(
            db,
            organization_id,
            stage_id,
        )

        pipeline = await get_valid_pipeline(
            db,
            organization_id,
            stage.pipeline_id,
        )

        return pipeline, stage

    pipeline = await get_valid_pipeline(
        db,
        organization_id,
        pipeline_id,
    )

    stage = await get_valid_stage(
        db,
        organization_id,
        stage_id,
    )

    if stage.pipeline_id != pipeline.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Pipeline stage does not "
                "belong to the selected pipeline."
            ),
        )

    return pipeline, stage


def normalize_currency(
    value: str,
) -> str:
    normalized = value.strip().upper()

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "Currency must be a valid "
                "3-letter code."
            ),
        )

    return normalized


def apply_stage_state(
    opportunity: Opportunity,
    stage: PipelineStage,
) -> None:
    opportunity.stage_id = stage.id
    opportunity.pipeline_id = (
        stage.pipeline_id
    )

    opportunity.probability = (
        stage.probability
    )

    if stage.category in {
        "won",
        "lost",
    }:
        opportunity.closed_at = (
            datetime.now(timezone.utc)
        )
    else:
        opportunity.closed_at = None


async def create_opportunity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: OpportunityCreate,
) -> Opportunity:
    await get_valid_account(
        db,
        organization_id,
        payload.account_id,
    )

    if payload.owner_user_id is not None:
        await validate_opportunity_owner(
            db,
            organization_id,
            payload.owner_user_id,
        )

    pipeline, stage = (
        await resolve_pipeline_and_stage(
            db,
            organization_id,
            payload.pipeline_id,
            payload.stage_id,
        )
    )

    if payload.primary_contact_id is not None:
        await get_valid_contact(
            db,
            organization_id,
            payload.primary_contact_id,
            payload.account_id,
        )

    currency = normalize_currency(
        payload.currency
    )

    opportunity = Opportunity(
        organization_id=organization_id,
        name=payload.name.strip(),
        account_id=payload.account_id,
        primary_contact_id=(
            payload.primary_contact_id
        ),
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        owner_user_id=(
            payload.owner_user_id
        ),
        amount=payload.amount,
        currency=currency,
        probability=(
            payload.probability
            if payload.probability
            is not None
            else stage.probability
        ),
        expected_close_date=(
            payload.expected_close_date
        ),
        description=payload.description,
    )

    if stage.category in {
        "won",
        "lost",
    }:
        opportunity.closed_at = (
            datetime.now(timezone.utc)
        )

    db.add(opportunity)

    await db.commit()
    await db.refresh(opportunity)

    return opportunity


async def list_opportunities(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    pipeline_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None,
    owner_user_id: uuid.UUID | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Opportunity]:
    statement = select(
        Opportunity
    ).where(
        Opportunity.organization_id
        == organization_id
    )

    if account_id is not None:
        statement = statement.where(
            Opportunity.account_id
            == account_id
        )

    if pipeline_id is not None:
        statement = statement.where(
            Opportunity.pipeline_id
            == pipeline_id
        )

    if stage_id is not None:
        statement = statement.where(
            Opportunity.stage_id
            == stage_id
        )

    if owner_user_id is not None:
        statement = statement.where(
            Opportunity.owner_user_id
            == owner_user_id
        )

    if search:
        search_term = (
            f"%{search.strip()}%"
        )

        statement = statement.where(
            or_(
                Opportunity.name.ilike(
                    search_term
                ),
                Opportunity.description.ilike(
                    search_term
                ),
            )
        )

    statement = (
        statement
        .order_by(
            Opportunity.created_at.desc()
        )
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(
        statement
    )

    return list(
        result.scalars().all()
    )


async def get_opportunity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> Opportunity:
    result = await db.execute(
        select(Opportunity).where(
            Opportunity.id
            == opportunity_id,
            Opportunity.organization_id
            == organization_id,
        )
    )

    opportunity = (
        result.scalar_one_or_none()
    )

    if opportunity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found.",
        )

    return opportunity


async def update_opportunity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    payload: OpportunityUpdate,
) -> Opportunity:
    opportunity = await get_opportunity(
        db,
        organization_id,
        opportunity_id,
    )

    updates = payload.model_dump(
        exclude_unset=True
    )

    if (
        "name" in updates
        and updates["name"] is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "Opportunity name cannot "
                "be null."
            ),
        )

    if (
        "account_id" in updates
        and updates["account_id"] is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "Opportunity account cannot "
                "be null."
            ),
        )

    if (
        "pipeline_id" in updates
        and updates["pipeline_id"] is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "Opportunity pipeline cannot "
                "be null."
            ),
        )

    if (
        "stage_id" in updates
        and updates["stage_id"] is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "Opportunity stage cannot "
                "be null."
            ),
        )

    new_account_id = updates.get(
        "account_id",
        opportunity.account_id,
    )

    if "account_id" in updates:
        await get_valid_account(
            db,
            organization_id,
            new_account_id,
        )

    if "owner_user_id" in updates:
        owner_user_id = updates[
            "owner_user_id"
        ]

        if owner_user_id is not None:
            await validate_opportunity_owner(
                db,
                organization_id,
                owner_user_id,
            )

    pipeline_changed = (
        "pipeline_id" in updates
    )

    stage_changed = (
        "stage_id" in updates
    )

    selected_stage = None

    if pipeline_changed or stage_changed:
        requested_pipeline_id = (
            updates.get(
                "pipeline_id"
            )
            if pipeline_changed
            else None
        )

        requested_stage_id = (
            updates.get(
                "stage_id"
            )
            if stage_changed
            else None
        )

        if (
            pipeline_changed
            and not stage_changed
        ):
            pipeline = (
                await get_valid_pipeline(
                    db,
                    organization_id,
                    requested_pipeline_id,
                )
            )

            selected_stage = (
                await get_first_open_stage(
                    db,
                    organization_id,
                    pipeline.id,
                )
            )

        elif (
            stage_changed
            and not pipeline_changed
        ):
            selected_stage = (
                await get_valid_stage(
                    db,
                    organization_id,
                    requested_stage_id,
                )
            )

            await get_valid_pipeline(
                db,
                organization_id,
                selected_stage.pipeline_id,
            )

        else:
            pipeline, selected_stage = (
                await resolve_pipeline_and_stage(
                    db,
                    organization_id,
                    requested_pipeline_id,
                    requested_stage_id,
                )
            )

    contact_id = (
        updates.get(
            "primary_contact_id"
        )
        if "primary_contact_id"
        in updates
        else opportunity.primary_contact_id
    )

    if contact_id is not None:
        await get_valid_contact(
            db,
            organization_id,
            contact_id,
            new_account_id,
        )

    if "currency" in updates:
        currency = updates["currency"]

        if currency is None:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    "Currency cannot be null."
                ),
            )

        updates["currency"] = (
            normalize_currency(
                currency
            )
        )

    if (
        "name" in updates
        and updates["name"] is not None
    ):
        updates["name"] = (
            updates["name"].strip()
        )

    fields_to_skip = {
        "pipeline_id",
        "stage_id",
    }

    for field, value in updates.items():
        if field in fields_to_skip:
            continue

        setattr(
            opportunity,
            field,
            value,
        )

    if selected_stage is not None:
        apply_stage_state(
            opportunity,
            selected_stage,
        )

    elif (
        "probability" in updates
        and updates["probability"]
        is not None
    ):
        opportunity.probability = (
            updates["probability"]
        )

    await db.commit()
    await db.refresh(opportunity)

    return opportunity
