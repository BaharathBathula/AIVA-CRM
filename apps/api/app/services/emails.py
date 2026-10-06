from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.email_message import EmailMessage
from app.models.email_thread import EmailThread
from app.models.opportunity import Opportunity
from app.services.activities import (
    add_automated_activity,
)

from app.schemas.email import (
    EmailMessageCreate,
    EmailThreadCreate,
)


async def get_email_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Account:
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.organization_id
            == organization_id,
        )
    )

    account = result.scalar_one_or_none()

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Email account does not belong "
                "to this organization."
            ),
        )

    return account


async def get_email_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
) -> Contact:
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.organization_id
            == organization_id,
        )
    )

    contact = result.scalar_one_or_none()

    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Email contact does not belong "
                "to this organization."
            ),
        )

    return contact


async def get_email_opportunity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> Opportunity:
    result = await db.execute(
        select(Opportunity).where(
            Opportunity.id
            == opportunity_id,
            Opportunity.organization_id
            == organization_id,
        )
    )

    opportunity = result.scalar_one_or_none()

    if opportunity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Email opportunity does not belong "
                "to this organization."
            ),
        )

    return opportunity


async def resolve_email_relationships(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID | None,
    contact_id: uuid.UUID | None,
    opportunity_id: uuid.UUID | None,
) -> tuple[
    uuid.UUID | None,
    uuid.UUID | None,
    uuid.UUID | None,
]:
    effective_account_id = account_id

    if account_id is not None:
        await get_email_account(
            db,
            organization_id,
            account_id,
        )

    contact = None

    if contact_id is not None:
        contact = await get_email_contact(
            db,
            organization_id,
            contact_id,
        )

        if contact.account_id is not None:
            if effective_account_id is None:
                effective_account_id = (
                    contact.account_id
                )

            elif (
                effective_account_id
                != contact.account_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Email contact does not belong "
                        "to the selected account."
                    ),
                )

    opportunity = None

    if opportunity_id is not None:
        opportunity = await get_email_opportunity(
            db,
            organization_id,
            opportunity_id,
        )

        if effective_account_id is None:
            effective_account_id = (
                opportunity.account_id
            )

        elif (
            effective_account_id
            != opportunity.account_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Email opportunity does not belong "
                    "to the selected account."
                ),
            )

        if (
            contact is not None
            and contact.account_id is not None
            and contact.account_id
            != opportunity.account_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Email contact and opportunity "
                    "belong to different accounts."
                ),
            )

    return (
        effective_account_id,
        contact_id,
        opportunity_id,
    )


async def infer_email_relationships(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    direction: str,
    from_address: str,
    to_recipients: list[dict],
) -> tuple[
    uuid.UUID | None,
    uuid.UUID | None,
    uuid.UUID | None,
]:
    candidate_emails: set[str] = set()

    if direction == "inbound":
        candidate_emails.add(
            from_address.strip().lower()
        )

    else:
        for recipient in to_recipients:
            email = recipient.get("email")

            if isinstance(email, str):
                normalized = (
                    email.strip().lower()
                )

                if normalized:
                    candidate_emails.add(
                        normalized
                    )

    if not candidate_emails:
        return (
            None,
            None,
            None,
        )

    contact_result = await db.execute(
        select(Contact)
        .where(
            Contact.organization_id
            == organization_id,
            Contact.is_active.is_(True),
            func.lower(
                Contact.email
            ).in_(
                candidate_emails
            ),
        )
        .limit(2)
    )

    contacts = list(
        contact_result.scalars().all()
    )

    # Never guess when multiple CRM contacts
    # match the message participants.
    if len(contacts) != 1:
        return (
            None,
            None,
            None,
        )

    contact = contacts[0]

    account_id = contact.account_id
    opportunity_id = None

    if account_id is not None:
        opportunity_result = (
            await db.execute(
                select(Opportunity)
                .where(
                    Opportunity.organization_id
                    == organization_id,
                    Opportunity.account_id
                    == account_id,
                    Opportunity.primary_contact_id
                    == contact.id,
                    Opportunity.closed_at.is_(None),
                )
                .order_by(
                    Opportunity.created_at.desc()
                )
                .limit(2)
            )
        )

        opportunities = list(
            opportunity_result
            .scalars()
            .all()
        )

        # Only infer an Opportunity when
        # the association is unambiguous.
        if len(opportunities) == 1:
            opportunity_id = (
                opportunities[0].id
            )

    return (
        account_id,
        contact.id,
        opportunity_id,
    )


async def ensure_unique_external_thread(
    db: AsyncSession,
    organization_id: uuid.UUID,
    provider: str,
    external_thread_id: str | None,
) -> None:
    if external_thread_id is None:
        return

    result = await db.execute(
        select(EmailThread.id).where(
            EmailThread.organization_id
            == organization_id,
            EmailThread.provider
            == provider,
            EmailThread.external_thread_id
            == external_thread_id,
        )
    )

    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Email thread already exists "
                "for this provider."
            ),
        )


async def ensure_unique_external_message(
    db: AsyncSession,
    organization_id: uuid.UUID,
    provider: str,
    external_message_id: str | None,
) -> None:
    if external_message_id is None:
        return

    result = await db.execute(
        select(EmailMessage.id).where(
            EmailMessage.organization_id
            == organization_id,
            EmailMessage.provider
            == provider,
            EmailMessage.external_message_id
            == external_message_id,
        )
    )

    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Email message already exists "
                "for this provider."
            ),
        )


async def create_email_thread(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: EmailThreadCreate,
) -> EmailThread:
    provider = payload.provider.strip().lower()

    external_thread_id = (
        payload.external_thread_id.strip()
        if payload.external_thread_id
        else None
    )

    await ensure_unique_external_thread(
        db,
        organization_id,
        provider,
        external_thread_id,
    )

    (
        account_id,
        contact_id,
        opportunity_id,
    ) = await resolve_email_relationships(
        db,
        organization_id,
        payload.account_id,
        payload.contact_id,
        payload.opportunity_id,
    )

    thread = EmailThread(
        organization_id=organization_id,
        provider=provider,
        external_thread_id=(
            external_thread_id
        ),
        subject=payload.subject.strip(),
        snippet=payload.snippet,
        account_id=account_id,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        participants=payload.participants,
        last_message_at=(
            payload.last_message_at
        ),
    )

    db.add(thread)

    await db.commit()
    await db.refresh(thread)

    return thread


async def get_email_thread(
    db: AsyncSession,
    organization_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> EmailThread:
    result = await db.execute(
        select(EmailThread).where(
            EmailThread.id == thread_id,
            EmailThread.organization_id
            == organization_id,
        )
    )

    thread = result.scalar_one_or_none()

    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email thread not found.",
        )

    return thread


async def list_email_threads(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    provider: str | None = None,
    account_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[EmailThread]:
    statement = select(
        EmailThread
    ).where(
        EmailThread.organization_id
        == organization_id
    )

    if provider:
        statement = statement.where(
            EmailThread.provider
            == provider.strip().lower()
        )

    if account_id is not None:
        statement = statement.where(
            EmailThread.account_id
            == account_id
        )

    if contact_id is not None:
        statement = statement.where(
            EmailThread.contact_id
            == contact_id
        )

    if opportunity_id is not None:
        statement = statement.where(
            EmailThread.opportunity_id
            == opportunity_id
        )

    if search:
        normalized_search = (
            search.strip()
        )

        if normalized_search:
            pattern = (
                f"%{normalized_search}%"
            )

            statement = statement.where(
                or_(
                    EmailThread.subject.ilike(
                        pattern
                    ),
                    EmailThread.snippet.ilike(
                        pattern
                    ),
                )
            )

    statement = (
        statement
        .order_by(
            EmailThread.last_message_at
            .desc()
            .nullslast(),
            EmailThread.created_at.desc(),
        )
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(
        statement
    )

    return list(
        result.scalars().all()
    )


async def create_email_message(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: EmailMessageCreate,
) -> EmailMessage:
    thread = await get_email_thread(
        db,
        organization_id,
        payload.thread_id,
    )

    provider = payload.provider.strip().lower()

    if provider != thread.provider:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Email message provider must "
                "match its thread provider."
            ),
        )

    external_message_id = (
        payload.external_message_id.strip()
        if payload.external_message_id
        else None
    )

    await ensure_unique_external_message(
        db,
        organization_id,
        provider,
        external_message_id,
    )

    requested_account_id = (
        payload.account_id
        if payload.account_id is not None
        else thread.account_id
    )

    requested_contact_id = (
        payload.contact_id
        if payload.contact_id is not None
        else thread.contact_id
    )

    requested_opportunity_id = (
        payload.opportunity_id
        if payload.opportunity_id is not None
        else thread.opportunity_id
    )

    (
        inferred_account_id,
        inferred_contact_id,
        inferred_opportunity_id,
    ) = await infer_email_relationships(
        db,
        organization_id,
        direction=payload.direction,
        from_address=str(
            payload.from_address
        ),
        to_recipients=(
            payload.to_recipients
        ),
    )

    if requested_account_id is None:
        requested_account_id = (
            inferred_account_id
        )

    if requested_contact_id is None:
        requested_contact_id = (
            inferred_contact_id
        )

    if requested_opportunity_id is None:
        requested_opportunity_id = (
            inferred_opportunity_id
        )

    (
        account_id,
        contact_id,
        opportunity_id,
    ) = await resolve_email_relationships(
        db,
        organization_id,
        requested_account_id,
        requested_contact_id,
        requested_opportunity_id,
    )

    if (
        thread.account_id is not None
        and account_id != thread.account_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Email message account conflicts "
                "with its thread."
            ),
        )

    if (
        thread.contact_id is not None
        and contact_id != thread.contact_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Email message contact conflicts "
                "with its thread."
            ),
        )

    if (
        thread.opportunity_id is not None
        and opportunity_id
        != thread.opportunity_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Email message opportunity "
                "conflicts with its thread."
            ),
        )

    if thread.account_id is None:
        thread.account_id = account_id

    if thread.contact_id is None:
        thread.contact_id = contact_id

    if thread.opportunity_id is None:
        thread.opportunity_id = (
            opportunity_id
        )

    message = EmailMessage(
        organization_id=organization_id,
        thread_id=thread.id,
        provider=provider,
        external_message_id=(
            external_message_id
        ),
        account_id=account_id,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        direction=payload.direction,
        subject=payload.subject.strip(),
        from_address=str(
            payload.from_address
        ),
        from_name=payload.from_name,
        to_recipients=(
            payload.to_recipients
        ),
        cc_recipients=(
            payload.cc_recipients
        ),
        bcc_recipients=(
            payload.bcc_recipients
        ),
        reply_to=(
            str(payload.reply_to)
            if payload.reply_to
            else None
        ),
        body_text=payload.body_text,
        body_html=payload.body_html,
        snippet=payload.snippet,
        occurred_at=payload.occurred_at,
        is_read=payload.is_read,
        is_draft=payload.is_draft,
        has_attachments=(
            payload.has_attachments
        ),
        attachments=payload.attachments,
        internet_message_id=(
            payload.internet_message_id
        ),
        in_reply_to=payload.in_reply_to,
        references=payload.references,
        message_metadata=(
            payload.message_metadata
        ),
    )

    db.add(message)

    thread.last_message_at = (
        payload.occurred_at
    )

    if payload.snippet:
        thread.snippet = (
            payload.snippet
        )
    elif payload.body_text:
        thread.snippet = (
            payload.body_text[:240]
        )

    # Flush first so message.id is available
    # while keeping Email + Activity atomic.
    await db.flush()

    activity_subject_prefix = (
        "Email sent"
        if payload.direction
        == "outbound"
        else "Email received"
    )

    await add_automated_activity(
        db,
        organization_id,
        activity_type="email",
        subject=(
            f"{activity_subject_prefix}: "
            f"{message.subject}"
        ),
        body=(
            message.body_text
            or message.snippet
        ),
        account_id=(
            message.account_id
        ),
        contact_id=(
            message.contact_id
        ),
        opportunity_id=(
            message.opportunity_id
        ),
        occurred_at=(
            message.occurred_at
        ),
        direction=(
            message.direction
        ),
        external_id=(
            message.external_message_id
        ),
        activity_metadata={
            "automated": True,
            "trigger": (
                "email_message_created"
            ),
            "email_message_id": str(
                message.id
            ),
            "email_thread_id": str(
                message.thread_id
            ),
            "provider": (
                message.provider
            ),
            "direction": (
                message.direction
            ),
            "from_address": (
                message.from_address
            ),
            "has_attachments": (
                message.has_attachments
            ),
            "is_draft": (
                message.is_draft
            ),
        },
    )

    await db.commit()
    await db.refresh(message)

    return message


async def get_email_message(
    db: AsyncSession,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
) -> EmailMessage:
    result = await db.execute(
        select(EmailMessage).where(
            EmailMessage.id == message_id,
            EmailMessage.organization_id
            == organization_id,
        )
    )

    message = result.scalar_one_or_none()

    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email message not found.",
        )

    return message


async def list_thread_messages(
    db: AsyncSession,
    organization_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[EmailMessage]:
    await get_email_thread(
        db,
        organization_id,
        thread_id,
    )

    result = await db.execute(
        select(EmailMessage)
        .where(
            EmailMessage.organization_id
            == organization_id,
            EmailMessage.thread_id
            == thread_id,
        )
        .order_by(
            EmailMessage.occurred_at.asc(),
            EmailMessage.created_at.asc(),
        )
        .offset(skip)
        .limit(limit)
    )

    return list(
        result.scalars().all()
    )
