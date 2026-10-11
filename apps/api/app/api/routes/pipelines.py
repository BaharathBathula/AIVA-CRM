import uuid

from fastapi import (
    APIRouter,
    Depends,
    Query,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.db.session import get_db
from app.schemas.pipeline import (
    PipelineResponse,
    PipelineStageResponse,
)
from app.services.pipelines import (
    get_pipeline,
    get_pipeline_stage,
    list_pipeline_stages,
    list_pipelines,
)


router = APIRouter(
    prefix="/pipelines",
    tags=["Pipelines"],
)


@router.get(
    "",
    response_model=list[
        PipelineResponse
    ],
)
async def list_pipelines_endpoint(
    include_inactive: bool = Query(
        default=False,
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await list_pipelines(
        db,
        organization_id,
        include_inactive,
    )


@router.get(
    "/{pipeline_id}",
    response_model=PipelineResponse,
)
async def get_pipeline_endpoint(
    pipeline_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await get_pipeline(
        db,
        organization_id,
        pipeline_id,
    )


@router.get(
    "/{pipeline_id}/stages",
    response_model=list[
        PipelineStageResponse
    ],
)
async def list_pipeline_stages_endpoint(
    pipeline_id: uuid.UUID,
    include_inactive: bool = Query(
        default=False,
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await list_pipeline_stages(
        db,
        organization_id,
        pipeline_id,
        include_inactive,
    )


@router.get(
    "/{pipeline_id}/stages/{stage_id}",
    response_model=PipelineStageResponse,
)
async def get_pipeline_stage_endpoint(
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await get_pipeline_stage(
        db,
        organization_id,
        pipeline_id,
        stage_id,
    )


# AIVA_P32_CREATE_PIPELINE
from app.schemas.pipeline import PipelineCreate
from app.services.pipelines import create_pipeline


@router.post(
    "",
    response_model=PipelineResponse,
    status_code=201,
)
async def create_pipeline_endpoint(
    payload: PipelineCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await create_pipeline(
        db=db,
        organization_id=organization_id,
        name=payload.name,
    )


# AIVA_P43_RENAME_PIPELINE
from app.schemas.pipeline import PipelineRename
from app.services.pipelines import rename_pipeline


@router.patch(
    "/{pipeline_id}",
    response_model=PipelineResponse,
)
async def rename_pipeline_endpoint(
    pipeline_id: uuid.UUID,
    payload: PipelineRename,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await rename_pipeline(
        db=db,
        organization_id=organization_id,
        pipeline_id=pipeline_id,
        name=payload.name,
    )


# AIVA_P51_ADD_STAGE
from app.schemas.pipeline import PipelineStageCreate
from app.services.pipelines import create_pipeline_stage


@router.post(
    "/{pipeline_id}/stages",
    response_model=PipelineStageResponse,
    status_code=201,
)
async def create_pipeline_stage_endpoint(
    pipeline_id: uuid.UUID,
    payload: PipelineStageCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await create_pipeline_stage(
        db=db,
        organization_id=organization_id,
        pipeline_id=pipeline_id,
        name=payload.name,
        probability=payload.probability,
        category=payload.category,
    )

# AIVA_P53_UPDATE_STAGE_ENDPOINT
from app.schemas.pipeline import PipelineStageUpdate
from app.services.pipelines import update_pipeline_stage


@router.patch(
    "/{pipeline_id}/stages/{stage_id}",
    response_model=PipelineStageResponse,
)
async def update_pipeline_stage_endpoint(
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
    payload: PipelineStageUpdate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await update_pipeline_stage(
        db=db,
        organization_id=organization_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        changes=payload.model_dump(exclude_unset=True),
    )


# AIVA_P54_REORDER_STAGE_ENDPOINT
from app.schemas.pipeline import PipelineStageReorder
from app.services.pipelines import reorder_pipeline_stages


@router.put(
    "/{pipeline_id}/stages/reorder",
    response_model=list[PipelineStageResponse],
)
async def reorder_pipeline_stages_endpoint(
    pipeline_id: uuid.UUID,
    payload: PipelineStageReorder,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await reorder_pipeline_stages(
        db=db,
        organization_id=organization_id,
        pipeline_id=pipeline_id,
        stage_ids=payload.stage_ids,
    )


# AIVA_P55_DEACTIVATE_STAGE_ENDPOINT
from app.services.pipelines import deactivate_pipeline_stage


@router.patch(
    "/{pipeline_id}/stages/{stage_id}/deactivate",
    response_model=PipelineStageResponse,
)
async def deactivate_pipeline_stage_endpoint(
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    return await deactivate_pipeline_stage(
        db=db,
        organization_id=organization_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
    )


# AIVA_P56_REACTIVATE_STAGE_ENDPOINT
from app.services.pipelines import reactivate_pipeline_stage


@router.patch(
    "/{pipeline_id}/stages/{stage_id}/reactivate",
    response_model=PipelineStageResponse,
)
async def reactivate_pipeline_stage_endpoint(
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
):
    """Reactivate an inactive stage in an active pipeline."""
    return await reactivate_pipeline_stage(
        db=db,
        organization_id=organization_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
    )
