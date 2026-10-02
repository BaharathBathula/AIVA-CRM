import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
)


PipelineStageCategory = Literal[
    "open",
    "won",
    "lost",
]


class PipelineResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    name: str

    is_default: bool
    is_active: bool

    created_at: datetime
    updated_at: datetime


class PipelineStageResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    pipeline_id: uuid.UUID

    name: str
    position: int
    probability: int

    category: PipelineStageCategory

    is_active: bool

    created_at: datetime
    updated_at: datetime
