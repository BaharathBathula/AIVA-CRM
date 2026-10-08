import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.opportunity import Opportunity
from app.services.opportunity_bulk_forecast import (
    bulk_change_opportunity_forecast,
)


@pytest.mark.asyncio
async def test_bulk_forecast_postgres_transaction():
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
                    pytest.skip("No opportunities available.")

                opportunity_id = opportunity.id
                organization_id = opportunity.organization_id
                original_category = opportunity.forecast_category

                target_category = (
                    "commit"
                    if original_category != "commit"
                    else "best_case"
                )

                response = await bulk_change_opportunity_forecast(
                    db=db,
                    organization_id=organization_id,
                    opportunity_ids=[opportunity_id],
                    forecast_category=target_category,
                )

                assert response["updated_count"] == 1
                assert response["forecast_category"] == target_category

                await db.refresh(opportunity)

                assert opportunity.forecast_category == target_category

                print("\nPASS: PostgreSQL forecast update")
                print("PASS: Forecast persisted inside transaction")

        finally:
            await transaction.rollback()

    async with AsyncSession(engine) as verification_db:
        original = await verification_db.get(
            Opportunity,
            opportunity_id,
        )

        assert original is not None
        assert original.forecast_category == original_category

    print("PASS: Original forecast restored after rollback")
