"use client";

import { ExternalLink, MousePointerClick, Pencil, Plus, Trash2, Webhook, Workflow, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime, titleCase } from "@/lib/format";
import type { CustomModuleSummary } from "@/lib/modules";
import type { Catalog } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Step =
  | { type: "set_field"; field: string; expression: string }
  | { type: "create_task"; title: string; due_in_days: number }
  | { type: "notify"; message: string };

interface Button {
  id: string;
  object_type: string;
  label: string;
  description: string | null;
  action_type: "open_url" | "webhook" | "function";
  config: { url?: string; new_tab?: boolean; secret?: string; confirm?: boolean; steps?: Step[] };
  has_secret: boolean;
  is_active: boolean;
  run_count: number;
  last_run_at: string | null;
}

const STANDARD = [
  { key: "lead", label: "Leads" },
  { key: "contact", label: "Contacts" },
  { key: "account", label: "Accounts" },
  { key: "opportunity", label: "Opportunities" },
  { key: "case", label: "Cases" },
];

const ACTION_ICON = { open_url: ExternalLink, webhook: Webhook, function: Workflow };
const ACTION_LABEL = { open_url: "Open a link", webhook: "Call a webhook", function: "Run a function" };

export default function ButtonsPage() {
  return (
    <SettingsShell permission="admin.settings" what="custom buttons" feature="custom_buttons">
      <Buttons />
    </SettingsShell>
  );
}

function Buttons() {
  const toast = useToast();
  const buttons = useApi<Button[]>("/buttons");
  const modules = useApi<CustomModuleSummary[]>("/custom-modules");
  const [editing, setEditing] = useState<Button | "new" | null>(null);
  const [deleting, setDeleting] = useState<Button | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const objects = useMemo(() => [...STANDARD, ...(modules.data ?? []).map((m) => ({ key: m.key, label: m.name }))], [modules.data]);
  const objectLabel = (key: string) => objects.find((o) => o.key === key)?.label ?? titleCase(key);

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/buttons/${deleting.id}`);
      toast("Button deleted.");
      setDeleting(null);
      await buttons.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Custom buttons"
        description="Add one-click buttons to record pages: open another system with the record's details, send it to a webhook, or run a small function that updates fields and creates tasks."
        actions={
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
            <Plus size={16} /> New button
          </button>
        }
      />
      {buttons.error ? <Alert>{buttons.error}</Alert> : null}
      <Card flush>
        {buttons.loading && !buttons.data ? (
          <LoadingRows />
        ) : buttons.data?.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Button</th>
                  <th>Shows on</th>
                  <th>Does</th>
                  <th>Used</th>
                  <th>Status</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {buttons.data.map((button) => {
                  const Icon = ACTION_ICON[button.action_type];
                  return (
                    <tr key={button.id}>
                      <td className="cell-title">
                        {button.label}
                        {button.description ? <div className="ui-muted ui-small">{button.description}</div> : null}
                      </td>
                      <td>{objectLabel(button.object_type)}</td>
                      <td>
                        <span className="ui-row" style={{ gap: 6 }}>
                          <Icon size={14} /> {ACTION_LABEL[button.action_type]}
                        </span>
                        {button.action_type === "function" ? (
                          <div className="ui-muted ui-small">{button.config.steps?.length ?? 0} steps</div>
                        ) : button.config.url ? (
                          <div className="ui-muted ui-small ui-mono" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>
                            {button.config.url}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        {button.run_count}×{button.last_run_at ? <div className="ui-muted ui-small">{relativeTime(button.last_run_at)}</div> : null}
                      </td>
                      <td>{button.is_active ? <Badge tone="success">On</Badge> : <Badge>Off</Badge>}</td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${button.label}`} onClick={() => setEditing(button)}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${button.label}`} onClick={() => setDeleting(button)}>
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<MousePointerClick size={20} />}
            title="No custom buttons"
            message="For example: “Open in Google Maps” on accounts, or “Mark as VIP” that sets a field and creates a task for the account owner."
          />
        )}
      </Card>
      <ButtonEditor
        button={editing}
        objects={objects}
        onClose={() => setEditing(null)}
        onSaved={(saved) => {
          setEditing(null);
          if (saved.action_type === "webhook" && saved.config.secret) setSecret(saved.config.secret);
          void buttons.reload();
        }}
      />
      <Modal open={Boolean(secret)} title="Webhook signing secret" onClose={() => setSecret(null)}>
        <div className="ui-stack">
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Every webhook call carries an <code>X-AIVA-Signature: sha256=…</code> header — an HMAC of the body with this secret — so your endpoint can verify it came from AIVA.
          </p>
          {secret ? <CopyField value={secret} label="Copy secret" /> : null}
        </div>
      </Modal>
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.label ?? "button"}?`}
        message="It disappears from every record page."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function ButtonEditor({
  button,
  objects,
  onClose,
  onSaved,
}: {
  button: Button | "new" | null;
  objects: { key: string; label: string }[];
  onClose: () => void;
  onSaved: (saved: Button) => void;
}) {
  const open = button !== null;
  const existing = button && button !== "new" ? button : null;
  const catalog = useApi<Catalog>(open ? "/meta/catalog" : null);
  const [objectType, setObjectType] = useState("account");
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [actionType, setActionType] = useState<Button["action_type"]>("open_url");
  const [url, setUrl] = useState("");
  const [newTab, setNewTab] = useState(true);
  const [confirm, setConfirm] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const customFields = useApi<{ key: string; label: string; field_type: string; is_active: boolean }[]>(open ? `/custom-fields?object_type=${objectType}` : null);

  useEffect(() => {
    if (!open) return;
    setObjectType(existing?.object_type ?? "account");
    setLabel(existing?.label ?? "");
    setDescription(existing?.description ?? "");
    setActionType(existing?.action_type ?? "open_url");
    setUrl(existing?.config.url ?? "");
    setNewTab(existing?.config.new_tab ?? true);
    setConfirm(existing?.config.confirm ?? false);
    setSteps(existing?.config.steps ?? [{ type: "create_task", title: "Follow up on {{name}}", due_in_days: 1 }]);
    setActive(existing?.is_active ?? true);
    setError(null);
  }, [open, existing]);

  const settable = useMemo(() => {
    const standard = (catalog.data?.objects.find((o) => o.key === objectType)?.fields ?? []).filter((f) => f.updatable && !f.virtual).map((f) => ({ key: f.key, label: f.label }));
    const custom = (customFields.data ?? [])
      .filter((f) => f.is_active && !["formula", "auto_number"].includes(f.field_type))
      .map((f) => ({ key: `cf.${f.key}`, label: `${f.label} (custom)` }));
    return [...standard, ...custom];
  }, [catalog.data, customFields.data, objectType]);

  const placeholders = useMemo(() => {
    const standard = (catalog.data?.objects.find((o) => o.key === objectType)?.fields ?? []).filter((f) => !f.virtual).slice(0, 8).map((f) => f.key);
    return [...(standard.length ? standard : ["id", "name"]), ...(customFields.data ?? []).slice(0, 4).map((f) => `cf.${f.key}`)];
  }, [catalog.data, customFields.data, objectType]);

  function setStep(index: number, next: Step) {
    setSteps(steps.map((s, i) => (i === index ? next : s)));
  }

  async function save() {
    setBusy(true);
    setError(null);
    const config = actionType === "function" ? { steps, confirm } : actionType === "webhook" ? { url, confirm } : { url, new_tab: newTab };
    const body = { object_type: objectType, label: label.trim(), description: description.trim() || null, action_type: actionType, config, is_active: active };
    try {
      const saved = existing ? await api.put<Button>(`/buttons/${existing.id}`, body) : await api.post<Button>("/buttons", body);
      onSaved(saved);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={existing ? `Edit ${existing.label}` : "New button"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <label className="ui-check" style={{ marginRight: "auto" }}>
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> On
          </label>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !label.trim()} onClick={() => void save()}>
            Save button
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Button label" required>
            <input className="ui-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Open in Maps" maxLength={60} />
          </Field>
          <Field label="Shows on">
            <select className="ui-select" value={objectType} onChange={(e) => setObjectType(e.target.value)}>
              {objects.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="Description">
          <input className="ui-input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="When clicked">
          <div className="chip-list">
            {(Object.keys(ACTION_LABEL) as Button["action_type"][]).map((key) => {
              const Icon = ACTION_ICON[key];
              return (
                <button key={key} type="button" className={`chip${actionType === key ? " on" : ""}`} aria-pressed={actionType === key} onClick={() => setActionType(key)}>
                  <Icon size={13} /> {ACTION_LABEL[key]}
                </button>
              );
            })}
          </div>
        </Field>
        {actionType !== "function" ? (
          <Field
            label={actionType === "webhook" ? "Webhook URL (https)" : "Link"}
            hint={actionType === "open_url" ? <>Insert record values with {"{{field}}"}, for example https://maps.google.com/?q={"{{billing_city}}"}</> : "AIVA POSTs the record as JSON, signed with a secret."}
          >
            <input className="ui-input ui-mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={actionType === "webhook" ? "https://hooks.example.com/aiva" : "https://"} />
          </Field>
        ) : null}
        {actionType === "open_url" ? (
          <label className="ui-check">
            <input type="checkbox" checked={newTab} onChange={(e) => setNewTab(e.target.checked)} /> Open in a new tab
          </label>
        ) : (
          <label className="ui-check">
            <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> Ask “Are you sure?” before running
          </label>
        )}
        {actionType === "open_url" ? (
          <div className="chip-list">
            {placeholders.map((key) => (
              <button key={key} type="button" className="chip" onClick={() => setUrl(`${url}{{${key}}}`)}>
                {`{{${key}}}`}
              </button>
            ))}
          </div>
        ) : null}
        {actionType === "function" ? (
          <Field label="Steps" hint="Formulas can use the record's fields, like IF(amount > 50000, &quot;high&quot;, &quot;medium&quot;).">
            <div className="ui-stack" style={{ gap: 10 }}>
              {steps.map((step, index) => (
                <div key={index} className="function-step">
                  <span className="process-index">{index + 1}</span>
                  <select
                    className="ui-select sm"
                    value={step.type}
                    aria-label="Step type"
                    onChange={(e) => {
                      const type = e.target.value as Step["type"];
                      setStep(
                        index,
                        type === "set_field"
                          ? { type, field: settable[0]?.key ?? "", expression: "" }
                          : type === "create_task"
                            ? { type, title: "", due_in_days: 1 }
                            : { type, message: "" },
                      );
                    }}
                  >
                    <option value="set_field">Set a field</option>
                    <option value="create_task">Create a task</option>
                    <option value="notify">Show a message</option>
                  </select>
                  {step.type === "set_field" ? (
                    <>
                      <select className="ui-select sm" value={step.field} onChange={(e) => setStep(index, { ...step, field: e.target.value })} aria-label="Field">
                        {settable.map((f) => (
                          <option key={f.key} value={f.key}>
                            {f.label}
                          </option>
                        ))}
                      </select>
                      <input className="ui-input sm ui-mono" value={step.expression} onChange={(e) => setStep(index, { ...step, expression: e.target.value })} placeholder={'"hot"'} aria-label="Value formula" />
                    </>
                  ) : step.type === "create_task" ? (
                    <>
                      <input className="ui-input sm" value={step.title} onChange={(e) => setStep(index, { ...step, title: e.target.value })} placeholder="Call {{first_name}}" aria-label="Task title" />
                      <input
                        className="ui-input sm"
                        type="number"
                        min={0}
                        value={step.due_in_days}
                        onChange={(e) => setStep(index, { ...step, due_in_days: Number(e.target.value) || 0 })}
                        aria-label="Due in days"
                        style={{ width: 80 }}
                      />
                    </>
                  ) : (
                    <input className="ui-input sm" value={step.message} onChange={(e) => setStep(index, { ...step, message: e.target.value })} placeholder="Marked as VIP" aria-label="Message" />
                  )}
                  <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove step" onClick={() => setSteps(steps.filter((_, i) => i !== index))}>
                    <X size={13} />
                  </button>
                </div>
              ))}
              {steps.length < 10 ? (
                <button type="button" className="ui-btn ui-btn-sm" style={{ width: "fit-content" }} onClick={() => setSteps([...steps, { type: "notify", message: "" }])}>
                  <Plus size={13} /> Add step
                </button>
              ) : null}
            </div>
          </Field>
        ) : null}
      </div>
    </Modal>
  );
}
