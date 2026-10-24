"""Google Drive, Microsoft OneDrive and Dropbox: find files, attach them to records
and copy them into AIVA Documents.

Each person connects their own account, so searching and copying always use
the signed-in person's access: a file someone else attached opens for you only
if the provider lets you see it. Links store the file's name, type, size and
web address so records show them without calling the provider.

Provider APIs used:

* Google Drive v3: ``files.list`` (search, folders, recent, shared with me),
  ``files.get`` (metadata and ``alt=media`` downloads) and ``files.export``
  for Google Docs, Sheets, Slides and Drawings.
* Microsoft Graph: ``/me/drive/root/search``, ``children``, ``recent``,
  ``sharedWithMe`` and ``/drives/{drive}/items/{item}/content``.
* Dropbox: ``files/search_v2``, ``files/list_folder``, ``files/get_metadata``,
  ``files/download`` and ``sharing/list_shared_links``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _doc_store
from app.models.integration_hub import ExternalFileLink, IntegrationConnection

PROVIDERS = ("google_drive", "onedrive", "dropbox")

DRIVE = "https://www.googleapis.com/drive/v3"
GRAPH = "https://graph.microsoft.com/v1.0"
DBX = "https://api.dropboxapi.com/2"
DBX_CONTENT = "https://content.dropboxapi.com/2"

PAGE = 30
GOOGLE_FOLDER = "application/vnd.google-apps.folder"
GOOGLE_SHORTCUT = "application/vnd.google-apps.shortcut"
DRIVE_FIELDS = "id,name,mimeType,size,modifiedTime,webViewLink,owners(displayName),shortcutDetails(targetId,targetMimeType)"
GRAPH_SELECT = "id,name,size,file,folder,package,webUrl,lastModifiedDateTime,parentReference,remoteItem,createdBy"

OFFICE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
# Google's own formats can't be downloaded as-is; they're exported.
GOOGLE_EXPORTS: dict[str, dict[str, tuple[str, str]]] = {
    "application/vnd.google-apps.document": {"pdf": ("application/pdf", ".pdf"), "office": (OFFICE["docx"], ".docx")},
    "application/vnd.google-apps.spreadsheet": {"pdf": ("application/pdf", ".pdf"), "office": (OFFICE["xlsx"], ".xlsx")},
    "application/vnd.google-apps.presentation": {"pdf": ("application/pdf", ".pdf"), "office": (OFFICE["pptx"], ".pptx")},
    "application/vnd.google-apps.drawing": {"pdf": ("application/pdf", ".pdf"), "office": ("image/png", ".png")},
}


# ---------------------------------------------------------------------------
# Normalising provider items
# ---------------------------------------------------------------------------


def kind_label(mime: str | None, name: str = "", is_folder: bool = False) -> str:
    if is_folder:
        return "Folder"
    mime = (mime or "").lower()
    google = {
        "application/vnd.google-apps.document": "Google Doc",
        "application/vnd.google-apps.spreadsheet": "Google Sheet",
        "application/vnd.google-apps.presentation": "Google Slides",
        "application/vnd.google-apps.drawing": "Google Drawing",
        "application/vnd.google-apps.form": "Google Form",
    }
    if mime in google:
        return google[mime]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if mime == "application/pdf" or extension == "pdf":
        return "PDF"
    if "wordprocessing" in mime or mime == "application/msword" or extension in {"doc", "docx"}:
        return "Word"
    if "spreadsheet" in mime or mime == "application/vnd.ms-excel" or extension in {"xls", "xlsx", "csv"}:
        return "Spreadsheet"
    if "presentation" in mime or extension in {"ppt", "pptx", "key"}:
        return "Presentation"
    if mime.startswith("image/"):
        return "Image"
    if mime.startswith("video/"):
        return "Video"
    if mime.startswith("audio/"):
        return "Audio"
    if mime.startswith("text/"):
        return "Text"
    if extension in {"zip", "rar", "7z", "gz"}:
        return "Archive"
    return "File"


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _drive_item(raw: dict[str, Any]) -> dict[str, Any]:
    mime = raw.get("mimeType")
    file_id = raw.get("id")
    if mime == GOOGLE_SHORTCUT and raw.get("shortcutDetails"):
        file_id = raw["shortcutDetails"].get("targetId") or file_id
        mime = raw["shortcutDetails"].get("targetMimeType") or mime
    folder = mime == GOOGLE_FOLDER
    name = raw.get("name") or "Untitled"
    owners = raw.get("owners") or []
    return {
        "id": file_id,
        "name": name,
        "mime_type": "folder" if folder else mime,
        "size": _int(raw.get("size")),
        "modified_at": raw.get("modifiedTime"),
        "web_url": raw.get("webViewLink") or (f"https://drive.google.com/drive/folders/{file_id}" if folder else f"https://drive.google.com/file/d/{file_id}/view"),
        "is_folder": folder,
        "kind": kind_label(mime, name, folder),
        "owner": owners[0].get("displayName") if owners else None,
        "path": None,
        "copyable": not folder and (not (mime or "").startswith("application/vnd.google-apps.") or mime in GOOGLE_EXPORTS),
    }


def _graph_ids(raw: dict[str, Any]) -> tuple[str | None, str]:
    remote = raw.get("remoteItem") or {}
    drive_id = (remote.get("parentReference") or {}).get("driveId") or (raw.get("parentReference") or {}).get("driveId")
    return drive_id, remote.get("id") or raw["id"]


def _graph_item(raw: dict[str, Any]) -> dict[str, Any]:
    remote = raw.get("remoteItem") or {}
    drive_id, item_id = _graph_ids(raw)
    folder = "folder" in raw or "folder" in remote or "package" in raw
    mime = (raw.get("file") or remote.get("file") or {}).get("mimeType")
    name = raw.get("name") or remote.get("name") or "Untitled"
    created = (raw.get("createdBy") or remote.get("createdBy") or {}).get("user") or {}
    return {
        "id": f"{drive_id}:{item_id}" if drive_id else item_id,
        "name": name,
        "mime_type": "folder" if folder else mime,
        "size": _int(raw.get("size") if raw.get("size") is not None else remote.get("size")),
        "modified_at": raw.get("lastModifiedDateTime") or remote.get("lastModifiedDateTime"),
        "web_url": raw.get("webUrl") or remote.get("webUrl"),
        "is_folder": folder,
        "kind": kind_label(mime, name, folder),
        "owner": created.get("displayName"),
        "path": ((raw.get("parentReference") or {}).get("path") or "").replace("/drive/root:", "") or None,
        "copyable": not folder,
    }


def _dropbox_preview(path_display: str | None, name: str, folder: bool) -> str:
    if not path_display:
        return "https://www.dropbox.com/home"
    if folder:
        return f"https://www.dropbox.com/home{quote(path_display)}"
    parent = path_display.rsplit("/", 1)[0]
    return f"https://www.dropbox.com/home{quote(parent)}?preview={quote(name)}"


def _dropbox_item(raw: dict[str, Any]) -> dict[str, Any]:
    folder = raw.get(".tag") == "folder"
    name = raw.get("name") or "Untitled"
    return {
        "id": raw.get("id"),
        "name": name,
        "mime_type": "folder" if folder else _doc_store.content_type_for(name, None),
        "size": _int(raw.get("size")),
        "modified_at": raw.get("server_modified") or raw.get("client_modified"),
        "web_url": _dropbox_preview(raw.get("path_display"), name, folder),
        "is_folder": folder,
        "kind": kind_label(None, name, folder),
        "owner": None,
        "path": raw.get("path_display"),
        "copyable": not folder,
    }


def _escape_drive(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _graph_item_url(file_id: str) -> str:
    if ":" in file_id:
        drive_id, item_id = file_id.split(":", 1)
        return f"{GRAPH}/drives/{quote(drive_id, safe='!')}/items/{quote(item_id, safe='!')}"
    return f"{GRAPH}/me/drive/items/{quote(file_id, safe='!')}"


def _check_file_id(key: str, file_id: str) -> str:
    file_id = (file_id or "").strip()
    bad = not file_id or len(file_id) > 500 or any(c in file_id for c in "\r\n\t\x00")
    if key == "dropbox":
        # Dropbox takes ids ("id:…") or paths; both go in a JSON body, never a URL.
        bad = bad or not (file_id.startswith("id:") or file_id.startswith("/"))
    else:
        # Drive ids and Graph "drive:item" ids go into URL paths.
        bad = bad or any(c in file_id for c in "/?#%\\ ")
    if bad:
        raise IntegrationError("That file reference isn't valid.", 422)
    return file_id


# ---------------------------------------------------------------------------
# Browse and search
# ---------------------------------------------------------------------------


async def browse(
    db: AsyncSession,
    connection: IntegrationConnection,
    *,
    query: str | None = None,
    folder_id: str | None = None,
    view: str = "files",
    cursor: str | None = None,
) -> dict[str, Any]:
    """One page of files: a search when ``query`` is given, else a folder (or recent / shared)."""

    key = connection.provider
    query = (query or "").strip()[:200]
    if key == "google_drive":
        return await _drive_browse(db, connection, query, folder_id, view, cursor)
    if key == "onedrive":
        return await _graph_browse(db, connection, query, folder_id, view, cursor)
    if key == "dropbox":
        return await _dropbox_browse(db, connection, query, folder_id, cursor)
    raise IntegrationError("Unknown file provider.", 404)


async def _drive_browse(db, connection, query, folder_id, view, cursor) -> dict[str, Any]:
    params: dict[str, Any] = {
        "fields": f"nextPageToken,files({DRIVE_FIELDS})",
        "pageSize": PAGE,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "corpora": "allDrives",
    }
    if query:
        safe = _escape_drive(query)
        params["q"] = f"(name contains '{safe}' or fullText contains '{safe}') and trashed = false"
    elif folder_id:
        params["q"] = f"'{_escape_drive(_check_file_id('google_drive', folder_id))}' in parents and trashed = false"
        params["orderBy"] = "folder,name_natural"
    elif view == "shared":
        params["q"] = "sharedWithMe = true and trashed = false"
        params["orderBy"] = "sharedWithMeTime desc"
        params.pop("corpora")
        params.pop("includeItemsFromAllDrives")
    elif view == "recent":
        params["q"] = f"mimeType != '{GOOGLE_FOLDER}' and trashed = false"
        params["orderBy"] = "viewedByMeTime desc"
    else:
        params["q"] = "'root' in parents and trashed = false"
        params["orderBy"] = "folder,name_natural"
        params.pop("corpora")
        params.pop("includeItemsFromAllDrives")
    if cursor:
        params["pageToken"] = cursor[:1000]
    data = (await core.request(db, connection, "GET", f"{DRIVE}/files", params=params)).json()
    return {"items": [_drive_item(f) for f in data.get("files") or []], "cursor": data.get("nextPageToken")}


async def _graph_browse(db, connection, query, folder_id, view, cursor) -> dict[str, Any]:
    if cursor:
        # Graph pages are full URLs; only follow ones that point back at Graph.
        if not cursor.startswith(f"{GRAPH}/"):
            raise IntegrationError("That page link isn't valid.", 422)
        response = await core.request(db, connection, "GET", cursor)
    else:
        params = {"$top": PAGE, "$select": GRAPH_SELECT}
        if query:
            url = f"{GRAPH}/me/drive/root/search(q='{quote(query.replace(chr(39), chr(39) * 2), safe='')}')"
        elif folder_id:
            url = f"{_graph_item_url(_check_file_id('onedrive', folder_id))}/children"
        elif view == "recent":
            url = f"{GRAPH}/me/drive/recent"
            params.pop("$select")
        elif view == "shared":
            url = f"{GRAPH}/me/drive/sharedWithMe"
            params.pop("$select")
        else:
            url = f"{GRAPH}/me/drive/root/children"
        response = await core.request(db, connection, "GET", url, params=params)
    data = response.json()
    items = [_graph_item(i) for i in data.get("value") or []]
    if not query:
        items.sort(key=lambda i: (not i["is_folder"], i["name"].lower()))
    return {"items": items, "cursor": data.get("@odata.nextLink")}


async def _dropbox(db, connection, endpoint: str, body: Any) -> dict[str, Any]:
    response = await core.request(
        db, connection, "POST", f"{DBX}/{endpoint}", content=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    return response.json() if response.content else {}


async def _dropbox_browse(db, connection, query, folder_id, cursor) -> dict[str, Any]:
    if cursor:
        kind, _, token = cursor.partition(":")
        if kind == "s":
            data = await _dropbox(db, connection, "files/search/continue_v2", {"cursor": token})
        else:
            data = await _dropbox(db, connection, "files/list_folder/continue", {"cursor": token})
    elif query:
        kind = "s"
        data = await _dropbox(db, connection, "files/search_v2", {"query": query, "options": {"max_results": PAGE, "file_status": "active", "filename_only": False}})
    else:
        kind = "l"
        path = _check_file_id("dropbox", folder_id) if folder_id else ""
        data = await _dropbox(db, connection, "files/list_folder", {"path": path, "limit": 100, "include_non_downloadable_files": False})
    if kind == "s":
        raw = [((m.get("metadata") or {}).get("metadata") or {}) for m in data.get("matches") or []]
    else:
        raw = data.get("entries") or []
    items = [_dropbox_item(r) for r in raw if r.get(".tag") in {"file", "folder"}]
    if kind == "l":
        items.sort(key=lambda i: (not i["is_folder"], i["name"].lower()))
    next_cursor = f"{kind}:{data['cursor']}" if data.get("has_more") and data.get("cursor") else None
    return {"items": items, "cursor": next_cursor}


# ---------------------------------------------------------------------------
# One file: metadata and content
# ---------------------------------------------------------------------------


async def metadata(db: AsyncSession, connection: IntegrationConnection, file_id: str) -> dict[str, Any]:
    key = connection.provider
    file_id = _check_file_id(key, file_id)
    if key == "google_drive":
        data = (
            await core.request(db, connection, "GET", f"{DRIVE}/files/{quote(file_id, safe='')}", params={"fields": DRIVE_FIELDS, "supportsAllDrives": "true"})
        ).json()
        return _drive_item(data)
    if key == "onedrive":
        data = (await core.request(db, connection, "GET", _graph_item_url(file_id), params={"$select": GRAPH_SELECT})).json()
        return _graph_item(data)
    if key == "dropbox":
        data = await _dropbox(db, connection, "files/get_metadata", {"path": file_id})
        item = _dropbox_item(data)
        if not item["is_folder"]:
            # A shared link opens for teammates; fall back to the owner's preview page.
            try:
                links = await _dropbox(db, connection, "sharing/list_shared_links", {"path": data.get("id") or file_id, "direct_only": True})
                if (links.get("links") or []) and links["links"][0].get("url"):
                    item["web_url"] = links["links"][0]["url"]
            except IntegrationError:
                pass
        return item
    raise IntegrationError("Unknown file provider.", 404)


async def download(db: AsyncSession, connection: IntegrationConnection, file_id: str, *, fmt: str = "pdf") -> tuple[str, str, bytes, dict[str, Any]]:
    """Returns (file name, content type, bytes, metadata). Enforces the Documents size limit."""

    key = connection.provider
    item = await metadata(db, connection, file_id)
    if item["is_folder"]:
        raise IntegrationError("Folders can't be copied; attach the folder instead, or pick a file inside it.", 422)
    limit = _doc_store.max_bytes()
    name = item["name"]
    if key == "google_drive":
        mime = item["mime_type"] or ""
        if mime.startswith("application/vnd.google-apps."):
            export = GOOGLE_EXPORTS.get(mime)
            if export is None:
                raise IntegrationError(f"{item['kind']} files can't be copied into Documents. Attach the link instead.", 422)
            target, extension = export["office" if fmt == "office" else "pdf"]
            response = await core.request(
                db, connection, "GET", f"{DRIVE}/files/{quote(item['id'], safe='')}/export", params={"mimeType": target}, timeout=60
            )
            content, content_type = response.content, target
            if not name.lower().endswith(extension):
                name = f"{name}{extension}"
        else:
            if item["size"] and item["size"] > limit:
                raise IntegrationError(_doc_store.too_big_message(), 413)
            response = await core.request(
                db, connection, "GET", f"{DRIVE}/files/{quote(item['id'], safe='')}", params={"alt": "media", "supportsAllDrives": "true"}, timeout=90
            )
            content, content_type = response.content, mime
    elif key == "onedrive":
        if item["size"] and item["size"] > limit:
            raise IntegrationError(_doc_store.too_big_message(), 413)
        response = await core.request(db, connection, "GET", f"{_graph_item_url(item['id'])}/content", follow_redirects=True, timeout=90)
        content, content_type = response.content, item["mime_type"] or response.headers.get("content-type")
    elif key == "dropbox":
        if item["size"] and item["size"] > limit:
            raise IntegrationError(_doc_store.too_big_message(), 413)
        # Dropbox-API-Arg must be ASCII; json.dumps escapes everything else.
        response = await core.request(
            db, connection, "POST", f"{DBX_CONTENT}/files/download", headers={"Dropbox-API-Arg": json.dumps({"path": item["id"]})}, timeout=90
        )
        content, content_type = response.content, None
    else:
        raise IntegrationError("Unknown file provider.", 404)
    if len(content) > limit:
        raise IntegrationError(_doc_store.too_big_message(), 413)
    return name, _doc_store.content_type_for(name, content_type), content, item


# ---------------------------------------------------------------------------
# Links on records
# ---------------------------------------------------------------------------


def parse_modified(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def apply_metadata(link: ExternalFileLink, item: dict[str, Any]) -> None:
    link.name = (item.get("name") or link.name or "Untitled")[:500]
    link.mime_type = (item.get("mime_type") or "")[:200] or None
    link.size_bytes = item.get("size")
    link.web_url = item.get("web_url") or link.web_url
    link.file_modified_at = parse_modified(item.get("modified_at")) or link.file_modified_at


def link_out(link: ExternalFileLink, names: dict[str, str] | None = None, record: dict[str, Any] | None = None) -> dict[str, Any]:
    is_folder = link.mime_type == "folder"
    return {
        "id": str(link.id),
        "provider": link.provider,
        "file_id": link.file_id,
        "name": link.name,
        "mime_type": link.mime_type,
        "kind": kind_label(link.mime_type, link.name, is_folder),
        "is_folder": is_folder,
        "size": link.size_bytes,
        "web_url": link.web_url,
        "modified_at": link.file_modified_at,
        "record_type": link.record_type,
        "record_id": str(link.record_id),
        "record": record,
        "added_by_user_id": str(link.added_by_user_id) if link.added_by_user_id else None,
        "added_by": (names or {}).get(str(link.added_by_user_id)) if link.added_by_user_id else None,
        "added_at": link.created_at,
        "updated_at": link.updated_at,
    }


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    def size(value: Any) -> str:
        number = _int(value) or 0
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if number < 1024 or unit == "TB":
                return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
            number /= 1024
        return str(number)

    if key == "google_drive":
        data = (await core.request(db, connection, "GET", f"{DRIVE}/about", params={"fields": "user(emailAddress),storageQuota(usage,limit)"})).json()
        quota = data.get("storageQuota") or {}
        used = f" ({size(quota.get('usage'))} of {size(quota.get('limit'))} used)" if quota.get("limit") else ""
        return f"Google Drive answered for {(data.get('user') or {}).get('emailAddress') or connection.account_label}{used}."
    if key == "onedrive":
        data = (await core.request(db, connection, "GET", f"{GRAPH}/me/drive", params={"$select": "driveType,quota,owner"})).json()
        quota = data.get("quota") or {}
        used = f" ({size(quota.get('used'))} of {size(quota.get('total'))} used)" if quota.get("total") else ""
        kind = "OneDrive for Business" if data.get("driveType") == "business" else "OneDrive"
        return f"{kind} answered for {connection.account_label or 'your account'}{used}."
    if key == "dropbox":
        response = await core.request(db, connection, "POST", f"{DBX}/users/get_space_usage", content=b"null", headers={"Content-Type": "application/json"})
        data = response.json()
        allocated = (data.get("allocation") or {}).get("allocated")
        used = f" ({size(data.get('used'))} of {size(allocated)} used)" if allocated else ""
        return f"Dropbox answered for {connection.account_label or 'your account'}{used}."
    raise IntegrationError("Unknown file provider.", 404)


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    total = (
        await db.execute(select(func.count()).select_from(ExternalFileLink).where(ExternalFileLink.organization_id == org, ExternalFileLink.provider == key))
    ).scalar_one()
    mine = (
        await db.execute(
            select(func.count()).select_from(ExternalFileLink).where(
                ExternalFileLink.organization_id == org, ExternalFileLink.provider == key, ExternalFileLink.added_by_user_id == principal.user_id
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(ExternalFileLink).where(ExternalFileLink.organization_id == org, ExternalFileLink.provider == key).order_by(ExternalFileLink.created_at.desc()).limit(8)
        )
    ).scalars().all()
    from app.services.lookups import names_for
    from app.services.records import resolve_many

    names = await names_for(db, org, "user", [r.added_by_user_id for r in rows])
    records = await resolve_many(db, org, [(r.record_type, r.record_id) for r in rows])
    config = await core.workspace_config(db, org, key)
    week_ago = core.utcnow() - timedelta(days=7)
    added_week = (
        await db.execute(
            select(func.count()).select_from(ExternalFileLink).where(
                ExternalFileLink.organization_id == org, ExternalFileLink.provider == key, ExternalFileLink.created_at >= week_ago
            )
        )
    ).scalar_one()
    return {
        "links_total": int(total),
        "links_mine": int(mine),
        "links_this_week": int(added_week),
        "allow_copy": config.get("allow_copy", True) is not False,
        "max_mb": _doc_store.max_bytes() // (1024 * 1024),
        "recent": [link_out(r, names, records.get((r.record_type, str(r.record_id)))) for r in rows if (r.record_type, str(r.record_id)) in records],
    }


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if personal:
        if config:
            raise IntegrationError("There are no personal settings for this integration.", 422)
        return {}
    cleaned: dict[str, Any] = {}
    for name, value in config.items():
        if name == "allow_copy":
            if not isinstance(value, bool):
                raise IntegrationError("“Allow copying into Documents” must be on or off.", 422)
            cleaned[name] = value
        else:
            raise IntegrationError(f"Unknown setting “{name}”.", 422)
    return cleaned


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    # Dropbox tokens can be revoked individually. Google and Microsoft revocation
    # would also sign the person out of their other Google / Microsoft integrations.
    if key == "dropbox" and connection.access_token:
        try:
            await core.request(db, connection, "POST", f"{DBX}/auth/token/revoke", retry_auth=False)
        except IntegrationError:
            pass


def provider_summary(key: str) -> dict[str, Any]:
    from app.integrations.catalog import INTEGRATIONS

    item = INTEGRATIONS[key]
    return {"key": key, "name": item.name, "vendor": item.vendor, "color": item.color, "monogram": item.monogram or item.name[:1]}


def new_link(organization_id: uuid.UUID, key: str, item: dict[str, Any], record_type: str, record_id: uuid.UUID, user_id: uuid.UUID) -> ExternalFileLink:
    link = ExternalFileLink(
        id=uuid.uuid4(),
        organization_id=organization_id,
        provider=key,
        file_id=str(item["id"])[:500],
        name=(item.get("name") or "Untitled")[:500],
        record_type=record_type,
        record_id=record_id,
        added_by_user_id=user_id,
    )
    apply_metadata(link, item)
    return link
