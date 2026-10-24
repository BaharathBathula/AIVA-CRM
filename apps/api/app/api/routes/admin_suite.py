"""Settings for billing add-ons, credits, AI words, data requests,
territories, licenses, sharing defaults, workspace security and support
access, plus the generic settings-namespace endpoints."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    Principal,
    client_ip,
    get_current_principal,
    invalidate_security_cache,
    require_feature,
    require_permission,
)
from app.core.config import get_settings
from app.core.plans import ADDONS, PLANS
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.editions import Subscription
from app.models.governance import AiUsageEvent, CreditTransaction, DataRequest, OrgAddon, SupportAccessGrant, Territory
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.platform import Notification
from app.models.user import User
from app.services import metering, workspace_settings
from app.services.automation import invalidate_automation_cache, record_audit
from app.services.billing import active_seats, add_month, get_subscription, record_invoice
from app.services.lookups import names_for
from app.services.territories import FIELDS as TERRITORY_FIELDS
from app.services.territories import OPERATORS as TERRITORY_OPERATORS
from app.services.territories import as_context, find_territory, next_member

router = APIRouter(tags=["Workspace administration"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Settings namespaces
# ===========================================================================


class SettingsPayload(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str | None] = Field(default_factory=dict)


@router.get("/workspace-settings/{namespace}")
async def read_settings(namespace: str, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    if namespace not in workspace_settings.NAMESPACES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown settings section.")
    value = await workspace_settings.get(db, principal.organization_id, namespace)
    return {
        "namespace": namespace,
        "data": value.model_dump(mode="json"),
        "secrets": await workspace_settings.secrets_set(db, principal.organization_id, namespace),
        "can_edit": principal.can(workspace_settings.PERMISSIONS[namespace]),
    }


@router.put("/workspace-settings/{namespace}")
async def write_settings(
    namespace: str,
    payload: SettingsPayload,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    if namespace not in workspace_settings.NAMESPACES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown settings section.")
    if not principal.can(workspace_settings.PERMISSIONS[namespace]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role doesn't allow this action. Ask an administrator for access.")
    if namespace == "stages":
        await _check_stage_removal(db, principal, payload.data)
    if namespace == "security":
        await _check_lockout(db, principal, payload.data)
    try:
        value, diff = await workspace_settings.save(db, principal.organization_id, namespace, payload.data, principal.user_id, payload.secrets)
    except workspace_settings.SettingsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if diff:
        record_audit(
            db,
            organization_id=principal.organization_id,
            action="settings_update",
            entity_type="workspace_settings",
            entity_label=namespace.replace("_", " ").title(),
            changes=diff,
        )
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    invalidate_security_cache(principal.organization_id)
    return {
        "namespace": namespace,
        "data": value.model_dump(mode="json"),
        "secrets": await workspace_settings.secrets_set(db, principal.organization_id, namespace),
        "can_edit": True,
    }


async def _check_stage_removal(db: AsyncSession, principal: Principal, data: dict[str, Any]) -> None:
    """Don't let a stage disappear while records still use it."""

    current = await workspace_settings.get(db, principal.organization_id, "stages")
    for object_type, model, column in (("contact", Contact, Contact.stage), ("account", Account, Account.lifecycle_stage)):
        if object_type not in data:
            continue
        new_keys = {str(s.get("key")) for s in data[object_type] or [] if isinstance(s, dict)}
        removed = [s.key for s in getattr(current, object_type) if s.key not in new_keys]
        for key in removed:
            count = (
                await db.execute(select(func.count()).select_from(model).where(model.organization_id == principal.organization_id, column == key))
            ).scalar_one()
            if count:
                label = next((s.label for s in getattr(current, object_type) if s.key == key), key)
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{count} {object_type}{'s' if count != 1 else ''} are in the “{label}” stage. Move them to another stage before removing it.",
                )


async def _check_lockout(db: AsyncSession, principal: Principal, data: dict[str, Any]) -> None:
    """Refuse settings that would lock out the person saving them."""

    from app.api.dependencies.auth import _ip_allowed

    if data.get("require_mfa") and principal.api_key_id is None:
        user = await db.get(User, principal.user_id)
        if user is not None and not user.mfa_enabled:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Turn on two-step verification for your own account first (Profile → Password & security), then require it for everyone.",
            )
    allowlist = data.get("ip_allowlist")
    if not allowlist:
        return
    try:
        networks = workspace_settings.SecuritySettings(ip_allowlist=allowlist).ip_allowlist
    except Exception:  # noqa: BLE001 - the save itself reports the validation error
        return
    ip = db.sync_session.info.get("ip")
    if networks and not _ip_allowed(ip, networks):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Your current address ({ip or 'unknown'}) isn't in that list, so saving it would lock you out. Add it first.",
        )


# ===========================================================================
# Product add-ons
# ===========================================================================


def addon_out(row: OrgAddon | None, key: str, seats: int) -> dict[str, Any]:
    addon = ADDONS[key]
    quantity = row.quantity if row else 0
    units = seats if addon.per_seat else quantity
    return {
        **addon.to_dict(),
        "active": bool(row and (row.status == "active" or (row.renews_at and row.renews_at > utcnow()))),
        "status": row.status if row else None,
        "quantity": quantity,
        "monthly_cost": float(addon.price * units) if row and row.status == "active" else 0,
        "renews_at": row.renews_at if row else None,
        "cancelled_at": row.cancelled_at if row else None,
        "started_at": row.started_at if row else None,
    }


@router.get("/billing/addons")
async def list_addons(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    rows = {r.addon_key: r for r in (await db.execute(select(OrgAddon).where(OrgAddon.organization_id == principal.organization_id))).scalars()}
    seats = await active_seats(db, principal.organization_id)
    subscription = await get_subscription(db, principal.organization_id)
    plan = PLANS.get(subscription.plan if subscription else "trial", PLANS["trial"])
    items = []
    for key, addon in ADDONS.items():
        item = addon_out(rows.get(key), key, seats)
        item["included_in_plan"] = bool(addon.features) and addon.features <= plan.features
        items.append(item)
    return {"items": items, "seats": seats, "plan": plan.key, "allowances": await metering.allowances(db, principal.organization_id)}


class AddonPurchase(BaseModel):
    quantity: int = Field(default=1, ge=1, le=100)


@router.post("/billing/addons/{key}")
async def buy_addon(key: str, payload: AddonPurchase, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    addon = ADDONS.get(key)
    if addon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown add-on.")
    quantity = 1 if addon.per_seat else min(payload.quantity, addon.max_quantity)
    subscription = await get_subscription(db, principal.organization_id)
    if subscription is None or subscription.provider == "sandbox":
        raise HTTPException(status.HTTP_409_CONFLICT, "A sandbox uses its production workspace's plan and add-ons.")
    plan = PLANS.get(subscription.plan, PLANS["trial"])
    if addon.features and addon.features <= plan.features:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{addon.name} is already included in your {plan.name} plan.")
    seats = await active_seats(db, principal.organization_id)

    from app.services import stripe_billing

    if stripe_billing.configured() and subscription.plan == "trial":
        # With real billing, add-ons are charged alongside a plan; a trial has nothing to bill against.
        raise HTTPException(status.HTTP_409_CONFLICT, "Your trial already includes every feature. Choose a plan first, then add credits or AI words.")
    if stripe_billing.configured():
        base = get_settings().app_base_url.rstrip("/")
        try:
            url = await stripe_billing.create_addon_checkout(
                subscription=subscription,
                addon=addon,
                quantity=seats if addon.per_seat else quantity,
                customer_email=subscription.billing_email or principal.email,
                success_url=f"{base}/settings/billing/add-ons?checkout=success",
                cancel_url=f"{base}/settings/billing/add-ons?checkout=canceled",
            )
        except stripe_billing.StripeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {exc}") from exc
        return {"redirect_url": url}

    row = (
        await db.execute(select(OrgAddon).where(OrgAddon.organization_id == principal.organization_id, OrgAddon.addon_key == key))
    ).scalar_one_or_none()
    now = utcnow()
    # A cancelled add-on that's still inside its paid month comes back without a new charge.
    resuming = bool(row and row.status == "cancelled" and row.renews_at and row.renews_at > now)
    previous = row.quantity if row and (row.status == "active" or resuming) else 0
    if row is None:
        row = OrgAddon(id=uuid.uuid4(), organization_id=principal.organization_id, addon_key=key, started_at=now)
        db.add(row)
    row.quantity = quantity
    row.status = "active"
    row.cancelled_at = None
    row.unit_price = addon.price
    row.renews_at = add_month(now) if not row.renews_at or row.renews_at <= now else row.renews_at
    row.provider = "demo"
    row.purchased_by_user_id = principal.user_id
    units = (0 if previous else seats) if addon.per_seat else quantity - previous
    if units > 0 and subscription.plan != "trial":
        await record_invoice(
            db,
            subscription,
            plan=plan,
            seats=seats,
            period_start=now,
            period_end=row.renews_at,
            amount=addon.price * units,
            description=f"{addon.name} add-on · {units} {'user' if addon.per_seat and units == 1 else 'users' if addon.per_seat else 'unit' if units == 1 else 'units'} · {now:%b %d} – {row.renews_at:%b %d, %Y}",
        )
    record_audit(
        db, organization_id=principal.organization_id, action="addon_purchase", entity_type="addon", entity_id=row.id,
        entity_label=addon.name, changes={"quantity": {"from": previous, "to": quantity}},
    )
    await db.commit()
    return {"redirect_url": None, "addon": addon_out(row, key, seats)}


@router.delete("/billing/addons/{key}")
async def cancel_addon(key: str, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(select(OrgAddon).where(OrgAddon.organization_id == principal.organization_id, OrgAddon.addon_key == key))
    ).scalar_one_or_none()
    if row is None or row.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That add-on isn't active.")
    if row.provider == "stripe":
        from app.services import stripe_billing

        try:
            await stripe_billing.cancel_subscription_at_period_end(row.stripe_subscription_id)
        except stripe_billing.StripeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe: {exc}") from exc
    row.status = "cancelled"
    row.cancelled_at = utcnow()
    record_audit(db, organization_id=principal.organization_id, action="addon_cancel", entity_type="addon", entity_id=row.id, entity_label=ADDONS[key].name if key in ADDONS else key)
    await db.commit()
    return {"addon": addon_out(row, key, await active_seats(db, principal.organization_id))}


# ===========================================================================
# Credit usage and AI word usage
# ===========================================================================

FEATURE_LABELS = {
    "enrichment": "Waterfall enrichment",
    "data_request": "Data requests",
    "drafts": "Email drafts",
    "summaries": "Record summaries",
    "ask": "Ask AIVA",
    "agents": "AI agents",
    "conversations": "Conversation notes",
    "field_prompts": "Field prompts",
}


def _period(value: str | None) -> str:
    if not value:
        return metering.period_key()
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use a month like 2026-10.") from exc
    return value


def _recent_periods(count: int = 6) -> list[str]:
    now = utcnow()
    out, year, month = [], now.year, now.month
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


@router.get("/credits/usage")
async def credit_usage(
    period: str | None = None,
    user_id: uuid.UUID | None = None,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    period = _period(period)
    everyone = principal.can("admin.settings") or principal.can("admin.users")
    scope_user = user_id if everyone else principal.user_id
    allowance = await metering.allowances(db, org)
    used = await metering.credits_used(db, org, period)
    by_user_rows = (
        await db.execute(
            select(CreditTransaction.user_id, func.sum(CreditTransaction.amount))
            .where(CreditTransaction.organization_id == org, CreditTransaction.period == period)
            .group_by(CreditTransaction.user_id)
        )
    ).all()
    by_feature_rows = (
        await db.execute(
            select(CreditTransaction.feature, func.sum(CreditTransaction.amount))
            .where(CreditTransaction.organization_id == org, CreditTransaction.period == period)
            .group_by(CreditTransaction.feature)
        )
    ).all()
    query = select(CreditTransaction).where(CreditTransaction.organization_id == org, CreditTransaction.period == period)
    if scope_user:
        query = query.where(CreditTransaction.user_id == scope_user)
    rows = (await db.execute(query.order_by(CreditTransaction.created_at.desc()).limit(300))).scalars().all()
    names = await names_for(db, org, "user", [r.user_id for r in rows] + [u for u, _ in by_user_rows])
    licenses = await workspace_settings.get(db, org, "licenses")
    trend = []
    for month in reversed(_recent_periods()):
        trend.append({"period": month, "used": await metering.credits_used(db, org, month)})
    return {
        "period": period,
        "allowance": allowance,
        "used": used,
        "remaining": max(0, allowance["credits_total"] - used) if period == metering.period_key() else None,
        "by_user": sorted(
            [
                {
                    "user_id": str(u) if u else None,
                    "name": names.get(str(u), "Workspace") if u else "Workspace",
                    "used": max(0, -int(total)),
                    "limit": licenses.credit_limits.get(str(u), licenses.credit_limit_default) if u else None,
                }
                for u, total in by_user_rows
                if everyone or u == principal.user_id
            ],
            key=lambda r: -r["used"],
        ),
        "by_feature": [{"feature": f, "label": FEATURE_LABELS.get(f, f.replace("_", " ").title()), "used": max(0, -int(total))} for f, total in by_feature_rows],
        "transactions": [
            {
                "id": str(r.id),
                "created_at": r.created_at,
                "user_name": names.get(str(r.user_id)) if r.user_id else "Workspace",
                "kind": r.kind,
                "amount": r.amount,
                "feature": FEATURE_LABELS.get(r.feature, r.feature),
                "description": r.description,
                "record_type": r.record_type,
                "record_id": str(r.record_id) if r.record_id else None,
            }
            for r in rows
        ],
        "trend": trend,
        "periods": _recent_periods(),
        "can_see_everyone": everyone,
    }


@router.get("/credits/usage.csv")
async def credit_usage_csv(period: str | None = None, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    period = _period(period)
    rows = (
        await db.execute(
            select(CreditTransaction)
            .where(CreditTransaction.organization_id == principal.organization_id, CreditTransaction.period == period)
            .order_by(CreditTransaction.created_at)
        )
    ).scalars().all()
    names = await names_for(db, principal.organization_id, "user", [r.user_id for r in rows])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "User", "Feature", "Type", "Credits", "Description"])
    for r in rows:
        writer.writerow([r.created_at.isoformat(), names.get(str(r.user_id), "Workspace"), FEATURE_LABELS.get(r.feature, r.feature), r.kind, r.amount, r.description or ""])
    return Response(buffer.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="aiva-credits-{period}.csv"'})


@router.get("/ai-usage")
async def ai_usage(period: str | None = None, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    period = _period(period)
    everyone = principal.can("admin.settings") or principal.can("admin.users")
    allowance = await metering.allowances(db, org)
    used = await metering.words_used(db, org, period)
    by_user = (
        await db.execute(
            select(AiUsageEvent.user_id, func.sum(AiUsageEvent.words), func.count())
            .where(AiUsageEvent.organization_id == org, AiUsageEvent.period == period)
            .group_by(AiUsageEvent.user_id)
        )
    ).all()
    by_feature = (
        await db.execute(
            select(AiUsageEvent.feature, AiUsageEvent.source, func.sum(AiUsageEvent.words), func.count())
            .where(AiUsageEvent.organization_id == org, AiUsageEvent.period == period)
            .group_by(AiUsageEvent.feature, AiUsageEvent.source)
        )
    ).all()
    daily = (
        await db.execute(
            select(func.date(AiUsageEvent.created_at), func.sum(AiUsageEvent.words))
            .where(AiUsageEvent.organization_id == org, AiUsageEvent.period == period)
            .group_by(func.date(AiUsageEvent.created_at))
            .order_by(func.date(AiUsageEvent.created_at))
        )
    ).all()
    names = await names_for(db, org, "user", [u for u, _, _ in by_user])
    licenses = await workspace_settings.get(db, org, "licenses")
    features: dict[str, dict[str, Any]] = {}
    for feature, source, words, count in by_feature:
        entry = features.setdefault(feature, {"feature": feature, "label": FEATURE_LABELS.get(feature, feature.title()), "words": 0, "requests": 0, "claude": 0, "rules": 0})
        entry["words"] += int(words or 0)
        entry["requests"] += int(count)
        entry[source if source in ("claude", "rules") else "rules"] += int(words or 0)
    from app.services import ai

    return {
        "period": period,
        "allowance": allowance["ai_words_total"],
        "allowance_detail": allowance,
        "used": used,
        "remaining": max(0, allowance["ai_words_total"] - used) if period == metering.period_key() else None,
        "by_user": sorted(
            [
                {
                    "user_id": str(u) if u else None,
                    "name": names.get(str(u), "Workspace (agents)") if u else "Workspace (agents)",
                    "words": int(w or 0),
                    "requests": int(c),
                    "limit": licenses.ai_word_limits.get(str(u), licenses.ai_word_limit_default) if u else None,
                }
                for u, w, c in by_user
                if everyone or u == principal.user_id
            ],
            key=lambda r: -r["words"],
        ),
        "by_feature": sorted(features.values(), key=lambda r: -r["words"]),
        "daily": [{"date": d.isoformat() if hasattr(d, "isoformat") else str(d), "words": int(w or 0)} for d, w in daily],
        "periods": _recent_periods(),
        "model_source": ai.source_label(),
        "can_see_everyone": everyone,
    }


# ===========================================================================
# Data requests
# ===========================================================================

REQUEST_FIELDS = {
    "email": ("Email", 1),
    "phone": ("Phone", 1),
    "mobile": ("Mobile phone", 5),
    "job_title": ("Job title", 1),
    "company": ("Company", 1),
    "linkedin": ("LinkedIn profile", 1),
}
REQUEST_MODELS = {"lead": Lead, "contact": Contact}


def _record_label(record: Any) -> str:
    return f"{record.first_name or ''} {record.last_name or ''}".strip() or record.email or "Unnamed"


def request_out(row: DataRequest, names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "record_type": row.record_type,
        "record_id": str(row.record_id),
        "record_label": row.record_label,
        "href": f"/{'leads' if row.record_type == 'lead' else 'contacts'}/{row.record_id}",
        "fields": list(row.fields or []),
        "field_labels": [REQUEST_FIELDS.get(f, (f, 1))[0] for f in row.fields or []],
        "status": row.status,
        "note": row.note,
        "requested_by": names.get(str(row.requested_by_user_id)) if row.requested_by_user_id else None,
        "requested_by_user_id": str(row.requested_by_user_id) if row.requested_by_user_id else None,
        "assigned_to": names.get(str(row.assigned_to_user_id)) if row.assigned_to_user_id else None,
        "assigned_to_user_id": str(row.assigned_to_user_id) if row.assigned_to_user_id else None,
        "fulfilled_by": names.get(str(row.fulfilled_by_user_id)) if row.fulfilled_by_user_id else None,
        "fulfilled_at": row.fulfilled_at,
        "result": row.result or {},
        "credits_charged": row.credits_charged,
        "credit_cost": sum(REQUEST_FIELDS.get(f, ("", 1))[1] for f in row.fields or []),
        "due_at": row.due_at,
        "created_at": row.created_at,
        "overdue": bool(row.status == "open" and row.due_at and row.due_at < utcnow()),
    }


@router.get("/data-requests")
async def list_data_requests(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(open|fulfilled|not_found|cancelled)$"),
    mine: bool = False,
    record_type: str | None = None,
    record_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(DataRequest).where(DataRequest.organization_id == org)
    if status_filter:
        query = query.where(DataRequest.status == status_filter)
    if mine or not (principal.can("data.requests") or principal.can("records.view_all")):
        query = query.where(DataRequest.requested_by_user_id == principal.user_id)
    if record_type and record_id:
        query = query.where(DataRequest.record_type == record_type, DataRequest.record_id == record_id)
    rows = (await db.execute(query.order_by(DataRequest.created_at.desc()).limit(500))).scalars().all()
    names = await names_for(db, org, "user", [u for r in rows for u in (r.requested_by_user_id, r.assigned_to_user_id, r.fulfilled_by_user_id)])
    counts = dict(
        (
            await db.execute(select(DataRequest.status, func.count()).where(DataRequest.organization_id == org).group_by(DataRequest.status))
        ).all()
    )
    return {
        "items": [request_out(r, names) for r in rows],
        "counts": {k: int(v) for k, v in counts.items()},
        "fields": [{"key": k, "label": v[0], "credits": v[1]} for k, v in REQUEST_FIELDS.items()],
        "can_work": principal.can("data.requests"),
    }


class DataRequestPayload(BaseModel):
    record_type: str = Field(pattern="^(lead|contact)$")
    record_id: uuid.UUID
    fields: list[str] = Field(min_length=1, max_length=6)
    note: str | None = Field(default=None, max_length=2000)


@router.post("/data-requests", status_code=status.HTTP_201_CREATED)
async def create_data_request(payload: DataRequestPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    fields = [f for f in dict.fromkeys(payload.fields) if f in REQUEST_FIELDS]
    if not fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick what you need: email, phone, mobile, title, company or LinkedIn.")
    record = await db.get(REQUEST_MODELS[payload.record_type], payload.record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    duplicate = (
        await db.execute(
            select(DataRequest).where(
                DataRequest.organization_id == principal.organization_id,
                DataRequest.record_id == record.id,
                DataRequest.status == "open",
            )
        )
    ).scalars().first()
    if duplicate is not None:
        merged = list(dict.fromkeys([*duplicate.fields, *fields]))
        duplicate.fields = merged
        if payload.note:
            duplicate.note = f"{duplicate.note}\n{payload.note}".strip() if duplicate.note else payload.note
        await db.commit()
        names = await names_for(db, principal.organization_id, "user", [duplicate.requested_by_user_id])
        return request_out(duplicate, names)
    row = DataRequest(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        record_type=payload.record_type,
        record_id=record.id,
        record_label=_record_label(record)[:300],
        fields=fields,
        note=payload.note,
        requested_by_user_id=principal.user_id,
        due_at=utcnow() + timedelta(days=2),
    )
    db.add(row)
    # Let the people who work requests know.
    workers = (
        await db.execute(
            select(OrganizationMembership.user_id, OrganizationMembership.role).where(
                OrganizationMembership.organization_id == principal.organization_id, OrganizationMembership.status == "active"
            )
        )
    ).all()
    from app.api.dependencies.auth import resolve_role_permissions

    for user_id, role in workers:
        if user_id == principal.user_id:
            continue
        if "data.requests" in await resolve_role_permissions(db, principal.organization_id, role):
            db.add(
                Notification(
                    id=uuid.uuid4(), organization_id=principal.organization_id, user_id=user_id, kind="data_request",
                    title=f"Data request: {row.record_label}"[:200],
                    body=f"{principal.full_name} needs {', '.join(REQUEST_FIELDS[f][0].lower() for f in fields)}.",
                    link="/settings/data-requests", is_read=False,
                )
            )
    await db.commit()
    return request_out(row, {str(principal.user_id): principal.full_name})


class FulfilPayload(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=2000)


async def _request_for_work(db: AsyncSession, principal: Principal, request_id: uuid.UUID) -> DataRequest:
    row = await db.get(DataRequest, request_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found.")
    return row


def _apply_values(record: Any, values: dict[str, str]) -> dict[str, str]:
    applied = {}
    mapping = {"email": "email", "phone": "phone", "job_title": "job_title"}
    for key, value in values.items():
        value = (value or "").strip()
        if not value:
            continue
        if key == "email":
            if "@" not in value or " " in value:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{value} isn't an email address.")
            value = value.lower()
        if key in mapping and hasattr(record, mapping[key]):
            setattr(record, mapping[key], value[:320])
            applied[key] = value
        elif key == "mobile" and hasattr(record, "mobile"):
            record.mobile = value[:50]
            applied[key] = value
        elif key == "mobile":
            record.phone = record.phone or value[:50]
            applied[key] = value
        elif key == "linkedin" and hasattr(record, "linkedin_url"):
            record.linkedin_url = value[:500]
            applied[key] = value
        elif key == "company" and hasattr(record, "company_name"):
            record.company_name = value[:200]
            applied[key] = value
        else:
            record.custom_fields = {**(record.custom_fields or {}), f"requested_{key}": value[:500]}
            applied[key] = value
    return applied


@router.post("/data-requests/{request_id}/fulfil")
async def fulfil_data_request(request_id: uuid.UUID, payload: FulfilPayload, principal: Principal = Depends(require_permission("data.requests")), db: AsyncSession = Depends(get_db)):
    row = await _request_for_work(db, principal, request_id)
    if row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "This request is already closed.")
    record = await db.get(REQUEST_MODELS[row.record_type], row.record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The record was deleted.")
    applied = _apply_values(record, {k: v for k, v in payload.values.items() if k in row.fields})
    if not applied:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter at least one of the requested values, or mark it not found.")
    cost = sum(REQUEST_FIELDS[k][1] for k in applied)
    await metering.charge_credits(
        db, principal.organization_id, row.requested_by_user_id, cost, "data_request",
        record_type=row.record_type, record_id=row.record_id, description=f"{', '.join(REQUEST_FIELDS[k][0] for k in applied)} for {row.record_label}",
    )
    row.status = "fulfilled"
    row.result = {**applied, **({"note": payload.note} if payload.note else {})}
    row.fulfilled_at = utcnow()
    row.fulfilled_by_user_id = principal.user_id
    row.credits_charged = cost
    if row.requested_by_user_id and row.requested_by_user_id != principal.user_id:
        db.add(
            Notification(
                id=uuid.uuid4(), organization_id=principal.organization_id, user_id=row.requested_by_user_id, kind="data_request",
                title=f"Data found for {row.record_label}"[:200], body=", ".join(f"{REQUEST_FIELDS[k][0]}: {v}" for k, v in applied.items())[:500],
                link=f"/{'leads' if row.record_type == 'lead' else 'contacts'}/{row.record_id}", is_read=False,
            )
        )
    await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.requested_by_user_id, row.fulfilled_by_user_id])
    return request_out(row, names)


class ClosePayload(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/data-requests/{request_id}/not-found")
async def request_not_found(request_id: uuid.UUID, payload: ClosePayload, principal: Principal = Depends(require_permission("data.requests")), db: AsyncSession = Depends(get_db)):
    row = await _request_for_work(db, principal, request_id)
    if row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "This request is already closed.")
    row.status = "not_found"
    row.result = {"note": payload.note} if payload.note else {}
    row.fulfilled_at = utcnow()
    row.fulfilled_by_user_id = principal.user_id
    if row.requested_by_user_id and row.requested_by_user_id != principal.user_id:
        db.add(
            Notification(
                id=uuid.uuid4(), organization_id=principal.organization_id, user_id=row.requested_by_user_id, kind="data_request",
                title=f"No data found for {row.record_label}"[:200], body=payload.note or "Nothing was charged.", link="/settings/data-requests", is_read=False,
            )
        )
    await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.requested_by_user_id, row.fulfilled_by_user_id])
    return request_out(row, names)


@router.post("/data-requests/{request_id}/cancel")
async def cancel_data_request(request_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _request_for_work(db, principal, request_id)
    if row.requested_by_user_id != principal.user_id and not principal.can("data.requests"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who asked can cancel this request.")
    if row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "This request is already closed.")
    row.status = "cancelled"
    await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.requested_by_user_id])
    return request_out(row, names)


class AssignPayload(BaseModel):
    user_id: uuid.UUID | None = None


@router.post("/data-requests/{request_id}/assign")
async def assign_data_request(request_id: uuid.UUID, payload: AssignPayload, principal: Principal = Depends(require_permission("data.requests")), db: AsyncSession = Depends(get_db)):
    row = await _request_for_work(db, principal, request_id)
    row.assigned_to_user_id = payload.user_id
    await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.requested_by_user_id, row.assigned_to_user_id])
    return request_out(row, names)


@router.post("/data-requests/{request_id}/auto")
async def auto_fulfil(request_id: uuid.UUID, principal: Principal = Depends(require_permission("data.requests")), db: AsyncSession = Depends(get_db)):
    """Try the waterfall enrichment providers for the requested fields."""

    from app.services import waterfall

    row = await _request_for_work(db, principal, request_id)
    if row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "This request is already closed.")
    record = await db.get(REQUEST_MODELS[row.record_type], row.record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The record was deleted.")
    result = await waterfall.run(db, principal, row.record_type, record, wanted=set(row.fields), charge=False)
    found = {k: v for k, v in result["found"].items() if k in row.fields}
    if not found:
        await db.commit()
        return {"found": {}, "tried": result["tried"], "request": request_out(row, await names_for(db, principal.organization_id, "user", [row.requested_by_user_id]))}
    return {
        "found": found,
        "tried": result["tried"],
        "request": await fulfil_data_request(request_id, FulfilPayload(values=found, note="Found by waterfall enrichment."), principal, db),
    }


# ===========================================================================
# Territories
# ===========================================================================


class TerritoryRule(BaseModel):
    field: str = Field(min_length=1, max_length=80)
    operator: str = Field(pattern="^(in|not_in|contains|starts_with|between|gte|lte|is_empty)$")
    values: list[str] = Field(default_factory=list, max_length=500)


class TerritoryPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str = Field(default="blue", max_length=20)
    match: str = Field(default="all", pattern="^(all|any)$")
    rules: list[TerritoryRule] = Field(default_factory=list, max_length=30)
    member_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    assignment: str = Field(default="round_robin", pattern="^(round_robin|territory_only)$")
    applies_to: list[str] = Field(default_factory=lambda: ["account", "lead"])
    auto_assign: bool = True
    priority: int = Field(default=100, ge=1, le=10000)
    is_active: bool = True


async def _territory_counts(db: AsyncSession, org: uuid.UUID) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for kind, model in (("accounts", Account), ("leads", Lead)):
        for territory_id, count in (
            await db.execute(
                select(model.territory_id, func.count()).where(model.organization_id == org, model.territory_id.is_not(None)).group_by(model.territory_id)
            )
        ).all():
            counts.setdefault(str(territory_id), {"accounts": 0, "leads": 0})[kind] = int(count)
    return counts


def territory_out(row: Territory, names: dict[str, str], counts: dict[str, dict[str, int]]) -> dict[str, Any]:
    return {
        **as_context(row),
        "description": row.description,
        "color": row.color,
        "members": [{"id": m, "name": names.get(m, "Former user")} for m in (str(x) for x in row.member_user_ids or [])],
        "accounts": counts.get(str(row.id), {}).get("accounts", 0),
        "leads": counts.get(str(row.id), {}).get("leads", 0),
        "created_at": row.created_at,
    }


def _validate_territory(payload: TerritoryPayload) -> None:
    applies = [a for a in payload.applies_to if a in ("account", "lead")]
    if not applies:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose whether the territory covers accounts, leads or both.")
    payload.applies_to = applies
    for rule in payload.rules:
        if not rule.field.startswith("cf.") and not any(rule.field in TERRITORY_FIELDS[a] for a in applies):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"“{rule.field}” isn't a field territories can use.")
        if rule.operator != "is_empty" and not [v for v in rule.values if str(v).strip()]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Every rule needs at least one value.")
    if not payload.rules:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one rule, like “Country is USA”.")


async def _check_members(db: AsyncSession, org: uuid.UUID, ids: list[uuid.UUID]) -> None:
    if not ids:
        return
    found = set(
        (
            await db.execute(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.organization_id == org, OrganizationMembership.user_id.in_(ids), OrganizationMembership.status == "active"
                )
            )
        ).scalars()
    )
    if len(found) != len(set(ids)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Territory members must be active people in this workspace.")


@router.get("/territories", dependencies=[Depends(require_feature("territories"))])
async def list_territories(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    rows = (await db.execute(select(Territory).where(Territory.organization_id == org).order_by(Territory.priority, Territory.name))).scalars().all()
    names = await names_for(db, org, "user", [m for r in rows for m in r.member_user_ids or []])
    counts = await _territory_counts(db, org)
    unassigned = {
        "accounts": (await db.execute(select(func.count()).select_from(Account).where(Account.organization_id == org, Account.territory_id.is_(None)))).scalar_one(),
        "leads": (await db.execute(select(func.count()).select_from(Lead).where(Lead.organization_id == org, Lead.territory_id.is_(None)))).scalar_one(),
    }
    return {
        "items": [territory_out(r, names, counts) for r in rows],
        "unassigned": unassigned,
        "fields": {kind: [{"key": k, "label": v["label"], "type": v["type"]} for k, v in fields.items()] for kind, fields in TERRITORY_FIELDS.items()},
        "operators": TERRITORY_OPERATORS,
        "can_manage": principal.can("admin.settings"),
    }


@router.post("/territories", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("territories"))])
async def create_territory(payload: TerritoryPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    _validate_territory(payload)
    await _check_members(db, principal.organization_id, payload.member_user_ids)
    exists = (
        await db.execute(select(Territory.id).where(Territory.organization_id == principal.organization_id, func.lower(Territory.name) == payload.name.strip().lower()))
    ).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "A territory with that name already exists.")
    row = Territory(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        **{**payload.model_dump(exclude={"rules", "member_user_ids"}), "name": payload.name.strip()},
        rules=[r.model_dump() for r in payload.rules],
        member_user_ids=[str(m) for m in payload.member_user_ids],
    )
    db.add(row)
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="territory", entity_id=row.id, entity_label=row.name)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    names = await names_for(db, principal.organization_id, "user", row.member_user_ids)
    return territory_out(row, names, {})


@router.put("/territories/{territory_id}", dependencies=[Depends(require_feature("territories"))])
async def update_territory(territory_id: uuid.UUID, payload: TerritoryPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Territory, territory_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Territory not found.")
    _validate_territory(payload)
    await _check_members(db, principal.organization_id, payload.member_user_ids)
    clash = (
        await db.execute(
            select(Territory.id).where(
                Territory.organization_id == principal.organization_id, func.lower(Territory.name) == payload.name.strip().lower(), Territory.id != row.id
            )
        )
    ).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "A territory with that name already exists.")
    for key, value in payload.model_dump(exclude={"rules", "member_user_ids"}).items():
        setattr(row, key, value)
    row.name = payload.name.strip()
    row.rules = [r.model_dump() for r in payload.rules]
    row.member_user_ids = [str(m) for m in payload.member_user_ids]
    record_audit(db, organization_id=principal.organization_id, action="update", entity_type="territory", entity_id=row.id, entity_label=row.name)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    names = await names_for(db, principal.organization_id, "user", row.member_user_ids)
    return territory_out(row, names, await _territory_counts(db, principal.organization_id))


@router.delete("/territories/{territory_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("territories"))])
async def delete_territory(territory_id: uuid.UUID, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Territory, territory_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Territory not found.")
    record_audit(db, organization_id=principal.organization_id, action="delete", entity_type="territory", entity_id=row.id, entity_label=row.name)
    await db.delete(row)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ApplyPayload(BaseModel):
    reassign_owners: bool = False
    only_untagged: bool = True


@router.post("/territories/apply", dependencies=[Depends(require_feature("territories"))])
async def apply_territories(payload: ApplyPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Tag existing accounts and leads with the territory they match (and optionally re-route owners)."""

    org = principal.organization_id
    territories = [as_context(t) for t in (await db.execute(select(Territory).where(Territory.organization_id == org, Territory.is_active.is_(True)))).scalars()]
    if not territories:
        raise HTTPException(status.HTTP_409_CONFLICT, "Create a territory first.")
    tagged = reassigned = 0
    for kind, model in (("account", Account), ("lead", Lead)):
        query = select(model).where(model.organization_id == org)
        if payload.only_untagged:
            query = query.where(model.territory_id.is_(None))
        if kind == "lead":
            query = query.where(Lead.status != "converted")
        for record in (await db.execute(query.limit(20000))).scalars():
            territory = find_territory(record, kind, territories)
            if territory is None:
                continue
            new_id = uuid.UUID(territory["id"])
            if record.territory_id != new_id:
                record.territory_id = new_id
                tagged += 1
            if payload.reassign_owners and territory.get("assignment") == "round_robin":
                members = territory.get("member_user_ids") or []
                if members and str(record.owner_user_id) not in members:
                    record.owner_user_id = next_member(territory)
                    reassigned += 1
    record_audit(db, organization_id=org, action="territories_applied", entity_type="territory", entity_label="All territories", changes={"tagged": tagged, "reassigned": reassigned})
    await db.commit()
    return {"tagged": tagged, "reassigned": reassigned}


@router.post("/territories/preview", dependencies=[Depends(require_feature("territories"))])
async def preview_territory(payload: TerritoryPayload, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    from app.services.territories import territory_matches

    spec = {
        "rules": [r.model_dump() for r in payload.rules],
        "match": payload.match,
        "applies_to": [a for a in payload.applies_to if a in ("account", "lead")],
    }
    out: dict[str, Any] = {}
    for kind, model in (("account", Account), ("lead", Lead)):
        if kind not in spec["applies_to"]:
            continue
        rows = (await db.execute(select(model).where(model.organization_id == principal.organization_id).limit(20000))).scalars().all()
        matched = [r for r in rows if territory_matches(r, kind, spec)]
        out[kind] = {
            "count": len(matched),
            "sample": [
                {"id": str(r.id), "label": r.name if kind == "account" else _record_label(r), "href": f"/{kind}s/{r.id}"}
                for r in matched[:8]
            ],
        }
    return out


# ===========================================================================
# License settings
# ===========================================================================


@router.get("/licenses")
async def license_overview(principal: Principal = Depends(require_permission("admin.users")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == org))).scalar_one_or_none()
    rows = (
        await db.execute(
            select(OrganizationMembership, User)
            .join(User, User.id == OrganizationMembership.user_id)
            .where(OrganizationMembership.organization_id == org, OrganizationMembership.status.in_(("active", "invited")))
            .order_by(User.full_name)
        )
    ).all()
    period = metering.period_key()
    credit_by_user = dict(
        (
            await db.execute(
                select(CreditTransaction.user_id, func.sum(CreditTransaction.amount))
                .where(CreditTransaction.organization_id == org, CreditTransaction.period == period)
                .group_by(CreditTransaction.user_id)
            )
        ).all()
    )
    words_by_user = dict(
        (
            await db.execute(
                select(AiUsageEvent.user_id, func.sum(AiUsageEvent.words))
                .where(AiUsageEvent.organization_id == org, AiUsageEvent.period == period)
                .group_by(AiUsageEvent.user_id)
            )
        ).all()
    )
    licenses = await workspace_settings.get(db, org, "licenses")
    from app.services.auth import role_name

    active = sum(1 for m, _ in rows if m.status == "active")
    invited = sum(1 for m, _ in rows if m.status == "invited")
    return {
        "plan": subscription.plan if subscription else "trial",
        "seats_purchased": subscription.seats if subscription else active,
        "seats_active": active,
        "seats_invited": invited,
        "allowances": await metering.allowances(db, org),
        "settings": licenses.model_dump(mode="json"),
        "users": [
            {
                "id": str(user.id),
                "name": user.full_name,
                "email": user.email,
                "status": membership.status,
                "role": membership.role,
                "role_name": await role_name(db, org, membership.role),
                "last_login_at": user.last_login_at,
                "credits_used": max(0, -int(credit_by_user.get(user.id) or 0)),
                "credit_limit": licenses.credit_limits.get(str(user.id)),
                "ai_words_used": int(words_by_user.get(user.id) or 0),
                "ai_word_limit": licenses.ai_word_limits.get(str(user.id)),
                "mfa_enabled": user.mfa_enabled,
            }
            for membership, user in rows
        ],
    }


# ===========================================================================
# Security overview
# ===========================================================================


@router.get("/security/overview")
async def security_overview(request: Request, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    members = (
        await db.execute(
            select(User)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == org, OrganizationMembership.status == "active")
        )
    ).scalars().all()
    from app.models.identity import SsoConnection

    sso = (await db.execute(select(SsoConnection).where(SsoConnection.organization_id == org))).scalars().first()
    from app.models.organization import Organization

    organization = await db.get(Organization, org)
    settings = organization.settings or {}
    return {
        "your_ip": client_ip(request),
        "members": len(members),
        "without_mfa": [{"id": str(u.id), "name": u.full_name, "email": u.email} for u in members if not u.mfa_enabled],
        "sso_enabled": bool(sso and getattr(sso, "is_active", True)),
        "hipaa_mode": bool(settings.get("hipaa_mode")),
        "session_timeout_minutes": settings.get("session_timeout_minutes"),
        "settings": (await workspace_settings.get(db, org, "security")).model_dump(mode="json"),
    }


# ===========================================================================
# Support access
# ===========================================================================


class SupportGrantPayload(BaseModel):
    access_level: str = Field(default="read_only", pattern="^(read_only|full)$")
    hours: int = Field(default=24, ge=1, le=24 * 14)
    reason: str | None = Field(default=None, max_length=2000)


def grant_out(row: SupportAccessGrant, names: dict[str, str]) -> dict[str, Any]:
    now = utcnow()
    return {
        "id": str(row.id),
        "access_level": row.access_level,
        "reason": row.reason,
        "granted_by": names.get(str(row.granted_by_user_id)) if row.granted_by_user_id else None,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
        "revoked_by": names.get(str(row.revoked_by_user_id)) if row.revoked_by_user_id else None,
        "last_used_at": row.last_used_at,
        "use_count": row.use_count,
        "active": row.revoked_at is None and row.expires_at > now,
    }


@router.get("/support-access")
async def list_support_access(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(SupportAccessGrant).where(SupportAccessGrant.organization_id == principal.organization_id).order_by(SupportAccessGrant.created_at.desc()).limit(50)
        )
    ).scalars().all()
    names = await names_for(db, principal.organization_id, "user", [u for r in rows for u in (r.granted_by_user_id, r.revoked_by_user_id)])
    from app.models.platform import AuditLog

    sessions = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.organization_id == principal.organization_id, AuditLog.action == "support_access")
            .order_by(AuditLog.created_at.desc())
            .limit(30)
        )
    ).scalars().all()
    actors = await names_for(db, principal.organization_id, "user", [s.actor_user_id for s in sessions])
    items = [grant_out(r, names) for r in rows]
    return {
        "active": next((i for i in items if i["active"]), None),
        "history": items,
        "activity": [
            {"at": s.created_at, "who": actors.get(str(s.actor_user_id), "AIVA support"), "path": (s.changes or {}).get("path"), "ip": s.ip_address}
            for s in sessions
        ],
    }


@router.post("/support-access", status_code=status.HTTP_201_CREATED)
async def grant_support_access(payload: SupportGrantPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    if principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support staff can't extend their own access.")
    now = utcnow()
    for active in (
        await db.execute(
            select(SupportAccessGrant).where(
                SupportAccessGrant.organization_id == principal.organization_id, SupportAccessGrant.revoked_at.is_(None), SupportAccessGrant.expires_at > now
            )
        )
    ).scalars():
        active.revoked_at = now
        active.revoked_by_user_id = principal.user_id
    row = SupportAccessGrant(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        granted_by_user_id=principal.user_id,
        access_level=payload.access_level,
        reason=payload.reason,
        expires_at=now + timedelta(hours=payload.hours),
    )
    db.add(row)
    record_audit(
        db, organization_id=principal.organization_id, action="support_access_granted", entity_type="support_access", entity_id=row.id,
        entity_label=f"{'Full' if payload.access_level == 'full' else 'Read-only'} support access for {payload.hours} hours",
        changes={"reason": payload.reason, "expires_at": row.expires_at.isoformat()},
    )
    await db.commit()
    return grant_out(row, {str(principal.user_id): principal.full_name})


@router.post("/support-access/{grant_id}/revoke")
async def revoke_support_access(grant_id: uuid.UUID, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(SupportAccessGrant, grant_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Grant not found.")
    if principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support staff can't change support access.")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoked_by_user_id = principal.user_id
        record_audit(db, organization_id=principal.organization_id, action="support_access_revoked", entity_type="support_access", entity_id=row.id, entity_label="Support access")
        await db.commit()
    names = await names_for(db, principal.organization_id, "user", [row.granted_by_user_id, row.revoked_by_user_id])
    return grant_out(row, names)


# ===========================================================================
# Stage usage (Contact / Account fields & stages)
# ===========================================================================


@router.get("/stages")
async def stage_usage(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    stages = await workspace_settings.get(db, org, "stages")
    contact_counts = dict(
        (await db.execute(select(Contact.stage, func.count()).where(Contact.organization_id == org).group_by(Contact.stage))).all()
    )
    account_counts = dict(
        (await db.execute(select(Account.lifecycle_stage, func.count()).where(Account.organization_id == org).group_by(Account.lifecycle_stage))).all()
    )
    return {
        "contact": [{**s.model_dump(), "count": int(contact_counts.get(s.key, 0))} for s in stages.contact],
        "account": [{**s.model_dump(), "count": int(account_counts.get(s.key, 0))} for s in stages.account],
        "unknown": {
            "contact": {k: int(v) for k, v in contact_counts.items() if k not in {s.key for s in stages.contact}},
            "account": {k: int(v) for k, v in account_counts.items() if k not in {s.key for s in stages.account}},
        },
        "can_edit": principal.can("admin.settings"),
    }


class StageMove(BaseModel):
    object_type: str = Field(pattern="^(contact|account)$")
    from_stage: str = Field(min_length=1, max_length=40)
    to_stage: str = Field(min_length=1, max_length=40)


@router.post("/stages/move")
async def move_stage(payload: StageMove, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    org = principal.organization_id
    stages = await workspace_settings.get(db, org, "stages")
    if payload.to_stage not in workspace_settings.stage_keys(stages, payload.object_type):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a stage that exists.")
    model, column = (Contact, "stage") if payload.object_type == "contact" else (Account, "lifecycle_stage")
    rows = (
        await db.execute(select(model).where(model.organization_id == org, getattr(model, column) == payload.from_stage))
    ).scalars().all()
    for row in rows:
        setattr(row, column, payload.to_stage)
    await db.commit()
    return {"moved": len(rows)}


__all__ = ["router", "REQUEST_FIELDS"]
