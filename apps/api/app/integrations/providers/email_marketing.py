"""Mailchimp, ActiveCampaign, Brevo, Constant Contact and Klaviyo: keep the
tool's audience in step with AIVA's contacts and leads, and AIVA's do-not-contact
list in step with the tool's unsubscribes, bounces and complaints.

Hooks called by the integrations hub. The work is in ``_em_sync`` (shared) and
one ``_em_<tool>`` adapter per tool.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_base import CRM_EVENTS, KEYS, LINK_TYPE, SOURCE_KEYS, SOURCES, settings_from
from app.integrations.providers._em_sync import (
    adapter_for,
    public_address,
    record_engagement,
    remove_webhook,
    run_due as _run_due,
    save_state,
    settings_for,
    state_of,
    suppress,
)
from app.integrations.providers._em_base import Engagement
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink

MAX_WEBHOOK_BYTES = 256 * 1024
MAX_CONFIRMATIONS = 10
LIST_ID = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
TARGET = re.compile(r"^[A-Za-z0-9_ .\-]{1,100}$")


# ---------------------------------------------------------------------------
# Connect / disconnect / test
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: Any, values: dict[str, str]) -> dict[str, Any]:
    return await adapter_for(key).validate(values)


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    state = state_of(connection)
    state["next_run_at"] = None  # pick it up on the next scheduler tick
    state.pop("last_error", None)
    save_state(connection, state)
    settings = await settings_for(db, connection)
    adapter = adapter_for(key)
    if adapter.list_required and not settings["list_id"]:
        core.log(db, connection.organization_id, key, "setup_needed", f"Connected. Choose the {adapter.list_label.lower()} to sync to on the {adapter.name} page.", connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await remove_webhook(db, connection)
    # The links describe this account's audience; a new connection starts fresh.
    await db.execute(
        delete(IntegrationRecordLink).where(IntegrationRecordLink.organization_id == connection.organization_id, IntegrationRecordLink.provider == key)
    )


async def on_toggle(db: AsyncSession, key: str, organization_id: Any, enabled: bool) -> None:
    if not enabled:
        return
    connection = await core.connection_for(db, organization_id, key)
    if connection is not None:
        state = state_of(connection)
        state["next_run_at"] = None
        save_state(connection, state)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    return await adapter_for(key).test(db, connection)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _strings(value: Any, *, most: int, longest: int, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise IntegrationError(f"{label} should be a list.", 422)
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > longest:
            raise IntegrationError(f"“{text[:30]}…” is too long for {label.lower()}.", 422)
        if text not in out:
            out.append(text)
    if len(out) > most:
        raise IntegrationError(f"Use at most {most} {label.lower()}.", 422)
    return out


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    adapter = adapter_for(key)
    cleaned: dict[str, Any] = {}
    if "list_id" in config:
        list_id = str(config.get("list_id") or "").strip()
        if list_id and not LIST_ID.match(list_id):
            raise IntegrationError(f"That isn't a {adapter.name} {adapter.list_label.lower()}.", 422)
        cleaned["list_id"] = list_id or None
        cleaned["list_name"] = str(config.get("list_name") or "").strip()[:200] or None
    if "record_types" in config:
        types = [t for t in _strings(config.get("record_types"), most=2, longest=10, label="Record types") if t in ("contact", "lead")]
        if not types:
            raise IntegrationError("Sync contacts, leads or both.", 422)
        cleaned["record_types"] = types
    if "lead_statuses" in config:
        cleaned["lead_statuses"] = _strings(config.get("lead_statuses"), most=20, longest=40, label="Lead statuses")
    if "contact_tags" in config:
        cleaned["contact_tags"] = _strings(config.get("contact_tags"), most=20, longest=60, label="Contact tags")
    if "skip_converted" in config:
        cleaned["skip_converted"] = bool(config.get("skip_converted"))
    if "sync_paused" in config:
        cleaned["sync_paused"] = bool(config.get("sync_paused"))
    if "status_for_new" in config:
        status = str(config.get("status_for_new") or "subscribed")
        if status not in ("subscribed", "pending", "transactional") or (status != "subscribed" and not adapter.capabilities.get("status_for_new")):
            raise IntegrationError("Choose subscribed, pending (double opt-in) or transactional.", 422)
        cleaned["status_for_new"] = status
    if "subscribe_mode" in config:
        mode = str(config.get("subscribe_mode") or "add")
        if mode not in ("add", "subscribe") or (mode == "subscribe" and not adapter.capabilities.get("subscribe_mode")):
            raise IntegrationError("Choose whether to only add people to the list or also subscribe them.", 422)
        cleaned["subscribe_mode"] = mode
    if "tags" in config:
        tags = _strings(config.get("tags"), most=10, longest=100, label="Tags")
        if tags and not adapter.capabilities.get("tags"):
            raise IntegrationError(f"{adapter.name} tags aren't supported here.", 422)
        cleaned["tags"] = tags
    if "field_map" in config:
        raw = config.get("field_map") or {}
        if not isinstance(raw, dict) or len(raw) > 30:
            raise IntegrationError("Map at most 30 fields.", 422)
        mapping: dict[str, str] = {}
        for target, source in raw.items():
            target = str(target or "").strip()
            source = str(source or "").strip()
            if not target or not source:
                continue
            if not TARGET.match(target):
                raise IntegrationError(f"“{target[:40]}” isn't a {adapter.name} field name.", 422)
            if source not in SOURCE_KEYS:
                raise IntegrationError(f"“{source[:40]}” isn't an AIVA field AIVA can send.", 422)
            mapping[target] = source
        cleaned["field_map"] = mapping
    if "events" in config:
        events = [e for e in _strings(config.get("events"), most=len(CRM_EVENTS), longest=40, label="Events") if e in CRM_EVENTS]
        if events and not adapter.capabilities.get("events"):
            raise IntegrationError(f"{adapter.name} doesn't receive CRM events.", 422)
        cleaned["events"] = events
    return cleaned


# ---------------------------------------------------------------------------
# Page data
# ---------------------------------------------------------------------------


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    adapter = adapter_for(key)
    org = principal.organization_id
    settings = settings_from(await core.workspace_config(db, org, key))
    connection = await core.connection_for(db, org, key)
    out: dict[str, Any] = {
        "capabilities": adapter.capabilities,
        "list_label": adapter.list_label,
        "list_required": adapter.list_required,
        "builtin_fields": [{"target": target, "source": source} for target, source in adapter.builtin_fields],
        "sources": [{"key": k, "label": label} for k, label in SOURCES],
        "crm_events": [{"key": k, "label": label} for k, label in CRM_EVENTS.items()],
        "settings": settings,
        "connected": bool(connection and connection.status != "needs_reauth"),
    }
    if connection is None:
        return out
    state = state_of(connection)
    parent = str(settings.get("list_id") or "")
    counts = dict(
        (
            await db.execute(
                select(IntegrationRecordLink.status, func.count())
                .where(
                    IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key,
                    IntegrationRecordLink.external_type == LINK_TYPE, IntegrationRecordLink.external_parent == parent,
                )
                .group_by(IntegrationRecordLink.status)
            )
        ).all()
    )
    campaigns = (
        await db.execute(
            select(IntegrationObject)
            .where(IntegrationObject.organization_id == org, IntegrationObject.provider == key, IntegrationObject.object_type == "campaign")
            .order_by(IntegrationObject.occurred_at.desc().nulls_last())
            .limit(10)
        )
    ).scalars().all()
    hook = state.get("webhook") or {}
    out.update(
        {
            "sync": {
                "last_run_at": state.get("last_run_at"),
                "next_run_at": state.get("next_run_at"),
                "last_error": state.get("last_error"),
                "last_result": state.get("last_result") or {},
                "backlog": bool(state.get("backlog")),
                "caught_up": state.get("caught_up") or {},
                "list_id": state.get("list_id"),
                "counts": {
                    "in_audience": int(counts.get("linked", 0)),
                    "pending": int(counts.get("pending", 0)),
                    "opted_out": sum(int(counts.get(k, 0)) for k in ("unsubscribed", "cleaned", "bounced", "complaint")),
                    "errors": int(counts.get("error", 0)),
                },
            },
            "webhook": {
                "supported": bool(adapter.capabilities.get("webhooks")),
                "active": bool(hook.get("id")),
                "error": state.get("webhook_error"),
                "note": state.get("webhook_note"),
                "public_address": public_address(),
            },
            "campaigns": [
                {"id": c.external_id, "title": c.title, "status": c.status, "sent_at": c.occurred_at, "url": c.url, "stats": c.data or {}} for c in campaigns
            ],
            "webhook_url": core.webhook_url(key, connection.webhook_token) if adapter.capabilities.get("webhooks") else None,
        }
    )
    return out


# ---------------------------------------------------------------------------
# Inbound webhooks (Mailchimp, ActiveCampaign, Brevo)
# ---------------------------------------------------------------------------


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    adapter = adapter_for(key)
    if request.method != "POST":
        # Mailchimp checks the address with a GET before saving the webhook.
        return JSONResponse({"ok": True, "message": f"AIVA receives {adapter.name} events here."})
    if not adapter.capabilities.get("webhooks"):
        return JSONResponse({"error": "not supported"}, status_code=404)
    declared = request.headers.get("content-length") or ""
    if declared.isascii() and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    form = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True) if content_type == "application/x-www-form-urlencoded" else None
    try:
        events = await adapter.parse_webhook(body, content_type, form)
    except Exception:  # noqa: BLE001 - a malformed delivery is ignored
        return JSONResponse({"ok": True, "ignored": "unreadable"})
    settings = await settings_for(db, connection)
    wanted_list = str(settings.get("list_id") or "")
    events = [e for e in events[:50] if e.email and (not e.list_id or not wanted_list or str(e.list_id) == wanted_list)]
    confirmed = []
    engagement: list[Engagement] = []
    checks = 0
    for event in events:
        if event.kind in ("unsubscribed", "cleaned", "bounced", "complaint"):
            if checks >= MAX_CONFIRMATIONS:
                continue
            checks += 1
            try:
                # Unsigned webhooks: believe the tool's API, not the request.
                change = await adapter.confirm(db, connection, settings, event.email)  # type: ignore[arg-type]
            except IntegrationError:
                change = None
            if change is not None:
                if event.kind == "complaint" and change.kind == "unsubscribed":
                    change.kind = "complaint"
                confirmed.append(change)
        elif event.kind in ("open", "click") and event.campaign_id:
            engagement.append(Engagement(email=event.email, kind=event.kind, campaign_id=str(event.campaign_id)[:100], campaign_title=event.campaign_title or "", url=event.url))  # type: ignore[arg-type]
    if confirmed:
        await suppress(db, connection, confirmed, settings)
    if engagement:
        await record_engagement(db, connection, settings, engagement)
    connection.last_used_at = core.utcnow()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


async def run_due(db: AsyncSession) -> int:
    return await _run_due(db)


__all__ = [
    "KEYS", "clean_settings", "detail", "handle_webhook", "on_connected", "on_disconnect", "on_toggle", "run_due", "test", "validate_credentials",
]
