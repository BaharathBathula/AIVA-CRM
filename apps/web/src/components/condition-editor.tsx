"use client";

import { Plus, X } from "lucide-react";

import { ValueInput, operatorLabel, operatorsFor } from "@/components/field-inputs";
import type { FieldDef } from "@/lib/platform-types";

export interface Condition {
  field: string;
  operator: string;
  value: string | null;
}

/** "All of these must match" editor for record conditions. */
export function ConditionEditor({
  fields,
  operators,
  conditions,
  onChange,
  emptyLabel = "No conditions — applies to every record.",
  includePeriod = false,
  disabled,
}: {
  fields: FieldDef[];
  operators: string[];
  conditions: Condition[];
  onChange: (conditions: Condition[]) => void;
  emptyLabel?: string;
  includePeriod?: boolean;
  disabled?: boolean;
}) {
  const usable = fields.filter((f) => !f.virtual || f.key === "stage_category");
  const byKey = new Map(usable.map((f) => [f.key, f]));
  const set = (index: number, next: Condition) => onChange(conditions.map((c, i) => (i === index ? next : c)));

  return (
    <div className="ui-stack" style={{ gap: 10 }}>
      {conditions.length ? null : <span className="ui-muted ui-small">{emptyLabel}</span>}
      {conditions.map((condition, index) => {
        const field = byKey.get(condition.field);
        const ops = operatorsFor(field, operators, includePeriod);
        return (
          <div key={index} className="ui-row" style={{ flexWrap: "wrap", gap: 8 }}>
            <select
              className="ui-select sm"
              style={{ flex: "1 1 160px" }}
              value={condition.field}
              disabled={disabled}
              aria-label="Field"
              onChange={(e) => {
                const next = byKey.get(e.target.value);
                set(index, { field: e.target.value, operator: operatorsFor(next, operators, includePeriod)[0], value: "" });
              }}
            >
              {usable.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
            <select
              className="ui-select sm"
              style={{ flex: "1 1 140px" }}
              value={condition.operator}
              disabled={disabled}
              aria-label="Condition"
              onChange={(e) => set(index, { ...condition, operator: e.target.value, value: "" })}
            >
              {ops.map((op) => (
                <option key={op} value={op}>
                  {operatorLabel(op, field)}
                </option>
              ))}
            </select>
            <div style={{ flex: "1 1 160px", minWidth: 0 }}>
              <ValueInput field={field} operator={condition.operator} value={condition.value ?? ""} allowMe={false} onChange={(value) => set(index, { ...condition, value })} />
            </div>
            {disabled ? null : (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove condition" onClick={() => onChange(conditions.filter((_, i) => i !== index))}>
                <X size={14} />
              </button>
            )}
          </div>
        );
      })}
      {disabled || !usable.length ? null : (
        <button
          type="button"
          className="ui-btn ui-btn-sm"
          style={{ width: "fit-content" }}
          onClick={() => onChange([...conditions, { field: usable[0].key, operator: operatorsFor(usable[0], operators, includePeriod)[0], value: "" }])}
        >
          <Plus size={14} /> Add condition
        </button>
      )}
    </div>
  );
}

export function cleanConditions(conditions: Condition[]) {
  return conditions.map((c) => ({ ...c, value: ["is_empty", "is_not_empty"].includes(c.operator) ? null : c.value }));
}
