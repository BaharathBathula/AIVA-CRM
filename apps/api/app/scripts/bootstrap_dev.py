import asyncio
import uuid

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.pipeline import Pipeline
from app.models.pipeline_stage import PipelineStage
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

DEV_PIPELINE_ID = uuid.UUID(
    "40000000-0000-0000-0000-000000000001"
)


DEFAULT_STAGES = [
    {
        "id": uuid.UUID(
            "41000000-0000-0000-0000-000000000001"
        ),
        "name": "Qualification",
        "position": 1,
        "probability": 10,
        "category": "open",
    },
    {
        "id": uuid.UUID(
            "41000000-0000-0000-0000-000000000002"
        ),
        "name": "Discovery",
        "position": 2,
        "probability": 25,
        "category": "open",
    },
    {
        "id": uuid.UUID(
            "41000000-0000-0000-0000-000000000003"
        ),
        "name": "Demo",
        "position": 3,
        "probability": 40,
        "category": "open",
    },
    {
        "id": uuid.UUID(
            "41000000-0000-0000-0000-000000000004"
        ),
        "name": "Proposal",
        "position": 4,
        "probability": 60,
        "category": "open",
    },
    {
        "id": uuid.UUID(
            "41000000-0000-0000-0000-000000000005"
        ),
        "name": "Negotiation",
        "position": 5,
        "probability": 80,
        "category": "open",
    },
    {
        "id": uuid.UUID(
            "41000000-0000-0000-0000-000000000006"
        ),
        "name": "Closed Won",
        "position": 6,
        "probability": 100,
        "category": "won",
    },
    {
        "id": uuid.UUID(
            "41000000-0000-0000-0000-000000000007"
        ),
        "name": "Closed Lost",
        "position": 7,
        "probability": 0,
        "category": "lost",
    },
]


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

        membership = result.scalar_one_or_none()

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

        pipeline_result = await db.execute(
            select(Pipeline).where(
                Pipeline.organization_id
                == DEV_ORGANIZATION_ID,
                Pipeline.name
                == "Default Sales Pipeline",
            )
        )

        pipeline = pipeline_result.scalar_one_or_none()

        if pipeline is None:
            pipeline = Pipeline(
                id=DEV_PIPELINE_ID,
                organization_id=DEV_ORGANIZATION_ID,
                name="Default Sales Pipeline",
                is_default=True,
                is_active=True,
            )

            db.add(pipeline)

            await db.flush()
        else:
            pipeline.is_default = True
            pipeline.is_active = True

        for stage_data in DEFAULT_STAGES:
            stage_result = await db.execute(
                select(PipelineStage).where(
                    PipelineStage.pipeline_id
                    == pipeline.id,
                    PipelineStage.position
                    == stage_data["position"],
                )
            )

            stage = stage_result.scalar_one_or_none()

            if stage is None:
                stage = PipelineStage(
                    id=stage_data["id"],
                    organization_id=DEV_ORGANIZATION_ID,
                    pipeline_id=pipeline.id,
                    name=stage_data["name"],
                    position=stage_data["position"],
                    probability=stage_data["probability"],
                    category=stage_data["category"],
                    is_active=True,
                )

                db.add(stage)
            else:
                stage.name = stage_data["name"]
                stage.probability = stage_data["probability"]
                stage.category = stage_data["category"]
                stage.is_active = True

        await db.commit()

    print(
        "AIVA development workspace ready."
    )

    print(
        f"Organization ID: {DEV_ORGANIZATION_ID}"
    )

    print(
        f"User ID: {DEV_USER_ID}"
    )

    print(
        f"Pipeline ID: {DEV_PIPELINE_ID}"
    )

    print(
        "Default sales pipeline ready."
    )


if __name__ == "__main__":
    asyncio.run(
        bootstrap()
    )
