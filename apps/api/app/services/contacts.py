import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.schemas.contact import (
    ContactCreate,
    ContactUpdate,
)


def normalize_contact_text(
    value: str | None,
) -> str:
    if not value:
        return ""

    return " ".join(
        value.strip().lower().split()
    )


def normalize_contact_email(
    value: str | None,
) -> str:
    return normalize_contact_text(
        value
    )


def normalize_contact_tags(
    values: list[str] | None,
) -> list[str]:
    if not values:
        return []

    normalized: list[str] = []

    seen: set[str] = set()

    for value in values:
        tag = (
            " ".join(
                value.strip().split()
            )
        )

        if not tag:
            continue

        key = tag.lower()

        if key in seen:
            continue

        seen.add(
            key
        )

        normalized.append(
            tag
        )

    return normalized


def normalize_contact_phone(
    value: str | None,
) -> str:
    if not value:
        return ""

    return "".join(
        character
        for character in value
        if character.isdigit()
    )


async def validate_contact_account(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Account:
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.organization_id == organization_id,
        )
    )

    account = result.scalar_one_or_none()

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Account does not exist "
                "in this organization."
            ),
        )

    if account.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Contacts cannot be assigned "
                "to an archived account."
            ),
        )

    return account


async def clear_other_primary_contacts(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    exclude_contact_id: uuid.UUID | None = None,
) -> None:
    statement = select(Contact).where(
        Contact.organization_id == organization_id,
        Contact.account_id == account_id,
        Contact.is_primary.is_(True),
    )

    if exclude_contact_id is not None:
        statement = statement.where(
            Contact.id != exclude_contact_id
        )

    result = await db.execute(
        statement
    )

    for contact in result.scalars().all():
        contact.is_primary = False


async def create_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    payload: ContactCreate,
) -> Contact:
    if payload.account_id is not None:
        await validate_contact_account(
            db,
            organization_id,
            payload.account_id,
        )

    if (
        payload.is_primary
        and
        payload.account_id is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A primary contact must "
                "belong to an account."
            ),
        )

    if (
        payload.is_primary
        and
        payload.account_id is not None
    ):
        await clear_other_primary_contacts(
            db,
            organization_id,
            payload.account_id,
        )

    contact_data = (
        payload.model_dump()
    )

    contact_data["tags"] = (
        normalize_contact_tags(
            payload.tags
        )
    )

    if payload.segment is not None:
        contact_data["segment"] = (
            payload.segment.strip()
            or None
        )

    contact = Contact(
        organization_id=
            organization_id,
        **contact_data,
    )

    db.add(contact)

    await db.commit()
    await db.refresh(contact)

    return contact


async def list_contacts(
    db: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    is_primary: bool | None = None,
    include_archived: bool = False,
    segment: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[Contact]:
    statement = select(Contact).where(
        Contact.organization_id
        == organization_id
    )


    if not include_archived:
        statement = statement.where(
            Contact.is_archived.is_(
                False
            )
        )

    if account_id is not None:
        await validate_contact_account(
            db,
            organization_id,
            account_id,
        )

        statement = statement.where(
            Contact.account_id
            == account_id
        )

    if is_active is not None:
        statement = statement.where(
            Contact.is_active.is_(
                is_active
            )
        )

    if is_primary is not None:
        statement = statement.where(
            Contact.is_primary.is_(
                is_primary
            )
        )


    if segment:
        statement = statement.where(
            Contact.segment
            ==
            segment.strip()
        )

    if tag:
        statement = statement.where(
            Contact.tags.contains(
                [
                    tag.strip()
                ]
            )
        )

    if search:
        normalized_search = (
            search.strip().lower()
        )

        if normalized_search:
            pattern = (
                f"%{normalized_search}%"
            )

            statement = statement.where(
                or_(
                    func.lower(
                        Contact.first_name
                    ).like(pattern),
                    func.lower(
                        Contact.last_name
                    ).like(pattern),
                    func.lower(
                        Contact.email
                    ).like(pattern),
                    func.lower(
                        Contact.job_title
                    ).like(pattern),
                    func.lower(
                        Contact.department
                    ).like(pattern),
                )
            )

    statement = (
        statement
        .order_by(
            Contact.last_name.asc(),
            Contact.first_name.asc(),
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


async def check_contact_duplicates(
    db: AsyncSession,
    organization_id: uuid.UUID,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    mobile: str | None = None,
    account_id: uuid.UUID | None = None,
    exclude_contact_id: uuid.UUID | None = None,
) -> list[dict]:
    normalized_first_name = (
        normalize_contact_text(
            first_name
        )
    )

    normalized_last_name = (
        normalize_contact_text(
            last_name
        )
    )

    normalized_email = (
        normalize_contact_email(
            email
        )
    )

    normalized_phone = (
        normalize_contact_phone(
            phone
        )
    )

    normalized_mobile = (
        normalize_contact_phone(
            mobile
        )
    )

    if not any(
        [
            normalized_email,
            normalized_phone,
            normalized_mobile,
            (
                normalized_first_name
                and
                normalized_last_name
            ),
        ]
    ):
        return []

    statement = select(
        Contact
    ).where(
        Contact.organization_id
        ==
        organization_id
    )

    if exclude_contact_id is not None:
        statement = statement.where(
            Contact.id
            !=
            exclude_contact_id
        )

    result = await db.execute(
        statement
    )

    matches: list[dict] = []

    for candidate in result.scalars().all():
        reasons: list[str] = []

        confidence = "medium"

        candidate_email = (
            normalize_contact_email(
                candidate.email
            )
        )

        candidate_phone = (
            normalize_contact_phone(
                candidate.phone
            )
        )

        candidate_mobile = (
            normalize_contact_phone(
                candidate.mobile
            )
        )

        candidate_first_name = (
            normalize_contact_text(
                candidate.first_name
            )
        )

        candidate_last_name = (
            normalize_contact_text(
                candidate.last_name
            )
        )

        same_email = (
            bool(normalized_email)
            and
            candidate_email
            ==
            normalized_email
        )

        requested_phones = {
            value
            for value in [
                normalized_phone,
                normalized_mobile,
            ]
            if value
        }

        candidate_phones = {
            value
            for value in [
                candidate_phone,
                candidate_mobile,
            ]
            if value
        }

        same_phone = bool(
            requested_phones
            &
            candidate_phones
        )

        same_name = (
            bool(
                normalized_first_name
            )
            and
            bool(
                normalized_last_name
            )
            and
            candidate_first_name
            ==
            normalized_first_name
            and
            candidate_last_name
            ==
            normalized_last_name
        )

        same_account = (
            account_id is not None
            and
            candidate.account_id
            ==
            account_id
        )

        if same_email:
            reasons.append(
                "Same email address"
            )

            confidence = "high"

        if same_phone:
            reasons.append(
                "Same phone or mobile number"
            )

            confidence = "high"

        if (
            same_name
            and
            same_account
        ):
            reasons.append(
                "Same name in the same account"
            )

            confidence = "high"

        elif same_name:
            reasons.append(
                "Same first and last name"
            )

        if not reasons:
            continue

        matches.append(
            {
                "contact": candidate,
                "confidence": confidence,
                "reasons": reasons,
            }
        )

    matches.sort(
        key=lambda match: (
            0
            if match[
                "confidence"
            ] == "high"
            else 1,
            (
                match[
                    "contact"
                ].last_name.lower()
            ),
            (
                match[
                    "contact"
                ].first_name.lower()
            ),
        )
    )

    return matches


async def get_contact(
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

    contact = (
        result.scalar_one_or_none()
    )

    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found.",
        )

    return contact


async def archive_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
) -> Contact:
    contact = await get_contact(
        db,
        organization_id,
        contact_id,
    )

    if contact.is_archived:
        return contact

    contact.is_archived = True
    contact.archived_at = (
        datetime.now(
            timezone.utc
        )
    )

    # An archived CRM record must
    # never remain the account's
    # primary operational contact.
    contact.is_primary = False

    await db.commit()
    await db.refresh(contact)

    return contact


async def reactivate_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
) -> Contact:
    contact = await get_contact(
        db,
        organization_id,
        contact_id,
    )

    contact.is_archived = False
    contact.archived_at = None

    await db.commit()
    await db.refresh(contact)

    return contact


async def update_contact(
    db: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    payload: ContactUpdate,
) -> Contact:
    contact = await get_contact(
        db,
        organization_id,
        contact_id,
    )

    updates = payload.model_dump(
        exclude_unset=True
    )


    if "tags" in updates:
        updates["tags"] = (
            normalize_contact_tags(
                updates["tags"]
            )
        )

    if (
        "segment" in updates
        and
        updates["segment"] is not None
    ):
        updates["segment"] = (
            updates["segment"].strip()
            or None
        )

    if (
        "first_name" in updates
        and
        updates["first_name"] is None
    ):
        raise HTTPException(
            status_code=
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "First name cannot be null."
            ),
        )

    if (
        "last_name" in updates
        and
        updates["last_name"] is None
    ):
        raise HTTPException(
            status_code=
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Last name cannot be null."
            ),
        )

    if (
        "account_id" in updates
        and
        updates["account_id"] is not None
    ):
        await validate_contact_account(
            db,
            organization_id,
            updates["account_id"],
        )

    if (
        "account_id" in updates
        and
        updates["account_id"] is None
        and
        contact.is_primary
        and
        "is_primary" not in updates
    ):
        updates["is_primary"] = False

    final_account_id = (
        updates["account_id"]
        if "account_id" in updates
        else contact.account_id
    )

    final_is_primary = (
        updates["is_primary"]
        if "is_primary" in updates
        else contact.is_primary
    )

    final_is_active = (
        updates["is_active"]
        if "is_active" in updates
        else contact.is_active
    )

    if not final_is_active:
        updates["is_primary"] = False
        final_is_primary = False

    if (
        final_is_primary
        and
        final_account_id is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A primary contact must "
                "belong to an account."
            ),
        )

    if (
        final_is_primary
        and
        final_account_id is not None
    ):
        await clear_other_primary_contacts(
            db,
            organization_id,
            final_account_id,
            exclude_contact_id=contact.id,
        )

    for field, value in updates.items():
        setattr(
            contact,
            field,
            value,
        )

    await db.commit()
    await db.refresh(contact)

    return contact