"""Slack: CRM alerts in the channels you choose, and sharing records.

Two ways to connect:

* **The AIVA Slack app** (OAuth, bot token): alerts go out with
  ``chat.postMessage`` to a default channel, or a different channel per event,
  and anyone can share a record to any channel.
* **An incoming webhook** (fallback, pasted on the Slack page): alerts go to
  the one channel the webhook was made for.

Alerts are queued by the automation engine as ``OutboundMessage`` rows
(``services/automation.py``) and delivered by the chat job
(``services/chat.py``), which hands Slack messages to :func:`deliver`. The
automation engine reads which events to post from the workspace's ``Integration``
row (kind ``slack``); :func:`sync_bridge` keeps that row in step with this
integration's switch, connection and settings.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.editions import Integration, OutboundMessage
from app.models.integration_hub import IntegrationConnection
from app.services import chat
from app.services.automation import CHAT_EVENTS, invalidate_automation_cache

logger = logging.getLogger("aiva.integrations.slack")

API = "https://slack.com/api"
KEY = "slack"
DEFAULT_EVENTS = ["lead_created", "deal_won", "case_urgent"]
ROUTABLE = {**CHAT_EVENTS, "workflow": "Workflow messages"}
CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{6,20}$")
WEBHOOK_PREFIX = "https://hooks.slack.com/"

AUTH_ERRORS = {"invalid_auth", "token_revoked", "account_inactive", "not_authed", "token_expired", "org_login_required", "ekm_access_denied"}
PERMANENT_ERRORS = AUTH_ERRORS | {"channel_not_found", "not_in_channel", "is_archived", "missing_scope", "restricted_action", "no_text", "msg_too_long", "invalid_blocks"}
FRIENDLY = {
    "channel_not_found": "That Slack channel doesn't exist any more, or AIVA can't see it.",
    "not_in_channel": "AIVA isn't in that private channel. In Slack, type /invite @AIVA in the channel.",
    "is_archived": "That Slack channel is archived.",
    "missing_scope": "The AIVA Slack app is missing a permission. Reconnect Slack to grant it.",
    "restricted_action": "Your Slack workspace doesn't allow apps to post in that channel.",
    "ratelimited": "Slack is rate limiting AIVA. It will try again shortly.",
    "msg_too_long": "The message is too long for Slack.",
}

_CHANNELS: dict[uuid.UUID, tuple[float, list[dict[str, Any]]]] = {}


class SlackError(IntegrationError):
    def __init__(self, message: str, error: str, status_code: int = 400):
        super().__init__(message, status_code)
        self.error = error


class DeliveryError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def bot_token(connection: IntegrationConnection | None) -> str | None:
    if connection is None or not connection.access_token:
        return None
    return decrypt(connection.access_token)


async def api(db: AsyncSession, connection: IntegrationConnection, method: str, *, params: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call the Slack Web API as the bot. Slack answers 200 with ``ok: false`` on errors."""

    if payload is not None:
        response = await core.request(db, connection, "POST", f"{API}/{method}", json=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    else:
        response = await core.request(db, connection, "GET", f"{API}/{method}", params=params or {})
    try:
        data = response.json()
    except ValueError as exc:
        raise SlackError("Slack sent an unexpected answer.", "bad_response", 502) from exc
    if not data.get("ok"):
        error = str(data.get("error") or "unknown_error")
        if error in AUTH_ERRORS:
            connection.status = "needs_reauth"
            message = "Slack disconnected AIVA (the app was removed or its token revoked). Reconnect Slack."
        elif error == "missing_scope" and data.get("needed"):
            message = f"The AIVA Slack app is missing the {data['needed']} permission. Reconnect Slack to grant it."
        else:
            message = FRIENDLY.get(error) or f"Slack said: {error.replace('_', ' ')}."
        connection.last_error = message
        raise SlackError(message, error)
    return data


# ---------------------------------------------------------------------------
# Channels and routing
# ---------------------------------------------------------------------------


async def channels(db: AsyncSession, connection: IntegrationConnection, refresh: bool = False) -> list[dict[str, Any]]:
    cached = _CHANNELS.get(connection.id)
    if cached and not refresh and cached[0] > time.monotonic():
        return cached[1]
    found: list[dict[str, Any]] = []
    types = "public_channel,private_channel"
    cursor = None
    for _ in range(15):
        params = {"types": types, "exclude_archived": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await api(db, connection, "conversations.list", params=params)
        except SlackError as exc:
            if exc.error == "missing_scope" and types != "public_channel":
                types, cursor, found = "public_channel", None, []
                continue
            raise
        for row in data.get("channels") or []:
            found.append(
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "is_private": bool(row.get("is_private")),
                    "is_member": bool(row.get("is_member")),
                    "members": row.get("num_members"),
                }
            )
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    found.sort(key=lambda c: (c["name"] or "").lower())
    _CHANNELS[connection.id] = (time.monotonic() + 300, found)
    return found


def route(config: dict[str, Any], event: str) -> str | None:
    return (config.get("routes") or {}).get(event) or config.get("default_channel_id")


async def legacy_row(db: AsyncSession, organization_id: uuid.UUID) -> Integration | None:
    return (await db.execute(select(Integration).where(Integration.organization_id == organization_id, Integration.kind == "slack"))).scalar_one_or_none()


def legacy_webhook(row: Integration | None) -> str | None:
    return decrypt((row.config or {}).get("webhook_url")) if row is not None and (row.config or {}).get("webhook_url") else None


async def sync_bridge(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    events: list[str] | None = None,
    enabled: bool | None = None,
    removing: bool = False,
) -> Integration | None:
    """Keep the automation engine's Slack row in step with this integration."""

    toggle = await core.toggle_row(db, organization_id, KEY)
    is_on = enabled if enabled is not None else bool(toggle and toggle.enabled)
    connection = None if removing else await core.connection_for(db, organization_id, KEY)
    row = await legacy_row(db, organization_id)
    usable = connection is not None and connection.status != "needs_reauth" and bool(bot_token(connection) or core.secret(connection, "webhook_url"))
    usable = usable or bool(legacy_webhook(row))
    if events is None:
        events = list(row.events or []) if row is not None else list((toggle.config or {}).get("events") or DEFAULT_EVENTS) if toggle else list(DEFAULT_EVENTS)
    if row is None:
        if not (is_on and usable):
            return None
        row = Integration(id=uuid.uuid4(), organization_id=organization_id, kind="slack", config={"managed_by": "integration_hub"}, events=events, is_active=True)
        db.add(row)
    row.events = [e for e in events if e in CHAT_EVENTS]
    row.is_active = bool(is_on and usable)
    await db.flush()
    invalidate_automation_cache(organization_id)
    return row


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------


def blocks_for(title: str, text: str, link: str | None, *, fields: list[tuple[str, str]] | None = None, context: str | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{_escape(title)}*\n{_escape(text)}"[:3000]}}]
    if fields:
        blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": f"*{_escape(k)}*\n{_escape(v)}"[:2000]} for k, v in fields[:10]]})
    url = chat.absolute(link)
    if url:
        blocks.append({"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open in AIVA"}, "url": url}]})
    if context:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _escape(context)[:300]}]})
    return blocks


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def post(db: AsyncSession, connection: IntegrationConnection, channel_id: str, title: str, text: str, link: str | None = None, **extra: Any) -> dict[str, Any]:
    payload = {
        "channel": channel_id,
        "text": f"{title}: {text}"[:3000],
        "blocks": blocks_for(title, text, link, fields=extra.get("fields"), context=extra.get("context")),
        "unfurl_links": False,
        "unfurl_media": False,
    }
    data = await api(db, connection, "chat.postMessage", payload=payload)
    connection.last_used_at = core.utcnow()
    return data


async def deliver(db: AsyncSession, message: OutboundMessage, legacy: Integration | None) -> None:
    """Deliver one queued Slack alert (called by the chat job)."""

    org = message.organization_id
    toggle = await core.toggle_row(db, org, KEY)
    if toggle is not None and not toggle.enabled:
        raise DeliveryError("Slack is turned off in Integrations.", permanent=True)
    payload = message.payload or {}
    title = payload.get("title") or "AIVA"
    text = payload.get("text") or ""
    link = payload.get("link")
    connection = await core.connection_for(db, org, KEY)
    config = dict(toggle.config or {}) if toggle else {}
    if bot_token(connection) and connection is not None and connection.status != "needs_reauth":
        channel_id = route(config, message.event)
        if channel_id:
            try:
                await post(db, connection, channel_id, title, text, link)
                return
            except SlackError as exc:
                permanent = exc.error in PERMANENT_ERRORS
                if permanent:
                    core.log(db, org, KEY, "delivery_failed", f"Couldn't post “{title}” to Slack: {exc}", level="error", connection=connection)
                raise DeliveryError(str(exc), permanent=permanent) from exc
            except IntegrationError as exc:
                raise DeliveryError(str(exc), permanent=exc.status_code == 409) from exc
    url = (core.secret(connection, "webhook_url") if connection is not None else None) or legacy_webhook(legacy)
    if url:
        await chat.post("slack", url, title, text, link)
        if connection is not None:
            connection.last_used_at = core.utcnow()
        return
    if bot_token(connection):
        raise DeliveryError("Choose a default Slack channel on the Slack integration page.", permanent=True)
    raise DeliveryError("Slack isn't connected.", permanent=True)


async def share(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    channel_id: str | None,
    title: str,
    text: str,
    link: str | None,
    fields: list[tuple[str, str]],
    note: str | None,
    shared_by: str,
) -> dict[str, Any]:
    await core.require_enabled(db, organization_id, KEY)
    connection = await core.connection_for(db, organization_id, KEY)
    toggle = await core.toggle_row(db, organization_id, KEY)
    config = dict(toggle.config or {}) if toggle else {}
    context = f"Shared from AIVA by {shared_by}"
    body = f"{note.strip()}\n\n{text}" if note and note.strip() else text
    if bot_token(connection) and connection is not None:
        if connection.status == "needs_reauth":
            raise IntegrationError("Slack needs to be reconnected.", 409)
        target = channel_id or config.get("default_channel_id")
        if not target:
            raise IntegrationError("Pick a channel to share to.", 422)
        if not CHANNEL_ID.match(target):
            raise IntegrationError("That isn't a Slack channel.", 422)
        data = await post(db, connection, target, title, body, link, fields=fields, context=context)
        permalink = None
        try:
            found = await api(db, connection, "chat.getPermalink", params={"channel": data.get("channel") or target, "message_ts": data.get("ts")})
            permalink = found.get("permalink")
        except IntegrationError:
            pass
        return {"channel_id": data.get("channel") or target, "permalink": permalink}
    url = (core.secret(connection, "webhook_url") if connection is not None else None) or legacy_webhook(await legacy_row(db, organization_id))
    if not url:
        raise IntegrationError("Slack isn't connected.", 409)
    try:
        await chat.post("slack", url, title, f"{body}\n_{context}_", link)
    except Exception as exc:  # noqa: BLE001
        raise IntegrationError(f"Slack didn't accept the message: {exc}", 502) from exc
    return {"channel_id": None, "permalink": None}


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    url = (values.get("webhook_url") or "").strip()
    connection = await core.connection_for(db, organization_id, KEY)
    if url and not url.startswith(WEBHOOK_PREFIX):
        raise IntegrationError("Slack incoming webhook URLs start with https://hooks.slack.com/.", 422)
    if not url and not bot_token(connection):
        raise IntegrationError("Paste an incoming webhook URL, or connect with Slack instead.", 422)
    if bot_token(connection):
        return {}
    return {"label": "Incoming webhook", "config": {"mode": "webhook"}}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if bot_token(connection):
        try:
            info = await api(db, connection, "auth.test")
            connection.config = {**(connection.config or {}), "mode": "bot", "team_url": info.get("url"), "bot_user_id": info.get("user_id") or (connection.config or {}).get("bot_user_id")}
        except IntegrationError as exc:
            core.log(db, connection.organization_id, KEY, "connect_check", f"Connected, but Slack's check failed: {exc}", level="warning", connection=connection)
        _CHANNELS.pop(connection.id, None)
    if core.secret(connection, "webhook_url"):
        await _forget_legacy_webhook(db, connection.organization_id)
    await sync_bridge(db, connection.organization_id)


async def _forget_legacy_webhook(db: AsyncSession, organization_id: uuid.UUID) -> None:
    """A webhook saved before the integrations hub existed is replaced by the hub's own."""

    row = await legacy_row(db, organization_id)
    if row is not None and (row.config or {}).get("webhook_url"):
        row.config = {k: v for k, v in (row.config or {}).items() if k != "webhook_url"}


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if bot_token(connection) and connection.status != "needs_reauth":
        try:
            await api(db, connection, "auth.revoke")
        except IntegrationError:
            pass
    _CHANNELS.pop(connection.id, None)
    # Disconnecting Slack here stops every Slack alert, including an older saved webhook.
    await _forget_legacy_webhook(db, connection.organization_id)
    await sync_bridge(db, connection.organization_id, removing=True)


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    await sync_bridge(db, organization_id, enabled=enabled)


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    if "default_channel_id" in config:
        channel_id = (config.get("default_channel_id") or "").strip()
        if channel_id and not CHANNEL_ID.match(channel_id):
            raise IntegrationError("That isn't a Slack channel.", 422)
        cleaned["default_channel_id"] = channel_id or None
        cleaned["default_channel_name"] = str(config.get("default_channel_name") or "")[:80] or None
    if "routes" in config:
        routes = config.get("routes") or {}
        if not isinstance(routes, dict):
            raise IntegrationError("Channel routing should map events to channels.", 422)
        clean_routes = {}
        for event, channel_id in routes.items():
            if event not in ROUTABLE:
                raise IntegrationError(f"Unknown event “{event}”.", 422)
            if channel_id:
                if not CHANNEL_ID.match(str(channel_id)):
                    raise IntegrationError("That isn't a Slack channel.", 422)
                clean_routes[event] = str(channel_id)
        cleaned["routes"] = clean_routes
    if "channel_names" in config and isinstance(config.get("channel_names"), dict):
        cleaned["channel_names"] = {str(k): str(v)[:80] for k, v in list(config["channel_names"].items())[:60] if CHANNEL_ID.match(str(k))}
    if "events" in config:
        events = config.get("events") or []
        if not isinstance(events, list) or any(e not in CHAT_EVENTS for e in events):
            raise IntegrationError("Pick events from the list.", 422)
        cleaned["events"] = list(dict.fromkeys(events))
        await sync_bridge(db, principal.organization_id, events=cleaned["events"])
    return cleaned


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    toggle = await core.toggle_row(db, connection.organization_id, KEY)
    config = dict(toggle.config or {}) if toggle else {}
    if bot_token(connection):
        info = await api(db, connection, "auth.test")
        target = config.get("default_channel_id")
        if not target:
            return f"Connected to {info.get('team') or 'Slack'} as @{info.get('user') or 'AIVA'}. Choose a default channel to start posting alerts."
        await post(db, connection, target, "AIVA is connected", f"{principal.full_name} sent a test from AIVA CRM. Alerts will appear here.", "/")
        return f"Posted a test message to #{config.get('default_channel_name') or target}."
    url = core.secret(connection, "webhook_url") or legacy_webhook(await legacy_row(db, connection.organization_id))
    if not url:
        raise IntegrationError("Connect Slack or paste an incoming webhook first.", 409)
    try:
        await chat.post("slack", url, "AIVA is connected", f"{principal.full_name} sent a test from AIVA CRM. Alerts will appear here.", "/")
    except Exception as exc:  # noqa: BLE001
        raise IntegrationError(f"Slack didn't accept the test message: {exc}", 502) from exc
    return "Posted a test message through the incoming webhook."


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    toggle = await core.toggle_row(db, org, KEY)
    row = await legacy_row(db, org)
    config = dict(toggle.config or {}) if toggle else {}
    events = list(row.events or []) if row is not None else list(config.get("events") or DEFAULT_EVENTS)
    return {
        "mode": "bot" if bot_token(connection) else "webhook" if (connection is not None and core.secret(connection, "webhook_url")) or legacy_webhook(row) else None,
        "team_name": (connection.config or {}).get("team_name") if connection else None,
        "team_url": (connection.config or {}).get("team_url") if connection else None,
        "default_channel_id": config.get("default_channel_id"),
        "default_channel_name": config.get("default_channel_name"),
        "routes": config.get("routes") or {},
        "channel_names": config.get("channel_names") or {},
        "events": events,
        "event_options": [{"key": k, "label": v} for k, v in CHAT_EVENTS.items()],
        "routable": [{"key": k, "label": v} for k, v in ROUTABLE.items()],
        "legacy_webhook": bool(legacy_webhook(row)),
        "alerts_active": bool(row and row.is_active),
    }
