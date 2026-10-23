"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Download, Link2, Lock, Pencil, Trash2, Upload, X } from "lucide-react";
import { useRef, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { PinButton } from "@/components/pins";
import { useSession, useToast } from "@/components/providers";
import { RecordSearch } from "@/components/record-search";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { fileBadge, fileKind, fileSize, uploadFile, type DocumentItem, type Folder } from "@/lib/workspace";

export default function DocumentPage() {
  return (
    <FeaturePage feature="documents">
      <DocumentView />
    </FeaturePage>
  );
}

function DocumentView() {
  const { documentId } = useParams<{ documentId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const doc = useApi<DocumentItem>(`/documents/${documentId}`);
  const folders = useApi<{ items: Folder[] }>("/documents/folders");
  const versionInput = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [linking, setLinking] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", folder_id: "", visibility: "organization", tags: "" });
  const d = doc.data;

  async function newVersion(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", files[0]);
      if (note.trim()) fd.append("note", note.trim());
      doc.setData(await uploadFile<DocumentItem>(`/documents/${documentId}/versions`, fd));
      setNote("");
      toast("New version uploaded.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
      if (versionInput.current) versionInput.current.value = "";
    }
  }

  async function save() {
    setBusy(true);
    try {
      doc.setData(
        await api.patch<DocumentItem>(`/documents/${documentId}`, {
          name: form.name.trim(),
          description: form.description.trim() || null,
          folder_id: form.folder_id || null,
          visibility: form.visibility,
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        })
      );
      setEditing(false);
      toast("Saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function link(type: string, id: string) {
    try {
      doc.setData(await api.post<DocumentItem>(`/documents/${documentId}/links`, { record_type: type, record_id: id }));
      setLinking(false);
      toast("Linked.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function unlink(linkId: string) {
    try {
      doc.setData(await api.del<DocumentItem>(`/documents/${documentId}/links/${linkId}`));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/documents/${documentId}`);
      toast("Document deleted.");
      router.replace("/documents");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  if (doc.error) return <Alert>{doc.error}</Alert>;
  if (!d) return <div className="ui-card"><LoadingRows rows={8} /></div>;
  const download = `/api/aiva/documents/${d.id}/download`;
  const inline = `${download}?inline=true`;

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/documents" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All documents
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            {fileKind(d.content_type, d.name)} · {fileSize(d.size_bytes)} · Version {d.version}
            {d.visibility === "private" ? (
              <>
                {" "}
                <Badge>
                  <Lock size={11} /> Private
                </Badge>
              </>
            ) : null}
          </>
        }
        title={d.name}
        description={`${d.folder_name ?? "No folder"} · Uploaded by ${d.owner_name ?? "—"} · ${d.download_count} download${d.download_count === 1 ? "" : "s"}`}
        actions={
          <>
            <PinButton type="document" id={d.id} />
            <a className="ui-btn ui-btn-primary" href={download}>
              <Download size={15} /> Download
            </a>
            {d.can_edit ? (
              <button
                type="button"
                className="ui-btn"
                onClick={() => {
                  setForm({ name: d.name, description: d.description ?? "", folder_id: d.folder_id ?? "", visibility: d.visibility, tags: d.tags.join(", ") });
                  setEditing(true);
                }}
              >
                <Pencil size={15} /> Edit
              </button>
            ) : null}
          </>
        }
      />
      <div className="ui-split">
        <Card title="Preview">
          {d.previewable ? (
            <div className="doc-preview">
              {d.content_type.startsWith("image/") ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={inline} alt={d.name} />
              ) : (
                <iframe src={inline} title={d.name} />
              )}
            </div>
          ) : (
            <div className="doc-preview">
              <div className="ui-stack" style={{ alignItems: "center", textAlign: "center", padding: 24 }}>
                <span className="file-chip" style={{ width: 56, height: 56, fontSize: 13 }}>
                  {fileBadge(d.content_type, d.name)}
                </span>
                <span className="ui-muted">This file type can't be previewed in the browser.</span>
                <a className="ui-btn" href={download}>
                  <Download size={15} /> Download to open
                </a>
              </div>
            </div>
          )}
          {d.description ? <p style={{ margin: "14px 0 0", whiteSpace: "pre-wrap" }}>{d.description}</p> : null}
          {d.tags.length ? (
            <div className="tag-list" style={{ marginTop: 12 }}>
              {d.tags.map((t) => (
                <Badge key={t}>{t}</Badge>
              ))}
            </div>
          ) : null}
        </Card>
        <div className="ui-stack">
          <Card
            title="Linked records"
            icon={<Link2 size={16} />}
            actions={
              can("records.write") ? (
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setLinking(true)}>
                  Link
                </button>
              ) : null
            }
          >
            {d.links.length ? (
              <ul className="mini-list">
                {d.links.map((l) => (
                  <li key={l.link_id}>
                    <div className="mini-main">
                      <Link href={l.href} className="mini-title">
                        {l.label}
                      </Link>
                      <span className="mini-sub">{l.kind}</span>
                    </div>
                    {can("records.write") ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Unlink ${l.label}`} onClick={() => void unlink(l.link_id)}>
                        <X size={14} />
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Not linked to anything. Link it to an account, deal or order so people find it there.
              </p>
            )}
          </Card>
          <Card title="Versions">
            <ul className="mini-list">
              {(d.versions ?? []).map((v) => (
                <li key={v.version}>
                  <div className="mini-main">
                    <span className="mini-title">
                      v{v.version} · {v.file_name}
                    </span>
                    <span className="mini-sub">
                      {fileSize(v.size_bytes)} · {v.uploaded_by ?? "—"} · {dateTime(v.created_at)}
                      {v.note ? ` · ${v.note}` : ""}
                    </span>
                  </div>
                  <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={`${download}?version=${v.version}`} aria-label={`Download version ${v.version}`}>
                    <Download size={14} />
                  </a>
                </li>
              ))}
            </ul>
            {d.can_edit ? (
              <div className="ui-stack" style={{ gap: 8, marginTop: 12 }}>
                <input className="ui-input sm" placeholder="What changed? (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Version note" />
                <input ref={versionInput} type="file" hidden onChange={(e) => void newVersion(e.target.files)} aria-label="Upload new version" />
                <button type="button" className="ui-btn" onClick={() => versionInput.current?.click()} disabled={busy}>
                  {busy ? <span className="ui-spinner" /> : <Upload size={15} />} Upload new version
                </button>
              </div>
            ) : null}
          </Card>
          {d.can_edit ? (
            <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDeleting(true)}>
              <Trash2 size={15} /> Delete document
            </button>
          ) : null}
        </div>
      </div>
      <Modal
        open={editing}
        title="Edit document"
        onClose={() => setEditing(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy || !form.name.trim()}>
              Save
            </button>
          </>
        }
      >
        <div className="ui-form-grid">
          <Field label="Name" required className="span-2">
            <input className="ui-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Folder">
            <select className="ui-select" value={form.folder_id} onChange={(e) => setForm({ ...form, folder_id: e.target.value })}>
              <option value="">No folder</option>
              {(folders.data?.items ?? []).map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Who can see it">
            <select className="ui-select" value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value })}>
              <option value="organization">Everyone in the workspace</option>
              <option value="private">Only me and administrators</option>
            </select>
          </Field>
          <Field label="Tags" hint="Separate with commas." className="span-2">
            <input className="ui-input" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="contract, signed" />
          </Field>
          <Field label="Description" className="span-2">
            <textarea className="ui-textarea" rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
        </div>
      </Modal>
      <Modal open={linking} title="Link to a record" onClose={() => setLinking(false)}>
        <RecordSearch onPick={(r) => void link(r.type, r.id)} />
      </Modal>
      <ConfirmDialog open={deleting} title={`Delete ${d.name}?`} message="Every version of the file is deleted permanently." busy={busy} onConfirm={() => void remove()} onClose={() => setDeleting(false)} />
    </>
  );
}
