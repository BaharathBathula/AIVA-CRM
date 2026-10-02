from fastapi import APIRouter

from app.api.routes.accounts import (
    router as accounts_router,
)
from app.api.routes.activities import (
    router as activities_router,
)
from app.api.routes.contacts import (
    router as contacts_router,
)
from app.api.routes.health import (
    router as health_router,
)


api_router = APIRouter()

api_router.include_router(
    health_router
)

api_router.include_router(
    accounts_router
)

api_router.include_router(
    contacts_router
)

api_router.include_router(
    activities_router
)
