import asyncio
import uuid

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.user import User


DEV_ORGANIZATION_ID = uuid.UUID(
    "10000000-0000-0000-0000-000000000001"
)

DEV_USER_ID = uuid.UUID(
    "20000000-0000-0000-0000-000000000001"
)

DEV_MEMBERSHIP_ID = uuid.UUID(
    "30000000-0000-0000-0000-000000000001"
)


async def bootstrap() -> None:
    async with AsyncSessionLocal() as db:
        organization = await db.get(
            Organization,
            DEV_ORGANIZATION_ID,
        )

        if organization is None:
            organization = Organization(
                id=DEV_ORGANIZATION_ID,
                name="AIVA Development",
                slug="aiva-development",
                is_active=True,
            )

            db.add(organization)
        else:
            organization.is_active = True

        user = await db.get(
            User,
            DEV_USER_ID,
        )

        if user is None:
            user = User(
                id=DEV_USER_ID,
                email="admin@aiva.local",
                full_name="AIVA Administrator",
                password_hash=None,
                is_active=True,
            )

            db.add(user)
        else:
            user.is_active = True

        await db.flush()

        result = await db.execute(
            select(
                OrganizationMembership
            ).where(
                OrganizationMembership.organization_id
                == DEV_ORGANIZATION_ID,
                OrganizationMembership.user_id
                == DEV_USER_ID,
            )
        )

        membership = (
            result.scalar_one_or_none()
        )

        if membership is None:
            membership = OrganizationMembership(
                id=DEV_MEMBERSHIP_ID,
                organization_id=DEV_ORGANIZATION_ID,
                user_id=DEV_USER_ID,
                role="owner",
            )

            db.add(membership)
        else:
            membership.role = "owner"

        await db.commit()

    print(
        "AIVA development workspace ready."
    )

    print(
        f"Organization ID: "
        f"{DEV_ORGANIZATION_ID}"
    )

    print(
        f"User ID: {DEV_USER_ID}"
    )


if __name__ == "__main__":
    asyncio.run(
        bootstrap()
    )
