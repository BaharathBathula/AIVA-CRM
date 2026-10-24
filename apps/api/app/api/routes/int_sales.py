"""Integration endpoints: e-signature and sales intelligence (Adobe Acrobat Sign,
PandaDoc, Apollo.io, ZoomInfo, LinkedIn Lead Gen Forms).

* ``/pandadoc/templates``: the workspace's PandaDoc templates and their roles,
  tokens and pricing tables (for the send dialog and the default quote
  template). Sending itself goes through ``/e-signatures`` (int_documents).
* ``/adobe-sign/webhook``, ``/pandadoc/webhook``: register the status webhook
  again (admins).
* ``/prospects``: search Apollo or ZoomInfo for people and companies and import
  them as leads, contacts or accounts.
* ``/linkedin-leads``: the connected member's ad accounts, which ones sync, and
  "sync now".

Inbound webhooks arrive through the integrations hub.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import adobe_sign, lead_capture, linkedin_leads, pandadoc, sales_intel
from app.services.automation import record_audit

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

SIGN = [Depends(require_feature("e_signature"))]
INTEL = [Depends(require_feature("waterfall_enrichment"))]
LEADS = [Depends(require_feature("web_forms"))]


async def _fail(db: AsyncSession, exc: IntegrationError) -> HTTPException:
    """Keep what the attempt recorded (activity log, last error) and report the error."""

    await db.commit()
    return HTTPException(exc.status_code, str(exc))


async def _detail(key: str, principal: Principal, db: AsyncSession) -> dict[str, Any]:
    from app.api.routes.integration_hub import integration_detail

    return await integration_detail(key, principal, db)


# ===========================================================================
# PandaDoc templates and webhooks
# ===========================================================================


@router.get("/pandadoc/templates", dependencies=SIGN)
async def pandadoc_templates(
    search: str | None = Query(default=None, max_length=100),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        connection = await core.active_connection(db, principal.organization_id, pandadoc.KEY)
        items = await pandadoc.templates(connection, search)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    config = await core.workspace_config(db, principal.organization_id, pandadoc.KEY)
    return {"items": items, "default_quote_template_id": config.get("quote_template_id")}


@router.get("/pandadoc/templates/{template_id}", dependencies=SIGN)
async def pandadoc_template(template_id: str, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    if len(template_id) > 100 or not template_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    try:
        connection = await core.active_connection(db, principal.organization_id, pandadoc.KEY)
        details = await pandadoc.template_details(connection, template_id)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return details


@router.post("/pandadoc/webhook", dependencies=SIGN)
async def pandadoc_resubscribe(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    try:
        connection = await core.active_connection(db, principal.organization_id, pandadoc.KEY)
        if not pandadoc._push_url(connection):
            raise IntegrationError("PandaDoc can only notify AIVA on a public https address; until then AIVA checks documents every 15 minutes.", 409)
        await pandadoc.subscribe(db, connection)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _detail(pandadoc.KEY, principal, db)


@router.post("/adobe-sign/webhook", dependencies=SIGN)
async def adobe_sign_register(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    try:
        connection = await core.active_connection(db, principal.organization_id, adobe_sign.KEY)
        if not adobe_sign._push_url(connection):
            raise IntegrationError("Acrobat Sign can only notify AIVA on a public https address; until then AIVA checks agreements every 15 minutes.", 409)
        await adobe_sign.register_webhook(db, connection)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _detail(adobe_sign.KEY, principal, db)


# ===========================================================================
# Prospects: Apollo.io and ZoomInfo
# ===========================================================================


ProviderKey = Literal["apollo", "zoominfo"]


@router.get("/prospects/providers", dependencies=INTEL)
async def prospect_providers(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Which prospect sources are on and connected (cheap: no provider calls)."""

    out = []
    for key in sales_intel.KEYS:
        enabled = await core.is_enabled(db, principal.organization_id, key)
        connection = await core.connection_for(db, principal.organization_id, key) if enabled else None
        out.append({"key": key, "name": sales_intel.VENDOR[key], "enabled": enabled, "connected": bool(connection and connection.status == "connected")})
    return {
        "items": out,
        "can_import": principal.can("records.write"),
        "can_assign_owner": principal.can("data.import"),
        "max_import": sales_intel.MAX_IMPORT,
        "seniorities": list(sales_intel.SENIORITIES),
        "employee_ranges": list(sales_intel.EMPLOYEE_RANGES),
    }


class SearchPayload(BaseModel):
    kind: Literal["people", "companies"] = "people"
    keywords: str | None = Field(default=None, max_length=200)
    titles: list[str] = Field(default_factory=list, max_length=10)
    seniorities: list[str] = Field(default_factory=list, max_length=11)
    locations: list[str] = Field(default_factory=list, max_length=10)
    company_locations: list[str] = Field(default_factory=list, max_length=10)
    industries: list[str] = Field(default_factory=list, max_length=10)
    employee_ranges: list[str] = Field(default_factory=list, max_length=8)
    company_name: str | None = Field(default=None, max_length=200)
    domains: list[str] = Field(default_factory=list, max_length=20)
    page: int = Field(default=1, ge=1, le=100)


@router.post("/prospects/{provider}/search", dependencies=INTEL)
async def prospect_search(provider: ProviderKey, payload: SearchPayload, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    if principal.api_key_id is None and principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support access can't search for prospects.")
    try:
        filters = sales_intel.clean_filters(payload.model_dump())
        if payload.kind == "people" and not any(filters[k] for k in ("keywords", "titles", "seniorities", "locations", "company_locations", "employee_ranges", "domains")):
            raise IntegrationError("Add at least one filter (a title, seniority, location, company size, domain or keywords).", 422)
        if payload.kind == "companies" and not any(filters[k] for k in ("keywords", "company_name", "locations", "company_locations", "industries", "employee_ranges")):
            raise IntegrationError("Add at least one filter (a name, location, industry or company size).", 422)
        connection = await core.active_connection(db, principal.organization_id, provider)
        result = await sales_intel.search(db, connection, provider, payload.kind, filters)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return result


class ImportItem(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=300)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=250)
    domain: str | None = Field(default=None, max_length=250)
    website: str | None = Field(default=None, max_length=500)
    industry: str | None = Field(default=None, max_length=150)
    employees: int | None = Field(default=None, ge=0, le=10_000_000)
    linkedin: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=300)


class ImportPayload(BaseModel):
    provider: ProviderKey
    kind: Literal["people", "companies"] = "people"
    as_type: Literal["lead", "contact", "account"] = Field(default="lead", alias="as")
    owner_user_id: uuid.UUID | None = None
    lead_source: str | None = Field(default=None, max_length=80)
    items: list[ImportItem] = Field(min_length=1, max_length=sales_intel.MAX_IMPORT)

    model_config = {"populate_by_name": True}


@router.post("/prospects/import", dependencies=INTEL)
async def prospect_import(payload: ImportPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    if principal.api_key_id is None and principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support access can't import prospects.")
    if payload.as_type == "account" and payload.kind != "companies" or payload.as_type != "account" and payload.kind == "companies":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "People import as leads or contacts; companies import as accounts.")
    try:
        owner = await lead_capture.valid_owner(db, org, payload.owner_user_id) if payload.owner_user_id else None
        # Bulk importing for someone else is what the "Import data" permission is for (admins and managers by default).
        if owner and owner != principal.user_id and not principal.can("data.import"):
            raise IntegrationError("Only people who can import data can import records for someone else.", 403)
        defaults = await core.workspace_config(db, org, payload.provider)
        try:  # a default owner who has since left the workspace falls back to the person importing
            default_owner = await lead_capture.valid_owner(db, org, defaults.get("owner_user_id"))
        except IntegrationError:
            default_owner = None
        result = await sales_intel.import_items(
            db, principal, payload.provider, payload.kind, [i.model_dump() for i in payload.items], as_type=payload.as_type,
            owner_user_id=owner or default_owner,  # the admin's default owner, else the person importing
            lead_source=payload.lead_source or defaults.get("lead_source"),
        )
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    if result["created"]:
        record_audit(
            db, organization_id=org, action="prospects_imported", entity_type=payload.as_type,
            entity_label=f"{len(result['created'])} from {sales_intel.VENDOR[payload.provider]}",
            changes={"provider": payload.provider, "created": len(result["created"]), "skipped": len(result["skipped"])},
        )
    await db.commit()
    return result


# ===========================================================================
# LinkedIn Lead Gen Forms
# ===========================================================================


@router.post("/linkedin-leads/accounts/refresh", dependencies=LEADS)
async def linkedin_refresh(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    try:
        connection = await core.active_connection(db, principal.organization_id, linkedin_leads.KEY)
        await linkedin_leads.refresh_accounts(db, connection)
        for account in linkedin_leads._accounts(connection):
            if account.get("sync"):
                await linkedin_leads.load_forms(db, connection, account["id"])
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _detail(linkedin_leads.KEY, principal, db)


class AccountSync(BaseModel):
    sync: bool


@router.put("/linkedin-leads/accounts/{account_id}", dependencies=LEADS)
async def linkedin_account(account_id: str, payload: AccountSync, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    if not account_id.isdigit() or len(account_id) > 30:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ad account not found.")
    try:
        connection = await core.active_connection(db, principal.organization_id, linkedin_leads.KEY)
        await linkedin_leads.set_account_sync(db, connection, account_id, payload.sync, principal.user_id)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await _detail(linkedin_leads.KEY, principal, db)


@router.post("/linkedin-leads/sync", dependencies=LEADS)
async def linkedin_sync(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    try:
        connection = await core.active_connection(db, org, linkedin_leads.KEY)
        if not any(a.get("sync") for a in linkedin_leads._accounts(connection)):
            raise IntegrationError("Choose at least one ad account to sync first.", 409)
        imported = await linkedin_leads.sync(db, connection)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return {"imported": imported, **(await _detail(linkedin_leads.KEY, principal, db))}
