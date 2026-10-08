import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
)


class OpportunityCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=250,
    )

    account_id: uuid.UUID

    primary_contact_id: uuid.UUID | None = None

    pipeline_id: uuid.UUID | None = None

    stage_id: uuid.UUID | None = None

    owner_user_id: uuid.UUID | None = None

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

    expected_close_date: date | None = None

    description: str | None = None

    # ---------------------------------------------------------
    # ENTERPRISE OPPORTUNITY FIELDS
    # ---------------------------------------------------------

    opportunity_type: str = Field(
        default="new_business",
        pattern=(
            "^(new_business|renewal|upsell|"
            "cross_sell|expansion)$"
        ),
    )

    lead_source: str | None = Field(
        default=None,
        max_length=80,
    )

    priority: str = Field(
        default="medium",
        pattern="^(low|medium|high|critical)$",
    )

    forecast_category: str = Field(
        default="pipeline",
        pattern=(
            "^(pipeline|best_case|commit|"
            "closed|omitted)$"
        ),
    )

    next_step: str | None = None

    loss_reason: str | None = None

    competitor: str | None = Field(
        default=None,
        max_length=250,
    )


class OpportunityUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=250,
    )

    account_id: uuid.UUID | None = None

    primary_contact_id: uuid.UUID | None = None

    pipeline_id: uuid.UUID | None = None

    stage_id: uuid.UUID | None = None

    owner_user_id: uuid.UUID | None = None

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

    expected_close_date: date | None = None

    description: str | None = None

    # ---------------------------------------------------------
    # ENTERPRISE OPPORTUNITY FIELDS
    # ---------------------------------------------------------

    opportunity_type: str | None = Field(
        default=None,
        pattern=(
            "^(new_business|renewal|upsell|"
            "cross_sell|expansion)$"
        ),
    )

    lead_source: str | None = Field(
        default=None,
        max_length=80,
    )

    priority: str | None = Field(
        default=None,
        pattern="^(low|medium|high|critical)$",
    )

    forecast_category: str | None = Field(
        default=None,
        pattern=(
            "^(pipeline|best_case|commit|"
            "closed|omitted)$"
        ),
    )

    next_step: str | None = None

    loss_reason: str | None = None

    competitor: str | None = Field(
        default=None,
        max_length=250,
    )


class OpportunityResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    name: str
    description: str | None

    account_id: uuid.UUID
    primary_contact_id: uuid.UUID | None

    pipeline_id: uuid.UUID
    stage_id: uuid.UUID

    owner_user_id: uuid.UUID | None

    amount: Decimal | None
    currency: str

    probability: int

    expected_close_date: date | None

    closed_at: datetime | None

    # ---------------------------------------------------------
    # ENTERPRISE OPPORTUNITY FIELDS
    # ---------------------------------------------------------

    opportunity_type: str

    lead_source: str | None

    priority: str

    forecast_category: str

    next_step: str | None

    loss_reason: str | None

    competitor: str | None

    stage_entered_at: datetime

    created_at: datetime
    updated_at: datetime

    # ---------------------------------------------------------
    # DERIVED METRICS
    # ---------------------------------------------------------

    @computed_field
    @property
    def weighted_amount(self) -> Decimal | None:
        if self.amount is None:
            return None

        return (
            self.amount
            * Decimal(self.probability)
            / Decimal(100)
        )


class OpportunityBulkOwnerUpdate(BaseModel):
    opportunity_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=100,
    )
    owner_user_id: uuid.UUID


class OpportunityBulkOwnerResult(BaseModel):
    updated_count: int
    updated_ids: list[uuid.UUID]
    requested_count: int
    owner_user_id: uuid.UUID


class OpportunityBulkStageRequest(BaseModel):
    opportunity_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=100,
    )
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID


class OpportunityBulkStageResponse(BaseModel):
    updated_count: int
    updated_ids: list[uuid.UUID]
    requested_count: int
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID


class OpportunityBulkPriorityRequest(BaseModel):
    opportunity_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=100,
    )
    priority: str = Field(
        pattern="^(low|medium|high|critical)$",
    )


class OpportunityBulkPriorityResponse(BaseModel):
    updated_count: int
    updated_ids: list[uuid.UUID]
    requested_count: int
    priority: str
