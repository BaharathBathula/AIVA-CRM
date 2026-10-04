import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


TaskType = Literal[
    "general",
    "call",
    "email",
    "meeting",
    "follow_up",
    "demo",
    "proposal",
    "review",
    "renewal",
]

TaskStatus = Literal[
    "open",
    "in_progress",
    "completed",
    "cancelled",
]

TaskPriority = Literal[
    "low",
    "medium",
    "high",
    "urgent",
]

TaskSource = Literal[
    "manual",
    "system",
    "ai",
    "email",
    "meeting",
    "workflow",
]


class TaskCreate(BaseModel):
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None

    assigned_to_user_id: uuid.UUID | None = None
    parent_task_id: uuid.UUID | None = None

    title: str = Field(
        min_length=1,
        max_length=300,
    )

    description: str | None = None

    task_type: TaskType = "general"
    status: TaskStatus = "open"
    priority: TaskPriority = "medium"
    source: TaskSource = "manual"

    start_at: datetime | None = None
    due_at: datetime | None = None
    reminder_at: datetime | None = None

    is_recurring: bool = False

    recurrence_rule: str | None = Field(
        default=None,
        max_length=500,
    )

    is_ai_generated: bool = False

    external_id: str | None = Field(
        default=None,
        max_length=500,
    )

    tags: list[str] = Field(
        default_factory=list,
    )

    task_metadata: dict = Field(
        default_factory=dict,
    )

    @model_validator(mode="after")
    def validate_task_dates(self):
        if (
            self.start_at is not None
            and self.due_at is not None
            and self.due_at < self.start_at
        ):
            raise ValueError(
                "due_at cannot be earlier than start_at."
            )

        if (
            self.reminder_at is not None
            and self.due_at is not None
            and self.reminder_at > self.due_at
        ):
            raise ValueError(
                "reminder_at cannot be later than due_at."
            )

        return self

    @model_validator(mode="after")
    def validate_recurrence(self):
        if (
            self.is_recurring
            and not self.recurrence_rule
        ):
            raise ValueError(
                "recurrence_rule is required "
                "for recurring tasks."
            )

        if (
            not self.is_recurring
            and self.recurrence_rule
        ):
            raise ValueError(
                "recurrence_rule cannot be set "
                "when is_recurring is false."
            )

        return self


class TaskUpdate(BaseModel):
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None

    assigned_to_user_id: uuid.UUID | None = None
    parent_task_id: uuid.UUID | None = None

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )

    description: str | None = None

    task_type: TaskType | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    source: TaskSource | None = None

    start_at: datetime | None = None
    due_at: datetime | None = None
    reminder_at: datetime | None = None

    is_recurring: bool | None = None

    recurrence_rule: str | None = Field(
        default=None,
        max_length=500,
    )

    external_id: str | None = Field(
        default=None,
        max_length=500,
    )

    tags: list[str] | None = None

    task_metadata: dict | None = None


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class TaskAssign(BaseModel):
    assigned_to_user_id: uuid.UUID | None


class TaskResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    account_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None

    assigned_to_user_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None

    parent_task_id: uuid.UUID | None

    title: str
    description: str | None

    task_type: str
    status: str
    priority: str
    source: str

    start_at: datetime | None
    due_at: datetime | None
    reminder_at: datetime | None
    completed_at: datetime | None

    is_recurring: bool
    recurrence_rule: str | None

    is_ai_generated: bool

    external_id: str | None

    tags: list[str]
    task_metadata: dict

    created_at: datetime
    updated_at: datetime
