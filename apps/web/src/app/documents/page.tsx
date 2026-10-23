"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Download, Folder, FolderOpen, FolderPlus, Lock, Pencil, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";
import { fileBadge, fileKind, fileSize, uploadFile, type DocumentItem, type Folder as FolderItem } from "@/lib/workspace";

export default function DocumentsPage() {
  return (
    <FeaturePage feature="documents">
      <Documents />
    </FeaturePage>
  );
}

type Place = { kind: "all" } | { kind: "unfiled" } | { kind: "mine" } | { kind: "folder"; id: string };

function Documents() {
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const [place, setPlace] = useState<Place>({ kind: "all" });
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [folderModal, setFolderModal] = useState<{ folder?: FolderItem } | null>(null);
  const [deleting, setDeleting] = useState<FolderItem | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const folders = useApi<{ items: FolderItem[]; unfiled_count: number; total_count: number }>("/documents/folders");
  const query = new URLSearchParams();
  if (place.kind === "folder") query.set("folder_id", place.id);
  if (place.kind === "unfiled") query.set("unfiled", "true");
  if (place.kind === "mine") query.set("mine", "true");
  if (debounced.trim()) query.set("search", debounced.trim());
  const docs = useApi<{ items: DocumentItem[]; max_mb: number }>(`/documents?${query}`);
  const currentFolder = place.kind === "folder" ? folders.data?.items.find((f) => f.id === place.id) : undefined;

  const upload = useCallback(
    async (files: FileList | File[] | null) => {
      const list = Array.from(files ?? []);
      if (!list.length) return;
      setUploading(true);
      let done = 0;
      try {
        for (const file of list) {
          const form = new FormData();
          form.append("file", file);
          if (place.kind === "folder") form.append("folder_id", place.id);
          await uploadFile("/documents", form);
          done += 1;
        }
        toast(list.length === 1 ? `Uploaded ${list[0].name}.` : `Uploaded ${done} files.`);
      } catch (caught) {
        toast(`${done ? `${done} uploaded, then: ` : ""}${errorMessage(caught)}`, "danger");
      } finally {
        setUploading(false);
        if (input.current) input.current.value = "";
        await Promise.all([docs.reload(), folders.reload()]);
      }
    },
    [place, toast, docs, folders]
  );

  async function removeFolder(folder: FolderItem) {
    try {
      await api.del(`/documents/folders/${folder.id}`);
      toast(`Deleted ${folder.name}.`);
      setDeleting(null);
      setPlace({ kind: "all" });
      await folders.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setDeleting(null);
    }
  }

  const tree = folders.data?.items ?? [];
  const children = (parent: string | null) => tree.filter((f) => f.parent_id === parent);
  function renderTree(parent: string | null, depth: number): React.ReactNode {
    return children(parent).map((folder) => (
      <li key={folder.id}>
        <button type="button" style={{ paddingLeft: 10 + depth * 16 }} aria-current={place.kind === "folder" && place.id === folder.id} onClick={() => setPlace({ kind: "folder", id: folder.id })}>
          {place.kind === "folder" && place.id === folder.id ? <FolderOpen size={15} /> : <Folder size={15} />}
          <span className="ui-truncate">{folder.name}</span>
          <span className="count">{folder.document_count}</span>
        </button>
        {children(folder.id).length ? <ul className="folder-tree">{renderTree(folder.id, depth + 1)}</ul> : null}
      </li>
    ));
  }

  return (
    <>
      <PageHeader
        title="Documents"
        description="Contracts, proposals, price lists and specs in one place. Files keep every version and can be linked to any record."
        actions={
          can("records.write") ? (
            <>
              <button type="button" className="ui-btn" onClick={() => setFolderModal({})}>
                <FolderPlus size={16} /> New folder
              </button>
              <input ref={input} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} aria-label="Upload files" />
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => input.current?.click()} disabled={uploading}>
                {uploading ? <span className="ui-spinner" /> : <Upload size={16} />} Upload
              </button>
            </>
          ) : null
        }
      />
      <div className="doc-layout">
        <Card title="Folders">
          <ul className="folder-tree">
            <li>
              <button type="button" aria-current={place.kind === "all"} onClick={() => setPlace({ kind: "all" })}>
                <FolderOpen size={15} /> All documents <span className="count">{folders.data?.total_count ?? ""}</span>
              </button>
            </li>
            <li>
              <button type="button" aria-current={place.kind === "mine"} onClick={() => setPlace({ kind: "mine" })}>
                <Lock size={15} /> Uploaded by me
              </button>
            </li>
            <li>
              <button type="button" aria-current={place.kind === "unfiled"} onClick={() => setPlace({ kind: "unfiled" })}>
                <Folder size={15} /> Not in a folder <span className="count">{folders.data?.unfiled_count ?? ""}</span>
              </button>
            </li>
            {renderTree(null, 0)}
          </ul>
        </Card>
        <div className="ui-stack">
          <div className="ui-toolbar" style={{ marginBottom: 0 }}>
            <h2 style={{ margin: 0, fontSize: 17 }}>
              {place.kind === "folder" ? currentFolder?.name ?? "Folder" : place.kind === "mine" ? "Uploaded by me" : place.kind === "unfiled" ? "Not in a folder" : "All documents"}
            </h2>
            {currentFolder && can("records.write") ? (
              <>
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setFolderModal({ folder: currentFolder })}>
                  <Pencil size={14} /> Rename
                </button>
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setFolderModal({ folder: { id: "", name: "", parent_id: currentFolder.id, document_count: 0 } })}>
                  <FolderPlus size={14} /> Subfolder
                </button>
                {can("records.delete") ? (
                  <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setDeleting(currentFolder)}>
                    <Trash2 size={14} /> Delete
                  </button>
                ) : null}
              </>
            ) : null}
            <span className="ui-spacer" />
            <div className="ui-search">
              <Search size={15} />
              <input className="ui-input sm" placeholder="Search names, notes, tags" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search documents" />
            </div>
          </div>
          {can("records.write") ? (
            <div
              className={`dropzone${over ? " over" : ""}`}
              onDragOver={(e) => (e.preventDefault(), setOver(true))}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setOver(false);
                void upload(e.dataTransfer.files);
              }}
            >
              <Upload size={20} />
              <span>
                Drop files here to upload{currentFolder ? ` to ${currentFolder.name}` : ""} · up to {docs.data?.max_mb ?? 25} MB each
              </span>
            </div>
          ) : null}
          {docs.error ? <Alert>{docs.error}</Alert> : null}
          <div className="ui-card">
            {docs.loading && !docs.data ? (
              <LoadingRows />
            ) : docs.data?.items.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Linked to</th>
                      <th>Size</th>
                      <th>Owner</th>
                      <th>Updated</th>
                      <th aria-label="Download" />
                    </tr>
                  </thead>
                  <tbody>
                    {docs.data.items.map((doc) => (
                      <tr key={doc.id} className="clickable" onClick={() => router.push(`/documents/${doc.id}`)}>
                        <td>
                          <div className="ui-row" style={{ gap: 10 }}>
                            <span className="file-chip">{fileBadge(doc.content_type, doc.name)}</span>
                            <div style={{ minWidth: 0 }}>
                              <Link href={`/documents/${doc.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                                {doc.name}
                              </Link>
                              <div className="cell-sub">
                                {doc.visibility === "private" ? "Private · " : ""}
                                {doc.folder_name ?? "No folder"}
                                {doc.version > 1 ? ` · v${doc.version}` : ""}
                                {doc.tags.length ? ` · ${doc.tags.join(", ")}` : ""}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="ui-truncate" style={{ maxWidth: 220 }}>
                          {doc.links.length ? doc.links.map((l) => l.label).join(", ") : "—"}
                        </td>
                        <td>{fileSize(doc.size_bytes)}</td>
                        <td>{doc.owner_name ?? "—"}</td>
                        <td>{relativeTime(doc.updated_at)}</td>
                        <td>
                          <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={`/api/aiva/documents/${doc.id}/download`} onClick={(e) => e.stopPropagation()} aria-label={`Download ${doc.name}`}>
                            <Download size={14} />
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon={<FolderOpen size={20} />} title={search ? "No documents match" : "No documents here yet"} message="Upload files or drop them in the box above." />
            )}
          </div>
        </div>
      </div>
      <FolderModal
        state={folderModal}
        folders={tree}
        onClose={() => setFolderModal(null)}
        onSaved={async (folder) => {
          setFolderModal(null);
          await folders.reload();
          setPlace({ kind: "folder", id: folder.id });
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title={`Delete ${deleting?.name ?? "folder"}?`}
        message="Only empty folders can be deleted. Move or delete the files in it first."
        onConfirm={() => deleting && void removeFolder(deleting)}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function FolderModal({
  state,
  folders,
  onClose,
  onSaved,
}: {
  state: { folder?: FolderItem } | null;
  folders: FolderItem[];
  onClose: () => void;
  onSaved: (folder: FolderItem) => void;
}) {
  const editing = Boolean(state?.folder?.id);
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [seen, setSeen] = useState<typeof state>(null);
  if (state !== seen) {
    setSeen(state);
    setName(state?.folder?.id ? state.folder.name : "");
    setParent(state?.folder?.parent_id ?? "");
    setError(null);
  }

  async function save() {
    if (!name.trim()) return setError("Name the folder.");
    setBusy(true);
    setError(null);
    try {
      const saved = editing
        ? await api.patch<FolderItem>(`/documents/folders/${state!.folder!.id}`, { name: name.trim(), parent_id: parent || null })
        : await api.post<FolderItem>("/documents/folders", { name: name.trim(), parent_id: parent || null });
      onSaved(saved);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={state !== null}
      size="sm"
      title={editing ? "Rename folder" : "New folder"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            {editing ? "Save" : "Create"}
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-stack">
        <Field label="Name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Contracts" />
        </Field>
        <Field label="Inside">
          <select className="ui-select" value={parent} onChange={(e) => setParent(e.target.value)}>
            <option value="">Top level</option>
            {folders
              .filter((f) => f.id !== state?.folder?.id)
              .map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
          </select>
        </Field>
      </div>
    </Modal>
  );
}
