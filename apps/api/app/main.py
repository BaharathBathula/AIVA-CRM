from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.router import api_router
from app.core.config import get_settings
from app.db.session import engine
from app.infrastructure.redis import redis_client

# Registers the audit / workflow / lead-scoring flush hook.
import app.services.automation  # noqa: E402,F401
from app.services.automation import AutomationBlocked
from app.services.scheduler import scheduler


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.scheduler_enabled and settings.environment != "test":
        scheduler.start()
    yield

    await scheduler.stop()
    await engine.dispose()
    if redis_client is not None:
        await redis_client.aclose()


app = FastAPI(
    title=settings.app_name,
    description=(
        "Backend API for AIVA CRM — an AI-native autonomous "
        "customer relationship management platform."
    ),
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Friendly messages for unique constraints, instead of a 500.
CONFLICTS = {
    "uq_sla_policies_org_name": "An SLA policy with that name already exists.",
    "uq_teams_org_name": "A team with that name already exists.",
    "uq_roles_org_key": "A role with that name already exists.",
    "uq_products_org_sku": "Another product already uses that SKU.",
    "uq_kb_articles_org_slug": "An article with that title already exists.",
    "uq_forecast_quotas_user_period": "That quota was just changed by someone else. Refresh and try again.",
    "uq_custom_fields_key": "A field with that name already exists.",
    "uq_custom_modules_key": "A module with that name already exists.",
    "uq_target_accounts_account": "That account is already a target account.",
    "uq_team_members_user": "That person is already on the team.",
    "uq_portal_users_email": "That person already has access to this portal.",
    "uq_journey_enrollments_record": "That record is already in this journey.",
    "uq_stage_rules_stage": "That stage already has a playbook.",
}


@app.exception_handler(AutomationBlocked)
async def automation_blocked_handler(_: Request, exc: AutomationBlocked):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code, **exc.extra})


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_: Request, exc: IntegrityError):
    text = str(exc.orig)
    detail = next((message for name, message in CONFLICTS.items() if name in text), None)
    if detail is None:
        detail = "That conflicts with an existing record." if "unique" in text.lower() or "duplicate" in text.lower() else (
            "That change refers to a record that doesn't exist."
        )
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": detail})


app.include_router(
    api_router,
    prefix=settings.api_v1_prefix,
)


@app.get("/", tags=["System"])
async def root():
    return {
        "name": "AIVA CRM API",
        "version": "0.2.0",
        "status": "running",
        "docs": "/docs",
    }
