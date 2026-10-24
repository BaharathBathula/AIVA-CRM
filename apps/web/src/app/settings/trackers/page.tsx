"use client";

import { ListTree, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { ColorDot, ColorPicker, TagsInput, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Tracker = {
  id: string;
  name: string;
  category: string | null;
  keywords: string[];
  color: string;
  is_active: boolean;
  mentions_30_days: number;
  conversations_30_days: number;
};

type TrackerList = { items: Tracker[]; conversations_30_days: number; can_edit: boolean };

type Draft = { name: string; category: string; keywords: string[]; color: string; is_active: boolean };

const CATEGORIES = ["Competitors", "Pricing", "Objections", "Product", "Next steps", "Risks"];

const STARTERS: Draft[] = [
  { name: "Competitors", category: "Competitors", keywords: ["Salesforce", "HubSpot", "Pipedrive", "Zoho"], color: "red", is_active: true },
  { name: "Pricing", category: "Pricing", keywords: ["price", "pricing", "budget", "discount", "cost"], color: "amber", is_active: true },
  { name: "Objections", category: "Objections", keywords: ["too expensive", "not a priority", "no budget", "already using"], color: "purple", is_active: true },
  { name: "Next steps", category: "Next steps", keywords: ["next step", "follow up", "send over", "proposal", "contract"], color: "green", is_active: true },
];

export default function TrackersPage() {
  return (
    <SettingsShell feature="conversation_intelligence">
      <Trackers />
    </SettingsShell>
  );
}

function Trackers() {
  const toast = useToast();
  const trackers = useApi<TrackerList>("/conversation-trackers");
  const [editing, setEditing] = useState<Tracker | Draft | null>(null);
  const [deleting, setDeleting] = useState<Tracker | null>(null);
  const [busy, setBusy] = useState(false);
  const items = trackers.data?.items ?? [];
  const editable = Boolean(trackers.data?.can_edit);
  const unused = STARTERS.filter((starter) => !items.some((t) => t.name.toLowerCase() === starter.name.toLowerCase()));

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/conversation-trackers/${deleting.id}`);
      toast("Tracker deleted.");
      setDeleting(null);
      await trackers.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function addStarter(starter: Draft) {
    try {
      await api.post("/conversation-trackers", starter);
      toast(`${starter.name} tracker added.`);
      await trackers.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const blank: Draft = { name: "", category: "", keywords: [], color: "blue", is_active: true };

  return (
    <>
      <PageHeader
        title="Trackers"
        description="Words and phrases AIVA looks for in every conversation — competitors, pricing, objections, next steps. Mentions are highlighted in transcripts and counted on the Conversations page."
        actions={
          editable ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing(blank)}>
              <Plus size={16} /> New tracker
            </button>
          ) : null
        }
      />
      {trackers.error ? <Alert>{trackers.error}</Alert> : null}
      <div className="ui-stack">
        <Card flush title="Your trackers" actions={trackers.data ? <span className="ui-muted ui-small">{trackers.data.conversations_30_days} conversations in the last 30 days</span> : null}>
          {trackers.loading && !trackers.data ? (
            <LoadingRows />
          ) : items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Tracker</th>
                    <th>Listens for</th>
                    <th className="ui-num">Mentions (30 days)</th>
                    <th className="ui-num">Calls</th>
                    <th>Status</th>
                    {editable ? <th aria-label="Actions" /> : null}
                  </tr>
                </thead>
                <tbody>
                  {items.map((tracker) => (
                    <tr key={tracker.id}>
                      <td>
                        <div className="cell-title ui-row" style={{ gap: 8 }}>
                          <ColorDot color={tracker.color} />
                          {tracker.name}
                        </div>
                        {tracker.category ? <div className="cell-sub">{tracker.category}</div> : null}
                      </td>
                      <td style={{ maxWidth: 360 }}>
                        <div className="conv-keywords">
                          {tracker.keywords.slice(0, 8).map((keyword) => (
                            <span key={keyword} className="conv-keyword">
                              {keyword}
                            </span>
                          ))}
                          {tracker.keywords.length > 8 ? <span className="ui-muted ui-small">+{tracker.keywords.length - 8} more</span> : null}
                        </div>
                      </td>
                      <td className="ui-num">{tracker.mentions_30_days}</td>
                      <td className="ui-num">{tracker.conversations_30_days}</td>
                      <td>{tracker.is_active ? <Badge tone="success">On</Badge> : <Badge>Off</Badge>}</td>
                      {editable ? (
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${tracker.name}`} onClick={() => setEditing(tracker)}>
                            <Pencil size={14} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${tracker.name}`} onClick={() => setDeleting(tracker)}>
                            <Trash2 size={14} />
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState icon={<ListTree size={20} />} title="No trackers yet" message="Start with one of the suggestions below, or create your own." />
          )}
        </Card>

        {editable && unused.length ? (
          <Card title="Suggested trackers">
            <div className="settings-rows">
              {unused.map((starter) => (
                <SettingRowLike key={starter.name} starter={starter} onAdd={() => void addStarter(starter)} />
              ))}
            </div>
          </Card>
        ) : null}
      </div>

      {editing ? (
        <TrackerModal
          tracker={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await trackers.reload();
          }}
        />
      ) : null}
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete tracker?"
        message={`“${deleting?.name ?? ""}” stops being counted on new conversations. Past highlights stay until those conversations are re-analyzed.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function SettingRowLike({ starter, onAdd }: { starter: Draft; onAdd: () => void }) {
  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <div className="settings-row-label ui-row" style={{ gap: 8 }}>
          <ColorDot color={starter.color} /> {starter.name}
        </div>
        <div className="settings-row-hint">{starter.keywords.join(", ")}</div>
      </div>
      <div className="settings-row-control">
        <button type="button" className="ui-btn ui-btn-sm" onClick={onAdd}>
          <Plus size={14} /> Add
        </button>
      </div>
    </div>
  );
}

function TrackerModal({ tracker, onClose, onSaved }: { tracker: Tracker | Draft; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const existing = "id" in tracker ? tracker : null;
  const [draft, setDraft] = useState<Draft>({
    name: tracker.name,
    category: tracker.category ?? "",
    keywords: [...tracker.keywords],
    color: tracker.color || "blue",
    is_active: tracker.is_active,
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => setError(null), [tracker]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { ...draft, name: draft.name.trim(), category: draft.category.trim() || null };
    try {
      if (existing) await api.put(`/conversation-trackers/${existing.id}`, payload);
      else await api.post("/conversation-trackers", payload);
      toast(existing ? "Tracker saved." : "Tracker created.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      title={existing ? "Edit tracker" : "New tracker"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="tracker-form" className="ui-btn ui-btn-primary" disabled={saving || !draft.name.trim() || !draft.keywords.length}>
            {saving ? <span className="ui-spinner" /> : null} Save tracker
          </button>
        </>
      }
    >
      <form id="tracker-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Name" required>
            <input className="ui-input" value={draft.name} maxLength={120} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Competitors" />
          </Field>
          <Field label="Category">
            <input
              className="ui-input"
              list="tracker-categories"
              value={draft.category}
              maxLength={40}
              onChange={(event) => setDraft({ ...draft, category: event.target.value })}
              placeholder="Competitors"
            />
          </Field>
        </div>
        <datalist id="tracker-categories">
          {CATEGORIES.map((category) => (
            <option key={category} value={category} />
          ))}
        </datalist>
        <Field label="Words and phrases" required hint="Press Enter or comma after each one. Matching ignores capital letters and finds whole words, so “price” won't match “priceless”.">
          <TagsInput label="Words and phrases" value={draft.keywords} placeholder="Salesforce, HubSpot…" onChange={(keywords) => setDraft({ ...draft, keywords })} />
        </Field>
        <Field label="Color">
          <ColorPicker value={draft.color} onChange={(color) => setDraft({ ...draft, color })} />
        </Field>
        <div className="ui-row">
          <Toggle label="Tracker is on" checked={draft.is_active} onChange={(is_active) => setDraft({ ...draft, is_active })} />
          <span className="ui-small">{draft.is_active ? "On — counted on new conversations" : "Off — not counted"}</span>
        </div>
      </form>
    </Modal>
  );
}
