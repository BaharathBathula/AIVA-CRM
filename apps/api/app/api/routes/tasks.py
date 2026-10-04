import uuid

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.db.session import get_db
from app.schemas.task import (
    TaskAssign,
    TaskCreate,
    TaskPriority,
    TaskResponse,
    TaskStatus,
    TaskStatusUpdate,
    TaskType,
    TaskUpdate,
)
from app.services.tasks import (
    assign_task,
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    reopen_task,
    update_task,
    update_task_status,
)


router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"],
)


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_endpoint(
    payload: TaskCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await create_task(
        db,
        organization_id,
        payload,
    )


@router.get(
    "",
    response_model=list[TaskResponse],
)
async def list_tasks_endpoint(
    account_id: uuid.UUID | None = Query(
        default=None,
    ),
    contact_id: uuid.UUID | None = Query(
        default=None,
    ),
    lead_id: uuid.UUID | None = Query(
        default=None,
    ),
    opportunity_id: uuid.UUID | None = Query(
        default=None,
    ),
    assigned_to_user_id: uuid.UUID | None = Query(
        default=None,
    ),
    task_status: TaskStatus | None = Query(
        default=None,
        alias="status",
    ),
    priority: TaskPriority | None = Query(
        default=None,
    ),
    task_type: TaskType | None = Query(
        default=None,
    ),
    is_ai_generated: bool | None = Query(
        default=None,
    ),
    is_overdue: bool | None = Query(
        default=None,
    ),
    search: str | None = Query(
        default=None,
        max_length=200,
    ),
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await list_tasks(
        db=db,
        organization_id=organization_id,
        account_id=account_id,
        contact_id=contact_id,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
        assigned_to_user_id=(
            assigned_to_user_id
        ),
        task_status=task_status,
        priority=priority,
        task_type=task_type,
        is_ai_generated=is_ai_generated,
        is_overdue=is_overdue,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
)
async def get_task_endpoint(
    task_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await get_task(
        db,
        organization_id,
        task_id,
    )


@router.patch(
    "/{task_id}",
    response_model=TaskResponse,
)
async def update_task_endpoint(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await update_task(
        db,
        organization_id,
        task_id,
        payload,
    )


@router.patch(
    "/{task_id}/status",
    response_model=TaskResponse,
)
async def update_task_status_endpoint(
    task_id: uuid.UUID,
    payload: TaskStatusUpdate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await update_task_status(
        db,
        organization_id,
        task_id,
        payload,
    )


@router.patch(
    "/{task_id}/assign",
    response_model=TaskResponse,
)
async def assign_task_endpoint(
    task_id: uuid.UUID,
    payload: TaskAssign,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await assign_task(
        db,
        organization_id,
        task_id,
        payload,
    )


@router.post(
    "/{task_id}/complete",
    response_model=TaskResponse,
)
async def complete_task_endpoint(
    task_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await complete_task(
        db,
        organization_id,
        task_id,
    )


@router.post(
    "/{task_id}/reopen",
    response_model=TaskResponse,
)
async def reopen_task_endpoint(
    task_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await reopen_task(
        db,
        organization_id,
        task_id,
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_task_endpoint(
    task_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    await delete_task(
        db,
        organization_id,
        task_id,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )
