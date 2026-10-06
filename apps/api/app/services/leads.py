import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.activities import (
    add_automated_activity,
)

from app.schemas.lead import (
    LeadConvertRequest,
    LeadConvertResponse,
    LeadCreate,
    LeadUpdate,
)


async def validate_lead_owner(
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
                "Lead owner must belong "
                "to the organization."
            ),
        )


async def create_lead(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: LeadCreate,
) -> Lead:
    if payload.owner_user_id is not None:
        await validate_lead_owner(
            db,
            organization_id,
            payload.owner_user_id,
        )

    lead = Lead(
        organization_id=organization_id,
        **payload.model_dump(),
    )

    db.add(lead)

    await db.commit()
    await db.refresh(lead)

    return lead


async def list_leads(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_status: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Lead]:
    statement = select(Lead).where(
        Lead.organization_id
        == organization_id
    )

    if lead_status:
        statement = statement.where(
            Lead.status == lead_status
        )

    if search:
        search_term = (
            f"%{search.strip()}%"
        )

        statement = statement.where(
            or_(
                Lead.first_name.ilike(
                    search_term
                ),
                Lead.last_name.ilike(
                    search_term
                ),
                Lead.email.ilike(
                    search_term
                ),
                Lead.company_name.ilike(
                    search_term
                ),
                Lead.job_title.ilike(
                    search_term
                ),
            )
        )

    statement = (
        statement
        .order_by(
            Lead.created_at.desc()
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


async def get_lead(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> Lead:
    result = await db.execute(
        select(Lead).where(
            Lead.id == lead_id,
            Lead.organization_id
            == organization_id,
        )
    )

    lead = result.scalar_one_or_none()

    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found.",
        )

    return lead


async def update_lead(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: LeadUpdate,
) -> Lead:
    lead = await get_lead(
        db,
        organization_id,
        lead_id,
    )

    if lead.status == "converted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Converted leads cannot "
                "be edited."
            ),
        )

    previous_status = (
        lead.status
    )

    updates = payload.model_dump(
        exclude_unset=True
    )

    for field in [
        "first_name",
        "last_name",
    ]:
        if (
            field in updates
            and updates[field] is None
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    f"{field.replace('_', ' ').title()} "
                    "cannot be null."
                ),
            )

    owner_user_id = updates.get(
        "owner_user_id"
    )

    if owner_user_id is not None:
        await validate_lead_owner(
            db,
            organization_id,
            owner_user_id,
        )

    for field, value in updates.items():
        setattr(
            lead,
            field,
            value,
        )

    if (
        lead.status == "qualified"
        and previous_status
        != "qualified"
    ):
        await add_automated_activity(
            db,
            organization_id,
            activity_type="system",
            subject=(
                "Lead qualified: "
                f"{lead.first_name} "
                f"{lead.last_name}"
            ),
            body=(
                "Lead status changed to "
                "Qualified."
            ),
            lead_id=lead.id,
            activity_metadata={
                "automated": True,
                "trigger": (
                    "lead_qualified"
                ),
                "lead_id": str(
                    lead.id
                ),
                "previous_status": (
                    previous_status
                ),
                "new_status": (
                    lead.status
                ),
                "score": (
                    lead.score
                ),
            },
        )

    await db.commit()
    await db.refresh(lead)

    return lead


async def convert_lead(
    db: AsyncSession,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: LeadConvertRequest,
) -> LeadConvertResponse:
    try:
        lead_result = await db.execute(
            select(Lead)
            .where(
                Lead.id == lead_id,
                Lead.organization_id
                == organization_id,
            )
            .with_for_update()
        )

        lead = (
            lead_result.scalar_one_or_none()
        )

        if lead is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found.",
            )

        if lead.status == "converted":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Lead has already been "
                    "converted."
                ),
            )

        if lead.status != "qualified":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Lead must be qualified "
                    "before conversion."
                ),
            )

        if (
            lead.company_name is None
            or not lead.company_name.strip()
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    "Company name is required "
                    "to convert this lead."
                ),
            )

        pipeline_result = await db.execute(
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

        pipeline = (
            pipeline_result.scalar_one_or_none()
        )

        if pipeline is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No active default sales "
                    "pipeline is configured."
                ),
            )

        stage_result = await db.execute(
            select(PipelineStage)
            .where(
                PipelineStage.organization_id
                == organization_id,
                PipelineStage.pipeline_id
                == pipeline.id,
                PipelineStage.category
                == "open",
                PipelineStage.is_active.is_(True),
            )
            .order_by(
                PipelineStage.position.asc()
            )
            .limit(1)
        )

        stage = (
            stage_result.scalar_one_or_none()
        )

        if stage is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Default pipeline has no "
                    "active open stage."
                ),
            )

        account_name = (
            lead.company_name.strip()
        )

        account_result = await db.execute(
            select(Account)
            .where(
                Account.organization_id
                == organization_id,
                func.lower(
                    Account.name
                )
                == account_name.lower(),
            )
            .order_by(
                Account.created_at.asc()
            )
            .limit(1)
        )

        account = (
            account_result.scalar_one_or_none()
        )

        if account is None:
            account = Account(
                organization_id=organization_id,
                name=account_name,
                lifecycle_stage="prospect",
                owner_user_id=(
                    lead.owner_user_id
                ),
            )

            db.add(account)

            await db.flush()

        contact = None

        if (
            lead.email is not None
            and lead.email.strip()
        ):
            normalized_email = (
                lead.email
                .strip()
                .lower()
            )

            contact_result = await db.execute(
                select(Contact)
                .where(
                    Contact.organization_id
                    == organization_id,
                    func.lower(
                        Contact.email
                    )
                    == normalized_email,
                )
                .order_by(
                    Contact.created_at.asc()
                )
                .limit(1)
            )

            contact = (
                contact_result.scalar_one_or_none()
            )

        if contact is not None:
            if (
                contact.account_id is not None
                and contact.account_id
                != account.id
            ):
                raise HTTPException(
                    status_code=(
                        status.HTTP_409_CONFLICT
                    ),
                    detail=(
                        "A contact with this email "
                        "already belongs to another "
                        "account."
                    ),
                )

            if contact.account_id is None:
                contact.account_id = (
                    account.id
                )

            if not contact.job_title:
                contact.job_title = (
                    lead.job_title
                )

            if not contact.phone:
                contact.phone = (
                    lead.phone
                )

            contact.is_primary = True
            contact.is_active = True

        else:
            contact = Contact(
                organization_id=organization_id,
                account_id=account.id,
                first_name=lead.first_name,
                last_name=lead.last_name,
                email=(
                    lead.email.strip()
                    if lead.email
                    else None
                ),
                phone=lead.phone,
                job_title=lead.job_title,
                is_primary=True,
                is_active=True,
            )

            db.add(contact)

            await db.flush()

        if payload.opportunity_name:
            opportunity_name = (
                payload.opportunity_name.strip()
            )
        else:
            opportunity_name = (
                f"{account.name} - New Business"
            )

        opportunity = Opportunity(
            organization_id=organization_id,
            name=opportunity_name,
            account_id=account.id,
            primary_contact_id=(
                contact.id
            ),
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            owner_user_id=(
                lead.owner_user_id
            ),
            amount=(
                payload.opportunity_amount
            ),
            currency="USD",
            probability=(
                stage.probability
            ),
            expected_close_date=(
                payload.expected_close_date
            ),
            description=(
                "Created from converted lead: "
                f"{lead.first_name} "
                f"{lead.last_name}."
            ),
        )

        db.add(opportunity)

        await db.flush()

        lead.status = "converted"

        lead.converted_at = (
            datetime.now(
                timezone.utc
            )
        )

        lead.converted_account_id = (
            account.id
        )

        lead.converted_contact_id = (
            contact.id
        )

        lead.converted_opportunity_id = (
            opportunity.id
        )

        await add_automated_activity(
            db,
            organization_id,
            activity_type="system",
            subject=(
                "Lead converted: "
                f"{lead.first_name} "
                f"{lead.last_name}"
            ),
            body=(
                "Lead converted into "
                "Account, Contact and "
                "Opportunity records."
            ),
            account_id=account.id,
            contact_id=contact.id,
            lead_id=lead.id,
            opportunity_id=(
                opportunity.id
            ),
            occurred_at=(
                lead.converted_at
            ),
            activity_metadata={
                "automated": True,
                "trigger": (
                    "lead_converted"
                ),
                "lead_id": str(
                    lead.id
                ),
                "account_id": str(
                    account.id
                ),
                "contact_id": str(
                    contact.id
                ),
                "opportunity_id": str(
                    opportunity.id
                ),
                "pipeline_id": str(
                    pipeline.id
                ),
                "stage_id": str(
                    stage.id
                ),
            },
        )

        await db.commit()

        return LeadConvertResponse(
            lead_id=lead.id,
            account_id=account.id,
            contact_id=contact.id,
            opportunity_id=(
                opportunity.id
            ),
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            status="converted",
        )

    except HTTPException:
        await db.rollback()
        raise

    except Exception as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Lead conversion failed. "
                "No CRM records were committed."
            ),
        ) from exc
