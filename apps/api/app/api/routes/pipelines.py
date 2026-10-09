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
