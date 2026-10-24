"""DocuSign eSignature: send a quote or an AIVA document for signature from a
record, follow it until it's signed, and save the signed copy back to AIVA.

* Sending: ``POST /restapi/v2.1/accounts/{account}/envelopes`` with one
  document (quotes are rendered to HTML, which DocuSign converts to PDF),
  signers and copies with routing order, and anchor-text signature fields
  (``/sn1/`` and ``/ds1/`` in a document place signer 1's signature and date;
  without them signers place their own).
* Status: DocuSign Connect pushes changes to the integration's webhook URL
  when AIVA runs on a public https address; the webhook is only a nudge and
  AIVA always reads the envelope back from the API before changing anything.
  With a Connect HMAC key saved, unsigned or wrongly signed pushes are refused
  (``X-DocuSign-Signature-1`` = base64 HMAC-SHA256 of the raw body).
  Envelopes are also polled every 15 minutes, so nothing is missed.
* Completion: the combined signed PDF (with the certificate of completion) is
  saved to Documents and linked to the record, the quote can be marked
  accepted, and the sender is notified.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _doc_store, _signing
from app.integrations.providers._signing import DEFAULTS, OPEN, clean_recipients, quote_html, requests_out, settings_for, timelines  # noqa: F401 - re-exported
from app.models.collaboration import Document
from app.models.integration_hub import IntegrationConnection, SignatureRequest

KEY = "docusign"
SUPPORTS = {"quotes": True, "documents": True, "templates": False, "void": True, "remind": True}
POLL_EVERY = timedelta(minutes=15)
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "rtf", "txt", "htm", "html", "xls", "xlsx", "csv", "ppt", "pptx", "odt", "ods", "odp",
    "png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff",
}
ENVELOPE_STATUS = {
    "created": "created",
    "sent": "sent",
    "correct": "sent",
    "delivered": "delivered",
    "signed": "delivered",
    "completed": "completed",
    "authoritativecopy": "completed",
    "transfercompleted": "completed",
    "declined": "declined",
    "voided": "voided",
    "deleted": "voided",
    "timedout": "voided",
}
# ---------------------------------------------------------------------------
# Account address
# ---------------------------------------------------------------------------


def account_url(connection: IntegrationConnection) -> str:
    config = connection.config or {}
    base = str(config.get("base_uri") or "").rstrip("/")
    account = str(config.get("account_id") or "")
    host = urlparse(base).hostname or ""
    if urlparse(base).scheme != "https" or not (host.endswith(".docusign.net") or host.endswith(".docusign.com")) or not account:
        raise IntegrationError("DocuSign didn't give AIVA a valid account address. Reconnect DocuSign.", 409)
    return f"{base}/restapi/v2.1/accounts/{quote(account, safe='')}"


def _push_url(connection: IntegrationConnection) -> str | None:
    """Connect needs a public https address; on a local or http server AIVA polls instead."""

    url = core.webhook_url(KEY, connection.webhook_token)
    return url if url and url.startswith("https://") else None


# ---------------------------------------------------------------------------
# What gets sent
# ---------------------------------------------------------------------------


def _anchor_tabs(index: int) -> dict[str, Any]:
    def tab(anchor: str, offset_y: str) -> dict[str, str]:
        return {
            "anchorString": anchor,
            "anchorUnits": "pixels",
            "anchorXOffset": "0",
            "anchorYOffset": offset_y,
            "anchorIgnoreIfNotPresent": "true",
            "anchorCaseSensitive": "false",
        }

    return {
        "signHereTabs": [tab(f"/aivasign{index}/", "-8"), tab(f"/sn{index}/", "-8")],
        "dateSignedTabs": [tab(f"/aivadate{index}/", "-4"), tab(f"/ds{index}/", "-4")],
    }


def envelope_body(
    *,
    request_row: SignatureRequest,
    document_bytes: bytes,
    document_name: str,
    extension: str,
    recipients: list[dict[str, Any]],
    push_url: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    signers = []
    copies = []
    signer_index = 0
    for recipient in recipients:
        entry = {"email": recipient["email"], "name": recipient["name"], "recipientId": recipient["recipient_id"], "routingOrder": str(recipient["routing_order"])}
        if recipient["role"] == "signer":
            signer_index += 1
            entry["tabs"] = _anchor_tabs(signer_index)
            signers.append(entry)
        else:
            copies.append(entry)
    body: dict[str, Any] = {
        "emailSubject": request_row.subject[:100],
        "status": "sent",
        "documents": [{"documentBase64": base64.b64encode(document_bytes).decode(), "name": document_name[:100], "fileExtension": extension, "documentId": "1"}],
        "recipients": {"signers": signers, "carbonCopies": copies},
        "customFields": {
            "textCustomFields": [
                {"name": "aiva_request_id", "value": str(request_row.id), "show": "false", "required": "false"},
                {"name": "aiva_workspace", "value": str(request_row.organization_id), "show": "false", "required": "false"},
            ]
        },
    }
    if request_row.message:
        body["emailBlurb"] = request_row.message[:10000]
    reminder_days = int(options.get("reminder_days") or 0)
    expire_days = int(options.get("expire_days") or 0)
    if reminder_days or expire_days:
        notification: dict[str, Any] = {"useAccountDefaults": "false"}
        if reminder_days:
            notification["reminders"] = {"reminderEnabled": "true", "reminderDelay": str(reminder_days), "reminderFrequency": str(reminder_days)}
        if expire_days:
            notification["expirations"] = {"expireEnabled": "true", "expireAfter": str(expire_days), "expireWarn": str(min(5, max(1, expire_days - 1)))}
        body["notification"] = notification
    if push_url:
        body["eventNotification"] = {
            "url": push_url,
            "loggingEnabled": "true",
            "requireAcknowledgment": "true",
            "deliveryMode": "SIM",
            "includeHMAC": "true",
            "events": [
                "envelope-sent", "envelope-delivered", "envelope-completed", "envelope-declined", "envelope-voided",
                "recipient-delivered", "recipient-completed", "recipient-declined",
            ],
            "eventData": {"version": "restv2.1", "format": "json", "includeData": ["recipients"]},
        }
    return body


# ---------------------------------------------------------------------------
# Logging helpers (the activity log doubles as each request's timeline)
# ---------------------------------------------------------------------------


def _log(db: AsyncSession, request_row: SignatureRequest, action: str, message: str, *, level: str = "info", connection: IntegrationConnection | None = None, user_id: uuid.UUID | None = None, **detail: Any) -> None:
    _signing.log(db, request_row, action, message, level=level, connection=connection, user_id=user_id, **detail)


# ---------------------------------------------------------------------------
# Status from DocuSign
# ---------------------------------------------------------------------------


def _when(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


async def refresh(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest) -> bool:
    """Reads the envelope back from DocuSign and applies any change. Returns True if something changed."""

    if not request_row.envelope_id:
        return False
    url = f"{account_url(connection)}/envelopes/{quote(request_row.envelope_id, safe='')}"
    data = (await core.request(db, connection, "GET", url, params={"include": "recipients"})).json()
    request_row.last_checked_at = core.utcnow()
    changed = False
    recipients = [dict(r) for r in request_row.recipients or []]
    remote = data.get("recipients") or {}
    by_id = {str(r.get("recipientId")): r for r in (remote.get("signers") or []) + (remote.get("carbonCopies") or [])}
    for recipient in recipients:
        found = by_id.get(str(recipient.get("recipient_id")))
        if not found:
            continue
        status = str(found.get("status") or "").lower() or recipient.get("status")
        if status == recipient.get("status"):
            continue
        changed = True
        recipient["status"] = status
        if status == "delivered":
            recipient["delivered_at"] = _when(found.get("deliveredDateTime")) or core.utcnow().isoformat()
            _log(db, request_row, "recipient_viewed", f"{recipient['name']} opened “{request_row.document_name}”.", connection=connection)
        elif status in {"signed", "completed"} and recipient.get("role") == "signer":
            recipient["delivered_at"] = recipient.get("delivered_at") or _when(found.get("deliveredDateTime"))
            recipient["signed_at"] = _when(found.get("signedDateTime")) or core.utcnow().isoformat()
            _log(db, request_row, "recipient_signed", f"{recipient['name']} signed “{request_row.document_name}”.", level="success", connection=connection)
        elif status == "declined":
            recipient["declined_at"] = _when(found.get("declinedDateTime")) or core.utcnow().isoformat()
            recipient["decline_reason"] = (found.get("declinedReason") or "")[:500] or None
            reason = f": {recipient['decline_reason']}" if recipient["decline_reason"] else "."
            _log(db, request_row, "recipient_declined", f"{recipient['name']} declined to sign{reason}", level="warning", connection=connection)
    request_row.recipients = recipients

    new_status = ENVELOPE_STATUS.get(str(data.get("status") or "").lower(), request_row.status)
    if new_status == "delivered" and request_row.status in {"created", "sent"}:
        request_row.status = "delivered"  # the recipient line above already says who opened it
        request_row.error = None
        return True
    if await _signing.apply_status(
        db, connection, request_row, new_status,
        completed_at=_when(data.get("completedDateTime")),
        reason=data.get("voidedReason") or "The envelope was voided or expired.",
        download=lambda certificate: _download(db, connection, request_row, certificate),
        description=f"Signed with DocuSign (envelope {request_row.envelope_id}).",
    ):
        changed = True
    return changed


async def _download(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest, certificate: bool) -> bytes:
    url = f"{account_url(connection)}/envelopes/{quote(request_row.envelope_id or '', safe='')}/documents/combined"
    response = await core.request(db, connection, "GET", url, params={"certificate": "true" if certificate else "false"}, timeout=90)
    return response.content


# ---------------------------------------------------------------------------
# Sending, voiding, resending
# ---------------------------------------------------------------------------


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
) -> SignatureRequest:
    org = principal.organization_id
    cleaned = clean_recipients(recipients)
    signers = [r for r in cleaned if r["role"] == "signer"]
    if quote_row is not None:
        if not quote_row.line_items:
            raise IntegrationError("Add at least one line item to the quote before sending it for signature.", 409)
        data = await quote_html(db, quote_row, signers)
        name = f"Quote Q-{quote_row.number} - {quote_row.name}"[:180]
        extension = "html"
        document_name = f"Quote Q-{quote_row.number} · {quote_row.name}"[:300]
    elif document is not None:
        stored = await _doc_store.latest_file(db, document)
        if stored is None:
            raise IntegrationError("That document has no file to send.", 409)
        extension = stored.file_name.rsplit(".", 1)[-1].lower() if "." in stored.file_name else ""
        if extension not in ALLOWED_EXTENSIONS:
            raise IntegrationError("DocuSign accepts PDF, Word, Excel, PowerPoint, text, HTML and image files.", 415)
        if stored.size_bytes > MAX_DOCUMENT_BYTES:
            raise IntegrationError("DocuSign accepts documents up to 25 MB.", 413)
        data = stored.data
        name = stored.file_name
        document_name = document.name[:300]
    else:
        raise IntegrationError("Choose a quote or a document to send.", 422)

    request_row = _signing.new_request(
        db, principal, provider=KEY, record_type=record_type, record_id=record_id, subject=subject, message=message,
        recipients=cleaned, document_name=document_name, quote_row=quote_row, document=document,
    )
    await db.flush()
    options = settings_for(await core.workspace_config(db, org, KEY))
    body = envelope_body(
        request_row=request_row, document_bytes=data, document_name=name, extension=extension, recipients=cleaned,
        push_url=_push_url(connection), options=options,
    )
    try:
        response = await core.request(db, connection, "POST", f"{account_url(connection)}/envelopes", json=body, timeout=60)
    except IntegrationError as exc:
        request_row.status = "error"
        request_row.error = str(exc)[:1000]
        _log(db, request_row, "send_failed", f"Couldn't send “{document_name}”: {exc}", level="error", connection=connection, user_id=principal.user_id)
        raise
    result = response.json()
    request_row.envelope_id = str(result.get("envelopeId") or "")[:100] or None
    _signing.mark_sent(
        db, request_row, connection=connection, principal=principal, quote_row=quote_row,
        status=ENVELOPE_STATUS.get(str(result.get("status") or "sent").lower(), "sent"),
    )
    return request_row


async def void(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest, reason: str, user_id: uuid.UUID) -> None:
    if request_row.status not in OPEN or not request_row.envelope_id:
        raise IntegrationError(f"This request is already {request_row.status}.", 409)
    url = f"{account_url(connection)}/envelopes/{quote(request_row.envelope_id, safe='')}"
    await core.request(db, connection, "PUT", url, json={"status": "voided", "voidedReason": reason[:200]})
    request_row.status = "voided"
    request_row.error = reason[:1000]
    request_row.last_checked_at = core.utcnow()
    _log(db, request_row, "voided", f"Voided “{request_row.document_name}”: {reason}", level="warning", connection=connection, user_id=user_id)


async def resend(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest, user_id: uuid.UUID) -> None:
    if request_row.status not in {"sent", "delivered"} or not request_row.envelope_id:
        raise IntegrationError("Only requests waiting for signatures can be resent.", 409)
    url = f"{account_url(connection)}/envelopes/{quote(request_row.envelope_id, safe='')}"
    await core.request(db, connection, "PUT", url, params={"resend_envelope": "true"}, json={})
    waiting = [r["name"] for r in request_row.recipients or [] if r.get("role") == "signer" and r.get("status") not in {"signed", "completed", "declined"}]
    _log(db, request_row, "resent", f"Sent a reminder to {', '.join(waiting) or 'the recipients'}.", connection=connection, user_id=user_id)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    account_url(connection)  # refuse odd account addresses straight away
    if not connection.webhook_token:
        connection.webhook_token = core.new_webhook_token()
    if not _push_url(connection):
        core.log(
            db, connection.organization_id, KEY, "polling",
            "AIVA isn't on a public https address, so DocuSign can't push updates; it will check envelopes every 15 minutes instead.",
            connection=connection,
        )


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    data = (await core.request(db, connection, "GET", account_url(connection))).json()
    environment = (connection.config or {}).get("environment") or "demo"
    where = "production" if environment == "production" else "developer sandbox"
    return f"DocuSign answered: account “{data.get('accountName') or connection.account_label}” ({where})."


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    toggle = await core.toggle_row(db, org, KEY)
    push = _push_url(connection) if connection is not None else None
    summary = await _signing.detail_summary(db, KEY, principal)
    return {
        **summary,
        "settings": settings_for(toggle.config if toggle else {}),
        "connect": {
            "push": bool(push),
            "webhook_url": push,
            "hmac_configured": bool(toggle and core.secret(toggle, "connect_hmac_key")),
        },
        "account": {
            "id": (connection.config or {}).get("account_id") if connection else None,
            "environment": (connection.config or {}).get("environment") if connection else None,
            "email": (connection.config or {}).get("email") if connection else None,
        },
    }


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if personal:
        raise IntegrationError("DocuSign is set up for the whole workspace.", 422)
    return _signing.clean_settings_values(config, "DocuSign")


def _envelope_id(body: bytes) -> str | None:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        match = re.search(rb"<EnvelopeID>([0-9a-fA-F-]{36})</EnvelopeID>", body or b"")
        return match.group(1).decode() if match else None
    if not isinstance(data, dict):
        return None
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    value = inner.get("envelopeId") or data.get("envelopeId") or (inner.get("envelopeSummary") or {}).get("envelopeId")
    return str(value) if value else None


def verify_hmac(body: bytes, key: str, headers: Any) -> bool:
    expected = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()
    signatures = [v for k, v in headers.items() if k.lower().startswith("x-docusign-signature-")]
    return any(hmac.compare_digest(expected.encode(), s.strip().encode()) for s in signatures)


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method == "GET":
        return JSONResponse({"ok": True})
    body = await request.body()
    if len(body) > 5 * 1024 * 1024:
        return JSONResponse({"error": "too large"}, status_code=413)
    org = connection.organization_id
    toggle = await core.toggle_row(db, org, KEY)
    hmac_key = core.secret(toggle, "connect_hmac_key") if toggle else None
    if hmac_key and not verify_hmac(body, hmac_key, request.headers):
        core.log(db, org, KEY, "webhook_rejected", "A DocuSign Connect message with a missing or wrong HMAC signature was refused.", level="warning", connection=connection)
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    envelope_id = _envelope_id(body)
    if not envelope_id:
        return JSONResponse({"ignored": "no envelope"})
    request_row = (
        await db.execute(
            select(SignatureRequest)
            .where(SignatureRequest.organization_id == org, SignatureRequest.provider == KEY, SignatureRequest.envelope_id == envelope_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if request_row is None:
        return JSONResponse({"ignored": "unknown envelope"})
    try:
        await refresh(db, connection, request_row)
    except IntegrationError as exc:
        request_row.last_checked_at = None  # let polling retry soon
        core.log(db, org, KEY, "webhook_refresh_failed", f"Couldn't read envelope {envelope_id} back from DocuSign: {exc}", level="warning", connection=connection)
        return JSONResponse({"error": "retry later"}, status_code=503 if exc.status_code >= 500 else 200)
    return JSONResponse({"ok": True, "status": request_row.status})


async def run_due(db: AsyncSession) -> int:
    now = core.utcnow()
    ids = await _signing.due_requests(db, KEY, POLL_EVERY)
    handled = 0
    connections: dict[uuid.UUID, IntegrationConnection | None] = {}
    for request_id in ids:
        # Lock one row at a time so a DocuSign push for the same envelope can't
        # apply the same change twice (and save the signed copy twice).
        request_row = (
            await db.execute(select(SignatureRequest).where(SignatureRequest.id == request_id).with_for_update(skip_locked=True))
        ).scalar_one_or_none()
        if request_row is None or request_row.status not in OPEN:
            await db.commit()
            continue
        org = request_row.organization_id
        if org not in connections:
            enabled = await core.is_enabled(db, org, KEY)
            connections[org] = await core.connection_for(db, org, KEY) if enabled else None
        connection = connections[org]
        if connection is None or connection.status == "needs_reauth":
            request_row.last_checked_at = now
            continue
        try:
            if await refresh(db, connection, request_row):
                handled += 1
        except IntegrationError as exc:
            request_row.last_checked_at = now
            message = str(exc)[:1000]
            if request_row.error != message:
                request_row.error = message
                _log(db, request_row, "check_failed", f"Couldn't check “{request_row.document_name}”: {exc}", level="warning", connection=connection)
        await db.commit()
    await db.commit()
    return handled


def connect_settings_url() -> str:
    environment = get_settings().docusign_environment
    return "https://admin.docusign.com/connect" if environment == "production" else "https://admindemo.docusign.com/connect"


def signable(name: str, content_type: str | None, size: int) -> bool:
    """Whether DocuSign can take this AIVA document."""

    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return size <= MAX_DOCUMENT_BYTES and (extension in ALLOWED_EXTENSIONS or content_type == "application/pdf")
