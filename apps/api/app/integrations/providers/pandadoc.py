"""PandaDoc: documents from templates (filled from the deal, account, contact and
quote) and AIVA documents sent for signature.

* Connecting: an admin pastes an API key (``Authorization: API-Key …``).
* From a template: AIVA reads the template's roles, tokens and pricing tables,
  fills the tokens it knows (``Client.FirstName``, ``Deal.Name``,
  ``Quote.Total``…), puts the quote's lines in the first pricing table and
  gives the signers the template's roles in signing order. Quotes are always
  sent through a template (the workspace picks a default one).
* From a file: AIVA documents (PDF or Word) are uploaded with signers as roles
  ``Signer1``, ``Signer2``…; field tags such as ``{signature:Signer1}`` and
  ``{date:Signer1}`` in the file place their fields.
* PandaDoc prepares new documents in the background: AIVA sends the document as
  soon as it is ready (a few seconds), or from the scheduler a minute later.
* Status: a webhook subscription (made through the API, with its own shared key)
  nudges AIVA; ``?signature=`` must be the HMAC-SHA256 of the raw body. AIVA
  always reads the document back before changing anything, and also checks
  open documents every 15 minutes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote as urlquote

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

logger = logging.getLogger("aiva.integrations.pandadoc")

KEY = "pandadoc"
VENDOR = "PandaDoc"
BASE = "https://api.pandadoc.com/public/v1"
SUPPORTS = {"quotes": True, "documents": True, "templates": True, "void": True, "remind": True}
POLL_EVERY = timedelta(minutes=15)
SEND_RETRY = timedelta(minutes=1)
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf", "docx"}
READY_WAIT = (1.0, 1.5, 2.0, 2.5)  # seconds between checks while PandaDoc prepares a new document
STATUS = {
    "document.uploaded": "created",
    "document.draft": "created",
    "document.sent": "sent",
    "document.viewed": "delivered",
    "document.waiting_approval": "delivered",
    "document.approved": "delivered",
    "document.waiting_pay": "delivered",
    "document.external_review": "delivered",
    "document.scheduled": "sent",
    "document.completed": "completed",
    "document.paid": "completed",
    "document.declined": "declined",
    "document.rejected": "voided",
    "document.voided": "voided",
    "document.error": "error",
}
DEFAULTS_EXTRA = {"quote_template_id": None}
SETTINGS = {"save_signed_copy", "mark_quote_accepted", "notify_sender"}


def role_name(index: int) -> str:
    """Roles in uploaded files (PandaDoc field tags can't use underscores)."""

    return f"Signer{index}"


def signable(name: str, content_type: str | None, size: int) -> bool:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return size <= MAX_DOCUMENT_BYTES and (extension in ALLOWED_EXTENSIONS or content_type == "application/pdf")


# ---------------------------------------------------------------------------
# Talking to PandaDoc
# ---------------------------------------------------------------------------


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"API-Key {api_key}", "Accept": "application/json"}


def _key(connection: IntegrationConnection) -> str:
    value = core.secret(connection, "api_key")
    if not value:
        raise IntegrationError("Paste a PandaDoc API key first.", 409)
    return value


async def _api(connection: IntegrationConnection, method: str, path: str, *, ok: tuple[int, ...] = (), timeout: float = 30, **kwargs: Any) -> Any:
    headers = {**_headers(_key(connection)), **(kwargs.pop("headers", None) or {})}
    return await _sales_http.call(VENDOR, method, f"{BASE}{path}", connection=connection, headers=headers, ok=ok, timeout=timeout, **kwargs)


def _doc_path(document_id: str) -> str:
    return f"/documents/{urlquote(document_id, safe='')}"


def _push_url(connection: IntegrationConnection | None) -> str | None:
    url = core.webhook_url(KEY, connection.webhook_token) if connection is not None else None
    return url if url and url.startswith("https://") else None


async def templates(connection: IntegrationConnection, search: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"count": 100, "page": 1, "deleted": "false"}
    if search:
        params["q"] = search[:100]
    data = (await _api(connection, "GET", "/templates", params=params)).json()
    return [
        {"id": str(t.get("id")), "name": t.get("name") or "Untitled template", "modified_at": t.get("date_modified")}
        for t in data.get("results") or []
        if t.get("id")
    ]


async def template_details(connection: IntegrationConnection, template_id: str) -> dict[str, Any]:
    if not template_id or len(template_id) > 100:
        raise IntegrationError("Choose a PandaDoc template.", 422)
    data = (await _api(connection, "GET", f"/templates/{urlquote(template_id, safe='')}/details")).json()
    roles = sorted(
        [{"name": str(r.get("name") or ""), "signing_order": r.get("signing_order")} for r in data.get("roles") or [] if r.get("name")],
        key=lambda r: (r["signing_order"] is None, r["signing_order"] or 0),
    )
    tables = ((data.get("pricing") or {}).get("tables") or []) if isinstance(data.get("pricing"), dict) else []
    return {
        "id": template_id,
        "name": data.get("name") or "Template",
        "roles": roles,
        "tokens": [str(t.get("name")) for t in data.get("tokens") or [] if t.get("name")],
        "pricing_tables": [str(t.get("name")) for t in tables if isinstance(t, dict) and t.get("name")],
    }


# ---------------------------------------------------------------------------
# Filling a template from AIVA records
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value.quantize(Decimal('0.01')):f}"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


async def record_tokens(db: AsyncSession, principal: Any, record_type: str | None, record_id: uuid.UUID | None, quote_row: Any) -> dict[str, str]:
    """What AIVA knows about the record, as PandaDoc token values."""

    from app.models.account import Account
    from app.models.contact import Contact
    from app.models.lead import Lead
    from app.models.opportunity import Opportunity
    from app.models.organization import Organization

    account = contact = deal = lead = None
    if quote_row is not None:
        account = await db.get(Account, quote_row.account_id) if quote_row.account_id else None
        contact = await db.get(Contact, quote_row.contact_id) if quote_row.contact_id else None
        deal = await db.get(Opportunity, quote_row.opportunity_id) if getattr(quote_row, "opportunity_id", None) else None
    if record_type == "opportunity" and record_id and deal is None:
        deal = await db.get(Opportunity, record_id)
    if record_type == "account" and record_id and account is None:
        account = await db.get(Account, record_id)
    if record_type == "contact" and record_id and contact is None:
        contact = await db.get(Contact, record_id)
    if record_type == "lead" and record_id:
        lead = await db.get(Lead, record_id)
    if deal is not None and account is None and deal.account_id:
        account = await db.get(Account, deal.account_id)
    if deal is not None and contact is None and getattr(deal, "primary_contact_id", None):
        contact = await db.get(Contact, deal.primary_contact_id)
    if contact is not None and account is None and contact.account_id:
        account = await db.get(Account, contact.account_id)
    organization = await db.get(Organization, principal.organization_id)
    first, last = _signing.split_name(principal.full_name or "")
    tokens = {
        "Sender.FirstName": first,
        "Sender.LastName": last,
        "Sender.Email": principal.email or "",
        "Sender.Company": organization.name if organization else "",
    }
    person = contact or lead
    if person is not None:
        tokens.update(
            {
                "Client.FirstName": person.first_name or "",
                "Client.LastName": person.last_name or "",
                "Client.Email": person.email or "",
                "Client.Phone": person.phone or "",
                "Client.Title": person.job_title or "",
                "Client.Company": (account.name if account else getattr(person, "company_name", None)) or "",
            }
        )
    if account is not None:
        tokens.update(
            {
                "Account.Name": account.name,
                "Account.Website": account.website or "",
                "Account.Phone": account.phone or "",
                "Account.Industry": account.industry or "",
                "Account.City": account.billing_city or "",
                "Account.Country": account.billing_country or "",
            }
        )
        tokens.setdefault("Client.Company", account.name)
    if deal is not None:
        tokens.update(
            {
                "Deal.Name": deal.name,
                "Deal.Amount": _text(getattr(deal, "amount", None)),
                "Deal.CloseDate": _text(getattr(deal, "expected_close_date", None) or getattr(deal, "close_date", None)),
            }
        )
    if quote_row is not None:
        tokens.update(
            {
                "Quote.Number": f"Q-{quote_row.number}",
                "Quote.Name": quote_row.name or "",
                "Quote.Total": _text(quote_row.total),
                "Quote.Subtotal": _text(quote_row.subtotal),
                "Quote.Tax": _text(quote_row.tax_total),
                "Quote.Discount": _text(quote_row.discount_total),
                "Quote.Currency": quote_row.currency or "USD",
                "Quote.ValidUntil": _text(quote_row.valid_until),
                "Quote.Terms": quote_row.terms or "",
            }
        )
    return {k: v for k, v in tokens.items() if v not in (None, "")}


def pricing_table(name: str, quote_row: Any) -> dict[str, Any]:
    rows = []
    for line in quote_row.line_items or []:
        data: dict[str, Any] = {
            "Name": str(line.name)[:500],
            "Description": str(line.description or "")[:2000],
            "Price": float(Decimal(str(line.unit_price or 0))),
            "QTY": float(Decimal(str(line.quantity or 0))),
        }
        if line.discount_percent:
            data["Discount"] = {"value": float(Decimal(str(line.discount_percent))), "type": "percent"}
        rows.append({"options": {"optional": False, "optional_selected": True, "qty_editable": False}, "data": data})
    return {
        "name": name,
        "data_merge": False,
        "options": {"currency": quote_row.currency or "USD"},
        "sections": [{"title": f"Quote Q-{quote_row.number}", "default": True, "rows": rows}],
    }


def _recipient_entries(recipients: list[dict[str, Any]], roles: list[str]) -> list[dict[str, Any]]:
    """Signers get the roles in signing order; people copied get no role (PandaDoc makes them CC)."""

    signers = _signing.signers_in_order(recipients)
    if len(signers) > len(roles):
        names = ", ".join(roles) or "none"
        raise IntegrationError(f"This template has {len(roles)} signer role{'s' if len(roles) != 1 else ''} ({names}), so add at most that many signers.", 422)
    ordered = len({int(s["routing_order"]) for s in signers}) > 1
    out = []
    for index, signer in enumerate(signers):
        first, last = _signing.split_name(signer["name"])
        entry: dict[str, Any] = {"email": signer["email"], "first_name": first, "last_name": last, "role": roles[index]}
        if ordered:
            entry["signing_order"] = int(signer["routing_order"])
        out.append(entry)
    for copy in (r for r in recipients if r["role"] == "cc"):
        first, last = _signing.split_name(copy["name"])
        out.append({"email": copy["email"], "first_name": first, "last_name": last})
    return out


# ---------------------------------------------------------------------------
# Sending
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
    template_id: str | None = None,
) -> SignatureRequest:
    org = principal.organization_id
    cleaned = _signing.clean_recipients(recipients)
    config = await core.workspace_config(db, org, KEY)
    if quote_row is not None and not template_id:
        template_id = config.get("quote_template_id")
        if not template_id:
            raise IntegrationError("PandaDoc sends quotes through a template. Choose a template (one with a pricing table works best), or set a default quote template on the PandaDoc page.", 422)
    if quote_row is not None and not quote_row.line_items:
        raise IntegrationError("Add at least one line item to the quote before sending it for signature.", 409)
    metadata = {"aiva_workspace": str(org)}

    if template_id:
        template = await template_details(connection, str(template_id))
        roles = [r["name"] for r in template["roles"]]
        entries = _recipient_entries(cleaned, roles)
        values = await record_tokens(db, principal, record_type, record_id, quote_row)
        wanted = set(template["tokens"])
        body: dict[str, Any] = {
            "name": "",
            "template_uuid": template["id"],
            "recipients": entries,
            "tokens": [{"name": name, "value": value} for name, value in values.items() if name in wanted],
            "metadata": metadata,
            "tags": ["aiva"],
        }
        if quote_row is not None and template["pricing_tables"]:
            body["pricing_tables"] = [pricing_table(template["pricing_tables"][0], quote_row)]
        document_name = (f"Quote Q-{quote_row.number} · {quote_row.name}" if quote_row is not None else f"{template['name']}")[:300]
        files = None
    elif document is not None:
        stored = await _doc_store.latest_file(db, document)
        if stored is None:
            raise IntegrationError("That document has no file to send.", 409)
        if not signable(stored.file_name, document.content_type, stored.size_bytes):
            raise IntegrationError("PandaDoc takes PDF and Word (.docx) files up to 25 MB.", 415)
        signers = _signing.signers_in_order(cleaned)
        entries = _recipient_entries(cleaned, [role_name(i) for i in range(1, len(signers) + 1)])
        document_name = document.name[:300]
        body = {"name": "", "recipients": entries, "parse_form_fields": False, "metadata": metadata, "tags": ["aiva"]}
        files = {"file": (stored.file_name[:200], stored.data, "application/pdf" if stored.file_name.lower().endswith(".pdf") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    else:
        raise IntegrationError("Choose a template, a quote or a document to send.", 422)

    request_row = _signing.new_request(
        db, principal, provider=KEY, record_type=record_type, record_id=record_id, subject=subject, message=message,
        recipients=cleaned, document_name=document_name, quote_row=quote_row, document=document,
    )
    await db.flush()
    body["name"] = (subject.strip() or document_name)[:255]
    body["metadata"] = {**metadata, "aiva_request_id": str(request_row.id)}
    try:
        if files is not None:
            created = await _api(connection, "POST", "/documents?upload", files=files, data={"data": json.dumps(body)}, timeout=90)
        else:
            created = await _api(connection, "POST", "/documents", json=body, timeout=60)
    except IntegrationError as exc:
        request_row.status = "error"
        request_row.error = str(exc)[:1000]
        _signing.log(db, request_row, "send_failed", f"Couldn't create “{document_name}” in PandaDoc: {exc}", level="error", connection=connection, user_id=principal.user_id)
        raise
    request_row.envelope_id = str(created.json().get("id") or "")[:100] or None
    request_row.last_checked_at = core.utcnow()
    _signing.log(db, request_row, "prepared", f"PandaDoc is preparing “{document_name}”.", connection=connection, user_id=principal.user_id)
    request_row.recipients = [{**r, "pandadoc_role": None} for r in cleaned]
    for index, signer in enumerate(_signing.signers_in_order(cleaned)):
        for recipient in request_row.recipients:
            if recipient["recipient_id"] == signer["recipient_id"]:
                recipient["pandadoc_role"] = entries[index].get("role")
    if await _send_when_ready(db, connection, request_row, principal=principal, quote_row=quote_row, waits=READY_WAIT):
        return request_row
    _signing.log(db, request_row, "queued", "PandaDoc is still preparing the document; AIVA will send it as soon as it's ready.", connection=connection)
    return request_row


async def _send_when_ready(
    db: AsyncSession,
    connection: IntegrationConnection,
    request_row: SignatureRequest,
    *,
    principal: Any = None,
    quote_row: Any = None,
    waits: tuple[float, ...] = (),
) -> bool:
    """Send the document once PandaDoc has finished preparing it. Returns True when sent."""

    path = _doc_path(request_row.envelope_id or "")
    for pause in (0.0, *waits):
        if pause:
            await asyncio.sleep(pause)
        status = str((await _api(connection, "GET", path)).json().get("status") or "")
        if status == "document.draft":
            break
        if status == "document.error":
            request_row.status = "error"
            request_row.error = "PandaDoc couldn't prepare the document."
            _signing.log(db, request_row, "send_failed", f"PandaDoc couldn't prepare “{request_row.document_name}”.", level="error", connection=connection)
            return True
        if status and status not in {"document.uploaded", "document.draft"}:
            # Already sent (for example by a second scheduler run): just follow it.
            request_row.status = STATUS.get(status, request_row.status)
            return True
    else:
        return False
    body: dict[str, Any] = {"subject": request_row.subject[:255], "silent": False}
    if request_row.message:
        body["message"] = request_row.message[:5000]
    try:
        await _api(connection, "POST", f"{path}/send", json=body)
    except IntegrationError as exc:
        request_row.status = "error"
        request_row.error = str(exc)[:1000]
        _signing.log(db, request_row, "send_failed", f"Couldn't send “{request_row.document_name}”: {exc}", level="error", connection=connection)
        return True
    if principal is None:
        principal = _Sender(request_row)
    _signing.mark_sent(db, request_row, connection=connection, principal=principal, quote_row=quote_row)
    return True


class _Sender:
    """The person who sent a request, for sends that finish in the scheduler."""

    def __init__(self, request_row: SignatureRequest):
        self.user_id = request_row.sent_by_user_id
        self.organization_id = request_row.organization_id


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def _download(connection: IntegrationConnection, request_row: SignatureRequest, certificate: bool) -> bytes:
    path = _doc_path(request_row.envelope_id or "")
    response = await _api(connection, "GET", f"{path}/download-protected", ok=(404, 409), timeout=90, headers={"Accept": "application/pdf, */*"})
    if response.status_code >= 400:
        response = await _api(connection, "GET", f"{path}/download", timeout=90, headers={"Accept": "application/pdf, */*"})
    return response.content


async def refresh(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest) -> bool:
    """Reads the document back from PandaDoc and applies any change."""

    if not request_row.envelope_id:
        return False
    if request_row.status == "created" and request_row.sent_at is None:
        sent = await _send_when_ready(db, connection, request_row)
        request_row.last_checked_at = core.utcnow()
        return sent
    data = (await _api(connection, "GET", f"{_doc_path(request_row.envelope_id)}/details")).json()
    request_row.last_checked_at = core.utcnow()
    changed = False
    recipients = [dict(r) for r in request_row.recipients or []]
    by_email = {r["email"].lower(): r for r in recipients}
    for remote in data.get("recipients") or []:
        recipient = by_email.get(str(remote.get("email") or "").lower())
        if recipient is None:
            continue
        recipient["pandadoc_id"] = remote.get("id")
        if remote.get("has_completed") and recipient["role"] == "signer":
            changed |= _signing.set_recipient(db, request_row, recipient, "signed", connection=connection)
    status = str(data.get("status") or "")
    new_status = STATUS.get(status, request_row.status)
    if new_status == "delivered":
        for recipient in recipients:
            if recipient["role"] == "signer" and recipient.get("status") == "sent":
                changed |= _signing.set_recipient(db, request_row, recipient, "delivered", connection=connection)
                break
    if new_status == "declined":
        for recipient in recipients:
            if recipient["role"] == "signer" and recipient.get("status") not in {"signed", "completed", "declined"}:
                changed |= _signing.set_recipient(db, request_row, recipient, "declined", connection=connection)
                break
    request_row.recipients = recipients
    if new_status in {"created", "sent", "delivered"}:
        if new_status != request_row.status and not (new_status == "created" and request_row.sent_at):
            request_row.status = new_status
            request_row.error = None
            changed = True
        return changed
    if new_status == "error":
        if request_row.status != "error":
            request_row.status = "error"
            request_row.error = "PandaDoc reported an error with this document."
            changed = True
        return changed
    if await _signing.apply_status(
        db, connection, request_row, new_status,
        reason="The document was rejected by an approver in PandaDoc." if status == "document.rejected" else "The document was voided or expired in PandaDoc.",
        download=lambda certificate: _download(connection, request_row, certificate),
        description=f"Signed with PandaDoc (document {request_row.envelope_id}).",
    ):
        changed = True
    return changed


async def void(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest, reason: str, user_id: uuid.UUID) -> None:
    if request_row.status not in _signing.OPEN or not request_row.envelope_id:
        raise IntegrationError(f"This request is already {request_row.status}.", 409)
    if request_row.sent_at is None:
        await _api(connection, "DELETE", _doc_path(request_row.envelope_id), ok=(404,))
    else:
        await _api(connection, "PATCH", f"{_doc_path(request_row.envelope_id)}/status", json={"status": 11, "note": reason[:500], "notify_recipients": True})
    request_row.status = "voided"
    request_row.error = reason[:1000]
    request_row.last_checked_at = core.utcnow()
    _signing.log(db, request_row, "voided", f"Voided “{request_row.document_name}”: {reason}", level="warning", connection=connection, user_id=user_id)


async def resend(db: AsyncSession, connection: IntegrationConnection, request_row: SignatureRequest, user_id: uuid.UUID) -> None:
    if request_row.status not in {"sent", "delivered"} or not request_row.envelope_id:
        raise IntegrationError("Only requests waiting for signatures can be resent.", 409)
    data = (await _api(connection, "GET", f"{_doc_path(request_row.envelope_id)}/details")).json()
    waiting = [
        r for r in data.get("recipients") or []
        if not r.get("has_completed") and str(r.get("recipient_type") or "signer").lower() == "signer" and r.get("id")
    ]
    if not waiting:
        raise IntegrationError("Nobody is waiting to sign right now.", 409)
    result = (
        await _api(
            connection, "POST", f"{_doc_path(request_row.envelope_id)}/send-reminder",
            json={"reminders": [{"recipient_id": r["id"], "delivery_methods": {"email": True}} for r in waiting]},
        )
    ).json()
    failures = [item for item in (result.get("result") or []) if str((item.get("email") or {}).get("status") or item.get("status") or "").lower() in {"error", "failed"}]
    names = ", ".join(f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() or r.get("email", "") for r in waiting)
    if failures and len(failures) == len(waiting):
        raise IntegrationError("PandaDoc didn't send the reminder (each person can get one every 24 hours, in signing order).", 409)
    _signing.log(db, request_row, "resent", f"Sent a reminder to {names}.", connection=connection, user_id=user_id)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def signature_ok(shared_key: str | None, body: bytes, signature: str | None) -> bool:
    if not shared_key or not signature:
        return False
    expected = hmac.new(shared_key.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.strip().lower().encode())


async def subscribe(db: AsyncSession, connection: IntegrationConnection) -> None:
    url = _push_url(connection)
    if not url:
        return
    await unsubscribe(db, connection)
    created = (
        await _api(
            connection, "POST", "/webhook-subscriptions",
            json={"name": "AIVA CRM", "url": url, "active": True, "payload": ["metadata"], "triggers": ["document_state_changed", "recipient_completed"]},
        )
    ).json()
    subscription_id = str(created.get("uuid") or created.get("id") or "") or None
    shared_key = created.get("shared_key")
    if subscription_id and not shared_key:
        shared_key = (await _api(connection, "PATCH", f"/webhook-subscriptions/{urlquote(subscription_id, safe='')}/shared-key")).json().get("shared_key")
    connection.config = {**(connection.config or {}), "webhook_id": subscription_id}
    core.set_secret(connection, "webhook_shared_key", shared_key or None)
    core.log(db, connection.organization_id, KEY, "webhook_registered", "PandaDoc will tell AIVA when documents are viewed, signed or declined.", level="success", connection=connection)


async def unsubscribe(db: AsyncSession, connection: IntegrationConnection) -> None:
    subscription_id = (connection.config or {}).get("webhook_id")
    if not subscription_id:
        return
    try:
        await _api(connection, "DELETE", f"/webhook-subscriptions/{urlquote(str(subscription_id), safe='')}", ok=(404,))
    except IntegrationError as exc:
        core.log(db, connection.organization_id, KEY, "webhook_cleanup", f"Couldn't remove the old PandaDoc webhook: {exc}", level="warning", connection=connection)
    connection.config = {**(connection.config or {}), "webhook_id": None}
    core.set_secret(connection, "webhook_shared_key", None)


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method == "GET":
        return JSONResponse({"ok": True})
    body = await request.body()
    if len(body) > 2 * 1024 * 1024:
        return JSONResponse({"error": "too large"}, status_code=413)
    org = connection.organization_id
    if not signature_ok(core.secret(connection, "webhook_shared_key"), body, request.query_params.get("signature")):
        core.log(db, org, KEY, "webhook_rejected", "A PandaDoc notification with a missing or wrong signature was refused.", level="warning", connection=connection)
        return JSONResponse({"error": "invalid signature"}, status_code=403)
    try:
        events = json.loads(body or b"[]")
    except ValueError:
        return JSONResponse({"ignored": "not json"})
    if isinstance(events, dict):
        events = [events]
    ids = []
    for event in events[:50] if isinstance(events, list) else []:
        data = event.get("data") if isinstance(event, dict) else None
        document_id = str((data or {}).get("id") or "")[:100]
        if document_id and document_id not in ids:
            ids.append(document_id)
    refreshed = 0
    for document_id in ids[:20]:
        request_row = (
            await db.execute(
                select(SignatureRequest)
                .where(SignatureRequest.organization_id == org, SignatureRequest.provider == KEY, SignatureRequest.envelope_id == document_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request_row is None or request_row.status not in _signing.OPEN:
            continue
        try:
            await refresh(db, connection, request_row)  # the notification is only a nudge
            refreshed += 1
        except IntegrationError as exc:
            request_row.last_checked_at = None
            core.log(db, org, KEY, "webhook_refresh_failed", f"Couldn't read document {document_id} back from PandaDoc: {exc}", level="warning", connection=connection)
    return JSONResponse({"ok": True, "refreshed": refreshed})


# ---------------------------------------------------------------------------
# Hub hooks
# ---------------------------------------------------------------------------


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    api_key = (values.get("api_key") or "").strip()
    if len(api_key) < 20:
        raise IntegrationError("That doesn't look like a PandaDoc API key.", 422)
    await _sales_http.call(VENDOR, "GET", f"{BASE}/templates", headers=_headers(api_key), params={"count": 1, "page": 1})
    me = await _sales_http.call(VENDOR, "GET", f"{BASE}/members/current", headers=_headers(api_key), ok=(403, 404))
    info = me.json() if me.status_code < 400 else {}
    workspace = info.get("workspace_name") or ""
    email = info.get("email") or ""
    label = " · ".join(x for x in (workspace, email) if x) or VENDOR
    return {"label": f"PandaDoc ({label})" if label != VENDOR else VENDOR, "external_id": info.get("workspace") or None, "config": {"email": email or None, "workspace_name": workspace or None}}


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if not _push_url(connection):
        core.log(
            db, connection.organization_id, KEY, "polling",
            "AIVA isn't on a public https address, so PandaDoc can't notify it; documents are checked every 15 minutes instead.",
            connection=connection,
        )
        return
    try:
        await subscribe(db, connection)
    except IntegrationError as exc:
        core.log(db, connection.organization_id, KEY, "webhook_failed", f"Couldn't subscribe to PandaDoc updates ({exc}); AIVA will check documents every 15 minutes instead.",
                 level="warning", connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await unsubscribe(db, connection)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    found = await templates(connection)
    return f"PandaDoc answered: {len(found)} template{'s' if len(found) != 1 else ''} available."


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if personal:
        raise IntegrationError("PandaDoc is set up for the whole workspace.", 422)
    config = dict(config)
    extra = {k: config.pop(k) for k in list(config) if k in DEFAULTS_EXTRA}
    # PandaDoc's signed PDF always carries its own audit trail, and reminders/expiry are set in PandaDoc.
    cleaned = _signing.clean_settings_values(config, VENDOR, SETTINGS)
    if "quote_template_id" in extra:
        value = str(extra["quote_template_id"] or "").strip()
        if value and (len(value) > 100 or not value.replace("-", "").replace("_", "").isalnum()):
            raise IntegrationError("Choose a PandaDoc template from the list.", 422)
        if value:
            connection = await core.active_connection(db, principal.organization_id, KEY)
            details = await template_details(connection, value)
            cleaned["quote_template_name"] = details["name"]
        else:
            cleaned["quote_template_name"] = None
        cleaned["quote_template_id"] = value or None
    return cleaned


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    toggle = await core.toggle_row(db, org, KEY)
    config = dict(toggle.config or {}) if toggle else {}
    return {
        **await _signing.detail_summary(db, KEY, principal),
        "settings": {**settings_for(config), "quote_template_id": config.get("quote_template_id"), "quote_template_name": config.get("quote_template_name")},
        "push": bool(_push_url(connection)),
        "webhook": {"registered": bool(connection and (connection.config or {}).get("webhook_id"))},
        "account": {"email": (connection.config or {}).get("email"), "workspace": (connection.config or {}).get("workspace_name")} if connection else {},
        "field_tags": {"signature": "{signature:Signer1}", "date": "{date:Signer1}"},
        "tokens": sorted(
            [
                "Client.FirstName", "Client.LastName", "Client.Email", "Client.Phone", "Client.Title", "Client.Company",
                "Sender.FirstName", "Sender.LastName", "Sender.Email", "Sender.Company",
                "Account.Name", "Account.Website", "Account.Phone", "Account.Industry", "Account.City", "Account.Country",
                "Deal.Name", "Deal.Amount", "Deal.CloseDate",
                "Quote.Number", "Quote.Name", "Quote.Total", "Quote.Subtotal", "Quote.Tax", "Quote.Discount", "Quote.Currency", "Quote.ValidUntil", "Quote.Terms",
            ]
        ),
    }


async def run_due(db: AsyncSession) -> int:
    handled = 0
    now = core.utcnow()
    # Documents still waiting to be sent (PandaDoc was preparing them), then open ones to check.
    waiting = list(
        (
            await db.execute(
                select(SignatureRequest.id)
                .where(
                    SignatureRequest.provider == KEY,
                    SignatureRequest.status == "created",
                    SignatureRequest.sent_at.is_(None),
                    SignatureRequest.envelope_id.is_not(None),
                    (SignatureRequest.last_checked_at.is_(None)) | (SignatureRequest.last_checked_at < now - SEND_RETRY),
                )
                .limit(30)
            )
        ).scalars()
    )
    due = waiting + [i for i in await _signing.due_requests(db, KEY, POLL_EVERY) if i not in waiting]
    connections: dict[uuid.UUID, IntegrationConnection | None] = {}
    for request_id in due[:60]:
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
