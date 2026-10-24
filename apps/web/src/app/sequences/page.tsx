"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, ArchiveRestore, Copy, Plus, Search, SquareStack, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { SEQUENCE_STATUS, type SequenceSummary } from "@/components/sequence-enroll";
import { Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function SequencesPage() {
  return (
    <FeaturePage feature="sequences">
      <Sequences />
    </FeaturePage>
  );
}

type StatusFilter = "" | "active" | "paused" | "draft" | "archived";

function rate(value: number | undefined) {
  return `${(value ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function Sequences() {
  const toast = useToast();
  const router = useRouter();
  const { can } = useSession();
  const [scope, setScope] = useState<"all" | "mine">("all");
  const [status, setStatus] = useState<StatusFilter>("");
  const [term, setTerm] = useState("");
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (scope === "mine") params.set("mine", "true");
  const list = useApi<{ items: SequenceSummary[]; can_create: boolean }>(`/sequences${params.toString() ? `?${params}` : ""}`);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<SequenceSummary | null>(null);
  const [archiving, setArchiving] = useState<SequenceSummary | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const all = list.data?.items ?? [];
  const lowered = term.trim().toLowerCase();
  const items = lowered ? all.filter((s) => `${s.name} ${s.description ?? ""} ${s.owner_name ?? ""}`.toLowerCase().includes(lowered)) : all;
  const canCreate = list.data?.can_create ?? can("sequences.manage");

  const totals = all.reduce(
    (acc, s) => {
      acc.active += s.status === "active" ? 1 : 0;
      acc.people += s.stats.active ?? 0;
      acc.sent += s.stats.sent ?? 0;
      acc.replied += s.stats.replied ?? 0;
      return acc;
    },
    { active: 0, people: 0, sent: 0, replied: 0 },
  );

  async function setSequenceStatus(sequence: SequenceSummary, next: "active" | "paused" | "archived" | "draft", message: string) {
    setBusy(sequence.id);
    try {
      await api.post(`/sequences/${sequence.id}/status`, { status: next });
      toast(message);
      setArchiving(null);
      await list.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function duplicate(sequence: SequenceSummary) {
    setBusy(sequence.id);
    try {
      const copy = await api.post<{ id: string }>(`/sequences/${sequence.id}/duplicate`);
      toast("Copy created. It starts turned off.");
      router.push(`/sequences/${copy.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(null);
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(deleting.id);
    try {
      await api.del(`/sequences/${deleting.id}`);
      toast("Sequence deleted.");
      setDeleting(null);
      await list.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Sequences"
        description="Multi-step outreach: automatic emails go out on schedule, and calls, LinkedIn touches and manual emails land on your task list. People leave the sequence when they reply or book a meeting."
        actions={
          canCreate ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New sequence
            </button>
          ) : null
        }
      />

      <div className="ui-grid ui-grid-4" style={{ marginBottom: 18 }}>
        <Kpi label="Active sequences" value={totals.active} />
        <Kpi label="People in progress" value={totals.people.toLocaleString()} />
        <Kpi label="Emails sent" value={totals.sent.toLocaleString()} />
        <Kpi label="Reply rate" value={totals.sent ? rate((totals.replied / totals.sent) * 100) : "—"} note={totals.replied ? `${totals.replied} ${totals.replied === 1 ? "reply" : "replies"}` : undefined} />
      </div>

      <div className="ui-toolbar">
        <Tabs
          label="Whose sequences"
          value={scope}
          onChange={setScope}
          items={[
            { value: "all", label: "All sequences" },
            { value: "mine", label: "Mine" },
          ]}
        />
        <select className="ui-select" aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)}>
          <option value="">Not archived</option>
          <option value="active">Active</option>
          <option value="paused">Paused</option>
          <option value="draft">Draft</option>
          <option value="archived">Archived</option>
        </select>
        <div className="ui-search">
          <Search size={16} />
          <input className="ui-input" placeholder="Search sequences" aria-label="Search sequences" value={term} onChange={(event) => setTerm(event.target.value)} />
        </div>
      </div>

      {list.error ? <Alert>{list.error}</Alert> : null}
      <Card flush>
        {list.loading && !list.data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table seq-table">
              <thead>
                <tr>
                  <th style={{ width: 54 }}>
                    <span className="seq-sr-only">On or off</span>
                  </th>
                  <th>Sequence</th>
                  <th className="ui-num">Steps</th>
                  <th className="ui-num">Active</th>
                  <th className="ui-num">Sent</th>
                  <th className="ui-num">Opened</th>
                  <th className="ui-num">Clicked</th>
                  <th className="ui-num">Replied</th>
                  <th className="ui-num">Bounced</th>
                  <th>Owner</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {items.map((sequence) => {
                  const info = SEQUENCE_STATUS[sequence.status];
                  const rates = sequence.stats.rates;
                  const archived = sequence.status === "archived";
                  return (
                    <tr key={sequence.id}>
                      <td>
                        {sequence.can_edit && !archived ? (
                          <Toggle
                            label={sequence.status === "active" ? `Pause ${sequence.name}` : `Turn on ${sequence.name}`}
                            checked={sequence.status === "active"}
                            disabled={busy === sequence.id}
                            onChange={(on) =>
                              void setSequenceStatus(sequence, on ? "active" : "paused", on ? `${sequence.name} is on.` : `${sequence.name} is paused. No steps run until you turn it back on.`)
                            }
                          />
                        ) : null}
                      </td>
                      <td style={{ minWidth: 220 }}>
                        <Link href={`/sequences/${sequence.id}`} className="cell-title">
                          {sequence.name}
                        </Link>
                        <div className="ui-row ui-small" style={{ gap: 6, marginTop: 3 }}>
                          <Badge tone={info.tone} dot>
                            {info.label}
                          </Badge>
                          {!sequence.is_shared ? <span className="ui-muted">Private</span> : null}
                          <span className="ui-muted">Updated {relativeTime(sequence.updated_at)}</span>
                        </div>
                      </td>
                      <td className="ui-num">{sequence.step_count ?? 0}</td>
                      <td className="ui-num">
                        {sequence.stats.active ?? 0}
                        <span className="ui-muted"> / {sequence.stats.total ?? 0}</span>
                      </td>
                      <td className="ui-num">{(sequence.stats.sent ?? 0).toLocaleString()}</td>
                      <td className="ui-num">{sequence.stats.sent ? rate(rates?.opened) : "—"}</td>
                      <td className="ui-num">{sequence.stats.sent ? rate(rates?.clicked) : "—"}</td>
                      <td className="ui-num seq-good">{sequence.stats.sent ? rate(rates?.replied) : "—"}</td>
                      <td className={`ui-num${(rates?.bounced ?? 0) >= 5 ? " seq-bad" : ""}`}>{sequence.stats.sent ? rate(rates?.bounced) : "—"}</td>
                      <td className="ui-nowrap">{sequence.owner_name ?? "—"}</td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {canCreate ? (
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Duplicate ${sequence.name}`} title="Duplicate" disabled={busy === sequence.id} onClick={() => void duplicate(sequence)}>
                            <Copy size={14} />
                          </button>
                        ) : null}
                        {sequence.can_edit ? (
                          archived ? (
                            <button
                              type="button"
                              className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                              aria-label={`Restore ${sequence.name}`}
                              title="Restore"
                              disabled={busy === sequence.id}
                              onClick={() => void setSequenceStatus(sequence, "paused", "Sequence restored. It's paused until you turn it on.")}
                            >
                              <ArchiveRestore size={14} />
                            </button>
                          ) : (
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Archive ${sequence.name}`} title="Archive" onClick={() => setArchiving(sequence)}>
                              <Archive size={14} />
                            </button>
                          )
                        ) : null}
                        {sequence.can_edit && !(sequence.stats.total ?? 0) ? (
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${sequence.name}`} title="Delete" onClick={() => setDeleting(sequence)}>
                            <Trash2 size={14} />
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<SquareStack size={20} />}
            title={lowered || status || scope === "mine" ? "No sequences match" : "No sequences yet"}
            message={
              lowered || status || scope === "mine"
                ? "Try another filter."
                : "A sequence is a series of emails, calls and tasks spread over days. Build one, turn it on, then add leads or contacts from their pages."
            }
            action={
              canCreate && !lowered && !status ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
                  <Plus size={16} /> New sequence
                </button>
              ) : null
            }
          />
        )}
      </Card>

      <NewSequenceModal open={creating} onClose={() => setCreating(false)} onCreated={(id) => router.push(`/sequences/${id}`)} />
      <ConfirmDialog
        open={Boolean(archiving)}
        title="Archive sequence?"
        message={`Everyone still in “${archiving?.name ?? ""}” is removed and their open sequence tasks are cancelled. Its history and stats are kept, and you can restore it later.`}
        confirmLabel="Archive"
        busy={busy === archiving?.id}
        onConfirm={() => archiving && void setSequenceStatus(archiving, "archived", "Sequence archived.")}
        onClose={() => setArchiving(null)}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete sequence?"
        message={`“${deleting?.name ?? ""}” and its steps will be deleted. This can't be undone.`}
        busy={busy === deleting?.id}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function NewSequenceModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(true);
  const [stopOnReply, setStopOnReply] = useState(true);
  const [stopOnMeeting, setStopOnMeeting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName("");
    setDescription("");
    setShared(true);
    setStopOnReply(true);
    setStopOnMeeting(true);
    setError(null);
  }, [open]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await api.post<SequenceSummary>("/sequences", {
        name,
        description: description || null,
        is_shared: shared,
        settings: { stop_on_reply: stopOnReply, stop_on_meeting: stopOnMeeting },
      });
      toast("Sequence created. Add its first step.");
      onCreated(created.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New sequence"
      description="You'll add steps next. Sequences start turned off."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="new-sequence" className="ui-btn ui-btn-primary" disabled={saving || !name.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Create sequence
          </button>
        </>
      }
    >
      <form id="new-sequence" className="ui-stack" style={{ gap: 14 }} onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Name" required>
          <input className="ui-input" value={name} maxLength={200} placeholder="Cold outreach: HVAC owners" onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Description" hint="Who it's for and what it's trying to do.">
          <textarea className="ui-textarea" rows={3} value={description} maxLength={5000} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        <div className="seq-switches">
          <div className="seq-switch-row">
            <Toggle label="Share with the team" checked={shared} onChange={setShared} />
            <span>
              <strong>Share with the team</strong>
              <span className="ui-muted ui-small">Teammates can see it and add their own people.</span>
            </span>
          </div>
          <div className="seq-switch-row">
            <Toggle label="Stop when they reply" checked={stopOnReply} onChange={setStopOnReply} />
            <span>
              <strong>Stop when they reply</strong>
              <span className="ui-muted ui-small">Replies are picked up from your connected mailbox.</span>
            </span>
          </div>
          <div className="seq-switch-row">
            <Toggle label="Stop when a meeting is booked" checked={stopOnMeeting} onChange={setStopOnMeeting} />
            <span>
              <strong>Stop when a meeting is booked</strong>
              <span className="ui-muted ui-small">Any meeting in AIVA with them as an attendee.</span>
            </span>
          </div>
        </div>
      </form>
    </Modal>
  );
}
