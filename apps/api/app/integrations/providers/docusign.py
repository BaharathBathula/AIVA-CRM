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
import html
import json
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _doc_store
from app.models.collaboration import Document
from app.models.integration_hub import IntegrationConnection, IntegrationLog, SignatureRequest

KEY = "docusign"
OPEN = ("created", "sent", "delivered")
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
DEFAULTS: dict[str, Any] = {
    "save_signed_copy": True,
    "include_certificate": True,
    "mark_quote_accepted": True,
    "notify_sender": True,
    "reminder_days": 0,
    "expire_days": 0,
}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def settings_for(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    for name in DEFAULTS:
        if config and name in config:
            merged[name] = config[name]
    return merged


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


def _money(value: Any, currency: str) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "AUD": "A$", "CAD": "C$"}.get(currency.upper())
    text = f"{amount:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency.upper()}"


async def quote_html(db: AsyncSession, quote_row: Any, signers: list[dict[str, Any]]) -> bytes:
    """A clean, printable quote with a signature block per signer (anchor text in white)."""

    from app.models.account import Account
    from app.models.contact import Contact
    from app.models.organization import Organization

    organization = await db.get(Organization, quote_row.organization_id)
    account = await db.get(Account, quote_row.account_id) if quote_row.account_id else None
    contact = await db.get(Contact, quote_row.contact_id) if quote_row.contact_id else None
    e = lambda value: html.escape(str(value or ""))  # noqa: E731
    currency = quote_row.currency or "USD"
    rows = []
    for line in quote_row.line_items or []:
        discount = f"{Decimal(str(line.discount_percent or 0)).normalize()}%" if line.discount_percent else "—"
        rows.append(
            "<tr>"
            f"<td><strong>{e(line.name)}</strong>{'<br><span class=muted>' + e(line.description) + '</span>' if line.description else ''}</td>"
            f"<td class=num>{Decimal(str(line.quantity)).normalize():f}</td>"
            f"<td class=num>{_money(line.unit_price, currency)}</td>"
            f"<td class=num>{discount}</td>"
            f"<td class=num>{_money(line.line_total, currency)}</td>"
            "</tr>"
        )
    prepared_for = " · ".join(
        x for x in [e(account.name) if account else "", e(f"{contact.first_name} {contact.last_name}".strip()) if contact else "", e(contact.email) if contact and contact.email else ""] if x
    )
    blocks = []
    for index, signer in enumerate(signers, start=1):
        blocks.append(
            "<div class=sig>"
            f"<div class=line><span class=anchor>/aivasign{index}/</span></div>"
            f"<div class=label>Signature — {e(signer['name'])}</div>"
            f"<div class=line short><span class=anchor>/aivadate{index}/</span></div>"
            "<div class=label>Date</div>"
            "</div>"
        )
    issued = (quote_row.sent_at or quote_row.created_at or core.utcnow()).strftime("%d %b %Y")
    valid = quote_row.valid_until.strftime("%d %b %Y") if quote_row.valid_until else "—"
    document = f"""<!doctype html><html><head><meta charset="utf-8"><title>Quote Q-{quote_row.number}</title>
<style>
body{{font-family:Helvetica,Arial,sans-serif;color:#1d1b22;font-size:12px;margin:36px}}
h1{{font-size:22px;margin:0 0 4px}} .muted{{color:#6b6873}} .head{{display:flex;justify-content:space-between;border-bottom:2px solid #1d1b22;padding-bottom:12px;margin-bottom:18px}}
table{{width:100%;border-collapse:collapse;margin:14px 0}} th{{text-align:left;font-size:11px;text-transform:uppercase;color:#6b6873;border-bottom:1px solid #ccc;padding:6px}}
td{{padding:8px 6px;border-bottom:1px solid #eee;vertical-align:top}} .num{{text-align:right;white-space:nowrap}}
.totals td{{border:none;padding:3px 6px}} .totals .grand td{{font-size:14px;font-weight:bold;border-top:2px solid #1d1b22;padding-top:6px}}
.box{{margin-top:16px}} .box h3{{font-size:12px;text-transform:uppercase;color:#6b6873;margin:0 0 4px}}
.sigs{{margin-top:36px}} .sig{{display:inline-block;width:46%;margin:0 3% 24px 0;vertical-align:top}}
.line{{border-bottom:1px solid #1d1b22;height:34px;position:relative}} .line.short{{width:55%;margin-top:14px}}
.label{{font-size:10.5px;color:#6b6873;margin-top:3px}} .anchor{{color:#ffffff;font-size:8px}}
</style></head><body>
<div class=head><div><h1>Quote Q-{quote_row.number}</h1><div>{e(quote_row.name)}</div></div>
<div style="text-align:right"><strong>{e(organization.name if organization else '')}</strong><br><span class=muted>Issued {issued} · Valid until {valid}</span></div></div>
{f'<div><span class=muted>Prepared for</span><br><strong>{prepared_for}</strong></div>' if prepared_for else ''}
<table><thead><tr><th>Item</th><th class=num>Qty</th><th class=num>Unit price</th><th class=num>Discount</th><th class=num>Amount</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<table class=totals style="width:45%;margin-left:55%">
<tr><td>Subtotal</td><td class=num>{_money(quote_row.subtotal, currency)}</td></tr>
<tr><td>Discount</td><td class=num>−{_money(quote_row.discount_total, currency)}</td></tr>
<tr><td>Tax</td><td class=num>{_money(quote_row.tax_total, currency)}</td></tr>
<tr class=grand><td>Total</td><td class=num>{_money(quote_row.total, currency)}</td></tr></table>
{f'<div class=box><h3>Terms</h3><div>{e(quote_row.terms).replace(chr(10), "<br>")}</div></div>' if quote_row.terms else ''}
{f'<div class=box><h3>Notes</h3><div>{e(quote_row.notes).replace(chr(10), "<br>")}</div></div>' if quote_row.notes else ''}
<div class=sigs><div class=box><h3>Acceptance</h3><div class=muted>By signing, you accept this quote and its terms.</div></div>{''.join(blocks)}</div>
</body></html>"""
    return document.encode("utf-8")


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


def clean_recipients(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in items:
        role = raw.get("role") or "signer"
        if role not in {"signer", "cc"}:
            raise IntegrationError("Each recipient must sign or get a copy.", 422)
        name = str(raw.get("name") or "").strip()[:100]
        email = str(raw.get("email") or "").strip().lower()[:320]
        if not name:
            raise IntegrationError("Add a name for each recipient.", 422)
        if not EMAIL.match(email):
            raise IntegrationError(f"“{email or name}” isn't a valid email address.", 422)
        try:
            order = int(raw.get("routing_order") or 1)
        except (TypeError, ValueError) as exc:
            raise IntegrationError("Signing order must be a number.", 422) from exc
        if not 1 <= order <= 20:
            raise IntegrationError("Signing order must be between 1 and 20.", 422)
        if (role, email) in seen:
            continue
        seen.add((role, email))
        out.append({"name": name, "email": email, "role": role, "routing_order": order})
    signers = [r for r in out if r["role"] == "signer"]
    if not signers:
        raise IntegrationError("Add at least one person to sign.", 422)
    if len(signers) > 10 or len(out) - len(signers) > 10:
        raise IntegrationError("Up to 10 signers and 10 people copied, please.", 422)
    for index, recipient in enumerate(out, start=1):
        recipient["recipient_id"] = str(index)
        recipient.update({"status": "created", "delivered_at": None, "signed_at": None, "declined_at": None, "decline_reason": None})
    return out


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
    core.log(
        db, request_row.organization_id, KEY, action, message, level=level, connection=connection, user_id=user_id,
        detail={"signature_request_id": str(request_row.id), "envelope_id": request_row.envelope_id, **detail},
    )


async def timelines(db: AsyncSession, organization_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[str, list[dict[str, Any]]]:
    if not ids:
        return {}
    keys = [str(i) for i in ids]
    rows = (
        await db.execute(
            select(IntegrationLog)
            .where(
                IntegrationLog.organization_id == organization_id,
                IntegrationLog.provider == KEY,
                IntegrationLog.detail["signature_request_id"].astext.in_(keys),
            )
            .order_by(IntegrationLog.created_at)
        )
    ).scalars().all()
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in keys}
    for row in rows:
        out.setdefault(row.detail.get("signature_request_id"), []).append(
            {"action": row.action, "message": row.message, "level": row.level, "at": row.created_at, "signed_document_id": (row.detail or {}).get("signed_document_id")}
        )
    return out


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
    previous = request_row.status
    if new_status != previous:
        changed = True
        request_row.status = new_status
        request_row.error = None
        if new_status == "completed":
            completed = _when(data.get("completedDateTime"))
            request_row.completed_at = datetime.fromisoformat(completed) if completed else core.utcnow()
            await _completed(db, connection, request_row)
        elif new_status == "declined":
            _log(db, request_row, "declined", f"“{request_row.document_name}” was declined.", level="warning", connection=connection)
            await _notify(db, request_row, f"Declined: {request_row.document_name}", "A recipient declined to sign.")
        elif new_status == "voided":
            reason = data.get("voidedReason") or "The envelope was voided or expired."
            request_row.error = str(reason)[:1000]
            _log(db, request_row, "voided", f"“{request_row.document_name}” was voided: {reason}", level="warning", connection=connection)
            await _notify(db, request_row, f"Voided: {request_row.document_name}", str(reason))
        elif new_status == "delivered" and previous in {"created", "sent"}:
            pass  # the recipient line above already says who opened it
    return changed


async def _notify(db: AsyncSession, request_row: SignatureRequest, title: str, body: str) -> None:
    config = settings_for(await core.workspace_config(db, request_row.organization_id, KEY))
    if not config["notify_sender"] or not request_row.sent_by_user_id:
        return
    from app.models.platform import Notification

    link = None
    if request_row.record_type and request_row.record_id:
        from fastapi import HTTPException

        from app.services.records import resolve

        try:
            link = (await resolve(db, request_row.organization_id, request_row.record_type, request_row.record_id))["href"]
        except HTTPException:
            link = None
    db.add(
        Notification(
            id=uuid.uuid4(), organization_id=request_row.organization_id, user_id=request_row.sent_by_user_id, kind="signature",
            title=title[:200], body=body[:2000], link=(link or "/integrations/docusign")[:300],
        )
    )


async def _completed(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest) -> None:
    org = request_row.organization_id
    config = settings_for(await core.workspace_config(db, org, KEY))
    signed_document_id = None
    if config["save_signed_copy"]:
        url = f"{account_url(connection)}/envelopes/{quote(request_row.envelope_id or '', safe='')}/documents/combined"
        try:
            response = await core.request(db, connection, "GET", url, params={"certificate": "true" if config["include_certificate"] else "false"}, timeout=90)
            links: list[tuple[str, uuid.UUID]] = []
            if request_row.record_type and request_row.record_id:
                links.append((request_row.record_type, request_row.record_id))
            if request_row.quote_id:
                links.append(("quote", request_row.quote_id))
                from app.models.platform import Quote

                quote_row = await db.get(Quote, request_row.quote_id)
                if quote_row is not None and quote_row.opportunity_id:
                    links.append(("opportunity", quote_row.opportunity_id))
            base_name = re.sub(r"\.(html?|pdf|docx?|xlsx?|pptx?|txt|rtf)$", "", request_row.document_name or "Document", flags=re.I)
            document = await _doc_store.save_document(
                db,
                organization_id=org,
                owner_user_id=request_row.sent_by_user_id,
                name=f"{base_name} (signed).pdf",
                data=response.content,
                content_type="application/pdf",
                links=links,
                description=f"Signed with DocuSign (envelope {request_row.envelope_id}).",
                tags=["docusign", "signed"],
            )
            signed_document_id = str(document.id)
            request_row.signed_document_id = document.id
        except IntegrationError as exc:
            _log(db, request_row, "signed_copy_failed", f"Signed, but the signed copy couldn't be saved: {exc}", level="warning", connection=connection)
    _log(
        db, request_row, "completed", f"Everyone signed “{request_row.document_name}”." + (" The signed copy is in Documents." if signed_document_id else ""),
        level="success", connection=connection, signed_document_id=signed_document_id,
    )
    if config["mark_quote_accepted"] and request_row.quote_id:
        await _accept_quote(db, request_row)
    await _notify(db, request_row, f"Signed: {request_row.document_name}", "Everyone has signed." + (" The signed copy is in Documents." if signed_document_id else ""))


async def _accept_quote(db: AsyncSession, request_row: SignatureRequest) -> None:
    from app.models.platform import Quote

    quote_row = await db.get(Quote, request_row.quote_id)
    if quote_row is None or quote_row.status not in {"draft", "sent"}:
        return
    from app.services.billing import get_subscription, plan_state
    from app.services.metering import addon_features

    features = plan_state(await get_subscription(db, request_row.organization_id)).features | await addon_features(db, request_row.organization_id)
    if "inventory" in features:
        from app.api.routes.operations import apply_quote_sale

        await apply_quote_sale(db, quote_row, request_row.sent_by_user_id)
    quote_row.status = "accepted"
    quote_row.accepted_at = core.utcnow()
    _log(db, request_row, "quote_accepted", f"Quote Q-{quote_row.number} was marked accepted.", level="success")


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

    request_row = SignatureRequest(
        id=uuid.uuid4(),
        organization_id=org,
        provider=KEY,
        subject=(subject or f"Please sign: {document_name}").strip()[:300],
        message=(message or "").strip()[:10000] or None,
        status="created",
        record_type=record_type,
        record_id=record_id,
        document_id=document.id if document is not None else None,
        quote_id=quote_row.id if quote_row is not None else None,
        document_name=document_name,
        recipients=cleaned,
        sent_by_user_id=principal.user_id,
    )
    db.add(request_row)
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
    request_row.status = ENVELOPE_STATUS.get(str(result.get("status") or "sent").lower(), "sent")
    request_row.sent_at = core.utcnow()
    request_row.last_checked_at = core.utcnow()
    first_order = min(r["routing_order"] for r in cleaned)
    request_row.recipients = [{**r, "status": "sent" if r["routing_order"] == first_order else "created"} for r in cleaned]
    who = ", ".join(r["name"] for r in signers)
    _log(db, request_row, "sent", f"Sent “{document_name}” to {who} for signature.", level="success", connection=connection, user_id=principal.user_id)
    if quote_row is not None and quote_row.status == "draft":
        quote_row.status = "sent"
        quote_row.sent_at = core.utcnow()
    from app.services.automation import record_audit

    record_audit(
        db, organization_id=org, action="signature_requested", entity_type=record_type or "document", entity_id=record_id,
        entity_label=document_name, changes={"envelope_id": request_row.envelope_id, "signers": [r["email"] for r in signers]},
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


def request_out(row: SignatureRequest, names: dict[str, str], records: dict[tuple[str, str], dict[str, Any]], timeline: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    signed_document_id = str(row.signed_document_id) if row.signed_document_id else next(
        (e.get("signed_document_id") for e in reversed(timeline or []) if e.get("signed_document_id")), None
    )
    record = records.get((row.record_type, str(row.record_id))) if row.record_type and row.record_id else None
    recipients = row.recipients or []
    signers = [r for r in recipients if r.get("role") == "signer"]
    return {
        "id": str(row.id),
        "provider": row.provider,
        "envelope_id": row.envelope_id,
        "subject": row.subject,
        "message": row.message,
        "status": row.status,
        "document_name": row.document_name,
        "document_id": str(row.document_id) if row.document_id else None,
        "quote_id": str(row.quote_id) if row.quote_id else None,
        "record_type": row.record_type,
        "record_id": str(row.record_id) if row.record_id else None,
        "record": record,
        "recipients": recipients,
        "signed_count": sum(1 for r in signers if r.get("status") in {"signed", "completed"}),
        "signer_count": len(signers),
        "sent_by": names.get(str(row.sent_by_user_id)) if row.sent_by_user_id else None,
        "sent_by_user_id": str(row.sent_by_user_id) if row.sent_by_user_id else None,
        "sent_at": row.sent_at,
        "completed_at": row.completed_at,
        "last_checked_at": row.last_checked_at,
        "error": row.error,
        "signed_document_id": signed_document_id,
        "timeline": timeline or [],
        "created_at": row.created_at,
    }


async def requests_out(db: AsyncSession, organization_id: uuid.UUID, rows: list[SignatureRequest], with_timeline: bool = True) -> list[dict[str, Any]]:
    from app.services.lookups import names_for
    from app.services.records import resolve_many

    names = await names_for(db, organization_id, "user", [r.sent_by_user_id for r in rows])
    records = await resolve_many(db, organization_id, [(r.record_type, r.record_id) for r in rows if r.record_type and r.record_id])
    lines = await timelines(db, organization_id, [r.id for r in rows]) if with_timeline else {}
    return [request_out(r, names, records, lines.get(str(r.id))) for r in rows]


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    counts = dict(
        (
            await db.execute(select(SignatureRequest.status, func.count()).where(SignatureRequest.organization_id == org).group_by(SignatureRequest.status))
        ).all()
    )
    rows = (
        await db.execute(select(SignatureRequest).where(SignatureRequest.organization_id == org).order_by(SignatureRequest.created_at.desc()).limit(8))
    ).scalars().all()
    toggle = await core.toggle_row(db, org, KEY)
    push = _push_url(connection) if connection is not None else None
    return {
        "counts": {status: int(counts.get(status, 0)) for status in ("sent", "delivered", "completed", "declined", "voided", "error", "created")},
        "recent": await requests_out(db, org, list(rows), with_timeline=True),
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
    cleaned: dict[str, Any] = {}
    for name, value in config.items():
        if name in {"save_signed_copy", "include_certificate", "mark_quote_accepted", "notify_sender"}:
            if not isinstance(value, bool):
                raise IntegrationError(f"“{name.replace('_', ' ')}” must be on or off.", 422)
            cleaned[name] = value
        elif name in {"reminder_days", "expire_days"}:
            limit = 30 if name == "reminder_days" else 365
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
                raise IntegrationError(f"“{name.replace('_', ' ')}” must be a whole number from 0 to {limit}.", 422)
            cleaned[name] = value
        else:
            raise IntegrationError(f"Unknown setting “{name}”.", 422)
    return cleaned


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
            select(SignatureRequest).where(SignatureRequest.organization_id == org, SignatureRequest.envelope_id == envelope_id).with_for_update()
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
    ids = (
        await db.execute(
            select(SignatureRequest.id)
            .where(
                SignatureRequest.status.in_(OPEN),
                SignatureRequest.envelope_id.is_not(None),
                (SignatureRequest.last_checked_at.is_(None)) | (SignatureRequest.last_checked_at < now - POLL_EVERY),
            )
            .order_by(SignatureRequest.last_checked_at.asc().nulls_first())
            .limit(50)
        )
    ).scalars().all()
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
