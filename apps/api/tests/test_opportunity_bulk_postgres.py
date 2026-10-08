import uuid

import pytest
from sqlalchemy import select

from app.db.session import (
    AsyncSessionLocal,
    engine,
)
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.user import User


@pytest.fixture(scope="module", autouse=True)
async def cleanup_database_connections():
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_bulk_owner_postgres_transaction():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Opportunity).limit(1)
        )

        opportunity = result.scalar_one_or_none()

        if opportunity is None:
            pytest.skip(
                "No development opportunity available."
            )

        opportunity_id = opportunity.id
        organization_id = opportunity.organization_id
        original_owner_id = opportunity.owner_user_id

        result = await db.execute(
            select(User.id)
            .join(
                OrganizationMembership,
                OrganizationMembership.user_id == User.id,
            )
            .where(
                User.is_active.is_(True),
                OrganizationMembership.organization_id
                == organization_id,
            )
            .limit(1)
        )

        owner_id = result.scalar_one_or_none()

        if owner_id is None:
            pytest.skip(
                "No active organization member available."
            )

        await db.rollback()

        try:
            result = await db.execute(
                select(Opportunity)
                .where(
                    Opportunity.id == opportunity_id,
                    Opportunity.organization_id == organization_id,
                )
                .with_for_update()
            )

            locked = result.scalar_one()
            locked.owner_user_id = owner_id

            await db.flush()

            assert locked.owner_user_id == owner_id

        finally:
            await db.rollback()

        db.expire_all()

        result = await db.execute(
            select(Opportunity).where(
                Opportunity.id == opportunity_id
            )
        )

        restored = result.scalar_one()

        assert restored.owner_user_id == original_owner_id

        await db.rollback()


@pytest.mark.asyncio
async def test_bulk_owner_rejects_foreign_organization():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Opportunity).limit(1)
        )

        opportunity = result.scalar_one_or_none()

        if opportunity is None:
            pytest.skip(
                "No development opportunity available."
            )

        result = await db.execute(
            select(Opportunity).where(
                Opportunity.id == opportunity.id,
                Opportunity.organization_id == uuid.uuid4(),
            )
        )

        assert result.scalar_one_or_none() is None

        await db.rollback()
