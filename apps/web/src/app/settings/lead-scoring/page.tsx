"use client";

import { Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { DonutChart } from "@/components/charts";
import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { OPERATOR_LABELS } from "@/lib/constants";
import { number } from "@/lib/format";
import type { Catalog, FieldDef, ScoringRule, ScoringSummary } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const NO_VALUE = new Set(["is_empty", "is_not_empty"]);

type ObjectType = "lead" | "contact" | "account";

const OBJECTS: Record<ObjectType, { one: string; many: string; example: string }> = {
  lead: { one: "lead", many: "leads", example: "title, source, company size" },
  contact: { one: "contact", many: "contacts", example: "seniority, department, stage" },
  account: { one: "account", many: "accounts", example: "industry, employees, revenue, region" },
};

export default function LeadScoringPage() {
  return (
    <SettingsShell>
      <LeadScoring />
    </SettingsShell>
  );
}

function LeadScoring() {
  const { can } = useSession();
  const toast = useToast();
  const editable = can("sales.lead_scoring");
  const [objectType, setObjectType] = useState<ObjectType>("lead");
  const noun = OBJECTS[objectType];
  const rules = useApi<ScoringRule[]>(`/lead-scoring/rules?object_type=${objectType}`);
  const summary = useApi<ScoringSummary>(`/lead-scoring/summary?object_type=${objectType}`);
  const catalog = useApi<Catalog>("/meta/catalog");
  const leadFields = catalog.data?.objects.find((object) => object.key === objectType)?.fields ?? [];
  const [editing, setEditing] = useState<ScoringRule | "new" | null>(null);
  const [deleting, setDeleting] = useState<ScoringRule | null>(null);
  const [busy, setBusy] = useState(false);

  const fieldLabel = (key: string) => leadFields.find((field) => field.key === key)?.label ?? key;

  async function recalculate() {
    setBusy(true);
    try {
      const result = await api.post<{ updated: number; leads: number }>(`/lead-scoring/recalculate?object_type=${objectType}`);
      toast(`Rescored ${number(result.leads)} ${noun.many} — ${number(result.updated)} scores changed.`);
      await summary.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(rule: ScoringRule) {
    try {
      await api.patch(`/lead-scoring/rules/${rule.id}`, { ...rule, object_type: objectType, is_active: !rule.is_active });
      await rules.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/lead-scoring/rules/${deleting.id}`);
      toast("Rule deleted. Recalculate to update existing scores.");
      setDeleting(null);
      await rules.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const distribution = summary.data?.distribution;

  return (
    <>
      <PageHeader
        title="Scoring"
        description={`Points add up to a 0–100 score on every ${noun.one}. New and edited ${noun.many} are scored automatically.`}
        actions={
          editable ? (
            <>
              <button type="button" className="ui-btn" onClick={() => void recalculate()} disabled={busy}>
                <RefreshCw size={15} /> Recalculate all
              </button>
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                <Plus size={16} /> Add rule
              </button>
            </>
          ) : null
        }
      />

      <div className="ui-toolbar" style={{ marginBottom: 14 }}>
        <Tabs
          label="Scored records"
          value={objectType}
          onChange={setObjectType}
          items={[
            { value: "lead", label: "Leads" },
            { value: "contact", label: "Contacts" },
            { value: "account", label: "Accounts" },
          ]}
        />
      </div>

      <div className="ui-stack">
        <div className="ui-grid ui-grid-3">
          <Kpi label={`${noun.many[0].toUpperCase()}${noun.many.slice(1)} scored`} value={number(summary.data?.leads ?? 0)} />
          <Kpi label="Average score" value={number(summary.data?.average ?? 0, 1)} />
          <Kpi label={`Grade A ${noun.many}`} value={number(distribution?.A ?? 0)} note="Score 80 and above" tone="success" />
        </div>

        <div className="ui-split">
          <Card title="Rules" flush>
            {rules.loading && !rules.data ? (
              <LoadingRows />
            ) : rules.data?.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Rule</th>
                      <th>Condition</th>
                      <th className="ui-num">Points</th>
                      <th>Active</th>
                      {editable ? <th aria-label="Actions" /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {rules.data.map((rule) => (
                      <tr key={rule.id} style={{ opacity: rule.is_active ? 1 : 0.55 }}>
                        <td className="cell-title">{rule.name}</td>
                        <td className="ui-muted">
                          {fieldLabel(rule.field)} {OPERATOR_LABELS[rule.operator] ?? rule.operator}{" "}
                          {NO_VALUE.has(rule.operator) ? "" : <strong style={{ color: "var(--text)" }}>{rule.value}</strong>}
                        </td>
                        <td className="ui-num">
                          <Badge tone={rule.points >= 0 ? "success" : "danger"}>
                            {rule.points > 0 ? "+" : ""}
                            {rule.points}
                          </Badge>
                        </td>
                        <td>
                          <label className="ui-check">
                            <input type="checkbox" checked={rule.is_active} disabled={!editable} onChange={() => void toggle(rule)} />
                            <span className="ui-small">{rule.is_active ? "On" : "Off"}</span>
                          </label>
                        </td>
                        {editable ? (
                          <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Edit rule" onClick={() => setEditing(rule)}>
                              <Pencil size={14} />
                            </button>
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Delete rule" onClick={() => setDeleting(rule)}>
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
              <EmptyState title="No scoring rules" message={`Add rules for what predicts a good customer: ${noun.example}.`} />
            )}
          </Card>

          <Card title="Score distribution">
            {distribution ? (
              <DonutChart
                data={[
                  { label: "A · 80–100", value: distribution.A },
                  { label: "B · 60–79", value: distribution.B },
                  { label: "C · 40–59", value: distribution.C },
                  { label: "D · 0–39", value: distribution.D },
                ]}
                size={150}
                sort={false}
              />
            ) : (
              <LoadingRows rows={4} />
            )}
          </Card>
        </div>
      </div>

      <RuleModal
        rule={editing}
        objectType={objectType}
        fields={leadFields}
        operators={catalog.data?.operators ?? []}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await rules.reload();
          await summary.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete scoring rule?"
        message={`“${deleting?.name}” will stop adding points. Existing scores change after you recalculate.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function RuleModal({
  rule,
  objectType,
  fields,
  operators,
  onClose,
  onSaved,
}: {
  rule: ScoringRule | "new" | null;
  objectType: ObjectType;
  fields: FieldDef[];
  operators: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [field, setField] = useState("job_title");
  const [operator, setOperator] = useState("contains");
  const [value, setValue] = useState("");
  const [points, setPoints] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!rule) return;
    setName(rule === "new" ? "" : rule.name);
    setField(rule === "new" ? (objectType === "account" ? "industry" : "job_title") : rule.field);
    setOperator(rule === "new" ? "contains" : rule.operator);
    setValue(rule === "new" ? "" : rule.value ?? "");
    setPoints(rule === "new" ? 10 : rule.points);
    setError(null);
  }, [rule, objectType]);

  const definition = fields.find((item) => item.key === field);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const payload = {
      name: name.trim(),
      field,
      operator,
      value: NO_VALUE.has(operator) ? null : value,
      points,
      is_active: rule === "new" || !rule ? true : rule.is_active,
      object_type: objectType,
    };
    try {
      if (rule === "new") await api.post("/lead-scoring/rules", payload);
      else if (rule) await api.patch(`/lead-scoring/rules/${rule.id}`, payload);
      toast(`Rule saved. New ${OBJECTS[objectType].many} use it right away; recalculate to rescore existing ones.`);
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(rule)}
      title={rule === "new" ? "New scoring rule" : "Edit scoring rule"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="rule-form" className="ui-btn ui-btn-primary" disabled={busy}>
            Save rule
          </button>
        </>
      }
    >
      <form id="rule-form" className="ui-stack" onSubmit={submit}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Rule name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} required placeholder="Referral source" />
        </Field>
        <div className="ui-form-grid">
          <Field label={`When ${OBJECTS[objectType].one} field`}>
            <select className="ui-select" value={field} onChange={(e) => setField(e.target.value)}>
              {fields
                .filter((item) => !["created_at", "updated_at", "converted_at", "score"].includes(item.key))
                .map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Condition">
            <select className="ui-select" value={operator} onChange={(e) => setOperator(e.target.value)}>
              {operators.map((item) => (
                <option key={item} value={item}>
                  {OPERATOR_LABELS[item] ?? item}
                </option>
              ))}
            </select>
          </Field>
          {NO_VALUE.has(operator) ? null : (
            <Field label="Value" hint={operator === "in" ? "Separate options with commas." : undefined}>
              {definition?.type === "enum" && operator !== "in" ? (
                <select className="ui-select" value={value} onChange={(e) => setValue(e.target.value)} required>
                  <option value="">Choose…</option>
                  {definition.options.map((option) => (
                    <option key={option} value={option}>
                      {option.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              ) : (
                <input className="ui-input" value={value} onChange={(e) => setValue(e.target.value)} required />
              )}
            </Field>
          )}
          <Field label="Points" hint="Negative points lower the score.">
            <input
              className="ui-input"
              type="number"
              min={-100}
              max={100}
              value={points}
              onChange={(e) => setPoints(Number(e.target.value))}
              required
            />
          </Field>
        </div>
      </form>
    </Modal>
  );
}
