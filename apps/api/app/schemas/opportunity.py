import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class OpportunityCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=250,
    )

    account_id: uuid.UUID

    primary_contact_id: (
        uuid.UUID | None
    ) = None

    pipeline_id: (
        uuid.UUID | None
    ) = None

    stage_id: (
        uuid.UUID | None
    ) = None

    owner_user_id: (
        uuid.UUID | None
    ) = None

    amount: Decimal | None = Field(
        default=None,
        ge=0,
    )

    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
    )

    probability: int | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    expected_close_date: (
        date | None
    ) = None

    description: str | None = None


class OpportunityUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=250,
    )

    account_id: (
        uuid.UUID | None
    ) = None

    primary_contact_id: (
        uuid.UUID | None
    ) = None

    pipeline_id: (
        uuid.UUID | None
    ) = None

    stage_id: (
        uuid.UUID | None
    ) = None

    owner_user_id: (
        uuid.UUID | None
    ) = None

    amount: Decimal | None = Field(
        default=None,
        ge=0,
    )

    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
    )

    probability: int | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    expected_close_date: (
        date | None
    ) = None

    description: str | None = None


class OpportunityResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    name: str

    account_id: uuid.UUID
    primary_contact_id: (
        uuid.UUID | None
    )

    pipeline_id: uuid.UUID
    stage_id: uuid.UUID

    owner_user_id: (
        uuid.UUID | None
    )

    amount: Decimal | None
    currency: str

    probability: int

    expected_close_date: (
        date | None
    )

    closed_at: datetime | None

    description: str | None

    created_at: datetime
    updated_at: datetime
