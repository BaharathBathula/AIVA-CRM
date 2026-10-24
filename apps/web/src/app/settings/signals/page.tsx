"use client";

import Link from "next/link";
import { Bell, ListTodo, Pencil, Play, Plus, Radio, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, TagsInput, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Config = { days?: number; paths?: string[]; min_views?: number; level?: string; opens?: number; form_id?: string | null; persona_id?: string | null };

type Rule = {
  id: string;
  name: string;
  kind: string;
  kind_label: string;
  config: Config;
  notify_owner: boolean;
  create_task: boolean;
  is_active: boolean;
  last_evaluated_at: string | null;
  fire_count: number;
};

type Kind = { key: string; label: string; defaults: Config };

/** What each kind watches for, and how its "days" setting reads. */
const KIND_HELP: Record<string, { what: string; days: string }> = {
  page_visit: { what: "A known lead or contact reads the pages you list.", days: "Count views in the last" },
  intent_surge: { what: "An account's buying-intent score reaches the level you choose.", days: "Measure intent over the last" },
  email_engagement: { what: "Someone keeps opening your sequence emails.", days: "Count opens in the last" },
  form_submitted: { what: "A lead or contact fills in one of your web forms.", days: "Look back" },
  deal_stalled: { what: "An open deal has had no activity for a while.", days: "Stalled after" },
  job_change: { what: "A contact is marked “Changed job” — time to follow them to their new company.", days: "Look back" },
  sequence_reply: { what: "Someone replies to a sequence email.", days: "Look back" },
  persona_match: { what: "A new lead or contact matches a persona.", days: "Look back" },
};

export default function SignalsSettingsPage() {
  return (
    <SettingsShell feature="buying_intent">
      <SignalRules />
    </SettingsShell>
  );
}

function describe(rule: Rule, personas: Record<string, string>, forms: Record<string, string>) {
  const c = rule.config;
  const days = `${c.days ?? 1} day${c.days === 1 ? "" : "s"}`;
  switch (rule.kind) {
    case "page_visit":
      return `${c.min_views ?? 1}+ views of ${(c.paths ?? []).join(", ")} within ${days}`;
    case "intent_surge":
      return `${(c.level ?? "high")[0].toUpperCase()}${(c.level ?? "high").slice(1)} intent over ${days}`;
    case "email_engagement":
      return `${c.opens ?? 3}+ opens within ${days}`;
    case "form_submitted":
      return `${c.form_id ? forms[c.form_id] ?? "A specific form" : "Any form"} · last ${days}`;
    case "deal_stalled":
      return `No activity for ${days}`;
    case "persona_match":
      return `${c.persona_id ? personas[c.persona_id] ?? "A persona" : "Any persona"} · new in the last ${days}`;
    default:
      return `Last ${days}`;
  }
}

function SignalRules() {
  const toast = useToast();
  const { has } = useSession();
  const rules = useApi<{ items: Rule[]; kinds: Kind[]; can_edit: boolean }>("/signals/rules");
  const personas = useApi<{ items: { id: string; name: string }[] }>("/personas");
  const forms = useApi<{ id: string; name: string }[]>(has("web_forms") ? "/forms" : null);
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = Boolean(rules.data?.can_edit);
  const personaNames = Object.fromEntries((personas.data?.items ?? []).map((p) => [p.id, p.name]));
  const formNames = Object.fromEntries((forms.data ?? []).map((f) => [f.id, f.name]));

  async function run(rule: Rule) {
    setRunning(rule.id);
    try {
      const result = await api.post<{ fired: number }>(`/signals/rules/${rule.id}/run`);
      toast(result.fired ? `${result.fired} new signal${result.fired === 1 ? "" : "s"} — see the Signals feed.` : "Nothing new right now.");
      await rules.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setRunning(null);
    }
  }

  async function toggle(rule: Rule) {
    try {
      await api.put(`/signals/rules/${rule.id}`, { name: rule.name, kind: rule.kind, config: rule.config, notify_owner: rule.notify_owner, create_task: rule.create_task, is_active: !rule.is_active });
      await rules.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/signals/rules/${deleting.id}`);
      toast("Signal deleted.");
      setDeleting(null);
      await rules.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const items = rules.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Signals"
        description="Signals watch for moments worth acting on — a pricing-page visit, a reply, a stalled deal — and put them in the Signals feed. AIVA checks every few minutes."
        actions={
          <>
            <Link className="ui-btn" href="/signals">
              <Radio size={15} /> Open the feed
            </Link>
            {canEdit ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                <Plus size={16} /> New signal
              </button>
            ) : null}
          </>
        }
      />
      {rules.error ? <Alert>{rules.error}</Alert> : null}
      <Card flush>
        {rules.loading && !rules.data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Signal</th>
                  <th>When</th>
                  <th>Then</th>
                  <th className="ui-num">Fired</th>
                  <th>Checked</th>
                  <th>On</th>
                  {canEdit ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {items.map((rule) => (
                  <tr key={rule.id} style={{ opacity: rule.is_active ? 1 : 0.55 }}>
                    <td>
                      <div className="cell-title">{rule.name}</div>
                      <div className="ui-muted ui-small">{rule.kind_label}</div>
                    </td>
                    <td className="ui-small">{describe(rule, personaNames, formNames)}</td>
                    <td>
                      <span className="ui-row" style={{ gap: 6, flexWrap: "wrap" }}>
                        <Badge tone="info">Feed</Badge>
                        {rule.notify_owner ? (
                          <Badge>
                            <Bell size={11} /> Owner
                          </Badge>
                        ) : null}
                        {rule.create_task ? (
                          <Badge>
                            <ListTodo size={11} /> Task
                          </Badge>
                        ) : null}
                      </span>
                    </td>
                    <td className="ui-num">{rule.fire_count}</td>
                    <td className="ui-muted ui-small ui-nowrap">{rule.last_evaluated_at ? relativeTime(rule.last_evaluated_at) : "Not yet"}</td>
                    <td>
                      <Toggle label={`${rule.name} on`} checked={rule.is_active} disabled={!canEdit} onChange={() => void toggle(rule)} />
                    </td>
                    {canEdit ? (
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Run ${rule.name} now`} title="Check now" onClick={() => void run(rule)} disabled={running === rule.id}>
                          {running === rule.id ? <span className="ui-spinner" /> : <Play size={14} />}
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${rule.name}`} onClick={() => setEditing(rule)}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${rule.name}`} onClick={() => setDeleting(rule)}>
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
          <EmptyState icon={<Radio size={20} />} title="No signals yet" message="Start with “Visited the pricing page” — the strongest sign someone is ready to talk." />
        )}
      </Card>

      <RuleModal
        rule={editing}
        kinds={rules.data?.kinds ?? []}
        personas={personas.data?.items ?? []}
        forms={forms.data ?? []}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await rules.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete signal?"
        message={`“${deleting?.name ?? ""}” stops watching. Signals it already raised stay in the feed.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function RuleModal({
  rule,
  kinds,
  personas,
  forms,
  onClose,
  onSaved,
}: {
  rule: Rule | "new" | null;
  kinds: Kind[];
  personas: { id: string; name: string }[];
  forms: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [kind, setKind] = useState("page_visit");
  const [config, setConfig] = useState<Config>({});
  const [notify, setNotify] = useState(true);
  const [task, setTask] = useState(false);
  const [active, setActive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!rule) return;
    const existing = rule === "new" ? null : rule;
    const initialKind = existing?.kind ?? "page_visit";
    setName(existing?.name ?? "");
    setKind(initialKind);
    setConfig(existing?.config ?? kinds.find((k) => k.key === initialKind)?.defaults ?? {});
    setNotify(existing?.notify_owner ?? true);
    setTask(existing?.create_task ?? false);
    setActive(existing?.is_active ?? true);
    setError(null);
  }, [rule, kinds]);

  function chooseKind(next: string) {
    setKind(next);
    setConfig({ ...(kinds.find((k) => k.key === next)?.defaults ?? {}) });
    if (!name.trim() || kinds.some((k) => k.label === name)) setName(kinds.find((k) => k.key === next)?.label ?? "");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { name, kind, config, notify_owner: notify, create_task: task, is_active: active };
    try {
      if (rule === "new") await api.post("/signals/rules", payload);
      else if (rule) await api.put(`/signals/rules/${rule.id}`, payload);
      toast(rule === "new" ? "Signal created. AIVA starts checking within a few minutes." : "Signal saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  const help = KIND_HELP[kind];

  return (
    <Modal
      open={Boolean(rule)}
      title={rule === "new" ? "New signal" : "Edit signal"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="signal-form" className="ui-btn ui-btn-primary" disabled={saving || !name.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Save signal
          </button>
        </>
      }
    >
      <form id="signal-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Watch for" hint={help?.what}>
          <select className="ui-select" value={kind} onChange={(event) => chooseKind(event.target.value)}>
            {kinds.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name" required>
          <input className="ui-input" value={name} maxLength={160} onChange={(event) => setName(event.target.value)} />
        </Field>

        {kind === "page_visit" ? (
          <>
            <Field label="Pages" hint="Paths like /pricing or /compare/*.">
              <TagsInput
                label="Pages"
                value={config.paths ?? []}
                onChange={(paths) => setConfig({ ...config, paths })}
                placeholder="/pricing"
                transform={(v) => (v && !v.startsWith("/") ? `/${v}` : v)}
              />
            </Field>
            <Field label="At least this many views">
              <NumberInput label="Minimum views" value={config.min_views ?? 1} min={1} max={100} onChange={(v) => setConfig({ ...config, min_views: v ?? 1 })} width={100} />
            </Field>
          </>
        ) : null}
        {kind === "intent_surge" ? (
          <Field label="Intent level">
            <select className="ui-select" value={config.level ?? "high"} onChange={(event) => setConfig({ ...config, level: event.target.value })}>
              <option value="high">High</option>
              <option value="medium">Medium or higher</option>
              <option value="low">Any intent</option>
            </select>
          </Field>
        ) : null}
        {kind === "email_engagement" ? (
          <Field label="At least this many opens">
            <NumberInput label="Minimum opens" value={config.opens ?? 3} min={1} max={100} onChange={(v) => setConfig({ ...config, opens: v ?? 3 })} width={100} />
          </Field>
        ) : null}
        {kind === "form_submitted" ? (
          <Field label="Form">
            <select className="ui-select" value={config.form_id ?? ""} onChange={(event) => setConfig({ ...config, form_id: event.target.value || null })}>
              <option value="">Any form</option>
              {forms.map((form) => (
                <option key={form.id} value={form.id}>
                  {form.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {kind === "persona_match" ? (
          <Field label="Persona" hint={personas.length ? undefined : "Create personas in Settings → Personas first."}>
            <select className="ui-select" value={config.persona_id ?? ""} onChange={(event) => setConfig({ ...config, persona_id: event.target.value || null })}>
              <option value="">Any persona</option>
              {personas.map((persona) => (
                <option key={persona.id} value={persona.id}>
                  {persona.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label={help?.days ?? "Look back"}>
          <NumberInput label="Days" value={config.days ?? 1} min={1} max={180} onChange={(v) => setConfig({ ...config, days: v ?? 1 })} suffix="days" width={100} />
        </Field>

        <div className="dm-toggles">
          <div className="ui-row">
            <Toggle label="Notify the record owner" checked={notify} onChange={setNotify} />
            <span className="ui-small">Notify the record owner</span>
          </div>
          <div className="ui-row">
            <Toggle label="Create a follow-up task" checked={task} onChange={setTask} />
            <span className="ui-small">Create a follow-up task for them</span>
          </div>
          <div className="ui-row">
            <Toggle label="Signal is on" checked={active} onChange={setActive} />
            <span className="ui-small">{active ? "On" : "Off"}</span>
          </div>
        </div>
      </form>
    </Modal>
  );
}
