"use client";

import { Pencil, Plus, Search, TextQuote, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Toggle } from "@/components/settings-kit";
import type { Snippet } from "@/components/snippet-picker";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";

type MergeField = { key: string; label: string; token: string };

export default function SnippetsPage() {
  return (
    <SettingsShell>
      <Snippets />
    </SettingsShell>
  );
}

function Snippets() {
  const toast = useToast();
  const [term, setTerm] = useState("");
  const debounced = useDebounced(term, 250);
  const snippets = useApi<{ items: Snippet[] }>(`/snippets${debounced ? `?search=${encodeURIComponent(debounced)}` : ""}`);
  const [editing, setEditing] = useState<Snippet | "new" | null>(null);
  const [deleting, setDeleting] = useState<Snippet | null>(null);
  const [busy, setBusy] = useState(false);
  const items = snippets.data?.items ?? [];

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/snippets/${deleting.id}`);
      toast("Snippet deleted.");
      setDeleting(null);
      await snippets.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Snippets"
        description="Reusable text for emails, sequence steps and call notes. Insert one with the Snippet button in any message box; merge fields fill in for the person you're writing to."
        actions={
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
            <Plus size={16} /> New snippet
          </button>
        }
      />
      {snippets.error ? <Alert>{snippets.error}</Alert> : null}
      <Card
        flush
        title="All snippets"
        actions={
          <label className="settings-nav-search" style={{ width: 240 }}>
            <Search size={15} aria-hidden />
            <input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search snippets" aria-label="Search snippets" />
          </label>
        }
      >
        {snippets.loading && !snippets.data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Snippet</th>
                  <th>Shortcut</th>
                  <th>Category</th>
                  <th>Shared</th>
                  <th className="ui-num">Uses</th>
                  <th>Updated</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {items.map((snippet) => (
                  <tr key={snippet.id}>
                    <td style={{ maxWidth: 360 }}>
                      <div className="cell-title">{snippet.name}</div>
                      <div className="ui-muted ui-small ui-truncate">{snippet.body}</div>
                    </td>
                    <td className="ui-mono">{snippet.shortcut ? `/${snippet.shortcut}` : "—"}</td>
                    <td>{snippet.category ? <Badge>{snippet.category}</Badge> : <span className="ui-muted">—</span>}</td>
                    <td>{snippet.is_shared ? <Badge tone="info">Team</Badge> : <span className="ui-muted ui-small">Only {snippet.owner_name ?? "owner"}</span>}</td>
                    <td className="ui-num">{snippet.usage_count}</td>
                    <td className="ui-muted ui-small ui-nowrap">{relativeTime(snippet.updated_at)}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {snippet.can_edit ? (
                        <>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${snippet.name}`} onClick={() => setEditing(snippet)}>
                            <Pencil size={14} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${snippet.name}`} onClick={() => setDeleting(snippet)}>
                            <Trash2 size={14} />
                          </button>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<TextQuote size={20} />}
            title={debounced ? "No snippets match" : "No snippets yet"}
            message={debounced ? "Try another word." : "Save answers you type often — pricing, a case study link, a meeting request — and drop them into any email."}
          />
        )}
      </Card>

      <SnippetModal
        snippet={editing}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await snippets.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete snippet?"
        message={`“${deleting?.name ?? ""}” will no longer be available to insert. Emails already sent keep their text.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function SnippetModal({ snippet, onClose, onSaved }: { snippet: Snippet | "new" | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const fields = useApi<MergeField[]>(snippet ? "/email-templates/merge-fields" : null);
  const [name, setName] = useState("");
  const [shortcut, setShortcut] = useState("");
  const [category, setCategory] = useState("");
  const [body, setBody] = useState("");
  const [shared, setShared] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!snippet) return;
    const existing = snippet === "new" ? null : snippet;
    setName(existing?.name ?? "");
    setShortcut(existing?.shortcut ?? "");
    setCategory(existing?.category ?? "");
    setBody(existing?.body ?? "");
    setShared(existing?.is_shared ?? true);
    setError(null);
  }, [snippet]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { name, shortcut: shortcut.replace(/^\//, "") || null, category: category || null, body, is_shared: shared };
    try {
      if (snippet === "new") await api.post("/snippets", payload);
      else if (snippet) await api.put(`/snippets/${snippet.id}`, payload);
      toast(snippet === "new" ? "Snippet created." : "Snippet saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={Boolean(snippet)}
      title={snippet === "new" ? "New snippet" : "Edit snippet"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="snippet-form" className="ui-btn ui-btn-primary" disabled={saving || !name.trim() || !body.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Save snippet
          </button>
        </>
      }
    >
      <form id="snippet-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Name" required>
            <input className="ui-input" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="Pricing overview" />
          </Field>
          <Field label="Shortcut" hint="Letters, numbers, - and _. Type /shortcut to find it fast.">
            <input className="ui-input" value={shortcut} maxLength={40} onChange={(event) => setShortcut(event.target.value.replace(/[^A-Za-z0-9_/-]/g, ""))} placeholder="pricing" />
          </Field>
          <Field label="Category">
            <input className="ui-input" value={category} maxLength={60} onChange={(event) => setCategory(event.target.value)} placeholder="Sales" />
          </Field>
          <Field label="Who can use it">
            <span className="ui-row" style={{ height: 44 }}>
              <Toggle label="Share with the team" checked={shared} onChange={setShared} />
              <span className="ui-small">{shared ? "Everyone in the workspace" : "Only you"}</span>
            </span>
          </Field>
          <Field label="Text" required className="span-2">
            <textarea className="ui-textarea" rows={8} value={body} onChange={(event) => setBody(event.target.value)} />
          </Field>
        </div>
        {fields.data?.length ? (
          <div>
            <div className="ui-muted ui-small" style={{ marginBottom: 6 }}>
              Merge fields
            </div>
            <div className="ui-row" style={{ flexWrap: "wrap", gap: 6 }}>
              {fields.data
                .filter((field) => field.key !== "unsubscribe_url")
                .map((field) => (
                  <button key={field.key} type="button" className="ui-btn ui-btn-sm" onClick={() => setBody((value) => `${value}${value && !value.endsWith(" ") ? " " : ""}${field.token}`)}>
                    {field.label}
                  </button>
                ))}
            </div>
          </div>
        ) : null}
      </form>
    </Modal>
  );
}
