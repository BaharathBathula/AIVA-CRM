"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowDown, ArrowUp, Lock, Pencil, Plus, Settings2, Trash2 } from "lucide-react";
import { Suspense, useEffect, useMemo, useState } from "react";

import type { CustomFieldDef } from "@/components/custom-fields";
import { PlanChip } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { MODULE_ICONS, ModuleIcon } from "@/components/record-wizard";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { refreshCustomModules, type CustomModuleSummary } from "@/lib/modules";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface FieldTypes {
  types: { key: string; label: string; extended: boolean; available: boolean }[];
  lookup_targets: string[];
  standard_objects: { key: string; label: string }[];
  can_encrypt: boolean;
  can_use_steps: boolean;
}

const ENCRYPTABLE = ["text", "textarea", "email", "phone", "number", "date"];

export default function FieldsPage() {
  return (
    <SettingsShell permission="admin.settings" what="fields and modules">
      <Suspense>
        <FieldsSettings />
      </Suspense>
    </SettingsShell>
  );
}

function FieldsSettings() {
  const params = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const objectType = params.get("object") || "lead";
  const types = useApi<FieldTypes>("/custom-fields/types");
  const modules = useApi<CustomModuleSummary[]>("/custom-modules");
  const fields = useApi<CustomFieldDef[]>(`/custom-fields?object_type=${encodeURIComponent(objectType)}`);
  const [editing, setEditing] = useState<CustomFieldDef | "new" | null>(null);
  const [deleting, setDeleting] = useState<CustomFieldDef | null>(null);
  const [moduleSettings, setModuleSettings] = useState(false);
  const [busy, setBusy] = useState(false);

  const module = modules.data?.find((m) => m.key === objectType) ?? null;
  const objects = [
    ...(types.data?.standard_objects ?? []).map((o) => ({ ...o, module: null as CustomModuleSummary | null })),
    ...(modules.data ?? []).map((m) => ({ key: m.key, label: m.name, module: m })),
  ];
  const label = objects.find((o) => o.key === objectType)?.label ?? objectType;
  const rows = fields.data ?? [];

  async function move(index: number, delta: number) {
    const next = [...rows];
    const [item] = next.splice(index, 1);
    next.splice(index + delta, 0, item);
    fields.setData(next);
    try {
      await api.post("/custom-fields/reorder", { field_ids: next.map((f) => f.id) });
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      void fields.reload();
    }
  }

  async function toggleActive(field: CustomFieldDef) {
    try {
      await api.patch(`/custom-fields/${field.id}`, { ...payloadOf(field), is_active: !field.is_active });
      await fields.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/custom-fields/${deleting.id}`);
      toast(`${deleting.label} deleted.`);
      setDeleting(null);
      await fields.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Fields and modules"
        description="Add your own fields to any record type, or build whole new modules. Fields show on record pages, forms, imports, exports, reports and workflows."
        actions={
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
            <Plus size={16} /> New field
          </button>
        }
      />
      <div className="settings-split">
        <nav className="ui-card object-nav" aria-label="Record types">
          <span className="object-nav-head">Standard</span>
          {(types.data?.standard_objects ?? []).map((o) => (
            <button key={o.key} type="button" className={objectType === o.key ? "active" : undefined} onClick={() => router.replace(`/settings/fields?object=${o.key}`)}>
              {o.label}
            </button>
          ))}
          <span className="object-nav-head">
            Custom modules <PlanChip feature="custom_modules" />
          </span>
          {(modules.data ?? []).map((m) => (
            <button key={m.key} type="button" className={objectType === m.key ? "active" : undefined} onClick={() => router.replace(`/settings/fields?object=${m.key}`)}>
              <ModuleIcon icon={m.icon} size={14} /> {m.name}
              {!m.is_active ? <Badge>Off</Badge> : null}
            </button>
          ))}
          <Link href="/modules" className="object-nav-link">
            <Plus size={14} /> New module
          </Link>
        </nav>
        <Card
          flush
          title={label}
          actions={
            module ? (
              <>
                <Link href={`/modules/${module.key}`} className="ui-btn ui-btn-sm ui-btn-ghost">
                  Open records
                </Link>
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => setModuleSettings(true)}>
                  <Settings2 size={14} /> Module settings
                </button>
              </>
            ) : null
          }
        >
          {fields.error ? <Alert>{fields.error}</Alert> : null}
          {fields.loading && !fields.data ? (
            <LoadingRows />
          ) : rows.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th style={{ width: 70 }}>Order</th>
                    <th>Field</th>
                    <th>Type</th>
                    <th>Rules</th>
                    <th>Active</th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((field, index) => (
                    <tr key={field.id}>
                      <td>
                        <div className="ui-row" style={{ gap: 2 }}>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" disabled={index === 0} aria-label="Move up" onClick={() => void move(index, -1)}>
                            <ArrowUp size={13} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" disabled={index === rows.length - 1} aria-label="Move down" onClick={() => void move(index, 1)}>
                            <ArrowDown size={13} />
                          </button>
                        </div>
                      </td>
                      <td className="cell-title">
                        {field.label}
                        {field.is_encrypted ? <Lock size={12} style={{ marginLeft: 6, color: "var(--muted)" }} aria-label="Encrypted" /> : null}
                        <div className="ui-muted ui-small ui-mono">{field.key}</div>
                      </td>
                      <td>
                        {field.type_label}
                        {field.field_type === "formula" ? <div className="ui-muted ui-small ui-mono">= {String(field.config.expression ?? "")}</div> : null}
                        {field.field_type === "lookup" ? <div className="ui-muted ui-small">→ {String(field.config.target ?? "")}</div> : null}
                      </td>
                      <td>
                        <div className="chip-list">
                          {field.required ? <Badge tone="warning">Required</Badge> : null}
                          {field.step > 1 ? <Badge tone="info">Step {field.step}</Badge> : null}
                          {field.is_encrypted ? <Badge tone="primary">Encrypted</Badge> : null}
                          {field.options.length ? <Badge>{field.options.length} options</Badge> : null}
                        </div>
                      </td>
                      <td>
                        <label className="ui-switch">
                          <input type="checkbox" checked={field.is_active} onChange={() => void toggleActive(field)} aria-label={`${field.label} active`} />
                          <span />
                        </label>
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${field.label}`} onClick={() => setEditing(field)}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${field.label}`} onClick={() => setDeleting(field)}>
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<Settings2 size={20} />}
              title={`No custom fields on ${label.toLowerCase()} yet`}
              message="Add a field for anything else you need to track."
              action={
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                  <Plus size={15} /> New field
                </button>
              }
            />
          )}
        </Card>
      </div>
      {types.data ? (
        <FieldEditor
          open={editing !== null}
          field={editing === "new" ? null : editing}
          objectType={objectType}
          types={types.data}
          modules={modules.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void fields.reload();
            void modules.reload();
          }}
        />
      ) : null}
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.label ?? "field"}?`}
        message="The field and the values stored in it are removed from every record. To keep the data, turn the field off instead."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
      {module ? (
        <ModuleSettings
          open={moduleSettings}
          module={module}
          onClose={() => setModuleSettings(false)}
          onSaved={() => {
            setModuleSettings(false);
            void modules.reload();
            void refreshCustomModules();
          }}
          onDeleted={() => {
            setModuleSettings(false);
            void refreshCustomModules();
            void modules.reload();
            router.replace("/settings/fields?object=lead");
          }}
        />
      ) : null}
    </>
  );
}

function payloadOf(field: CustomFieldDef) {
  return {
    object_type: field.object_type,
    label: field.label,
    field_type: field.field_type,
    required: field.required,
    options: field.options,
    help_text: field.help_text,
    step: field.step,
    is_encrypted: field.is_encrypted,
    is_active: field.is_active,
    config: field.config,
  };
}

function FieldEditor({
  open,
  field,
  objectType,
  types,
  modules,
  onClose,
  onSaved,
}: {
  open: boolean;
  field: CustomFieldDef | null;
  objectType: string;
  types: FieldTypes;
  modules: CustomModuleSummary[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { has } = useSession();
  const [label, setLabel] = useState("");
  const [fieldType, setFieldType] = useState("text");
  const [required, setRequired] = useState(false);
  const [options, setOptions] = useState("");
  const [help, setHelp] = useState("");
  const [step, setStep] = useState(1);
  const [encrypted, setEncrypted] = useState(false);
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLabel(field?.label ?? "");
    setFieldType(field?.field_type ?? "text");
    setRequired(field?.required ?? false);
    setOptions((field?.options ?? []).join("\n"));
    setHelp(field?.help_text ?? "");
    setStep(field?.step ?? 1);
    setEncrypted(field?.is_encrypted ?? false);
    setConfig(field?.config ?? {});
    setError(null);
  }, [open, field]);

  const lookupTargets = useMemo(
    () => [
      ...types.lookup_targets.map((t) => ({ value: t, label: t === "user" ? "User" : `${t[0].toUpperCase()}${t.slice(1)}` })),
      ...modules.filter((m) => m.key !== objectType).map((m) => ({ value: `module:${m.key}`, label: m.singular })),
    ],
    [types.lookup_targets, modules, objectType],
  );

  async function save() {
    setBusy(true);
    setError(null);
    const body = {
      object_type: objectType,
      label: label.trim(),
      field_type: fieldType,
      required,
      options: options
        .split("\n")
        .map((o) => o.trim())
        .filter(Boolean),
      help_text: help.trim() || null,
      step,
      is_encrypted: encrypted,
      is_active: field?.is_active ?? true,
      config,
    };
    try {
      if (field) await api.patch(`/custom-fields/${field.id}`, body);
      else await api.post("/custom-fields", body);
      toast(field ? "Field saved." : `${body.label} added.`);
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const type = types.types.find((t) => t.key === fieldType);

  return (
    <Modal
      open={open}
      title={field ? `Edit ${field.label}` : "New field"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !label.trim() || (type ? !type.available && !field : false)} onClick={() => void save()}>
            {field ? "Save" : "Add field"}
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Label" required>
            <input className="ui-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Contract renewal date" autoFocus />
          </Field>
          <Field label="Type" hint={field ? "A field's type can't change after it's created." : undefined}>
            <select className="ui-select" value={fieldType} disabled={Boolean(field)} onChange={(e) => setFieldType(e.target.value)}>
              {types.types.map((t) => (
                <option key={t.key} value={t.key} disabled={!t.available}>
                  {t.label}
                  {!t.available ? " — Enterprise" : ""}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {!has("extended_fields") && !field ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Currency, email, lookup, formula, auto-number and other extended field types come with Enterprise. <PlanChip feature="extended_fields" />
          </p>
        ) : null}
        {fieldType === "picklist" || fieldType === "multiselect" ? (
          <Field label="Options" hint="One per line." required>
            <textarea className="ui-textarea" rows={5} value={options} onChange={(e) => setOptions(e.target.value)} placeholder={"Bronze\nSilver\nGold"} />
          </Field>
        ) : null}
        {fieldType === "lookup" ? (
          <Field label="Links to" required>
            <select className="ui-select" value={String(config.target ?? "")} onChange={(e) => setConfig({ ...config, target: e.target.value })}>
              <option value="">Choose…</option>
              {lookupTargets.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {fieldType === "currency" ? (
          <Field label="Currency">
            <input className="ui-input" value={String(config.currency ?? "USD")} maxLength={3} onChange={(e) => setConfig({ ...config, currency: e.target.value.toUpperCase() })} />
          </Field>
        ) : null}
        {fieldType === "auto_number" ? (
          <div className="ui-form-grid">
            <Field label="Prefix">
              <input className="ui-input" value={String(config.prefix ?? "")} onChange={(e) => setConfig({ ...config, prefix: e.target.value })} placeholder="PRJ-" />
            </Field>
            <Field label="Start at">
              <input className="ui-input" type="number" min={1} value={String(config.start ?? 1)} onChange={(e) => setConfig({ ...config, start: Number(e.target.value) || 1 })} />
            </Field>
          </div>
        ) : null}
        {fieldType === "formula" ? (
          <Field
            label="Formula"
            required
            hint="Use other field keys, + − × ÷, and functions like IF, ROUND, MIN, MAX, CONCAT, DAYS_BETWEEN, TODAY. Example: ROUND(amount * 0.1, 2)"
          >
            <textarea className="ui-textarea ui-mono" rows={3} value={String(config.expression ?? "")} onChange={(e) => setConfig({ ...config, expression: e.target.value })} />
          </Field>
        ) : null}
        <Field label="Help text" hint="Shown under the field on forms.">
          <input className="ui-input" value={help} onChange={(e) => setHelp(e.target.value)} />
        </Field>
        <div className="ui-form-grid">
          <Field label="Form step" hint={types.can_use_steps ? "Split long forms into steps." : undefined}>
            <select className="ui-select" value={step} disabled={!types.can_use_steps} onChange={(e) => setStep(Number(e.target.value))}>
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  Step {n}
                </option>
              ))}
            </select>
            {!types.can_use_steps ? <PlanChip feature="sequential_forms" /> : null}
          </Field>
          <div className="ui-stack" style={{ gap: 10, paddingTop: 26 }}>
            {fieldType !== "formula" && fieldType !== "auto_number" ? (
              <label className="ui-check">
                <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} /> Required
              </label>
            ) : null}
            {ENCRYPTABLE.includes(fieldType) ? (
              <label className="ui-check" title={field ? "Encryption is chosen when a field is created." : undefined}>
                <input type="checkbox" checked={encrypted} disabled={Boolean(field) || !types.can_encrypt} onChange={(e) => setEncrypted(e.target.checked)} /> Encrypt values
                {!types.can_encrypt ? <PlanChip feature="field_encryption" /> : null}
              </label>
            ) : null}
          </div>
        </div>
        {encrypted && !field ? (
          <Alert tone="info">Values are encrypted at rest. People without the &quot;View encrypted fields&quot; permission see them masked.</Alert>
        ) : null}
      </div>
    </Modal>
  );
}

function ModuleSettings({
  open,
  module,
  onClose,
  onSaved,
  onDeleted,
}: {
  open: boolean;
  module: CustomModuleSummary;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(module.name);
  const [singular, setSingular] = useState(module.singular);
  const [description, setDescription] = useState(module.description ?? "");
  const [icon, setIcon] = useState(module.icon);
  const [active, setActive] = useState(module.is_active);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(module.name);
    setSingular(module.singular);
    setDescription(module.description ?? "");
    setIcon(module.icon);
    setActive(module.is_active);
  }, [open, module]);

  async function save() {
    setBusy(true);
    try {
      await api.patch(`/custom-modules/${module.id}`, { name, singular, description: description || null, icon, is_active: active });
      toast("Module saved.");
      onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/custom-modules/${module.id}`);
      toast(`${module.name} deleted.`);
      setConfirming(false);
      onDeleted();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Modal
        open={open && !confirming}
        title="Module settings"
        onClose={onClose}
        footer={
          <>
            <button type="button" className="ui-btn ui-btn-danger" style={{ marginRight: "auto" }} onClick={() => setConfirming(true)}>
              <Trash2 size={14} /> Delete module
            </button>
            <button type="button" className="ui-btn" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim() || !singular.trim()} onClick={() => void save()}>
              Save
            </button>
          </>
        }
      >
        <div className="ui-stack">
          <div className="ui-form-grid">
            <Field label="Name (plural)" required>
              <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="One record is a…" required>
              <input className="ui-input" value={singular} onChange={(e) => setSingular(e.target.value)} />
            </Field>
          </div>
          <Field label="Description">
            <input className="ui-input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <Field label="Icon">
            <div className="chip-list">
              {Object.keys(MODULE_ICONS).map((key) => (
                <button key={key} type="button" className={`chip${icon === key ? " on" : ""}`} aria-pressed={icon === key} aria-label={key} onClick={() => setIcon(key)}>
                  <ModuleIcon icon={key} size={14} />
                </button>
              ))}
            </div>
          </Field>
          <label className="ui-check">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Module is on (shows in the Apps menu and accepts new records)
          </label>
        </div>
      </Modal>
      <ConfirmDialog
        open={confirming}
        title={`Delete ${module.name}?`}
        message={`All ${module.record_count} ${module.name.toLowerCase()} and their fields are deleted for good.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setConfirming(false)}
      />
    </>
  );
}
