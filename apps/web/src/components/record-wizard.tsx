"use client";

import {
  Box,
  Briefcase,
  Building,
  Calendar,
  Car,
  Check,
  File,
  Heart,
  Home,
  Shield,
  Star,
  Truck,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { FieldsForm, type CustomFieldDef } from "@/components/custom-fields";
import { Alert, Field, Modal } from "@/components/ui";
import { errorMessage } from "@/lib/use-api";

export const MODULE_ICONS: Record<string, LucideIcon> = {
  box: Box,
  car: Car,
  home: Home,
  building: Building,
  briefcase: Briefcase,
  file: File,
  shield: Shield,
  truck: Truck,
  wrench: Wrench,
  heart: Heart,
  star: Star,
  calendar: Calendar,
};

export function ModuleIcon({ icon, size = 16 }: { icon: string; size?: number }) {
  const Icon = MODULE_ICONS[icon] ?? Box;
  return <Icon size={size} />;
}

/**
 * Create or edit a custom-module record. When the module's fields are split
 * into steps (multi-page forms), the form walks through them one at a time.
 */
export function RecordWizard({
  open,
  title,
  singular,
  fields,
  initialName = "",
  initialValues = {},
  onClose,
  onSave,
}: {
  open: boolean;
  title: string;
  singular: string;
  fields: CustomFieldDef[];
  initialName?: string;
  initialValues?: Record<string, unknown>;
  onClose: () => void;
  onSave: (name: string, values: Record<string, unknown>) => Promise<void>;
}) {
  const active = useMemo(() => fields.filter((f) => f.is_active), [fields]);
  const steps = useMemo(() => Array.from(new Set(active.map((f) => f.step))).sort((a, b) => a - b), [active]);
  const [index, setIndex] = useState(0);
  const [name, setName] = useState(initialName);
  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setIndex(0);
    setName(initialName);
    setValues(initialValues);
    setError(null);
    // Reset only when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const multi = steps.length > 1;
  const step = steps[index] ?? 1;
  const last = !multi || index === steps.length - 1;

  function missing(stepNumber?: number) {
    return active.filter(
      (f) =>
        f.required &&
        (stepNumber === undefined || f.step === stepNumber) &&
        !["formula", "auto_number"].includes(f.field_type) &&
        (values[f.key] === undefined || values[f.key] === null || values[f.key] === "" || (Array.isArray(values[f.key]) && !(values[f.key] as unknown[]).length)),
    );
  }

  function next() {
    if (index === 0 && !name.trim()) {
      setError(`Give the ${singular.toLowerCase()} a name.`);
      return;
    }
    const gaps = missing(step);
    if (gaps.length) {
      setError(`Fill in ${gaps.map((f) => f.label).join(", ")}.`);
      return;
    }
    setError(null);
    setIndex(index + 1);
  }

  async function save() {
    if (!name.trim()) {
      setError(`Give the ${singular.toLowerCase()} a name.`);
      setIndex(0);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const editable = Object.fromEntries(active.filter((f) => !["formula", "auto_number"].includes(f.field_type)).map((f) => [f.key, values[f.key] ?? null]));
      await onSave(name.trim(), editable);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={title}
      onClose={onClose}
      size="lg"
      footer={
        <>
          {multi && index > 0 ? (
            <button type="button" className="ui-btn" style={{ marginRight: "auto" }} onClick={() => setIndex(index - 1)}>
              Back
            </button>
          ) : null}
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          {last ? (
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
              Save
            </button>
          ) : (
            <button type="button" className="ui-btn ui-btn-primary" onClick={next}>
              Next
            </button>
          )}
        </>
      }
    >
      <div className="ui-stack">
        {multi ? (
          <ol className="wizard-steps" aria-label="Steps">
            {steps.map((value, i) => (
              <li key={value} className={i === index ? "current" : i < index ? "done" : undefined}>
                <span>{i < index ? <Check size={12} /> : i + 1}</span>
                Step {i + 1}
              </li>
            ))}
          </ol>
        ) : null}
        {error ? <Alert>{error}</Alert> : null}
        {index === 0 ? (
          <Field label={`${singular} name`} required>
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </Field>
        ) : null}
        <FieldsForm fields={active} values={values} onChange={setValues} step={multi ? step : undefined} />
      </div>
    </Modal>
  );
}
