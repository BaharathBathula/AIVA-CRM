import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


LeadStatus = Literal[
    "new",
    "contacted",
    "qualified",
    "unqualified",
    "nurture",
    "converted",
]


EditableLeadStatus = Literal[
    "new",
    "contacted",
    "qualified",
    "unqualified",
    "nurture",
]


class LeadCreate(BaseModel):
    first_name: str = Field(
        min_length=1,
        max_length=120,
    )

    last_name: str = Field(
        min_length=1,
        max_length=120,
    )

    email: str | None = Field(
        default=None,
        max_length=320,
    )

    phone: str | None = Field(
        default=None,
        max_length=50,
    )

    company_name: str | None = Field(
        default=None,
        max_length=250,
    )

    job_title: str | None = Field(
        default=None,
        max_length=150,
    )

    source: str = Field(
        default="manual",
        min_length=1,
        max_length=80,
    )

    status: EditableLeadStatus = "new"

    score: int = Field(
        default=0,
        ge=0,
        le=100,
    )

    notes: str | None = None

    owner_user_id: uuid.UUID | None = None


class LeadUpdate(BaseModel):
    first_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )

    last_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )

    email: str | None = Field(
        default=None,
        max_length=320,
    )

    phone: str | None = Field(
        default=None,
        max_length=50,
    )

    company_name: str | None = Field(
        default=None,
        max_length=250,
    )

    job_title: str | None = Field(
        default=None,
        max_length=150,
    )

    source: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
    )

    status: EditableLeadStatus | None = None

    score: int | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    notes: str | None = None

    owner_user_id: uuid.UUID | None = None


class LeadResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    first_name: str
    last_name: str

    email: str | None
    phone: str | None

    company_name: str | None
    job_title: str | None

    source: str
    status: LeadStatus
    score: int

    notes: str | None

    owner_user_id: uuid.UUID | None

    converted_at: datetime | None
    converted_account_id: uuid.UUID | None
    converted_contact_id: uuid.UUID | None
    converted_opportunity_id: uuid.UUID | None

    created_at: datetime
    updated_at: datetime


class LeadConvertRequest(BaseModel):
    opportunity_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=250,
    )

    opportunity_amount: Decimal | None = Field(
        default=None,
        ge=0,
    )

    expected_close_date: date | None = None


class LeadConvertResponse(BaseModel):
    lead_id: uuid.UUID

    account_id: uuid.UUID
    contact_id: uuid.UUID
    opportunity_id: uuid.UUID

    pipeline_id: uuid.UUID
    stage_id: uuid.UUID

    status: Literal["converted"] = "converted"
