"""WhatsApp Business Platform (Cloud API): send messages and approved templates,
receive replies and delivery receipts.

Connects with the phone number ID, the WhatsApp Business Account ID, a
permanent system-user token and the Meta app secret. Every Graph call carries an
``appsecret_proof``. Webhooks come to the connection's URL: Meta first checks it
with a GET (``hub.verify_token`` must match the token AIVA shows), then POSTs
events signed with ``X-Hub-Signature-256`` (HMAC-SHA256 of the raw body, keyed
with the app secret), which AIVA verifies before reading anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core, messaging
from app.integrations.core import IntegrationError
from app.integrations.providers.twilio_sms import clean_channel_settings
from app.models.integration_hub import ChannelMessage, IntegrationConnection

logger = logging.getLogger("aiva.integrations.whatsapp")

GRAPH = "https://graph.facebook.com/v24.0"
NUMERIC_ID = re.compile(r"^\d{5,25}$")
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
TEMPLATE_SYNC_EVERY = timedelta(hours=6)
MEDIA_LIMIT = 25 * 1024 * 1024

ERRORS: dict[int, str] = {
    190: "The WhatsApp access token expired or was revoked. Paste a new permanent system-user token.",
    10: "The token doesn't have permission for this phone number. Give the system user whatsapp_business_messaging and whatsapp_business_management.",
    200: "The token doesn't have permission for this phone number. Give the system user whatsapp_business_messaging and whatsapp_business_management.",
    131047: "More than 24 hours have passed since this customer last replied. Send an approved template instead.",
    131026: "WhatsApp couldn't deliver it. The number may not be on WhatsApp, or the person hasn't accepted WhatsApp's latest terms.",
    131030: "That number isn't on your test number's list of allowed recipients. Add it in WhatsApp Manager, or use a production number.",
    131031: "This WhatsApp Business Account is locked or restricted. Check WhatsApp Manager.",
    131042: "There's a payment problem on the WhatsApp Business Account. Check the payment method in WhatsApp Manager.",
    131049: "Meta didn't deliver this marketing message to keep the person's experience healthy. Try again later.",
    131051: "WhatsApp doesn't support that kind of message.",
    131056: "Too many messages to this number in a short time. Wait a moment and try again.",
    131021: "You can't message your own business number.",
    132000: "The number of template values doesn't match the template.",
    132001: "That template doesn't exist in this language or isn't approved yet. Sync templates and try again.",
    132005: "The filled-in template is too long.",
    132007: "The template's content breaks WhatsApp's policies.",
    132012: "A template value is in the wrong format.",
    132015: "That template is paused because of low quality.",
    132016: "That template has been disabled.",
    133010: "This phone number isn't registered with the Cloud API yet. Register it in WhatsApp Manager.",
    130429: "WhatsApp is rate limiting this number. Try again shortly.",
    131048: "WhatsApp is limiting messages from this number because of quality. Try again later.",
    80007: "The WhatsApp Business Account hit its rate limit. Try again shortly.",
    368: "Meta temporarily blocked this account for policy reasons. Check WhatsApp Manager.",
}

_CACHE: dict[str, tuple[float, Any]] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _proof(token: str, app_secret: str | None) -> dict[str, str]:
    if not app_secret:
        return {}
    return {"appsecret_proof": hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()}


def _creds(connection: IntegrationConnection) -> tuple[str, str | None]:
    token = core.secret(connection, "access_token")
    if not token:
        raise IntegrationError("WhatsApp isn't fully connected. Paste the access token on the WhatsApp page.", 409)
    return token, core.secret(connection, "app_secret")


def _error(response, connection: IntegrationConnection | None) -> IntegrationError:
    try:
        data = response.json()
    except ValueError:
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    details = (error.get("error_data") or {}).get("details") if isinstance(error.get("error_data"), dict) else None
    raw = str(error.get("message") or response.text[:200] or response.status_code)
    if "appsecret_proof" in raw:
        message = "The app secret doesn't match the app that issued the token. Copy it from the Meta app's Settings → Basic."
    else:
        message = ERRORS.get(code if isinstance(code, int) else -1) or f"WhatsApp said: {raw}" + (f" ({details})" if details else "")
    if connection is not None and (code == 190 or response.status_code == 401):
        connection.status = "error"
        connection.last_error = message
    exc = IntegrationError(message, 502 if response.status_code >= 500 else 400)
    exc.code = code  # type: ignore[attr-defined]
    return exc


async def _graph(method: str, path: str, token: str, app_secret: str | None, connection: IntegrationConnection | None = None, **kwargs: Any) -> dict[str, Any]:
    url = path if path.startswith("https://") else f"{GRAPH}/{path.lstrip('/')}"
    params = {**(kwargs.pop("params", None) or {}), **_proof(token, app_secret)}
    response = await messaging.http(method, url, params=params, headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if response.status_code >= 400:
        raise _error(response, connection)
    try:
        return response.json()
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _placeholders(text: str | None) -> list[str]:
    seen: list[str] = []
    for name in PLACEHOLDER.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return seen


def _template_out(row: dict[str, Any]) -> dict[str, Any]:
    components = {str(c.get("type", "")).upper(): c for c in row.get("components") or []}
    header = components.get("HEADER") or {}
    body = components.get("BODY") or {}
    buttons = (components.get("BUTTONS") or {}).get("buttons") or []
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "language": row.get("language"),
        "status": row.get("status"),
        "category": row.get("category"),
        "parameter_format": (row.get("parameter_format") or "POSITIONAL").upper(),
        "header": {"format": header.get("format"), "text": header.get("text"), "params": _placeholders(header.get("text"))} if header else None,
        "body": body.get("text") or "",
        "body_params": _placeholders(body.get("text")),
        "footer": (components.get("FOOTER") or {}).get("text"),
        "buttons": [{"type": b.get("type"), "text": b.get("text")} for b in buttons][:10],
    }


async def sync_templates(db: AsyncSession, connection: IntegrationConnection) -> int:
    token, app_secret = _creds(connection)
    waba = (connection.config or {}).get("business_account_id")
    if not waba:
        raise IntegrationError("Add the WhatsApp Business Account ID to list templates.", 409)
    url = f"{waba}/message_templates"
    params: dict[str, Any] = {"fields": "id,name,language,status,category,components,parameter_format", "limit": 100}
    templates: list[dict[str, Any]] = []
    for _ in range(20):
        data = await _graph("GET", url, token, app_secret, connection, params=params)
        templates.extend(_template_out(row) for row in data.get("data") or [])
        after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
        if not (data.get("paging") or {}).get("next") or not after:
            break
        params = {**params, "after": after}
    templates.sort(key=lambda t: (t["status"] != "APPROVED", t["name"] or "", t["language"] or ""))
    connection.config = {**(connection.config or {}), "templates": templates[:500], "templates_synced_at": utcnow().isoformat()}
    return len(templates)


def find_template(connection: IntegrationConnection, name: str, language: str | None) -> dict[str, Any] | None:
    for row in (connection.config or {}).get("templates") or []:
        if row.get("name") == name and (not language or row.get("language") == language):
            return row
    return None


def _render(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text or "")


def build_template(connection: IntegrationConnection, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The Cloud API ``template`` object, and the text to keep for the conversation."""

    name = str(request.get("name") or "").strip()
    language = str(request.get("language") or "").strip()
    if not name:
        raise IntegrationError("Choose a template.", 422)
    template = find_template(connection, name, language)
    if template is None:
        raise IntegrationError("That template isn't in the synced list. Sync templates on the WhatsApp page and try again.", 422)
    if template.get("status") != "APPROVED":
        raise IntegrationError(f"The “{name}” template is {str(template.get('status') or 'not approved').lower()}, so it can't be sent yet.", 409)
    params = request.get("params") or {}
    body_values = {str(k): str(v).strip() for k, v in (params.get("body") or {}).items()}
    header_values = {str(k): str(v).strip() for k, v in (params.get("header") or {}).items()}
    named = template.get("parameter_format") == "NAMED"

    def parameters(names: list[str], values: dict[str, str], part: str) -> list[dict[str, Any]]:
        out = []
        for placeholder in names:
            value = values.get(placeholder, "")
            if not value:
                raise IntegrationError(f"Fill in {{{{{placeholder}}}}} in the template {part}.", 422)
            if len(value) > 1024:
                raise IntegrationError("A template value is too long.", 422)
            item: dict[str, Any] = {"type": "text", "text": value}
            if named:
                item["parameter_name"] = placeholder
            out.append(item)
        return out

    components: list[dict[str, Any]] = []
    header = template.get("header") or {}
    header_format = (header.get("format") or "").upper()
    if header_format == "TEXT" and header.get("params"):
        components.append({"type": "header", "parameters": parameters(header["params"], header_values, "header")})
    elif header_format in {"IMAGE", "VIDEO", "DOCUMENT"}:
        link = str(params.get("header_media_url") or "").strip()
        if not link.startswith("https://"):
            raise IntegrationError(f"This template needs a {header_format.lower()} link (https://…) for its header.", 422)
        components.append({"type": "header", "parameters": [{"type": header_format.lower(), header_format.lower(): {"link": link}}]})
    if template.get("body_params"):
        components.append({"type": "body", "parameters": parameters(template["body_params"], body_values, "body")})
    payload = {"name": template["name"], "language": {"code": template["language"]}}
    if components:
        payload["components"] = components
    text = _render(template.get("body") or "", body_values)
    if header_format == "TEXT" and header.get("text"):
        text = f"{_render(header['text'], header_values)}\n{text}"
    return payload, text.strip()


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    phone_id = (values.get("phone_number_id") or "").strip()
    waba_id = (values.get("business_account_id") or "").strip()
    token = (values.get("access_token") or "").strip()
    app_secret = (values.get("app_secret") or "").strip()
    if not NUMERIC_ID.match(phone_id):
        raise IntegrationError("The phone number ID is a long number from WhatsApp Manager → API setup (not the phone number itself).", 422)
    if not NUMERIC_ID.match(waba_id):
        raise IntegrationError("The WhatsApp Business Account ID is a long number from WhatsApp Manager.", 422)
    if not re.fullmatch(r"[0-9a-f]{32}", app_secret):
        raise IntegrationError("The app secret is 32 letters and numbers, from the Meta app's Settings → Basic.", 422)
    phone = await _graph(
        "GET", phone_id, token, app_secret,
        params={"fields": "display_phone_number,verified_name,quality_rating,code_verification_status,name_status,messaging_limit_tier"},
    )
    account = await _graph("GET", waba_id, token, app_secret, params={"fields": "id,name"})
    numbers = await _graph("GET", f"{waba_id}/phone_numbers", token, app_secret, params={"fields": "id", "limit": 100})
    if phone_id not in {str(row.get("id")) for row in numbers.get("data") or []}:
        raise IntegrationError("That phone number ID isn't part of this WhatsApp Business Account.", 400)
    display = phone.get("display_phone_number") or ""
    business = messaging.normalize_phone(display if display.startswith("+") else f"+{display}", None)
    name = phone.get("verified_name") or account.get("name") or "WhatsApp"
    return {
        "label": f"{name} · {display}" if display else name,
        "external_id": phone_id,
        "config": {
            "display_phone_number": display,
            "business_number": business,
            "verified_name": phone.get("verified_name"),
            "quality_rating": phone.get("quality_rating"),
            "messaging_limit_tier": phone.get("messaging_limit_tier"),
            "waba_name": account.get("name"),
        },
    }


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if not core.secret(connection, "verify_token"):
        core.set_secret(connection, "verify_token", secrets.token_urlsafe(24))
    token, app_secret = _creds(connection)
    waba = (connection.config or {}).get("business_account_id")
    try:
        await _graph("POST", f"{waba}/subscribed_apps", token, app_secret, connection)
        connection.config = {**(connection.config or {}), "app_subscribed": True}
    except IntegrationError as exc:
        core.log(db, connection.organization_id, key, "subscribe_failed", f"Couldn't subscribe the app to the business account's webhooks: {exc}", level="warning", connection=connection)
    try:
        count = await sync_templates(db, connection)
        core.log(db, connection.organization_id, key, "templates_synced", f"Synced {count} message templates.", connection=connection)
    except IntegrationError as exc:
        core.log(db, connection.organization_id, key, "templates_failed", f"Couldn't list message templates: {exc}", level="warning", connection=connection)


async def use_webhook_override(db: AsyncSession, connection: IntegrationConnection) -> None:
    """Point the business account's webhooks at AIVA (Meta verifies the URL right away)."""

    token, app_secret = _creds(connection)
    waba = (connection.config or {}).get("business_account_id")
    url = core.webhook_url("whatsapp", connection.webhook_token)
    verify = core.secret(connection, "verify_token")
    if not url or not verify:
        raise IntegrationError("Save the WhatsApp credentials first.", 409)
    await _graph("POST", f"{waba}/subscribed_apps", token, app_secret, connection, json={"override_callback_uri": url, "verify_token": verify})
    connection.config = {**(connection.config or {}), "app_subscribed": True, "webhook_override": True}


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    token, app_secret = _creds(connection)
    phone_id = (connection.config or {}).get("phone_number_id")
    phone = await _graph("GET", phone_id, token, app_secret, connection, params={"fields": "display_phone_number,verified_name,quality_rating,messaging_limit_tier"})
    connection.config = {**(connection.config or {}), "quality_rating": phone.get("quality_rating"), "messaging_limit_tier": phone.get("messaging_limit_tier")}
    approved = sum(1 for t in (connection.config or {}).get("templates") or [] if t.get("status") == "APPROVED")
    quality = (phone.get("quality_rating") or "unknown").lower()
    return f"Connected to {phone.get('verified_name') or 'your business'} ({phone.get('display_phone_number')}). Quality rating: {quality}. {approved} approved template{'s' if approved != 1 else ''}."


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    return clean_channel_settings(config)


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, key)
    settings = await core.workspace_config(db, org, key)
    out: dict[str, Any] = {
        "default_country_code": settings.get("default_country_code") or "1",
        "notify_owner": settings.get("notify_owner", True),
        "stats": await messaging.stats(db, org, "whatsapp"),
    }
    if connection is None:
        return out
    config = connection.config or {}
    templates = config.get("templates") or []
    out.update(
        {
            "webhook_url": core.webhook_url(key, connection.webhook_token),
            "business_number": config.get("business_number"),
            "display_phone_number": config.get("display_phone_number"),
            "verified_name": config.get("verified_name"),
            "quality_rating": config.get("quality_rating"),
            "messaging_limit_tier": config.get("messaging_limit_tier"),
            "app_subscribed": bool(config.get("app_subscribed")),
            "webhook_override": bool(config.get("webhook_override")),
            "templates": [
                {k: t.get(k) for k in ("name", "language", "status", "category", "body", "body_params", "header")} for t in templates
            ],
            "templates_synced_at": config.get("templates_synced_at"),
            "approved_templates": sum(1 for t in templates if t.get("status") == "APPROVED"),
        }
    )
    if principal.can("admin.settings"):
        out["verify_token"] = core.secret(connection, "verify_token")
    return out


async def send(db: AsyncSession, connection: IntegrationConnection, message: ChannelMessage, template: dict[str, Any] | None) -> None:
    token, app_secret = _creds(connection)
    config = connection.config or {}
    phone_id = config.get("phone_number_id")
    payload: dict[str, Any] = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": messaging.digits_of(message.to_number)}
    if template is not None:
        built, text = build_template(connection, template)
        payload.update(type="template", template=built)
        message.template_name = f"{built['name']} ({built['language']['code']})"[:200]
        message.body = text
    else:
        payload.update(type="text", text={"preview_url": "http" in (message.body or ""), "body": message.body})
    result = await _graph("POST", f"{phone_id}/messages", token, app_secret, connection, json=payload)
    ids = [m.get("id") for m in result.get("messages") or [] if m.get("id")]
    if not ids:
        raise IntegrationError("WhatsApp accepted the request but didn't return a message ID.", 502)
    message.external_id = ids[0]
    message.from_number = config.get("business_number")
    message.status = "sent"


async def fetch_media(db: AsyncSession, connection: IntegrationConnection, item: dict[str, Any]) -> tuple[bytes, str]:
    media_id = str(item.get("id") or "")
    if not NUMERIC_ID.match(media_id):
        raise IntegrationError("That attachment isn't available.", 404)
    token, app_secret = _creds(connection)
    info = await _graph("GET", media_id, token, app_secret, connection)
    url = info.get("url") or ""
    if not url.startswith("https://"):
        raise IntegrationError("WhatsApp no longer has that attachment.", 404)
    if int(info.get("file_size") or 0) > MEDIA_LIMIT:
        raise IntegrationError("That attachment is too large to open here.", 413)
    response = await messaging.http("GET", url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=True, timeout=60)
    if response.status_code >= 400:
        raise IntegrationError("WhatsApp no longer has that attachment.", 404)
    return response.content, info.get("mime_type") or item.get("mime_type") or "application/octet-stream"


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def valid_signature(app_secret: str | None, raw: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.removeprefix("sha256=").encode())


def _parse(item: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    kind = item.get("type")
    if kind == "text":
        return (item.get("text") or {}).get("body") or "", []
    if kind in {"image", "video", "audio", "document", "sticker"}:
        media = item.get(kind) or {}
        entry = {"type": kind, "id": media.get("id"), "mime_type": media.get("mime_type"), "caption": media.get("caption"), "filename": media.get("filename")}
        return media.get("caption") or "", [{k: v for k, v in entry.items() if v}]
    if kind == "location":
        loc = item.get("location") or {}
        text = " · ".join(p for p in (loc.get("name"), loc.get("address")) if p) or "Shared a location"
        return text, [{"type": "location", "latitude": loc.get("latitude"), "longitude": loc.get("longitude"), "name": loc.get("name"), "address": loc.get("address")}]
    if kind == "button":
        return (item.get("button") or {}).get("text") or "", []
    if kind == "interactive":
        reply = (item.get("interactive") or {})
        chosen = reply.get("button_reply") or reply.get("list_reply") or {}
        return chosen.get("title") or "", []
    if kind == "reaction":
        emoji = (item.get("reaction") or {}).get("emoji")
        return f"Reacted {emoji}" if emoji else "Removed a reaction", []
    if kind == "contacts":
        names = [((c.get("name") or {}).get("formatted_name") or "a contact") for c in item.get("contacts") or []]
        return f"Shared {', '.join(names) or 'a contact'}", []
    return "[A message type AIVA can't show yet]", []


async def _handle_value(db: AsyncSession, connection: IntegrationConnection, value: dict[str, Any]) -> int:
    org = connection.organization_id
    config = connection.config or {}
    metadata = value.get("metadata") or {}
    if metadata.get("phone_number_id") and str(metadata["phone_number_id"]) != str(config.get("phone_number_id")):
        return 0
    business = config.get("business_number")
    names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}
    handled = 0
    for item in value.get("messages") or []:
        sender = messaging.normalize_phone(f"+{item.get('from')}", None)
        if sender is None:
            continue
        body, media = _parse(item)
        try:
            sent_at = datetime.fromtimestamp(int(item.get("timestamp")), timezone.utc)
        except (TypeError, ValueError):
            sent_at = None
        message = await messaging.record_inbound(
            db,
            organization_id=org,
            channel="whatsapp",
            from_number=sender,
            to_number=business,
            body=body,
            media=media,
            external_id=item.get("id"),
            profile_name=names.get(item.get("from")),
            sent_at=sent_at,
        )
        if message is None:
            continue
        handled += 1
        word = messaging.keyword(body) if item.get("type") in {"text", "button"} else None
        if word == "stop" and await messaging.set_opted_out(db, org, sender, note=f"Replied “{body.strip()[:40]}” on WhatsApp."):
            core.log(db, org, "whatsapp", "opted_out", f"{messaging.pretty(sender)} opted out on WhatsApp.", connection=connection)
        elif word == "start" and await messaging.clear_opt_out(db, org, sender):
            core.log(db, org, "whatsapp", "opted_in", f"{messaging.pretty(sender)} opted back in on WhatsApp.", connection=connection)
    for item in value.get("statuses") or []:
        status = item.get("status")
        if status not in {"sent", "delivered", "read", "failed"} or not item.get("id"):
            continue
        errors = item.get("errors") or []
        error = None
        if errors:
            first = errors[0]
            error = ERRORS.get(first.get("code")) or first.get("message") or first.get("title")
        message = await messaging.apply_status(db, org, "whatsapp", item["id"], status, error)
        if message is not None:
            handled += 1
            if status == "failed":
                core.log(db, org, "whatsapp", "delivery_failed", f"WhatsApp message to {messaging.pretty(message.to_number)} failed: {error or 'unknown reason'}", level="warning", connection=connection)
    for problem in value.get("errors") or []:
        core.log(db, org, "whatsapp", "webhook_error", f"WhatsApp reported: {problem.get('message') or problem.get('title')}", level="warning", connection=connection)
    return handled


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method == "GET":
        params = request.query_params
        verify = core.secret(connection, "verify_token") or ""
        given = params.get("hub.verify_token") or ""
        if params.get("hub.mode") == "subscribe" and verify and hmac.compare_digest(given.encode(), verify.encode()):
            core.log(db, connection.organization_id, key, "webhook_verified", "Meta verified the webhook URL.", level="success", connection=connection)
            return PlainTextResponse(params.get("hub.challenge") or "")
        return PlainTextResponse("Verification failed", status_code=403)
    raw = await request.body()
    if not valid_signature(core.secret(connection, "app_secret"), raw, request.headers.get("X-Hub-Signature-256")):
        core.log(db, connection.organization_id, key, "webhook_rejected", "Rejected a webhook with a missing or wrong signature. Check that the app secret belongs to the Meta app sending webhooks.",
                 level="warning", connection=connection)
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if payload.get("object") != "whatsapp_business_account":
        return JSONResponse({"ok": True, "ignored": True})
    handled = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") == "messages":
                handled += await _handle_value(db, connection, change.get("value") or {})
    if handled:
        connection.last_sync_at = utcnow()
    return JSONResponse({"ok": True})


async def run_due(db: AsyncSession) -> int:
    """Keep each workspace's template list fresh."""

    rows = (await db.execute(select(IntegrationConnection).where(IntegrationConnection.provider == "whatsapp", IntegrationConnection.user_id.is_(None)))).scalars().all()

    def synced_at(connection: IntegrationConnection) -> datetime | None:
        try:
            value = (connection.config or {}).get("templates_synced_at")
            return datetime.fromisoformat(value) if value else None
        except ValueError:
            return None

    stale = [c for c in rows if (synced_at(c) is None or utcnow() - synced_at(c) >= TEMPLATE_SYNC_EVERY)]
    stale.sort(key=lambda c: synced_at(c) or datetime.min.replace(tzinfo=timezone.utc))  # longest-waiting first
    handled = 0
    for connection in stale:
        if connection.status != "connected" or not await core.is_enabled(db, connection.organization_id, "whatsapp"):
            continue
        try:
            await sync_templates(db, connection)
            handled += 1
        except IntegrationError as exc:
            connection.config = {**(connection.config or {}), "templates_synced_at": utcnow().isoformat()}
            core.log(db, connection.organization_id, "whatsapp", "templates_failed", f"Couldn't refresh message templates: {exc}", level="warning", connection=connection)
        if handled >= 20:
            break
    await db.commit()
    return handled
