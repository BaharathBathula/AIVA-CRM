
import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.opportunity import Opportunity
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
from app.services.opportunities import (
    lock_active_stage_for_assignment,
)
from app.services.pipelines import deactivate_pipeline_stage


@pytest_asyncio.fixture(autouse=True)
async def isolate_pool():
    await engine.dispose()
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_deactivation_waits_for_stage_assignment():
    async with AsyncSession(engine) as lookup:
        pipelines = (
            await lookup.execute(
                select(Pipeline)
                .where(Pipeline.is_active.is_(True))
                .order_by(Pipeline.id)
            )
        ).scalars().all()

        target = None

        for pipeline in pipelines:
            stages = (
                await lookup.execute(
                    select(PipelineStage)
                    .where(
                        PipelineStage.pipeline_id == pipeline.id,
                        PipelineStage.organization_id
                        == pipeline.organization_id,
                        PipelineStage.is_active.is_(True),
                    )
                    .order_by(PipelineStage.id)
                )
            ).scalars().all()

            if len(stages) < 2:
                continue

            for stage in stages:
                count = (
                    await lookup.execute(
                        select(func.count(Opportunity.id)).where(
                            Opportunity.organization_id
                            == pipeline.organization_id,
                            Opportunity.pipeline_id == pipeline.id,
                            Opportunity.stage_id == stage.id,
                        )
                    )
                ).scalar_one()

                if count == 0:
                    target = (
                        pipeline.organization_id,
                        pipeline.id,
                        stage.id,
                    )
                    break

            if target is not None:
                break

    if target is None:
        pytest.skip(
            "No unused stage in an active multi-stage pipeline."
        )

    organization_id, pipeline_id, stage_id = target

    # Transaction A holds the pipeline and stage row locks.
    async with engine.connect() as connection_a:
        transaction_a = await connection_a.begin()

        try:
            async with AsyncSession(
                bind=connection_a,
                expire_on_commit=False,
            ) as session_a:
                await lock_active_stage_for_assignment(
                    session_a,
                    organization_id,
                    stage_id,
                )

                # Transaction B must not acquire those same locks.
                async with engine.connect() as connection_b:
                    transaction_b = await connection_b.begin()

                    try:
                        await connection_b.execute(
                            text("SET LOCAL lock_timeout = '300ms'")
                        )

                        async with AsyncSession(
                            bind=connection_b,
                            expire_on_commit=False,
                            join_transaction_mode="create_savepoint",
                        ) as session_b:
                            with pytest.raises(Exception) as exc:
                                await deactivate_pipeline_stage(
                                    session_b,
                                    organization_id,
                                    pipeline_id,
                                    stage_id,
                                )

                            # PostgreSQL 55P03 = lock_not_available.
                            original = getattr(
                                exc.value,
                                "orig",
                                None,
                            )

                            sqlstate = getattr(
                                original,
                                "sqlstate",
                                None,
                            )

                            if sqlstate is None:
                                cause = getattr(
                                    exc.value,
                                    "__cause__",
                                    None,
                                )
                                original = getattr(
                                    cause,
                                    "orig",
                                    None,
                                )
                                sqlstate = getattr(
                                    original,
                                    "sqlstate",
                                    None,
                                )

                            assert sqlstate == "55P03", (
                                f"Expected PostgreSQL lock timeout, "
                                f"received {type(exc.value).__name__}: "
                                f"{exc.value}"
                            )

                    finally:
                        await transaction_b.rollback()

        finally:
            await transaction_a.rollback()

    async with AsyncSession(engine) as verification:
        stage = (
            await verification.execute(
                select(PipelineStage).where(
                    PipelineStage.id == stage_id
                )
            )
        ).scalar_one()

        assert stage.is_active is True


@pytest.mark.asyncio
async def test_assignment_rejected_after_deactivation():
    from fastapi import HTTPException

    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                pipelines = (
                    await db.execute(
                        select(Pipeline)
                        .where(Pipeline.is_active.is_(True))
                        .order_by(Pipeline.id)
                    )
                ).scalars().all()

                target = None

                for pipeline in pipelines:
                    stages = (
                        await db.execute(
                            select(PipelineStage)
                            .where(
                                PipelineStage.pipeline_id == pipeline.id,
                                PipelineStage.organization_id
                                == pipeline.organization_id,
                                PipelineStage.is_active.is_(True),
                            )
                            .order_by(PipelineStage.id)
                        )
                    ).scalars().all()

                    if len(stages) < 2:
                        continue

                    for stage in stages:
                        count = (
                            await db.execute(
                                select(func.count(Opportunity.id)).where(
                                    Opportunity.organization_id
                                    == pipeline.organization_id,
                                    Opportunity.pipeline_id == pipeline.id,
                                    Opportunity.stage_id == stage.id,
                                )
                            )
                        ).scalar_one()

                        if count == 0:
                            target = (
                                pipeline.organization_id,
                                pipeline.id,
                                stage.id,
                            )
                            break

                    if target is not None:
                        break

                if target is None:
                    pytest.skip(
                        "No unused stage in an active multi-stage pipeline."
                    )

                organization_id, pipeline_id, stage_id = target

                deactivated = await deactivate_pipeline_stage(
                    db,
                    organization_id,
                    pipeline_id,
                    stage_id,
                )

                assert deactivated.is_active is False

                with pytest.raises(HTTPException) as exc:
                    await lock_active_stage_for_assignment(
                        db,
                        organization_id,
                        stage_id,
                    )

                assert exc.value.status_code == 409

        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_assignment_waits_for_uncommitted_deactivation():
    async with AsyncSession(engine) as lookup:
        pipelines = (
            await lookup.execute(
                select(Pipeline)
                .where(Pipeline.is_active.is_(True))
                .order_by(Pipeline.id)
            )
        ).scalars().all()

        target = None

        for pipeline in pipelines:
            stages = (
                await lookup.execute(
                    select(PipelineStage)
                    .where(
                        PipelineStage.pipeline_id == pipeline.id,
                        PipelineStage.organization_id
                        == pipeline.organization_id,
                        PipelineStage.is_active.is_(True),
                    )
                )
            ).scalars().all()

            if len(stages) < 2:
                continue

            for stage in stages:
                count = (
                    await lookup.execute(
                        select(func.count(Opportunity.id)).where(
                            Opportunity.organization_id
                            == pipeline.organization_id,
                            Opportunity.pipeline_id == pipeline.id,
                            Opportunity.stage_id == stage.id,
                        )
                    )
                ).scalar_one()

                if count == 0:
                    target = (
                        pipeline.organization_id,
                        pipeline.id,
                        stage.id,
                    )
                    break

            if target:
                break

    if target is None:
        pytest.skip("No unused stage available.")

    organization_id, pipeline_id, stage_id = target

    async with engine.connect() as connection_a:
        transaction_a = await connection_a.begin()

        try:
            async with AsyncSession(
                bind=connection_a,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as session_a:
                stage = await deactivate_pipeline_stage(
                    session_a,
                    organization_id,
                    pipeline_id,
                    stage_id,
                )

                assert stage.is_active is False

                async with engine.connect() as connection_b:
                    transaction_b = await connection_b.begin()

                    try:
                        await connection_b.execute(
                            text("SET LOCAL lock_timeout = '300ms'")
                        )

                        async with AsyncSession(
                            bind=connection_b,
                            expire_on_commit=False,
                            join_transaction_mode="create_savepoint",
                        ) as session_b:
                            with pytest.raises(Exception) as exc:
                                await lock_active_stage_for_assignment(
                                    session_b,
                                    organization_id,
                                    stage_id,
                                )

                            error = exc.value
                            sqlstate = None

                            for candidate in (
                                error,
                                getattr(error, "orig", None),
                                getattr(error, "__cause__", None),
                                getattr(
                                    getattr(error, "__cause__", None),
                                    "orig",
                                    None,
                                ),
                            ):
                                value = getattr(
                                    candidate,
                                    "sqlstate",
                                    None,
                                )
                                if value:
                                    sqlstate = value
                                    break

                            assert sqlstate == "55P03", (
                                f"Expected PostgreSQL lock timeout, "
                                f"received {type(error).__name__}: {error}"
                            )

                    finally:
                        await transaction_b.rollback()

        finally:
            await transaction_a.rollback()

    async with AsyncSession(engine) as verification:
        restored = (
            await verification.execute(
                select(PipelineStage).where(
                    PipelineStage.id == stage_id
                )
            )
        ).scalar_one()

        assert restored.is_active is True


@pytest.mark.asyncio
async def test_assignment_rejected_after_committed_deactivation():
    from fastapi import HTTPException

    async with engine.connect() as connection:
        outer_transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                pipelines = (
                    await db.execute(
                        select(Pipeline)
                        .where(Pipeline.is_active.is_(True))
                        .order_by(Pipeline.id)
                    )
                ).scalars().all()

                target = None

                for pipeline in pipelines:
                    stages = (
                        await db.execute(
                            select(PipelineStage).where(
                                PipelineStage.pipeline_id == pipeline.id,
                                PipelineStage.organization_id
                                == pipeline.organization_id,
                                PipelineStage.is_active.is_(True),
                            )
                        )
                    ).scalars().all()

                    if len(stages) < 2:
                        continue

                    for stage in stages:
                        count = (
                            await db.execute(
                                select(func.count(Opportunity.id)).where(
                                    Opportunity.organization_id
                                    == pipeline.organization_id,
                                    Opportunity.pipeline_id == pipeline.id,
                                    Opportunity.stage_id == stage.id,
                                )
                            )
                        ).scalar_one()

                        if count == 0:
                            target = (
                                pipeline.organization_id,
                                pipeline.id,
                                stage.id,
                            )
                            break

                    if target is not None:
                        break

                if target is None:
                    pytest.skip("No unused stage available.")

                organization_id, pipeline_id, stage_id = target

                updated = await deactivate_pipeline_stage(
                    db,
                    organization_id,
                    pipeline_id,
                    stage_id,
                )

                assert updated.is_active is False

                # The service committed its session savepoint.
                # The outer transaction remains open for rollback.
                with pytest.raises(HTTPException) as exc:
                    await lock_active_stage_for_assignment(
                        db,
                        organization_id,
                        stage_id,
                    )

                assert exc.value.status_code == 409

        finally:
            await outer_transaction.rollback()


@pytest.mark.asyncio
async def test_committed_deactivation_rejected_by_fresh_session():
    import uuid
    from fastapi import HTTPException
    from sqlalchemy import delete

    test_id = uuid.uuid4()
    organization_id = None
    pipeline_id = uuid.uuid4()
    first_stage_id = uuid.uuid4()
    second_stage_id = uuid.uuid4()

    # Reuse an existing organization without changing its records.
    async with AsyncSession(engine) as lookup:
        organization_id = (
            await lookup.execute(
                select(Pipeline.organization_id).limit(1)
            )
        ).scalar_one_or_none()

    if organization_id is None:
        pytest.skip("No organization available for test isolation.")

    try:
        async with AsyncSession(
            engine,
            expire_on_commit=False,
        ) as setup:
            setup.add(
                Pipeline(
                    id=pipeline_id,
                    organization_id=organization_id,
                    name=f"Concurrency Test {test_id}",
                    is_default=False,
                    is_active=True,
                )
            )

            setup.add_all(
                [
                    PipelineStage(
                        id=first_stage_id,
                        organization_id=organization_id,
                        pipeline_id=pipeline_id,
                        name="Open",
                        position=1,
                        probability=10,
                        category="open",
                        is_active=True,
                    ),
                    PipelineStage(
                        id=second_stage_id,
                        organization_id=organization_id,
                        pipeline_id=pipeline_id,
                        name="Qualified",
                        position=2,
                        probability=30,
                        category="open",
                        is_active=True,
                    ),
                ]
            )

            await setup.commit()

        async with AsyncSession(
            engine,
            expire_on_commit=False,
        ) as deactivation:
            updated = await deactivate_pipeline_stage(
                deactivation,
                organization_id,
                pipeline_id,
                second_stage_id,
            )

            assert updated.is_active is False

        # This session uses a new transaction after commit.
        async with AsyncSession(engine) as assignment:
            with pytest.raises(HTTPException) as exc:
                await lock_active_stage_for_assignment(
                    assignment,
                    organization_id,
                    second_stage_id,
                )

            assert exc.value.status_code == 409

        async with AsyncSession(engine) as verification:
            result = await verification.execute(
                select(PipelineStage.is_active).where(
                    PipelineStage.id == second_stage_id
                )
            )

            assert result.scalar_one() is False

    finally:
        # Delete only the pipeline created by this test.
        # The pipeline-stage FK uses ON DELETE CASCADE.
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(
                delete(Pipeline).where(
                    Pipeline.id == pipeline_id,
                    Pipeline.organization_id == organization_id,
                )
            )
            await cleanup.commit()
