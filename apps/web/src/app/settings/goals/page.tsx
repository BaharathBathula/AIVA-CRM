"use client";

import { Goal as GoalIcon, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { MemberPicker, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader, Tabs, type Tone } from "@/components/ui";
import { compactMoney, number } from "@/lib/format";
import type { Team } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Metric = { key: string; label: string; unit: "money" | "count" };
type Pace = "ahead" | "on_track" | "behind" | "done";
type Period = "week" | "month" | "quarter" | "year";
type AssigneeType = "user" | "team" | "workspace";

type Goal = {
  id: string;
  name: string;
  metric: string;
  metric_label: string;
  unit: "money" | "count";
  period: Period;
  period_label: string;
  target: number;
  target_total: number;
  actual: number;
  percent: number;
  elapsed_percent: number;
  pace: Pace;
  assignee_type: AssigneeType;
  assignee_ids: string[];
  assignees: string[];
  people: { id: string; name: string; actual: number; target: number; percent: number }[];
  is_active: boolean;
};

type GoalList = { items: Goal[]; metrics: Metric[]; can_edit: boolean };

const PACE: Record<Pace, { label: string; tone: Tone }> = {
  done: { label: "Reached", tone: "success" },
  ahead: { label: "Ahead", tone: "success" },
  on_track: { label: "On track", tone: "info" },
  behind: { label: "Behind", tone: "warning" },
};

const PERIODS: { value: Period; label: string }[] = [
  { value: "week", label: "Weekly" },
  { value: "month", label: "Monthly" },
  { value: "quarter", label: "Quarterly" },
  { value: "year", label: "Yearly" },
];

export default function GoalsPage() {
  return (
    <SettingsShell feature="goals">
      <Goals />
    </SettingsShell>
  );
}

function Goals() {
  const toast = useToast();
  const { session } = useSession();
  const currency = (session?.organization.settings?.currency as string | undefined) || "USD";
  const [scope, setScope] = useState<"all" | "mine">("all");
  const goals = useApi<GoalList>(`/goals${scope === "mine" ? "?mine=true" : ""}`);
  const [editing, setEditing] = useState<Goal | "new" | null>(null);
  const [deleting, setDeleting] = useState<Goal | null>(null);
  const [busy, setBusy] = useState(false);
  const items = goals.data?.items ?? [];
  const canEdit = Boolean(goals.data?.can_edit);
  const fmt = (value: number, unit: string) => (unit === "money" ? compactMoney(value, currency) : number(value));

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/goals/${deleting.id}`);
      toast("Goal deleted.");
      setDeleting(null);
      await goals.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Goals"
        description="Targets for people, teams or the whole workspace. Progress updates live from deals, meetings, calls, emails, tasks and leads."
        actions={
          canEdit ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New goal
            </button>
          ) : null
        }
      />
      <div className="ui-toolbar" style={{ marginBottom: 14 }}>
        <Tabs
          label="Goals"
          value={scope}
          onChange={setScope}
          items={[
            { value: "all", label: "All goals" },
            { value: "mine", label: "Mine" },
          ]}
        />
      </div>
      {goals.error ? <Alert>{goals.error}</Alert> : null}
      {goals.loading && !goals.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : items.length ? (
        <div className="dm-goals">
          {items.map((goal) => {
            const pace = PACE[goal.pace];
            return (
              <Card
                key={goal.id}
                title={goal.name}
                className={goal.is_active ? undefined : "dm-inactive"}
                actions={
                  <span className="ui-row" style={{ gap: 4 }}>
                    {!goal.is_active ? <Badge>Paused</Badge> : <Badge tone={pace.tone}>{pace.label}</Badge>}
                    {canEdit ? (
                      <>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${goal.name}`} onClick={() => setEditing(goal)}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${goal.name}`} onClick={() => setDeleting(goal)}>
                          <Trash2 size={14} />
                        </button>
                      </>
                    ) : null}
                  </span>
                }
              >
                <div className="ui-muted ui-small">
                  {goal.metric_label} · {goal.period_label} ·{" "}
                  {goal.assignee_type === "workspace" ? "Whole workspace" : goal.assignees.join(", ") || "No one yet"}
                </div>
                <div className="dm-goal-numbers">
                  <strong>{fmt(goal.actual, goal.unit)}</strong>
                  <span className="ui-muted">of {fmt(goal.target_total, goal.unit)}</span>
                  <span className="ui-spacer" />
                  <strong>{number(goal.percent, 1)}%</strong>
                </div>
                <GoalBar percent={goal.percent} elapsed={goal.elapsed_percent} tone={pace.tone} />
                <div className="ui-muted ui-small" style={{ marginTop: 6 }}>
                  {number(goal.elapsed_percent)}% of the period has passed.
                </div>
                {goal.people.length > 1 ? (
                  <div className="dm-goal-people">
                    {goal.people
                      .slice()
                      .sort((a, b) => b.percent - a.percent)
                      .map((person) => (
                        <div key={person.id} className="dm-goal-person">
                          <span className="ui-truncate">{person.name}</span>
                          <GoalBar percent={person.percent} elapsed={goal.elapsed_percent} tone={person.percent >= goal.elapsed_percent ? "success" : "warning"} small />
                          <span className="ui-num ui-small">{fmt(person.actual, goal.unit)}</span>
                        </div>
                      ))}
                  </div>
                ) : null}
              </Card>
            );
          })}
        </div>
      ) : (
        <div className="ui-card">
          <EmptyState
            icon={<GoalIcon size={20} />}
            title={scope === "mine" ? "No goals for you yet" : "No goals yet"}
            message="Set a target — say $50k won this month or 40 calls a week — and see everyone's progress here."
          />
        </div>
      )}

      <GoalModal
        goal={editing}
        metrics={goals.data?.metrics ?? []}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await goals.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete goal?"
        message={`“${deleting?.name ?? ""}” and its progress will disappear. The deals and activity it counted aren't affected.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function GoalBar({ percent, elapsed, tone, small }: { percent: number; elapsed: number; tone: Tone; small?: boolean }) {
  return (
    <div className={`dm-goal-bar tone-${tone}${small ? " small" : ""}`} role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
      <div className="fill" style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
      <div className="marker" style={{ left: `${Math.min(100, Math.max(0, elapsed))}%` }} title="Where you'd be at an even pace" />
    </div>
  );
}

function GoalModal({ goal, metrics, onClose, onSaved }: { goal: Goal | "new" | null; metrics: Metric[]; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const teams = useApi<Team[]>(goal ? "/admin/teams" : null);
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("deals_won_amount");
  const [period, setPeriod] = useState<Period>("month");
  const [target, setTarget] = useState("");
  const [assigneeType, setAssigneeType] = useState<AssigneeType>("user");
  const [assignees, setAssignees] = useState<string[]>([]);
  const [active, setActive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!goal) return;
    const existing = goal === "new" ? null : goal;
    setName(existing?.name ?? "");
    setMetric(existing?.metric ?? "deals_won_amount");
    setPeriod(existing?.period ?? "month");
    setTarget(existing ? String(existing.target) : "");
    setAssigneeType(existing?.assignee_type ?? "user");
    setAssignees(existing?.assignee_ids ?? []);
    setActive(existing?.is_active ?? true);
    setError(null);
  }, [goal]);

  const unit = useMemo(() => metrics.find((m) => m.key === metric)?.unit ?? "count", [metric, metrics]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = {
      name,
      metric,
      period,
      target: Number(target),
      assignee_type: assigneeType,
      assignee_ids: assigneeType === "workspace" ? [] : assignees,
      is_active: active,
    };
    try {
      if (goal === "new") await api.post("/goals", payload);
      else if (goal) await api.put(`/goals/${goal.id}`, payload);
      toast(goal === "new" ? "Goal created." : "Goal saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={Boolean(goal)}
      title={goal === "new" ? "New goal" : "Edit goal"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="goal-form" className="ui-btn ui-btn-primary" disabled={saving || !name.trim() || !(Number(target) > 0)}>
            {saving ? <span className="ui-spinner" /> : null} Save goal
          </button>
        </>
      }
    >
      <form id="goal-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Name" required className="span-2">
            <input className="ui-input" value={name} maxLength={160} onChange={(event) => setName(event.target.value)} placeholder="Q4 new business" />
          </Field>
          <Field label="What to count">
            <select className="ui-select" value={metric} onChange={(event) => setMetric(event.target.value)}>
              {metrics.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Period" hint="Resets at the start of each period (quarters and years follow your fiscal year).">
            <select className="ui-select" value={period} onChange={(event) => setPeriod(event.target.value as Period)}>
              {PERIODS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={unit === "money" ? "Target amount" : "Target"} required hint={assigneeType === "user" ? "Per person." : undefined}>
            <input className="ui-input" type="number" min={1} step={unit === "money" ? 100 : 1} value={target} onChange={(event) => setTarget(event.target.value)} />
          </Field>
          <Field label="Status">
            <span className="ui-row" style={{ height: 44 }}>
              <Toggle label="Goal is active" checked={active} onChange={setActive} />
              <span className="ui-small">{active ? "Active" : "Paused"}</span>
            </span>
          </Field>
          <Field label="Who it's for" className="span-2">
            <select
              className="ui-select"
              value={assigneeType}
              onChange={(event) => {
                setAssigneeType(event.target.value as AssigneeType);
                setAssignees([]);
              }}
            >
              <option value="user">Specific people (each gets the target)</option>
              <option value="team">Teams (shared target)</option>
              <option value="workspace">Whole workspace</option>
            </select>
          </Field>
        </div>
        {assigneeType === "user" ? <MemberPicker value={assignees} onChange={setAssignees} label="People" /> : null}
        {assigneeType === "team" ? (
          <div className="settings-member-list" role="group" aria-label="Teams">
            {teams.error ? <span className="ui-muted ui-small">{teams.error}</span> : null}
            {(teams.data ?? []).map((team) => (
              <label key={team.id} className="ui-check">
                <input
                  type="checkbox"
                  checked={assignees.includes(team.id)}
                  onChange={(event) => setAssignees(event.target.checked ? [...assignees, team.id] : assignees.filter((id) => id !== team.id))}
                />
                <span>
                  {team.name} <span className="ui-muted ui-small">{team.members.length} people</span>
                </span>
              </label>
            ))}
            {teams.data && !teams.data.length ? <span className="ui-muted ui-small">No teams yet. Create them in Settings → Teams.</span> : null}
          </div>
        ) : null}
      </form>
    </Modal>
  );
}
