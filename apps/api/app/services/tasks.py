import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.task import Task
from app.services.activities import (
    add_automated_activity,
)

from app.schemas.task import (
    TaskAssign,
    TaskCreate,
    TaskStatusUpdate,
    TaskUpdate,
)


async def validate_task_user(
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
                "Assigned user must belong "
                "to this organization."
            ),
        )


async def get_valid_task_account(
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
                "Task account does not belong "
                "to this organization."
            ),
        )

    return account


async def get_valid_task_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
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
                "Task contact does not belong "
                "to this organization."
            ),
        )

    return contact


async def get_valid_task_lead(
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Task lead does not belong "
                "to this organization."
            ),
        )

    return lead


async def get_valid_task_opportunity(
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

    opportunity = result.scalar_one_or_none()

    if opportunity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Task opportunity does not "
                "belong to this organization."
            ),
        )

    return opportunity


async def get_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
) -> Task:
    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.organization_id
            == organization_id,
        )
    )

    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found.",
        )

    return task


async def validate_parent_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    parent_task_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
) -> Task:
    parent = await get_task(
        db,
        organization_id,
        parent_task_id,
    )

    if (
        task_id is not None
        and parent.id == task_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A task cannot be its own "
                "parent."
            ),
        )

    if task_id is None:
        return parent

    seen: set[uuid.UUID] = set()

    current = parent

    while current.parent_task_id is not None:
        if current.id in seen:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Existing task hierarchy "
                    "contains a cycle."
                ),
            )

        seen.add(current.id)

        if current.parent_task_id == task_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Task hierarchy would "
                    "create a cycle."
                ),
            )

        current = await get_task(
            db,
            organization_id,
            current.parent_task_id,
        )

    return parent


def validate_effective_dates(
    start_at: datetime | None,
    due_at: datetime | None,
    reminder_at: datetime | None,
) -> None:
    if (
        start_at is not None
        and due_at is not None
        and due_at < start_at
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "due_at cannot be earlier "
                "than start_at."
            ),
        )

    if (
        reminder_at is not None
        and due_at is not None
        and reminder_at > due_at
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "reminder_at cannot be later "
                "than due_at."
            ),
        )


async def resolve_task_relationships(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID | None,
    contact_id: uuid.UUID | None,
    lead_id: uuid.UUID | None,
    opportunity_id: uuid.UUID | None,
) -> uuid.UUID | None:
    effective_account_id = account_id

    if account_id is not None:
        await get_valid_task_account(
            db,
            organization_id,
            account_id,
        )

    contact = None

    if contact_id is not None:
        contact = await get_valid_task_contact(
            db,
            organization_id,
            contact_id,
        )

        if contact.account_id is not None:
            if effective_account_id is None:
                effective_account_id = (
                    contact.account_id
                )

            elif (
                effective_account_id
                != contact.account_id
            ):
                raise HTTPException(
                    status_code=(
                        status.HTTP_409_CONFLICT
                    ),
                    detail=(
                        "Task contact does not "
                        "belong to the selected "
                        "account."
                    ),
                )

    if lead_id is not None:
        await get_valid_task_lead(
            db,
            organization_id,
            lead_id,
        )

    if opportunity_id is not None:
        opportunity = (
            await get_valid_task_opportunity(
                db,
                organization_id,
                opportunity_id,
            )
        )

        if effective_account_id is None:
            effective_account_id = (
                opportunity.account_id
            )

        elif (
            opportunity.account_id
            != effective_account_id
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_409_CONFLICT
                ),
                detail=(
                    "Task opportunity does not "
                    "belong to the selected "
                    "account."
                ),
            )

    return effective_account_id


async def add_task_completed_activity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task: Task,
) -> None:
    await add_automated_activity(
        db,
        organization_id,
        activity_type="task_completed",
        subject=(
            f"Task completed: {task.title}"
        ),
        body=task.description,
        account_id=task.account_id,
        contact_id=task.contact_id,
        lead_id=task.lead_id,
        opportunity_id=(
            task.opportunity_id
        ),
        occurred_at=(
            task.completed_at
            or datetime.now(
                timezone.utc
            )
        ),
        activity_metadata={
            "task_id": str(task.id),
            "task_type": task.task_type,
            "priority": task.priority,
            "source": task.source,
            "automated": True,
            "trigger": (
                "task_completed"
            ),
        },
    )


async def create_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: TaskCreate,
    created_by_user_id: uuid.UUID | None = None,
) -> Task:
    if created_by_user_id is not None:
        await validate_task_user(
            db,
            organization_id,
            created_by_user_id,
        )

    if payload.assigned_to_user_id is not None:
        await validate_task_user(
            db,
            organization_id,
            payload.assigned_to_user_id,
        )

    if payload.parent_task_id is not None:
        await validate_parent_task(
            db,
            organization_id,
            payload.parent_task_id,
        )

    account_id = await resolve_task_relationships(
        db,
        organization_id,
        payload.account_id,
        payload.contact_id,
        payload.lead_id,
        payload.opportunity_id,
    )

    task = Task(
        organization_id=organization_id,
        created_by_user_id=(
            created_by_user_id
        ),
        account_id=account_id,
        contact_id=payload.contact_id,
        lead_id=payload.lead_id,
        opportunity_id=(
            payload.opportunity_id
        ),
        assigned_to_user_id=(
            payload.assigned_to_user_id
        ),
        parent_task_id=(
            payload.parent_task_id
        ),
        title=payload.title.strip(),
        description=payload.description,
        task_type=payload.task_type,
        status=payload.status,
        priority=payload.priority,
        source=payload.source,
        start_at=payload.start_at,
        due_at=payload.due_at,
        reminder_at=payload.reminder_at,
        is_recurring=payload.is_recurring,
        recurrence_rule=(
            payload.recurrence_rule
        ),
        is_ai_generated=(
            payload.is_ai_generated
        ),
        external_id=payload.external_id,
        tags=payload.tags,
        task_metadata=(
            payload.task_metadata
        ),
    )

    if payload.status == "completed":
        task.completed_at = (
            datetime.now(timezone.utc)
        )

    db.add(task)

    await db.commit()
    await db.refresh(task)

    return task


async def list_tasks(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    assigned_to_user_id: (
        uuid.UUID | None
    ) = None,
    task_status: str | None = None,
    priority: str | None = None,
    task_type: str | None = None,
    is_ai_generated: bool | None = None,
    is_overdue: bool | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Task]:
    statement = select(Task).where(
        Task.organization_id
        == organization_id
    )

    if account_id is not None:
        statement = statement.where(
            Task.account_id == account_id
        )

    if contact_id is not None:
        statement = statement.where(
            Task.contact_id == contact_id
        )

    if lead_id is not None:
        statement = statement.where(
            Task.lead_id == lead_id
        )

    if opportunity_id is not None:
        statement = statement.where(
            Task.opportunity_id
            == opportunity_id
        )

    if assigned_to_user_id is not None:
        statement = statement.where(
            Task.assigned_to_user_id
            == assigned_to_user_id
        )

    if task_status is not None:
        statement = statement.where(
            Task.status == task_status
        )

    if priority is not None:
        statement = statement.where(
            Task.priority == priority
        )

    if task_type is not None:
        statement = statement.where(
            Task.task_type == task_type
        )

    if is_ai_generated is not None:
        statement = statement.where(
            Task.is_ai_generated
            == is_ai_generated
        )

    now = datetime.now(timezone.utc)

    if is_overdue is True:
        statement = statement.where(
            Task.due_at.is_not(None),
            Task.due_at < now,
            Task.status.notin_(
                [
                    "completed",
                    "cancelled",
                ]
            ),
        )

    elif is_overdue is False:
        statement = statement.where(
            or_(
                Task.due_at.is_(None),
                Task.due_at >= now,
                Task.status.in_(
                    [
                        "completed",
                        "cancelled",
                    ]
                ),
            )
        )

    if search:
        search_term = (
            f"%{search.strip()}%"
        )

        statement = statement.where(
            or_(
                Task.title.ilike(
                    search_term
                ),
                Task.description.ilike(
                    search_term
                ),
            )
        )

    statement = (
        statement
        .order_by(
            Task.due_at.asc().nullslast(),
            Task.created_at.desc(),
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


async def update_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskUpdate,
) -> Task:
    task = await get_task(
        db,
        organization_id,
        task_id,
    )

    updates = payload.model_dump(
        exclude_unset=True
    )

    if (
        "title" in updates
        and updates["title"] is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail="Task title cannot be null.",
        )

    if "title" in updates:
        updates["title"] = (
            updates["title"].strip()
        )

        if not updates["title"]:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    "Task title cannot be empty."
                ),
            )

    if (
        "assigned_to_user_id"
        in updates
        and updates[
            "assigned_to_user_id"
        ] is not None
    ):
        await validate_task_user(
            db,
            organization_id,
            updates[
                "assigned_to_user_id"
            ],
        )

    if (
        "parent_task_id" in updates
        and updates["parent_task_id"]
        is not None
    ):
        await validate_parent_task(
            db,
            organization_id,
            updates["parent_task_id"],
            task.id,
        )

    effective_account_id = updates.get(
        "account_id",
        task.account_id,
    )

    effective_contact_id = updates.get(
        "contact_id",
        task.contact_id,
    )

    effective_lead_id = updates.get(
        "lead_id",
        task.lead_id,
    )

    effective_opportunity_id = updates.get(
        "opportunity_id",
        task.opportunity_id,
    )

    resolved_account_id = (
        await resolve_task_relationships(
            db,
            organization_id,
            effective_account_id,
            effective_contact_id,
            effective_lead_id,
            effective_opportunity_id,
        )
    )

    if (
        resolved_account_id
        != effective_account_id
    ):
        updates["account_id"] = (
            resolved_account_id
        )

    effective_start_at = updates.get(
        "start_at",
        task.start_at,
    )

    effective_due_at = updates.get(
        "due_at",
        task.due_at,
    )

    effective_reminder_at = updates.get(
        "reminder_at",
        task.reminder_at,
    )

    validate_effective_dates(
        effective_start_at,
        effective_due_at,
        effective_reminder_at,
    )

    effective_is_recurring = updates.get(
        "is_recurring",
        task.is_recurring,
    )

    effective_recurrence_rule = (
        updates.get(
            "recurrence_rule",
            task.recurrence_rule,
        )
    )

    if (
        effective_is_recurring
        and not effective_recurrence_rule
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "recurrence_rule is required "
                "for recurring tasks."
            ),
        )

    if effective_is_recurring is False:
        updates["recurrence_rule"] = None

    old_status = task.status
    new_status = updates.get(
        "status",
        old_status,
    )

    for field, value in updates.items():
        setattr(task, field, value)

    if (
        new_status == "completed"
        and old_status != "completed"
    ):
        task.completed_at = (
            datetime.now(timezone.utc)
        )

        await add_task_completed_activity(
            db,
            organization_id,
            task,
        )

    elif (
        old_status == "completed"
        and new_status != "completed"
    ):
        task.completed_at = None

    await db.commit()
    await db.refresh(task)

    return task


async def update_task_status(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskStatusUpdate,
) -> Task:
    task = await get_task(
        db,
        organization_id,
        task_id,
    )

    previous_status = task.status

    task.status = payload.status

    if (
        payload.status == "completed"
        and previous_status != "completed"
    ):
        task.completed_at = (
            datetime.now(timezone.utc)
        )

        await add_task_completed_activity(
            db,
            organization_id,
            task,
        )

    elif payload.status != "completed":
        task.completed_at = None

    await db.commit()
    await db.refresh(task)

    return task


async def assign_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskAssign,
) -> Task:
    task = await get_task(
        db,
        organization_id,
        task_id,
    )

    if payload.assigned_to_user_id is not None:
        await validate_task_user(
            db,
            organization_id,
            payload.assigned_to_user_id,
        )

    task.assigned_to_user_id = (
        payload.assigned_to_user_id
    )

    await db.commit()
    await db.refresh(task)

    return task


async def complete_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
) -> Task:
    task = await get_task(
        db,
        organization_id,
        task_id,
    )

    previous_status = task.status

    task.status = "completed"

    if task.completed_at is None:
        task.completed_at = (
            datetime.now(timezone.utc)
        )

    if previous_status != "completed":
        await add_task_completed_activity(
            db,
            organization_id,
            task,
        )

    await db.commit()
    await db.refresh(task)

    return task


async def reopen_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
) -> Task:
    task = await get_task(
        db,
        organization_id,
        task_id,
    )

    task.status = "open"
    task.completed_at = None

    await db.commit()
    await db.refresh(task)

    return task


async def delete_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
) -> None:
    task = await get_task(
        db,
        organization_id,
        task_id,
    )

    await db.delete(task)
    await db.commit()
