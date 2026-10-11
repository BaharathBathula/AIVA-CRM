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


# AIVA_P32_CREATE_PIPELINE
from pydantic import Field, field_validator


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Pipeline name cannot be blank.")
        return name


# AIVA_P43_RENAME_PIPELINE
class PipelineRename(BaseModel):
    name: str = Field(min_length=1, max_length=150)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Pipeline name cannot be blank.")
        return name


# AIVA_P51_ADD_STAGE
class PipelineStageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    probability: int = Field(default=0, ge=0, le=100)
    category: PipelineStageCategory = "open"

    @field_validator("name")
    @classmethod
    def normalize_stage_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Stage name cannot be blank.")
        if len(name) > 120:
            raise ValueError("Stage name exceeds 120 characters.")
        return name

# AIVA_P53_UPDATE_STAGE
class PipelineStageUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    probability: int | None = Field(
        default=None,
        ge=0,
        le=100,
        strict=True,
    )
    category: PipelineStageCategory | None = None

    @field_validator("name")
    @classmethod
    def normalize_update_stage_name(
        cls, value: str | None
    ) -> str | None:
        if value is None:
            return value

        name = value.strip()

        if not name:
            raise ValueError("Stage name cannot be blank.")

        if len(name) > 120:
            raise ValueError("Stage name exceeds 120 characters.")

        return name

    @field_validator(
        "name",
        "probability",
        "category",
    )
    @classmethod
    def reject_explicit_null(cls, value, info):
        if value is None:
            raise ValueError(
                f"{info.field_name} cannot be null."
            )
        return value


# AIVA_P54_REORDER_STAGE_SCHEMA
from uuid import UUID as _StageReorderUUID
from pydantic import field_validator as _stage_reorder_validator


class PipelineStageReorder(BaseModel):
    stage_ids: list[_StageReorderUUID] = Field(
        min_length=1,
    )

    @_stage_reorder_validator("stage_ids")
    @classmethod
    def validate_unique_stage_ids(
        cls,
        value: list[_StageReorderUUID],
    ) -> list[_StageReorderUUID]:
        if len(value) != len(set(value)):
            raise ValueError(
                "Stage IDs must be unique."
            )
        return value
