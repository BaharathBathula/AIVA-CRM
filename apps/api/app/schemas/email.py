import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)


EmailDirection = Literal[
    "inbound",
    "outbound",
]


class EmailRecipient(BaseModel):
    email: EmailStr
    name: str | None = None


class EmailThreadCreate(BaseModel):
    provider: str = Field(
        default="internal",
        min_length=1,
        max_length=30,
    )

    external_thread_id: str | None = Field(
        default=None,
        max_length=500,
    )

    subject: str = Field(
        min_length=1,
        max_length=500,
    )

    snippet: str | None = None

    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None

    participants: list[dict] = Field(
        default_factory=list
    )

    last_message_at: datetime | None = None


class EmailThreadResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID

    provider: str
    external_thread_id: str | None

    subject: str
    snippet: str | None

    account_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None

    participants: list[dict]

    last_message_at: datetime | None

    created_at: datetime
    updated_at: datetime


class EmailMessageCreate(BaseModel):
    thread_id: uuid.UUID

    provider: str = Field(
        default="internal",
        min_length=1,
        max_length=30,
    )

    external_message_id: str | None = Field(
        default=None,
        max_length=500,
    )

    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None

    direction: EmailDirection

    subject: str = Field(
        min_length=1,
        max_length=500,
    )

    from_address: EmailStr

    from_name: str | None = Field(
        default=None,
        max_length=250,
    )

    to_recipients: list[dict] = Field(
        default_factory=list
    )

    cc_recipients: list[dict] = Field(
        default_factory=list
    )

    bcc_recipients: list[dict] = Field(
        default_factory=list
    )

    reply_to: EmailStr | None = None

    body_text: str | None = None
    body_html: str | None = None
    snippet: str | None = None

    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(
            timezone.utc
        )
    )

    is_read: bool = False
    is_draft: bool = False

    has_attachments: bool = False

    attachments: list[dict] = Field(
        default_factory=list
    )

    internet_message_id: str | None = Field(
        default=None,
        max_length=500,
    )

    in_reply_to: str | None = Field(
        default=None,
        max_length=500,
    )

    references: list[str] = Field(
        default_factory=list
    )

    message_metadata: dict = Field(
        default_factory=dict
    )


class EmailComposeRequest(BaseModel):
    to_recipients: list[dict] = Field(
        default_factory=list
    )
    cc_recipients: list[dict] = Field(
        default_factory=list
    )
    bcc_recipients: list[dict] = Field(
        default_factory=list
    )

    subject: str = Field(
        min_length=1,
        max_length=500,
    )

    body_text: str | None = None
    body_html: str | None = None

    from_address: EmailStr
    from_name: str | None = None
    reply_to: EmailStr | None = None

    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None

    is_draft: bool = False


class EmailReplyRequest(BaseModel):
    body_text: str | None = None
    body_html: str | None = None

    from_address: EmailStr
    from_name: str | None = None

    cc_recipients: list[dict] = Field(
        default_factory=list
    )
    bcc_recipients: list[dict] = Field(
        default_factory=list
    )

    is_draft: bool = False


class EmailMessageResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: uuid.UUID
    organization_id: uuid.UUID
    thread_id: uuid.UUID

    provider: str
    external_message_id: str | None

    account_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None

    direction: str

    subject: str

    from_address: str
    from_name: str | None

    to_recipients: list[dict]
    cc_recipients: list[dict]
    bcc_recipients: list[dict]

    reply_to: str | None

    body_text: str | None
    body_html: str | None
    snippet: str | None

    occurred_at: datetime

    is_read: bool
    is_draft: bool

    has_attachments: bool
    attachments: list[dict]

    internet_message_id: str | None
    in_reply_to: str | None
    references: list[str]

    message_metadata: dict

    created_at: datetime
    updated_at: datetime


class EmailSendResponse(BaseModel):
    thread: EmailThreadResponse
    message: EmailMessageResponse
