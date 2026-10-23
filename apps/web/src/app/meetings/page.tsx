"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Ban, CalendarClock, CalendarDays, Check, ChevronLeft, ChevronRight, Download, ExternalLink, List, MapPin, Pencil, Plus, Trash2, Video } from "lucide-react";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { MeetingModal } from "@/components/meeting-modal";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Avatar, Badge, ConfirmDialog, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { MEETING_STATUS, RESPONSE_LABEL, type Meeting } from "@/lib/workspace";

export default function MeetingsPage() {
  return (
    <FeaturePage feature="tasks_meetings_calls">
      <Suspense>
        <Meetings />
      </Suspense>
    </FeaturePage>
  );
}

const DAY = 24 * 3600 * 1000;

function startOfWeek(date: Date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  const shift = (d.getDay() + 6) % 7; // Monday first
  d.setDate(d.getDate() - shift);
  return d;
}

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

const time = (value: string) => new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const longDay = (value: Date) => value.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });

function Meetings() {
  const params = useSearchParams();
  const router = useRouter();
  const { can, session } = useSession();
  const [view, setView] = useState<"week" | "agenda">("week");
  const [anchor, setAnchor] = useState(() => startOfWeek(new Date()));
  const [mine, setMine] = useState(!can("records.view_all"));
  const [scheduling, setScheduling] = useState<{ start?: Date } | null>(params.get("new") === "1" ? {} : null);
  const [openId, setOpenId] = useState<string | null>(params.get("open"));
  const [editing, setEditing] = useState<Meeting | null>(null);

  const range = useMemo(() => {
    if (view === "week") return { start: anchor, end: new Date(anchor.getTime() + 7 * DAY) };
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return { start: today, end: new Date(today.getTime() + 45 * DAY) };
  }, [view, anchor]);
  const meetings = useApi<{ items: Meeting[] }>(`/meetings?start=${range.start.toISOString()}&end=${range.end.toISOString()}${mine ? "&mine=true" : ""}`);
  const items = meetings.data?.items ?? [];
  const me = session?.user.id;

  useEffect(() => {
    const id = params.get("open");
    if (id) setOpenId(id);
  }, [params]);

  const closeDetail = useCallback(() => {
    setOpenId(null);
    if (params.get("open")) router.replace("/meetings");
  }, [params, router]);

  const now = new Date();
  const todayCount = items.filter((m) => m.status === "scheduled" && sameDay(new Date(m.start_at), now)).length;
  const awaiting = items.filter((m) => m.status === "scheduled" && m.my_response === "needs_action").length;
  const days = Array.from({ length: 7 }, (_, i) => new Date(anchor.getTime() + i * DAY));
  const upcoming = items.filter((m) => new Date(m.end_at) >= now || view === "week");

  return (
    <>
      <PageHeader
        title="Meetings"
        description="Your calendar of customer and team meetings. Invites go out with a calendar file, and held meetings are logged on the deal and account."
        actions={
          can("records.write") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setScheduling({})}>
              <Plus size={16} /> Schedule meeting
            </button>
          ) : null
        }
      />
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Today" value={todayCount} note="Scheduled meetings" />
        <Kpi label={view === "week" ? "This view" : "Next 45 days"} value={items.filter((m) => m.status === "scheduled").length} />
        <Kpi label="Waiting for your reply" value={awaiting} tone={awaiting ? "danger" : undefined} />
        <Kpi label="Held" value={items.filter((m) => m.status === "completed").length} tone="success" />
      </div>
      <div className="cal-toolbar">
        <Tabs
          label="Calendar view"
          value={view}
          onChange={setView}
          items={[
            { value: "week", label: <><CalendarDays size={14} /> Week</> },
            { value: "agenda", label: <><List size={14} /> Agenda</> },
          ]}
        />
        {view === "week" ? (
          <div className="cal-nav">
            <button type="button" className="ui-btn ui-btn-icon" aria-label="Previous week" onClick={() => setAnchor(new Date(anchor.getTime() - 7 * DAY))}>
              <ChevronLeft size={16} />
            </button>
            <button type="button" className="ui-btn" onClick={() => setAnchor(startOfWeek(new Date()))}>
              Today
            </button>
            <button type="button" className="ui-btn ui-btn-icon" aria-label="Next week" onClick={() => setAnchor(new Date(anchor.getTime() + 7 * DAY))}>
              <ChevronRight size={16} />
            </button>
            <h2 className="cal-title">
              {anchor.toLocaleDateString([], { month: "short", day: "numeric" })} – {new Date(anchor.getTime() + 6 * DAY).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}
            </h2>
          </div>
        ) : null}
        <span className="ui-spacer" />
        {can("records.view_all") ? (
          <Tabs
            label="Whose meetings"
            value={mine ? "mine" : "all"}
            onChange={(value) => setMine(value === "mine")}
            items={[
              { value: "mine", label: "Mine" },
              { value: "all", label: "Everyone" },
            ]}
          />
        ) : null}
      </div>
      {meetings.error ? <Alert>{meetings.error}</Alert> : null}
      {meetings.loading && !meetings.data ? (
        <div className="ui-card">
          <LoadingRows rows={6} />
        </div>
      ) : view === "week" ? (
        <div className="cal-week">
          {days.map((day) => {
            const list = items.filter((m) => sameDay(new Date(m.start_at), day));
            const weekend = day.getDay() === 0 || day.getDay() === 6;
            return (
              <section key={day.toISOString()} className={`cal-day${sameDay(day, now) ? " today" : ""}${weekend ? " weekend" : ""}`} aria-label={longDay(day)}>
                <div className="cal-day-head">
                  {day.toLocaleDateString([], { weekday: "short" })} <strong>{day.getDate()}</strong>
                </div>
                {list.map((m) => (
                  <button key={m.id} type="button" className={`cal-event ${m.status}`} onClick={() => setOpenId(m.id)}>
                    <span className="time">{m.is_all_day ? "All day" : `${time(m.start_at)} – ${time(m.end_at)}`}</span>
                    <span className="title">{m.title}</span>
                  </button>
                ))}
                {can("records.write") ? (
                  <button
                    type="button"
                    className="cal-add"
                    onClick={() => {
                      const start = new Date(day);
                      start.setHours(10, 0, 0, 0);
                      setScheduling({ start });
                    }}
                  >
                    + Add
                  </button>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : upcoming.length ? (
        <div className="agenda">
          {Array.from(new Set(upcoming.map((m) => new Date(m.start_at).toDateString()))).map((key) => (
            <div key={key} className="agenda-day">
              <h3>{sameDay(new Date(key), now) ? "Today" : longDay(new Date(key))}</h3>
              <div className="ui-card">
                <div className="ui-list">
                  {upcoming
                    .filter((m) => new Date(m.start_at).toDateString() === key)
                    .map((m) => (
                      <div key={m.id} className="ui-list-item" role="button" tabIndex={0} onClick={() => setOpenId(m.id)} onKeyDown={(e) => e.key === "Enter" && setOpenId(m.id)}>
                        <CalendarClock size={18} className="mini-icon" />
                        <div className="main">
                          <div className="title">{m.title}</div>
                          <div className="sub">
                            {m.is_all_day ? "All day" : `${time(m.start_at)} – ${time(m.end_at)}`}
                            {m.account_name ? ` · ${m.account_name}` : ""} · {m.attendees.length} guest{m.attendees.length === 1 ? "" : "s"}
                          </div>
                        </div>
                        {m.my_response === "needs_action" && m.organizer_user_id !== me ? <Badge tone="warning">Reply needed</Badge> : null}
                        <Badge tone={MEETING_STATUS[m.status].tone}>{MEETING_STATUS[m.status].label}</Badge>
                      </div>
                    ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="ui-card">
          <EmptyState icon={<CalendarClock size={20} />} title="Nothing on the calendar" message="Meetings you schedule or are invited to show up here." />
        </div>
      )}

      <MeetingModal
        open={scheduling !== null || editing !== null}
        meeting={editing}
        defaults={scheduling ?? undefined}
        onClose={() => (setScheduling(null), setEditing(null))}
        onSaved={() => void meetings.reload()}
      />
      <MeetingDetail
        id={openId}
        onClose={closeDetail}
        onEdit={(m) => (setOpenId(null), setEditing(m))}
        onChanged={() => void meetings.reload()}
      />
    </>
  );
}

function MeetingDetail({ id, onClose, onEdit, onChanged }: { id: string | null; onClose: () => void; onEdit: (meeting: Meeting) => void; onChanged: () => void }) {
  const toast = useToast();
  const { session } = useSession();
  const meeting = useApi<Meeting>(id ? `/meetings/${id}` : null);
  const [mode, setMode] = useState<"view" | "complete" | "cancel" | "delete">("view");
  const [summary, setSummary] = useState("");
  const [notes, setNotes] = useState("");
  const [actions, setActions] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const m = id ? meeting.data : null;

  useEffect(() => {
    setMode("view");
    setSummary("");
    setNotes("");
    setActions("");
    setReason("");
  }, [id]);

  async function run(fn: () => Promise<Meeting | void>, message: string) {
    setBusy(true);
    try {
      const result = await fn();
      if (result) meeting.setData(result);
      toast(message);
      onChanged();
      setMode("view");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const respond = (response: "accepted" | "declined" | "tentative") =>
    run(() => api.post<Meeting>(`/meetings/${id}/respond`, { response }), `Reply sent: ${RESPONSE_LABEL[response]}.`);
  const complete = () =>
    run(async () => {
      const result = await api.post<Meeting>(`/meetings/${id}/complete`, {
        summary: summary.trim() || null,
        notes: notes.trim() || null,
        action_items: actions.split("\n").map((a) => a.trim()).filter(Boolean),
      });
      if (result.tasks_created) toast(`${result.tasks_created} follow-up task${result.tasks_created === 1 ? "" : "s"} created.`);
      return result;
    }, "Marked as held and logged on the timeline.");
  const cancel = () => run(() => api.post<Meeting>(`/meetings/${id}/cancel`, { reason: reason.trim() || null, notify: true }), "Meeting cancelled. Guests were told.");
  const remove = () =>
    run(async () => {
      await api.del(`/meetings/${id}`);
      onClose();
    }, "Meeting deleted.");

  if (!id) return null;
  const mineInvite = m?.attendees.find((a) => a.type === "user" && a.id === session?.user.id);
  const past = m ? new Date(m.start_at) <= new Date() : false;

  return (
    <>
      <Modal
        open={Boolean(id) && mode !== "delete"}
        size="lg"
        title={m ? m.title : "Meeting"}
        description={
          m ? (
            <>
              {new Date(m.start_at).toLocaleString([], { weekday: "long", month: "long", day: "numeric", hour: "numeric", minute: "2-digit" })}
              {m.is_all_day ? " · All day" : ` – ${time(m.end_at)}`}
            </>
          ) : undefined
        }
        onClose={onClose}
        footer={
          m && mode === "view" ? (
            <>
              <a className="ui-btn" href={`/api/aiva/meetings/${m.id}/invite.ics`} style={{ marginRight: "auto" }}>
                <Download size={15} /> Add to calendar
              </a>
              {m.can_edit ? (
                <button type="button" className="ui-btn ui-btn-ghost" onClick={() => setMode("delete")}>
                  <Trash2 size={15} /> Delete
                </button>
              ) : null}
              {m.can_edit && m.status === "scheduled" ? (
                <>
                  <button type="button" className="ui-btn ui-btn-danger" onClick={() => setMode("cancel")}>
                    <Ban size={15} /> Cancel meeting
                  </button>
                  <button type="button" className="ui-btn" onClick={() => onEdit(m)}>
                    <Pencil size={15} /> Edit
                  </button>
                  <button type="button" className="ui-btn ui-btn-primary" onClick={() => setMode("complete")} disabled={!past}>
                    <Check size={15} /> Mark as held
                  </button>
                </>
              ) : null}
            </>
          ) : mode === "complete" ? (
            <>
              <button type="button" className="ui-btn" onClick={() => setMode("view")}>
                Back
              </button>
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => void complete()} disabled={busy}>
                {busy ? <span className="ui-spinner" /> : <Check size={15} />} Save and log
              </button>
            </>
          ) : mode === "cancel" ? (
            <>
              <button type="button" className="ui-btn" onClick={() => setMode("view")}>
                Back
              </button>
              <button type="button" className="ui-btn ui-btn-danger solid" onClick={() => void cancel()} disabled={busy}>
                Cancel meeting
              </button>
            </>
          ) : null
        }
      >
        {meeting.error ? <Alert>{meeting.error}</Alert> : null}
        {!m ? (
          <LoadingRows rows={5} />
        ) : mode === "complete" ? (
          <div className="ui-form-grid">
            <Field label="Summary" hint="Saved on the timeline of the deal, account and contact." className="span-2">
              <textarea className="ui-textarea" rows={2} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="What was agreed?" />
            </Field>
            <Field label="Notes" className="span-2">
              <textarea className="ui-textarea" rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            <Field label="Action items" hint="One per line. Each becomes a follow-up task for the organizer." className="span-2">
              <textarea className="ui-textarea" rows={3} value={actions} onChange={(e) => setActions(e.target.value)} placeholder={"Send the proposal\nBook a site visit"} />
            </Field>
          </div>
        ) : mode === "cancel" ? (
          <Field label="Reason (optional)" hint="Guests get a cancellation with this note.">
            <input className="ui-input" value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        ) : (
          <div className="ui-stack">
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <Badge tone={MEETING_STATUS[m.status].tone}>{MEETING_STATUS[m.status].label}</Badge>
              {m.is_online && m.meeting_url ? (
                <a className="ui-btn ui-btn-primary ui-btn-sm" href={m.meeting_url} target="_blank" rel="noopener noreferrer">
                  <Video size={14} /> Join meeting
                </a>
              ) : null}
              {!m.is_online && m.location ? (
                <span className="ui-row ui-small" style={{ gap: 4 }}>
                  <MapPin size={14} /> {m.location}
                </span>
              ) : null}
              <span className="ui-spacer" />
              {mineInvite && m.status === "scheduled" ? (
                <div className="ui-row" style={{ gap: 6 }}>
                  <span className="ui-small ui-muted">Going?</span>
                  {(["accepted", "tentative", "declined"] as const).map((r) => (
                    <button key={r} type="button" className={`ui-btn ui-btn-sm${m.my_response === r ? " ui-btn-primary" : ""}`} onClick={() => void respond(r)} disabled={busy}>
                      {r === "accepted" ? "Yes" : r === "tentative" ? "Maybe" : "No"}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            {m.conflicts?.length ? <Alert tone="warning">Overlaps “{m.conflicts[0].title}” for someone on the guest list.</Alert> : null}
            {m.status === "cancelled" && m.cancel_reason ? <Alert tone="warning">Cancelled: {m.cancel_reason}</Alert> : null}
            <dl className="ui-dl">
              <dt>Organizer</dt>
              <dd>{m.organizer_name ?? "—"}</dd>
              {m.opportunity_id ? (
                <>
                  <dt>Deal</dt>
                  <dd>
                    <Link href={`/opportunities/${m.opportunity_id}`}>{m.opportunity_name}</Link>
                  </dd>
                </>
              ) : null}
              {m.account_id ? (
                <>
                  <dt>Account</dt>
                  <dd>
                    <Link href={`/accounts/${m.account_id}`}>{m.account_name}</Link>
                  </dd>
                </>
              ) : null}
              {m.contact_id ? (
                <>
                  <dt>Contact</dt>
                  <dd>
                    <Link href={`/contacts/${m.contact_id}`}>{m.contact_name}</Link>
                  </dd>
                </>
              ) : null}
              {m.lead_id ? (
                <>
                  <dt>Lead</dt>
                  <dd>
                    <Link href={`/leads/${m.lead_id}`}>{m.lead_name}</Link>
                  </dd>
                </>
              ) : null}
              {m.meeting_url ? (
                <>
                  <dt>Link</dt>
                  <dd className="ui-truncate">
                    <a href={m.meeting_url} target="_blank" rel="noopener noreferrer">
                      {m.meeting_url} <ExternalLink size={12} />
                    </a>
                  </dd>
                </>
              ) : null}
              <dt>Reminder</dt>
              <dd>{m.reminder_minutes === null ? "None" : m.reminder_minutes >= 60 ? `${m.reminder_minutes / 60} hour(s) before` : `${m.reminder_minutes} minutes before`}</dd>
            </dl>
            {m.description ? <p style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 13.5 }}>{m.description}</p> : null}
            <div>
              <div className="ui-label" style={{ marginBottom: 8 }}>
                Guests ({m.attendees.length})
              </div>
              <ul className="attendee-list">
                {m.attendees.map((a) => (
                  <li key={`${a.type}-${a.id ?? a.email}`}>
                    <Avatar name={a.name} size={28} />
                    <span className="who">
                      {a.name}
                      <span>
                        {a.type === "user" ? "Teammate" : a.type === "contact" ? "Contact" : a.type === "lead" ? "Lead" : "Guest"}
                        {a.email ? ` · ${a.email}` : ""}
                      </span>
                    </span>
                    {a.type === "user" ? <Badge tone={a.response === "accepted" ? "success" : a.response === "declined" ? "danger" : "neutral"}>{RESPONSE_LABEL[a.response]}</Badge> : null}
                  </li>
                ))}
                {!m.attendees.length ? <li className="ui-muted">No guests.</li> : null}
              </ul>
            </div>
            {m.status === "completed" ? (
              <div className="ui-stack" style={{ gap: 8 }}>
                {m.summary ? (
                  <p style={{ margin: 0 }}>
                    <strong>Summary:</strong> {m.summary}
                  </p>
                ) : null}
                {m.notes ? <p style={{ margin: 0, whiteSpace: "pre-wrap", color: "var(--text-2)" }}>{m.notes}</p> : null}
                {m.action_items.length ? (
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {m.action_items.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {m.status === "scheduled" && !past && m.can_edit ? <p className="ui-muted ui-small" style={{ margin: 0 }}>You can mark it as held once it has started.</p> : null}
          </div>
        )}
      </Modal>
      <ConfirmDialog
        open={mode === "delete"}
        title="Delete this meeting?"
        message="It's removed from AIVA. Guests aren't notified — cancel it instead if they should be."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setMode("view")}
      />
    </>
  );
}
