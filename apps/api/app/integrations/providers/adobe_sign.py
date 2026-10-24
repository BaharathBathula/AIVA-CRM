"""Adobe Acrobat Sign: send a quote or an AIVA document for signature and follow it.

* Connecting: an admin pastes an integration key (Acrobat Sign → Account →
  Acrobat Sign API → API information). AIVA looks up the account's API access
  point (``GET https://api.adobesign.com/api/rest/v6/baseUris``) and only ever
  calls an ``https://*.adobesign.com`` / ``*.echosign.com`` address.
* Sending: the file is uploaded as a transient document, then an agreement is
  created ``IN_PROCESS`` with one participant set per signer (same signing
  order = sign in parallel), copies, the message, reminders and expiry. Quotes
  carry invisible Acrobat Sign text tags (``{{Sig1_es_:signer1:signature}}``)
  so each signer's signature and date land in the right place; uploaded
  documents can use the same tags, otherwise signers place their own.
* Status: an account webhook (registered on connect when AIVA runs on public
  https) nudges AIVA; the notification is never trusted, AIVA reads the
  agreement, its members and events back from the API. Acrobat Sign checks
  webhook URLs by expecting its ``X-AdobeSign-ClientId`` header echoed back, on
  the first check and on every notification. Open agreements are also polled
  every 15 minutes.
* Completion: the combined signed PDF (with the audit report when chosen) is
  saved to Documents and linked; quotes are marked accepted per the settings.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import quote as urlquote
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _doc_store, _sales_http, _signing
from app.integrations.providers._signing import settings_for
from app.models.collaboration import Document
from app.models.integration_hub import IntegrationConnection, SignatureRequest

logger = logging.getLogger("aiva.integrations.adobe_sign")

KEY = "adobe_sign"
VENDOR = "Adobe Acrobat Sign"
DISCOVERY = "https://api.adobesign.com/api/rest/v6/baseUris"
SUPPORTS = {"quotes": True, "documents": True, "templates": False, "void": True, "remind": True}
POLL_EVERY = timedelta(minutes=15)
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "rtf", "txt", "htm", "html", "xls", "xlsx", "ppt", "pptx", "odt", "png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"}
MIME = {
    "pdf": "application/pdf", "doc": "application/msword", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "rtf": "application/rtf", "txt": "text/plain", "htm": "text/html", "html": "text/html", "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "odt": "application/vnd.oasis.opendocument.text",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff",
}
ALLOWED_HOSTS = (".adobesign.com", ".echosign.com", ".adobesign.us")
IN_PROGRESS = {
    "OUT_FOR_SIGNATURE", "OUT_FOR_DELIVERY", "OUT_FOR_ACCEPTANCE", "OUT_FOR_FORM_FILLING", "OUT_FOR_APPROVAL",
    "WAITING_FOR_VERIFICATION", "WAITING_FOR_FAXIN", "WAITING_FOR_NOTARIZATION", "WIDGET_WAITING_FOR_VERIFICATION",
}
DONE = {"SIGNED", "APPROVED", "ACCEPTED", "DELIVERED", "FORM_FILLED", "ARCHIVED"}
VIEWED_EVENTS = {"EMAIL_VIEWED", "VIEWED", "DOCUMENTS_VIEWED"}
SIGNED_EVENTS = {"ESIGNED", "SIGNED", "DIGSIGNED", "APPROVED", "ACCEPTED", "FORM_FILLED", "ACTION_COMPLETED", "PARTICIPANT_COMPLETED"}
DECLINED_EVENTS = {"REJECTED", "DECLINED"}
REMINDER = ((1, "DAILY_UNTIL_SIGNED"), (2, "EVERY_OTHER_DAY_UNTIL_SIGNED"), (4, "EVERY_THIRD_DAY_UNTIL_SIGNED"), (6, "EVERY_FIFTH_DAY_UNTIL_SIGNED"))


def _sign_tag(index: int) -> str:
    return f"{{{{Sig{index}_es_:signer{index}:signature}}}}"


def _date_tag(index: int) -> str:
    return f"{{{{Dte{index}_es_:signer{index}:date}}}}"


# ---------------------------------------------------------------------------
# Talking to Acrobat Sign
# ---------------------------------------------------------------------------


def check_access_point(value: str | None) -> str:
    """The account's API access point, refused unless it's an Acrobat Sign https address."""

    value = (value or "").strip()
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host.endswith(ALLOWED_HOSTS) or parts.username or parts.port not in (None, 443):
        raise IntegrationError("That isn't an Acrobat Sign API access point (it looks like https://api.na1.adobesign.com/).", 422)
    return f"https://{host}/"


def _key(connection: IntegrationConnection) -> str:
    value = core.secret(connection, "integration_key")
    if not value:
        raise IntegrationError("Paste an Acrobat Sign integration key first.", 409)
    return value


def api_base(connection: IntegrationConnection) -> str:
    return f"{check_access_point((connection.config or {}).get('api_access_point'))}api/rest/v6"


async def _api(connection: IntegrationConnection, method: str, path: str, *, ok: tuple[int, ...] = (), timeout: float = 30, **kwargs: Any) -> Any:
    headers = {"Authorization": f"Bearer {_key(connection)}", "Accept": "application/json", **(kwargs.pop("headers", None) or {})}
    return await _sales_http.call(VENDOR, method, f"{api_base(connection)}{path}", connection=connection, headers=headers, ok=ok, timeout=timeout, **kwargs)


async def _discover(integration_key: str) -> dict[str, str]:
    response = await _sales_http.call(VENDOR, "GET", DISCOVERY, headers={"Authorization": f"Bearer {integration_key}", "Accept": "application/json"})
    data = response.json()
    return {"api": check_access_point(data.get("apiAccessPoint")), "web": str(data.get("webAccessPoint") or "")[:300]}


def _push_url(connection: IntegrationConnection | None) -> str | None:
    url = core.webhook_url(KEY, connection.webhook_token) if connection is not None else None
    return url if url and url.startswith("https://") else None


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def signable(name: str, content_type: str | None, size: int) -> bool:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return size <= MAX_DOCUMENT_BYTES and (extension in ALLOWED_EXTENSIONS or content_type == "application/pdf")


def agreement_body(
    request_row: SignatureRequest, transient_id: str, recipients: list[dict[str, Any]], options: dict[str, Any]
) -> dict[str, Any]:
    signers = _signing.signers_in_order(recipients)
    body: dict[str, Any] = {
        "fileInfos": [{"transientDocumentId": transient_id}],
        "name": request_row.subject[:255],
        "participantSetsInfo": [
            {"memberInfos": [{"email": s["email"], "name": s["name"]}], "order": int(s["routing_order"]), "role": "SIGNER"} for s in signers
        ],
        "signatureType": "ESIGN",
        "state": "IN_PROCESS",
        "externalId": {"id": str(request_row.id)},
    }
    copies = [{"email": r["email"]} for r in recipients if r["role"] == "cc"]
    if copies:
        body["ccs"] = copies
    if request_row.message:
        body["message"] = request_row.message[:10000]
    days = int(options.get("reminder_days") or 0)
    if days:
        body["reminderFrequency"] = next((value for limit, value in REMINDER if days <= limit), "WEEKLY_UNTIL_SIGNED")
    expire = int(options.get("expire_days") or 0)
    if expire:
        body["expirationTime"] = (core.utcnow() + timedelta(days=expire)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return body


async def send(
    db: AsyncSession,
    *,
    principal: Any,
    connection: IntegrationConnection,
    record_type: str | None,
    record_id: uuid.UUID | None,
    subject: str,
    message: str | None,
    recipients: list[dict[str, Any]],
    quote_row: Any = None,
    document: Document | None = None,
    template_id: str | None = None,
) -> SignatureRequest:
    if template_id:
        raise IntegrationError("Acrobat Sign sends AIVA quotes and documents; templates are a PandaDoc option.", 422)
    cleaned = _signing.clean_recipients(recipients)
    signers = _signing.signers_in_order(cleaned)
    if quote_row is not None:
        if not quote_row.line_items:
            raise IntegrationError("Add at least one line item to the quote before sending it for signature.", 409)
        data = await _signing.quote_html(db, quote_row, signers, sign_tag=_sign_tag, date_tag=_date_tag)
        file_name = f"Quote Q-{quote_row.number}.html"
        document_name = f"Quote Q-{quote_row.number} · {quote_row.name}"
    elif document is not None:
        stored = await _doc_store.latest_file(db, document)
        if stored is None:
            raise IntegrationError("That document has no file to send.", 409)
        if not signable(stored.file_name, document.content_type, stored.size_bytes):
            raise IntegrationError("Acrobat Sign accepts PDF, Word, Excel, PowerPoint, text, HTML and image files up to 10 MB.", 415)
        data = stored.data
        file_name = stored.file_name
        document_name = document.name
    else:
        raise IntegrationError("Choose a quote or a document to send.", 422)
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "pdf"

    request_row = _signing.new_request(
        db, principal, provider=KEY, record_type=record_type, record_id=record_id, subject=subject, message=message,
        recipients=cleaned, document_name=document_name, quote_row=quote_row, document=document,
    )
    await db.flush()
    options = settings_for(await core.workspace_config(db, principal.organization_id, KEY))
    try:
        uploaded = await _api(
            connection, "POST", "/transientDocuments", timeout=90,
            data={"File-Name": file_name[:255], "Mime-Type": MIME.get(extension, "application/octet-stream")},
            files={"File": (file_name[:255], data, MIME.get(extension, "application/octet-stream"))},
        )
        transient_id = str(uploaded.json().get("transientDocumentId") or "")
        if not transient_id:
            raise IntegrationError("Acrobat Sign didn't accept the file.", 502)
        created = await _api(connection, "POST", "/agreements", json=agreement_body(request_row, transient_id, cleaned, options), timeout=60)
    except IntegrationError as exc:
        request_row.status = "error"
        request_row.error = str(exc)[:1000]
        _signing.log(db, request_row, "send_failed", f"Couldn't send “{document_name}”: {exc}", level="error", connection=connection, user_id=principal.user_id)
        raise
    request_row.envelope_id = str(created.json().get("id") or "")[:100] or None
    _signing.mark_sent(db, request_row, connection=connection, principal=principal, quote_row=quote_row)
    return request_row


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _agreement_path(request_row: SignatureRequest) -> str:
    if not request_row.envelope_id:
        raise IntegrationError("This request was never sent.", 409)
    return f"/agreements/{urlquote(request_row.envelope_id, safe='')}"


async def _members(connection: IntegrationConnection, request_row: SignatureRequest) -> dict[str, dict[str, Any]]:
    """Participant id and status by email."""

    data = (await _api(connection, "GET", f"{_agreement_path(request_row)}/members")).json()
    out: dict[str, dict[str, Any]] = {}
    for participant_set in data.get("participantSets") or []:
        for member in participant_set.get("memberInfos") or []:
            email = str(member.get("email") or "").lower()
            if email:
                out[email] = {"id": member.get("id"), "set_status": str(participant_set.get("status") or "").upper(), "status": str(member.get("status") or "").upper()}
    return out


async def _download(connection: IntegrationConnection, request_row: SignatureRequest, certificate: bool) -> bytes:
    response = await _api(
        connection, "GET", f"{_agreement_path(request_row)}/combinedDocument", timeout=90,
        params={"attachAuditReport": "true" if certificate else "false"}, headers={"Accept": "application/pdf, */*"},
    )
    return response.content


async def refresh(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest) -> bool:
    """Reads the agreement, its members and events back from Acrobat Sign and applies any change."""

    if not request_row.envelope_id:
        return False
    path = _agreement_path(request_row)
    agreement = (await _api(connection, "GET", path)).json()
    events = (await _api(connection, "GET", f"{path}/events", ok=(404,))).json().get("events") or []
    members = await _members(connection, request_row)
    request_row.last_checked_at = core.utcnow()
    changed = False
    recipients = [dict(r) for r in request_row.recipients or []]
    by_email = {r["email"].lower(): r for r in recipients}
    rejected = False
    for event in sorted(events, key=lambda e: str(e.get("date") or "")):
        kind = str(event.get("type") or "").upper()
        recipient = by_email.get(str(event.get("participantEmail") or event.get("actingUserEmail") or "").lower())
        stamp = _signing.when(event.get("date"))
        if kind in DECLINED_EVENTS:
            rejected = True
        if recipient is None:
            continue
        if kind in VIEWED_EVENTS:
            changed |= _signing.set_recipient(db, request_row, recipient, "delivered", at=stamp, connection=connection)
        elif kind in SIGNED_EVENTS and recipient["role"] == "signer":
            changed |= _signing.set_recipient(db, request_row, recipient, "signed", at=stamp, connection=connection)
        elif kind in DECLINED_EVENTS:
            changed |= _signing.set_recipient(db, request_row, recipient, "declined", at=stamp, reason=event.get("comment"), connection=connection)
    for email, member in members.items():
        recipient = by_email.get(email)
        if recipient is None:
            continue
        recipient["participant_id"] = member.get("id")
        if recipient["role"] == "signer" and member["set_status"] == "COMPLETED" and recipient.get("status") not in {"signed", "completed", "declined"}:
            changed |= _signing.set_recipient(db, request_row, recipient, "signed", connection=connection)
        elif recipient.get("status") == "created" and member["set_status"].startswith("WAITING_FOR_MY"):
            recipient["status"] = "sent"
            changed = True
    request_row.recipients = recipients

    status = str(agreement.get("status") or "").upper()
    viewed = any(r.get("status") == "delivered" for r in recipients)
    if status in DONE:
        new_status = "completed"
    elif status == "CANCELLED":
        new_status = "declined" if rejected or any(r.get("status") == "declined" for r in recipients) else "voided"
    elif status == "EXPIRED":
        new_status = "voided"
    elif status in IN_PROGRESS:
        new_status = "delivered" if viewed else "sent"
    else:
        new_status = request_row.status
    if new_status in {"sent", "delivered"}:
        if request_row.status != new_status and request_row.status in {"created", "sent"}:
            request_row.status = new_status
            request_row.error = None
            changed = True
        return changed
    if await _signing.apply_status(
        db, connection, request_row, new_status,
        reason="The agreement expired." if status == "EXPIRED" else "The agreement was cancelled in Acrobat Sign.",
        download=lambda certificate: _download(connection, request_row, certificate),
        description=f"Signed with Adobe Acrobat Sign (agreement {request_row.envelope_id}).",
    ):
        changed = True
    return changed


async def void(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest, reason: str, user_id: uuid.UUID) -> None:
    if request_row.status not in _signing.OPEN or not request_row.envelope_id:
        raise IntegrationError(f"This request is already {request_row.status}.", 409)
    await _api(
        connection, "PUT", f"{_agreement_path(request_row)}/state",
        json={"state": "CANCELLED", "agreementCancellationInfo": {"comment": reason[:200], "notifyOthers": True}},
    )
    request_row.status = "voided"
    request_row.error = reason[:1000]
    request_row.last_checked_at = core.utcnow()
    _signing.log(db, request_row, "voided", f"Voided “{request_row.document_name}”: {reason}", level="warning", connection=connection, user_id=user_id)


async def resend(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest, user_id: uuid.UUID) -> None:
    if request_row.status not in {"sent", "delivered"} or not request_row.envelope_id:
        raise IntegrationError("Only requests waiting for signatures can be resent.", 409)
    members = await _members(connection, request_row)
    waiting = [
        r for r in request_row.recipients or []
        if r.get("role") == "signer" and r.get("status") not in {"signed", "completed", "declined"}
        and (members.get(r["email"].lower()) or {}).get("set_status", "").startswith("WAITING_FOR_MY")
    ]
    ids = [members[r["email"].lower()]["id"] for r in waiting if members.get(r["email"].lower(), {}).get("id")]
    if not ids:
        raise IntegrationError("Nobody is waiting to sign right now.", 409)
    await _api(
        connection, "POST", f"{_agreement_path(request_row)}/reminders",
        json={"recipientParticipantIds": ids, "status": "ACTIVE", "note": f"A reminder about “{request_row.document_name}”."[:255]},
    )
    _signing.log(db, request_row, "resent", f"Sent a reminder to {', '.join(r['name'] for r in waiting)}.", connection=connection, user_id=user_id)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


async def register_webhook(db: AsyncSession, connection: IntegrationConnection) -> str | None:
    """Ask Acrobat Sign to notify AIVA about this account's agreements. The connection
    must already be saved: Acrobat Sign checks the URL before it answers."""

    url = _push_url(connection)
    if not url:
        connection.config = {**(connection.config or {}), "webhook_id": None, "webhook_pending": False}
        return None
    await unregister_webhook(db, connection)
    body = {
        "name": "AIVA CRM",
        "scope": "ACCOUNT",
        "state": "ACTIVE",
        "webhookSubscriptionEvents": ["AGREEMENT_ALL"],
        "webhookUrlInfo": {"url": url},
    }
    try:
        created = await _api(connection, "POST", "/webhooks", json=body)
    except IntegrationError as exc:
        if "PERMISSION" not in str(exc).upper() and "refused" not in str(exc):
            raise
        # Not an account admin: notifications for this user's agreements only.
        created = await _api(connection, "POST", "/webhooks", json={**body, "scope": "USER"})
        body["scope"] = "USER"
    webhook_id = str(created.json().get("id") or "") or None
    connection.config = {**(connection.config or {}), "webhook_id": webhook_id, "webhook_scope": body["scope"], "webhook_pending": False}
    core.log(db, connection.organization_id, KEY, "webhook_registered",
             f"Acrobat Sign will notify AIVA about {'all of the account' if body['scope'] == 'ACCOUNT' else 'this user'}'s agreements.", level="success", connection=connection)
    return webhook_id


async def unregister_webhook(db: AsyncSession, connection: IntegrationConnection) -> None:
    webhook_id = (connection.config or {}).get("webhook_id")
    if not webhook_id:
        return
    try:
        await _api(connection, "DELETE", f"/webhooks/{urlquote(str(webhook_id), safe='')}", ok=(404,))
    except IntegrationError as exc:
        core.log(db, connection.organization_id, KEY, "webhook_cleanup", f"Couldn't remove the old Acrobat Sign webhook: {exc}", level="warning", connection=connection)
    connection.config = {**(connection.config or {}), "webhook_id": None}


def _echo(client_id: str | None, body: dict[str, Any] | None = None, status_code: int = 200) -> Response:
    headers = {"X-AdobeSign-ClientId": client_id} if client_id else {}
    payload = {**(body or {}), **({"xAdobeSignClientId": client_id} if client_id else {})}
    return JSONResponse(payload, status_code=status_code, headers=headers)


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    client_id = (request.headers.get("x-adobesign-clientid") or "").strip()[:200] or None
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method == "GET":
        return _echo(client_id, {"ok": True})  # Acrobat Sign checking the address
    body = await request.body()
    if len(body) > 2 * 1024 * 1024:
        return _echo(client_id, {"error": "too large"}, 413)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return _echo(client_id, {"ignored": "not json"})
    agreement_id = str(((data.get("agreement") or {}) if isinstance(data, dict) else {}).get("id") or "")[:100]
    if not agreement_id:
        return _echo(client_id, {"ignored": "no agreement"})
    org = connection.organization_id
    request_row = (
        await db.execute(
            select(SignatureRequest)
            .where(SignatureRequest.organization_id == org, SignatureRequest.provider == KEY, SignatureRequest.envelope_id == agreement_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if request_row is None:
        return _echo(client_id, {"ignored": "unknown agreement"})
    try:
        await refresh(db, connection, request_row)  # the notification is only a nudge
    except IntegrationError as exc:
        request_row.last_checked_at = None  # let polling retry soon
        core.log(db, org, KEY, "webhook_refresh_failed", f"Couldn't read agreement {agreement_id} back from Acrobat Sign: {exc}", level="warning", connection=connection)
    return _echo(client_id, {"ok": True, "status": request_row.status})


# ---------------------------------------------------------------------------
# Hub hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    integration_key = (values.get("integration_key") or "").strip()
    if len(integration_key) < 20:
        raise IntegrationError("That doesn't look like an Acrobat Sign integration key.", 422)
    if values.get("api_access_point"):
        points = {"api": check_access_point(values["api_access_point"]), "web": ""}
    else:
        points = await _discover(integration_key)
    response = await _sales_http.call(
        VENDOR, "GET", f"{points['api']}api/rest/v6/users/me", headers={"Authorization": f"Bearer {integration_key}", "Accept": "application/json"}
    )
    me = response.json()
    email = me.get("email")
    return {
        "label": f"{VENDOR} ({email})" if email else VENDOR,
        "external_id": me.get("accountId") or me.get("id"),
        "config": {"api_access_point": points["api"], "web_access_point": points["web"] or None, "email": email,
                   "account_id": me.get("accountId"), "webhook_pending": True},
    }


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if not _push_url(connection):
        connection.config = {**(connection.config or {}), "webhook_pending": False}
        core.log(
            db, connection.organization_id, KEY, "polling",
            "AIVA isn't on a public https address, so Acrobat Sign can't notify it; agreements are checked every 15 minutes instead.",
            connection=connection,
        )
        return
    # Acrobat Sign calls the address back before answering, so the connection must be saved first.
    await db.commit()
    try:
        await register_webhook(db, connection)
    except IntegrationError as exc:
        connection.config = {**(connection.config or {}), "webhook_pending": True}
        core.log(db, connection.organization_id, KEY, "webhook_failed", f"Couldn't register the Acrobat Sign webhook yet ({exc}); AIVA will retry and check agreements every 15 minutes meanwhile.",
                 level="warning", connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await unregister_webhook(db, connection)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    me = (await _api(connection, "GET", "/users/me")).json()
    return f"Acrobat Sign answered: signed in as {me.get('email') or 'the key owner'}."


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if personal:
        raise IntegrationError("Acrobat Sign is set up for the whole workspace.", 422)
    return _signing.clean_settings_values(config, VENDOR)


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    toggle = await core.toggle_row(db, org, KEY)
    config = (connection.config or {}) if connection else {}
    return {
        **await _signing.detail_summary(db, KEY, principal),
        "settings": settings_for(toggle.config if toggle else {}),
        "push": bool(_push_url(connection)),
        "webhook": {"registered": bool(config.get("webhook_id")), "scope": config.get("webhook_scope"), "pending": bool(config.get("webhook_pending"))},
        "account": {"email": config.get("email"), "api_access_point": config.get("api_access_point")} if principal.can("admin.settings") else {"email": config.get("email")},
        "text_tags": {"signature": _sign_tag(1), "date": _date_tag(1)},
    }


async def run_due(db: AsyncSession) -> int:
    handled = 0
    now = core.utcnow()
    # Webhooks that couldn't be registered at connect time.
    pending = (
        await db.execute(select(IntegrationConnection).where(IntegrationConnection.provider == KEY, IntegrationConnection.status == "connected"))
    ).scalars().all()
    for connection in pending[:20]:
        config = connection.config or {}
        if not config.get("webhook_pending") or not _push_url(connection):
            continue
        last = config.get("webhook_attempt_at")
        if last and now.isoformat() < last:
            continue
        if not await core.is_enabled(db, connection.organization_id, KEY):
            continue
        connection.config = {**config, "webhook_attempt_at": (now + timedelta(minutes=30)).isoformat()}
        await db.commit()
        try:
            await register_webhook(db, connection)
            handled += 1
        except IntegrationError as exc:
            core.log(db, connection.organization_id, KEY, "webhook_failed", f"Still couldn't register the Acrobat Sign webhook: {exc}", level="warning", connection=connection)
        await db.commit()

    connections: dict[uuid.UUID, IntegrationConnection | None] = {}
    for request_id in await _signing.due_requests(db, KEY, POLL_EVERY):
        request_row = (
            await db.execute(select(SignatureRequest).where(SignatureRequest.id == request_id).with_for_update(skip_locked=True))
        ).scalar_one_or_none()
        if request_row is None or request_row.status not in _signing.OPEN:
            await db.commit()
            continue
        org = request_row.organization_id
        if org not in connections:
            enabled = await core.is_enabled(db, org, KEY)
            connections[org] = await core.connection_for(db, org, KEY) if enabled else None
        connection = connections[org]
        if connection is None or connection.status != "connected" or not request_row.envelope_id:
            request_row.last_checked_at = now
            await db.commit()
            continue
        try:
            if await refresh(db, connection, request_row):
                handled += 1
        except IntegrationError as exc:
            request_row.last_checked_at = now
            message = str(exc)[:1000]
            if request_row.error != message:
                request_row.error = message
                _signing.log(db, request_row, "check_failed", f"Couldn't check “{request_row.document_name}”: {exc}", level="warning", connection=connection)
        await db.commit()
    return handled
