"use client";

import Link from "next/link";
import { ArrowDown, ArrowUp, ListPlus, Pencil, Plus, Tags, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { STAGE_COLORS } from "@/components/data-settings";
import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { ColorDot, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type PicklistValue = { value: string; color: string; is_active: boolean };

type Picklist = {
  id: string;
  name: string;
  description: string | null;
  values: PicklistValue[];
  fields: { id: string; label: string; object_type: string }[];
  updated_at: string;
};

const OBJECT_LABELS: Record<string, string> = { lead: "Lead", contact: "Contact", account: "Account", opportunity: "Deal", case: "Case" };

export default function PicklistsPage() {
  return (
    <SettingsShell>
      <Picklists />
    </SettingsShell>
  );
}

function Picklists() {
  const toast = useToast();
  const picklists = useApi<{ items: Picklist[]; can_edit: boolean }>("/picklists");
  const [editing, setEditing] = useState<Picklist | "new" | null>(null);
  const [deleting, setDeleting] = useState<Picklist | null>(null);
  const [busy, setBusy] = useState(false);
  const items = picklists.data?.items ?? [];
  const canEdit = Boolean(picklists.data?.can_edit);

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/picklists/${deleting.id}`);
      toast("Picklist deleted.");
      setDeleting(null);
      await picklists.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Global picklists"
        description="One list of values that several fields share — regions, product lines, industries. Change the list here and every field that uses it updates."
        actions={
          canEdit ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New picklist
            </button>
          ) : null
        }
      />
      {picklists.error ? <Alert>{picklists.error}</Alert> : null}
      <Card flush>
        {picklists.loading && !picklists.data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Picklist</th>
                  <th>Values</th>
                  <th>Used by</th>
                  <th>Updated</th>
                  {canEdit ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {items.map((list) => {
                  const active = list.values.filter((v) => v.is_active);
                  return (
                    <tr key={list.id}>
                      <td style={{ maxWidth: 260 }}>
                        <div className="cell-title">{list.name}</div>
                        {list.description ? <div className="ui-muted ui-small ui-truncate">{list.description}</div> : null}
                      </td>
                      <td>
                        <div className="dm-chips">
                          {active.slice(0, 6).map((v) => (
                            <span key={v.value} className="dm-chip">
                              <ColorDot color={v.color} /> {v.value}
                            </span>
                          ))}
                          {active.length > 6 ? <span className="ui-muted ui-small">+{active.length - 6} more</span> : null}
                          {!active.length ? <span className="ui-muted ui-small">No active values</span> : null}
                        </div>
                      </td>
                      <td>
                        {list.fields.length ? (
                          <span className="ui-small" title={list.fields.map((f) => `${OBJECT_LABELS[f.object_type] ?? f.object_type}: ${f.label}`).join("\n")}>
                            {list.fields.length} field{list.fields.length === 1 ? "" : "s"}
                          </span>
                        ) : (
                          <span className="ui-muted ui-small">Not used yet</span>
                        )}
                      </td>
                      <td className="ui-muted ui-small ui-nowrap">{relativeTime(list.updated_at)}</td>
                      {canEdit ? (
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${list.name}`} onClick={() => setEditing(list)}>
                            <Pencil size={14} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${list.name}`} onClick={() => setDeleting(list)}>
                            <Trash2 size={14} />
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Tags size={20} />}
            title="No picklists yet"
            message={
              <>
                Create a list once, then pick it when you add a picklist or multi-select field in <Link href="/settings/fields">Fields & modules</Link>.
              </>
            }
          />
        )}
      </Card>

      <PicklistModal
        picklist={editing}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await picklists.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete picklist?"
        message={
          deleting?.fields.length
            ? `${deleting.fields.length} field${deleting.fields.length === 1 ? " uses" : "s use"} this list. Give ${deleting.fields.length === 1 ? "it" : "them"} their own options in Fields & modules first.`
            : `“${deleting?.name ?? ""}” will be deleted. Nothing uses it yet.`
        }
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function PicklistModal({ picklist, onClose, onSaved }: { picklist: Picklist | "new" | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [values, setValues] = useState<PicklistValue[]>([]);
  const [bulk, setBulk] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!picklist) return;
    const existing = picklist === "new" ? null : picklist;
    setName(existing?.name ?? "");
    setDescription(existing?.description ?? "");
    setValues(existing?.values.length ? existing.values.map((v) => ({ ...v })) : [{ value: "", color: "gray", is_active: true }]);
    setBulk("");
    setError(null);
  }, [picklist]);

  function addBulk() {
    const parts = bulk
      .split(/[\n,]/)
      .map((p) => p.trim())
      .filter(Boolean);
    if (!parts.length) return;
    setValues((current) => {
      const kept = current.filter((v) => v.value.trim());
      const next = [...kept];
      for (const part of parts) if (!next.some((v) => v.value.toLowerCase() === part.toLowerCase())) next.push({ value: part, color: "gray", is_active: true });
      return next;
    });
    setBulk("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { name, description: description || null, values: values.filter((v) => v.value.trim()).map((v) => ({ ...v, value: v.value.trim() })) };
    try {
      if (picklist === "new") await api.post("/picklists", payload);
      else if (picklist) await api.put(`/picklists/${picklist.id}`, payload);
      toast(picklist === "new" ? "Picklist created." : "Picklist saved. Fields that use it now show the new values.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  const used = picklist && picklist !== "new" ? picklist.fields : [];

  return (
    <Modal
      open={Boolean(picklist)}
      title={picklist === "new" ? "New picklist" : "Edit picklist"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="picklist-form" className="ui-btn ui-btn-primary" disabled={saving || !name.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Save picklist
          </button>
        </>
      }
    >
      <form id="picklist-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        {used.length ? (
          <Alert tone="info">
            Used by {used.map((f) => `${OBJECT_LABELS[f.object_type] ?? f.object_type} → ${f.label}`).join(", ")}. Turning a value off hides it from those fields; records keep what they have.
          </Alert>
        ) : null}
        <div className="ui-form-grid">
          <Field label="Name" required>
            <input className="ui-input" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="Sales regions" />
          </Field>
          <Field label="Description">
            <input className="ui-input" value={description} maxLength={2000} onChange={(event) => setDescription(event.target.value)} placeholder="Where a customer is based" />
          </Field>
        </div>
        <div>
          <div className="ui-row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <strong className="ui-small">Values</strong>
            <span className="ui-muted ui-small">{values.filter((v) => v.value.trim() && v.is_active).length} active</span>
          </div>
          <div className="settings-list">
            {values.map((value, index) => (
              <div key={index} className="settings-list-row">
                <div className="settings-list-handle">
                  <button type="button" aria-label="Move up" disabled={index === 0} onClick={() => setValues(shift(values, index, -1))}>
                    <ArrowUp size={13} />
                  </button>
                  <button type="button" aria-label="Move down" disabled={index === values.length - 1} onClick={() => setValues(shift(values, index, 1))}>
                    <ArrowDown size={13} />
                  </button>
                </div>
                <div className="settings-list-fields">
                  <ColorDot color={value.color} />
                  <input
                    className="ui-input sm grow"
                    aria-label="Value"
                    value={value.value}
                    maxLength={120}
                    onChange={(event) => setValues(values.map((v, i) => (i === index ? { ...v, value: event.target.value } : v)))}
                  />
                  <select
                    className="ui-select sm"
                    aria-label="Color"
                    style={{ width: 110 }}
                    value={value.color}
                    onChange={(event) => setValues(values.map((v, i) => (i === index ? { ...v, color: event.target.value } : v)))}
                  >
                    {STAGE_COLORS.map((color) => (
                      <option key={color} value={color}>
                        {color[0].toUpperCase() + color.slice(1)}
                      </option>
                    ))}
                  </select>
                  <span className="ui-row" style={{ gap: 6 }}>
                    <Toggle label="Active" checked={value.is_active} onChange={(on) => setValues(values.map((v, i) => (i === index ? { ...v, is_active: on } : v)))} />
                    {!value.is_active ? <Badge>Hidden</Badge> : null}
                  </span>
                </div>
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove value" onClick={() => setValues(values.filter((_, i) => i !== index))}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
          <div className="ui-row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => setValues([...values, { value: "", color: "gray", is_active: true }])}>
              <Plus size={14} /> Add value
            </button>
          </div>
          <Field label="Paste several at once" hint="One per line or separated by commas." className="dm-bulk">
            <div className="ui-row" style={{ gap: 8, alignItems: "flex-start" }}>
              <textarea className="ui-textarea" rows={2} value={bulk} onChange={(event) => setBulk(event.target.value)} placeholder={"North\nSouth\nWest"} aria-label="Values to add" />
              <button type="button" className="ui-btn ui-btn-sm" onClick={addBulk} disabled={!bulk.trim()}>
                <ListPlus size={14} /> Add
              </button>
            </div>
          </Field>
        </div>
      </form>
    </Modal>
  );
}

function shift<T>(list: T[], index: number, delta: number) {
  const next = [...list];
  const [item] = next.splice(index, 1);
  next.splice(index + delta, 0, item);
  return next;
}
