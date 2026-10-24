"use client";

import Link from "next/link";
import {
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Linkedin,
  Mail,
  MailPlus,
  Pause,
  Phone,
  Play,
  Plus,
  SquareStack,
  UserMinus,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { useMembers } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, type Tone } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

// ======================================================================= Types

export type SequenceStatus = "draft" | "active" | "paused" | "archived";
export type StepKind = "auto_email" | "manual_email" | "call" | "linkedin" | "task";

export interface SequenceRates {
  opened: number;
  clicked: number;
  replied: number;
  bounced: number;
}

export interface SequenceStats {
  enrollments: Record<string, number>;
  total: number;
  active: number;
  sent: number;
  opened: number;
  clicked: number;
  replied: number;
  bounced: number;
  rates: SequenceRates;
}

export interface SequenceSummary {
  id: string;
  name: string;
  description: string | null;
  status: SequenceStatus;
  is_shared: boolean;
  owner_user_id: string | null;
  owner_name: string | null;
  settings: { stop_on_reply: boolean; stop_on_meeting: boolean };
  stats: Partial<SequenceStats>;
  step_count: number | null;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
}

export interface SequenceStep {
  id: string;
  position: number;
  kind: StepKind;
  kind_label: string;
  delay_days: number;
  delay_hours: number;
  subject: string | null;
  body: string | null;
  note: string | null;
  priority: "low" | "medium" | "high" | "urgent";
  thread_reply: boolean;
  is_active: boolean;
  issues: string[];
  stats: Partial<{ sent: number; opened: number; clicked: number; replied: number; bounced: number; tasks: number }>;
  waiting: number;
}

export interface SequenceDetail extends SequenceSummary {
  steps: SequenceStep[];
  defaults: {
    stop_on_reply: boolean;
    stop_on_meeting: boolean;
    stop_on_bounce: boolean;
    one_active_sequence: boolean;
    allow_reenroll: boolean;
    thread_follow_ups: boolean;
    task_due_hours: number;
  };
  schedule: { days: number[]; start: number; end: number; timezone: string };
  step_kinds: { key: StepKind; label: string }[];
}

export type EnrollmentStatus = "active" | "paused" | "finished" | "replied" | "bounced" | "unsubscribed" | "meeting_booked" | "removed" | "failed";

export interface Enrollment {
  id: string;
  sequence_id: string;
  sequence_name: string | null;
  record_type: "lead" | "contact";
  record_id: string;
  href: string;
  name: string;
  email: string | null;
  status: EnrollmentStatus;
  step: number;
  steps: number;
  next_run_at: string | null;
  waiting_on_task: boolean;
  sender_name: string | null;
  enrolled_at: string;
  finished_at: string | null;
  finished_reason: string | null;
  error: string | null;
}

export interface EnrollmentHistoryItem {
  kind: StepKind;
  subject: string | null;
  status: "sent" | "failed" | "task_created" | "completed" | "skipped";
  sent_at: string;
  opened: boolean;
  clicked: boolean;
  replied: boolean;
  bounced: boolean;
  delivery: string | null;
}

export interface RecordEnrollment extends Enrollment {
  history: EnrollmentHistoryItem[];
}

export interface EnrollResult {
  enrolled: { id: string; name: string }[];
  skipped: { id: string; name: string; reason: string }[];
}

// ======================================================================= Labels

export const SEQUENCE_STATUS: Record<SequenceStatus, { label: string; tone: Tone }> = {
  active: { label: "Active", tone: "success" },
  paused: { label: "Paused", tone: "warning" },
  draft: { label: "Draft", tone: "neutral" },
  archived: { label: "Archived", tone: "neutral" },
};

export const ENROLLMENT_STATUS: Record<EnrollmentStatus, { label: string; tone: Tone }> = {
  active: { label: "In progress", tone: "info" },
  paused: { label: "Paused", tone: "warning" },
  finished: { label: "Finished", tone: "neutral" },
  replied: { label: "Replied", tone: "success" },
  meeting_booked: { label: "Meeting booked", tone: "success" },
  bounced: { label: "Bounced", tone: "danger" },
  unsubscribed: { label: "Unsubscribed", tone: "warning" },
  removed: { label: "Removed", tone: "neutral" },
  failed: { label: "Couldn't send", tone: "danger" },
};

export const STEP_KIND: Record<StepKind, { label: string; icon: LucideIcon; hint: string }> = {
  auto_email: { label: "Automatic email", icon: Mail, hint: "Sent for you inside the sending window." },
  manual_email: { label: "Manual email", icon: MailPlus, hint: "A task to review, personalize and send yourself." },
  call: { label: "Phone call", icon: Phone, hint: "A call task with the person's number." },
  linkedin: { label: "LinkedIn", icon: Linkedin, hint: "A task to connect or message on LinkedIn." },
  task: { label: "Task", icon: CheckSquare, hint: "Any other to-do, such as sending a gift." },
};

export const HISTORY_STATUS: Record<EnrollmentHistoryItem["status"], string> = {
  sent: "Sent",
  failed: "Failed",
  task_created: "Task created",
  completed: "Done",
  skipped: "Skipped",
};

export function isRunning(status: EnrollmentStatus) {
  return status === "active" || status === "paused";
}

/** When the next step happens, in words. */
export function nextRunLabel(enrollment: Pick<Enrollment, "status" | "next_run_at" | "waiting_on_task" | "finished_at">) {
  if (enrollment.status === "paused") return "Paused";
  if (!isRunning(enrollment.status)) return enrollment.finished_at ? `Ended ${relativeTime(enrollment.finished_at)}` : "Ended";
  if (enrollment.waiting_on_task) return "Waiting on a task";
  if (!enrollment.next_run_at) return "—";
  const at = new Date(enrollment.next_run_at);
  return at.getTime() <= Date.now() + 60_000 ? "Due now" : dateTime(enrollment.next_run_at);
}

// ======================================================================= Enroll results

export function EnrollResults({ result }: { result: EnrollResult }) {
  return (
    <div className="ui-stack" style={{ gap: 12 }}>
      {result.enrolled.length ? (
        <Alert tone="success">
          Added {result.enrolled.length} {result.enrolled.length === 1 ? "person" : "people"}. The first step runs on schedule.
        </Alert>
      ) : (
        <Alert tone="warning">No one was added.</Alert>
      )}
      {result.skipped.length ? (
        <div>
          <div className="ui-muted ui-small" style={{ marginBottom: 6 }}>
            Skipped {result.skipped.length}:
          </div>
          <ul className="seq-skipped">
            {result.skipped.map((item) => (
              <li key={item.id}>
                <strong>{item.name}</strong>
                <span>{item.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** Sender choice: outbound admins can send from someone else's mailbox. */
export function SenderSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { session, can } = useSession();
  const { members } = useMembers();
  if (!can("outbound.manage")) return null;
  const me = session?.user.id ?? "";
  return (
    <Field label="Send from" hint="Emails go out from this person's connected mailbox.">
      <select className="ui-select" value={value || me} onChange={(event) => onChange(event.target.value)}>
        {members
          .filter((member) => member.is_active)
          .map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.user_id === me ? `${member.full_name} (you)` : member.full_name} · {member.email}
            </option>
          ))}
      </select>
    </Field>
  );
}

// ======================================================================= Enroll modal

/** Pick an active sequence and add one or more leads or contacts to it. */
export function EnrollInSequenceModal({
  open,
  recordType,
  recordIds,
  onClose,
  onDone,
}: {
  open: boolean;
  recordType: "lead" | "contact";
  recordIds: string[];
  onClose: () => void;
  /** Called after people were added (or skipped); use it to refresh the page. */
  onDone?: (result: EnrollResult) => void;
}) {
  const { session } = useSession();
  const sequences = useApi<{ items: SequenceSummary[]; can_create: boolean }>(open ? "/sequences?status=active" : null);
  const [chosen, setChosen] = useState("");
  const [sender, setSender] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EnrollResult | null>(null);

  useEffect(() => {
    if (!open) return;
    setChosen("");
    setSender(session?.user.id ?? "");
    setError(null);
    setResult(null);
  }, [open, session?.user.id]);

  const items = sequences.data?.items ?? [];
  const count = recordIds.length;
  const noun = recordType === "lead" ? (count === 1 ? "lead" : "leads") : count === 1 ? "contact" : "contacts";

  async function submit() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { record_type: recordType, record_ids: recordIds };
      if (sender && sender !== session?.user.id) body.sender_user_id = sender;
      const outcome = await api.post<EnrollResult>(`/sequences/${chosen}/enroll`, body);
      setResult(outcome);
      onDone?.(outcome);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={result ? "Added to sequence" : "Add to sequence"}
      description={result ? undefined : `${count} ${noun} selected. Only sequences that are turned on are listed.`}
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
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void submit()} disabled={busy || !chosen || !count}>
              {busy ? <span className="ui-spinner" /> : null} Add {count > 1 ? `${count} ${noun}` : ""}
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
          {sequences.error ? <Alert>{sequences.error}</Alert> : null}
          {sequences.loading && !sequences.data ? (
            <LoadingRows rows={3} />
          ) : items.length ? (
            <div className="seq-pick" role="radiogroup" aria-label="Sequence">
              {items.map((sequence) => (
                <label key={sequence.id} className={`seq-pick-item${chosen === sequence.id ? " on" : ""}`}>
                  <input type="radio" name="sequence" value={sequence.id} checked={chosen === sequence.id} onChange={() => setChosen(sequence.id)} />
                  <span className="seq-pick-text">
                    <strong>{sequence.name}</strong>
                    <span className="ui-muted ui-small">
                      {sequence.step_count ?? 0} {sequence.step_count === 1 ? "step" : "steps"} · {sequence.stats.active ?? 0} active
                      {sequence.stats.sent ? ` · ${sequence.stats.rates?.replied ?? 0}% replied` : ""}
                      {sequence.owner_name ? ` · ${sequence.owner_name}` : ""}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<SquareStack size={20} />}
              title="No active sequences"
              message={
                <>
                  Build one and turn it on in <Link href="/sequences">Sequences</Link>.
                </>
              }
            />
          )}
          {items.length ? <SenderSelect value={sender} onChange={setSender} /> : null}
        </div>
      )}
    </Modal>
  );
}

// ======================================================================= Record card

/** Sequences card for a contact or lead page: where they are, what was sent, and quick actions. */
export function RecordSequences({ recordType, recordId }: { recordType: "lead" | "contact"; recordId: string }) {
  const { has, can } = useSession();
  const enabled = has("sequences");
  const toast = useToast();
  const data = useApi<{ items: RecordEnrollment[] }>(enabled ? `/sequences/for/${recordType}/${recordId}` : null);
  const [adding, setAdding] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [removing, setRemoving] = useState<RecordEnrollment | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!enabled) return null;
  const manage = can("sequences.manage");
  const items = data.data?.items ?? [];

  async function act(enrollment: RecordEnrollment, action: "pause" | "resume" | "remove") {
    setBusy(enrollment.id);
    try {
      await api.post(`/sequence-enrollments/${enrollment.id}/action`, { action });
      toast(action === "pause" ? "Paused for this person." : action === "resume" ? "Resumed." : "Removed from the sequence.");
      setRemoving(null);
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <Card
        title="Sequences"
        icon={<SquareStack size={16} />}
        actions={
          manage ? (
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => setAdding(true)}>
              <Plus size={14} /> Add to sequence
            </button>
          ) : null
        }
      >
        {data.error ? <Alert>{data.error}</Alert> : null}
        {data.loading && !data.data ? (
          <LoadingRows rows={2} />
        ) : items.length ? (
          <ul className="seq-record-list">
            {items.map((enrollment) => {
              const status = ENROLLMENT_STATUS[enrollment.status] ?? { label: enrollment.status, tone: "neutral" as Tone };
              const open = expanded === enrollment.id;
              return (
                <li key={enrollment.id} className="seq-record">
                  <div className="seq-record-top">
                    <button
                      type="button"
                      className="seq-record-toggle"
                      aria-expanded={open}
                      aria-label={open ? "Hide history" : "Show history"}
                      onClick={() => setExpanded(open ? null : enrollment.id)}
                    >
                      {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                    </button>
                    <div className="seq-record-main">
                      <Link href={`/sequences/${enrollment.sequence_id}`} className="seq-record-name">
                        {enrollment.sequence_name ?? "Sequence"}
                      </Link>
                      <div className="ui-muted ui-small">
                        Step {enrollment.step} of {enrollment.steps} · {nextRunLabel(enrollment)}
                        {enrollment.sender_name ? ` · from ${enrollment.sender_name}` : ""}
                      </div>
                      {enrollment.error && isRunning(enrollment.status) ? <div className="seq-error">{enrollment.error}</div> : null}
                      {!isRunning(enrollment.status) && enrollment.finished_reason ? (
                        <div className="ui-muted ui-small">{enrollment.finished_reason}</div>
                      ) : null}
                    </div>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                  {manage && isRunning(enrollment.status) ? (
                    <div className="seq-record-actions">
                      {enrollment.status === "active" ? (
                        <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy === enrollment.id} onClick={() => void act(enrollment, "pause")}>
                          <Pause size={13} /> Pause
                        </button>
                      ) : (
                        <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy === enrollment.id} onClick={() => void act(enrollment, "resume")}>
                          <Play size={13} /> Resume
                        </button>
                      )}
                      <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost ui-btn-danger" disabled={busy === enrollment.id} onClick={() => setRemoving(enrollment)}>
                        <UserMinus size={13} /> Remove
                      </button>
                    </div>
                  ) : null}
                  {open ? <History items={enrollment.history} /> : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Not in any sequence yet.
          </p>
        )}
      </Card>
      <EnrollInSequenceModal open={adding} recordType={recordType} recordIds={[recordId]} onClose={() => setAdding(false)} onDone={() => void data.reload()} />
      <ConfirmDialog
        open={Boolean(removing)}
        title="Remove from sequence?"
        message={`No more steps from “${removing?.sequence_name ?? "this sequence"}” will run for this person. Open tasks from it are cancelled.`}
        confirmLabel="Remove"
        busy={busy === removing?.id}
        onConfirm={() => removing && void act(removing, "remove")}
        onClose={() => setRemoving(null)}
      />
    </>
  );
}

function History({ items }: { items: EnrollmentHistoryItem[] }) {
  if (!items.length) return <p className="ui-muted ui-small seq-history-empty">Nothing has gone out yet.</p>;
  return (
    <ol className="seq-history">
      {items.map((item, index) => {
        const Icon = STEP_KIND[item.kind]?.icon ?? Mail;
        return (
          <li key={`${item.sent_at}-${index}`}>
            <Icon size={14} aria-hidden />
            <div className="seq-history-text">
              <span className="ui-truncate">{item.subject || STEP_KIND[item.kind]?.label || item.kind}</span>
              <span className="ui-muted ui-small">
                {HISTORY_STATUS[item.status] ?? item.status} · {relativeTime(item.sent_at)}
                {item.delivery === "outbox" ? " · saved to outbox (no mail server)" : ""}
              </span>
            </div>
            <span className="seq-flags">
              {item.bounced ? <Badge tone="danger">Bounced</Badge> : null}
              {item.replied ? <Badge tone="success">Replied</Badge> : null}
              {item.clicked ? <Badge tone="info">Clicked</Badge> : null}
              {item.opened && !item.clicked ? <Badge tone="neutral">Opened</Badge> : null}
              {item.status === "failed" ? <Badge tone="danger">Failed</Badge> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
