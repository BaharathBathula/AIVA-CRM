"use client";

import Link from "next/link";
import { Lock, Pencil, Settings2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useRefOptions } from "@/components/field-inputs";
import { useSession, useToast } from "@/components/providers";
import { Alert, Card, Field, Modal } from "@/components/ui";
import { date, dateTime, moneyExact, number } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

export interface CustomFieldDef {
  id: string;
  object_type: string;
  key: string;
  label: string;
  field_type: string;
  type_label: string;
  required: boolean;
  options: string[];
  help_text: string | null;
  position: number;
  step: number;
  is_encrypted: boolean;
  is_active: boolean;
  config: Record<string, unknown>;
}

export interface FieldValues {
  fields: CustomFieldDef[];
  values: Record<string, unknown>;
  labels: Record<string, string>;
}

/** The form control for one custom field. */
export function FieldInput({
  field,
  value,
  onChange,
}: {
  field: CustomFieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const target = field.field_type === "lookup" ? String(field.config.target ?? "") : null;
  const options = useRefOptions(target);
  const text = value === null || value === undefined ? "" : String(value);
  switch (field.field_type) {
    case "textarea":
      return <textarea className="ui-textarea" rows={3} value={text} onChange={(e) => onChange(e.target.value)} />;
    case "number":
    case "currency":
    case "percent":
      return <input className="ui-input" type="number" step="any" value={text} onChange={(e) => onChange(e.target.value)} />;
    case "date":
      return <input className="ui-input" type="date" value={text.slice(0, 10)} onChange={(e) => onChange(e.target.value)} />;
    case "datetime":
      return <input className="ui-input" type="datetime-local" value={text.slice(0, 16)} onChange={(e) => onChange(e.target.value)} />;
    case "checkbox":
      return (
        <label className="ui-check">
          <input type="checkbox" checked={value === true || value === "true"} onChange={(e) => onChange(e.target.checked)} /> Yes
        </label>
      );
    case "picklist":
      return (
        <select className="ui-select" value={text} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    case "multiselect": {
      const selected = Array.isArray(value) ? (value as string[]) : [];
      return (
        <div className="chip-list">
          {field.options.map((option) => {
            const on = selected.includes(option);
            return (
              <button
                key={option}
                type="button"
                className={`chip${on ? " on" : ""}`}
                aria-pressed={on}
                onClick={() => onChange(on ? selected.filter((item) => item !== option) : [...selected, option])}
              >
                {option}
              </button>
            );
          })}
        </div>
      );
    }
    case "lookup":
      return (
        <select className="ui-select" value={text} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      );
    case "email":
      return <input className="ui-input" type="email" value={text} onChange={(e) => onChange(e.target.value)} />;
    case "phone":
      return <input className="ui-input" type="tel" value={text} onChange={(e) => onChange(e.target.value)} />;
    case "url":
      return <input className="ui-input" type="url" value={text} placeholder="https://" onChange={(e) => onChange(e.target.value)} />;
    case "formula":
    case "auto_number":
      return <input className="ui-input" value={text} disabled placeholder="Filled in automatically" />;
    default:
      return <input className="ui-input" value={text} onChange={(e) => onChange(e.target.value)} />;
  }
}

/** A read-only rendering of a custom field value. */
export function FieldValue({ field, value, label }: { field: CustomFieldDef; value: unknown; label?: string }) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) {
    return <span className="field-value empty">—</span>;
  }
  let shown: string;
  switch (field.field_type) {
    case "currency":
      shown = moneyExact(Number(value), String(field.config.currency ?? "USD"));
      break;
    case "percent":
      shown = `${number(Number(value), 2)}%`;
      break;
    case "number":
      shown = number(Number(value), 2).replace(/\.00$/, "");
      break;
    case "date":
      shown = date(String(value));
      break;
    case "datetime":
      shown = dateTime(String(value));
      break;
    case "checkbox":
      shown = value === true || value === "true" ? "Yes" : "No";
      break;
    case "multiselect":
      shown = (value as string[]).join(", ");
      break;
    case "lookup":
      shown = label ?? String(value);
      break;
    default:
      shown = String(value);
  }
  if (field.field_type === "url") {
    return (
      <a className="field-value" href={shown} target="_blank" rel="noopener noreferrer">
        {shown}
      </a>
    );
  }
  if (field.field_type === "email") {
    return (
      <a className="field-value" href={`mailto:${shown}`}>
        {shown}
      </a>
    );
  }
  return (
    <span className="field-value">
      {shown}
      {field.is_encrypted ? <Lock size={11} style={{ marginLeft: 6, color: "var(--muted)" }} aria-label="Encrypted" /> : null}
    </span>
  );
}

/** Edit form for a set of custom fields (optionally split into steps). */
export function FieldsForm({
  fields,
  values,
  onChange,
  step,
}: {
  fields: CustomFieldDef[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
  step?: number;
}) {
  const shown = fields.filter((f) => f.is_active && (step === undefined || f.step === step));
  return (
    <div className="field-grid">
      {shown.map((field) => (
        <Field
          key={field.key}
          label={
            <>
              {field.label}
              {field.is_encrypted ? <Lock size={11} style={{ marginLeft: 6 }} aria-label="Encrypted" /> : null}
            </>
          }
          required={field.required}
          hint={field.help_text ?? (field.field_type === "formula" ? `= ${String(field.config.expression ?? "")}` : undefined)}
          className={field.field_type === "textarea" || field.field_type === "multiselect" ? "span-2" : undefined}
        >
          <FieldInput field={field} value={values[field.key]} onChange={(next) => onChange({ ...values, [field.key]: next })} />
        </Field>
      ))}
    </div>
  );
}

/** Custom fields on a standard record's detail page. */
export function CustomFieldsCard({ objectType, recordId }: { objectType: string; recordId: string }) {
  const { can } = useSession();
  const toast = useToast();
  const data = useApi<FieldValues>(`/custom-fields/values/${objectType}/${recordId}`);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fields = useMemo(() => (data.data?.fields ?? []).filter((f) => f.is_active), [data.data]);

  useEffect(() => {
    if (editing && data.data) {
      setDraft({ ...data.data.values });
      setError(null);
    }
  }, [editing, data.data]);

  if (!data.data) return null;
  if (!fields.length) {
    return can("admin.settings") ? (
      <Card title="Custom fields" icon={<Settings2 size={16} />}>
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Track anything else about these records. <Link href={`/settings/fields?object=${objectType}`}>Add custom fields</Link>
        </p>
      </Card>
    ) : null;
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const values = Object.fromEntries(
        fields.filter((f) => !["formula", "auto_number"].includes(f.field_type)).map((f) => [f.key, draft[f.key] ?? null])
      );
      const result = await api.put<FieldValues>(`/custom-fields/values/${objectType}/${recordId}`, { values });
      data.setData(result);
      setEditing(false);
      toast("Saved.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card
        title="More details"
        icon={<Settings2 size={16} />}
        actions={
          can("records.write") ? (
            <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => setEditing(true)}>
              <Pencil size={14} /> Edit
            </button>
          ) : null
        }
      >
        <dl className="kv">
          {fields.map((field) => (
            <div key={field.key} style={{ display: "contents" }}>
              <dt>{field.label}</dt>
              <dd>
                <FieldValue field={field} value={data.data!.values[field.key]} label={data.data!.labels[field.key]} />
              </dd>
            </div>
          ))}
        </dl>
      </Card>
      <Modal
        open={editing}
        title="Edit details"
        onClose={() => setEditing(false)}
        size="lg"
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
              Save
            </button>
          </>
        }
      >
        {error ? <Alert>{error}</Alert> : null}
        <FieldsForm fields={fields} values={draft} onChange={setDraft} />
      </Modal>
    </>
  );
}
