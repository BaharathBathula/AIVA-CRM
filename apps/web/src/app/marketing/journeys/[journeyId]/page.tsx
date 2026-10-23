"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowDown, ArrowLeft, ArrowUp, Bell, CheckSquare, Clock, GitFork, Mail, Pause, PenLine, Play, Plus, Trash2, UserPlus, Zap } from "lucide-react";
import { useEffect, useState } from "react";

import { ConditionEditor, cleanConditions, type Condition } from "@/components/condition-editor";
import { ValueInput } from "@/components/field-inputs";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, PageHeader } from "@/components/ui";
import { relativeTime, titleCase } from "@/lib/format";
import type { JourneySummary } from "@/lib/marketing";
import type { Catalog } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Step = { type: string; params: Record<string, unknown> };

interface JourneyDetail extends JourneySummary {
  recent: {
    id: string;
    record_type: string;
    record_id: string;
    record_label: string | null;
    status: string;
    current_step: number;
    next_run_at: string | null;
    history: { at: string; step?: number; message: string }[];
    updated_at: string;
  }[];
}

const STEP_META: Record<string, { label: string; icon: typeof Mail }> = {
  send_email: { label: "Send an email", icon: Mail },
  wait: { label: "Wait", icon: Clock },
  create_task: { label: "Create a task", icon: CheckSquare },
  update_field: { label: "Update a field", icon: PenLine },
  notify: { label: "Notify the owner", icon: Bell },
  branch: { label: "Check conditions", icon: GitFork },
};

function defaults(type: string): Record<string, unknown> {
  switch (type) {
    case "wait":
      return { days: 2, hours: 0 };
    case "create_task":
      return { title: "Follow up with {{first_name}}", due_in_days: 1 };
    case "notify":
      return { title: "{{full_name}} reached this step" };
    case "branch":
      return { conditions: [], else: "exit" };
    case "update_field":
      return { field: "", value: "" };
    default:
      return { template_id: "" };
  }
}

export default function JourneyPage() {
  return (
    <FeaturePage feature="customer_journeys">
      <JourneyBuilder />
    </FeaturePage>
  );
}

function JourneyBuilder() {
  const { journeyId } = useParams<{ journeyId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const journey = useApi<JourneyDetail>(`/journeys/${journeyId}`);
  const catalog = useApi<Catalog>("/meta/catalog");
  const templates = useApi<{ id: string; name: string }[]>("/email-templates");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [trigger, setTrigger] = useState<"created" | "manual">("created");
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const editable = can("marketing.manage");

  useEffect(() => {
    if (!journey.data) return;
    setName(journey.data.name);
    setDescription(journey.data.description ?? "");
    setTrigger(journey.data.trigger);
    setConditions(journey.data.conditions as Condition[]);
    setSteps(journey.data.steps);
  }, [journey.data]);

  if (journey.error) return <Alert>{journey.error}</Alert>;
  if (!journey.data || !catalog.data) return <Card><LoadingRows rows={10} /></Card>;

  const data = journey.data;
  const object = catalog.data.objects.find((o) => o.key === data.object_type)!;
  const updatable = object.fields.filter((f) => f.updatable);
  const setStep = (index: number, next: Step) => setSteps(steps.map((s, i) => (i === index ? next : s)));
  const param = (step: Step, key: string) => (step.params[key] ?? "") as string;

  async function save() {
    setBusy(true);
    try {
      await api.put(`/journeys/${journeyId}`, {
        name: name.trim(),
        description: description || null,
        object_type: data.object_type,
        trigger,
        conditions: cleanConditions(conditions),
        steps: steps.map((step) => (step.type === "branch" ? { ...step, params: { ...step.params, conditions: cleanConditions((step.params.conditions as Condition[]) ?? []) } } : step)),
      });
      await journey.reload();
      toast("Journey saved.");
      return true;
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: "active" | "paused") {
    if (status === "active" && !(await save())) return;
    try {
      await api.post(`/journeys/${journeyId}/status`, { status });
      await journey.reload();
      toast(status === "active" ? "Journey is live." : "Journey paused.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function enrollAll() {
    try {
      const result = await api.post<{ enrolled: number }>(`/journeys/${journeyId}/enroll`, { all_matching: true });
      toast(`Added ${result.enrolled} ${data.object_type}${result.enrolled === 1 ? "" : "s"}.`);
      await journey.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function runNow() {
    try {
      const result = await api.post<{ processed: number }>(`/journeys/${journeyId}/run`);
      toast(`Moved ${result.processed} ${result.processed === 1 ? "person" : "people"} forward.`);
      await journey.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href="/marketing/journeys" className="ui-row" style={{ gap: 6, color: "inherit", textDecoration: "none" }}>
            <ArrowLeft size={14} /> Journeys
          </Link>
        }
        title={data.name}
        description={`${titleCase(data.object_type)} journey · ${data.enrollments.active} in progress · ${data.enrollments.completed} completed`}
        actions={
          editable ? (
            <>
              <Badge tone={data.status === "active" ? "success" : data.status === "paused" ? "warning" : "neutral"}>{data.status}</Badge>
              <button type="button" className="ui-btn" disabled={busy} onClick={() => void save()}>
                Save
              </button>
              {data.status === "active" ? (
                <button type="button" className="ui-btn" onClick={() => void setStatus("paused")}>
                  <Pause size={15} /> Pause
                </button>
              ) : (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => void setStatus("active")}>
                  <Play size={15} /> Turn on
                </button>
              )}
            </>
          ) : null
        }
      />
      <div className="ui-builder">
        <div className="ui-stack" style={{ gap: 16 }}>
          <Card title="Who enters" icon={<UserPlus size={16} />}>
            <div className="ui-stack">
              <div className="ui-form-grid">
                <Field label="Name">
                  <input className="ui-input" value={name} disabled={!editable} onChange={(e) => setName(e.target.value)} />
                </Field>
                <Field label="Starts">
                  <select className="ui-select" value={trigger} disabled={!editable} onChange={(e) => setTrigger(e.target.value as "created" | "manual")}>
                    <option value="created">When a new {data.object_type} matches</option>
                    <option value="manual">Only when added by hand</option>
                  </select>
                </Field>
                <Field label="Description" className="span-2">
                  <input className="ui-input" value={description} disabled={!editable} onChange={(e) => setDescription(e.target.value)} />
                </Field>
              </div>
              <ConditionEditor
                fields={object.fields}
                operators={catalog.data.operators}
                conditions={conditions}
                onChange={setConditions}
                disabled={!editable}
                emptyLabel={`Every new ${data.object_type} enters.`}
              />
            </div>
          </Card>

          <Card title="Steps" icon={<Zap size={16} />}>
            <div className="ui-stack" style={{ gap: 14 }}>
              {steps.map((step, index) => {
                const meta = STEP_META[step.type] ?? STEP_META.notify;
                const Icon = meta.icon;
                const field = object.fields.find((f) => f.key === param(step, "field"));
                return (
                  <div key={index}>
                    {index > 0 ? <div className="builder-connector" /> : null}
                    <div className="builder-step">
                      <div className="builder-step-head">
                        <span className="num">{index + 1}</span>
                        <Icon size={16} />
                        <select className="ui-select sm" style={{ flex: 1 }} value={step.type} disabled={!editable} onChange={(e) => setStep(index, { type: e.target.value, params: defaults(e.target.value) })} aria-label="Step type">
                          {Object.entries(STEP_META).map(([key, value]) => (
                            <option key={key} value={key}>
                              {value.label}
                            </option>
                          ))}
                        </select>
                        {editable ? (
                          <>
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move up" disabled={index === 0} onClick={() => { const next = [...steps]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; setSteps(next); }}>
                              <ArrowUp size={14} />
                            </button>
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move down" disabled={index === steps.length - 1} onClick={() => { const next = [...steps]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; setSteps(next); }}>
                              <ArrowDown size={14} />
                            </button>
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove step" onClick={() => setSteps(steps.filter((_, i) => i !== index))}>
                              <Trash2 size={14} />
                            </button>
                          </>
                        ) : null}
                      </div>
                      {step.type === "send_email" ? (
                        <select className="ui-select" value={param(step, "template_id")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { template_id: e.target.value } })} aria-label="Template">
                          <option value="">Choose a template…</option>
                          {(templates.data ?? []).map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.name}
                            </option>
                          ))}
                        </select>
                      ) : null}
                      {step.type === "wait" ? (
                        <div className="ui-row" style={{ gap: 8 }}>
                          <input className="ui-input sm" type="number" min={0} max={365} style={{ width: 90 }} value={param(step, "days")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { ...step.params, days: Number(e.target.value) } })} aria-label="Days" />
                          <span className="ui-muted ui-small">days</span>
                          <input className="ui-input sm" type="number" min={0} max={23} style={{ width: 90 }} value={param(step, "hours")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { ...step.params, hours: Number(e.target.value) } })} aria-label="Hours" />
                          <span className="ui-muted ui-small">hours</span>
                        </div>
                      ) : null}
                      {step.type === "create_task" ? (
                        <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                          <input className="ui-input sm" style={{ flex: "1 1 240px" }} value={param(step, "title")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { ...step.params, title: e.target.value } })} aria-label="Task title" />
                          <select className="ui-select sm" value={param(step, "due_in_days")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { ...step.params, due_in_days: Number(e.target.value) } })} aria-label="Due">
                            {[0, 1, 2, 3, 7].map((d) => (
                              <option key={d} value={d}>
                                {d === 0 ? "Due today" : `Due in ${d} day${d === 1 ? "" : "s"}`}
                              </option>
                            ))}
                          </select>
                        </div>
                      ) : null}
                      {step.type === "notify" ? (
                        <input className="ui-input sm" value={param(step, "title")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { ...step.params, title: e.target.value } })} aria-label="Notification" />
                      ) : null}
                      {step.type === "update_field" ? (
                        <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                          <select className="ui-select sm" style={{ flex: "1 1 160px" }} value={param(step, "field")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { field: e.target.value, value: "" } })} aria-label="Field">
                            <option value="">Choose a field…</option>
                            {updatable.map((f) => (
                              <option key={f.key} value={f.key}>
                                {f.label}
                              </option>
                            ))}
                          </select>
                          <div style={{ flex: "1 1 160px" }}>
                            <ValueInput field={field} operator="equals" value={param(step, "value")} allowMe={false} onChange={(value) => setStep(index, { ...step, params: { ...step.params, value } })} />
                          </div>
                        </div>
                      ) : null}
                      {step.type === "branch" ? (
                        <div className="ui-stack" style={{ gap: 8 }}>
                          <span className="ui-small ui-strong">Continue only if</span>
                          <ConditionEditor
                            fields={object.fields}
                            operators={catalog.data!.operators}
                            conditions={(step.params.conditions as Condition[]) ?? []}
                            onChange={(next) => setStep(index, { ...step, params: { ...step.params, conditions: next } })}
                            disabled={!editable}
                            emptyLabel="No conditions — everyone continues."
                          />
                          <Field label="Otherwise">
                            <select className="ui-select sm" value={String(step.params.else ?? "exit")} disabled={!editable} onChange={(e) => setStep(index, { ...step, params: { ...step.params, else: e.target.value } })}>
                              <option value="exit">Leave the journey</option>
                              <option value="continue">Continue anyway</option>
                              {steps.map((_, i) => (i !== index ? <option key={i} value={i + 1}>Jump to step {i + 1}</option> : null))}
                            </select>
                          </Field>
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
              {editable ? (
                <div className="chip-list">
                  {Object.entries(STEP_META).map(([key, value]) => (
                    <button key={key} type="button" className="chip" onClick={() => setSteps([...steps, { type: key, params: defaults(key) }])}>
                      <Plus size={13} /> {value.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </Card>
        </div>

        <div className="ui-stack" style={{ gap: 16 }}>
          <Card title="Run it">
            <div className="ui-stack">
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                {data.status === "active"
                  ? "AIVA checks journeys every minute and moves people to their next step when it's due."
                  : "Turn the journey on to start enrolling people."}
              </p>
              {editable ? (
                <>
                  <button type="button" className="ui-btn" disabled={data.status !== "active"} onClick={() => void enrollAll()}>
                    <UserPlus size={15} /> Add everyone who matches now
                  </button>
                  <button type="button" className="ui-btn" disabled={data.status !== "active"} onClick={() => void runNow()}>
                    <Play size={15} /> Run due steps now
                  </button>
                  <button type="button" className="ui-btn ui-btn-ghost" onClick={() => setDeleting(true)}>
                    <Trash2 size={14} /> Delete journey
                  </button>
                </>
              ) : null}
            </div>
          </Card>
          <Card title="Recent people" flush>
            {data.recent.length ? (
              <div className="ui-list">
                {data.recent.slice(0, 12).map((e) => (
                  <Link key={e.id} href={`/${e.record_type === "lead" ? "leads" : "contacts"}/${e.record_id}`} className="ui-list-item">
                    <span className="main">
                      <span className="title">{e.record_label ?? "Record"}</span>
                      <span className="sub">{e.history.length ? e.history[e.history.length - 1].message : `Step ${e.current_step + 1}`}</span>
                    </span>
                    <Badge tone={e.status === "active" ? "info" : e.status === "completed" ? "success" : e.status === "failed" ? "danger" : "neutral"}>{e.status}</Badge>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="empty-hint">Nobody has entered yet.</p>
            )}
            {data.recent.length ? <p className="ui-muted ui-small" style={{ padding: "0 18px 14px", margin: 0 }}>Last update {relativeTime(data.recent[0].updated_at)}</p> : null}
          </Card>
        </div>
      </div>
      <ConfirmDialog
        open={deleting}
        title="Delete this journey?"
        message="People currently in it stop where they are."
        onConfirm={async () => {
          await api.del(`/journeys/${journeyId}`);
          router.push("/marketing/journeys");
        }}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}
