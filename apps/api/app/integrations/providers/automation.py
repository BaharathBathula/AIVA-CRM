"""Zapier, Make and n8n: outgoing webhooks on AIVA events.

How it works
------------
* **Subscriptions.** A ``WebhookSubscription`` sends one event (or every event,
  ``*``) to one URL. Admins paste URLs on the Webhooks page (``source="manual"``)
  or the automation platform subscribes itself through the REST hooks API with an
  AIVA API key (``source="rest_hook"``, Zapier's standard). Every subscription has
  its own signing secret.
* **Outbox.** The automation ``before_flush`` hook calls :func:`queue_events` for
  every record change. Matching subscriptions get a ``WebhookDelivery`` row in the
  *same transaction*, so a rolled-back change never sends anything and sending
  never slows down or breaks the person's save.
* **Delivery.** The scheduler calls :func:`run_due`, which posts pending
  deliveries (signed, ``X-AIVA-Signature: t=<unix>,v1=<hex HMAC-SHA256 of
  "<t>.<body>">``), retries failures with exponential backoff, and pauses a
  subscription after too many failures in a row (or at once when the receiver
  answers ``410 Gone``).
* **Safety.** Target URLs must be https in production and must resolve to public
  addresses; the connection is pinned to the checked address so DNS can't be
  switched to an internal host between the check and the request.
* **On/off.** While Zapier, Make or n8n is switched off, nothing new is queued for
  its subscriptions and anything already queued waits (up to a day) instead of
  being sent.

Subscriptions are never hard-deleted straight away: deleting one marks it
``source="deleted"`` and inactive, and the job purges it a few minutes later.
That keeps a delivery queued from a slightly stale rule cache in another worker
from pointing at a row that no longer exists (which would fail someone's save).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets as pysecrets
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.integration_hub import WebhookDelivery, WebhookSubscription

logger = logging.getLogger("aiva.integrations.webhooks")

AUTOMATION_KEYS = ("zapier", "make", "n8n")
PROVIDERS = (*AUTOMATION_KEYS, "custom")
PROVIDER_NAMES = {"zapier": "Zapier", "make": "Make", "n8n": "n8n", "custom": "Other"}

# Seconds to wait after the n-th failed attempt (then give up).
BACKOFF = (60, 5 * 60, 15 * 60, 60 * 60, 3 * 3600, 6 * 3600, 12 * 3600)
MAX_ATTEMPTS = len(BACKOFF) + 1
PAUSE_AFTER_FAILURES = 20  # failed attempts in a row before a subscription is paused
TIMEOUT_SECONDS = 10.0  # per network read/connect
TOTAL_SECONDS = 15.0  # a whole delivery, however slowly the receiver answers
MAX_RESPONSE_BYTES = 4096  # only the start of the receiver's reply is read (for the log)
BATCH = 200
CONCURRENCY = 8
PER_SUBSCRIPTION_PER_RUN = 25  # one busy subscription can't hold the queue
RUN_BUDGET_SECONDS = 45.0  # no new sends start after this; the rest go next run
OFF_GRACE = timedelta(hours=24)  # queued events wait this long for a switched-off integration
RETENTION = timedelta(days=30)
PURGE_DELETED_AFTER = timedelta(minutes=10)
MAX_SUBSCRIPTIONS = 200  # per workspace
USER_AGENT = "AIVA-Webhooks/1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Event catalog
# ===========================================================================


@dataclass(frozen=True)
class EventDef:
    key: str
    entity_type: str  # the AIVA object (lead, opportunity, ...)
    group: str
    label: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "object": self.entity_type, "group": self.group, "label": self.label, "description": self.description}


def _e(key: str, entity: str, group: str, label: str, description: str) -> EventDef:
    return EventDef(key, entity, group, label, description)


EVENTS: dict[str, EventDef] = {
    e.key: e
    for e in (
        _e("lead.created", "lead", "Leads", "New lead", "A lead is added: by a person, a web form, WordPress, Facebook lead ads, an import or the API."),
        _e("lead.updated", "lead", "Leads", "Lead updated", "Any field on a lead changes. The payload lists what changed."),
        _e("lead.converted", "lead", "Leads", "Lead converted", "A lead is converted into a contact, account and deal."),
        _e("lead.deleted", "lead", "Leads", "Lead deleted", "A lead is deleted."),
        _e("contact.created", "contact", "Contacts", "New contact", "A contact is added."),
        _e("contact.updated", "contact", "Contacts", "Contact updated", "Any field on a contact changes."),
        _e("contact.deleted", "contact", "Contacts", "Contact deleted", "A contact is deleted."),
        _e("account.created", "account", "Accounts", "New account", "An account (company) is added."),
        _e("account.updated", "account", "Accounts", "Account updated", "Any field on an account changes."),
        _e("account.deleted", "account", "Accounts", "Account deleted", "An account is deleted."),
        _e("deal.created", "opportunity", "Deals", "New deal", "A deal is created."),
        _e("deal.updated", "opportunity", "Deals", "Deal updated", "Any field on a deal changes."),
        _e("deal.stage_changed", "opportunity", "Deals", "Deal moved stage", "A deal moves to another pipeline stage."),
        _e("deal.won", "opportunity", "Deals", "Deal won", "A deal moves to a won stage."),
        _e("deal.lost", "opportunity", "Deals", "Deal lost", "A deal moves to a lost stage."),
        _e("deal.deleted", "opportunity", "Deals", "Deal deleted", "A deal is deleted."),
        _e("task.created", "task", "Tasks", "New task", "A task is created."),
        _e("task.completed", "task", "Tasks", "Task completed", "A task is marked completed."),
        _e("case.created", "case", "Cases", "New case", "A support case is opened."),
        _e("case.updated", "case", "Cases", "Case updated", "Any field on a case changes."),
        _e("case.closed", "case", "Cases", "Case resolved or closed", "A case is resolved or closed."),
        _e("quote.created", "quote", "Quotes", "New quote", "A quote is created."),
        _e("quote.accepted", "quote", "Quotes", "Quote accepted", "A quote is accepted."),
        _e("meeting.created", "meeting", "Meetings", "New meeting", "A meeting is booked (in AIVA, from a synced calendar or a scheduling link)."),
        _e("meeting.updated", "meeting", "Meetings", "Meeting updated", "A meeting is rescheduled, completed or cancelled."),
    )
}
ANY_EVENT = "*"
NOUN = {"opportunity": "deal"}
WATCHED = {e.entity_type for e in EVENTS.values()}


def event_catalog() -> list[dict[str, Any]]:
    return [e.to_dict() for e in EVENTS.values()]


def event_label(key: str) -> str:
    return "Every event" if key == ANY_EVENT else EVENTS[key].label if key in EVENTS else key


def events_for(kind: str, entity_type: str, obj: Any, changes: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """The webhook events one record change produces."""

    if entity_type not in WATCHED:
        return []
    noun = NOUN.get(entity_type, entity_type)
    names: list[str] = []
    if kind == "created":
        names.append(f"{noun}.created")
    elif kind == "deleted":
        names.append(f"{noun}.deleted")
    else:
        names.append(f"{noun}.updated")

        def became(field: str, *values: Any) -> bool:
            change = changes.get(field)
            return bool(change) and change.get("to") in values and change.get("from") not in values

        if noun == "lead" and changes.get("converted_at") and changes["converted_at"].get("from") is None and changes["converted_at"].get("to"):
            names.append("lead.converted")
        elif noun == "deal" and "stage_id" in changes:
            names.append("deal.stage_changed")
            category = (context.get("stages") or {}).get(str(getattr(obj, "stage_id", "")), {}).get("category")
            if category == "won":
                names.append("deal.won")
            elif category == "lost":
                names.append("deal.lost")
        elif noun == "task" and became("status", "completed"):
            names.append("task.completed")
        elif noun == "case" and became("status", "resolved", "closed"):
            names.append("case.closed")
        elif noun == "quote" and became("status", "accepted"):
            names.append("quote.accepted")
    return [name for name in names if name in EVENTS]


# ===========================================================================
# Payloads
# ===========================================================================

SKIP_COLUMNS = {"organization_id", "search_vector"}


def _plain(value: Any) -> Any:
    from app.services.automation import jsonable

    return jsonable(value)


def record_payload(obj: Any, entity_type: str, context: dict[str, Any] | None = None, *, deleted: bool = False) -> dict[str, Any]:
    """A JSON-safe snapshot of a record. Only reads attributes already loaded, so it
    is safe inside a flush (no lazy loads)."""

    from app.services.catalog import OBJECTS

    definition = OBJECTS.get(entity_type)
    state = inspect(obj)
    loaded = state.dict
    data: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in SKIP_COLUMNS or key not in loaded:
            continue
        value = loaded.get(key)
        if key == "custom_fields" and isinstance(value, dict):
            attribution = value.get("_attribution")
            if isinstance(attribution, dict):
                data["attribution"] = _plain(attribution)
            value = {k: v for k, v in value.items() if not str(k).startswith("_")}
        data[key] = _plain(value)
    if entity_type == "opportunity":
        stage = (context or {}).get("stages", {}).get(str(loaded.get("stage_id") or ""), {})
        data["stage_name"] = stage.get("name")
        data["stage_category"] = stage.get("category")
    base = get_settings().app_base_url.rstrip("/")
    try:
        label = definition.label_of(obj) if definition else None
    except Exception:  # noqa: BLE001 - a label is a nicety
        label = None
    try:
        link = definition.link(obj) if definition and not deleted else None
    except Exception:  # noqa: BLE001
        link = None
    data["label"] = str(label)[:300] if label else None
    data["url"] = f"{base}{link}" if link else None
    return data


def envelope(delivery_id: uuid.UUID, event: str, organization_id: uuid.UUID, data: dict[str, Any], changes: dict[str, Any] | None = None, *, test: bool = False) -> dict[str, Any]:
    entity = EVENTS[event].entity_type if event in EVENTS else None
    body: dict[str, Any] = {
        "id": str(delivery_id),
        "event": event,
        "object": NOUN.get(entity or "", entity),
        "occurred_at": utcnow().isoformat(),
        "workspace_id": str(organization_id),
        "data": data,
    }
    if changes:
        body["changes"] = _plain(changes)
    if test:
        body["test"] = True
    return body


def queue_events(session: Any, kind: str, entity_type: str, obj: Any, changes: dict[str, Any], context: dict[str, Any]) -> None:
    """Called from the automation flush hook: add deliveries for matching subscriptions.

    Runs inside the person's transaction, so it must never raise.
    """

    try:
        subscriptions = context.get("webhooks") or []
        organization_id = getattr(obj, "organization_id", None)
        if not subscriptions or organization_id is None:
            return
        names = events_for(kind, entity_type, obj, changes, context)
        if not names:
            return
        data: dict[str, Any] | None = None
        now = utcnow()
        for name in names:
            for subscription in subscriptions:
                if subscription["event"] not in (name, ANY_EVENT):
                    continue
                if data is None:
                    data = record_payload(obj, entity_type, context, deleted=kind == "deleted")
                delivery_id = uuid.uuid4()
                session.add(
                    WebhookDelivery(
                        id=delivery_id,
                        organization_id=organization_id,
                        subscription_id=uuid.UUID(subscription["id"]),
                        event=name,
                        payload=envelope(delivery_id, name, organization_id, data, changes if kind == "updated" else None),
                        status="pending",
                        attempts=0,
                        next_attempt_at=now,
                    )
                )
    except Exception:  # noqa: BLE001 - webhooks must never block a save
        logger.exception("Couldn't queue webhook events for a %s", entity_type)


async def context_entries(db: AsyncSession, organization_id: uuid.UUID) -> list[dict[str, str]]:
    """Active subscriptions whose integration is on, for the automation rule cache."""

    rows = (
        await db.execute(
            select(WebhookSubscription.id, WebhookSubscription.provider, WebhookSubscription.event).where(
                WebhookSubscription.organization_id == organization_id, WebhookSubscription.is_active.is_(True)
            )
        )
    ).all()
    if not rows:
        return []
    toggles = await core.toggles(db, organization_id)
    on = {key for key in AUTOMATION_KEYS if toggles.get(key) is not None and toggles[key].enabled}
    return [{"id": str(sid), "provider": provider, "event": event} for sid, provider, event in rows if provider == "custom" or provider in on]


def refresh_rules(organization_id: uuid.UUID) -> None:
    from app.services.automation import invalidate_automation_cache

    invalidate_automation_cache(organization_id)


# ===========================================================================
# Target URLs (SSRF protection)
# ===========================================================================


class UnsafeTarget(IntegrationError):
    pass


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


def _private_allowed() -> bool:
    """Local and private addresses are only allowed on a developer's own machine:
    a development or test environment whose public address is itself local."""

    settings = get_settings()
    if settings.environment not in {"development", "test"}:
        return False
    host = (urlsplit(settings.app_base_url or "").hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith((".localhost", ".test", ".internal"))


# IPv6 ranges that carry an IPv4 address inside them (NAT64, IPv4-compatible).
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    if any(address in network for network in _NAT64) or (address in _IPV4_COMPATIBLE and int(address) > 1):
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def _address_ok(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [address]
    if isinstance(address, ipaddress.IPv6Address):
        inner = _embedded_ipv4(address)
        if inner is not None:
            candidates = [inner] if address.ipv4_mapped is not None else [address, inner]
    if _private_allowed():
        return all(not a.is_multicast and not a.is_unspecified for a in candidates)
    return all(a.is_global and not a.is_multicast for a in candidates)


def check_target_url(url: str) -> str:
    """Validate a URL people paste or a platform subscribes. Returns it cleaned."""

    url = (url or "").strip()
    if not url or len(url) > 2000:
        raise UnsafeTarget("Enter the webhook URL from Zapier, Make or n8n.", 422)
    parts = urlsplit(url)
    allowed = {"https", "http"} if _private_allowed() else {"https"}
    if parts.scheme.lower() not in allowed:
        raise UnsafeTarget("The webhook URL must start with https://.", 422)
    if not parts.hostname:
        raise UnsafeTarget("That doesn't look like a web address.", 422)
    if parts.username or parts.password:
        raise UnsafeTarget("Remove the user name and password from the URL.", 422)
    host = parts.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal and not _address_ok(host):
        raise UnsafeTarget("Webhooks can't be sent to private or internal addresses.", 422)
    if not _private_allowed() and (host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))):
        raise UnsafeTarget("Webhooks can't be sent to private or internal addresses.", 422)
    try:
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError as exc:
        raise UnsafeTarget("The port in that URL isn't valid.", 422) from exc
    return url


async def _resolve(host: str, port: int) -> list[str]:
    try:
        ipaddress.ip_address(host)
        found = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeTarget(f"Couldn't find {host} (DNS lookup failed).", 422) from exc
        found = list(dict.fromkeys(info[4][0] for info in infos))
    if not found:
        raise UnsafeTarget(f"Couldn't find {host}.", 422)
    if not all(_address_ok(ip) for ip in found):
        raise UnsafeTarget(f"{host} points at a private or internal address, so AIVA won't send to it.", 422)
    return found


async def check_target_reachable(url: str) -> None:
    """Resolve the host now so a bad URL is refused when it's saved, not later."""

    parts = urlsplit(url)
    await _resolve(parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))


@dataclass
class SendResult:
    ok: bool
    status: int | None
    error: str | None
    gone: bool = False
    duration_ms: int = 0


async def _post(url: str, body: bytes, headers: dict[str, str]) -> SendResult:
    """POST with the connection pinned to an address that passed the checks."""

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    addresses = await _resolve(host, port)
    ip = addresses[0]
    netloc_ip = f"[{ip}]" if ":" in ip else ip
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    pinned = urlunsplit((scheme, netloc_ip if default_port else f"{netloc_ip}:{port}", parts.path or "/", parts.query, ""))
    request_headers = {**headers, "Host": host if default_port else f"{host}:{port}"}
    extensions = {"sni_hostname": host} if scheme == "https" else {}
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
        async with client.stream("POST", pinned, content=body, headers=request_headers, extensions=extensions) as response:
            status_code = response.status_code
            content = b""
            if not 200 <= status_code < 300:
                async for chunk in response.aiter_bytes():
                    content += chunk
                    if len(content) >= MAX_RESPONSE_BYTES:
                        break
    elapsed = int((time.monotonic() - started) * 1000)
    if 200 <= status_code < 300:
        return SendResult(True, status_code, None, duration_ms=elapsed)
    snippet = content[:300].decode("utf-8", "replace").strip()
    reason = f"HTTP {status_code}" + (f": {snippet}" if snippet else "")
    if 300 <= status_code < 400:
        reason = f"HTTP {status_code} redirect (AIVA doesn't follow redirects; use the final URL)."
    return SendResult(False, status_code, reason[:1000], gone=status_code == 410, duration_ms=elapsed)


# Marks a delivery left for the next run (not an attempt, not a failure).
DEFERRED = SendResult(False, None, "deferred")


async def send(url: str, body: bytes, headers: dict[str, str]) -> SendResult:
    """Send one webhook; never raises."""

    try:
        return await asyncio.wait_for(_post(url, body, headers), timeout=TOTAL_SECONDS)
    except UnsafeTarget as exc:
        return SendResult(False, None, str(exc))
    except (httpx.TimeoutException, asyncio.TimeoutError):
        return SendResult(False, None, f"No answer within {int(TOTAL_SECONDS)} seconds.")
    except httpx.HTTPError as exc:
        return SendResult(False, None, f"Couldn't connect ({exc.__class__.__name__}).")
    except Exception as exc:  # noqa: BLE001 - one broken URL mustn't stop the queue
        logger.exception("Webhook send failed")
        return SendResult(False, None, f"Couldn't send ({exc.__class__.__name__}).")


# ===========================================================================
# Signing
# ===========================================================================


def new_secret() -> str:
    return "whsec_" + pysecrets.token_urlsafe(32)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str | None, tolerance: int = 300) -> bool:
    """Check an ``X-AIVA-Signature`` header (used by the WordPress plugin too)."""

    if not header or not secret:
        return False
    parts = dict(item.strip().split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(time.time() - timestamp) > tolerance:
        return False
    expected = sign(secret, body, timestamp).split("v1=", 1)[1]
    return hmac.compare_digest(expected.encode(), str(parts.get("v1", "")).encode())


def _body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def _headers(subscription: WebhookSubscription, delivery: WebhookDelivery, body: bytes) -> dict[str, str]:
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-AIVA-Event": delivery.event,
        "X-AIVA-Delivery": str(delivery.id),
        "X-AIVA-Timestamp": str(timestamp),
        "X-AIVA-Attempt": str(delivery.attempts),
    }
    secret = decrypt(subscription.secret) if subscription.secret else None
    if secret:
        headers["X-AIVA-Signature"] = sign(secret, body, timestamp)
    return headers


# ===========================================================================
# Subscriptions
# ===========================================================================


def subscription_out(row: WebhookSubscription, names: dict[str, str] | None = None) -> dict[str, Any]:
    parts = urlsplit(row.target_url)
    return {
        "id": str(row.id),
        "provider": row.provider,
        "provider_name": PROVIDER_NAMES.get(row.provider, row.provider),
        "event": row.event,
        "event_label": event_label(row.event),
        "target_url": row.target_url,
        "target_host": parts.hostname,
        "label": row.label,
        "source": row.source,
        "is_active": row.is_active,
        "status": "active" if row.is_active else "paused",
        "created_by": (names or {}).get(str(row.created_by_user_id)) if row.created_by_user_id else None,
        "via_api_key": row.api_key_id is not None,
        "last_delivery_at": row.last_delivery_at,
        "last_status": row.last_status,
        "consecutive_failures": row.consecutive_failures or 0,
        "created_at": row.created_at,
    }


async def require_provider_on(db: AsyncSession, organization_id: uuid.UUID, provider: str) -> None:
    if provider not in PROVIDERS:
        raise IntegrationError("Choose Zapier, Make, n8n or Other.", 422)
    if provider in AUTOMATION_KEYS:
        await core.require_enabled(db, organization_id, provider)


async def create_subscriptions(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    provider: str,
    events: list[str],
    target_url: str,
    label: str | None,
    source: str,
    user_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None = None,
) -> tuple[list[WebhookSubscription], str]:
    """One subscription per event, sharing one new signing secret (returned once)."""

    await require_provider_on(db, organization_id, provider)
    events = list(dict.fromkeys(e.strip() for e in events if e and e.strip()))
    if not events:
        raise IntegrationError("Pick at least one event.", 422)
    unknown = [e for e in events if e != ANY_EVENT and e not in EVENTS]
    if unknown:
        raise IntegrationError(f"Unknown event: {unknown[0]}. See the list of events.", 422)
    if ANY_EVENT in events and len(events) > 1:
        events = [ANY_EVENT]
    url = check_target_url(target_url)
    await check_target_reachable(url)
    existing = (
        await db.execute(
            select(func.count()).select_from(WebhookSubscription).where(
                WebhookSubscription.organization_id == organization_id, WebhookSubscription.source != "deleted"
            )
        )
    ).scalar_one()
    if existing + len(events) > MAX_SUBSCRIPTIONS:
        raise IntegrationError(f"A workspace can have up to {MAX_SUBSCRIPTIONS} webhook subscriptions. Remove some you no longer use.", 409)
    secret = new_secret()
    rows = []
    for event in events:
        row = WebhookSubscription(
            id=uuid.uuid4(),
            organization_id=organization_id,
            provider=provider,
            event=event,
            target_url=url,
            secret=encrypt(secret),
            label=(label or "").strip()[:200] or None,
            source=source,
            is_active=True,
            api_key_id=api_key_id,
            created_by_user_id=user_id,
            consecutive_failures=0,
        )
        db.add(row)
        rows.append(row)
    names = ", ".join(event_label(e) for e in events)
    core.log(
        db, organization_id, _log_key(provider),
        "subscribed", f"Sending {names} to {urlsplit(url).hostname}" + (" (subscribed through the API)." if source == "rest_hook" else "."),
        level="success", user_id=user_id, detail={"events": events, "host": urlsplit(url).hostname, "source": source},
    )
    refresh_rules(organization_id)
    return rows, secret


def _log_key(provider: str) -> str:
    """Activity-log key: the integration, or "webhooks" for custom URLs."""

    return provider if provider in AUTOMATION_KEYS else "webhooks"


async def get_subscription(db: AsyncSession, organization_id: uuid.UUID, subscription_id: uuid.UUID) -> WebhookSubscription:
    row = await db.get(WebhookSubscription, subscription_id)
    if row is None or row.organization_id != organization_id or row.source == "deleted":
        raise IntegrationError("That webhook subscription doesn't exist.", 404)
    return row


async def remove_subscription(db: AsyncSession, row: WebhookSubscription, user_id: uuid.UUID | None, reason: str = "removed") -> None:
    row.is_active = False
    row.source = "deleted"
    await _cancel_pending(db, row, "Not sent: the subscription was removed.")
    core.log(db, row.organization_id, _log_key(row.provider), "unsubscribed",
             f"Stopped sending {event_label(row.event)} to {urlsplit(row.target_url).hostname} ({reason}).", user_id=user_id)
    refresh_rules(row.organization_id)


async def _cancel_pending(db: AsyncSession, row: WebhookSubscription, why: str) -> None:
    pending = (
        await db.execute(select(WebhookDelivery).where(WebhookDelivery.subscription_id == row.id, WebhookDelivery.status == "pending"))
    ).scalars().all()
    for delivery in pending:
        delivery.status = "failed"
        delivery.error = why
        delivery.next_attempt_at = None


async def pause_subscription(db: AsyncSession, row: WebhookSubscription, why: str, *, user_id: uuid.UUID | None = None, level: str = "info") -> None:
    row.is_active = False
    await _cancel_pending(db, row, f"Not sent: {why}")
    core.log(db, row.organization_id, _log_key(row.provider), "subscription_paused",
             f"Paused {event_label(row.event)} → {urlsplit(row.target_url).hostname}: {why}", level=level, user_id=user_id)
    refresh_rules(row.organization_id)


# ===========================================================================
# Delivery
# ===========================================================================


def _retry_at(attempts: int) -> datetime | None:
    if attempts >= MAX_ATTEMPTS:
        return None
    return utcnow() + timedelta(seconds=BACKOFF[min(attempts, len(BACKOFF)) - 1])


def _apply(db: AsyncSession, delivery: WebhookDelivery, subscription: WebhookSubscription, result: SendResult) -> None:
    now = utcnow()
    delivery.response_status = result.status
    subscription.last_delivery_at = now
    subscription.last_status = result.status
    if result.ok:
        delivery.status = "delivered"
        delivery.delivered_at = now
        delivery.error = None
        delivery.next_attempt_at = None
        subscription.consecutive_failures = 0
        return
    delivery.error = result.error
    subscription.consecutive_failures = (subscription.consecutive_failures or 0) + 1
    retry = None if result.gone else _retry_at(delivery.attempts)
    if retry is None:
        delivery.status = "failed"
        delivery.next_attempt_at = None
    else:
        delivery.status = "pending"
        delivery.next_attempt_at = retry


async def deliver_now(db: AsyncSession, delivery: WebhookDelivery, subscription: WebhookSubscription) -> SendResult:
    """Send one delivery immediately (tests and redelivery). Caller commits."""

    delivery.attempts = (delivery.attempts or 0) + 1
    body = _body(delivery.payload)
    result = await send(subscription.target_url, body, _headers(subscription, delivery, body))
    _apply(db, delivery, subscription, result)
    await _after_failure(db, subscription, result)
    return result


async def _after_failure(db: AsyncSession, subscription: WebhookSubscription, result: SendResult) -> None:
    if result.ok or not subscription.is_active:
        return
    if result.gone:
        await pause_subscription(db, subscription, "the receiver answered 410 Gone (the Zap or scenario was deleted or turned off).", level="warning")
    elif (subscription.consecutive_failures or 0) >= PAUSE_AFTER_FAILURES:
        await pause_subscription(db, subscription, f"{PAUSE_AFTER_FAILURES} deliveries in a row failed. Fix the URL and resume it.", level="error")


async def _plan_allows(db: AsyncSession, organization_id: uuid.UUID, cache: dict[uuid.UUID, bool]) -> bool:
    if organization_id not in cache:
        from app.models.editions import Subscription
        from app.services.billing import plan_state
        from app.services.metering import addon_features

        subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == organization_id))).scalar_one_or_none()
        state = plan_state(subscription)
        features = set(state.features) | set(await addon_features(db, organization_id))
        cache[organization_id] = "api_access" in features and not state.read_only
    return cache[organization_id]


async def _api_key_valid(db: AsyncSession, api_key_id: uuid.UUID | None, cache: dict[uuid.UUID, bool]) -> bool:
    if api_key_id is None:
        return True
    if api_key_id not in cache:
        from app.models.editions import ApiKey

        key = await db.get(ApiKey, api_key_id)
        now = utcnow()
        cache[api_key_id] = key is not None and key.revoked_at is None and (key.expires_at is None or key.expires_at > now)
    return cache[api_key_id]


async def run_due(db: AsyncSession) -> int:
    """Send what's due, retry failures, tidy up. Called by the scheduler."""

    db.sync_session.info["automation_disabled"] = True
    now = utcnow()
    deliveries = (
        await db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending", (WebhookDelivery.next_attempt_at.is_(None)) | (WebhookDelivery.next_attempt_at <= now))
            .order_by(WebhookDelivery.next_attempt_at.asc().nulls_first(), WebhookDelivery.created_at.asc())
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    handled = 0
    if deliveries:
        subscription_ids = {d.subscription_id for d in deliveries}
        subscriptions = {
            s.id: s for s in (await db.execute(select(WebhookSubscription).where(WebhookSubscription.id.in_(subscription_ids)))).scalars()
        }
        plan_cache: dict[uuid.UUID, bool] = {}
        key_cache: dict[uuid.UUID, bool] = {}
        enabled_cache: dict[tuple[uuid.UUID, str], bool] = {}
        groups: dict[uuid.UUID, tuple[WebhookSubscription, list[WebhookDelivery]]] = {}
        for delivery in deliveries:
            subscription = subscriptions.get(delivery.subscription_id)
            if subscription is None or not subscription.is_active:
                delivery.status = "failed"
                delivery.error = "Not sent: the subscription was paused or removed."
                delivery.next_attempt_at = None
                continue
            if not await _plan_allows(db, subscription.organization_id, plan_cache):
                delivery.status = "failed"
                delivery.error = "Not sent: API access isn't included in the workspace's plan."
                delivery.next_attempt_at = None
                continue
            if not await _api_key_valid(db, subscription.api_key_id, key_cache):
                await pause_subscription(db, subscription, "the API key that created it was revoked or expired.", level="warning")
                continue
            if subscription.provider in AUTOMATION_KEYS:
                cache_key = (subscription.organization_id, subscription.provider)
                if cache_key not in enabled_cache:
                    enabled_cache[cache_key] = await core.is_enabled(db, subscription.organization_id, subscription.provider)
                if not enabled_cache[cache_key]:
                    if delivery.created_at and now - delivery.created_at > OFF_GRACE:
                        delivery.status = "failed"
                        delivery.error = f"Not sent: {PROVIDER_NAMES[subscription.provider]} was switched off for more than a day."
                        delivery.next_attempt_at = None
                    else:
                        delivery.next_attempt_at = now + timedelta(minutes=10)
                    continue
            groups.setdefault(subscription.id, (subscription, []))[1].append(delivery)

        semaphore = asyncio.Semaphore(CONCURRENCY)
        stop_at = time.monotonic() + RUN_BUDGET_SECONDS

        async def run_group(subscription: WebhookSubscription, items: list[WebhookDelivery]) -> list[tuple[WebhookDelivery, SendResult | None]]:
            """Send in order; after a failure, hold the rest back (one try per URL per run).

            Deliveries beyond this run's share (per subscription, or once the run's time
            budget is spent) are left exactly as they are for the next run.
            """

            outcome: list[tuple[WebhookDelivery, SendResult | None]] = []
            failed = False
            for index, delivery in enumerate(items):
                if failed:
                    outcome.append((delivery, None))
                    continue
                if index >= PER_SUBSCRIPTION_PER_RUN or time.monotonic() >= stop_at:
                    outcome.append((delivery, DEFERRED))
                    continue
                delivery.attempts = (delivery.attempts or 0) + 1
                body = _body(delivery.payload)
                async with semaphore:
                    result = await send(subscription.target_url, body, _headers(subscription, delivery, body))
                outcome.append((delivery, result))
                failed = not result.ok
            return outcome

        outcomes = await asyncio.gather(*(run_group(subscription, items) for subscription, items in groups.values()))
        for (subscription, _items), outcome in zip(groups.values(), outcomes, strict=True):
            last: SendResult | None = None
            hold_until: datetime | None = None
            for delivery, result in outcome:
                if result is DEFERRED:
                    continue
                if result is None:
                    delivery.next_attempt_at = hold_until or now + timedelta(seconds=BACKOFF[0])
                    continue
                _apply(db, delivery, subscription, result)
                handled += 1
                last = result
                if not result.ok:
                    hold_until = delivery.next_attempt_at
            if last is not None:
                await _after_failure(db, subscription, last)
        await db.commit()

    handled += await _housekeeping(db)
    return handled


_LAST_HOUSEKEEPING = 0.0
HOUSEKEEPING_EVERY_SECONDS = 3600


async def _housekeeping(db: AsyncSession, *, force: bool = False) -> int:
    """Purge removed subscriptions and old deliveries (hourly; the scan isn't free)."""

    global _LAST_HOUSEKEEPING
    if not force and time.monotonic() - _LAST_HOUSEKEEPING < HOUSEKEEPING_EVERY_SECONDS and _LAST_HOUSEKEEPING:
        return 0
    _LAST_HOUSEKEEPING = time.monotonic()
    now = utcnow()
    removed = 0
    result = await db.execute(
        delete(WebhookSubscription).where(WebhookSubscription.source == "deleted", WebhookSubscription.updated_at < now - PURGE_DELETED_AFTER)
    )
    removed += result.rowcount or 0
    old = (
        await db.execute(
            select(WebhookDelivery.id)
            .where(WebhookDelivery.status != "pending", WebhookDelivery.created_at < now - RETENTION)
            .limit(2000)
        )
    ).scalars().all()
    if old:
        result = await db.execute(delete(WebhookDelivery).where(WebhookDelivery.id.in_(old)))
        removed += result.rowcount or 0
    if removed:
        await db.commit()
    return 0


# ===========================================================================
# Samples (for building Zaps and scenarios)
# ===========================================================================


def _typed_sample(event: str) -> dict[str, Any]:
    from app.services.catalog import OBJECTS

    entity = EVENTS[event].entity_type
    definition = OBJECTS.get(entity)
    sample: dict[str, Any] = {"id": "00000000-0000-4000-8000-000000000000"}
    examples = {
        "string": "Example", "number": 42, "currency": 12500.0, "datetime": "2026-01-15T14:30:00+00:00", "date": "2026-01-15",
        "bool": False, "user": "00000000-0000-4000-8000-0000000000aa", "ref": "00000000-0000-4000-8000-0000000000bb",
    }
    for field in definition.fields if definition else ():
        if field.type == "enum":
            sample[field.key] = field.options[0] if field.options else None
        else:
            sample[field.key] = examples.get(field.type, "Example")
    if entity == "lead":
        sample.update({"first_name": "Jordan", "last_name": "Lee", "email": "jordan.lee@example.com", "phone": "+1 415 555 0134", "company_name": "Northwind", "source": "web_form"})
    if entity == "opportunity":
        sample.update({"name": "Northwind expansion", "amount": 48000.0, "stage_name": "Proposal", "stage_category": "open"})
    sample["label"] = "Example record"
    sample["url"] = f"{get_settings().app_base_url.rstrip('/')}/"
    return sample


async def samples(db: AsyncSession, organization_id: uuid.UUID, event: str, limit: int = 3) -> list[dict[str, Any]]:
    """Recent real records shaped like the event's payload (or one typed example)."""

    if event not in EVENTS:
        raise IntegrationError("Unknown event.", 404)
    from app.services.catalog import OBJECTS

    entity = EVENTS[event].entity_type
    definition = OBJECTS[entity]
    model = definition.model
    context = db.sync_session.info.get("automation") or {}
    rows = (
        await db.execute(select(model).where(model.organization_id == organization_id).order_by(model.created_at.desc()).limit(limit))
    ).scalars().all()
    out = []
    for row in rows:
        out.append(envelope(uuid.uuid4(), event, organization_id, record_payload(row, entity, context), test=True))
    if not out:
        out.append(envelope(uuid.uuid4(), event, organization_id, _typed_sample(event), test=True))
    return out


async def test_delivery(db: AsyncSession, subscription: WebhookSubscription) -> tuple[WebhookDelivery, SendResult]:
    event = subscription.event if subscription.event != ANY_EVENT else "lead.created"
    sample = (await samples(db, subscription.organization_id, event, limit=1))[0]
    delivery_id = uuid.uuid4()
    sample["id"] = str(delivery_id)
    delivery = WebhookDelivery(
        id=delivery_id, organization_id=subscription.organization_id, subscription_id=subscription.id, event=event,
        payload=sample, status="pending", attempts=0, next_attempt_at=None,
    )
    db.add(delivery)
    await db.flush()
    result = await deliver_now(db, delivery, subscription)
    if not result.ok:
        # A test shouldn't keep retrying in the background.
        delivery.status = "failed"
        delivery.next_attempt_at = None
    return delivery, result


async def redeliver(db: AsyncSession, original: WebhookDelivery, subscription: WebhookSubscription) -> tuple[WebhookDelivery, SendResult]:
    delivery_id = uuid.uuid4()
    payload = {**(original.payload or {}), "id": str(delivery_id), "redelivery_of": str(original.id)}
    delivery = WebhookDelivery(
        id=delivery_id, organization_id=original.organization_id, subscription_id=subscription.id, event=original.event,
        payload=payload, status="pending", attempts=0, next_attempt_at=None,
    )
    db.add(delivery)
    await db.flush()
    result = await deliver_now(db, delivery, subscription)
    if not result.ok:
        delivery.status = "failed"
        delivery.next_attempt_at = None
    return delivery, result


def delivery_out(row: WebhookDelivery, subscription: WebhookSubscription | None, *, with_payload: bool = False) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "subscription_id": str(row.subscription_id),
        "provider": subscription.provider if subscription else None,
        "provider_name": PROVIDER_NAMES.get(subscription.provider, subscription.provider) if subscription else None,
        "target_host": urlsplit(subscription.target_url).hostname if subscription else None,
        "subscription_label": subscription.label if subscription else None,
        "event": row.event,
        "event_label": event_label(row.event),
        "status": row.status,
        "attempts": row.attempts or 0,
        "response_status": row.response_status,
        "error": row.error,
        "next_attempt_at": row.next_attempt_at,
        "delivered_at": row.delivered_at,
        "created_at": row.created_at,
        "test": bool((row.payload or {}).get("test")),
        "record_label": ((row.payload or {}).get("data") or {}).get("label"),
    }
    if with_payload:
        out["payload"] = row.payload
    return out


# ===========================================================================
# Hub hooks
# ===========================================================================


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    refresh_rules(organization_id)
    if key not in AUTOMATION_KEYS:
        return
    count = (
        await db.execute(
            select(func.count()).select_from(WebhookSubscription).where(
                WebhookSubscription.organization_id == organization_id, WebhookSubscription.provider == key, WebhookSubscription.is_active.is_(True)
            )
        )
    ).scalar_one()
    if not enabled and count:
        core.log(db, organization_id, key, "deliveries_paused",
                 f"{count} subscription{'s' if count != 1 else ''} won't receive events while {PROVIDER_NAMES[key]} is off. Events already queued wait up to a day.", level="warning")
    elif enabled and count:
        core.log(db, organization_id, key, "deliveries_resumed", f"Sending events to {count} subscription{'s' if count != 1 else ''} again.")


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    since = utcnow() - timedelta(hours=24)
    subs = (
        await db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.organization_id == org, WebhookSubscription.provider == key, WebhookSubscription.source != "deleted"
            )
        )
    ).scalars().all()
    counts = dict(
        (
            await db.execute(
                select(WebhookDelivery.status, func.count())
                .join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
                .where(WebhookDelivery.organization_id == org, WebhookSubscription.provider == key, WebhookDelivery.created_at >= since)
                .group_by(WebhookDelivery.status)
            )
        ).all()
    )
    base = get_settings().app_base_url.rstrip("/")
    api = f"{base}/api/aiva"
    return {
        "api_base_url": api,
        "rest_hooks": {
            "subscribe": f"{api}/hooks",
            "unsubscribe": f"{api}/hooks/{{id}}",
            "events": f"{api}/hooks/events",
            "sample": f"{api}/hooks/samples/{{event}}",
            "auth_test": f"{api}/hooks/me",
        },
        "subscriptions": {"active": sum(1 for s in subs if s.is_active), "paused": sum(1 for s in subs if not s.is_active)},
        "deliveries_24h": {"delivered": int(counts.get("delivered", 0)), "failed": int(counts.get("failed", 0)), "pending": int(counts.get("pending", 0))},
        "events": event_catalog(),
        "signature_header": "X-AIVA-Signature",
    }
