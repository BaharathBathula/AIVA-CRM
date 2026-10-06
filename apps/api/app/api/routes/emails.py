import uuid

from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.tenant import (
    get_current_organization_id,
)
from app.db.session import get_db
from app.schemas.email import (
    EmailComposeRequest,
    EmailMessageCreate,
    EmailMessageResponse,
    EmailReplyRequest,
    EmailSendResponse,
    EmailThreadCreate,
    EmailThreadResponse,
)
from app.services.emails import (
    create_email_message,
    create_email_thread,
    get_email_message,
    get_email_thread,
    list_email_threads,
    list_thread_messages,
)


router = APIRouter(
    prefix="/email",
    tags=["Email"],
)


@router.post(
    "/threads",
    response_model=EmailThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_email_thread_endpoint(
    payload: EmailThreadCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await create_email_thread(
        db,
        organization_id,
        payload,
    )


@router.get(
    "/threads",
    response_model=list[
        EmailThreadResponse
    ],
)
async def list_email_threads_endpoint(
    provider: str | None = Query(
        default=None,
        max_length=30,
    ),
    account_id: uuid.UUID | None = Query(
        default=None,
    ),
    contact_id: uuid.UUID | None = Query(
        default=None,
    ),
    opportunity_id: uuid.UUID | None = Query(
        default=None,
    ),
    search: str | None = Query(
        default=None,
        max_length=500,
    ),
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await list_email_threads(
        db,
        organization_id,
        provider=provider,
        account_id=account_id,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/threads/{thread_id}",
    response_model=EmailThreadResponse,
)
async def get_email_thread_endpoint(
    thread_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await get_email_thread(
        db,
        organization_id,
        thread_id,
    )


@router.get(
    "/threads/{thread_id}/messages",
    response_model=list[
        EmailMessageResponse
    ],
)
async def list_thread_messages_endpoint(
    thread_id: uuid.UUID,
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=200,
    ),
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await list_thread_messages(
        db,
        organization_id,
        thread_id,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/messages",
    response_model=EmailMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_email_message_endpoint(
    payload: EmailMessageCreate,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await create_email_message(
        db,
        organization_id,
        payload,
    )


@router.get(
    "/messages/{message_id}",
    response_model=EmailMessageResponse,
)
async def get_email_message_endpoint(
    message_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(
        get_db
    ),
):
    return await get_email_message(
        db,
        organization_id,
        message_id,
    )

@router.post(
    "/compose",
    response_model=EmailSendResponse,
)
async def compose_email_endpoint(
    payload: EmailComposeRequest,
    organization_id=Depends(
        get_current_organization_id
    ),
    db: AsyncSession = Depends(get_db),
):
    participants = [
        {
            "email": str(payload.from_address),
            "name": payload.from_name,
            "role": "from",
        }
    ]

    for recipient in payload.to_recipients:
        participants.append(
            {
                **recipient,
                "role": "to",
            }
        )

    for recipient in payload.cc_recipients:
        participants.append(
            {
                **recipient,
                "role": "cc",
            }
        )

    for recipient in payload.bcc_recipients:
        participants.append(
            {
                **recipient,
                "role": "bcc",
            }
        )

    thread_values = {
        "provider": "aiva",
        "subject": payload.subject,
        "participants": participants,
        "snippet": (
            payload.body_text
            or payload.body_html
            or ""
        )[:250],
        "account_id": payload.account_id,
        "contact_id": payload.contact_id,
        "lead_id": payload.lead_id,
        "opportunity_id": payload.opportunity_id,
        "thread_metadata": {
            "source": "manual_compose",
        },
    }

    allowed_thread_fields = (
        EmailThreadCreate.model_fields.keys()
    )

    thread_payload = EmailThreadCreate.model_validate(
        {
            key: value
            for key, value in thread_values.items()
            if key in allowed_thread_fields
        }
    )

    thread = await create_email_thread(
        db,
        organization_id,
        thread_payload,
    )

    message_values = {
        "thread_id": thread.id,
        "provider": "aiva",
        "direction": "outbound",
        "subject": payload.subject,
        "from_address": str(
            payload.from_address
        ),
        "from_name": payload.from_name,
        "to_recipients": payload.to_recipients,
        "cc_recipients": payload.cc_recipients,
        "bcc_recipients": payload.bcc_recipients,
        "reply_to": (
            str(payload.reply_to)
            if payload.reply_to
            else None
        ),
        "body_text": payload.body_text,
        "body_html": payload.body_html,
        "snippet": (
            payload.body_text
            or payload.body_html
            or ""
        )[:250],
        "is_read": True,
        "is_draft": payload.is_draft,
        "has_attachments": False,
        "attachments": [],
        "references": [],
        "message_metadata": {
            "source": "manual_compose",
        },
    }

    allowed_message_fields = (
        EmailMessageCreate.model_fields.keys()
    )

    message_payload = (
        EmailMessageCreate.model_validate(
            {
                key: value
                for key, value
                in message_values.items()
                if key
                in allowed_message_fields
            }
        )
    )

    message = await create_email_message(
        db,
        organization_id,
        message_payload,
    )

    return {
        "thread": thread,
        "message": message,
    }