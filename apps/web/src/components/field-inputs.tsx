"use client";

import { useEffect, useState } from "react";

import { OPERATOR_LABELS, PERIOD_LABELS } from "@/lib/constants";
import { titleCase } from "@/lib/format";
import type { FieldDef } from "@/lib/platform-types";
import { api } from "@/lib/use-api";

type Option = { value: string; label: string };

const cache = new Map<string, Promise<Option[]>>();

async function loadOptions(ref: string): Promise<Option[]> {
  switch (ref) {
    case "user": {
      const rows = await api.get<{ id: string; full_name: string }[]>("/meta/members");
      return rows.map((row) => ({ value: row.id, label: row.full_name }));
    }
    case "account": {
      const rows = await api.get<{ id: string; name: string }[]>("/accounts?limit=100");
      return rows.map((row) => ({ value: row.id, label: row.name }));
    }
    case "contact": {
      const rows = await api.get<{ id: string; first_name: string; last_name: string }[]>("/contacts?limit=100");
      return rows.map((row) => ({ value: row.id, label: `${row.first_name} ${row.last_name}`.trim() }));
    }
    case "opportunity": {
      const rows = await api.get<{ id: string; name: string }[]>("/opportunities?limit=100");
      return rows.map((row) => ({ value: row.id, label: row.name }));
    }
    case "lead": {
      const rows = await api.get<{ id: string; first_name: string; last_name: string; company_name: string | null }[]>("/leads?limit=100");
      return rows.map((row) => ({ value: row.id, label: `${row.first_name} ${row.last_name}`.trim() + (row.company_name ? ` (${row.company_name})` : "") }));
    }
    case "team": {
      const rows = await api.get<{ id: string; name: string }[]>("/admin/teams");
      return rows.map((row) => ({ value: row.id, label: row.name }));
    }
    case "pipeline": {
      const rows = await api.get<{ id: string; name: string }[]>("/pipelines");
      return rows.map((row) => ({ value: row.id, label: row.name }));
    }
    case "stage": {
      const pipelines = await api.get<{ id: string; name: string }[]>("/pipelines");
      const groups = await Promise.all(
        pipelines.map(async (pipeline) => {
          const stages = await api.get<{ id: string; name: string }[]>(`/pipelines/${pipeline.id}/stages`);
          return stages.map((stage) => ({
            value: stage.id,
            label: pipelines.length > 1 ? `${pipeline.name} · ${stage.name}` : stage.name,
          }));
        })
      );
      return groups.flat();
    }
    default:
      if (ref.startsWith("module:")) {
        const result = await api.get<{ items: { id: string; name: string }[] }>(`/modules/${ref.slice(7)}/records?page_size=200`);
        return result.items.map((row) => ({ value: row.id, label: row.name }));
      }
      return [];
  }
}

export function useRefOptions(ref: string | null) {
  const [options, setOptions] = useState<Option[]>([]);
  useEffect(() => {
    if (!ref) return;
    let active = true;
    if (!cache.has(ref)) cache.set(ref, loadOptions(ref).catch(() => []));
    void cache.get(ref)!.then((result) => {
      if (active) setOptions(result);
    });
    return () => {
      active = false;
    };
  }, [ref]);
  return options;
}

function refKind(field: FieldDef) {
  return field.type === "user" ? "user" : field.type === "ref" ? field.ref : null;
}

/** Operators that make sense for a field type. */
export function operatorsFor(field: FieldDef | undefined, all: string[], includePeriod = true) {
  if (!field) return all;
  const has = (op: string) => all.includes(op) || op === "in_period";
  switch (field.type) {
    case "date":
    case "datetime":
      return [...(includePeriod ? ["in_period"] : []), "equals", "not_equals", "greater_than", "less_than", "is_empty", "is_not_empty"].filter(has);
    case "number":
    case "currency":
      return ["equals", "not_equals", "greater_than", "less_than", "is_empty", "is_not_empty"].filter(has);
    case "bool":
      return ["equals", "not_equals"].filter(has);
    case "enum":
    case "user":
    case "ref":
      return ["equals", "not_equals", "in", "is_empty", "is_not_empty"].filter(has);
    default:
      return ["equals", "not_equals", "contains", "not_contains", "starts_with", "is_empty", "is_not_empty"].filter(has);
  }
}

export function operatorLabel(op: string, field?: FieldDef) {
  if (field && (field.type === "date" || field.type === "datetime")) {
    if (op === "greater_than") return "is after";
    if (op === "less_than") return "is before";
    if (op === "equals") return "is on";
  }
  return OPERATOR_LABELS[op] ?? op;
}

/** Value editor for a condition, matched to the field type. */
export function ValueInput({
  field,
  operator,
  value,
  onChange,
  allowMe = true,
}: {
  field: FieldDef | undefined;
  operator: string;
  value: string;
  onChange: (value: string) => void;
  allowMe?: boolean;
}) {
  const options = useRefOptions(field ? refKind(field) : null);

  if (operator === "is_empty" || operator === "is_not_empty") return null;
  if (!field) return <input className="ui-input sm" value={value} onChange={(e) => onChange(e.target.value)} />;

  if (operator === "in_period") {
    return (
      <select className="ui-select sm" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Period">
        <option value="">Choose a period…</option>
        {Object.entries(PERIOD_LABELS).map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "date" || field.type === "datetime") {
    return <input className="ui-input sm" type="date" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Date" />;
  }
  if (field.type === "bool") {
    return (
      <select className="ui-select sm" value={value || "true"} onChange={(e) => onChange(e.target.value)} aria-label="Value">
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    );
  }
  if (operator === "in") {
    return <input className="ui-input sm" value={value} onChange={(e) => onChange(e.target.value)} placeholder="Comma-separated values" aria-label="Values" />;
  }
  if (field.type === "enum") {
    return (
      <select className="ui-select sm" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Value">
        <option value="">Choose…</option>
        {field.options.map((option) => (
          <option key={option} value={option}>
            {titleCase(option)}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "user" || field.type === "ref") {
    return (
      <select className="ui-select sm" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Value">
        <option value="">Choose…</option>
        {field.type === "user" && allowMe ? <option value="me">Me (whoever is viewing)</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      className="ui-input sm"
      type={field.type === "number" || field.type === "currency" ? "number" : "text"}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Value"
    />
  );
}
