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
from app.api.routes.emails import (
    router as emails_router,
)
from app.api.routes.health import (
    router as health_router,
)
from app.api.routes.leads import (
    router as leads_router,
)
from app.api.routes.opportunities import (
    router as opportunities_router,
)
from app.api.routes.pipelines import (
    router as pipelines_router,
)
from app.api.routes.tasks import (
    router as tasks_router,
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

api_router.include_router(
    leads_router
)

api_router.include_router(
    opportunities_router
)

api_router.include_router(
    pipelines_router
)

api_router.include_router(
    tasks_router
)

api_router.include_router(
    emails_router
)