"""Chargebee: subscriptions, MRR and renewals on AIVA accounts.

Connects with the site name and an API key (HTTP Basic, key as the user name).
Customers are matched to AIVA accounts (an existing link, then company name,
then the email's company domain, then a contact with that email) and linked with
``IntegrationRecordLink(account ↔ customer)``. Subscriptions are kept as
``IntegrationObject(object_type="subscription")`` with plan, status, MRR and the
next billing date. Chargebee payments and refunds appear on the account too.

Chargebee can't sign webhooks, so the address carries an unguessable token and
AIVA never trusts the posted body: it re-reads the event from the Chargebee API
by its id before acting. A sync every 15 minutes catches anything missed.
Optional tasks for the account owner on cancellations, failed payments and
upcoming renewals (once per subscription and term).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _pay_common as pay
from app.models.account import Account
from app.models.contact import Contact
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.models.task import Task
from app.services.enrichment import FREE_EMAIL_DOMAINS

KEY = "chargebee"
SITE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
SYNC_EVERY = timedelta(minutes=15)
PAGE = 100
LIVE_STATES = {"active", "in_trial", "non_renewing", "future"}
PERIOD_TO_MONTHS = {"day": Decimal(12) / Decimal(365), "week": Decimal(12) / Decimal(52), "month": Decimal(1), "year": Decimal(12)}
STATUS_LABELS = {
    "future": "Starts later", "in_trial": "In trial", "active": "Active", "non_renewing": "Cancels at term end",
    "paused": "Paused", "cancelled": "Cancelled", "transferred": "Transferred",
}


def clean_site(value: str | None) -> str:
    site = (value or "").strip().lower()
    site = re.sub(r"^https?://", "", site).split("/", 1)[0]
    site = site.removesuffix(".chargebee.com")
    if not SITE.match(site):
        raise IntegrationError("The site is the part before .chargebee.com, like acme or acme-test.", 422)
    return site


async def _call(site: str, api_key: str, method: str, path: str, *, params: dict[str, Any] | None = None,
                connection: IntegrationConnection | None = None) -> dict[str, Any]:
    response = await pay.http(method, f"https://{site}.chargebee.com/api/v2{path}", params=params, auth=(api_key, ""), headers={"Accept": "application/json"})
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if response.status_code >= 400:
        message = (body.get("message") if isinstance(body, dict) else None) or f"Chargebee answered {response.status_code}."
        if response.status_code == 401:
            message = "Chargebee didn't accept the API key. Create one under Settings → Configure Chargebee → API keys."
            if connection is not None:
                connection.status = "error"
                connection.last_error = message
        elif response.status_code == 404 and path.startswith("/events/"):
            message = "Chargebee has no such event."
        error = IntegrationError(f"Chargebee: {message}"[:400] if not message.startswith("Chargebee") else message, 502 if response.status_code >= 500 else 400)
        error.http_status = response.status_code  # type: ignore[attr-defined]
        raise error
    return body if isinstance(body, dict) else {}


async def call(connection: IntegrationConnection, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    site = (connection.config or {}).get("site") or ""
    api_key = core.secret(connection, "api_key") or ""
    if not site or not api_key:
        raise IntegrationError("Chargebee isn't connected. Add the site and an API key.", 409)
    return await _call(site, api_key, method, path, connection=connection, **kwargs)


def _url(connection: IntegrationConnection, kind: str, object_id: str) -> str:
    return f"https://{(connection.config or {}).get('site')}.chargebee.com/d/{kind}/{object_id}"


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    site = clean_site(values.get("site"))
    api_key = (values.get("api_key") or "").strip()
    if len(api_key) < 10:
        raise IntegrationError("Paste the full Chargebee API key.", 422)
    await _call(site, api_key, "GET", "/customers", params={"limit": 1})
    await _call(site, api_key, "GET", "/subscriptions", params={"limit": 1})
    return {"label": f"{site}.chargebee.com", "external_id": site, "config": {"site": site, "test_site": site.endswith("-test")}}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    connection.config = {**(connection.config or {}), "customers_cursor": None, "subscriptions_cursor": None, "synced_at": None}
    core.log(db, connection.organization_id, KEY, "webhook_pending",
             "Add the webhook URL from this page in Chargebee (Settings → Configure Chargebee → Webhooks) so changes arrive straight away. AIVA also syncs every 15 minutes.",
             connection=connection)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    found = await call(connection, "GET", "/subscriptions", params={"limit": 1, "status[in]": json.dumps(sorted(LIVE_STATES))})
    return f"Chargebee answered ({'has' if found.get('list') else 'no'} live subscriptions yet)."


# ---------------------------------------------------------------------------
# Matching customers to accounts
# ---------------------------------------------------------------------------


async def _linked_account(db: AsyncSession, organization_id: uuid.UUID, customer_id: str) -> uuid.UUID | None:
    link = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == organization_id, IntegrationRecordLink.provider == KEY,
                IntegrationRecordLink.external_type == "customer", IntegrationRecordLink.external_parent == "",
                IntegrationRecordLink.external_id == customer_id,
            )
        )
    ).scalar_one_or_none()
    return link.record_id if link is not None and link.status != "removed" else None


async def match_account(db: AsyncSession, connection: IntegrationConnection, customer: dict[str, Any]) -> uuid.UUID | None:
    org = connection.organization_id
    customer_id = str(customer.get("id") or "")
    if not customer_id:
        return None
    found = await _linked_account(db, org, customer_id)
    if found is not None:
        return found
    company = (customer.get("company") or "").strip()
    email = (customer.get("email") or "").strip().lower()
    account_id: uuid.UUID | None = None
    if company:
        account_id = (
            await db.execute(select(Account.id).where(Account.organization_id == org, func.lower(Account.name) == company.lower(), Account.is_archived.is_(False)).limit(1))
        ).scalar_one_or_none()
    domain = email.rsplit("@", 1)[1] if "@" in email else ""
    if account_id is None and domain and domain not in FREE_EMAIL_DOMAINS:
        account_id = (
            await db.execute(
                select(Account.id).where(
                    Account.organization_id == org, Account.is_archived.is_(False),
                    # Exact host match only (scheme, "www." and path stripped): "x.co" must not match "box.com".
                    or_(
                        func.regexp_replace(func.lower(Account.domain), r"^www\.", "") == domain.removeprefix("www."),
                        func.regexp_replace(func.lower(Account.website), r"^(https?://)?(www\.)?([^/:?#]+).*$", r"\3") == domain.removeprefix("www."),
                    ),
                ).limit(1)
            )
        ).scalar_one_or_none()
    if account_id is None and email:
        account_id = (
            await db.execute(select(Contact.account_id).where(Contact.organization_id == org, func.lower(Contact.email) == email, Contact.account_id.is_not(None)).limit(1))
        ).scalar_one_or_none()
    settings = await core.workspace_config(db, org, KEY)
    if account_id is None and settings.get("create_accounts"):
        name = company or (domain if domain and domain not in FREE_EMAIL_DOMAINS else f"{customer.get('first_name') or ''} {customer.get('last_name') or ''}".strip()) or email or customer_id
        account = Account(id=uuid.uuid4(), organization_id=org, name=name[:255], domain=domain if domain and domain not in FREE_EMAIL_DOMAINS else None,
                          lifecycle_stage="customer", owner_user_id=connection.connected_by_user_id)
        db.add(account)
        await db.flush()
        account_id = account.id
        core.log(db, org, KEY, "account_created", f"Created account {name} for Chargebee customer {customer_id}.", connection=connection)
    if account_id is not None:
        # One Chargebee customer per account is linked (the first one found); others still match by name or domain.
        taken = (
            await db.execute(
                select(IntegrationRecordLink.id).where(
                    IntegrationRecordLink.organization_id == org, IntegrationRecordLink.provider == KEY, IntegrationRecordLink.external_type == "customer",
                    IntegrationRecordLink.external_parent == "",
                    or_(IntegrationRecordLink.external_id == customer_id[:200],
                        (IntegrationRecordLink.record_type == "account") & (IntegrationRecordLink.record_id == account_id)),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if taken is None:
            db.add(IntegrationRecordLink(
                id=uuid.uuid4(), organization_id=org, provider=KEY, record_type="account", record_id=account_id, external_type="customer",
                external_parent="", external_id=customer_id[:200], external_url=_url(connection, "customers", customer_id),
                status="linked", data={"email": email or None, "company": company or None}, last_synced_at=pay.utcnow(),
            ))
            await db.flush()
    return account_id


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


def monthly_amount(subscription: dict[str, Any]) -> int:
    """MRR in minor units: Chargebee's own figure when it has one, else from the items."""

    if subscription.get("mrr") is not None:
        try:
            return int(subscription["mrr"])
        except (TypeError, ValueError):
            pass
    total = Decimal(0)
    period = Decimal(int(subscription.get("billing_period") or 1) or 1)
    unit = str(subscription.get("billing_period_unit") or "month")
    factor = PERIOD_TO_MONTHS.get(unit, Decimal(1)) / period
    for item in subscription.get("subscription_items") or []:
        if item.get("item_type", "plan") == "charge":
            continue
        amount = item.get("amount")
        if amount is None:
            amount = int(item.get("unit_price") or 0) * int(item.get("quantity") or 1)
        total += Decimal(int(amount or 0))
    if not subscription.get("subscription_items") and subscription.get("plan_amount") is not None:
        total = Decimal(int(subscription.get("plan_amount") or 0)) * Decimal(int(subscription.get("plan_quantity") or 1))
    return int((total * factor).to_integral_value())


def _plan(subscription: dict[str, Any]) -> str | None:
    items = subscription.get("subscription_items") or []
    plan = next((i for i in items if i.get("item_type", "plan") == "plan"), items[0] if items else None)
    return (plan or {}).get("item_price_id") or subscription.get("plan_id")


async def upsert_subscription(
    db: AsyncSession, connection: IntegrationConnection, subscription: dict[str, Any], customer: dict[str, Any] | None, *, cancelled_now: bool = False
) -> IntegrationObject | None:
    """Create or update the subscription. A cancellation raises a task when Chargebee says it just
    happened (``cancelled_now``) or AIVA saw the subscription live before; old cancellations found
    on a first import don't."""

    sub_id = str(subscription.get("id") or "")
    if not SAFE_ID.match(sub_id):
        return None
    org = connection.organization_id
    account_id = await match_account(db, connection, customer or {"id": subscription.get("customer_id")})
    currency = (subscription.get("currency_code") or "USD").upper()
    mrr_minor = monthly_amount(subscription)
    obj = await pay.find_object(db, org, KEY, "subscription", sub_id)
    previous = obj.status if obj is not None else None
    if obj is None:
        obj = IntegrationObject(id=uuid.uuid4(), organization_id=org, provider=KEY, object_type="subscription", external_id=sub_id, external_parent="")
        db.add(obj)
    status = str(subscription.get("status") or "")
    plan = _plan(subscription)
    obj.title = plan or "Subscription"
    obj.status = status[:60]
    obj.amount = pay.money(pay.from_minor(mrr_minor, currency))
    obj.currency = currency[:3]
    obj.email = ((customer or {}).get("email") or None)
    obj.url = _url(connection, "subscriptions", sub_id)
    obj.occurred_at = pay.parse_time(subscription.get("created_at"))
    obj.external_updated_at = pay.parse_time(subscription.get("updated_at")) or pay.utcnow()
    obj.account_id = account_id
    obj.data = {
        "plan": plan, "status_label": STATUS_LABELS.get(status, status.replace("_", " ").title()), "mrr": str(obj.amount),
        "billing_period": f"{subscription.get('billing_period') or 1} {subscription.get('billing_period_unit') or 'month'}",
        "next_billing_at": _iso(subscription.get("next_billing_at")), "current_term_end": _iso(subscription.get("current_term_end")),
        "cancelled_at": _iso(subscription.get("cancelled_at")), "cancel_reason": subscription.get("cancel_reason_code") or subscription.get("cancel_reason"),
        "trial_end": _iso(subscription.get("trial_end")), "customer_id": subscription.get("customer_id"),
    }
    await db.flush()
    if status == "cancelled" and (cancelled_now or previous not in {None, "cancelled"}):
        await _task(db, connection, obj, "cancelled", f"Subscription cancelled: {plan or sub_id}",
                    f"Chargebee subscription {sub_id} was cancelled" + (f" ({obj.data['cancel_reason']})." if obj.data.get("cancel_reason") else "."))
    return obj


def _iso(value: Any) -> str | None:
    parsed = pay.parse_time(value)
    return parsed.isoformat() if parsed else None


async def _task(db: AsyncSession, connection: IntegrationConnection, obj: IntegrationObject, kind: str, title: str, description: str, *, term: str | None = None) -> bool:
    settings = await core.workspace_config(db, connection.organization_id, KEY)
    wanted = {"cancelled": settings.get("task_on_cancel", True), "payment_failed": settings.get("task_on_payment_failed", True),
              "renewal": settings.get("task_on_renewal", False)}.get(kind, False)
    if not wanted or obj.account_id is None:
        return False
    dedupe = f"chargebee:{kind}:{obj.external_id}:{term or ''}"
    exists = (
        await db.execute(select(Task.id).where(Task.organization_id == connection.organization_id, Task.task_metadata["integration_key"].astext == dedupe).limit(1))
    ).scalar_one_or_none()
    if exists is not None:
        return False
    owner = (await db.execute(select(Account.owner_user_id).where(Account.id == obj.account_id))).scalar_one_or_none()
    db.add(Task(
        id=uuid.uuid4(), organization_id=connection.organization_id, title=title[:300], description=description[:2000],
        task_type="follow_up", status="open", priority="high" if kind != "renewal" else "medium", source="integration",
        due_at=pay.utcnow() + timedelta(days=1 if kind != "renewal" else 3), assigned_to_user_id=owner, created_by_user_id=None,
        is_recurring=False, is_ai_generated=False, tags=["chargebee", kind], account_id=obj.account_id,
        task_metadata={"integration_key": dedupe, "integration": KEY, "integration_object_id": str(obj.id)},
    ))
    return True


async def record_transaction(db: AsyncSession, connection: IntegrationConnection, transaction: dict[str, Any], customer: dict[str, Any] | None, refund: bool) -> None:
    txn_id = str(transaction.get("id") or "")
    if not SAFE_ID.match(txn_id) or transaction.get("status") not in {None, "success"}:
        return
    org = connection.organization_id
    kind = "refund" if refund else "payment"
    if await pay.find_object(db, org, KEY, kind, txn_id) is not None:
        return
    currency = (transaction.get("currency_code") or "USD").upper()
    account_id = await match_account(db, connection, customer or {"id": transaction.get("customer_id")})
    db.add(IntegrationObject(
        id=uuid.uuid4(), organization_id=org, provider=KEY, object_type=kind, external_id=txn_id, external_parent="",
        title="Chargebee refund" if refund else "Chargebee payment", status="succeeded",
        amount=pay.money(pay.from_minor(transaction.get("amount") or 0, currency)), currency=currency[:3],
        email=(customer or {}).get("email"), url=_url(connection, "transactions", txn_id), occurred_at=pay.parse_time(transaction.get("date")),
        external_updated_at=pay.utcnow(), account_id=account_id,
        data={"method": transaction.get("payment_method"), "subscription_id": transaction.get("subscription_id"), "applied": False},
    ))


# ---------------------------------------------------------------------------
# Events (re-read from the API) and sync
# ---------------------------------------------------------------------------


async def process_event(db: AsyncSession, connection: IntegrationConnection, event_id: str) -> str | None:
    if not SAFE_ID.match(event_id or ""):
        return None
    event = (await call(connection, "GET", f"/events/{event_id}")).get("event") or {}
    kind = str(event.get("event_type") or "")
    content = event.get("content") or {}
    customer = content.get("customer")
    if content.get("subscription"):
        obj = await upsert_subscription(db, connection, content["subscription"], customer, cancelled_now=kind == "subscription_cancelled")
        if obj is not None and kind == "subscription_renewal_reminder":
            term = obj.data.get("current_term_end") or obj.data.get("next_billing_at")
            await _task(db, connection, obj, "renewal", f"Renewal coming up: {obj.title}", f"Chargebee subscription {obj.external_id} renews on {(term or '')[:10]}.", term=term)
    elif customer and kind.startswith("customer_"):
        await match_account(db, connection, customer)
    if kind in {"payment_succeeded", "payment_refunded"} and content.get("transaction"):
        await record_transaction(db, connection, content["transaction"], customer, kind == "payment_refunded")
    if kind == "payment_failed" and content.get("subscription"):
        obj = await pay.find_object(db, connection.organization_id, KEY, "subscription", str(content["subscription"].get("id") or ""))
        if obj is not None:
            reason = (content.get("transaction") or {}).get("error_text") or "the card was declined"
            await _task(db, connection, obj, "payment_failed", f"Payment failed: {obj.title}", f"A Chargebee payment for subscription {obj.external_id} failed: {reason}.",
                        term=str((content.get("invoice") or {}).get("id") or event_id))
    connection.last_sync_at = pay.utcnow()
    return kind


async def sync(db: AsyncSession, connection: IntegrationConnection) -> int:
    """One page of changed customers and subscriptions since the last sync."""

    config = dict(connection.config or {})
    handled = 0
    for kind, path in (("customers", "/customers"), ("subscriptions", "/subscriptions")):
        # Changes since the cursor, oldest first; a long backlog is read one page per run,
        # continuing from Chargebee's offset until it's done, and only then does the cursor move.
        cursor = config.get(f"{kind}_cursor")
        offset = config.get(f"{kind}_offset")
        params: dict[str, Any] = {"limit": PAGE, "sort_by[asc]": "updated_at"}
        if cursor:
            params["updated_at[after]"] = int(cursor)
        if offset:
            params["offset"] = str(offset)[:500]
        found = await call(connection, "GET", path, params=params)
        latest = config.get(f"{kind}_pending_cursor") or cursor
        for entry in found.get("list") or []:
            if kind == "customers":
                customer = entry.get("customer") or {}
                await match_account(db, connection, customer)
                stamp = customer.get("updated_at")
            else:
                subscription = entry.get("subscription") or {}
                await upsert_subscription(db, connection, subscription, entry.get("customer"))
                stamp = subscription.get("updated_at")
            handled += 1
            if isinstance(stamp, int) and (latest is None or stamp > int(latest)):
                latest = stamp
        if found.get("next_offset"):
            config[f"{kind}_offset"] = found["next_offset"]
            config[f"{kind}_pending_cursor"] = latest
        else:
            config[f"{kind}_offset"] = None
            config[f"{kind}_pending_cursor"] = None
            config[f"{kind}_cursor"] = latest
    config["synced_at"] = pay.utcnow().isoformat()
    connection.config = config
    connection.last_sync_at = pay.utcnow()
    await renewal_tasks(db, connection)
    return handled


async def renewal_tasks(db: AsyncSession, connection: IntegrationConnection) -> None:
    settings = await core.workspace_config(db, connection.organization_id, KEY)
    if not settings.get("task_on_renewal"):
        return
    days = int(settings.get("renewal_days") or 30)
    horizon = pay.utcnow() + timedelta(days=days)
    rows = (
        await db.execute(
            select(IntegrationObject).where(
                IntegrationObject.organization_id == connection.organization_id, IntegrationObject.provider == KEY,
                IntegrationObject.object_type == "subscription", IntegrationObject.status.in_(("active", "non_renewing")),
                IntegrationObject.account_id.is_not(None),
            ).limit(500)
        )
    ).scalars().all()
    for obj in rows:
        term = (obj.data or {}).get("current_term_end") or (obj.data or {}).get("next_billing_at")
        when = pay.parse_time(term)
        if when is None or not pay.utcnow() <= when <= horizon:
            continue
        verb = "ends" if obj.status == "non_renewing" else "renews"
        await _task(db, connection, obj, "renewal", f"Renewal coming up: {obj.title}", f"Chargebee subscription {obj.external_id} {verb} on {when.date().isoformat()}.", term=term)


def due(connection: IntegrationConnection) -> bool:
    config = connection.config or {}
    if config.get("customers_offset") or config.get("subscriptions_offset"):
        return True  # still working through a backlog
    synced = pay.parse_time(config.get("synced_at"))
    return synced is None or synced < pay.utcnow() - SYNC_EVERY


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method != "POST":
        return JSONResponse({"ok": True, "message": "AIVA receives Chargebee events here (POST)."})
    body = await request.body()
    if len(body) > 1_000_000:
        return JSONResponse({"error": "too large"}, status_code=413)
    try:
        posted = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    event_id = str((posted or {}).get("id") or "") if isinstance(posted, dict) else ""
    if not SAFE_ID.match(event_id):
        return JSONResponse({"error": "no event id"}, status_code=400)
    try:
        kind = await process_event(db, connection, event_id)
    except IntegrationError as exc:
        if getattr(exc, "http_status", 0) in {400, 404}:
            core.log(db, connection.organization_id, KEY, "webhook_rejected", f"Ignored event {event_id[:40]}: Chargebee doesn't know it.", level="warning", connection=connection)
            return JSONResponse({"ignored": "unknown event"})
        raise
    return JSONResponse({"received": True, "event_type": kind})


def clean(config: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for name in ("create_accounts", "task_on_cancel", "task_on_payment_failed", "task_on_renewal"):
        if name in config:
            cleaned[name] = bool(config.get(name))
    if "renewal_days" in config:
        try:
            days = int(config.get("renewal_days") or 30)
        except (TypeError, ValueError) as exc:
            raise IntegrationError("Renewal warning is a number of days.", 422) from exc
        if not 1 <= days <= 120:
            raise IntegrationError("Warn about renewals 1 to 120 days ahead.", 422)
        cleaned["renewal_days"] = days
    return cleaned
