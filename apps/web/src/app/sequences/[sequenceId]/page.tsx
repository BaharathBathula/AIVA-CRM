"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Archive,
  ArchiveRestore,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Copy,
  CornerDownRight,
  Eye,
  Flag,
  Pause,
  Pencil,
  Play,
  Plus,
  Search,
  Send,
  SkipForward,
  Trash2,
  TriangleAlert,
  UserMinus,
  UserPlus,
  X,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { EmailEditor } from "@/components/email-editor";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { RecordSearch, type FoundRecord } from "@/components/record-search";
import {
  ENROLLMENT_STATUS,
  EnrollResults,
  SEQUENCE_STATUS,
  STEP_KIND,
  SenderSelect,
  isRunning,
  nextRunLabel,
  type EnrollResult,
  type Enrollment,
  type SequenceDetail,
  type SequenceStep,
  type StepKind,
} from "@/components/sequence-enroll";
import { NumberInput, Toggle, WEEKDAYS, hourLabel } from "@/components/settings-kit";
import { SnippetPicker } from "@/components/snippet-picker";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, PRIORITY_TONE, Tabs } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";

export default function SequencePage() {
  return (
    <FeaturePage feature="sequences">
      <SequenceView />
    </FeaturePage>
  );
}

type Tab = "steps" | "people" | "settings";

function percent(value: number | undefined) {
  return `${(value ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function SequenceView() {
  const { sequenceId } = useParams<{ sequenceId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const detail = useApi<SequenceDetail>(`/sequences/${sequenceId}`);
  const [tab, setTab] = useState<Tab>("steps");
  const [busy, setBusy] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [adding, setAdding] = useState(false);
  const [peopleVersion, setPeopleVersion] = useState(0);
  const s = detail.data;

  if (detail.error && !s) return <Alert>{detail.error}</Alert>;
  if (!s)
    return (
      <div className="ui-card">
        <LoadingRows rows={8} />
      </div>
    );

  const info = SEQUENCE_STATUS[s.status];
  const stats = s.stats;
  const manage = can("sequences.manage");
  const archived = s.status === "archived";

  async function setStatus(next: "active" | "paused" | "archived", message: string) {
    setBusy(true);
    try {
      const updated = await api.post<SequenceDetail>(`/sequences/${sequenceId}/status`, { status: next });
      detail.setData(updated);
      setArchiving(false);
      toast(message);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function duplicate() {
    setBusy(true);
    try {
      const copy = await api.post<{ id: string }>(`/sequences/${sequenceId}/duplicate`);
      toast("Copy created. It starts turned off.");
      router.push(`/sequences/${copy.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/sequences" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All sequences
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <span className="ui-row" style={{ gap: 8 }}>
            <Badge tone={info.tone} dot>
              {info.label}
            </Badge>
            <span>
              {s.owner_name ? `Owned by ${s.owner_name}` : "Sequence"} · {s.is_shared ? "Shared with the team" : "Private"}
            </span>
          </span>
        }
        title={s.name}
        description={s.description || undefined}
        actions={
          <>
            {manage && s.status === "active" ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setAdding(true)}>
                <UserPlus size={15} /> Add people
              </button>
            ) : null}
            {s.can_edit && !archived ? (
              s.status === "active" ? (
                <button type="button" className="ui-btn" disabled={busy} onClick={() => void setStatus("paused", "Paused. No steps run until you turn it back on.")}>
                  <Pause size={15} /> Pause
                </button>
              ) : (
                <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void setStatus("active", "Sequence is on. Due steps start running within a few minutes.")}>
                  <Play size={15} /> Turn on
                </button>
              )
            ) : null}
            {manage ? (
              <button type="button" className="ui-btn" disabled={busy} onClick={() => void duplicate()}>
                <Copy size={15} /> Duplicate
              </button>
            ) : null}
            {s.can_edit ? (
              archived ? (
                <button type="button" className="ui-btn" disabled={busy} onClick={() => void setStatus("paused", "Restored. It's paused until you turn it on.")}>
                  <ArchiveRestore size={15} /> Restore
                </button>
              ) : (
                <button type="button" className="ui-btn ui-btn-ghost" onClick={() => setArchiving(true)}>
                  <Archive size={15} /> Archive
                </button>
              )
            ) : null}
          </>
        }
      />

      {s.status === "draft" || s.status === "paused" ? (
        <div style={{ marginBottom: 16 }}>
          <Alert tone="info">
            This sequence is off, so no steps run and nobody new can be added. {s.can_edit ? "Turn it on when the steps are ready." : ""}
          </Alert>
        </div>
      ) : null}

      <div className="ui-grid ui-grid-4" style={{ marginBottom: 18 }}>
        <Kpi label="People" value={(stats.total ?? 0).toLocaleString()} note={`${stats.active ?? 0} in progress`} />
        <Kpi label="Emails sent" value={(stats.sent ?? 0).toLocaleString()} note={stats.sent ? `${percent(stats.rates?.bounced)} bounced` : undefined} />
        <Kpi label="Open rate" value={stats.sent ? percent(stats.rates?.opened) : "—"} note={stats.sent ? `${percent(stats.rates?.clicked)} clicked` : undefined} />
        <Kpi label="Reply rate" value={stats.sent ? percent(stats.rates?.replied) : "—"} note={stats.replied ? `${stats.replied} ${stats.replied === 1 ? "reply" : "replies"}` : undefined} />
      </div>

      <div style={{ marginBottom: 16 }}>
        <Tabs
          label="Sequence sections"
          value={tab}
          onChange={setTab}
          items={[
            { value: "steps", label: "Steps", count: s.steps.length },
            { value: "people", label: "People", count: stats.total ?? 0 },
            { value: "settings", label: "Settings" },
          ]}
        />
      </div>

      {tab === "steps" ? <StepsTab sequence={s} onChanged={() => void detail.reload()} setSequence={detail.setData} /> : null}
      {tab === "people" ? (
        <PeopleTab key={peopleVersion} sequence={s} onAdd={() => setAdding(true)} onChanged={() => void detail.reload()} />
      ) : null}
      {tab === "settings" ? <SettingsTab sequence={s} setSequence={detail.setData} /> : null}

      <AddPeopleModal
        open={adding}
        sequence={s}
        onClose={() => setAdding(false)}
        onDone={() => {
          setPeopleVersion((v) => v + 1);
          void detail.reload();
        }}
      />
      <ConfirmDialog
        open={archiving}
        title="Archive sequence?"
        message="Everyone still in it is removed and their open sequence tasks are cancelled. History and stats are kept, and you can restore it later."
        confirmLabel="Archive"
        busy={busy}
        onConfirm={() => void setStatus("archived", "Sequence archived.")}
        onClose={() => setArchiving(false)}
      />
    </>
  );
}

// ======================================================================= Steps

/** "Day 3" style timing for each active step, counted from when someone is added. */
function stepTimings(steps: SequenceStep[]) {
  let hours = 0;
  const out: Record<string, string> = {};
  for (const step of steps) {
    if (!step.is_active) continue;
    hours += step.delay_days * 24 + step.delay_hours;
    const day = Math.floor(hours / 24) + 1;
    const rest = hours % 24;
    out[step.id] = `Day ${day}${rest ? ` +${rest}h` : ""}`;
  }
  return out;
}

function delayLabel(step: SequenceStep, first: boolean) {
  if (!step.delay_days && !step.delay_hours) return first ? "Right after they're added" : "Right after the previous step";
  const parts = [step.delay_days ? `${step.delay_days} day${step.delay_days === 1 ? "" : "s"}` : "", step.delay_hours ? `${step.delay_hours} hour${step.delay_hours === 1 ? "" : "s"}` : ""].filter(Boolean);
  return `Wait ${parts.join(" ")} ${first ? "after they're added" : "after the previous step"}`;
}

function StepsTab({ sequence, onChanged, setSequence }: { sequence: SequenceDetail; onChanged: () => void; setSequence: (value: SequenceDetail) => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState<SequenceStep | "new" | null>(null);
  const [previewing, setPreviewing] = useState<SequenceStep | null>(null);
  const [deleting, setDeleting] = useState<SequenceStep | null>(null);
  const [busy, setBusy] = useState(false);
  const steps = sequence.steps;
  const timings = stepTimings(steps);
  const editable = sequence.can_edit && sequence.status !== "archived";
  const firstActiveId = steps.find((step) => step.is_active)?.id;

  async function move(index: number, direction: -1 | 1) {
    const order = steps.map((step) => step.id);
    const target = index + direction;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    setBusy(true);
    try {
      const updated = await api.post<SequenceDetail>(`/sequences/${sequence.id}/steps/reorder`, { step_ids: order });
      setSequence(updated);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/sequences/${sequence.id}/steps/${deleting.id}`);
      toast("Step deleted.");
      setDeleting(null);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {!steps.length ? (
        <Card>
          <EmptyState
            icon={<Plus size={20} />}
            title="No steps yet"
            message="Start with an automatic email, then add follow-ups: another email a few days later, a call, a LinkedIn touch."
            action={
              editable ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                  <Plus size={16} /> Add the first step
                </button>
              ) : null
            }
          />
        </Card>
      ) : (
        <ol className="seq-steps">
          {steps.map((step, index) => {
            const kind = STEP_KIND[step.kind];
            const Icon = kind.icon;
            const email = step.kind === "auto_email" || step.kind === "manual_email";
            const st = step.stats;
            const threaded = email && step.thread_reply && step.id !== firstActiveId && !step.subject;
            return (
              <li key={step.id} className={`seq-step${step.is_active ? "" : " off"}`}>
                <div className="seq-step-rail" aria-hidden>
                  <span className="seq-step-num">{index + 1}</span>
                </div>
                <div className="seq-step-card">
                  <div className="seq-step-head">
                    <span className={`seq-kind k-${step.kind}`}>
                      <Icon size={15} aria-hidden /> {kind.label}
                    </span>
                    <span className="seq-step-when">{step.is_active ? timings[step.id] : "Turned off"}</span>
                    <span className="ui-muted ui-small seq-step-delay">{delayLabel(step, step.id === firstActiveId)}</span>
                    <span className="ui-spacer" />
                    {step.waiting ? <Badge tone="info">{step.waiting} here now</Badge> : null}
                    {!step.is_active ? <Badge>Off</Badge> : null}
                    {editable ? (
                      <span className="seq-step-tools">
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move step up" disabled={busy || index === 0} onClick={() => void move(index, -1)}>
                          <ArrowUp size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move step down" disabled={busy || index === steps.length - 1} onClick={() => void move(index, 1)}>
                          <ArrowDown size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Preview step ${index + 1}`} title="Preview for a person" onClick={() => setPreviewing(step)}>
                          <Eye size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit step ${index + 1}`} onClick={() => setEditing(step)}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete step ${index + 1}`} onClick={() => setDeleting(step)}>
                          <Trash2 size={14} />
                        </button>
                      </span>
                    ) : (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Preview step ${index + 1}`} onClick={() => setPreviewing(step)}>
                        <Eye size={14} />
                      </button>
                    )}
                  </div>
                  <div className="seq-step-body">
                    {email ? (
                      <>
                        <div className="seq-step-subject">
                          {threaded ? (
                            <span className="ui-muted">
                              <CornerDownRight size={13} style={{ verticalAlign: -2 }} /> Reply in the same thread
                            </span>
                          ) : (
                            step.subject || <span className="ui-muted">(no subject)</span>
                          )}
                        </div>
                        <p className="seq-step-text">{step.body || <span className="ui-muted">No message yet.</span>}</p>
                      </>
                    ) : (
                      <p className="seq-step-text">
                        {step.note || <span className="ui-muted">{kind.hint}</span>}
                        {step.kind !== "auto_email" ? (
                          <>
                            {" "}
                            <Badge tone={PRIORITY_TONE[step.priority]}>{step.priority}</Badge>
                          </>
                        ) : null}
                      </p>
                    )}
                    {step.issues.length ? (
                      <ul className="seq-issues" aria-label="Deliverability warnings">
                        {step.issues.map((issue) => (
                          <li key={issue}>
                            <TriangleAlert size={13} aria-hidden /> {issue}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                  <div className="seq-step-stats">
                    {email && step.kind === "auto_email" ? (
                      <>
                        <span>
                          <strong>{st.sent ?? 0}</strong> sent
                        </span>
                        <span>
                          <strong>{st.opened ?? 0}</strong> opened
                        </span>
                        <span>
                          <strong>{st.clicked ?? 0}</strong> clicked
                        </span>
                        <span>
                          <strong>{st.replied ?? 0}</strong> replied
                        </span>
                        <span className={(st.bounced ?? 0) > 0 ? "seq-bad" : undefined}>
                          <strong>{st.bounced ?? 0}</strong> bounced
                        </span>
                      </>
                    ) : (
                      <>
                        <span>
                          <strong>{st.tasks ?? 0}</strong> tasks created
                        </span>
                        {step.kind === "manual_email" ? (
                          <>
                            <span>
                              <strong>{st.sent ?? 0}</strong> sent
                            </span>
                            <span>
                              <strong>{st.replied ?? 0}</strong> replied
                            </span>
                          </>
                        ) : null}
                      </>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {steps.length && editable ? (
        <button type="button" className="ui-btn seq-add-step" onClick={() => setEditing("new")}>
          <Plus size={16} /> Add a step
        </button>
      ) : null}

      <StepModal
        sequence={sequence}
        step={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          onChanged();
        }}
      />
      <PreviewModal sequence={sequence} step={previewing} onClose={() => setPreviewing(null)} />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete step?"
        message="Its stats go with it. If people are in the sequence, turn the step off instead."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

const KIND_ORDER: StepKind[] = ["auto_email", "manual_email", "call", "linkedin", "task"];

function StepModal({ sequence, step, onClose, onSaved }: { sequence: SequenceDetail; step: SequenceStep | "new" | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState<StepKind>("auto_email");
  const [days, setDays] = useState(0);
  const [hours, setHours] = useState(0);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [note, setNote] = useState("");
  const [priority, setPriority] = useState<SequenceStep["priority"]>("medium");
  const [threadReply, setThreadReply] = useState(true);
  const [active, setActive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const existing = step && step !== "new" ? step : null;
  const position = existing ? existing.position : sequence.steps.length;
  const first = position === 0;

  useEffect(() => {
    if (!step) return;
    setKind(existing?.kind ?? "auto_email");
    setDays(existing?.delay_days ?? (sequence.steps.length ? 3 : 0));
    setHours(existing?.delay_hours ?? 0);
    setSubject(existing?.subject ?? "");
    setBody(existing?.body ?? "");
    setNote(existing?.note ?? "");
    setPriority(existing?.priority ?? "medium");
    setThreadReply(existing?.thread_reply ?? true);
    setActive(existing?.is_active ?? true);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const email = kind === "auto_email" || kind === "manual_email";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = {
      kind,
      delay_days: days,
      delay_hours: hours,
      subject: email ? subject || null : null,
      body: email ? body || null : null,
      note: note || null,
      priority,
      thread_reply: email ? threadReply : true,
      is_active: active,
    };
    try {
      if (existing) await api.put(`/sequences/${sequence.id}/steps/${existing.id}`, payload);
      else await api.post(`/sequences/${sequence.id}/steps`, payload);
      toast(existing ? "Step saved." : "Step added.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={Boolean(step)}
      title={existing ? `Edit step ${position + 1}` : `Add step ${position + 1}`}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="sequence-step" className="ui-btn ui-btn-primary" disabled={saving}>
            {saving ? <span className="ui-spinner" /> : null} {existing ? "Save step" : "Add step"}
          </button>
        </>
      }
    >
      <form id="sequence-step" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div role="radiogroup" aria-label="Step type" className="seq-kind-pick">
          {KIND_ORDER.map((key) => {
            const item = STEP_KIND[key];
            const Icon = item.icon;
            return (
              <button key={key} type="button" role="radio" aria-checked={kind === key} className={`seq-kind-option${kind === key ? " on" : ""}`} onClick={() => setKind(key)}>
                <Icon size={16} aria-hidden />
                <span>
                  <strong>{item.label}</strong>
                  <span className="ui-muted ui-small">{item.hint}</span>
                </span>
              </button>
            );
          })}
        </div>

        <div className="seq-delay">
          <span className="seq-delay-label">{first ? "Run" : "Wait"}</span>
          <NumberInput label="Days" value={days} min={0} max={365} width={80} onChange={(value) => setDays(Math.max(0, Math.min(365, value ?? 0)))} suffix="days" />
          <NumberInput label="Hours" value={hours} min={0} max={23} width={80} onChange={(value) => setHours(Math.max(0, Math.min(23, value ?? 0)))} suffix="hours" />
          <span className="ui-muted ui-small">{first ? "after someone is added" : "after the previous step"}</span>
        </div>

        {email ? (
          <>
            {!first ? (
              <div className="seq-switch-row">
                <Toggle label="Reply in the same thread" checked={threadReply} onChange={setThreadReply} />
                <span>
                  <strong>Reply in the same thread</strong>
                  <span className="ui-muted ui-small">Leave the subject empty to send this as “Re: …” under the first email.</span>
                </span>
              </div>
            ) : null}
            <EmailEditor subject={subject} body={body} onSubject={setSubject} onBody={setBody} />
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <SnippetPicker onInsert={(text) => setBody((value) => (value ? `${value.replace(/\s*$/, "")}\n\n${text}` : text))} />
              <span className="ui-muted ui-small">Snippets keep their merge fields, so they fill in for each person.</span>
            </div>
          </>
        ) : null}
        {kind !== "auto_email" ? (
          <div className="ui-form-grid">
            <Field label={email ? "Note for the task" : "Instructions"} className="span-2" hint={kind === "call" ? "Talking points. The person's phone number is added to the task." : undefined}>
              <textarea className="ui-textarea" rows={4} value={note} maxLength={5000} onChange={(event) => setNote(event.target.value)} />
            </Field>
            <div className="span-2">
              <SnippetPicker onInsert={(text) => setNote((value) => (value ? `${value.replace(/\s*$/, "")}\n${text}` : text))} />
            </div>
            <Field label="Task priority">
              <select className="ui-select" value={priority} onChange={(event) => setPriority(event.target.value as SequenceStep["priority"])}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </Field>
          </div>
        ) : null}
        {existing ? (
          <div className="seq-switch-row">
            <Toggle label="Step is on" checked={active} onChange={setActive} />
            <span>
              <strong>Step is on</strong>
              <span className="ui-muted ui-small">Turned-off steps are skipped for everyone.</span>
            </span>
          </div>
        ) : null}
      </form>
    </Modal>
  );
}

function PreviewModal({ sequence, step, onClose }: { sequence: SequenceDetail; step: SequenceStep | null; onClose: () => void }) {
  const [record, setRecord] = useState<FoundRecord | null>(null);
  const [result, setResult] = useState<{ subject: string; body: string; issues: string[]; to: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!step) return;
    setRecord(null);
    setResult(null);
    setError(null);
  }, [step]);

  async function pick(found: FoundRecord) {
    if (!step) return;
    setRecord(found);
    setLoading(true);
    setError(null);
    try {
      setResult(await api.post(`/sequences/${sequence.id}/steps/${step.id}/preview`, { record_type: found.type, record_id: found.id }));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  const email = step ? step.kind === "auto_email" || step.kind === "manual_email" : false;

  return (
    <Modal open={Boolean(step)} title={step ? `Preview step ${step.position + 1}` : "Preview"} description="See exactly what one person would get, with their merge fields filled in." onClose={onClose} size="lg">
      <div className="ui-stack" style={{ gap: 14 }}>
        {!record ? (
          <RecordSearch types={["contact", "lead"]} placeholder="Search contacts and leads" onPick={(found) => void pick(found)} />
        ) : (
          <div className="ui-row" style={{ gap: 8 }}>
            <span>
              For <strong>{record.title}</strong>
              {result?.to ? <span className="ui-muted"> · {result.to}</span> : null}
            </span>
            <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => setRecord(null)}>
              <X size={13} /> Pick someone else
            </button>
          </div>
        )}
        {error ? <Alert>{error}</Alert> : null}
        {loading ? <LoadingRows rows={3} /> : null}
        {result && !loading ? (
          <div className="seq-preview">
            {email ? <div className="seq-preview-subject">{result.subject || <span className="ui-muted">(no subject — sent as a reply)</span>}</div> : null}
            <div className="seq-preview-body">{result.body || <span className="ui-muted">Nothing to show.</span>}</div>
            {email && !result.to ? <Alert tone="warning">This person has no email address, so they'd be skipped.</Alert> : null}
            {result.issues.length ? (
              <ul className="seq-issues">
                {result.issues.map((issue) => (
                  <li key={issue}>
                    <TriangleAlert size={13} aria-hidden /> {issue}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

// ======================================================================= People

const PEOPLE_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "Everyone" },
  { value: "active", label: "In progress or paused" },
  { value: "paused", label: "Paused" },
  { value: "replied", label: "Replied" },
  { value: "meeting_booked", label: "Meeting booked" },
  { value: "finished", label: "Finished" },
  { value: "bounced", label: "Bounced" },
  { value: "unsubscribed", label: "Unsubscribed" },
  { value: "removed", label: "Removed" },
  { value: "failed", label: "Couldn't send" },
];

type Action = "pause" | "resume" | "remove" | "finish" | "skip_wait";

function PeopleTab({ sequence, onAdd, onChanged }: { sequence: SequenceDetail; onAdd: () => void; onChanged: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const [status, setStatus] = useState("");
  const [term, setTerm] = useState("");
  const debounced = useDebounced(term.trim(), 250);
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (debounced) params.set("search", debounced);
  const list = useApi<{ items: Enrollment[] }>(`/sequences/${sequence.id}/enrollments${params.toString() ? `?${params}` : ""}`);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ enrollment: Enrollment; action: "remove" | "finish" } | null>(null);
  const [sending, setSending] = useState<Enrollment | null>(null);
  const manage = can("sequences.manage");
  const activeSteps = sequence.steps.filter((step) => step.is_active);
  const items = list.data?.items ?? [];

  async function act(enrollment: Enrollment, action: Action) {
    setBusy(enrollment.id);
    try {
      await api.post(`/sequence-enrollments/${enrollment.id}/action`, { action });
      const messages: Record<Action, string> = {
        pause: `Paused for ${enrollment.name}.`,
        resume: `Resumed for ${enrollment.name}.`,
        remove: `${enrollment.name} was removed.`,
        finish: `${enrollment.name} is marked finished.`,
        skip_wait: enrollment.waiting_on_task ? "Step marked done. Moving on to the next one." : "Wait skipped. Emails still go out inside the sending window.",
      };
      toast(messages[action]);
      setConfirming(null);
      await list.reload();
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="ui-toolbar">
        <select className="ui-select" aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value)}>
          {PEOPLE_FILTERS.map((filter) => (
            <option key={filter.value} value={filter.value}>
              {filter.label}
            </option>
          ))}
        </select>
        <div className="ui-search">
          <Search size={16} />
          <input className="ui-input" placeholder="Search by name or email" aria-label="Search people" value={term} onChange={(event) => setTerm(event.target.value)} />
        </div>
        <span className="ui-spacer" />
        {manage ? (
          <button
            type="button"
            className="ui-btn"
            onClick={onAdd}
            disabled={sequence.status !== "active"}
            title={sequence.status !== "active" ? "Turn the sequence on to add people" : undefined}
          >
            <UserPlus size={15} /> Add people
          </button>
        ) : null}
      </div>
      {list.error ? <Alert>{list.error}</Alert> : null}
      <Card flush>
        {list.loading && !list.data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Status</th>
                  <th>Step</th>
                  <th>Next</th>
                  <th>Sender</th>
                  <th>Added</th>
                  {manage ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {items.map((enrollment) => {
                  const state = ENROLLMENT_STATUS[enrollment.status] ?? { label: enrollment.status, tone: "neutral" as const };
                  const current = activeSteps[enrollment.step - 1];
                  const running = isRunning(enrollment.status);
                  const canSend = enrollment.status === "active" && enrollment.waiting_on_task && current?.kind === "manual_email";
                  const disabled = busy === enrollment.id;
                  return (
                    <tr key={enrollment.id}>
                      <td style={{ minWidth: 200 }}>
                        <Link href={enrollment.href} className="cell-title">
                          {enrollment.name}
                        </Link>
                        <div className="ui-muted ui-small">{enrollment.email ?? "No email"}</div>
                      </td>
                      <td>
                        <Badge tone={state.tone} dot>
                          {state.label}
                        </Badge>
                        {enrollment.error && running ? <div className="seq-error">{enrollment.error}</div> : null}
                        {!running && enrollment.finished_reason && enrollment.finished_reason !== enrollment.status ? (
                          <div className="ui-muted ui-small">{enrollment.finished_reason}</div>
                        ) : null}
                      </td>
                      <td className="ui-nowrap">
                        {enrollment.step} of {enrollment.steps}
                        {running && current ? <div className="ui-muted ui-small">{STEP_KIND[current.kind]?.label}</div> : null}
                      </td>
                      <td className="ui-small">{nextRunLabel(enrollment)}</td>
                      <td className="ui-nowrap">{enrollment.sender_name ?? "—"}</td>
                      <td className="ui-muted ui-small ui-nowrap">{relativeTime(enrollment.enrolled_at)}</td>
                      {manage ? (
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          {canSend ? (
                            <button type="button" className="ui-btn ui-btn-sm" disabled={disabled} onClick={() => setSending(enrollment)}>
                              <Send size={13} /> Send email
                            </button>
                          ) : null}
                          {enrollment.status === "active" ? (
                            <>
                              <button
                                type="button"
                                className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                                disabled={disabled}
                                aria-label={enrollment.waiting_on_task ? `Mark ${enrollment.name}'s current step done` : `Skip the wait for ${enrollment.name}`}
                                title={enrollment.waiting_on_task ? "Mark this step done and move on" : "Skip the wait (emails still respect the sending window)"}
                                onClick={() => void act(enrollment, "skip_wait")}
                              >
                                <SkipForward size={14} />
                              </button>
                              <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" disabled={disabled} aria-label={`Pause ${enrollment.name}`} title="Pause" onClick={() => void act(enrollment, "pause")}>
                                <Pause size={14} />
                              </button>
                            </>
                          ) : null}
                          {enrollment.status === "paused" ? (
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" disabled={disabled} aria-label={`Resume ${enrollment.name}`} title="Resume" onClick={() => void act(enrollment, "resume")}>
                              <Play size={14} />
                            </button>
                          ) : null}
                          {running ? (
                            <>
                              <button
                                type="button"
                                className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                                disabled={disabled}
                                aria-label={`Mark ${enrollment.name} finished`}
                                title="Mark finished"
                                onClick={() => setConfirming({ enrollment, action: "finish" })}
                              >
                                <Flag size={14} />
                              </button>
                              <button
                                type="button"
                                className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                                disabled={disabled}
                                aria-label={`Remove ${enrollment.name}`}
                                title="Remove from sequence"
                                onClick={() => setConfirming({ enrollment, action: "remove" })}
                              >
                                <UserMinus size={14} />
                              </button>
                            </>
                          ) : null}
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<UserPlus size={20} />}
            title={status || debounced ? "No one matches" : "No one in this sequence yet"}
            message={
              status || debounced
                ? "Try another filter."
                : sequence.status === "active"
                  ? "Add people here, or use “Add to sequence” on a lead or contact page."
                  : "Turn the sequence on, then add leads or contacts."
            }
            action={
              manage && sequence.status === "active" && !status && !debounced ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={onAdd}>
                  <UserPlus size={15} /> Add people
                </button>
              ) : null
            }
          />
        )}
      </Card>

      <ConfirmDialog
        open={Boolean(confirming)}
        title={confirming?.action === "finish" ? "Mark as finished?" : "Remove from sequence?"}
        message={
          confirming?.action === "finish"
            ? `No more steps run for ${confirming.enrollment.name}, and they count as having finished the sequence.`
            : `No more steps run for ${confirming?.enrollment.name ?? "this person"}, and their open sequence tasks are cancelled.`
        }
        confirmLabel={confirming?.action === "finish" ? "Mark finished" : "Remove"}
        danger={confirming?.action !== "finish"}
        busy={busy === confirming?.enrollment.id}
        onConfirm={() => confirming && void act(confirming.enrollment, confirming.action)}
        onClose={() => setConfirming(null)}
      />
      <SendNowModal
        sequence={sequence}
        enrollment={sending}
        step={sending ? activeSteps[sending.step - 1] ?? null : null}
        onClose={() => setSending(null)}
        onSent={() => {
          setSending(null);
          void list.reload();
          onChanged();
        }}
      />
    </>
  );
}

/** Send the current manual-email step now, after reviewing or editing it. */
function SendNowModal({
  sequence,
  enrollment,
  step,
  onClose,
  onSent,
}: {
  sequence: SequenceDetail;
  enrollment: Enrollment | null;
  step: SequenceStep | null;
  onClose: () => void;
  onSent: () => void;
}) {
  const toast = useToast();
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enrollment || !step) return;
    let active = true;
    setLoading(true);
    setError(null);
    setSubject("");
    setBody("");
    api
      .post<{ subject: string; body: string }>(`/sequences/${sequence.id}/steps/${step.id}/preview`, { record_type: enrollment.record_type, record_id: enrollment.record_id })
      .then((result) => {
        if (!active) return;
        setSubject(result.subject);
        setBody(result.body);
      })
      .catch((caught) => active && setError(errorMessage(caught)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [enrollment, step, sequence.id]);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!enrollment) return;
    setSending(true);
    setError(null);
    try {
      const result = await api.post<{ sent: boolean; delivery: string }>(`/sequence-enrollments/${enrollment.id}/send`, { subject: subject || null, body: body || null });
      toast(result.delivery === "outbox" ? "Saved to the outbox (no mail server is connected)." : `Email sent to ${enrollment.name}.`);
      onSent();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSending(false);
    }
  }

  return (
    <Modal
      open={Boolean(enrollment)}
      title={`Email ${enrollment?.name ?? ""}`}
      description={enrollment?.email ? `To ${enrollment.email}. Edit anything, then send. The sequence moves on to the next step.` : undefined}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="sequence-send" className="ui-btn ui-btn-primary" disabled={sending || loading || !body.trim()}>
            {sending ? <span className="ui-spinner" /> : <Send size={15} />} Send now
          </button>
        </>
      }
    >
      <form id="sequence-send" className="ui-stack" style={{ gap: 14 }} onSubmit={(event) => void send(event)}>
        {error ? <Alert>{error}</Alert> : null}
        {loading ? (
          <LoadingRows rows={4} />
        ) : (
          <>
            <Field label="Subject" hint="Leave it as is to reply in the same thread.">
              <input className="ui-input" value={subject} maxLength={300} onChange={(event) => setSubject(event.target.value)} />
            </Field>
            <Field label="Message" required>
              <textarea className="ui-textarea" rows={10} value={body} maxLength={20000} onChange={(event) => setBody(event.target.value)} />
            </Field>
            <div>
              <SnippetPicker
                recordType={enrollment?.record_type}
                recordId={enrollment?.record_id}
                onInsert={(text) => setBody((value) => (value ? `${value.replace(/\s*$/, "")}\n\n${text}` : text))}
              />
            </div>
          </>
        )}
      </form>
    </Modal>
  );
}

function AddPeopleModal({ open, sequence, onClose, onDone }: { open: boolean; sequence: SequenceDetail; onClose: () => void; onDone: () => void }) {
  const { session } = useSession();
  const [picked, setPicked] = useState<FoundRecord[]>([]);
  const [sender, setSender] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EnrollResult | null>(null);

  useEffect(() => {
    if (!open) return;
    setPicked([]);
    setSender(session?.user.id ?? "");
    setError(null);
    setResult(null);
  }, [open, session?.user.id]);

  function add(found: FoundRecord) {
    setPicked((current) => (current.some((item) => item.id === found.id) ? current : [...current, found]));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    const merged: EnrollResult = { enrolled: [], skipped: [] };
    try {
      for (const type of ["contact", "lead"] as const) {
        const ids = picked.filter((item) => item.type === type).map((item) => item.id);
        if (!ids.length) continue;
        const body: Record<string, unknown> = { record_type: type, record_ids: ids };
        if (sender && sender !== session?.user.id) body.sender_user_id = sender;
        const outcome = await api.post<EnrollResult>(`/sequences/${sequence.id}/enroll`, body);
        merged.enrolled.push(...outcome.enrolled);
        merged.skipped.push(...outcome.skipped);
      }
      setResult(merged);
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={result ? "People added" : `Add people to ${sequence.name}`}
      description={result ? undefined : "Search for leads and contacts. You can also add people from list pages and record pages."}
      onClose={onClose}
      footer={
        result ? (
          <button type="button" className="ui-btn ui-btn-primary" onClick={onClose}>
            Done
          </button>
        ) : (
          <>
            <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !picked.length} onClick={() => void submit()}>
              {busy ? <span className="ui-spinner" /> : null} Add {picked.length || ""} {picked.length === 1 ? "person" : "people"}
            </button>
          </>
        )
      }
    >
      {result ? (
        <EnrollResults result={result} />
      ) : (
        <div className="ui-stack" style={{ gap: 14 }}>
          {error ? <Alert>{error}</Alert> : null}
          <RecordSearch types={["contact", "lead"]} placeholder="Search contacts and leads" onPick={add} />
          {picked.length ? (
            <ul className="seq-picked" aria-label="People to add">
              {picked.map((item) => (
                <li key={item.id}>
                  <span className="ui-truncate">
                    <strong>{item.title}</strong>
                    <span className="ui-muted ui-small"> · {item.type === "lead" ? "Lead" : "Contact"}</span>
                  </span>
                  <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Don't add ${item.title}`} onClick={() => setPicked((current) => current.filter((p) => p.id !== item.id))}>
                    <X size={14} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              No one picked yet.
            </p>
          )}
          <SenderSelect value={sender} onChange={setSender} />
        </div>
      )}
    </Modal>
  );
}

// ======================================================================= Settings

function SettingsTab({ sequence, setSequence }: { sequence: SequenceDetail; setSequence: (value: SequenceDetail) => void }) {
  const toast = useToast();
  const [name, setName] = useState(sequence.name);
  const [description, setDescription] = useState(sequence.description ?? "");
  const [shared, setShared] = useState(sequence.is_shared);
  const [stopOnReply, setStopOnReply] = useState(sequence.settings.stop_on_reply);
  const [stopOnMeeting, setStopOnMeeting] = useState(sequence.settings.stop_on_meeting);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editable = sequence.can_edit;
  const dirty =
    name !== sequence.name ||
    description !== (sequence.description ?? "") ||
    shared !== sequence.is_shared ||
    stopOnReply !== sequence.settings.stop_on_reply ||
    stopOnMeeting !== sequence.settings.stop_on_meeting;
  const schedule = sequence.schedule;
  const days = [...schedule.days].sort().map((d) => WEEKDAYS[d]).join(", ");
  const defaults = sequence.defaults;

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const updated = await api.put<SequenceDetail>(`/sequences/${sequence.id}`, {
        name,
        description: description || null,
        is_shared: shared,
        settings: { stop_on_reply: stopOnReply, stop_on_meeting: stopOnMeeting },
      });
      setSequence(updated);
      toast("Sequence settings saved.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="seq-settings">
      <Card title="About this sequence">
        <form className="ui-stack" style={{ gap: 14 }} onSubmit={(event) => void save(event)}>
          {error ? <Alert>{error}</Alert> : null}
          <Field label="Name" required>
            <input className="ui-input" value={name} maxLength={200} disabled={!editable} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Description">
            <textarea className="ui-textarea" rows={3} value={description} maxLength={5000} disabled={!editable} onChange={(event) => setDescription(event.target.value)} />
          </Field>
          <div className="seq-switches">
            <div className="seq-switch-row">
              <Toggle label="Share with the team" checked={shared} disabled={!editable} onChange={setShared} />
              <span>
                <strong>Share with the team</strong>
                <span className="ui-muted ui-small">Teammates can see it and add their own people.</span>
              </span>
            </div>
            <div className="seq-switch-row">
              <Toggle label="Stop when they reply" checked={stopOnReply} disabled={!editable} onChange={setStopOnReply} />
              <span>
                <strong>Stop when they reply</strong>
                <span className="ui-muted ui-small">Replies are picked up from the sender&apos;s connected mailbox.</span>
              </span>
            </div>
            <div className="seq-switch-row">
              <Toggle label="Stop when a meeting is booked" checked={stopOnMeeting} disabled={!editable} onChange={setStopOnMeeting} />
              <span>
                <strong>Stop when a meeting is booked</strong>
                <span className="ui-muted ui-small">Any meeting in AIVA with them as an attendee.</span>
              </span>
            </div>
          </div>
          {editable ? (
            <div>
              <button type="submit" className="ui-btn ui-btn-primary" disabled={saving || !dirty || !name.trim()}>
                {saving ? <span className="ui-spinner" /> : null} Save changes
              </button>
            </div>
          ) : null}
        </form>
      </Card>

      <div className="ui-stack" style={{ gap: 18 }}>
        <Card title="When emails go out" actions={<Link href="/settings/outbound/protection" className="ui-btn ui-btn-sm ui-btn-ghost">Change</Link>}>
          <dl className="seq-facts">
            <dt>Days</dt>
            <dd>{days || "None"}</dd>
            <dt>Hours</dt>
            <dd>
              {hourLabel(schedule.start)} – {schedule.end === 24 ? "midnight" : hourLabel(schedule.end)}
            </dd>
            <dt>Time zone</dt>
            <dd>{schedule.timezone.replace(/_/g, " ")}</dd>
          </dl>
          <p className="ui-muted ui-small" style={{ margin: "10px 0 0" }}>
            The sending window, daily limits and bounce protection apply to every sequence in the workspace.
          </p>
        </Card>
        <Card title="Workspace rules" actions={<Link href="/settings/outbound/sequences" className="ui-btn ui-btn-sm ui-btn-ghost">Change</Link>}>
          <ul className="seq-rules">
            <li>{defaults.one_active_sequence ? "A person can be in one sequence at a time." : "A person can be in several sequences at once."}</li>
            <li>{defaults.allow_reenroll ? "People can be added again after they finish." : "People who were in a sequence can't be added to it again."}</li>
            <li>{defaults.thread_follow_ups ? "Follow-up emails reply in the same thread." : "Every email starts a new thread."}</li>
            <li>{defaults.stop_on_bounce ? "A bounce stops the sequence for that person." : "Bounces don't stop the sequence."}</li>
            <li>
              Tasks from manual steps are due {defaults.task_due_hours} hour{defaults.task_due_hours === 1 ? "" : "s"} after they&apos;re created.
            </li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
