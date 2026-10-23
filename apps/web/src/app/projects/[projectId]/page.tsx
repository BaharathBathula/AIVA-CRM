"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Flag, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { PinButton } from "@/components/pins";
import { ProjectModal } from "@/components/project-modal";
import { useSession, useToast } from "@/components/providers";
import { RecordDocuments } from "@/components/record-panels";
import { Alert, Avatar, Badge, Card, ConfirmDialog, Field, Kpi, LoadingRows, Modal, PageHeader, Progress, Tabs } from "@/components/ui";
import { compactMoney, date, shortDate } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { PROJECT_STATUS, type Member, type Milestone, type Project, type ProjectTask } from "@/lib/workspace";

export default function ProjectPage() {
  return (
    <FeaturePage feature="projects">
      <ProjectView />
    </FeaturePage>
  );
}

const PRIORITY_TONE = { low: "neutral", medium: "info", high: "warning", urgent: "danger" } as const;

function ProjectView() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { session } = useSession();
  const project = useApi<Project>(`/projects/${projectId}`);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [taskModal, setTaskModal] = useState<{ task?: ProjectTask; milestoneId?: string } | null>(null);
  const [milestoneModal, setMilestoneModal] = useState<{ milestone?: Milestone } | null>(null);
  const [filter, setFilter] = useState<"open" | "mine" | "done" | "all">("all");
  const p = project.data;

  async function setStatus(task: ProjectTask, status: ProjectTask["status"]) {
    // Tick the box straight away; the server's numbers follow.
    if (p) project.setData({ ...p, tasks: (p.tasks ?? []).map((t) => (t.id === task.id ? { ...t, status } : t)) });
    try {
      await api.patch(`/projects/${projectId}/tasks/${task.id}`, { status });
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
    await project.reload();
  }

  async function toggleMilestone(m: Milestone) {
    try {
      await api.patch(`/projects/${projectId}/milestones/${m.id}`, { status: m.status === "completed" ? "open" : "completed" });
      await project.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function changeStatus(status: Project["status"]) {
    try {
      await api.patch<Project>(`/projects/${projectId}`, { status });
      await project.reload();
      toast(`Status set to ${PROJECT_STATUS[status].label.toLowerCase()}.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    try {
      await api.del(`/projects/${projectId}`);
      toast("Project deleted.");
      router.replace("/projects");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setDeleting(false);
    }
  }

  if (project.error) return <Alert>{project.error}</Alert>;
  if (!p) return <div className="ui-card"><LoadingRows rows={10} /></div>;
  const me = session?.user.id;
  const tasks = (p.tasks ?? []).filter((t) =>
    filter === "open" ? t.status === "open" || t.status === "in_progress" : filter === "mine" ? t.assigned_to_user_id === me && t.status !== "completed" : filter === "done" ? t.status === "completed" : true
  );
  const closed = p.status === "completed" || p.status === "cancelled";
  const milestoneName = (id: string | null) => p.milestones?.find((m) => m.id === id)?.name;

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/projects" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All projects
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            {p.display_number} · <Badge tone={p.is_late ? "danger" : PROJECT_STATUS[p.status].tone}>{p.is_late ? "Past due" : PROJECT_STATUS[p.status].label}</Badge> · {p.priority} priority
          </>
        }
        title={p.name}
        description={
          <>
            {p.account_id ? <Link href={`/accounts/${p.account_id}`}>{p.account_name}</Link> : "No account"}
            {p.opportunity_id ? (
              <>
                {" · "}
                <Link href={`/opportunities/${p.opportunity_id}`}>{p.opportunity_name}</Link>
              </>
            ) : null}
            {` · Owner ${p.owner_name ?? "—"}`}
          </>
        }
        actions={
          <>
            <PinButton type="project" id={p.id} />
            {p.can_manage ? (
              <>
                <select className="ui-select" style={{ width: "auto" }} value={p.status} onChange={(e) => void changeStatus(e.target.value as Project["status"])} aria-label="Project status">
                  {Object.entries(PROJECT_STATUS).map(([value, meta]) => (
                    <option key={value} value={value}>
                      {meta.label}
                    </option>
                  ))}
                </select>
                <button type="button" className="ui-btn" onClick={() => setEditing(true)}>
                  <Pencil size={15} /> Edit
                </button>
              </>
            ) : null}
            {p.can_work && !closed ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setTaskModal({})}>
                <Plus size={15} /> Add task
              </button>
            ) : null}
          </>
        }
      />
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Progress" value={`${p.progress}%`} note={`${p.tasks_done} of ${p.tasks_total} tasks done`} tone={p.progress >= 100 ? "success" : undefined} />
        <Kpi label="Overdue tasks" value={p.tasks_overdue} tone={p.tasks_overdue ? "danger" : undefined} />
        <Kpi label="Milestones" value={`${p.milestones_done}/${p.milestones_total}`} />
        <Kpi label="Timeline" value={p.due_date ? shortDate(p.due_date) : "No due date"} note={p.start_date ? `Started ${date(p.start_date)}` : undefined} tone={p.is_late ? "danger" : undefined} />
      </div>
      <div className="ui-split">
        <Card
          title="Tasks"
          flush
          actions={
            <Tabs
              label="Task filter"
              value={filter}
              onChange={setFilter}
              items={[
                { value: "all", label: "All" },
                { value: "open", label: "Open" },
                { value: "mine", label: "Mine" },
                { value: "done", label: "Done" },
              ]}
            />
          }
        >
          <div style={{ padding: "4px 18px 12px" }}>
            {tasks.length ? (
              tasks.map((task) => (
                <div key={task.id} className={`task-row${task.status === "completed" ? " done" : ""}`}>
                  <input
                    type="checkbox"
                    className="task-check"
                    checked={task.status === "completed"}
                    disabled={!p.can_work}
                    onChange={() => void setStatus(task, task.status === "completed" ? "open" : "completed")}
                    aria-label={`Mark ${task.title} ${task.status === "completed" ? "not done" : "done"}`}
                  />
                  <div className="task-main">
                    <span className="task-title">{task.title}</span>
                    <span className="task-sub">
                      {task.assignee_name ?? "Unassigned"}
                      {task.due_at ? (
                        <span style={{ color: task.is_overdue ? "var(--danger)" : undefined }}> · due {shortDate(task.due_at)}</span>
                      ) : null}
                      {task.milestone_id ? ` · ${milestoneName(task.milestone_id)}` : ""}
                    </span>
                  </div>
                  {task.status === "in_progress" ? <Badge tone="info">In progress</Badge> : null}
                  <Badge tone={PRIORITY_TONE[task.priority]}>{task.priority}</Badge>
                  {p.can_work ? (
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${task.title}`} onClick={() => setTaskModal({ task })}>
                      <Pencil size={14} />
                    </button>
                  ) : null}
                </div>
              ))
            ) : (
              <p className="ui-muted ui-small" style={{ margin: "14px 0" }}>
                {filter === "open" ? "No open tasks." : "Nothing here."}
              </p>
            )}
          </div>
        </Card>
        <div className="ui-stack">
          <Card
            title="Milestones"
            icon={<Flag size={16} />}
            actions={
              p.can_manage ? (
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setMilestoneModal({})}>
                  <Plus size={14} /> Add
                </button>
              ) : null
            }
          >
            <div className="milestones">
              {(p.milestones ?? []).map((m) => (
                <div key={m.id} className="milestone">
                  <div className="milestone-head">
                    <input type="checkbox" className="task-check" checked={m.status === "completed"} disabled={!p.can_manage} onChange={() => void toggleMilestone(m)} aria-label={`Milestone ${m.name} done`} />
                    <strong style={{ textDecoration: m.status === "completed" ? "line-through" : undefined }}>{m.name}</strong>
                    <span className="ui-small" style={{ color: m.is_late ? "var(--danger)" : "var(--muted)" }}>
                      {m.due_date ? shortDate(m.due_date) : ""}
                    </span>
                    {p.can_manage ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${m.name}`} onClick={() => setMilestoneModal({ milestone: m })}>
                        <Pencil size={13} />
                      </button>
                    ) : null}
                  </div>
                  <div className="ui-row" style={{ gap: 8 }}>
                    <Progress value={m.tasks_total ? (m.tasks_done / m.tasks_total) * 100 : m.status === "completed" ? 100 : 0} tone={m.status === "completed" ? "success" : undefined} />
                    <span className="ui-small ui-muted">
                      {m.tasks_done}/{m.tasks_total}
                    </span>
                    {p.can_work && !closed ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setTaskModal({ milestoneId: m.id })}>
                        <Plus size={13} /> Task
                      </button>
                    ) : null}
                  </div>
                </div>
              ))}
              {!p.milestones?.length ? <p className="ui-muted ui-small" style={{ margin: 0 }}>No milestones.</p> : null}
            </div>
          </Card>
          <Card title="Team">
            <div className="member-chips">
              {p.owner_name ? (
                <span className="member-chip">
                  <Avatar name={p.owner_name} size={22} /> {p.owner_name} <span className="ui-muted">· owner</span>
                </span>
              ) : null}
              {p.members.filter((m) => m.id !== p.owner_user_id).map((m) => (
                <span key={m.id} className="member-chip">
                  <Avatar name={m.name} size={22} /> {m.name}
                </span>
              ))}
            </div>
            <dl className="ui-dl" style={{ marginTop: 14 }}>
              <dt>Budget</dt>
              <dd>{p.budget !== null ? compactMoney(p.budget, p.currency) : "—"}</dd>
              <dt>Start</dt>
              <dd>{date(p.start_date)}</dd>
              <dt>Due</dt>
              <dd>{date(p.due_date)}</dd>
            </dl>
            {p.description ? <p style={{ margin: "12px 0 0", whiteSpace: "pre-wrap", fontSize: 13.5 }}>{p.description}</p> : null}
          </Card>
          <RecordDocuments recordType="project" recordId={p.id} />
          {p.can_manage ? (
            <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDeleting(true)}>
              <Trash2 size={15} /> Delete project
            </button>
          ) : null}
        </div>
      </div>
      <ProjectModal open={editing} project={p} onClose={() => setEditing(false)} onSaved={() => (setEditing(false), void project.reload(), toast("Project saved."))} />
      <TaskModal state={taskModal} project={p} onClose={() => setTaskModal(null)} onSaved={() => (setTaskModal(null), void project.reload())} />
      <MilestoneModal state={milestoneModal} projectId={p.id} onClose={() => setMilestoneModal(null)} onSaved={() => (setMilestoneModal(null), void project.reload())} />
      <ConfirmDialog open={deleting} title={`Delete ${p.name}?`} message="The project, its milestones and all its tasks are deleted." onConfirm={() => void remove()} onClose={() => setDeleting(false)} />
    </>
  );
}

function TaskModal({ state, project, onClose, onSaved }: { state: { task?: ProjectTask; milestoneId?: string } | null; project: Project; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const members = useApi<Member[]>(state ? "/meta/members" : null);
  const task = state?.task;
  const [form, setForm] = useState({ title: "", description: "", assigned_to_user_id: "", due: "", priority: "medium", milestone_id: "", status: "open" });
  const [seen, setSeen] = useState<typeof state>(null);
  const [busy, setBusy] = useState(false);
  if (state !== seen) {
    setSeen(state);
    setForm({
      title: task?.title ?? "",
      description: task?.description ?? "",
      assigned_to_user_id: task?.assigned_to_user_id ?? "",
      due: task?.due_at ? task.due_at.slice(0, 10) : "",
      priority: task?.priority ?? "medium",
      milestone_id: task?.milestone_id ?? state?.milestoneId ?? "",
      status: task?.status ?? "open",
    });
  }
  const team = (members.data ?? []).filter((m) => m.id === project.owner_user_id || project.member_user_ids.includes(m.id));
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: e.target.value });

  async function save() {
    if (!form.title.trim()) return;
    setBusy(true);
    const payload = {
      title: form.title.trim(),
      description: form.description.trim() || null,
      assigned_to_user_id: form.assigned_to_user_id || null,
      due_at: form.due ? new Date(`${form.due}T17:00:00`).toISOString() : null,
      priority: form.priority,
      milestone_id: form.milestone_id || null,
      ...(task ? { status: form.status } : {}),
    };
    try {
      if (task) await api.patch(`/projects/${project.id}/tasks/${task.id}`, payload);
      else await api.post(`/projects/${project.id}/tasks`, payload);
      toast(task ? "Task saved." : "Task added.");
      onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!task) return;
    try {
      await api.del(`/projects/${project.id}/tasks/${task.id}`);
      toast("Task deleted.");
      onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <Modal
      open={state !== null}
      title={task ? "Edit task" : "New task"}
      onClose={onClose}
      footer={
        <>
          {task ? (
            <button type="button" className="ui-btn ui-btn-ghost" style={{ marginRight: "auto" }} onClick={() => void remove()}>
              <Trash2 size={15} /> Delete
            </button>
          ) : null}
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy || !form.title.trim()}>
            Save
          </button>
        </>
      }
    >
      <div className="ui-form-grid">
        <Field label="Task" required className="span-2">
          <input className="ui-input" value={form.title} onChange={set("title")} placeholder="Configure the warehouse network" />
        </Field>
        <Field label="Assigned to">
          <select className="ui-select" value={form.assigned_to_user_id} onChange={set("assigned_to_user_id")}>
            <option value="">Me</option>
            {team.map((m) => (
              <option key={m.id} value={m.id}>
                {m.full_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Due">
          <input className="ui-input" type="date" value={form.due} onChange={set("due")} />
        </Field>
        <Field label="Priority">
          <select className="ui-select" value={form.priority} onChange={set("priority")}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="urgent">Urgent</option>
          </select>
        </Field>
        <Field label="Milestone">
          <select className="ui-select" value={form.milestone_id} onChange={set("milestone_id")}>
            <option value="">None</option>
            {(project.milestones ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>
        {task ? (
          <Field label="Status">
            <select className="ui-select" value={form.status} onChange={set("status")}>
              <option value="open">Open</option>
              <option value="in_progress">In progress</option>
              <option value="completed">Done</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </Field>
        ) : null}
        <Field label="Details" className="span-2">
          <textarea className="ui-textarea" rows={3} value={form.description} onChange={set("description")} />
        </Field>
      </div>
    </Modal>
  );
}

function MilestoneModal({ state, projectId, onClose, onSaved }: { state: { milestone?: Milestone } | null; projectId: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const milestone = state?.milestone;
  const [name, setName] = useState("");
  const [due, setDue] = useState("");
  const [seen, setSeen] = useState<typeof state>(null);
  if (state !== seen) {
    setSeen(state);
    setName(milestone?.name ?? "");
    setDue(milestone?.due_date ?? "");
  }
  async function save() {
    try {
      if (milestone) await api.patch(`/projects/${projectId}/milestones/${milestone.id}`, { name: name.trim(), due_date: due || null });
      else await api.post(`/projects/${projectId}/milestones`, { name: name.trim(), due_date: due || null });
      onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }
  async function remove() {
    if (!milestone) return;
    try {
      await api.del(`/projects/${projectId}/milestones/${milestone.id}`);
      onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }
  return (
    <Modal
      open={state !== null}
      size="sm"
      title={milestone ? "Edit milestone" : "New milestone"}
      onClose={onClose}
      footer={
        <>
          {milestone ? (
            <button type="button" className="ui-btn ui-btn-ghost" style={{ marginRight: "auto" }} onClick={() => void remove()}>
              <Trash2 size={15} /> Delete
            </button>
          ) : null}
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={!name.trim()}>
            Save
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <Field label="Name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Go-live" />
        </Field>
        <Field label="Due">
          <input className="ui-input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
