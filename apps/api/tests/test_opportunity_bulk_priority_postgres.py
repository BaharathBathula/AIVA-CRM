import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.opportunity import Opportunity
from app.services.opportunity_bulk_priority import (
    bulk_change_opportunity_priority,
)


@pytest.mark.asyncio
async def test_bulk_priority_postgres_transaction():
    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                result = await db.execute(
                    select(Opportunity)
                    .order_by(Opportunity.id)
                    .limit(1)
                )

                opportunity = result.scalar_one_or_none()

                if opportunity is None:
                    pytest.skip(
                        "No opportunities available."
                    )

                opportunity_id = opportunity.id
                organization_id = opportunity.organization_id
                original_priority = opportunity.priority

                target_priority = (
                    "critical"
                    if original_priority != "critical"
                    else "low"
                )

                response = await bulk_change_opportunity_priority(
                    db=db,
                    organization_id=organization_id,
                    opportunity_ids=[opportunity_id],
                    priority=target_priority,
                )

                assert response["updated_count"] == 1
                assert response["priority"] == target_priority

                await db.refresh(opportunity)
                assert opportunity.priority == target_priority

                print(
                    "\nPASS: PostgreSQL priority update"
                )
                print(
                    "PASS: Priority persisted inside transaction"
                )

        finally:
            await transaction.rollback()

    # Verify the original priority remains after rollback.
    async with AsyncSession(engine) as verification_db:
        original = await verification_db.get(
            Opportunity,
            opportunity_id,
        )

        assert original is not None
        assert original.priority == original_priority

    print("PASS: Original priority restored after rollback")
