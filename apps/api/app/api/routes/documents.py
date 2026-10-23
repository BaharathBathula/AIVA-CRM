"""Documents: folders, file uploads with versions, and links to records.

Files are stored in PostgreSQL (``document_versions.data``) so they're backed
up with the rest of the workspace. Downloads are always served as
attachments, except a short list of safe types (images, PDF, plain text)
that can be previewed inline.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.core.config import get_settings
from app.db.session import get_db
from app.models.collaboration import Document, DocumentFolder, DocumentLink, DocumentVersion
from app.services.automation import record_audit
from app.services.lookups import names_for
from app.services.records import RECORD_TYPES, resolve, resolve_many

router = APIRouter(tags=["Documents"], dependencies=[Depends(require_feature("documents"))])

INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"}
BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".jar"}
LINKABLE = set(RECORD_TYPES) - {"document", "task", "product"}


def _id(value: Any) -> str | None:
    return str(value) if value else None


def _is_admin(principal: Principal) -> bool:
    return principal.can("admin.settings")


def _can_see(principal: Principal, document: Document) -> bool:
    return document.visibility != "private" or document.owner_user_id == principal.user_id or _is_admin(principal)


def _can_change(principal: Principal, document: Document) -> bool:
    return document.owner_user_id == principal.user_id or principal.can("records.delete") or _is_admin(principal)


def _clean_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f\\/]+", " ", name or "").strip()
    return name[:255] or "Untitled"


def _content_type(upload: UploadFile, name: str) -> str:
    declared = (upload.content_type or "").split(";")[0].strip().lower()
    guessed = mimetypes.guess_type(name)[0]
    if declared and declared != "application/octet-stream":
        return declared[:160]
    return (guessed or "application/octet-stream")[:160]


async def _read_upload(upload: UploadFile) -> tuple[bytes, str, str]:
    name = _clean_name(upload.filename or "file")
    extension = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if extension in BLOCKED_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"{extension} files can't be uploaded for security reasons.")
    limit = get_settings().document_max_mb * 1024 * 1024
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Files can be up to {get_settings().document_max_mb} MB.")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That file is empty.")
    return data, name, _content_type(upload, name)


async def _folder(db: AsyncSession, principal: Principal, folder_id: uuid.UUID | None) -> DocumentFolder | None:
    if folder_id is None:
        return None
    folder = await db.get(DocumentFolder, folder_id)
    if folder is None or folder.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found.")
    return folder


async def _document(db: AsyncSession, principal: Principal, document_id: uuid.UUID) -> Document:
    document = await db.get(Document, document_id)
    if document is None or document.organization_id != principal.organization_id or not _can_see(principal, document):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return document


async def document_out(db: AsyncSession, document: Document, principal: Principal, detail: bool = False) -> dict[str, Any]:
    org = document.organization_id
    owners = await names_for(db, org, "user", [document.owner_user_id])
    folder = await db.get(DocumentFolder, document.folder_id) if document.folder_id else None
    links = (
        await db.execute(select(DocumentLink).where(DocumentLink.document_id == document.id).order_by(DocumentLink.created_at))
    ).scalars().all()
    resolved = await resolve_many(db, org, [(l.record_type, l.record_id) for l in links])
    data = {
        "id": str(document.id),
        "name": document.name,
        "description": document.description,
        "folder_id": _id(document.folder_id),
        "folder_name": folder.name if folder else None,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "version": document.version,
        "visibility": document.visibility,
        "tags": document.tags or [],
        "owner_user_id": _id(document.owner_user_id),
        "owner_name": owners.get(str(document.owner_user_id)),
        "download_count": document.download_count,
        "previewable": document.content_type in INLINE_TYPES,
        "links": [
            {**resolved[(l.record_type, str(l.record_id))], "link_id": str(l.id), "record_id": str(l.record_id)}
            for l in links
            if (l.record_type, str(l.record_id)) in resolved
        ],
        "can_edit": _can_change(principal, document),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if detail:
        versions = (
            await db.execute(select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.version.desc()))
        ).scalars().all()
        uploaders = await names_for(db, org, "user", [v.uploaded_by_user_id for v in versions])
        data["versions"] = [
            {
                "version": v.version,
                "file_name": v.file_name,
                "content_type": v.content_type,
                "size_bytes": v.size_bytes,
                "note": v.note,
                "uploaded_by": uploaders.get(str(v.uploaded_by_user_id)),
                "created_at": v.created_at,
            }
            for v in versions
        ]
    return data


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


class FolderPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    parent_id: uuid.UUID | None = None


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    parent_id: uuid.UUID | None = None


async def _sibling_clash(db: AsyncSession, organization_id: uuid.UUID, parent_id: uuid.UUID | None, name: str, exclude: uuid.UUID | None = None) -> None:
    query = select(DocumentFolder.id).where(
        DocumentFolder.organization_id == organization_id,
        func.lower(DocumentFolder.name) == name.lower(),
        DocumentFolder.parent_id.is_(None) if parent_id is None else DocumentFolder.parent_id == parent_id,
    )
    if exclude:
        query = query.where(DocumentFolder.id != exclude)
    if (await db.execute(query)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a folder called “{name}” here.")


@router.get("/documents/folders")
async def list_folders(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    folders = (await db.execute(select(DocumentFolder).where(DocumentFolder.organization_id == org).order_by(DocumentFolder.name))).scalars().all()
    visible = or_(Document.visibility != "private", Document.owner_user_id == principal.user_id)
    if _is_admin(principal):
        visible = Document.id.is_not(None)
    counts = dict(
        (
            await db.execute(
                select(Document.folder_id, func.count()).where(Document.organization_id == org, visible).group_by(Document.folder_id)
            )
        ).all()
    )
    return {
        "items": [
            {"id": str(f.id), "name": f.name, "parent_id": _id(f.parent_id), "document_count": counts.get(f.id, 0), "created_at": f.created_at}
            for f in folders
        ],
        "unfiled_count": counts.get(None, 0),
        "total_count": sum(counts.values()),
    }


@router.post("/documents/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    await _folder(db, principal, payload.parent_id)
    name = _clean_name(payload.name)
    await _sibling_clash(db, principal.organization_id, payload.parent_id, name)
    folder = DocumentFolder(id=uuid.uuid4(), organization_id=principal.organization_id, name=name, parent_id=payload.parent_id, created_by_user_id=principal.user_id)
    db.add(folder)
    await db.commit()
    return {"id": str(folder.id), "name": folder.name, "parent_id": _id(folder.parent_id), "document_count": 0, "created_at": folder.created_at}


@router.patch("/documents/folders/{folder_id}")
async def update_folder(
    folder_id: uuid.UUID,
    payload: FolderUpdate,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    folder = await _folder(db, principal, folder_id)
    changes = payload.model_dump(exclude_unset=True)
    parent_id = changes.get("parent_id", folder.parent_id)
    if "parent_id" in changes and parent_id is not None:
        # Moving a folder inside itself (or one of its subfolders) would cut it off.
        current = await _folder(db, principal, parent_id)
        while current is not None:
            if current.id == folder.id:
                raise HTTPException(status.HTTP_409_CONFLICT, "A folder can't be moved inside itself.")
            current = await db.get(DocumentFolder, current.parent_id) if current.parent_id else None
    name = _clean_name(changes["name"]) if changes.get("name") else folder.name
    await _sibling_clash(db, principal.organization_id, parent_id, name, exclude=folder.id)
    folder.name = name
    folder.parent_id = parent_id
    await db.commit()
    return {"id": str(folder.id), "name": folder.name, "parent_id": _id(folder.parent_id)}


@router.delete("/documents/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.delete")),
    db: AsyncSession = Depends(get_db),
):
    folder = await _folder(db, principal, folder_id)
    documents = (await db.execute(select(func.count()).select_from(Document).where(Document.folder_id == folder.id))).scalar_one()
    children = (await db.execute(select(func.count()).select_from(DocumentFolder).where(DocumentFolder.parent_id == folder.id))).scalar_one()
    if documents or children:
        raise HTTPException(status.HTTP_409_CONFLICT, "Move or delete what's inside this folder first.")
    await db.delete(folder)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.get("/documents")
async def list_documents(
    folder_id: uuid.UUID | None = None,
    unfiled: bool = False,
    search: str | None = Query(default=None, max_length=200),
    mine: bool = False,
    record_type: str | None = Query(default=None, max_length=40),
    record_id: uuid.UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(Document).where(Document.organization_id == org)
    if not _is_admin(principal):
        query = query.where(or_(Document.visibility != "private", Document.owner_user_id == principal.user_id))
    if folder_id:
        query = query.where(Document.folder_id == folder_id)
    elif unfiled:
        query = query.where(Document.folder_id.is_(None))
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(Document.name.ilike(like), Document.description.ilike(like), cast(Document.tags, String).ilike(like)))
    if mine:
        query = query.where(Document.owner_user_id == principal.user_id)
    if record_type and record_id:
        query = query.join(DocumentLink, DocumentLink.document_id == Document.id).where(
            DocumentLink.record_type == record_type, DocumentLink.record_id == record_id
        )
    documents = (await db.execute(query.order_by(Document.updated_at.desc()).limit(limit))).scalars().all()
    return {
        "items": [await document_out(db, d, principal) for d in documents],
        "max_mb": get_settings().document_max_mb,
    }


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    folder_id: uuid.UUID | None = Form(default=None),
    name: str | None = Form(default=None, max_length=255),
    description: str | None = Form(default=None, max_length=5000),
    visibility: str = Form(default="organization", pattern="^(organization|private)$"),
    tags: str | None = Form(default=None, max_length=500),
    record_type: str | None = Form(default=None, max_length=40),
    record_id: uuid.UUID | None = Form(default=None),
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    await _folder(db, principal, folder_id)
    link = None
    if record_type and record_id:
        if record_type not in LINKABLE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Files can't be attached to that kind of record.")
        link = await resolve(db, org, record_type, record_id)
    data, file_name, content_type = await _read_upload(file)
    checksum = hashlib.sha256(data).hexdigest()
    document = Document(
        id=uuid.uuid4(),
        organization_id=org,
        name=_clean_name(name) if name and name.strip() else file_name,
        description=(description or "").strip() or None,
        folder_id=folder_id,
        content_type=content_type,
        size_bytes=len(data),
        version=1,
        checksum=checksum,
        visibility=visibility,
        tags=sorted({t.strip()[:40] for t in (tags or "").split(",") if t.strip()})[:20],
        owner_user_id=principal.user_id,
        download_count=0,
    )
    db.add(document)
    await db.flush()
    db.add(
        DocumentVersion(
            id=uuid.uuid4(),
            organization_id=org,
            document_id=document.id,
            version=1,
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(data),
            checksum=checksum,
            uploaded_by_user_id=principal.user_id,
            data=data,
        )
    )
    if link:
        db.add(DocumentLink(id=uuid.uuid4(), organization_id=org, document_id=document.id, record_type=record_type, record_id=record_id))
    record_audit(db, organization_id=org, action="upload", entity_type="document", entity_id=document.id, entity_label=document.name, changes={"size": len(data)})
    await db.commit()
    return await document_out(db, document, principal, detail=True)


@router.get("/documents/{document_id}")
async def get_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    return await document_out(db, await _document(db, principal, document_id), principal, detail=True)


class DocumentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    folder_id: uuid.UUID | None = None
    visibility: str | None = Field(default=None, pattern="^(organization|private)$")
    tags: list[str] | None = Field(default=None, max_length=20)


@router.patch("/documents/{document_id}")
async def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    document = await _document(db, principal, document_id)
    if not _can_change(principal, document):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an administrator can change this document.")
    changes = payload.model_dump(exclude_unset=True)
    if "folder_id" in changes:
        await _folder(db, principal, changes["folder_id"])
        document.folder_id = changes["folder_id"]
    if changes.get("name"):
        document.name = _clean_name(changes["name"])
    if "description" in changes:
        document.description = (changes["description"] or "").strip() or None
    if changes.get("visibility"):
        document.visibility = changes["visibility"]
    if changes.get("tags") is not None:
        document.tags = sorted({t.strip()[:40] for t in changes["tags"] if t.strip()})
    await db.commit()
    return await document_out(db, document, principal, detail=True)


@router.post("/documents/{document_id}/versions")
async def upload_version(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    note: str | None = Form(default=None, max_length=300),
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    document = await _document(db, principal, document_id)
    if not _can_change(principal, document):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an administrator can upload a new version.")
    data, file_name, content_type = await _read_upload(file)
    checksum = hashlib.sha256(data).hexdigest()
    if checksum == document.checksum:
        raise HTTPException(status.HTTP_409_CONFLICT, "That file is identical to the current version.")
    document.version += 1
    document.content_type = content_type
    document.size_bytes = len(data)
    document.checksum = checksum
    db.add(
        DocumentVersion(
            id=uuid.uuid4(),
            organization_id=document.organization_id,
            document_id=document.id,
            version=document.version,
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(data),
            checksum=checksum,
            note=(note or "").strip() or None,
            uploaded_by_user_id=principal.user_id,
            data=data,
        )
    )
    record_audit(db, organization_id=document.organization_id, action="new_version", entity_type="document", entity_id=document.id, entity_label=document.name, changes={"version": document.version})
    await db.commit()
    return await document_out(db, document, principal, detail=True)


def _disposition(kind: str, file_name: str) -> str:
    ascii_name = file_name.encode("ascii", "ignore").decode().replace('"', "") or "download"
    from urllib.parse import quote

    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: uuid.UUID,
    version: int | None = Query(default=None, ge=1),
    inline: bool = False,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    document = await _document(db, principal, document_id)
    file = (
        await db.execute(
            select(DocumentVersion)
            .options(undefer(DocumentVersion.data))
            .where(DocumentVersion.document_id == document.id, DocumentVersion.version == (version or document.version))
        )
    ).scalar_one_or_none()
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That version doesn't exist.")
    document.download_count += 1
    await db.commit()
    show_inline = inline and file.content_type in INLINE_TYPES
    risky = file.content_type in {"text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", "application/xml"}
    headers = {
        "Content-Disposition": _disposition("inline" if show_inline else "attachment", file.file_name),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if file.content_type != "application/pdf":
        # PDFs need the browser's viewer; everything else is locked down.
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    return Response(
        content=file.data,
        media_type="application/octet-stream" if risky else file.content_type,
        headers=headers,
    )


class LinkPayload(BaseModel):
    record_type: str = Field(max_length=40)
    record_id: uuid.UUID


@router.post("/documents/{document_id}/links", status_code=status.HTTP_201_CREATED)
async def link_document(
    document_id: uuid.UUID,
    payload: LinkPayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    document = await _document(db, principal, document_id)
    if payload.record_type not in LINKABLE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Files can't be attached to that kind of record.")
    await resolve(db, principal.organization_id, payload.record_type, payload.record_id)
    exists = (
        await db.execute(
            select(DocumentLink.id).where(
                DocumentLink.document_id == document.id, DocumentLink.record_type == payload.record_type, DocumentLink.record_id == payload.record_id
            )
        )
    ).first()
    if not exists:
        db.add(DocumentLink(id=uuid.uuid4(), organization_id=principal.organization_id, document_id=document.id, record_type=payload.record_type, record_id=payload.record_id))
        await db.commit()
    return await document_out(db, document, principal, detail=True)


@router.delete("/documents/{document_id}/links/{link_id}")
async def unlink_document(
    document_id: uuid.UUID,
    link_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    document = await _document(db, principal, document_id)
    link = await db.get(DocumentLink, link_id)
    if link is None or link.document_id != document.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found.")
    await db.delete(link)
    await db.commit()
    return await document_out(db, document, principal, detail=True)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    document = await _document(db, principal, document_id)
    if not _can_change(principal, document):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an administrator can delete this document.")
    record_audit(db, organization_id=document.organization_id, action="delete", entity_type="document", entity_id=document.id, entity_label=document.name)
    await db.delete(document)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
