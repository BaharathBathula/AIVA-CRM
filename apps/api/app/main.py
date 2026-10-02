from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.db.session import engine
from app.infrastructure.redis import redis_client


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield

    await engine.dispose()
    await redis_client.aclose()


app = FastAPI(
    title=settings.app_name,
    description=(
        "Backend API for AIVA CRM — an AI-native autonomous "
        "customer relationship management platform."
    ),
    version="0.1.0",
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


app.include_router(
    api_router,
    prefix=settings.api_v1_prefix,
)


@app.get("/", tags=["System"])
async def root():
    return {
        "name": "AIVA CRM API",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
    }
