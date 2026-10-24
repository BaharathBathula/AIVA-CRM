"""Lead capture (WordPress Forms, Facebook Lead Ads) and automation webhooks
(Zapier, Make, n8n). Provider logic lives in ``app/integrations/providers``.

Routes
------
* ``/integration-webhooks/...``: outgoing webhook subscriptions and their
  deliveries, for the Webhooks page and the Zapier / Make / n8n panels.
* ``/hooks/...``: the REST hooks API automation platforms call with an AIVA API
  key (``Authorization: Bearer aiva_sk_...``) to subscribe and unsubscribe
  themselves, list events and fetch sample payloads.
* ``/lead-capture/...``: WordPress plugin download, signing secret and test
  submissions; Facebook pages, page subscriptions, forms and catch-up sync.

Inbound webhooks (WordPress posts, Meta lead notifications) arrive through the
hub's public ``/public/integrations/{key}/webhook/{token}`` dispatcher.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_feature, require_permission
from app.api.routes.integration_hub import integration_detail
from app.core.crypto import encrypt
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import automation as hooks
from app.integrations.providers import facebook, wordpress
from app.integrations.providers.lead_capture import Answer
from app.models.integration_hub import WebhookDelivery, WebhookSubscription
from app.models.organization import Organization
from app.services.automation import record_audit
from app.services.lookups import names_for

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def hook_manager(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Outgoing webhooks send record data out of AIVA, so only people who can see
    every record and manage automation (or admins) may set them up."""

    if not principal.has("api_access"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Webhooks for Zapier, Make and n8n need API access, which isn't in your plan.")
    if principal.can("admin.settings") or (principal.can("automation.workflows") and principal.can("records.view_all")):
        return principal
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins (or people who manage workflows and can see all records) can manage webhooks.")


# ===========================================================================
# Outgoing webhooks: subscriptions and deliveries
# ===========================================================================


@router.get("/integration-webhooks/events", dependencies=[Depends(require_feature("api_access"))])
async def webhook_events(principal: Principal = Depends(get_current_principal)):
    return {
        "items": hooks.event_catalog(),
        "any": hooks.ANY_EVENT,
        "providers": [{"key": k, "name": hooks.PROVIDER_NAMES[k]} for k in hooks.PROVIDERS],
        "can_manage": principal.can("admin.settings") or (principal.can("automation.workflows") and principal.can("records.view_all")),
    }


@router.get("/integration-webhooks/subscriptions")
async def list_subscriptions(
    provider: str | None = Query(default=None, pattern="^(zapier|make|n8n|custom)$"),
    event: str | None = Query(default=None, max_length=60),
    state: str | None = Query(default=None, alias="status", pattern="^(active|paused)$"),
    principal: Principal = Depends(hook_manager),
    db: AsyncSession = Depends(get_db),
):
    query = select(WebhookSubscription).where(WebhookSubscription.organization_id == principal.organization_id, WebhookSubscription.source != "deleted")
    if provider:
        query = query.where(WebhookSubscription.provider == provider)
    if event:
        query = query.where(WebhookSubscription.event == event)
    if state:
        query = query.where(WebhookSubscription.is_active.is_(state == "active"))
    rows = (await db.execute(query.order_by(WebhookSubscription.created_at.desc()).limit(500))).scalars().all()
    names = await names_for(db, principal.organization_id, "user", [r.created_by_user_id for r in rows])
    toggles = await core.toggles(db, principal.organization_id)
    switched = {key: bool(toggles.get(key) and toggles[key].enabled) for key in hooks.AUTOMATION_KEYS}
    return {"items": [hooks.subscription_out(r, names) for r in rows], "providers_on": switched}


class SubscriptionCreate(BaseModel):
    provider: str = Field(pattern="^(zapier|make|n8n|custom)$")
    events: list[str] = Field(min_length=1, max_length=40)
    target_url: str = Field(min_length=8, max_length=2000)
    label: str | None = Field(default=None, max_length=200)


@router.post("/integration-webhooks/subscriptions", status_code=status.HTTP_201_CREATED)
async def create_subscription(payload: SubscriptionCreate, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    try:
        rows, secret = await hooks.create_subscriptions(
            db, organization_id=principal.organization_id, provider=payload.provider, events=payload.events, target_url=payload.target_url,
            label=payload.label, source="manual", user_id=principal.user_id, api_key_id=principal.api_key_id,
        )
    except IntegrationError as exc:
        raise _error(exc) from exc
    for row in rows:
        record_audit(db, organization_id=principal.organization_id, action="webhook_subscribed", entity_type="webhook", entity_id=row.id,
                     entity_label=f"{hooks.PROVIDER_NAMES[row.provider]}: {hooks.event_label(row.event)}")
    await db.commit()
    names = {str(principal.user_id): principal.full_name}
    return {"items": [hooks.subscription_out(r, names) for r in rows], "secret": secret}


class SubscriptionUpdate(BaseModel):
    target_url: str | None = Field(default=None, min_length=8, max_length=2000)
    label: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None
    event: str | None = Field(default=None, max_length=60)


@router.patch("/integration-webhooks/subscriptions/{subscription_id}")
async def update_subscription(subscription_id: uuid.UUID, payload: SubscriptionUpdate, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    try:
        row = await hooks.get_subscription(db, principal.organization_id, subscription_id)
        changed: dict[str, Any] = {}
        if payload.target_url is not None and payload.target_url.strip() != row.target_url:
            url = hooks.check_target_url(payload.target_url)
            await hooks.check_target_reachable(url)
            changed["target_url"] = {"from": row.target_url, "to": url}
            row.target_url = url
        if payload.label is not None:
            row.label = payload.label.strip()[:200] or None
        if payload.event is not None and payload.event != row.event:
            if payload.event != hooks.ANY_EVENT and payload.event not in hooks.EVENTS:
                raise IntegrationError("Unknown event.", 422)
            changed["event"] = {"from": row.event, "to": payload.event}
            row.event = payload.event
        if payload.is_active is not None and payload.is_active != row.is_active:
            if payload.is_active:
                await hooks.require_provider_on(db, principal.organization_id, row.provider)
                row.is_active = True
                row.consecutive_failures = 0
                core.log(db, row.organization_id, hooks._log_key(row.provider), "subscription_resumed",
                         f"Resumed {hooks.event_label(row.event)} → {hooks.subscription_out(row)['target_host']}.", user_id=principal.user_id)
            else:
                await hooks.pause_subscription(db, row, "paused by a person.", user_id=principal.user_id)
            changed["is_active"] = {"from": not payload.is_active, "to": payload.is_active}
    except IntegrationError as exc:
        raise _error(exc) from exc
    if changed:
        record_audit(db, organization_id=principal.organization_id, action="webhook_updated", entity_type="webhook", entity_id=row.id,
                     entity_label=f"{hooks.PROVIDER_NAMES[row.provider]}: {hooks.event_label(row.event)}", changes=changed)
    hooks.refresh_rules(principal.organization_id)
    await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.created_by_user_id])
    return hooks.subscription_out(row, names)


@router.delete("/integration-webhooks/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(subscription_id: uuid.UUID, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    try:
        row = await hooks.get_subscription(db, principal.organization_id, subscription_id)
    except IntegrationError as exc:
        raise _error(exc) from exc
    await hooks.remove_subscription(db, row, principal.user_id, "removed by a person")
    record_audit(db, organization_id=principal.organization_id, action="webhook_removed", entity_type="webhook", entity_id=row.id,
                 entity_label=f"{hooks.PROVIDER_NAMES[row.provider]}: {hooks.event_label(row.event)}")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/integration-webhooks/subscriptions/{subscription_id}/rotate-secret")
async def rotate_subscription_secret(subscription_id: uuid.UUID, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    try:
        row = await hooks.get_subscription(db, principal.organization_id, subscription_id)
    except IntegrationError as exc:
        raise _error(exc) from exc
    secret = hooks.new_secret()
    row.secret = encrypt(secret)
    record_audit(db, organization_id=principal.organization_id, action="webhook_secret_rotated", entity_type="webhook", entity_id=row.id,
                 entity_label=f"{hooks.PROVIDER_NAMES[row.provider]}: {hooks.event_label(row.event)}")
    core.log(db, row.organization_id, hooks._log_key(row.provider), "secret_rotated",
             f"New signing secret for {hooks.event_label(row.event)} → {hooks.subscription_out(row)['target_host']}.", level="warning", user_id=principal.user_id)
    await db.commit()
    return {"secret": secret}


@router.post("/integration-webhooks/subscriptions/{subscription_id}/test")
async def test_subscription(subscription_id: uuid.UUID, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    try:
        row = await hooks.get_subscription(db, principal.organization_id, subscription_id)
        await hooks.require_provider_on(db, principal.organization_id, row.provider)
        delivery, result = await hooks.test_delivery(db, row)
    except IntegrationError as exc:
        raise _error(exc) from exc
    core.log(db, row.organization_id, hooks._log_key(row.provider), "test_sent" if result.ok else "test_failed",
             f"Test {hooks.event_label(delivery.event)} → {hooks.subscription_out(row)['target_host']}: " + ("delivered." if result.ok else (result.error or "failed.")),
             level="success" if result.ok else "error", user_id=principal.user_id)
    await db.commit()
    return {
        "ok": result.ok,
        "message": f"Delivered (HTTP {result.status}) in {result.duration_ms} ms." if result.ok else f"Not delivered: {result.error}",
        "delivery": hooks.delivery_out(delivery, row),
    }


@router.get("/integration-webhooks/deliveries")
async def list_deliveries(
    subscription_id: uuid.UUID | None = None,
    provider: str | None = Query(default=None, pattern="^(zapier|make|n8n|custom)$"),
    event: str | None = Query(default=None, max_length=60),
    state: str | None = Query(default=None, alias="status", pattern="^(pending|delivered|failed)$"),
    before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(hook_manager),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(WebhookDelivery, WebhookSubscription)
        .join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
        .where(WebhookDelivery.organization_id == principal.organization_id)
    )
    if subscription_id:
        query = query.where(WebhookDelivery.subscription_id == subscription_id)
    if provider:
        query = query.where(WebhookSubscription.provider == provider)
    if event:
        query = query.where(WebhookDelivery.event == event)
    if state:
        query = query.where(WebhookDelivery.status == state)
    if before:
        query = query.where(WebhookDelivery.created_at < before)
    rows = (await db.execute(query.order_by(WebhookDelivery.created_at.desc()).limit(limit + 1))).all()
    items = [hooks.delivery_out(d, s) for d, s in rows[:limit]]
    return {"items": items, "next_before": items[-1]["created_at"] if len(rows) > limit and items else None}


async def _delivery(db: AsyncSession, principal: Principal, delivery_id: uuid.UUID) -> tuple[WebhookDelivery, WebhookSubscription]:
    row = (
        await db.execute(
            select(WebhookDelivery, WebhookSubscription)
            .join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
            .where(WebhookDelivery.id == delivery_id, WebhookDelivery.organization_id == principal.organization_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That delivery doesn't exist.")
    return row[0], row[1]


@router.get("/integration-webhooks/deliveries/{delivery_id}")
async def get_delivery(delivery_id: uuid.UUID, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    delivery, subscription = await _delivery(db, principal, delivery_id)
    return hooks.delivery_out(delivery, subscription, with_payload=True)


@router.post("/integration-webhooks/deliveries/{delivery_id}/redeliver")
async def redeliver(delivery_id: uuid.UUID, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    original, subscription = await _delivery(db, principal, delivery_id)
    if subscription.source == "deleted":
        raise HTTPException(status.HTTP_409_CONFLICT, "That subscription was removed.")
    if not subscription.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Resume the subscription before sending again.")
    try:
        await hooks.require_provider_on(db, principal.organization_id, subscription.provider)
        delivery, result = await hooks.redeliver(db, original, subscription)
    except IntegrationError as exc:
        raise _error(exc) from exc
    core.log(db, subscription.organization_id, hooks._log_key(subscription.provider), "redelivered" if result.ok else "redelivery_failed",
             f"Sent {hooks.event_label(delivery.event)} again to {hooks.subscription_out(subscription)['target_host']}: " + ("delivered." if result.ok else (result.error or "failed.")),
             level="success" if result.ok else "error", user_id=principal.user_id)
    await db.commit()
    return {"ok": result.ok, "message": "Delivered." if result.ok else f"Not delivered: {result.error}", "delivery": hooks.delivery_out(delivery, subscription)}


# ===========================================================================
# REST hooks API (Zapier-style subscribe/unsubscribe with an API key)
# ===========================================================================


@router.get("/hooks/me")
async def hooks_me(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    """Auth test for automation platforms: who this key (or session) belongs to."""

    organization = await db.get(Organization, principal.organization_id)
    return {
        "ok": True,
        "workspace_id": str(principal.organization_id),
        "workspace_name": organization.name if organization else None,
        "user": {"name": principal.full_name, "email": principal.email},
        "api_key": principal.api_key_id is not None,
        "can_subscribe": principal.has("api_access") and (principal.can("admin.settings") or (principal.can("automation.workflows") and principal.can("records.view_all"))),
    }


@router.get("/hooks/events")
async def hooks_events(principal: Principal = Depends(get_current_principal)):
    return [{"key": e["key"], "label": e["label"], "object": e["object"], "description": e["description"]} for e in hooks.event_catalog()]


class HookSubscribe(BaseModel):
    event: str = Field(min_length=1, max_length=60)
    target_url: str | None = Field(default=None, max_length=2000)
    hookUrl: str | None = Field(default=None, max_length=2000)  # noqa: N815 - Zapier's name
    url: str | None = Field(default=None, max_length=2000)
    provider: str = Field(default="zapier", pattern="^(zapier|make|n8n|custom)$")
    label: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _one_url(self) -> HookSubscribe:
        if not (self.target_url or self.hookUrl or self.url):
            raise ValueError("Send the URL to post events to as target_url.")
        return self


@router.get("/hooks")
async def hooks_list(principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    query = select(WebhookSubscription).where(
        WebhookSubscription.organization_id == principal.organization_id, WebhookSubscription.source == "rest_hook"
    )
    if principal.api_key_id is not None:
        query = query.where(WebhookSubscription.api_key_id == principal.api_key_id)
    rows = (await db.execute(query.order_by(WebhookSubscription.created_at.desc()).limit(500))).scalars().all()
    return [{"id": str(r.id), "event": r.event, "target_url": r.target_url, "provider": r.provider, "is_active": r.is_active} for r in rows]


@router.post("/hooks", status_code=status.HTTP_201_CREATED)
async def hooks_subscribe(payload: HookSubscribe, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    if payload.event == hooks.ANY_EVENT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Subscribe to one event at a time.")
    try:
        rows, secret = await hooks.create_subscriptions(
            db, organization_id=principal.organization_id, provider=payload.provider, events=[payload.event],
            target_url=payload.target_url or payload.hookUrl or payload.url or "", label=payload.label or f"{hooks.PROVIDER_NAMES[payload.provider]} (API)",
            source="rest_hook", user_id=principal.user_id, api_key_id=principal.api_key_id,
        )
    except IntegrationError as exc:
        raise _error(exc) from exc
    row = rows[0]
    record_audit(db, organization_id=principal.organization_id, action="webhook_subscribed", entity_type="webhook", entity_id=row.id,
                 entity_label=f"{hooks.PROVIDER_NAMES[row.provider]}: {hooks.event_label(row.event)} (API)")
    await db.commit()
    return {"id": str(row.id), "event": row.event, "target_url": row.target_url, "provider": row.provider, "secret": secret, "signature_header": "X-AIVA-Signature"}


@router.delete("/hooks/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def hooks_unsubscribe(subscription_id: uuid.UUID, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    row = await db.get(WebhookSubscription, subscription_id)
    if row is None or row.organization_id != principal.organization_id or row.source == "deleted":
        # Unsubscribing twice is fine (Zapier retries).
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if principal.api_key_id is not None and row.api_key_id not in (None, principal.api_key_id) and not principal.can("admin.settings"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That subscription was created with a different API key.")
    await hooks.remove_subscription(db, row, principal.user_id, "unsubscribed through the API")
    record_audit(db, organization_id=principal.organization_id, action="webhook_removed", entity_type="webhook", entity_id=row.id,
                 entity_label=f"{hooks.PROVIDER_NAMES.get(row.provider, row.provider)}: {hooks.event_label(row.event)} (API)")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/hooks/samples/{event}")
async def hooks_samples(event: str, principal: Principal = Depends(hook_manager), db: AsyncSession = Depends(get_db)):
    """Recent records shaped exactly like the event's webhook body (for building Zaps)."""

    try:
        return await hooks.samples(db, principal.organization_id, event)
    except IntegrationError as exc:
        raise _error(exc) from exc


# ===========================================================================
# WordPress Forms
# ===========================================================================


def _needs_web_forms(principal: Principal) -> None:
    if not principal.has("web_forms"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Lead capture integrations aren't included in your plan.")


async def _wordpress_connection(db: AsyncSession, principal: Principal):
    _needs_web_forms(principal)
    if not await core.is_enabled(db, principal.organization_id, wordpress.KEY):
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn WordPress Forms on first.")
    connection = await core.connection_for(db, principal.organization_id, wordpress.KEY)
    if connection is None or not connection.webhook_token:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn WordPress Forms off and on again to create its webhook address.")
    return connection


@router.post("/lead-capture/wordpress/secret")
async def wordpress_rotate_secret(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    connection = await _wordpress_connection(db, principal)
    secret = await wordpress.rotate_secret(db, connection, principal.user_id)
    record_audit(db, organization_id=principal.organization_id, action="integration_secret_rotated", entity_type="integration", entity_label="WordPress Forms")
    await db.commit()
    return {"secret": secret}


@router.get("/lead-capture/wordpress/plugin.zip")
async def wordpress_plugin(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    connection = await _wordpress_connection(db, principal)
    secret = await wordpress.ensure_secret(db, connection)
    organization = await db.get(Organization, principal.organization_id)
    data = wordpress.plugin_zip(core.webhook_url(wordpress.KEY, connection.webhook_token) or "", secret, organization.name if organization else "")
    core.log(db, principal.organization_id, wordpress.KEY, "plugin_downloaded", "The AIVA WordPress plugin was downloaded.", connection=connection, user_id=principal.user_id)
    record_audit(db, organization_id=principal.organization_id, action="integration_plugin_downloaded", entity_type="integration", entity_label="WordPress Forms")
    await db.commit()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="aiva-crm-forms.zip"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


class WordPressTest(BaseModel):
    create: bool = False
    form_key: str | None = Field(default=None, max_length=120)


SAMPLE_ANSWERS = [
    Answer("your-name", "Your name", "Taylor Test"),
    Answer("your-email", "Your email", "taylor.test@example.com"),
    Answer("your-phone", "Phone", "+1 415 555 0100"),
    Answer("company", "Company", "Example Co"),
    Answer("your-message", "Your message", "This is a test submission sent from AIVA."),
]


@router.post("/lead-capture/wordpress/test")
async def wordpress_test(payload: WordPressTest, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Run a sample submission through the mapping. With ``create``, a test lead is made."""

    connection = await _wordpress_connection(db, principal)
    config = await core.workspace_config(db, principal.organization_id, wordpress.KEY)
    seen = (config.get("seen_forms") or {}).get(payload.form_key or "") if payload.form_key else None
    if seen:
        answers = [Answer(f["key"], f.get("label") or f["key"], _sample_value(f)) for f in seen.get("fields") or []]
        meta = {"form_key": payload.form_key, "form_name": seen.get("name"), "plugin": seen.get("plugin")}
    else:
        answers = list(SAMPLE_ANSWERS)
        meta = {"plugin": "aiva", "form_name": "AIVA test form", "form_key": "aiva:test"}
    try:
        result = await wordpress.process(db, connection, meta, answers, dry_run=not payload.create, origin_note="a test from AIVA")
    except IntegrationError as exc:
        raise _error(exc) from exc
    await db.commit()
    if payload.create and result.get("lead_id"):
        result["link"] = f"/leads/{result['lead_id']}"
    return result


def _sample_value(field: dict[str, Any]) -> str:
    from app.integrations.providers.lead_capture import auto_target

    target = auto_target(Answer(field["key"], field.get("label") or field["key"], ""))
    return {
        "email": f"test+{uuid.uuid4().hex[:6]}@example.com",
        "first_name": "Taylor",
        "last_name": "Test",
        "full_name": "Taylor Test",
        "phone": "+1 415 555 0100",
        "company_name": "Example Co",
        "job_title": "Operations lead",
        "notes": "This is a test submission sent from AIVA.",
    }.get(target or "", "Test")


# ===========================================================================
# Facebook Lead Ads
# ===========================================================================


async def _facebook_connection(db: AsyncSession, principal: Principal):
    _needs_web_forms(principal)
    try:
        return await core.active_connection(db, principal.organization_id, facebook.KEY)
    except IntegrationError as exc:
        raise _error(exc) from exc


@router.post("/lead-capture/facebook/pages/refresh")
async def facebook_refresh_pages(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    connection = await _facebook_connection(db, principal)
    try:
        pages = await facebook.refresh_pages(db, connection)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    core.log(db, principal.organization_id, facebook.KEY, "pages_loaded", f"Found {len(pages)} page{'s' if len(pages) != 1 else ''}.", connection=connection, user_id=principal.user_id)
    await db.commit()
    return await integration_detail(facebook.KEY, principal, db)


class PageSubscription(BaseModel):
    subscribed: bool


@router.put("/lead-capture/facebook/pages/{page_id}")
async def facebook_page_subscription(page_id: str, payload: PageSubscription, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    if not page_id.isdigit() or len(page_id) > 40:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown page.")
    connection = await _facebook_connection(db, principal)
    try:
        page = await facebook.set_page_subscription(db, connection, page_id, payload.subscribed, principal.user_id)
        if payload.subscribed:
            try:
                await facebook.load_forms(db, connection, page_id)
            except IntegrationError:
                pass  # forms are a nicety for the mapping editor; leads still arrive
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    record_audit(db, organization_id=principal.organization_id, action="facebook_page_subscribed" if payload.subscribed else "facebook_page_unsubscribed",
                 entity_type="integration", entity_label=f"Facebook page {page['name']}")
    await db.commit()
    return await integration_detail(facebook.KEY, principal, db)


@router.get("/lead-capture/facebook/pages/{page_id}/forms")
async def facebook_forms(page_id: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    if not page_id.isdigit() or len(page_id) > 40:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown page.")
    connection = await _facebook_connection(db, principal)
    try:
        forms = await facebook.load_forms(db, connection, page_id)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    await db.commit()
    return {"items": forms}


@router.post("/lead-capture/facebook/sync")
async def facebook_sync(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Check every synced form for leads that haven't arrived yet."""

    connection = await _facebook_connection(db, principal)
    try:
        imported = await facebook.backfill(db, connection, force=True)
    except IntegrationError as exc:
        await db.rollback()
        raise _error(exc) from exc
    await db.commit()
    return {"imported": imported, "message": f"Checked for missed leads: {imported} new." if imported else "Checked for missed leads: you're up to date."}
