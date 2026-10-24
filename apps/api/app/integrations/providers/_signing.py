"""What every e-signature provider shares: DocuSign, Adobe Acrobat Sign and PandaDoc.

A ``SignatureRequest`` row is one envelope / agreement / document sent from
AIVA (its provider's id lives in ``envelope_id``). This module holds the parts
that don't depend on the provider:

* checking recipients (signers and copies, signing order);
* the printable quote that is sent for signature (each provider has its own
  invisible text tags for where signer N signs and dates);
* the request's timeline (the activity log, keyed by the request id);
* finishing a signed request: saving the signed PDF to Documents and linking
  it to the record, quote and deal, marking the quote accepted, telling the
  sender;
* how requests are shown to the browser.

Each provider module implements ``send``, ``refresh``, ``void`` and ``resend``
plus ``SUPPORTS`` and ``signable(document)``; ``module(provider)`` finds it.
"""

from __future__ import annotations

import html
import importlib
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from types import ModuleType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _doc_store
from app.models.collaboration import Document
from app.models.integration_hub import IntegrationConnection, IntegrationLog, SignatureRequest

SIGN_KEYS = ("docusign", "adobe_sign", "pandadoc")
LABELS = {"docusign": "DocuSign", "adobe_sign": "Adobe Acrobat Sign", "pandadoc": "PandaDoc"}
OPEN = ("created", "sent", "delivered")
DEFAULTS: dict[str, Any] = {
    "save_signed_copy": True,
    "include_certificate": True,
    "mark_quote_accepted": True,
    "notify_sender": True,
    "reminder_days": 0,
    "expire_days": 0,
}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def module(provider: str) -> ModuleType:
    if provider not in SIGN_KEYS:
        raise IntegrationError("Unknown e-signature provider.", 422)
    return importlib.import_module(f"app.integrations.providers.{provider}")


def settings_for(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    for name in DEFAULTS:
        if config and name in config:
            merged[name] = config[name]
    return merged


def clean_settings_values(config: dict[str, Any], label: str, allowed: set[str] | None = None) -> dict[str, Any]:
    """Validate the shared e-signature settings (each provider may allow a subset)."""

    allowed = allowed or set(DEFAULTS)
    cleaned: dict[str, Any] = {}
    for name, value in config.items():
        if name not in allowed:
            raise IntegrationError(f"Unknown setting “{name}”.", 422)
        if name in {"save_signed_copy", "include_certificate", "mark_quote_accepted", "notify_sender"}:
            if not isinstance(value, bool):
                raise IntegrationError(f"“{name.replace('_', ' ')}” must be on or off.", 422)
            cleaned[name] = value
        elif name in {"reminder_days", "expire_days"}:
            limit = 30 if name == "reminder_days" else 365
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
                raise IntegrationError(f"“{name.replace('_', ' ')}” must be a whole number from 0 to {limit}.", 422)
            cleaned[name] = value
    return cleaned


# ---------------------------------------------------------------------------
# What gets sent
# ---------------------------------------------------------------------------


def money(value: Any, currency: str) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "AUD": "A$", "CAD": "C$"}.get(currency.upper())
    text = f"{amount:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency.upper()}"


def _docusign_sign(index: int) -> str:
    return f"/aivasign{index}/"


def _docusign_date(index: int) -> str:
    return f"/aivadate{index}/"


async def quote_html(
    db: AsyncSession,
    quote_row: Any,
    signers: list[dict[str, Any]],
    *,
    sign_tag: Callable[[int], str] = _docusign_sign,
    date_tag: Callable[[int], str] = _docusign_date,
) -> bytes:
    """A clean, printable quote with a signature block per signer.

    ``sign_tag`` / ``date_tag`` give the provider's invisible tag (white text)
    that places signer N's signature and date.
    """

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
            f"<td class=num>{money(line.unit_price, currency)}</td>"
            f"<td class=num>{discount}</td>"
            f"<td class=num>{money(line.line_total, currency)}</td>"
            "</tr>"
        )
    prepared_for = " · ".join(
        x for x in [e(account.name) if account else "", e(f"{contact.first_name} {contact.last_name}".strip()) if contact else "", e(contact.email) if contact and contact.email else ""] if x
    )
    blocks = []
    for index, signer in enumerate(signers, start=1):
        blocks.append(
            "<div class=sig>"
            f"<div class=line><span class=anchor>{e(sign_tag(index))}</span></div>"
            f"<div class=label>Signature — {e(signer['name'])}</div>"
            f"<div class=line short><span class=anchor>{e(date_tag(index))}</span></div>"
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
<tr><td>Subtotal</td><td class=num>{money(quote_row.subtotal, currency)}</td></tr>
<tr><td>Discount</td><td class=num>−{money(quote_row.discount_total, currency)}</td></tr>
<tr><td>Tax</td><td class=num>{money(quote_row.tax_total, currency)}</td></tr>
<tr class=grand><td>Total</td><td class=num>{money(quote_row.total, currency)}</td></tr></table>
{f'<div class=box><h3>Terms</h3><div>{e(quote_row.terms).replace(chr(10), "<br>")}</div></div>' if quote_row.terms else ''}
{f'<div class=box><h3>Notes</h3><div>{e(quote_row.notes).replace(chr(10), "<br>")}</div></div>' if quote_row.notes else ''}
<div class=sigs><div class=box><h3>Acceptance</h3><div class=muted>By signing, you accept this quote and its terms.</div></div>{''.join(blocks)}</div>
</body></html>"""
    return document.encode("utf-8")


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


def signers_in_order(recipients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Signers by signing order (ties keep the order they were added in)."""

    signers = [r for r in recipients if r.get("role") == "signer"]
    return sorted(signers, key=lambda r: (int(r.get("routing_order") or 1), int(r.get("recipient_id") or 0)))


def split_name(name: str) -> tuple[str, str]:
    parts = (name or "").strip().split(None, 1)
    if not parts:
        return "", ""
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


# ---------------------------------------------------------------------------
# Timeline (the activity log, keyed by the request id)
# ---------------------------------------------------------------------------


def log(
    db: AsyncSession,
    request_row: SignatureRequest,
    action: str,
    message: str,
    *,
    level: str = "info",
    connection: IntegrationConnection | None = None,
    user_id: uuid.UUID | None = None,
    **detail: Any,
) -> None:
    core.log(
        db, request_row.organization_id, request_row.provider, action, message, level=level, connection=connection, user_id=user_id,
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
                IntegrationLog.provider.in_(SIGN_KEYS),
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


def when(value: Any) -> str | None:
    """An ISO timestamp from a provider's date (ISO text or epoch milliseconds)."""

    if not value:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, tz=core.utcnow().tzinfo).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Creating, finishing and announcing requests
# ---------------------------------------------------------------------------


def new_request(
    db: AsyncSession,
    principal: Any,
    *,
    provider: str,
    record_type: str | None,
    record_id: uuid.UUID | None,
    subject: str,
    message: str | None,
    recipients: list[dict[str, Any]],
    document_name: str,
    quote_row: Any = None,
    document: Document | None = None,
) -> SignatureRequest:
    row = SignatureRequest(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        provider=provider,
        subject=(subject or f"Please sign: {document_name}").strip()[:300],
        message=(message or "").strip()[:10000] or None,
        status="created",
        record_type=record_type,
        record_id=record_id,
        document_id=document.id if document is not None else None,
        quote_id=quote_row.id if quote_row is not None else None,
        document_name=document_name[:300],
        recipients=recipients,
        sent_by_user_id=principal.user_id,
    )
    db.add(row)
    return row


def mark_sent(
    db: AsyncSession,
    request_row: SignatureRequest,
    *,
    connection: IntegrationConnection,
    principal: Any,
    status: str = "sent",
    quote_row: Any = None,
) -> None:
    """After the provider accepted the request: statuses, timeline, the quote, the audit trail."""

    request_row.status = status
    request_row.sent_at = core.utcnow()
    request_row.last_checked_at = core.utcnow()
    request_row.error = None
    cleaned = request_row.recipients or []
    first_order = min((r["routing_order"] for r in cleaned), default=1)
    request_row.recipients = [{**r, "status": "sent" if r["routing_order"] == first_order else "created"} for r in cleaned]
    signers = [r for r in cleaned if r["role"] == "signer"]
    who = ", ".join(r["name"] for r in signers)
    via = "" if request_row.provider == "docusign" else f" with {LABELS[request_row.provider]}"
    log(db, request_row, "sent", f"Sent “{request_row.document_name}” to {who} for signature{via}.", level="success", connection=connection, user_id=principal.user_id)
    if quote_row is not None and quote_row.status == "draft":
        quote_row.status = "sent"
        quote_row.sent_at = core.utcnow()
    from app.services.automation import record_audit

    record_audit(
        db, organization_id=request_row.organization_id, action="signature_requested", entity_type=request_row.record_type or "document",
        entity_id=request_row.record_id, entity_label=request_row.document_name,
        changes={"provider": request_row.provider, "envelope_id": request_row.envelope_id, "signers": [r["email"] for r in signers]},
    )


async def notify(db: AsyncSession, request_row: SignatureRequest, title: str, body: str) -> None:
    config = settings_for(await core.workspace_config(db, request_row.organization_id, request_row.provider))
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
            title=title[:200], body=body[:2000], link=(link or f"/integrations/{request_row.provider}")[:300],
        )
    )


async def save_signed_copy(db: AsyncSession, request_row: SignatureRequest, pdf: bytes, *, description: str) -> Document:
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
        organization_id=request_row.organization_id,
        owner_user_id=request_row.sent_by_user_id,
        name=f"{base_name} (signed).pdf",
        data=pdf,
        content_type="application/pdf",
        links=links,
        description=description,
        tags=[request_row.provider, "signed"],
    )
    request_row.signed_document_id = document.id
    return document


async def finish(
    db: AsyncSession,
    connection: IntegrationConnection,
    request_row: SignatureRequest,
    download: Callable[[bool], Any] | None,
    *,
    description: str,
) -> None:
    """Everyone signed: save the signed copy (``download(include_certificate)``
    returns the PDF bytes), mark the quote accepted, tell the sender."""

    config = settings_for(await core.workspace_config(db, request_row.organization_id, request_row.provider))
    signed_document_id = None
    if config["save_signed_copy"] and download is not None:
        try:
            pdf = await download(bool(config["include_certificate"]))
            document = await save_signed_copy(db, request_row, pdf, description=description)
            signed_document_id = str(document.id)
        except IntegrationError as exc:
            log(db, request_row, "signed_copy_failed", f"Signed, but the signed copy couldn't be saved: {exc}", level="warning", connection=connection)
    log(
        db, request_row, "completed", f"Everyone signed “{request_row.document_name}”." + (" The signed copy is in Documents." if signed_document_id else ""),
        level="success", connection=connection, signed_document_id=signed_document_id,
    )
    if config["mark_quote_accepted"] and request_row.quote_id:
        await accept_quote(db, request_row)
    await notify(db, request_row, f"Signed: {request_row.document_name}", "Everyone has signed." + (" The signed copy is in Documents." if signed_document_id else ""))


async def accept_quote(db: AsyncSession, request_row: SignatureRequest) -> None:
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
    log(db, request_row, "quote_accepted", f"Quote Q-{quote_row.number} was marked accepted.", level="success")


def set_recipient(
    db: AsyncSession,
    request_row: SignatureRequest,
    recipient: dict[str, Any],
    status: str,
    *,
    at: str | None = None,
    reason: str | None = None,
    connection: IntegrationConnection | None = None,
) -> bool:
    """Move one recipient to ``delivered`` (opened), ``signed`` or ``declined`` and note it on the timeline."""

    if recipient.get("status") == status or recipient.get("status") in {"signed", "completed", "declined"} and status == "delivered":
        return False
    recipient["status"] = status
    stamp = at or core.utcnow().isoformat()
    if status == "delivered":
        recipient["delivered_at"] = recipient.get("delivered_at") or stamp
        log(db, request_row, "recipient_viewed", f"{recipient['name']} opened “{request_row.document_name}”.", connection=connection)
    elif status in {"signed", "completed"} and recipient.get("role") == "signer":
        recipient["signed_at"] = recipient.get("signed_at") or stamp
        log(db, request_row, "recipient_signed", f"{recipient['name']} signed “{request_row.document_name}”.", level="success", connection=connection)
    elif status == "declined":
        recipient["declined_at"] = recipient.get("declined_at") or stamp
        recipient["decline_reason"] = (reason or "")[:500] or None
        text = f": {recipient['decline_reason']}" if recipient["decline_reason"] else "."
        log(db, request_row, "recipient_declined", f"{recipient['name']} declined to sign{text}", level="warning", connection=connection)
    return True


async def apply_status(
    db: AsyncSession,
    connection: IntegrationConnection,
    request_row: SignatureRequest,
    new_status: str,
    *,
    completed_at: str | None = None,
    reason: str | None = None,
    download: Callable[[bool], Any] | None = None,
    description: str = "",
) -> bool:
    """Apply a new overall status (sent, delivered, completed, declined, voided). Returns True if it changed."""

    previous = request_row.status
    if new_status == previous:
        return False
    request_row.status = new_status
    request_row.error = None
    if new_status == "completed":
        request_row.completed_at = datetime.fromisoformat(completed_at) if completed_at else core.utcnow()
        await finish(db, connection, request_row, download, description=description)
    elif new_status == "declined":
        log(db, request_row, "declined", f"“{request_row.document_name}” was declined.", level="warning", connection=connection)
        await notify(db, request_row, f"Declined: {request_row.document_name}", "A recipient declined to sign.")
    elif new_status == "voided":
        text = reason or "The request was voided or expired."
        request_row.error = str(text)[:1000]
        log(db, request_row, "voided", f"“{request_row.document_name}” was voided: {text}", level="warning", connection=connection)
        await notify(db, request_row, f"Voided: {request_row.document_name}", str(text))
    return True


# ---------------------------------------------------------------------------
# How requests are shown
# ---------------------------------------------------------------------------


def request_out(row: SignatureRequest, names: dict[str, str], records: dict[tuple[str, str], dict[str, Any]], timeline: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    signed_document_id = str(row.signed_document_id) if row.signed_document_id else next(
        (e.get("signed_document_id") for e in reversed(timeline or []) if e.get("signed_document_id")), None
    )
    record = records.get((row.record_type, str(row.record_id))) if row.record_type and row.record_id else None
    recipients = row.recipients or []
    signers = [r for r in recipients if r.get("role") == "signer"]
    try:
        supports = dict(module(row.provider).SUPPORTS)
    except IntegrationError:
        supports = {}
    return {
        "id": str(row.id),
        "provider": row.provider,
        "provider_name": LABELS.get(row.provider, row.provider),
        "supports": supports,
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


async def provider_states(db: AsyncSession, organization_id: uuid.UUID) -> list[dict[str, Any]]:
    """Which e-signature providers are on and connected for the workspace."""

    out = []
    for key in SIGN_KEYS:
        enabled = await core.is_enabled(db, organization_id, key)
        connection = await core.connection_for(db, organization_id, key) if enabled else None
        mod = module(key)
        out.append(
            {
                "key": key,
                "name": LABELS[key],
                "enabled": enabled,
                "connected": bool(connection and connection.status == "connected"),
                "supports": dict(mod.SUPPORTS),
            }
        )
    return out


async def detail_summary(db: AsyncSession, provider: str, principal: Any) -> dict[str, Any]:
    """Counts and recent requests for one provider's integration page (record access respected)."""

    from sqlalchemy import func

    org = principal.organization_id
    counts = dict(
        (
            await db.execute(
                select(SignatureRequest.status, func.count())
                .where(SignatureRequest.organization_id == org, SignatureRequest.provider == provider)
                .group_by(SignatureRequest.status)
            )
        ).all()
    )
    rows = (
        await db.execute(
            select(SignatureRequest)
            .where(SignatureRequest.organization_id == org, SignatureRequest.provider == provider)
            .order_by(SignatureRequest.created_at.desc())
            .limit(8)
        )
    ).scalars().all()
    recent = await requests_out(db, org, list(rows), with_timeline=True)
    if not principal.can("admin.settings"):
        recent = [i for i in recent if i["sent_by_user_id"] == str(principal.user_id) or i["record"] is not None]
    return {
        "counts": {status: int(counts.get(status, 0)) for status in ("sent", "delivered", "completed", "declined", "voided", "error", "created")},
        "recent": recent,
    }


async def due_requests(db: AsyncSession, provider: str, every: Any, limit: int = 50) -> list[uuid.UUID]:
    """Open requests of one provider that haven't been checked for ``every``."""

    now = core.utcnow()
    return list(
        (
            await db.execute(
                select(SignatureRequest.id)
                .where(
                    SignatureRequest.provider == provider,
                    SignatureRequest.status.in_(OPEN),
                    (SignatureRequest.last_checked_at.is_(None)) | (SignatureRequest.last_checked_at < now - every),
                )
                .order_by(SignatureRequest.last_checked_at.asc().nulls_first())
                .limit(limit)
            )
        ).scalars().all()
    )
