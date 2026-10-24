"use client";

import Link from "next/link";
import { Map as MapIcon, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { ColorDot, ColorPicker, MemberPicker, NumberInput, TagsInput, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Rule = { field: string; operator: string; values: string[] };
type FieldDef = { key: string; label: string; type: string };

type Territory = {
  id: string;
  name: string;
  description: string | null;
  color: string;
  match: "all" | "any";
  rules: Rule[];
  member_user_ids: string[];
  members: { id: string; name: string }[];
  assignment: "round_robin" | "territory_only";
  applies_to: ("account" | "lead")[];
  auto_assign: boolean;
  priority: number;
  is_active: boolean;
  accounts: number;
  leads: number;
};

type TerritoryList = {
  items: Territory[];
  unassigned: { accounts: number; leads: number };
  fields: Record<"account" | "lead", FieldDef[]>;
  operators: Record<string, string>;
  can_manage: boolean;
};

type Draft = Omit<Territory, "id" | "members" | "accounts" | "leads">;

const EMPTY: Draft = {
  name: "",
  description: "",
  color: "blue",
  match: "all",
  rules: [{ field: "country", operator: "in", values: [] }],
  member_user_ids: [],
  assignment: "round_robin",
  applies_to: ["account", "lead"],
  auto_assign: true,
  priority: 100,
  is_active: true,
};

function describeRule(rule: Rule, fields: FieldDef[], operators: Record<string, string>) {
  const label = fields.find((f) => f.key === rule.field)?.label ?? rule.field.replace(/^cf\./, "");
  const op = operators[rule.operator] ?? rule.operator;
  if (rule.operator === "is_empty") return `${label} ${op}`;
  if (rule.operator === "between") return `${label} ${op} ${rule.values[0] ?? "?"} and ${rule.values[1] ?? "?"}`;
  return `${label} ${op} ${rule.values.join(", ")}`;
}

export default function TerritoriesPage() {
  return (
    <SettingsShell feature="territories">
      <Territories />
    </SettingsShell>
  );
}

function Territories() {
  const toast = useToast();
  const list = useApi<TerritoryList>("/territories");
  const [editing, setEditing] = useState<Territory | "new" | null>(null);
  const [deleting, setDeleting] = useState<Territory | null>(null);
  const [applying, setApplying] = useState(false);
  const [busy, setBusy] = useState(false);
  const data = list.data;
  const allFields = data ? [...data.fields.account, ...data.fields.lead] : [];

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/territories/${deleting.id}`);
      toast("Territory deleted. Its records keep their owners.");
      setDeleting(null);
      await list.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(territory: Territory) {
    try {
      const { id: _id, members: _m, accounts: _a, leads: _l, ...rest } = territory;
      await api.put(`/territories/${territory.id}`, { ...rest, is_active: !territory.is_active });
      await list.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Territories"
        description="Split your market by region, industry or company size. New accounts and leads that match a territory are tagged with it and, with round-robin, handed to the next rep in turn. The first matching territory (lowest priority number) wins."
        actions={
          data?.can_manage ? (
            <>
              <button type="button" className="ui-btn" onClick={() => setApplying(true)} disabled={!data.items.length}>
                <RefreshCw size={15} /> Apply to existing records
              </button>
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                <Plus size={16} /> New territory
              </button>
            </>
          ) : null
        }
      />
      {list.error ? <Alert>{list.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <div className="ui-stack">
          <div className="settings-kpis three">
            <Card>
              <div className="ui-kpi-label">Territories</div>
              <div className="ui-kpi-value">{data.items.filter((t) => t.is_active).length}</div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                {data.items.length - data.items.filter((t) => t.is_active).length} paused
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">Accounts without a territory</div>
              <div className="ui-kpi-value">{data.unassigned.accounts.toLocaleString()}</div>
            </Card>
            <Card>
              <div className="ui-kpi-label">Leads without a territory</div>
              <div className="ui-kpi-value">{data.unassigned.leads.toLocaleString()}</div>
            </Card>
          </div>
          {data.items.length ? (
            <div className="territory-list">
              {data.items.map((territory) => (
                <section key={territory.id} className={`ui-card territory-card${territory.is_active ? "" : " paused"}`}>
                  <div className="territory-head">
                    <ColorDot color={territory.color} />
                    <h2>{territory.name}</h2>
                    <Badge>Priority {territory.priority}</Badge>
                    {!territory.is_active ? <Badge tone="warning">Paused</Badge> : null}
                    <span className="ui-spacer" />
                    {data.can_manage ? (
                      <>
                        <Toggle label={`${territory.name} active`} checked={territory.is_active} onChange={() => void toggleActive(territory)} />
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${territory.name}`} onClick={() => setEditing(territory)}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${territory.name}`} onClick={() => setDeleting(territory)}>
                          <Trash2 size={14} />
                        </button>
                      </>
                    ) : null}
                  </div>
                  {territory.description ? <p className="ui-muted ui-small" style={{ margin: 0 }}>{territory.description}</p> : null}
                  <div className="territory-rules">
                    <span className="ui-muted ui-small">{territory.match === "all" ? "Matches when all of these are true" : "Matches when any of these is true"}</span>
                    <ul>
                      {territory.rules.map((rule, index) => (
                        <li key={index}>{describeRule(rule, allFields, data.operators)}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="territory-foot">
                    <span>
                      <strong>{territory.accounts.toLocaleString()}</strong> {territory.accounts === 1 ? "account" : "accounts"} · <strong>{territory.leads.toLocaleString()}</strong>{" "}
                      {territory.leads === 1 ? "lead" : "leads"}
                    </span>
                    <span className="ui-muted">
                      {territory.applies_to.length === 2 ? "Accounts and leads" : territory.applies_to[0] === "account" ? "Accounts only" : "Leads only"} ·{" "}
                      {territory.assignment === "round_robin" ? "Round-robin owners" : "Tag only"}
                    </span>
                    <span className="ui-muted">{territory.members.length ? territory.members.map((m) => m.name).join(", ") : "No reps yet"}</span>
                  </div>
                </section>
              ))}
            </div>
          ) : (
            <Card>
              <EmptyState
                icon={<MapIcon size={20} />}
                title="No territories yet"
                message="Start with one, like “Texas & Oklahoma” or “Enterprise (1,000+ employees)”, and pick the reps who work it."
                action={
                  data.can_manage ? (
                    <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                      <Plus size={16} /> New territory
                    </button>
                  ) : null
                }
              />
            </Card>
          )}
        </div>
      )}

      {data ? (
        <TerritoryModal
          territory={editing}
          fields={data.fields}
          operators={data.operators}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await list.reload();
          }}
        />
      ) : null}
      <ApplyModal
        open={applying}
        onClose={() => setApplying(false)}
        onDone={async () => {
          setApplying(false);
          await list.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.name ?? "territory"}?`}
        message="Records tagged with it lose the tag but keep their owners. New records won't be routed by it any more."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function TerritoryModal({
  territory,
  fields,
  operators,
  onClose,
  onSaved,
}: {
  territory: Territory | "new" | null;
  fields: TerritoryList["fields"];
  operators: Record<string, string>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<Record<string, { count: number; sample: { id: string; label: string; href: string }[] }> | null>(null);

  useEffect(() => {
    if (!territory) return;
    if (territory === "new") setDraft(EMPTY);
    else {
      const { id: _id, members: _m, accounts: _a, leads: _l, ...rest } = territory;
      setDraft({ ...rest, description: rest.description ?? "" });
    }
    setError(null);
    setPreview(null);
  }, [territory]);

  const available = [...new Map([...(draft.applies_to.includes("account") ? fields.account : []), ...(draft.applies_to.includes("lead") ? fields.lead : [])].map((f) => [f.key, f])).values()];

  function setRule(index: number, patch: Partial<Rule>) {
    setDraft((current) => ({ ...current, rules: current.rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)) }));
    setPreview(null);
  }

  async function runPreview() {
    try {
      setPreview(await api.post("/territories/preview", draft));
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = { ...draft, description: draft.description || null };
      if (territory === "new") await api.post("/territories", body);
      else if (territory) await api.put(`/territories/${territory.id}`, body);
      toast(territory === "new" ? "Territory created. New records are routed from now on." : "Territory saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={Boolean(territory)}
      title={territory === "new" ? "New territory" : `Edit ${territory ? territory.name : ""}`}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={() => void runPreview()}>
            Preview matches
          </button>
          <span className="ui-spacer" />
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="territory-form" className="ui-btn ui-btn-primary" disabled={saving || !draft.name.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Save territory
          </button>
        </>
      }
    >
      <form id="territory-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Name" required>
            <input className="ui-input" value={draft.name} maxLength={120} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Texas & Oklahoma" />
          </Field>
          <Field label="Priority" hint="Lower numbers are checked first.">
            <NumberInput label="Priority" value={draft.priority} min={1} max={10000} onChange={(value) => setDraft({ ...draft, priority: value ?? 100 })} />
          </Field>
          <Field label="Description" className="span-2">
            <input className="ui-input" value={draft.description ?? ""} maxLength={2000} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
          </Field>
          <Field label="Covers" htmlFor="territory-covers">
            <span className="ui-row" id="territory-covers" style={{ height: 44, gap: 16 }}>
              {(["account", "lead"] as const).map((kind) => (
                <label key={kind} className="ui-check">
                  <input
                    type="checkbox"
                    checked={draft.applies_to.includes(kind)}
                    onChange={(event) =>
                      setDraft({ ...draft, applies_to: event.target.checked ? [...draft.applies_to, kind] : draft.applies_to.filter((a) => a !== kind) })
                    }
                  />
                  {kind === "account" ? "Accounts" : "Leads"}
                </label>
              ))}
            </span>
          </Field>
          <Field label="Color" htmlFor="territory-color">
            <span id="territory-color" style={{ display: "flex", alignItems: "center", minHeight: 44 }}>
              <ColorPicker value={draft.color} onChange={(color) => setDraft({ ...draft, color })} />
            </span>
          </Field>
        </div>

        <div className="territory-rule-box">
          <div className="ui-row" style={{ flexWrap: "wrap" }}>
            <strong className="ui-small">Match</strong>
            <select className="ui-select sm" aria-label="Match" value={draft.match} onChange={(event) => setDraft({ ...draft, match: event.target.value as "all" | "any" })} style={{ width: 210 }}>
              <option value="all">all of these rules</option>
              <option value="any">any of these rules</option>
            </select>
          </div>
          {draft.rules.map((rule, index) => (
            <div key={index} className="territory-rule">
              <select className="ui-select sm" aria-label="Field" value={rule.field} onChange={(event) => setRule(index, { field: event.target.value })}>
                {available.map((f) => (
                  <option key={f.key} value={f.key}>
                    {f.label}
                  </option>
                ))}
                {!available.some((f) => f.key === rule.field) ? <option value={rule.field}>{rule.field}</option> : null}
              </select>
              <select className="ui-select sm" aria-label="Condition" value={rule.operator} onChange={(event) => setRule(index, { operator: event.target.value, values: [] })}>
                {Object.entries(operators).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
              {rule.operator === "is_empty" ? (
                <span />
              ) : rule.operator === "between" ? (
                <span className="ui-row">
                  <input className="ui-input sm" aria-label="From" inputMode="decimal" value={rule.values[0] ?? ""} onChange={(event) => setRule(index, { values: [event.target.value, rule.values[1] ?? ""] })} />
                  <span className="ui-muted">and</span>
                  <input className="ui-input sm" aria-label="To" inputMode="decimal" value={rule.values[1] ?? ""} onChange={(event) => setRule(index, { values: [rule.values[0] ?? "", event.target.value] })} />
                </span>
              ) : ["gte", "lte"].includes(rule.operator) ? (
                <input className="ui-input sm" aria-label="Value" inputMode="decimal" value={rule.values[0] ?? ""} onChange={(event) => setRule(index, { values: [event.target.value] })} />
              ) : (
                <TagsInput label="Values" value={rule.values} placeholder="Type a value and press Enter" onChange={(values) => setRule(index, { values })} />
              )}
              <button
                type="button"
                className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                aria-label="Remove rule"
                disabled={draft.rules.length === 1}
                onClick={() => setDraft({ ...draft, rules: draft.rules.filter((_, i) => i !== index) })}
              >
                <X size={14} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="ui-btn ui-btn-sm"
            style={{ justifySelf: "start" }}
            onClick={() => setDraft({ ...draft, rules: [...draft.rules, { field: available[0]?.key ?? "country", operator: "in", values: [] }] })}
          >
            <Plus size={14} /> Add rule
          </button>
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Text matching ignores capitals, so “tx” matches “TX”. Separate several values with Enter.
          </p>
        </div>

        {preview ? (
          <Alert tone="info">
            {Object.entries(preview).map(([kind, value]) => (
              <div key={kind}>
                <strong>
                  {value.count.toLocaleString()} {kind === "account" ? "accounts" : "leads"}
                </strong>{" "}
                match today{value.sample.length ? ": " : "."}
                {value.sample.map((s, i) => (
                  <span key={s.id}>
                    {i ? ", " : ""}
                    <Link href={s.href} target="_blank">
                      {s.label}
                    </Link>
                  </span>
                ))}
              </div>
            ))}
          </Alert>
        ) : null}

        <div className="ui-form-grid">
          <Field label="When a record matches" className="span-2">
            <select className="ui-select" value={draft.assignment} onChange={(event) => setDraft({ ...draft, assignment: event.target.value as Draft["assignment"] })}>
              <option value="round_robin">Tag it and give unowned records to the next rep in turn</option>
              <option value="territory_only">Only tag it (owners don&apos;t change)</option>
            </select>
          </Field>
          <Field label="Route new records automatically" htmlFor="territory-auto">
            <span className="ui-row" style={{ height: 44 }} id="territory-auto">
              <Toggle label="Route new records automatically" checked={draft.auto_assign} onChange={(value) => setDraft({ ...draft, auto_assign: value })} />
              <span className="ui-small">{draft.auto_assign ? "On" : "Off — only when you apply it"}</span>
            </span>
          </Field>
          <Field label="Active" htmlFor="territory-active">
            <span className="ui-row" style={{ height: 44 }} id="territory-active">
              <Toggle label="Active" checked={draft.is_active} onChange={(value) => setDraft({ ...draft, is_active: value })} />
            </span>
          </Field>
          <Field label="Reps in this territory" className="span-2" htmlFor="territory-members">
            <div id="territory-members">
              <MemberPicker value={draft.member_user_ids} onChange={(ids) => setDraft({ ...draft, member_user_ids: ids })} label="Reps in this territory" />
            </div>
          </Field>
        </div>
      </form>
    </Modal>
  );
}

function ApplyModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [onlyUntagged, setOnlyUntagged] = useState(true);
  const [reassign, setReassign] = useState(false);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      const result = await api.post<{ tagged: number; reassigned: number }>("/territories/apply", { only_untagged: onlyUntagged, reassign_owners: reassign });
      toast(`Tagged ${result.tagged.toLocaleString()} records${reassign ? ` and moved ${result.reassigned.toLocaleString()} to territory reps` : ""}.`);
      onDone();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Apply territories to existing records"
      description="New records are routed automatically. Use this once after creating or changing territories."
      onClose={onClose}
      size="sm"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void run()}>
            {busy ? <span className="ui-spinner" /> : null} Apply now
          </button>
        </>
      }
    >
      <div className="ui-stack" style={{ gap: 12 }}>
        <label className="ui-check">
          <input type="checkbox" checked={onlyUntagged} onChange={(event) => setOnlyUntagged(event.target.checked)} /> Only records that don&apos;t have a territory yet
        </label>
        <label className="ui-check">
          <input type="checkbox" checked={reassign} onChange={(event) => setReassign(event.target.checked)} /> Also move owners to the territory&apos;s reps (round-robin territories)
        </label>
        {reassign ? <Alert tone="warning">Records owned by someone outside the territory will get a new owner. This can&apos;t be undone in one step.</Alert> : null}
      </div>
    </Modal>
  );
}
