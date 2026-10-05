import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


ActivityType = Literal[
    "email",
    "call",
    "meeting",
    "note",
    "sms",
    "task_completed",
    "document",
    "system",
    "ai_action",
]

ActivityDirection = Literal[
    "inbound",
    "outbound",
]


class ActivityCreate(BaseModel):
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None

    activity_type: ActivityType

    subject: str = Field(
        min_length=1,
        max_length=300,
    )

    body: str | None = None

    direction: ActivityDirection | None = None

    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(
            timezone.utc
        )
    )

    external_id: str | None = Field(
        default=None,
        max_length=500,
    )

    activity_metadata: dict = Field(
        default_factory=dict
    )


class ActivityResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    account_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None

    created_by_user_id: uuid.UUID | None

    activity_type: str
    subject: str
    body: str | None
    direction: str | None

    occurred_at: datetime

    external_id: str | None

    activity_metadata: dict

    created_at: datetime
    updated_at: datetime
