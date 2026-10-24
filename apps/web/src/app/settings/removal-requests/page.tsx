"use client";

import Link from "next/link";
import { Ban, Eraser, Plus, Search, ShieldOff, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs, type Tone } from "@/components/ui";
import { date, dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";

type Status = "pending" | "completed" | "rejected";

type RemovalRequest = {
  id: string;
  email: string;
  full_name: string | null;
  regulation: "gdpr" | "ccpa" | "other";
  source: string;
  status: Status;
  received_at: string;
  due_at: string;
  days_left: number | null;
  overdue: boolean;
  notes: string | null;
  summary: Record<string, number>;
  completed_at: string | null;
  completed_by: string | null;
  created_by: string | null;
  created_at: string;
  matches?: {
    leads: { id: string; label: string; href: string }[];
    contacts: { id: string; label: string; href: string }[];
    counts: Record<string, number>;
  };
};

type Suppression = { id: string; kind: "email" | "domain"; value: string; reason: string; note: string | null; created_at: string };

const REGULATIONS: Record<string, string> = { gdpr: "GDPR", ccpa: "CCPA", other: "Other" };
const SOURCES: Record<string, string> = { email: "Email", web_form: "Web form", phone: "Phone", letter: "Letter", other: "Other" };
const STATUS_TONE: Record<Status, Tone> = { pending: "warning", completed: "success", rejected: "neutral" };
const COUNT_LABELS: Record<string, string> = {
  leads: "Leads",
  contacts: "Contacts",
  activities: "Activities",
  website_visitors: "Website visitor profiles",
  email_messages: "Emails",
  campaign_sends: "Campaign sends",
  sequence_enrollments: "Sequence enrollments",
  meeting_invites: "Meeting invites",
};
const REASONS: Record<string, string> = {
  manual: "Added by hand",
  unsubscribed: "Unsubscribed",
  bounced: "Bounced",
  removal_request: "Removal request",
  complaint: "Spam complaint",
};

export default function RemovalRequestsPage() {
  return (
    <SettingsShell permission="data.privacy" what="privacy requests">
      <RemovalRequests />
    </SettingsShell>
  );
}

function RemovalRequests() {
  const [tab, setTab] = useState<"requests" | "suppression">("requests");
  return (
    <>
      <PageHeader
        title="Removal requests"
        description="When someone asks you to delete their data (GDPR, CCPA), log it here. Completing a request erases them everywhere in AIVA and adds them to the do-not-contact list so they're never imported or emailed again."
      />
      <div className="ui-toolbar" style={{ marginBottom: 14 }}>
        <Tabs
          label="Privacy"
          value={tab}
          onChange={setTab}
          items={[
            { value: "requests", label: "Requests" },
            { value: "suppression", label: "Do-not-contact list" },
          ]}
        />
      </div>
      {tab === "requests" ? <Requests /> : <SuppressionList />}
    </>
  );
}

function Requests() {
  const requests = useApi<{ items: RemovalRequest[]; counts: { pending: number; overdue: number; completed: number } }>("/removal-requests");
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const items = [...(requests.data?.items ?? [])].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending") || a.due_at.localeCompare(b.due_at));
  const counts = requests.data?.counts;

  return (
    <div className="ui-stack">
      <div className="ui-grid ui-grid-3">
        <Kpi label="Waiting" value={counts?.pending ?? "—"} />
        <Kpi label="Overdue" value={counts?.overdue ?? "—"} tone={counts?.overdue ? "danger" : undefined} note="Past the legal deadline" />
        <Kpi label="Completed" value={counts?.completed ?? "—"} tone="success" />
      </div>
      {requests.error ? <Alert>{requests.error}</Alert> : null}
      <Card
        flush
        title="Requests"
        actions={
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => setCreating(true)}>
            <Plus size={15} /> Log a request
          </button>
        }
      >
        {requests.loading && !requests.data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Law</th>
                  <th>Received</th>
                  <th>Deadline</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id} className="clickable" onClick={() => setOpenId(row.id)}>
                    <td>
                      <button type="button" className="dm-linkbtn" onClick={() => setOpenId(row.id)}>
                        {row.email}
                      </button>
                      {row.full_name ? <div className="ui-muted ui-small">{row.full_name}</div> : null}
                    </td>
                    <td>
                      <Badge>{REGULATIONS[row.regulation]}</Badge>
                    </td>
                    <td className="ui-small ui-nowrap">
                      {date(row.received_at)}
                      <div className="ui-muted">{SOURCES[row.source] ?? row.source}</div>
                    </td>
                    <td className="ui-small ui-nowrap">
                      {row.status === "pending" ? (
                        row.overdue ? (
                          <Badge tone="danger">Overdue</Badge>
                        ) : (
                          <span>
                            {row.days_left} day{row.days_left === 1 ? "" : "s"} left
                          </span>
                        )
                      ) : (
                        <span className="ui-muted">{row.completed_at ? date(row.completed_at) : "—"}</span>
                      )}
                      <div className="ui-muted">Due {date(row.due_at)}</div>
                    </td>
                    <td>
                      <Badge tone={STATUS_TONE[row.status]}>{row.status === "pending" ? "To do" : row.status === "completed" ? "Erased" : "Rejected"}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Eraser size={20} />} title="No removal requests" message="When someone asks to be forgotten, log it here so it's handled before the legal deadline (30 days for GDPR, 45 for CCPA)." />
        )}
      </Card>
      <NewRequestModal
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={async (id) => {
          setCreating(false);
          await requests.reload();
          setOpenId(id);
        }}
      />
      <RequestModal id={openId} onClose={() => setOpenId(null)} onChanged={() => void requests.reload()} />
    </div>
  );
}

function NewRequestModal({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: (id: string) => void }) {
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [regulation, setRegulation] = useState("gdpr");
  const [source, setSource] = useState("email");
  const [received, setReceived] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setEmail("");
    setName("");
    setRegulation("gdpr");
    setSource("email");
    setReceived(new Date().toISOString().slice(0, 10));
    setNotes("");
    setError(null);
  }, [open]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const row = await api.post<RemovalRequest>("/removal-requests", {
        email,
        full_name: name || null,
        regulation,
        source,
        received_at: received ? new Date(`${received}T12:00:00`).toISOString() : null,
        notes: notes || null,
      });
      toast("Request logged. Check what AIVA found, then complete it.");
      onSaved(row.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Log a removal request"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="removal-form" className="ui-btn ui-btn-primary" disabled={saving || !email.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Log request
          </button>
        </>
      }
    >
      <form id="removal-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Their email" required>
            <input className="ui-input" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" />
          </Field>
          <Field label="Their name">
            <input className="ui-input" value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Law" hint="Sets the deadline: 30 days (GDPR) or 45 days (CCPA).">
            <select className="ui-select" value={regulation} onChange={(event) => setRegulation(event.target.value)}>
              {Object.entries(REGULATIONS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="How it arrived">
            <select className="ui-select" value={source} onChange={(event) => setSource(event.target.value)}>
              {Object.entries(SOURCES).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Received on">
            <input className="ui-input" type="date" value={received} max={new Date().toISOString().slice(0, 10)} onChange={(event) => setReceived(event.target.value)} />
          </Field>
          <Field label="Notes" className="span-2">
            <textarea className="ui-textarea" rows={3} value={notes} maxLength={5000} onChange={(event) => setNotes(event.target.value)} placeholder="How you verified who they are, ticket number…" />
          </Field>
        </div>
      </form>
    </Modal>
  );
}

function RequestModal({ id, onClose, onChanged }: { id: string | null; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const detail = useApi<RemovalRequest>(id ? `/removal-requests/${id}` : null);
  const [confirming, setConfirming] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const row = id && detail.data?.id === id ? detail.data : null;

  useEffect(() => {
    setConfirming(false);
    setRejecting(false);
    setNote("");
  }, [id]);

  async function complete() {
    if (!row) return;
    setBusy(true);
    try {
      await api.post(`/removal-requests/${row.id}/complete`, { note: note || null });
      toast("Personal data erased and the address blocked.");
      setConfirming(false);
      await detail.reload();
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!row) return;
    setBusy(true);
    try {
      await api.post(`/removal-requests/${row.id}/reject`, { note });
      toast("Request rejected.");
      setRejecting(false);
      await detail.reload();
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const totalFound = row?.matches
    ? row.matches.leads.length + row.matches.contacts.length + Object.values(row.matches.counts).reduce((sum, n) => sum + n, 0)
    : 0;

  return (
    <>
      <Modal
        open={Boolean(id)}
        title={row?.email ?? "Removal request"}
        description={row ? `${REGULATIONS[row.regulation]} request received ${date(row.received_at)} · due ${date(row.due_at)}` : undefined}
        onClose={onClose}
        size="lg"
        footer={
          row?.status === "pending" ? (
            <>
              <button type="button" className="ui-btn" onClick={() => setRejecting(true)} disabled={busy}>
                Reject
              </button>
              <button type="button" className="ui-btn ui-btn-danger solid" onClick={() => setConfirming(true)} disabled={busy}>
                <Eraser size={15} /> Erase their data
              </button>
            </>
          ) : (
            <button type="button" className="ui-btn" onClick={onClose}>
              Close
            </button>
          )
        }
      >
        {!row ? (
          detail.error ? (
            <Alert>{detail.error}</Alert>
          ) : (
            <LoadingRows rows={4} />
          )
        ) : (
          <div className="ui-stack">
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <Badge tone={STATUS_TONE[row.status]}>{row.status === "pending" ? "To do" : row.status === "completed" ? "Erased" : "Rejected"}</Badge>
              {row.status === "pending" ? (
                row.overdue ? (
                  <Badge tone="danger">Overdue</Badge>
                ) : (
                  <span className="ui-small">{row.days_left} days left</span>
                )
              ) : null}
              {row.full_name ? <span className="ui-small">{row.full_name}</span> : null}
              <span className="ui-muted ui-small">Logged by {row.created_by ?? "someone"}</span>
            </div>
            {row.status === "pending" && row.matches ? (
              <div>
                <h3 className="dm-subhead">What AIVA holds about them</h3>
                {totalFound ? (
                  <div className="dm-found">
                    {row.matches.leads.map((lead) => (
                      <Link key={lead.id} href={lead.href} className="dm-found-item">
                        Lead · {lead.label || row.email}
                      </Link>
                    ))}
                    {row.matches.contacts.map((contact) => (
                      <Link key={contact.id} href={contact.href} className="dm-found-item">
                        Contact · {contact.label || row.email}
                      </Link>
                    ))}
                    {Object.entries(row.matches.counts)
                      .filter(([, count]) => count > 0)
                      .map(([key, count]) => (
                        <span key={key} className="dm-found-item">
                          {COUNT_LABELS[key] ?? key}: {count}
                        </span>
                      ))}
                  </div>
                ) : (
                  <p className="ui-muted ui-small">Nothing matches this address. Completing the request still adds it to the do-not-contact list.</p>
                )}
              </div>
            ) : null}
            {row.status === "completed" ? (
              <div>
                <h3 className="dm-subhead">Erased {row.completed_at ? dateTime(row.completed_at) : ""}</h3>
                <div className="dm-found">
                  {Object.entries(row.summary).map(([key, count]) => (
                    <span key={key} className="dm-found-item">
                      {COUNT_LABELS[key] ?? key}: {count}
                    </span>
                  ))}
                </div>
                <p className="ui-muted ui-small">By {row.completed_by ?? "someone"}. The address stays on the do-not-contact list.</p>
              </div>
            ) : null}
            {row.notes ? (
              <div>
                <h3 className="dm-subhead">Notes</h3>
                <p className="ui-small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                  {row.notes}
                </p>
              </div>
            ) : null}
            {rejecting ? (
              <Field label="Why are you rejecting it?" required hint="For example: we couldn't verify who sent it.">
                <textarea className="ui-textarea" rows={3} value={note} onChange={(event) => setNote(event.target.value)} />
              </Field>
            ) : null}
            {rejecting ? (
              <div className="ui-actions">
                <button type="button" className="ui-btn" onClick={() => setRejecting(false)}>
                  Cancel
                </button>
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => void reject()} disabled={busy || !note.trim()}>
                  Reject request
                </button>
              </div>
            ) : null}
          </div>
        )}
      </Modal>
      <ConfirmDialog
        open={confirming}
        title="Erase this person's data?"
        confirmLabel="Erase everything"
        busy={busy}
        message={
          <>
            This permanently deletes their leads, contacts, activities, emails, website visits and sequence history, removes them from meeting invites and
            anonymises campaign sends. It can&apos;t be undone. <strong>{row?.email}</strong> is then added to the do-not-contact list so they&apos;re never
            imported or emailed again.
          </>
        }
        onConfirm={() => void complete()}
        onClose={() => setConfirming(false)}
      />
    </>
  );
}

function SuppressionList() {
  const toast = useToast();
  const [term, setTerm] = useState("");
  const debounced = useDebounced(term, 250);
  const list = useApi<{ items: Suppression[]; total: number; can_edit: boolean }>(`/suppression${debounced ? `?search=${encodeURIComponent(debounced)}` : ""}`);
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [removing, setRemoving] = useState<Suppression | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = Boolean(list.data?.can_edit);

  async function add(event: FormEvent) {
    event.preventDefault();
    const values = text
      .split(/[\s,;]+/)
      .map((v) => v.trim())
      .filter(Boolean);
    if (!values.length) return;
    setBusy(true);
    try {
      const result = await api.post<{ added: number; skipped: number }>("/suppression", { values, note: note || null });
      toast(`Added ${result.added}${result.skipped ? ` · ${result.skipped} skipped (already listed or not valid)` : ""}.`);
      setAdding(false);
      setText("");
      setNote("");
      await list.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await api.del(`/suppression/${removing.id}`);
      toast(`${removing.value} can be contacted again.`);
      setRemoving(null);
      await list.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ui-stack">
      <Alert tone="info">
        Addresses and whole domains on this list are skipped by sequences, campaigns and imports. People who unsubscribe or bounce are added automatically.
      </Alert>
      <Card
        flush
        title={`Do-not-contact list${list.data ? ` · ${list.data.total}` : ""}`}
        actions={
          <span className="ui-row" style={{ gap: 8 }}>
            <label className="settings-nav-search" style={{ width: 220 }}>
              <Search size={15} aria-hidden />
              <input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search" aria-label="Search the list" />
            </label>
            {canEdit ? (
              <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => setAdding(true)}>
                <Plus size={15} /> Add
              </button>
            ) : null}
          </span>
        }
      >
        {list.error ? (
          <div style={{ padding: 16 }}>
            <Alert>{list.error}</Alert>
          </div>
        ) : list.loading && !list.data ? (
          <LoadingRows />
        ) : list.data?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Address or domain</th>
                  <th>Why</th>
                  <th>Added</th>
                  {canEdit ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((entry) => (
                  <tr key={entry.id}>
                    <td>
                      <span className="ui-row" style={{ gap: 8 }}>
                        {entry.kind === "domain" ? <Badge tone="info">Domain</Badge> : null}
                        <span className="ui-mono">{entry.kind === "domain" ? `@${entry.value}` : entry.value}</span>
                      </span>
                      {entry.note ? <div className="ui-muted ui-small">{entry.note}</div> : null}
                    </td>
                    <td className="ui-small">{REASONS[entry.reason] ?? entry.reason}</td>
                    <td className="ui-muted ui-small ui-nowrap">{relativeTime(entry.created_at)}</td>
                    {canEdit ? (
                      <td style={{ textAlign: "right" }}>
                        {entry.reason !== "removal_request" ? (
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Remove ${entry.value}`} onClick={() => setRemoving(entry)}>
                            <Trash2 size={14} />
                          </button>
                        ) : (
                          <span title="Kept because of a completed removal request">
                            <ShieldOff size={14} className="ui-muted" aria-label="Locked" />
                          </span>
                        )}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Ban size={20} />} title={debounced ? "Nothing matches" : "The list is empty"} message="Add competitors, existing customers or anyone who asked not to be contacted." />
        )}
      </Card>

      <Modal
        open={adding}
        title="Add to the do-not-contact list"
        onClose={() => setAdding(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button type="submit" form="suppression-form" className="ui-btn ui-btn-primary" disabled={busy || !text.trim()}>
              {busy ? <span className="ui-spinner" /> : null} Add
            </button>
          </>
        }
      >
        <form id="suppression-form" className="ui-stack" onSubmit={(event) => void add(event)}>
          <Field label="Emails or domains" hint="One per line, or separated by commas. A domain like competitor.com blocks everyone there.">
            <textarea className="ui-textarea" rows={6} value={text} onChange={(event) => setText(event.target.value)} placeholder={"jane@example.com\ncompetitor.com"} />
          </Field>
          <Field label="Note">
            <input className="ui-input" value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} placeholder="Existing customers — handled by account managers" />
          </Field>
        </form>
      </Modal>
      <ConfirmDialog
        open={Boolean(removing)}
        title="Remove from the list?"
        confirmLabel="Remove"
        message={`${removing?.value ?? ""} will be allowed in sequences, campaigns and imports again.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setRemoving(null)}
      />
    </div>
  );
}
