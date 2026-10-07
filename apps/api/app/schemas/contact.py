import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ContactCreate(BaseModel):
    account_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None

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

    mobile: str | None = Field(
        default=None,
        max_length=50,
    )

    job_title: str | None = Field(
        default=None,
        max_length=150,
    )

    department: str | None = Field(
        default=None,
        max_length=150,
    )

    segment: str | None = Field(
        default=None,
        max_length=100,
    )

    tags: list[str] = Field(
        default_factory=list,
    )

    linkedin_url: str | None = Field(
        default=None,
        max_length=500,
    )

    is_primary: bool = False


class ContactUpdate(BaseModel):
    account_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None

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

    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    job_title: str | None = None
    department: str | None = None

    segment: str | None = Field(
        default=None,
        max_length=100,
    )

    tags: list[str] | None = None
    linkedin_url: str | None = None

    is_primary: bool | None = None
    is_active: bool | None = None


class ContactResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID
    account_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None

    first_name: str
    last_name: str

    email: str | None
    phone: str | None
    mobile: str | None

    job_title: str | None
    department: str | None

    segment: str | None
    tags: list[str]
    linkedin_url: str | None

    is_primary: bool
    is_active: bool

    is_archived: bool
    archived_at: datetime | None

    created_at: datetime
    updated_at: datetime


class ContactDuplicateMatch(BaseModel):
    contact: ContactResponse

    confidence: str

    reasons: list[str]


class ContactDuplicateCheckResponse(BaseModel):
    has_duplicates: bool

    matches: list[
        ContactDuplicateMatch
    ]
