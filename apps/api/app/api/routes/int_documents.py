"""Integration endpoints for documents, e-signature and scheduling:

* ``/cloud-files``: browse Google Drive, OneDrive and Dropbox with your own
  connection, attach files to records, copy them into AIVA Documents.
* ``/e-signatures``: send a quote or document with DocuSign, Adobe Acrobat Sign
  or PandaDoc (also a PandaDoc template) from a record, follow its status,
  void or resend it.
* ``/calendly``: your scheduling links, re-subscribing to bookings and the
  signing key for a workspace's own Calendly app.

Provider logic lives in ``app/integrations/providers`` (cloud_files, docusign,
calendly). Inbound webhooks arrive through the integrations hub.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.api.routes.documents import LINKABLE
from app.db.session import get_db
from app.integrations import core
from app.integrations.catalog import INTEGRATIONS
from app.integrations.core import IntegrationError
from app.integrations.providers import _doc_store, _signing, calendly, cloud_files, docusign
from app.models.collaboration import Document, DocumentLink
from app.models.contact import Contact
from app.models.integration_hub import ExternalFileLink, SignatureRequest
from app.services.automation import record_audit
from app.services.lookups import names_for
from app.services.records import resolve, resolve_many

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

FILES = [Depends(require_feature("documents"))]
SIGN = [Depends(require_feature("e_signature"))]
SCHEDULING = [Depends(require_feature("calendar_sync"))]


def _http(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _fail(db: AsyncSession, exc: IntegrationError) -> HTTPException:
    """Keep what the attempt recorded (activity log, a connection that needs reconnecting) and report the error."""

    await db.commit()
    return _http(exc)


def _linkable(record_type: str) -> str:
    if record_type not in LINKABLE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Files can't be attached to that kind of record.")
    return record_type


# ===========================================================================
# Cloud files: Google Drive, OneDrive, Dropbox
# ===========================================================================


def _file_key(key: str) -> str:
    if key not in cloud_files.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown file provider.")
    return key


@router.get("/cloud-files/providers", dependencies=FILES)
async def file_providers(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Which file services are on, and whether you've connected each one."""

    org = principal.organization_id
    items = []
    for key in cloud_files.PROVIDERS:
        item = INTEGRATIONS[key]
        enabled = await core.is_enabled(db, org, key)
        connection = await core.connection_for(db, org, key, principal.user_id)
        config = await core.workspace_config(db, org, key)
        items.append(
            {
                **cloud_files.provider_summary(key),
                "available": principal.has(item.feature),
                "enabled": enabled,
                "connected": bool(connection and connection.status == "connected"),
                "status": "off" if not enabled else (connection.status if connection else "not_connected"),
                "account_label": connection.account_label if connection else None,
                "allow_copy": config.get("allow_copy", True) is not False,
            }
        )
    return {"items": items, "max_mb": _doc_store.max_bytes() // (1024 * 1024)}


@router.get("/cloud-files/{key}/browse", dependencies=FILES)
async def browse_files(
    key: str,
    q: str | None = Query(default=None, max_length=200),
    folder_id: str | None = Query(default=None, max_length=500),
    view: Literal["files", "recent", "shared"] = "files",
    cursor: str | None = Query(default=None, max_length=2000),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    """Search or browse your own Drive / OneDrive / Dropbox."""

    key = _file_key(key)
    try:
        connection = await core.active_connection(db, principal.organization_id, key, principal.user_id)
        page = await cloud_files.browse(db, connection, query=q, folder_id=folder_id, view=view, cursor=cursor)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return page


async def _links_out(db: AsyncSession, principal: Principal, rows: list[ExternalFileLink], with_record: bool = False) -> list[dict[str, Any]]:
    names = await names_for(db, principal.organization_id, "user", [r.added_by_user_id for r in rows])
    records = await resolve_many(db, principal.organization_id, [(r.record_type, r.record_id) for r in rows]) if with_record else {}
    out = []
    for row in rows:
        data = cloud_files.link_out(row, names, records.get((row.record_type, str(row.record_id))) if with_record else None)
        data["can_remove"] = _can_remove(principal, row)
        out.append(data)
    return out


def _can_remove(principal: Principal, link: ExternalFileLink) -> bool:
    return link.added_by_user_id == principal.user_id or principal.can("records.delete") or principal.can("admin.settings")


@router.get("/cloud-files/links", dependencies=FILES)
async def record_file_links(
    record_type: str = Query(max_length=40),
    record_id: uuid.UUID = Query(),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    await resolve(db, principal.organization_id, _linkable(record_type), record_id)
    rows = (
        await db.execute(
            select(ExternalFileLink)
            .where(ExternalFileLink.organization_id == principal.organization_id, ExternalFileLink.record_type == record_type, ExternalFileLink.record_id == record_id)
            .order_by(ExternalFileLink.created_at.desc())
        )
    ).scalars().all()
    return {"items": await _links_out(db, principal, list(rows))}


class AttachPayload(BaseModel):
    provider: str = Field(max_length=30)
    file_id: str = Field(min_length=1, max_length=500)
    record_type: str = Field(max_length=40)
    record_id: uuid.UUID


@router.post("/cloud-files/links", dependencies=FILES)
async def attach_file(payload: AttachPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    """Attach a file (or folder) from your connected account to a record. Details come from the provider, not the browser."""

    org = principal.organization_id
    key = _file_key(payload.provider)
    record = await resolve(db, org, _linkable(payload.record_type), payload.record_id)
    try:
        connection = await core.active_connection(db, org, key, principal.user_id)
        item = await cloud_files.metadata(db, connection, payload.file_id)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    existing = (
        await db.execute(
            select(ExternalFileLink).where(
                ExternalFileLink.organization_id == org,
                ExternalFileLink.provider == key,
                ExternalFileLink.file_id == str(item["id"]),
                ExternalFileLink.record_type == payload.record_type,
                ExternalFileLink.record_id == payload.record_id,
            )
        )
    ).scalar_one_or_none()
    created = existing is None
    if existing is not None:
        cloud_files.apply_metadata(existing, item)
        link = existing
    else:
        link = cloud_files.new_link(org, key, item, payload.record_type, payload.record_id, principal.user_id)
        try:
            async with db.begin_nested():
                db.add(link)
                await db.flush()
        except IntegrityError:
            link = (
                await db.execute(
                    select(ExternalFileLink).where(
                        ExternalFileLink.organization_id == org,
                        ExternalFileLink.provider == key,
                        ExternalFileLink.file_id == str(item["id"]),
                        ExternalFileLink.record_type == payload.record_type,
                        ExternalFileLink.record_id == payload.record_id,
                    )
                )
            ).scalar_one()
            created = False
        if created:
            core.log(
                db, org, key, "file_attached", f"Attached “{link.name}” to {record['kind'].lower()} {record['label']}.", level="success",
                connection=connection, user_id=principal.user_id, detail={"link_id": str(link.id), "record_type": payload.record_type, "record_id": str(payload.record_id)},
            )
    await db.commit()
    return {"created": created, "link": (await _links_out(db, principal, [link]))[0]}


async def _link(db: AsyncSession, principal: Principal, link_id: uuid.UUID) -> tuple[ExternalFileLink, dict[str, str]]:
    link = await db.get(ExternalFileLink, link_id)
    if link is None or link.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File link not found.")
    record = await resolve(db, principal.organization_id, link.record_type, link.record_id)
    return link, record


@router.post("/cloud-files/links/{link_id}/refresh", dependencies=FILES)
async def refresh_file_link(link_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    """Re-read the file's name, size and address with your own connection."""

    link, _ = await _link(db, principal, link_id)
    try:
        connection = await core.active_connection(db, principal.organization_id, link.provider, principal.user_id)
        cloud_files.apply_metadata(link, await cloud_files.metadata(db, connection, link.file_id))
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return (await _links_out(db, principal, [link]))[0]


class CopyPayload(BaseModel):
    format: Literal["pdf", "office"] = "pdf"


@router.post("/cloud-files/links/{link_id}/copy", dependencies=FILES)
async def copy_file_link(
    link_id: uuid.UUID,
    payload: CopyPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    """Copy the file into AIVA Documents, linked to the same record. Google files are exported (PDF or Office)."""

    org = principal.organization_id
    link, record = await _link(db, principal, link_id)
    if link.mime_type == "folder":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Folders can't be copied; pick a file inside it.")
    config = await core.workspace_config(db, org, link.provider)
    if config.get("allow_copy", True) is False:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your admin has turned off copying files into AIVA for this integration.")
    try:
        connection = await core.active_connection(db, org, link.provider, principal.user_id)
        name, content_type, data, item = await cloud_files.download(db, connection, link.file_id, fmt=payload.format)
        cloud_files.apply_metadata(link, item)
        document = await _doc_store.save_document(
            db,
            organization_id=org,
            owner_user_id=principal.user_id,
            name=name,
            data=data,
            content_type=content_type,
            links=[(link.record_type, link.record_id)],
            description=f"Copied from {INTEGRATIONS[link.provider].name}" + (f": {link.web_url}" if link.web_url else "."),
            tags=[link.provider.replace("_", "-")],
        )
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    core.log(
        db, org, link.provider, "file_copied", f"Copied “{document.name}” into Documents for {record['kind'].lower()} {record['label']}.", level="success",
        connection=connection, user_id=principal.user_id, detail={"link_id": str(link.id), "document_id": str(document.id)},
    )
    await db.commit()
    return {"document": _doc_store.summary(document)}


@router.delete("/cloud-files/links/{link_id}", dependencies=FILES)
async def remove_file_link(link_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    link, record = await _link(db, principal, link_id)
    if not _can_remove(principal, link):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who attached this file or an admin can remove it.")
    core.log(
        db, principal.organization_id, link.provider, "file_detached", f"Removed “{link.name}” from {record['kind'].lower()} {record['label']}.",
        user_id=principal.user_id, detail={"record_type": link.record_type, "record_id": str(link.record_id)},
    )
    await db.delete(link)
    await db.commit()
    return {"ok": True}


# ===========================================================================
# E-signatures (DocuSign, Adobe Acrobat Sign, PandaDoc)
# ===========================================================================


SIGNABLE = {"opportunity", "quote", "account", "contact", "lead", "document", "sales_order", "project", "vendor", "case"}


async def _request(db: AsyncSession, principal: Principal, request_id: uuid.UUID, lock: bool = False) -> SignatureRequest:
    query = select(SignatureRequest).where(SignatureRequest.id == request_id, SignatureRequest.organization_id == principal.organization_id)
    row = (await db.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signature request not found.")
    if row.record_type and row.record_id and row.sent_by_user_id != principal.user_id:
        await resolve(db, principal.organization_id, row.record_type, row.record_id)  # record-level access
    return row


@router.get("/e-signatures", dependencies=SIGN)
async def list_signatures(
    record_type: str | None = Query(default=None, max_length=40),
    record_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(created|sent|delivered|completed|declined|voided|error|open)$"),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(SignatureRequest).where(SignatureRequest.organization_id == org)
    if record_type and record_id:
        await resolve(db, org, record_type, record_id)
        if record_type == "quote":
            query = query.where(or_(SignatureRequest.quote_id == record_id, (SignatureRequest.record_type == "quote") & (SignatureRequest.record_id == record_id)))
        elif record_type == "document":
            query = query.where(or_(SignatureRequest.document_id == record_id, (SignatureRequest.record_type == "document") & (SignatureRequest.record_id == record_id)))
        else:
            query = query.where(SignatureRequest.record_type == record_type, SignatureRequest.record_id == record_id)
    if status_filter == "open":
        query = query.where(SignatureRequest.status.in_(_signing.OPEN))
    elif status_filter:
        query = query.where(SignatureRequest.status == status_filter)
    rows = (await db.execute(query.order_by(SignatureRequest.created_at.desc()).limit(limit))).scalars().all()
    items = await _signing.requests_out(db, org, list(rows))
    if not (record_type and record_id) and not principal.can("admin.settings"):
        # Outside a record, people see requests they sent or on records they can open.
        items = [i for i in items if i["sent_by_user_id"] == str(principal.user_id) or i["record"] is not None]
    providers = await _signing.provider_states(db, org)
    return {
        "items": items,
        "enabled": any(p["enabled"] for p in providers),
        "connected": any(p["enabled"] and p["connected"] for p in providers),
        "providers": providers,
        "can_send": principal.can("records.write"),
    }


@router.get("/e-signatures/sources", dependencies=SIGN)
async def signature_sources(
    record_type: str = Query(max_length=40),
    record_id: uuid.UUID = Query(),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    """What can be sent from this record (its quotes and documents) and who would likely sign."""

    from app.models.lead import Lead
    from app.models.opportunity import Opportunity
    from app.models.platform import Quote

    org = principal.organization_id
    if record_type not in SIGNABLE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Signatures can't be requested from that kind of record.")
    record = await resolve(db, org, record_type, record_id)
    can_quotes = principal.has("cpq") and principal.can("sales.quotes")
    quotes: list[Any] = []
    contacts: list[Contact] = []
    people: list[dict[str, str]] = []
    if record_type == "quote":
        quote_row = await db.get(Quote, record_id)
        quotes = [quote_row] if quote_row else []
    elif record_type in {"opportunity", "account", "contact"}:
        column = {"opportunity": Quote.opportunity_id, "account": Quote.account_id, "contact": Quote.contact_id}[record_type]
        quotes = list((await db.execute(select(Quote).where(Quote.organization_id == org, column == record_id).order_by(Quote.created_at.desc()).limit(20))).scalars())
    if record_type == "contact":
        contact = await db.get(Contact, record_id)
        contacts = [contact] if contact else []
    elif record_type == "quote" and quotes and quotes[0].contact_id:
        contact = await db.get(Contact, quotes[0].contact_id)
        contacts = [contact] if contact else []
    elif record_type == "opportunity":
        opportunity = await db.get(Opportunity, record_id)
        if opportunity is not None:
            if opportunity.primary_contact_id:
                primary = await db.get(Contact, opportunity.primary_contact_id)
                contacts = [primary] if primary else []
            contacts += list(
                (
                    await db.execute(
                        select(Contact).where(Contact.organization_id == org, Contact.account_id == opportunity.account_id, Contact.is_archived.is_(False)).limit(10)
                    )
                ).scalars()
            )
    elif record_type == "account":
        contacts = list(
            (
                await db.execute(
                    select(Contact).where(Contact.organization_id == org, Contact.account_id == record_id, Contact.is_archived.is_(False))
                    .order_by(Contact.is_primary.desc(), Contact.last_name).limit(10)
                )
            ).scalars()
        )
    elif record_type == "lead":
        lead = await db.get(Lead, record_id)
        if lead is not None and lead.email:
            people.append({"name": f"{lead.first_name} {lead.last_name}".strip() or lead.email, "email": lead.email, "source": "Lead"})
    seen: set[str] = {p["email"].lower() for p in people}
    for contact in contacts:
        if contact and contact.email and contact.email.lower() not in seen:
            seen.add(contact.email.lower())
            people.append({"name": f"{contact.first_name} {contact.last_name}".strip() or contact.email, "email": contact.email, "source": "Contact"})

    doc_query = select(Document).where(Document.organization_id == org)
    if record_type == "document":
        doc_query = doc_query.where(Document.id == record_id)
    else:
        doc_query = doc_query.join(DocumentLink, DocumentLink.document_id == Document.id).where(DocumentLink.record_type == record_type, DocumentLink.record_id == record_id)
    if not principal.can("admin.settings"):
        doc_query = doc_query.where(or_(Document.visibility != "private", Document.owner_user_id == principal.user_id))
    documents = (await db.execute(doc_query.order_by(Document.updated_at.desc()).limit(50))).scalars().all()

    providers = await _signing.provider_states(db, org)
    ready = [p["key"] for p in providers if p["enabled"] and p["connected"]] or list(_signing.SIGN_KEYS)

    def signable_by(document: Document) -> dict[str, bool]:
        return {key: _signing.module(key).signable(document.name, document.content_type, document.size_bytes) for key in ready}

    return {
        "record": record,
        "quotes": [
            {"id": str(q.id), "label": f"Q-{q.number} · {q.name}", "status": q.status, "total": str(q.total), "currency": q.currency, "lines": len(q.line_items or [])}
            for q in quotes
        ]
        if can_quotes
        else [],
        "can_send_quotes": can_quotes,
        "documents": [
            {"id": str(d.id), "name": d.name, "content_type": d.content_type, "size_bytes": d.size_bytes, "signable": any(by.values()), "signable_by": by}
            for d in documents
            for by in [signable_by(d)]
        ],
        "recipients": people[:10],
        "me": {"name": principal.full_name, "email": principal.email},
        "providers": providers,
    }


class RecipientPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=320)
    role: Literal["signer", "cc"] = "signer"
    routing_order: int = Field(default=1, ge=1, le=20)


class SendPayload(BaseModel):
    record_type: str = Field(max_length=40)
    record_id: uuid.UUID
    # docusign | adobe_sign | pandadoc; empty picks the first one that's connected (DocuSign first).
    provider: str | None = Field(default=None, pattern="^(docusign|adobe_sign|pandadoc)$")
    quote_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    # PandaDoc only: create the document from this template (a quote fills its pricing table).
    template_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    subject: str | None = Field(default=None, max_length=100)
    message: str | None = Field(default=None, max_length=10000)
    recipients: list[RecipientPayload] = Field(min_length=1, max_length=20)


@router.post("/e-signatures", status_code=status.HTTP_201_CREATED, dependencies=SIGN)
async def send_for_signature(payload: SendPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    from app.models.platform import Quote

    org = principal.organization_id
    if payload.record_type not in SIGNABLE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Signatures can't be requested from that kind of record.")
    if payload.template_id:
        if payload.provider not in (None, "pandadoc"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Templates are a PandaDoc option.")
        if payload.document_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a template or a document, not both.")
    elif bool(payload.quote_id) == bool(payload.document_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose one quote or one document to send.")
    if principal.api_key_id is None and principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support access can't send documents for signature.")
    await resolve(db, org, payload.record_type, payload.record_id)
    quote_row = None
    document = None
    if payload.quote_id:
        if not (principal.has("cpq") and principal.can("sales.quotes")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You need permission to send quotes.")
        await resolve(db, org, "quote", payload.quote_id)
        quote_row = await db.get(Quote, payload.quote_id)
        if quote_row.status in {"declined", "expired"}:
            raise HTTPException(status.HTTP_409_CONFLICT, f"This quote is {quote_row.status}.")
        if quote_row.status == "draft" and principal.has("approvals"):
            from app.services.approvals import matching_quote_rule

            rule = await matching_quote_rule(db, quote_row)
            if rule is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, f"This quote needs approval before it's sent ({rule.name}). Submit it for approval first.")
    elif payload.document_id:
        document = await db.get(Document, payload.document_id)
        if (
            document is None
            or document.organization_id != org
            or (document.visibility == "private" and document.owner_user_id != principal.user_id and not principal.can("admin.settings"))
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    provider = payload.provider or ("pandadoc" if payload.template_id else None)
    if provider is None:
        ready = [p["key"] for p in await _signing.provider_states(db, org) if p["enabled"] and p["connected"]]
        provider = ready[0] if ready else docusign.KEY
    try:
        connection = await core.active_connection(db, org, provider)
        kwargs: dict[str, Any] = {"template_id": payload.template_id} if provider != docusign.KEY else {}
        row = await _signing.module(provider).send(
            db,
            principal=principal,
            connection=connection,
            record_type=payload.record_type,
            record_id=payload.record_id,
            subject=payload.subject or "",
            message=payload.message,
            recipients=[r.model_dump() for r in payload.recipients],
            quote_row=quote_row,
            document=document,
            **kwargs,
        )
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return (await _signing.requests_out(db, org, [row]))[0]


class HmacPayload(BaseModel):
    hmac_key: str | None = Field(default=None, max_length=200)


@router.put("/e-signatures/connect-key", dependencies=SIGN)
async def set_connect_key(payload: HmacPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """The DocuSign Connect HMAC key; once saved, pushes without a valid signature are refused."""

    from app.models.integration_hub import IntegrationToggle

    org = principal.organization_id
    toggle = await core.toggle_row(db, org, docusign.KEY)
    if toggle is None:
        toggle = IntegrationToggle(id=uuid.uuid4(), organization_id=org, provider=docusign.KEY, enabled=False)
        db.add(toggle)
    value = (payload.hmac_key or "").strip()
    if value and len(value) < 16:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That doesn't look like a DocuSign Connect HMAC key.")
    core.set_secret(toggle, "connect_hmac_key", value or None)
    core.log(db, org, docusign.KEY, "connect_key", "Connect HMAC key saved; unsigned pushes will be refused." if value else "Connect HMAC key removed.", user_id=principal.user_id)
    record_audit(db, organization_id=org, action="integration_secret_updated", entity_type="integration", entity_label="DocuSign Connect HMAC key")
    await db.commit()
    return {"hmac_configured": bool(value)}


@router.get("/e-signatures/{request_id}", dependencies=SIGN)
async def get_signature(request_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    row = await _request(db, principal, request_id)
    return (await _signing.requests_out(db, principal.organization_id, [row]))[0]


@router.post("/e-signatures/{request_id}/refresh", dependencies=SIGN)
async def refresh_signature(request_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    row = await _request(db, principal, request_id, lock=True)
    if row.status in _signing.OPEN and row.envelope_id:
        try:
            connection = await core.active_connection(db, principal.organization_id, row.provider)
            await _signing.module(row.provider).refresh(db, connection, row)
        except IntegrationError as exc:
            raise await _fail(db, exc) from exc
        await db.commit()
    return (await _signing.requests_out(db, principal.organization_id, [row]))[0]


class VoidPayload(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


def _can_manage_request(principal: Principal, row: SignatureRequest) -> bool:
    return row.sent_by_user_id == principal.user_id or principal.can("records.delete") or principal.can("admin.settings")


@router.post("/e-signatures/{request_id}/void", dependencies=SIGN)
async def void_signature(request_id: uuid.UUID, payload: VoidPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _request(db, principal, request_id, lock=True)
    if not _can_manage_request(principal, row):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the sender or an admin can void this request.")
    try:
        connection = await core.active_connection(db, principal.organization_id, row.provider)
        await _signing.module(row.provider).void(db, connection, row, payload.reason.strip(), principal.user_id)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    record_audit(db, organization_id=principal.organization_id, action="signature_voided", entity_type=row.record_type or "document", entity_id=row.record_id, entity_label=row.document_name, changes={"reason": payload.reason, "provider": row.provider})
    await db.commit()
    return (await _signing.requests_out(db, principal.organization_id, [row]))[0]


@router.post("/e-signatures/{request_id}/resend", dependencies=SIGN)
async def resend_signature(request_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _request(db, principal, request_id)
    try:
        connection = await core.active_connection(db, principal.organization_id, row.provider)
        await _signing.module(row.provider).resend(db, connection, row, principal.user_id)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return (await _signing.requests_out(db, principal.organization_id, [row]))[0]


# ===========================================================================
# Calendly
# ===========================================================================


@router.get("/calendly/status", dependencies=SCHEDULING)
async def calendly_status(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Cheap check (no Calendly call) so records only offer scheduling links when Calendly is on and connected."""

    org = principal.organization_id
    connection = await core.connection_for(db, org, calendly.KEY)
    return {
        "enabled": await core.is_enabled(db, org, calendly.KEY),
        "connected": bool(connection and connection.status == "connected"),
    }


@router.get("/calendly/links", dependencies=SCHEDULING)
async def calendly_links(
    record_type: str | None = Query(default=None, pattern="^(lead|contact)$"),
    record_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    """Your Calendly scheduling links; with a lead or contact, links come with their name and email filled in."""

    org = principal.organization_id
    person: dict[str, str] | None = None
    if record_type and record_id:
        await resolve(db, org, record_type, record_id)
        from app.models.lead import Lead

        row = await db.get(Contact if record_type == "contact" else Lead, record_id)
        if row is not None:
            person = {"name": f"{row.first_name} {row.last_name}".strip(), "email": row.email or ""}
    try:
        connection = await core.active_connection(db, org, calendly.KEY)
        links = await calendly.links_for(db, connection, principal)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    for item in links["items"]:
        item["share_url"] = calendly.scheduling_link_for(item, person["name"] if person else None, person["email"] if person else None)
    return {**links, "person": person}


@router.post("/calendly/resubscribe", dependencies=SCHEDULING)
async def calendly_resubscribe(principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    from app.api.routes.integration_hub import integration_detail

    try:
        connection = await core.active_connection(db, principal.organization_id, calendly.KEY)
        await calendly.subscribe(db, connection)
    except IntegrationError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return await integration_detail(calendly.KEY, principal, db)


class SigningKeyPayload(BaseModel):
    signing_key: str | None = Field(default=None, max_length=200)


@router.put("/calendly/signing-key", dependencies=SCHEDULING)
async def calendly_signing_key(payload: SigningKeyPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """The webhook signing key of the workspace's own Calendly OAuth app (Calendly shows it in the developer console)."""

    from app.api.routes.integration_hub import integration_detail
    from app.models.integration_hub import IntegrationToggle

    org = principal.organization_id
    toggle = await core.toggle_row(db, org, calendly.KEY)
    if toggle is None:
        toggle = IntegrationToggle(id=uuid.uuid4(), organization_id=org, provider=calendly.KEY, enabled=False)
        db.add(toggle)
    value = (payload.signing_key or "").strip()
    if value and len(value) < 16:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That doesn't look like a Calendly webhook signing key.")
    core.set_secret(toggle, "app_webhook_signing_key", value or None)
    core.log(db, org, calendly.KEY, "signing_key", "Webhook signing key saved; unsigned bookings will be refused." if value else "Webhook signing key removed.", user_id=principal.user_id)
    record_audit(db, organization_id=org, action="integration_secret_updated", entity_type="integration", entity_label="Calendly webhook signing key")
    await db.commit()
    return await integration_detail(calendly.KEY, principal, db)
