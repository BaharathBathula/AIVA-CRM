from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db.session import engine
from app.infrastructure.redis import redis_client


router = APIRouter(
    prefix="/health",
    tags=["System"],
)


@router.get("")
async def health():
    """
    Basic liveness check.

    This endpoint only confirms that the FastAPI application
    is running.
    """
    return {
        "status": "ok",
        "service": "aiva-crm-api",
    }


@router.get("/ready")
async def readiness():
    """
    Readiness check.

    Verifies that critical infrastructure dependencies
    are reachable before declaring the API ready.
    """

    database_status = "ok"
    redis_status = "ok"

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"

    if redis_client is None:
        redis_status = "not_configured"
    else:
        try:
            await redis_client.ping()
        except Exception:
            redis_status = "unavailable"

    ready = (
        database_status == "ok"
        and redis_status in {"ok", "not_configured"}
    )

    response = {
        "status": "ready" if ready else "not_ready",
        "dependencies": {
            "postgresql": database_status,
            "redis": redis_status,
        },
    }

    if not ready:
        raise HTTPException(
            status_code=503,
            detail=response,
        )

    return response
