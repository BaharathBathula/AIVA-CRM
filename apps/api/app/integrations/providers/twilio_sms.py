"""Twilio SMS: send texts, receive replies and delivery updates.

Connects with the account SID and auth token (plus a Twilio number or a
messaging service). Inbound messages and status callbacks come to the
connection's webhook URL and are only accepted with a valid
``X-Twilio-Signature`` (HMAC-SHA1 of the URL Twilio called plus the sorted POST
parameters, keyed with the auth token). STOP / START replies put the number on
or take it off the do-not-contact list.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core, messaging
from app.integrations.core import IntegrationError
from app.models.integration_hub import ChannelMessage, IntegrationConnection

API = "https://api.twilio.com/2010-04-01"
MESSAGING_API = "https://messaging.twilio.com/v1"
ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
SERVICE_SID = re.compile(r"^MG[0-9a-fA-F]{32}$")
NUMBER_SID = re.compile(r"^PN[0-9a-fA-F]{32}$")
TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# Twilio API error codes → what to tell people.
ERRORS: dict[int, str] = {
    20003: "Twilio didn't accept the account SID and auth token. Copy them again from the Twilio Console.",
    20404: "Twilio couldn't find that in this account.",
    21211: "That isn't a valid phone number.",
    21408: "Your Twilio account isn't allowed to text that country. Turn it on in Twilio under Messaging → Settings → Geo permissions.",
    21606: "The Twilio number can't text this destination. Use an SMS-capable number.",
    21608: "Twilio trial accounts can only text verified numbers. Verify the number in Twilio or upgrade the account.",
    21610: "This person replied STOP, so carriers block texts to them until they text START.",
    21612: "Twilio can't send between these two numbers (the route isn't supported).",
    21614: "That number can't receive texts (it may be a landline).",
    21617: "The message is too long.",
    21660: "The From number doesn't belong to this Twilio account.",
    21703: "The messaging service has no phone numbers. Add one to it in Twilio.",
    30034: "Texts from this US number are blocked until its A2P 10DLC registration is approved in Twilio.",
}
# Delivery failures reported in status callbacks.
DELIVERY_ERRORS: dict[str, str] = {
    "21610": ERRORS[21610],
    "30003": "The phone is unreachable (switched off or out of coverage).",
    "30004": "The recipient's carrier blocked the message.",
    "30005": "The number doesn't exist or isn't in service.",
    "30006": "The number is a landline or can't receive texts.",
    "30007": "The carrier filtered the message as spam.",
    "30008": "The carrier couldn't deliver it.",
    "30032": "The toll-free number isn't verified yet.",
    "30034": ERRORS[30034],
}
STATUS_MAP = {
    "accepted": "queued",
    "scheduled": "queued",
    "queued": "queued",
    "sending": "queued",
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "undelivered": "failed",
    "failed": "failed",
    "canceled": "failed",
}

_CACHE: dict[str, tuple[float, Any]] = {}


class TwilioError(IntegrationError):
    def __init__(self, message: str, status_code: int = 400, code: int | None = None):
        super().__init__(message, status_code)
        self.code = code
        self.opted_out = code == 21610


def _creds(connection: IntegrationConnection) -> tuple[str, str]:
    sid = (connection.config or {}).get("account_sid") or ""
    token = core.secret(connection, "auth_token") or ""
    if not sid or not token:
        raise IntegrationError("Twilio isn't fully connected. Add the account SID and auth token.", 409)
    return sid, token


def _error(response, connection: IntegrationConnection | None) -> TwilioError:
    try:
        data = response.json()
    except ValueError:
        data = {}
    code = data.get("code") if isinstance(data, dict) else None
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    message = ERRORS.get(code or 0) or f"Twilio said: {(data.get('message') if isinstance(data, dict) else None) or response.text[:200] or response.status_code}"
    if connection is not None and (response.status_code == 401 or code == 20003):
        connection.status = "error"
        connection.last_error = message
    return TwilioError(message, 502 if response.status_code >= 500 else 400, code)


async def _call(method: str, path: str, sid: str, token: str, connection: IntegrationConnection | None = None, **kwargs: Any) -> dict[str, Any]:
    url = path if path.startswith("https://") else f"{API}{path}"
    response = await messaging.http(method, url, auth=(sid, token), headers={"Accept": "application/json"}, **kwargs)
    if response.status_code >= 400:
        raise _error(response, connection)
    try:
        return response.json() if response.content else {}
    except ValueError:
        return {}


def status_callback_url(connection: IntegrationConnection) -> str | None:
    base = core.webhook_url("twilio_sms", connection.webhook_token)
    return f"{base}?event=status" if base else None


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


def signature_for(auth_token: str, url: str, params: list[tuple[str, str]]) -> str:
    payload = url + "".join(f"{key}{value}" for key, value in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _url_variants(url: str) -> list[str]:
    """Twilio signs the URL it called; some setups add or drop the default port."""

    parts = urlsplit(url)
    variants = [url]
    default = 443 if parts.scheme == "https" else 80
    if parts.port is None and parts.hostname:
        variants.append(urlunsplit((parts.scheme, f"{parts.hostname}:{default}", parts.path, parts.query, parts.fragment)))
    elif parts.port == default and parts.hostname:
        variants.append(urlunsplit((parts.scheme, parts.hostname, parts.path, parts.query, parts.fragment)))
    return variants


def valid_signature(auth_token: str | None, url: str, params: list[tuple[str, str]], signature: str | None) -> bool:
    if not auth_token or not signature:
        return False
    return any(hmac.compare_digest(signature_for(auth_token, candidate, params), signature) for candidate in _url_variants(url))


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    sid = (values.get("account_sid") or "").strip()
    token = (values.get("auth_token") or "").strip()
    if not ACCOUNT_SID.match(sid):
        raise IntegrationError("Account SIDs start with AC followed by 32 letters and numbers. Copy it from the Twilio Console.", 422)
    account = await _call("GET", f"/Accounts/{sid}.json", sid, token)
    state = account.get("status")
    if state and state != "active":
        raise IntegrationError(f"This Twilio account is {state}. Reactivate it in Twilio first.", 400)
    config: dict[str, Any] = {"account_name": account.get("friendly_name"), "account_type": account.get("type"), "account_sid": sid}
    raw_number = (values.get("from_number") or "").strip()
    service_sid = (values.get("messaging_service_sid") or "").strip()
    if not raw_number and not service_sid:
        raise IntegrationError("Add your Twilio phone number or a messaging service SID so AIVA knows what to send from.", 422)
    label = account.get("friendly_name") or "Twilio"
    if raw_number:
        number = messaging.normalize_phone(raw_number, await messaging.default_country_code(db, organization_id, "sms"))
        if number is None:
            raise IntegrationError("The Twilio phone number should be in international format, like +12145550100.", 422)
        found = await _call("GET", f"/Accounts/{sid}/IncomingPhoneNumbers.json", sid, token, params={"PhoneNumber": number})
        numbers = found.get("incoming_phone_numbers") or []
        if not numbers:
            raise IntegrationError(f"{messaging.pretty(number)} isn't a phone number in this Twilio account.", 400)
        row = numbers[0]
        if not (row.get("capabilities") or {}).get("sms", True):
            raise IntegrationError(f"{messaging.pretty(number)} can't send texts. Pick an SMS-capable Twilio number.", 400)
        config.update(from_number=number, number_sid=row.get("sid"), number_sms_url=row.get("sms_url"))
        label = f"{label} · {messaging.pretty(number)}"
    else:
        config.update(from_number=None, number_sid=None, number_sms_url=None)
    if service_sid:
        if not SERVICE_SID.match(service_sid):
            raise IntegrationError("Messaging service SIDs start with MG followed by 32 letters and numbers.", 422)
        service = await _call("GET", f"{MESSAGING_API}/Services/{service_sid}", sid, token)
        config.update(messaging_service_sid=service_sid, messaging_service_name=service.get("friendly_name"))
        if not raw_number:
            label = f"{label} · {service.get('friendly_name') or 'messaging service'}"
    else:
        config.update(messaging_service_sid=None, messaging_service_name=None)
    _CACHE.pop(f"numbers:{organization_id}", None)
    return {"label": label, "external_id": sid, "config": config}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    number_url = (connection.config or {}).get("number_sms_url")
    if (connection.config or {}).get("number_sid") and number_url != core.webhook_url(key, connection.webhook_token):
        core.log(
            db, connection.organization_id, key, "webhook_pending",
            "Replies won't reach AIVA until the number's \"A message comes in\" webhook points at AIVA. Use \"Point the number at AIVA\" on the Twilio page.",
            level="warning", connection=connection,
        )


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    sid, token = _creds(connection)
    account = await _call("GET", f"/Accounts/{sid}.json", sid, token, connection)
    if account.get("status") != "active":
        raise IntegrationError(f"The Twilio account is {account.get('status')}.", 400)
    config = connection.config or {}
    sender = messaging.pretty(config.get("from_number")) or config.get("messaging_service_name") or "your messaging service"
    return f"Twilio account “{account.get('friendly_name')}” is active ({account.get('type') or 'Full'}). Sending from {sender}."


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    return clean_channel_settings(config)


def clean_channel_settings(config: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    if "default_country_code" in config:
        code = re.sub(r"\D", "", str(config.get("default_country_code") or ""))
        if not 1 <= len(code) <= 3:
            raise IntegrationError("The default country code is 1 to 3 digits, like 1 for the US or 44 for the UK.", 422)
        cleaned["default_country_code"] = code
    if "notify_owner" in config:
        cleaned["notify_owner"] = bool(config.get("notify_owner"))
    return cleaned


async def list_numbers(db: AsyncSession, connection: IntegrationConnection, refresh: bool = False) -> list[dict[str, Any]]:
    cache_key = f"numbers:{connection.organization_id}"
    cached = _CACHE.get(cache_key)
    if cached and not refresh and cached[0] > time.monotonic():
        return cached[1]
    sid, token = _creds(connection)
    data = await _call("GET", f"/Accounts/{sid}/IncomingPhoneNumbers.json", sid, token, connection, params={"PageSize": 100})
    here = core.webhook_url("twilio_sms", connection.webhook_token)
    numbers = [
        {
            "sid": row.get("sid"),
            "phone_number": row.get("phone_number"),
            "friendly_name": row.get("friendly_name"),
            "sms_capable": bool((row.get("capabilities") or {}).get("sms", True)),
            "points_here": bool(here and row.get("sms_url") == here),
            "sms_url_set": bool(row.get("sms_url")),
        }
        for row in data.get("incoming_phone_numbers") or []
    ]
    _CACHE[cache_key] = (time.monotonic() + 120, numbers)
    return numbers


async def point_number_here(db: AsyncSession, connection: IntegrationConnection, number_sid: str) -> dict[str, Any]:
    if not NUMBER_SID.match(number_sid or ""):
        raise IntegrationError("That isn't a Twilio phone number SID.", 422)
    sid, token = _creds(connection)
    url = core.webhook_url("twilio_sms", connection.webhook_token)
    row = await _call("POST", f"/Accounts/{sid}/IncomingPhoneNumbers/{number_sid}.json", sid, token, connection, data={"SmsUrl": url, "SmsMethod": "POST"})
    if (connection.config or {}).get("number_sid") == number_sid:
        connection.config = {**(connection.config or {}), "number_sms_url": url}
    _CACHE.pop(f"numbers:{connection.organization_id}", None)
    return {"sid": row.get("sid"), "phone_number": row.get("phone_number")}


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, key)
    settings = await core.workspace_config(db, org, key)
    out: dict[str, Any] = {
        "default_country_code": settings.get("default_country_code") or "1",
        "notify_owner": settings.get("notify_owner", True),
        "stats": await messaging.stats(db, org, "sms"),
    }
    if connection is None:
        return out
    config = connection.config or {}
    out.update(
        {
            "webhook_url": core.webhook_url(key, connection.webhook_token),
            "status_callback_url": status_callback_url(connection),
            "from_number": config.get("from_number"),
            "messaging_service_sid": config.get("messaging_service_sid"),
            "messaging_service_name": config.get("messaging_service_name"),
            "account_name": config.get("account_name"),
            "account_type": config.get("account_type"),
            "number_points_here": bool(config.get("number_sid")) and config.get("number_sms_url") == core.webhook_url(key, connection.webhook_token),
        }
    )
    if principal.can("admin.settings"):
        try:
            out["numbers"] = await list_numbers(db, connection)
        except IntegrationError as exc:
            out["numbers_error"] = str(exc)
    return out


async def send(db: AsyncSession, connection: IntegrationConnection, message: ChannelMessage, template: dict[str, Any] | None) -> None:
    if template is not None:
        raise IntegrationError("Templates are for WhatsApp; SMS sends plain text.", 422)
    sid, token = _creds(connection)
    config = connection.config or {}
    data = {"To": message.to_number, "Body": message.body}
    callback = status_callback_url(connection)
    if callback:
        data["StatusCallback"] = callback
    if config.get("messaging_service_sid"):
        data["MessagingServiceSid"] = config["messaging_service_sid"]
    elif config.get("from_number"):
        data["From"] = config["from_number"]
    else:
        raise IntegrationError("Choose the Twilio number to send from on the Twilio integration page.", 409)
    result = await _call("POST", f"/Accounts/{sid}/Messages.json", sid, token, connection, data=data)
    message.external_id = result.get("sid")
    message.from_number = result.get("from") or config.get("from_number")
    message.status = STATUS_MAP.get(result.get("status") or "queued", "queued")
    if result.get("error_code"):
        message.status = "failed"
        message.error = DELIVERY_ERRORS.get(str(result["error_code"])) or result.get("error_message") or "Twilio couldn't send it."


async def fetch_media(db: AsyncSession, connection: IntegrationConnection, item: dict[str, Any]) -> tuple[bytes, str]:
    url = item.get("url") or ""
    if not url.startswith("https://api.twilio.com/"):
        raise IntegrationError("That attachment isn't available.", 404)
    sid, token = _creds(connection)
    response = await messaging.http("GET", url, auth=(sid, token), follow_redirects=True, timeout=40)
    if response.status_code >= 400:
        raise IntegrationError("Twilio no longer has that attachment.", 404)
    return response.content, response.headers.get("content-type") or item.get("mime_type") or "application/octet-stream"


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method != "POST":
        return JSONResponse({"ok": True, "message": "AIVA receives Twilio messages here (POST)."})
    form = await request.form()
    params = [(k, v if isinstance(v, str) else "") for k, v in form.multi_items()]
    query = request.url.query
    url = (core.webhook_url(key, connection.webhook_token) or "") + (f"?{query}" if query else "")
    token = core.secret(connection, "auth_token")
    if not valid_signature(token, url, params, request.headers.get("X-Twilio-Signature")):
        core.log(
            db, connection.organization_id, key, "webhook_rejected",
            "Rejected a request with a missing or wrong Twilio signature. If this keeps happening, check that APP_BASE_URL matches the address Twilio calls.",
            level="warning", connection=connection, detail={"expected_url": url},
        )
        return JSONResponse({"error": "invalid signature"}, status_code=403)
    values = dict(params)
    if values.get("AccountSid") and values["AccountSid"] != (connection.config or {}).get("account_sid"):
        return JSONResponse({"error": "wrong account"}, status_code=403)
    org = connection.organization_id
    cc = await messaging.default_country_code(db, org, "sms")

    if request.query_params.get("event") == "status" or ("MessageStatus" in values and "Body" not in values):
        sid = values.get("MessageSid") or values.get("SmsSid")
        status = STATUS_MAP.get((values.get("MessageStatus") or values.get("SmsStatus") or "").lower())
        if sid and status:
            code = values.get("ErrorCode") or ""
            error = DELIVERY_ERRORS.get(code) or (f"Twilio error {code}." if code else None)
            message = await messaging.apply_status(db, org, "sms", sid, status, error)
            if message is not None and values.get("From") and not message.from_number:
                message.from_number = messaging.normalize_phone(values["From"], cc)
            if message is not None and status == "failed":
                core.log(db, org, key, "delivery_failed", f"Text to {messaging.pretty(message.to_number)} wasn't delivered: {error or 'unknown reason'}", level="warning", connection=connection)
                if code == "21610" and message.to_number:
                    await messaging.set_opted_out(db, org, message.to_number, note="Carrier reports the number opted out (SMS).")
        return Response(status_code=204)

    sender = messaging.normalize_phone(values.get("From"), cc)
    if sender is None:
        return Response(TWIML_EMPTY, media_type="text/xml")
    media = []
    try:
        count = min(int(values.get("NumMedia") or 0), 10)
    except ValueError:
        count = 0
    for index in range(count):
        if values.get(f"MediaUrl{index}"):
            media.append({"type": "media", "url": values[f"MediaUrl{index}"], "mime_type": values.get(f"MediaContentType{index}")})
    body = values.get("Body") or ""
    message = await messaging.record_inbound(
        db,
        organization_id=org,
        channel="sms",
        from_number=sender,
        to_number=messaging.normalize_phone(values.get("To"), cc) or values.get("To"),
        body=body,
        media=media,
        external_id=values.get("MessageSid") or values.get("SmsMessageSid"),
    )
    if message is not None:
        connection.last_sync_at = messaging.utcnow()
        opt = (values.get("OptOutType") or "").lower() or messaging.keyword(body)
        if opt == "stop":
            if await messaging.set_opted_out(db, org, sender, note=f"Replied “{body.strip()[:40]}” by SMS."):
                core.log(db, org, key, "opted_out", f"{messaging.pretty(sender)} opted out (replied {body.strip()[:20] or 'STOP'}).", connection=connection)
        elif opt == "start":
            if await messaging.clear_opt_out(db, org, sender):
                core.log(db, org, key, "opted_in", f"{messaging.pretty(sender)} opted back in.", connection=connection)
    return Response(TWIML_EMPTY, media_type="text/xml")


async def run_due(db: AsyncSession) -> int:
    """Catch up on messages whose status callback never arrived (e.g. a firewall)."""

    now = messaging.utcnow()
    rows = (
        await db.execute(
            select(ChannelMessage)
            .where(
                ChannelMessage.channel == "sms",
                ChannelMessage.direction == "outbound",
                ChannelMessage.status == "queued",
                ChannelMessage.external_id.is_not(None),
                ChannelMessage.created_at > now - timedelta(days=2),
                ChannelMessage.updated_at < now - timedelta(minutes=10),
            )
            .order_by(ChannelMessage.updated_at)
            .limit(40)
        )
    ).scalars().all()
    if not rows:
        return 0
    connections: dict[uuid.UUID, IntegrationConnection | None] = {}
    handled = 0
    for message in rows:
        org = message.organization_id
        if org not in connections:
            enabled = await core.is_enabled(db, org, "twilio_sms")
            connections[org] = await core.connection_for(db, org, "twilio_sms") if enabled else None
        connection = connections[org]
        message.updated_at = now
        if connection is None:
            continue
        try:
            sid, token = _creds(connection)
            data = await _call("GET", f"/Accounts/{sid}/Messages/{message.external_id}.json", sid, token)
        except IntegrationError:
            continue
        status = STATUS_MAP.get((data.get("status") or "").lower())
        if status:
            code = str(data.get("error_code") or "")
            await messaging.apply_status(db, org, "sms", message.external_id, status, DELIVERY_ERRORS.get(code) or data.get("error_message"))
            handled += 1
    await db.commit()
    return handled
