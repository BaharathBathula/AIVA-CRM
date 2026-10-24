"""Saving files that came from another service (a Drive file, a signed DocuSign
PDF) into AIVA Documents, with the same rules as an upload: size limit, blocked
file types, the workspace's default visibility, a first version and a link to
the record it belongs to.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.documents import BLOCKED_EXTENSIONS, LINKABLE, _clean_name
from app.core.config import get_settings
from app.integrations.core import IntegrationError
from app.models.collaboration import Document, DocumentLink, DocumentVersion
from app.services.automation import record_audit


def max_bytes() -> int:
    return get_settings().document_max_mb * 1024 * 1024


def too_big_message() -> str:
    return f"That file is larger than {get_settings().document_max_mb} MB, the most AIVA Documents can hold."


def content_type_for(name: str, declared: str | None) -> str:
    declared = (declared or "").split(";")[0].strip().lower()
    if declared and declared != "application/octet-stream":
        return declared[:160]
    return (mimetypes.guess_type(name)[0] or "application/octet-stream")[:160]


async def save_document(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    owner_user_id: uuid.UUID | None,
    name: str,
    data: bytes,
    content_type: str | None = None,
    links: list[tuple[str, uuid.UUID]] | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> Document:
    """Create a document (version 1) and link it to records. Raises IntegrationError on bad input."""

    file_name = _clean_name(name)
    extension = ("." + file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else ""
    if extension in BLOCKED_EXTENSIONS:
        raise IntegrationError(f"{extension} files can't be stored in AIVA for security reasons.", 415)
    if not data:
        raise IntegrationError("That file is empty.", 422)
    if len(data) > max_bytes():
        raise IntegrationError(too_big_message(), 413)
    kind = content_type_for(file_name, content_type)

    from app.services import workspace_settings

    visibility = (await workspace_settings.get(db, organization_id, "sharing")).document_visibility
    checksum = hashlib.sha256(data).hexdigest()
    document = Document(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name=file_name,
        description=(description or "").strip()[:5000] or None,
        folder_id=None,
        content_type=kind,
        size_bytes=len(data),
        version=1,
        checksum=checksum,
        visibility=visibility,
        tags=sorted({t.strip()[:40] for t in (tags or []) if t and t.strip()})[:20],
        owner_user_id=owner_user_id,
        download_count=0,
    )
    db.add(document)
    await db.flush()
    db.add(
        DocumentVersion(
            id=uuid.uuid4(),
            organization_id=organization_id,
            document_id=document.id,
            version=1,
            file_name=file_name,
            content_type=kind,
            size_bytes=len(data),
            checksum=checksum,
            uploaded_by_user_id=owner_user_id,
            data=data,
        )
    )
    seen: set[tuple[str, uuid.UUID]] = set()
    for record_type, record_id in links or []:
        if record_type not in LINKABLE or (record_type, record_id) in seen:
            continue
        seen.add((record_type, record_id))
        db.add(DocumentLink(id=uuid.uuid4(), organization_id=organization_id, document_id=document.id, record_type=record_type, record_id=record_id))
    record_audit(
        db,
        organization_id=organization_id,
        action="upload",
        entity_type="document",
        entity_id=document.id,
        entity_label=document.name,
        changes={"size": len(data), "source": (tags or ["integration"])[0]},
        actor_user_id=owner_user_id,
    )
    return document


async def latest_file(db: AsyncSession, document: Document) -> DocumentVersion | None:
    from sqlalchemy.orm import undefer

    return (
        await db.execute(
            select(DocumentVersion)
            .options(undefer(DocumentVersion.data))
            .where(DocumentVersion.document_id == document.id, DocumentVersion.version == document.version)
        )
    ).scalar_one_or_none()


def summary(document: Document) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "name": document.name,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "href": f"/documents/{document.id}",
    }
