from fastapi import APIRouter, Depends

from app.api.dependencies.auth import enforce_record_permissions
from app.api.routes.accounts import router as accounts_router
from app.api.routes.activities import router as activities_router
from app.api.routes.admin import router as admin_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.auth import router as auth_router
from app.api.routes.oauth import router as oauth_router
from app.api.routes.billing import router as billing_router
from app.api.routes.customization import router as customization_router
from app.api.routes.data import router as data_router
from app.api.routes.enterprise import router as enterprise_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.marketing import router as marketing_router
from app.api.routes.operations import router as operations_router
from app.api.routes.portals import router as portals_router
from app.api.routes.public import router as public_router
from app.api.routes.contacts import router as contacts_router
from app.api.routes.emails import router as emails_router
from app.api.routes.health import router as health_router
from app.api.routes.leads import router as leads_router
from app.api.routes.opportunities import router as opportunities_router
from app.api.routes.organization_members import router as organization_members_router
from app.api.routes.pipelines import router as pipelines_router
from app.api.routes.platform import router as platform_router
from app.api.routes.sales import router as sales_router
from app.api.routes.service import router as service_router
from app.api.routes.tasks import router as tasks_router


api_router = APIRouter()

# Public: health checks and the sign-in / sign-up flows.
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(oauth_router)
api_router.include_router(public_router)

# Core CRM records: every request needs a signed-in member whose role
# allows reading (GET), writing (POST/PATCH/PUT) or deleting (DELETE).
record_guard = [Depends(enforce_record_permissions)]

for record_router in (
    accounts_router,
    contacts_router,
    activities_router,
    leads_router,
    opportunities_router,
    organization_members_router,
    pipelines_router,
    tasks_router,
    emails_router,
):
    api_router.include_router(record_router, dependencies=record_guard)

# Platform modules declare their own fine-grained permissions per endpoint.
api_router.include_router(admin_router)
api_router.include_router(sales_router)
api_router.include_router(service_router)
api_router.include_router(analytics_router)
api_router.include_router(platform_router)

# Plans and billing, and the Starter / Pro / Enterprise feature modules.
# Each endpoint checks its permission and the plan feature it belongs to.
api_router.include_router(billing_router)
api_router.include_router(customization_router)
api_router.include_router(data_router)
api_router.include_router(marketing_router)
api_router.include_router(integrations_router)
api_router.include_router(intelligence_router)
api_router.include_router(operations_router)
api_router.include_router(enterprise_router)
api_router.include_router(portals_router)
