"use client";

import Link from "next/link";
import { ArrowDown, ArrowUp, ArrowRightLeft, Boxes, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { CustomFieldDef } from "@/components/custom-fields";
import { useToast } from "@/components/providers";
import { COLORS, ColorDot, SettingsBody, useWorkspaceSettings } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, Field, LoadingRows, Modal } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

/**
 * Pieces shared by the "fields & stages" settings pages: an editor for the
 * contact and account stage lists, and a summary of an object's custom fields.
 */

export type StageCategory = "open" | "positive" | "negative" | "closed";

export type Stage = { key: string; label: string; category: StageCategory; color: string };

export type StageSettings = { contact: Stage[]; account: Stage[] };

type StageUsage = {
  contact: (Stage & { count: number })[];
  account: (Stage & { count: number })[];
  unknown: { contact: Record<string, number>; account: Record<string, number> };
  can_edit: boolean;
};

export const CATEGORY_LABELS: Record<StageCategory, { label: string; hint: string }> = {
  open: { label: "Open", hint: "Still being worked" },
  positive: { label: "Positive", hint: "Good outcome so far" },
  negative: { label: "Negative", hint: "Didn't work out (for now)" },
  closed: { label: "Closed", hint: "Stop reaching out" },
};

/** Colors used by stages and picklists; includes "orange", which the default stages use. */
export const STAGE_COLORS: string[] = [...COLORS];

function slug(label: string) {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
}

function uniqueKey(base: string, taken: string[]) {
  const root = base || "stage";
  let key = root;
  let n = 2;
  while (taken.includes(key)) {
    const suffix = `_${n++}`;
    key = `${root.slice(0, 40 - suffix.length)}${suffix}`;
  }
  return key;
}

export function StageEditor({ objectType }: { objectType: "contact" | "account" }) {
  const toast = useToast();
  const state = useWorkspaceSettings<StageSettings>("stages");
  const usage = useApi<StageUsage>("/stages");
  const [moving, setMoving] = useState<{ stage: Stage; count: number } | null>(null);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const noun = objectType === "contact" ? "contact" : "account";

  const counts = useMemo(() => Object.fromEntries((usage.data?.[objectType] ?? []).map((s) => [s.key, s.count])), [usage.data, objectType]);
  const savedKeys = useMemo(() => (state.saved?.[objectType] ?? []).map((s) => s.key), [state.saved, objectType]);
  const unknown = usage.data?.unknown[objectType] ?? {};

  function update(list: Stage[]) {
    state.set(objectType, list);
  }

  async function moveRecords() {
    if (!moving || !target) return;
    setBusy(true);
    try {
      const result = await api.post<{ moved: number }>("/stages/move", { object_type: objectType, from_stage: moving.stage.key, to_stage: target });
      const list = state.draft?.[objectType] ?? [];
      update(list.filter((s) => s.key !== moving.stage.key));
      toast(`Moved ${result.moved} ${noun}${result.moved === 1 ? "" : "s"}. Save to remove “${moving.stage.label}”.`);
      setMoving(null);
      await usage.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsBody state={state}>
      {(draft) => {
        const list = draft[objectType];
        const off = !state.canEdit;
        const keys = list.map((s) => s.key);
        return (
          <div className="ui-stack">
            {Object.keys(unknown).length ? (
              <Alert tone="warning">
                Some {noun}s use stages that aren&apos;t in this list ({Object.entries(unknown).map(([k, v]) => `${k}: ${v}`).join(", ")}). Add a stage with that key or move them.
              </Alert>
            ) : null}
            <Card
              title={`${noun === "contact" ? "Contact" : "Account"} stages`}
              actions={
                !off ? (
                  <button
                    type="button"
                    className="ui-btn ui-btn-sm"
                    onClick={() => update([...list, { key: uniqueKey("new_stage", keys), label: "New stage", category: "open", color: "gray" }])}
                    disabled={list.length >= 40}
                  >
                    <Plus size={14} /> Add stage
                  </button>
                ) : null
              }
            >
              <p className="ui-muted ui-small" style={{ marginTop: 0 }}>
                {noun === "contact"
                  ? "Stages show where each person is in your outreach. Sequences move people along automatically (Settings → Prospecting configuration)."
                  : "Stages show where each company is in its relationship with you."}{" "}
                The order here is the order everywhere else.
              </p>
              <div className="settings-list">
                {list.map((stage, index) => {
                  const isNew = !savedKeys.includes(stage.key);
                  const count = counts[stage.key] ?? 0;
                  return (
                    <div key={index} className="settings-list-row">
                      <div className="settings-list-handle">
                        <button type="button" aria-label={`Move ${stage.label} up`} disabled={off || index === 0} onClick={() => update(move(list, index, -1))}>
                          <ArrowUp size={13} />
                        </button>
                        <button type="button" aria-label={`Move ${stage.label} down`} disabled={off || index === list.length - 1} onClick={() => update(move(list, index, 1))}>
                          <ArrowDown size={13} />
                        </button>
                      </div>
                      <div className="settings-list-fields">
                        <ColorDot color={stage.color} />
                        <input
                          className="ui-input sm grow"
                          aria-label="Stage name"
                          value={stage.label}
                          maxLength={60}
                          disabled={off}
                          onChange={(event) => {
                            const label = event.target.value;
                            update(
                              list.map((s, i) =>
                                i === index ? { ...s, label, key: isNew ? uniqueKey(slug(label), keys.filter((_, j) => j !== index)) : s.key } : s,
                              ),
                            );
                          }}
                        />
                        <select
                          className="ui-select sm"
                          aria-label="Category"
                          style={{ width: 130 }}
                          value={stage.category}
                          disabled={off}
                          onChange={(event) => update(list.map((s, i) => (i === index ? { ...s, category: event.target.value as StageCategory } : s)))}
                        >
                          {(Object.keys(CATEGORY_LABELS) as StageCategory[]).map((category) => (
                            <option key={category} value={category}>
                              {CATEGORY_LABELS[category].label}
                            </option>
                          ))}
                        </select>
                        <select
                          className="ui-select sm"
                          aria-label="Color"
                          style={{ width: 122 }}
                          value={stage.color}
                          disabled={off}
                          onChange={(event) => update(list.map((s, i) => (i === index ? { ...s, color: event.target.value } : s)))}
                        >
                          {STAGE_COLORS.map((color) => (
                            <option key={color} value={color}>
                              {color[0].toUpperCase() + color.slice(1)}
                            </option>
                          ))}
                        </select>
                        <span className="ui-muted ui-small ui-nowrap" title={`Key: ${stage.key}`}>
                          {isNew ? <Badge tone="info">New</Badge> : `${count} ${noun}${count === 1 ? "" : "s"}`}
                        </span>
                      </div>
                      {!off ? (
                        <button
                          type="button"
                          className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                          aria-label={`Remove ${stage.label}`}
                          disabled={list.length <= 1}
                          onClick={() => {
                            if (!isNew && count > 0) {
                              setTarget(list.find((s) => s.key !== stage.key && savedKeys.includes(s.key))?.key ?? "");
                              setMoving({ stage, count });
                            } else {
                              update(list.filter((_, i) => i !== index));
                            }
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      ) : (
                        <span />
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="dm-legend">
                {(Object.keys(CATEGORY_LABELS) as StageCategory[]).map((category) => (
                  <span key={category}>
                    <strong>{CATEGORY_LABELS[category].label}:</strong> {CATEGORY_LABELS[category].hint}
                  </span>
                ))}
              </div>
            </Card>

            <Modal
              open={Boolean(moving)}
              title={`Move ${moving?.count ?? 0} ${noun}${moving?.count === 1 ? "" : "s"} first`}
              description={`“${moving?.stage.label ?? ""}” still has ${noun}s in it. Choose where they should go, then save to remove the stage.`}
              onClose={() => setMoving(null)}
              size="sm"
              footer={
                <>
                  <button type="button" className="ui-btn" onClick={() => setMoving(null)} disabled={busy}>
                    Cancel
                  </button>
                  <button type="button" className="ui-btn ui-btn-primary" onClick={() => void moveRecords()} disabled={busy || !target}>
                    {busy ? <span className="ui-spinner" /> : <ArrowRightLeft size={15} />} Move them
                  </button>
                </>
              }
            >
              <Field label="Move them to">
                <select className="ui-select" value={target} onChange={(event) => setTarget(event.target.value)}>
                  <option value="">Choose a stage…</option>
                  {(state.saved?.[objectType] ?? [])
                    .filter((s) => s.key !== moving?.stage.key)
                    .map((s) => (
                      <option key={s.key} value={s.key}>
                        {s.label}
                      </option>
                    ))}
                </select>
              </Field>
            </Modal>
          </div>
        );
      }}
    </SettingsBody>
  );
}

function move<T>(list: T[], index: number, delta: number) {
  const next = [...list];
  const [item] = next.splice(index, 1);
  next.splice(index + delta, 0, item);
  return next;
}

/** The custom fields on one object, with a link to manage them. */
export function CustomFieldsSummary({ objectType, title }: { objectType: string; title: string }) {
  const fields = useApi<CustomFieldDef[]>(`/custom-fields?object_type=${encodeURIComponent(objectType)}`);
  const rows = fields.data ?? [];
  return (
    <Card
      title={title}
      flush
      actions={
        <Link className="ui-btn ui-btn-sm" href={`/settings/fields?object=${objectType}`}>
          <Boxes size={14} /> Manage fields
        </Link>
      }
    >
      {fields.error ? (
        <div style={{ padding: 16 }}>
          <Alert tone="info">{fields.error}</Alert>
        </div>
      ) : fields.loading && !fields.data ? (
        <LoadingRows rows={3} />
      ) : rows.length ? (
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Type</th>
                <th>Required</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((field) => (
                <tr key={field.id} style={{ opacity: field.is_active ? 1 : 0.55 }}>
                  <td>
                    <div className="cell-title">{field.label}</div>
                    <div className="ui-muted ui-small ui-mono">{field.key}</div>
                  </td>
                  <td>{field.type_label}</td>
                  <td>{field.required ? <Badge tone="warning">Required</Badge> : <span className="ui-muted">Optional</span>}</td>
                  <td>{field.is_active ? <Badge tone="success">Active</Badge> : <Badge>Hidden</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No custom fields yet" message="Standard fields are always there. Add your own to track anything else." />
      )}
    </Card>
  );
}
