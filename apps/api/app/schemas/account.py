import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


LifecycleStage = Literal[
    "prospect",
    "lead",
    "customer",
    "partner",
    "inactive",
    "churned",
]


class AccountCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=250,
    )

    domain: str | None = Field(
        default=None,
        max_length=255,
    )

    website: str | None = Field(
        default=None,
        max_length=500,
    )

    industry: str | None = Field(
        default=None,
        max_length=150,
    )

    phone: str | None = Field(
        default=None,
        max_length=50,
    )

    lifecycle_stage: LifecycleStage = "prospect"

    employee_count: int | None = Field(
        default=None,
        ge=0,
    )

    annual_revenue: Decimal | None = Field(
        default=None,
        ge=0,
    )

    description: str | None = None

    billing_address_line1: str | None = None
    billing_address_line2: str | None = None
    billing_city: str | None = None
    billing_state: str | None = None
    billing_postal_code: str | None = None
    billing_country: str | None = None

    owner_user_id: uuid.UUID | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=250,
    )

    domain: str | None = None
    website: str | None = None
    industry: str | None = None
    phone: str | None = None

    lifecycle_stage: LifecycleStage | None = None

    employee_count: int | None = Field(
        default=None,
        ge=0,
    )

    annual_revenue: Decimal | None = Field(
        default=None,
        ge=0,
    )

    description: str | None = None

    billing_address_line1: str | None = None
    billing_address_line2: str | None = None
    billing_city: str | None = None
    billing_state: str | None = None
    billing_postal_code: str | None = None
    billing_country: str | None = None

    owner_user_id: uuid.UUID | None = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    name: str
    domain: str | None
    website: str | None
    industry: str | None
    phone: str | None

    lifecycle_stage: str

    employee_count: int | None
    annual_revenue: Decimal | None

    description: str | None

    billing_address_line1: str | None
    billing_address_line2: str | None
    billing_city: str | None
    billing_state: str | None
    billing_postal_code: str | None
    billing_country: str | None

    owner_user_id: uuid.UUID | None

    created_at: datetime
    updated_at: datetime
