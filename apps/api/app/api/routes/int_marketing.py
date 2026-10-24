"""Integration endpoints: email marketing (Mailchimp, ActiveCampaign, Brevo, Constant Contact, Klaviyo).

* ``/email-marketing/{key}/lists`` and ``/fields``: live pickers for the settings (admins).
* ``/email-marketing/{key}/sync``: "Sync now" (admins).
* ``/email-marketing/{key}/campaigns``: recent campaigns and their stats.
* ``/email-marketing/records/{type}/{id}``: a contact's or lead's status in each
  connected tool, plus subscribe and unsubscribe.

Settings are saved through the hub (``PUT /integration-hub/{key}/settings``) and
inbound webhooks arrive through the hub's public webhook URL.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_base import KEYS, LINK_TYPE, Person, normal_email, settings_from
from app.integrations.providers._em_sync import (
    _people,
    adapter_for,
    save_state,
    settings_for,
    state_of,
    sync_connection,
    sync_people,
)
from app.models.contact import Contact
from app.models.governance import SuppressionEntry
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.models.lead import Lead
from app.services.automation import record_audit

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

FEATURE = [Depends(require_feature("mass_email"))]
RECORD_MODELS = {"contact": Contact, "lead": Lead}
MEMBER_TIMEOUT = 8.0


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _bounded(key: str, work: Any) -> Any:
    """A provider call with a deadline. Constant Contact's calls may refresh its token
    in the database, so they're left to their HTTP timeout instead of being cancelled."""

    if key == "constant_contact":
        return await work
    return await asyncio.wait_for(work, timeout=MEMBER_TIMEOUT)


def _key(key: str) -> str:
    if key not in KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown email marketing tool.")
    return key


async def _connection(db: AsyncSession, organization_id: uuid.UUID, key: str) -> IntegrationConnection:
    try:
        return await core.active_connection(db, organization_id, key)
    except IntegrationError as exc:
        raise _error(exc) from exc


async def _record(db: AsyncSession, organization_id: uuid.UUID, record_type: str, record_id: uuid.UUID) -> Any:
    model = RECORD_MODELS.get(record_type)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Email marketing works with contacts and leads.")
    # A select (not db.get) so record visibility rules apply.
    found = (await db.execute(select(model).where(model.id == record_id, model.organization_id == organization_id))).scalar_one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{record_type.title()} not found.")
    return found


# ---------------------------------------------------------------------------
# Settings pickers
# ---------------------------------------------------------------------------


@router.get("/email-marketing/{key}/lists", dependencies=FEATURE)
async def tool_lists(key: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    connection = await _connection(db, principal.organization_id, _key(key))
    try:
        items = await adapter_for(key).lists(db, connection)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    await db.commit()
    return {"items": items}


@router.get("/email-marketing/{key}/fields", dependencies=FEATURE)
async def tool_fields(
    key: str,
    list_id: str | None = Query(default=None, max_length=100),
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    connection = await _connection(db, principal.organization_id, _key(key))
    settings = await settings_for(db, connection)
    if list_id is not None:
        settings["list_id"] = list_id
    try:
        items = await adapter_for(key).fields(db, connection, settings)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    await db.commit()
    return {"items": items}


# ---------------------------------------------------------------------------
# Sync now
# ---------------------------------------------------------------------------


class SyncPayload(BaseModel):
    resend_everyone: bool = False


@router.post("/email-marketing/{key}/sync", dependencies=FEATURE)
async def sync_now(key: str, payload: SyncPayload | None = None, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Run the sync now (up to about 20 seconds; the scheduler carries on with the rest)."""

    org = principal.organization_id
    found = await _connection(db, org, _key(key))
    db.sync_session.info["automation_disabled"] = True
    connection = (
        await db.execute(select(IntegrationConnection).where(IntegrationConnection.id == found.id).with_for_update(skip_locked=True))
    ).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A sync is already running. Try again in a minute.")
    adapter = adapter_for(key)
    if payload is not None and payload.resend_everyone:
        state = state_of(connection)
        state.pop("cursor", None)
        state.pop("caught_up", None)
        save_state(connection, state)
        await db.execute(
            update(IntegrationRecordLink)
            .where(IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key, IntegrationRecordLink.external_type == LINK_TYPE)
            .values(sync_hash=None)
        )
        core.log(db, org, key, "resync_started", f"Sending everyone to {adapter.name} again.", connection=connection, user_id=principal.user_id)
    result = await sync_connection(db, connection, budget=20.0, manual=True)
    summary = result["summary"]
    if result["problem"]:
        message = result["problem"]
    else:
        parts = [f"{summary['sent']} sent", f"{summary['unchanged']} unchanged"]
        if summary["unsubscribed_in_tool"]:
            parts.append(f"{summary['unsubscribed_in_tool']} unsubscribed in {adapter.name}")
        if summary["suppressed_in_aiva"]:
            parts.append(f"{summary['suppressed_in_aiva']} added to the do-not-contact list")
        if summary["errors"]:
            parts.append(f"{summary['errors']} not accepted")
        message = "Synced: " + ", ".join(parts) + "." + (" More to go; the rest follows within minutes." if result["backlog"] else "")
    core.log(db, org, key, "sync_now", message, level="error" if result["problem"] else "success", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"ok": not result["problem"], "message": message, "summary": summary, "backlog": result["backlog"]}


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


@router.get("/email-marketing/{key}/campaigns", dependencies=FEATURE)
async def tool_campaigns(key: str, limit: int = Query(default=30, ge=1, le=100), principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(IntegrationObject)
            .where(IntegrationObject.organization_id == principal.organization_id, IntegrationObject.provider == _key(key), IntegrationObject.object_type == "campaign")
            .order_by(IntegrationObject.occurred_at.desc().nulls_last())
            .limit(limit)
        )
    ).scalars().all()
    return {"items": [{"id": r.external_id, "title": r.title, "status": r.status, "sent_at": r.occurred_at, "url": r.url, "stats": r.data or {}} for r in rows]}


# ---------------------------------------------------------------------------
# A contact's or lead's status in each tool
# ---------------------------------------------------------------------------


async def _tools(db: AsyncSession, organization_id: uuid.UUID) -> list[tuple[str, IntegrationConnection, dict[str, Any]]]:
    out = []
    for key in KEYS:
        if not await core.is_enabled(db, organization_id, key):
            continue
        connection = await core.connection_for(db, organization_id, key)
        if connection is None or connection.status == "needs_reauth":
            continue
        out.append((key, connection, settings_from(await core.workspace_config(db, organization_id, key))))
    return out


def _link_out(link: IntegrationRecordLink | None) -> dict[str, Any] | None:
    if link is None:
        return None
    return {"status": link.status, "synced_at": link.last_synced_at, "error": link.last_error, "record_type": link.record_type, "record_id": str(link.record_id)}


@router.get("/email-marketing/records/{record_type}/{record_id}", dependencies=FEATURE)
async def record_marketing(record_type: Literal["contact", "lead"], record_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    from app.services.prospecting import suppression_for

    org = principal.organization_id
    record = await _record(db, org, record_type, record_id)
    email = normal_email(record.email)
    tools = await _tools(db, org)
    out: dict[str, Any] = {"email": email, "do_not_contact": None, "opted_out": bool(record.email_opt_out), "tools": [], "can_change": principal.can("records.write")}
    if email:
        entry = await suppression_for(db, org, email)
        if entry is not None:
            out["do_not_contact"] = {"reason": entry.reason, "note": entry.note, "at": entry.created_at, "kind": entry.kind}
    for key, connection, settings in tools:
        adapter = adapter_for(key)
        item: dict[str, Any] = {
            "key": key, "name": adapter.name, "list_label": adapter.list_label, "list_name": settings.get("list_name"),
            "configured": bool(settings.get("list_id")) or not adapter.list_required, "link": None, "member": None, "error": None,
        }
        if email and item["configured"]:
            link = (
                await db.execute(
                    select(IntegrationRecordLink).where(
                        IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key, IntegrationRecordLink.external_type == LINK_TYPE,
                        IntegrationRecordLink.external_parent == str(settings.get("list_id") or ""), IntegrationRecordLink.external_id == email,
                    )
                )
            ).scalar_one_or_none()
            item["link"] = _link_out(link)
            try:
                item["member"] = await _bounded(key, adapter.member(db, connection, settings, email))
            except asyncio.TimeoutError:
                item["error"] = f"{adapter.name} didn't answer in time."
            except IntegrationError as exc:
                item["error"] = str(exc)
        out["tools"].append(item)
    await db.commit()
    return out


class SubscribePayload(BaseModel):
    key: str = Field(max_length=40)


@router.post("/email-marketing/records/{record_type}/{record_id}/subscribe", dependencies=FEATURE)
async def subscribe_record(
    record_type: Literal["contact", "lead"],
    record_id: uuid.UUID,
    payload: SubscribePayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    from app.services.prospecting import suppression_for

    org = principal.organization_id
    key = _key(payload.key)
    adapter = adapter_for(key)
    record = await _record(db, org, record_type, record_id)
    email = normal_email(record.email)
    if not email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"This {record_type} has no valid email address.")
    if record.email_opt_out or await suppression_for(db, org, email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{email} is on AIVA's do-not-contact list, so AIVA won't add them to {adapter.name}.")
    connection = await _connection(db, org, key)
    settings = await settings_for(db, connection)
    if adapter.list_required and not settings.get("list_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Choose the {adapter.name} {adapter.list_label.lower()} first (on the {adapter.name} integration page).")
    link = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key, IntegrationRecordLink.external_type == LINK_TYPE,
                IntegrationRecordLink.external_parent == str(settings.get("list_id") or ""), IntegrationRecordLink.external_id == email,
            )
        )
    ).scalar_one_or_none()
    if link is not None and link.status in ("unsubscribed", "cleaned", "bounced", "complaint"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{email} unsubscribed or bounced in {adapter.name}. Only they can subscribe again.")
    db.sync_session.info["automation_disabled"] = True
    summary = {"sent": 0, "unchanged": 0, "skipped": 0, "errors": 0, "unsubscribed_in_tool": 0, "suppressed_in_aiva": 0, "activities": 0, "events": 0}
    pairs: list[tuple[Any, Person]] = await _people(db, org, record_type, [record])
    try:
        await sync_people(db, connection, settings, pairs, summary, force=True)
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    if summary["errors"]:
        await db.commit()
        failed = (
            await db.execute(
                select(IntegrationRecordLink.last_error).where(
                    IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key, IntegrationRecordLink.external_id == email,
                    IntegrationRecordLink.external_parent == str(settings.get("list_id") or ""),
                )
            )
        ).scalar_one_or_none()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, failed or f"{adapter.name} didn't accept {email}.")
    core.log(db, org, key, "subscribed", f"{principal.full_name or 'Someone'} added {email} to {adapter.name}.", connection=connection, user_id=principal.user_id)
    record_audit(db, organization_id=org, action="marketing_subscribed", entity_type=record_type, entity_id=record.id, entity_label=email, actor_user_id=principal.user_id)
    await db.commit()
    return {"ok": True, "message": f"Added {email} to {adapter.name}." if summary["sent"] else f"{email} is already in {adapter.name}.", "summary": summary}


@router.post("/email-marketing/records/{record_type}/{record_id}/unsubscribe", dependencies=FEATURE)
async def unsubscribe_record(
    record_type: Literal["contact", "lead"],
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    """Stop marketing email to this person everywhere: AIVA's do-not-contact list and every connected tool."""

    org = principal.organization_id
    record = await _record(db, org, record_type, record_id)
    email = normal_email(record.email)
    if not email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"This {record_type} has no valid email address.")
    db.sync_session.info["automation_disabled"] = True
    await db.execute(
        pg_insert(SuppressionEntry)
        .values(id=uuid.uuid4(), organization_id=org, kind="email", value=email, reason="unsubscribed",
                note=f"Unsubscribed from marketing email by {principal.full_name or 'a team member'}."[:300], created_by_user_id=principal.user_id)
        .on_conflict_do_nothing(constraint="uq_suppression_entries_value")
    )
    for model in (Contact, Lead):
        await db.execute(
            update(model).where(model.organization_id == org, func.lower(model.email) == email).values(email_opt_out=True).execution_options(synchronize_session=False)
        )
    results = []
    for key, connection, settings in await _tools(db, org):
        adapter = adapter_for(key)
        if adapter.list_required and not settings.get("list_id"):
            continue
        try:
            await _bounded(key, adapter.unsubscribe(db, connection, settings, [email]))
            links = (
                await db.execute(
                    select(IntegrationRecordLink).where(
                        IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == key,
                        IntegrationRecordLink.external_type == LINK_TYPE, IntegrationRecordLink.external_id == email,
                    )
                )
            ).scalars().all()
            for link in links:
                if link.status not in ("cleaned", "bounced", "complaint"):
                    link.status = "unsubscribed"
                    link.data = {**(link.data or {}), "opted_out_in": "AIVA", "opted_out_at": core.utcnow().isoformat()}
            results.append({"key": key, "name": adapter.name, "ok": True})
            core.log(db, org, key, "unsubscribed_in_tool", f"{principal.full_name or 'Someone'} unsubscribed {email} in {adapter.name}.", connection=connection, user_id=principal.user_id)
        except asyncio.TimeoutError:
            results.append({"key": key, "name": adapter.name, "ok": False, "error": f"{adapter.name} didn't answer in time; the next sync will unsubscribe them."})
        except IntegrationError as exc:
            results.append({"key": key, "name": adapter.name, "ok": False, "error": f"{exc} The next sync will try again."})
    record_audit(db, organization_id=org, action="marketing_unsubscribed", entity_type=record_type, entity_id=record.id, entity_label=email, actor_user_id=principal.user_id)
    await db.commit()
    failed = [r for r in results if not r["ok"]]
    message = f"{email} won't get marketing email any more" + (f" (also unsubscribed in {', '.join(r['name'] for r in results if r['ok'])})" if any(r["ok"] for r in results) else "") + "."
    return {"ok": not failed, "message": message, "tools": results}
