"use client";

import Link from "next/link";
import { CalendarCheck, Link2, Video } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useToast } from "@/components/providers";
import { Alert, Field, Modal } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";
import type { Meeting, Member } from "@/lib/workspace";
import type { Account } from "@/types/account";
import type { Contact } from "@/types/contact";
import type { Opportunity } from "@/types/opportunity";

export interface MeetingDefaults {
  title?: string;
  account_id?: string | null;
  contact_id?: string | null;
  lead_id?: string | null;
  opportunity_id?: string | null;
  start?: Date;
}

function localInput(date: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function nextHalfHour() {
  const date = new Date();
  date.setMinutes(date.getMinutes() < 30 ? 30 : 60, 0, 0);
  return date;
}

const DURATIONS = [15, 30, 45, 60, 90, 120, 180];

/** Video-call types an integration can create a link for (GET /integration-hub/mail-calendar/meeting-options). */
type ConferenceKey = "google_meet" | "zoom" | "microsoft_teams";
type MeetingOptions = {
  options: { key: ConferenceKey; label: string; available: boolean; reason: string | null; account: string | null; setup_url: string }[];
  calendar: { key: string; label: string; account: string | null; invites_from_calendar: boolean } | null;
};
const LAST_CONFERENCE = "aiva.meeting.conference";

function rememberedConference(): string {
  try {
    return window.localStorage.getItem(LAST_CONFERENCE) ?? "link";
  } catch {
    return "link";
  }
}

function rememberConference(value: string) {
  try {
    window.localStorage.setItem(LAST_CONFERENCE, value);
  } catch {
    // Storage can be unavailable (private windows); the choice just isn't remembered.
  }
}
const REMINDERS: { value: string; label: string }[] = [
  { value: "", label: "No reminder" },
  { value: "5", label: "5 minutes before" },
  { value: "10", label: "10 minutes before" },
  { value: "15", label: "15 minutes before" },
  { value: "30", label: "30 minutes before" },
  { value: "60", label: "1 hour before" },
  { value: "1440", label: "1 day before" },
];

/** Schedule a new meeting or edit an existing one. */
export function MeetingModal({
  open,
  onClose,
  onSaved,
  meeting,
  defaults,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (meeting: Meeting) => void;
  meeting?: Meeting | null;
  defaults?: MeetingDefaults;
}) {
  const toast = useToast();
  const members = useApi<Member[]>(open ? "/meta/members" : null);
  const accounts = useApi<Account[]>(open ? "/accounts?limit=100" : null);
  const opportunities = useApi<Opportunity[]>(open ? "/opportunities?limit=100" : null);
  const [title, setTitle] = useState("");
  const [start, setStart] = useState("");
  const [duration, setDuration] = useState(30);
  const [allDay, setAllDay] = useState(false);
  const [online, setOnline] = useState(true);
  const [url, setUrl] = useState("");
  const [location, setLocation] = useState("");
  const [accountId, setAccountId] = useState("");
  const [contactId, setContactId] = useState("");
  const [opportunityId, setOpportunityId] = useState("");
  const [leadId, setLeadId] = useState<string | null>(null);
  const [teammates, setTeammates] = useState<string[]>([]);
  const [contactGuests, setContactGuests] = useState<string[]>([]);
  const [emails, setEmails] = useState("");
  const [reminder, setReminder] = useState("15");
  const [description, setDescription] = useState("");
  const [sendInvites, setSendInvites] = useState(true);
  // "link" = paste a link yourself; otherwise an integration creates it when you save.
  const [conference, setConference] = useState<string>("link");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const contacts = useApi<Contact[]>(open && accountId ? `/contacts?account_id=${accountId}&limit=100` : null);
  const meetingOptions = useApi<MeetingOptions>(open ? "/integration-hub/mail-calendar/meeting-options" : null);
  const conferenceChoices = (meetingOptions.data?.options ?? []).filter((o) => o.available);
  const currentConference = meeting?.conference?.type ?? "link";
  const pulled = Boolean(meeting?.provider && meeting.provider !== "aiva");
  // Default length for new meetings comes from Settings → Team sharing & defaults.
  const sharing = useApi<{ data: { meeting_minutes: number } }>(open && !meeting ? "/workspace-settings/sharing" : null);
  const defaultMinutes = sharing.data?.data.meeting_minutes ?? 30;
  useEffect(() => {
    if (open && !meeting) setDuration(defaultMinutes);
  }, [open, meeting, defaultMinutes]);

  useEffect(() => {
    if (!open) return;
    setError(null);
    if (meeting) {
      const begin = new Date(meeting.start_at);
      setTitle(meeting.title);
      setStart(localInput(begin));
      setDuration(Math.max(5, Math.round((new Date(meeting.end_at).getTime() - begin.getTime()) / 60000)));
      setAllDay(meeting.is_all_day);
      setOnline(meeting.is_online);
      setUrl(meeting.meeting_url ?? "");
      setLocation(meeting.location ?? "");
      setAccountId(meeting.account_id ?? "");
      setContactId(meeting.contact_id ?? "");
      setOpportunityId(meeting.opportunity_id ?? "");
      setLeadId(meeting.lead_id);
      setTeammates(meeting.attendees.filter((a) => a.type === "user" && a.id).map((a) => a.id!));
      setContactGuests(meeting.attendees.filter((a) => a.type === "contact" && a.id).map((a) => a.id!));
      setEmails(meeting.attendees.filter((a) => a.type === "email" || a.type === "lead").map((a) => a.email).filter(Boolean).join(", "));
      setReminder(meeting.reminder_minutes === null ? "" : String(meeting.reminder_minutes));
      setDescription(meeting.description ?? "");
      setSendInvites(true);
      setConference(meeting.conference?.type ?? "link");
    } else {
      setTitle(defaults?.title ?? "");
      setStart(localInput(defaults?.start ?? nextHalfHour()));
      setDuration(30);
      setAllDay(false);
      setOnline(true);
      setUrl("");
      setLocation("");
      setAccountId(defaults?.account_id ?? "");
      setContactId(defaults?.contact_id ?? "");
      setOpportunityId(defaults?.opportunity_id ?? "");
      setLeadId(defaults?.lead_id ?? null);
      setTeammates([]);
      setContactGuests(defaults?.contact_id ? [defaults.contact_id] : []);
      setEmails("");
      setReminder("15");
      setDescription("");
      setSendInvites(true);
      setConference(rememberedConference());
    }
  }, [open, meeting, defaults]);

  // A remembered choice that isn't available any more (switched off, disconnected) falls back to a pasted link.
  useEffect(() => {
    if (!open || meeting || !meetingOptions.data) return;
    if (conference !== "link" && !conferenceChoices.some((o) => o.key === conference)) setConference("link");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, meeting, meetingOptions.data]);

  // Picking a deal fills in its account and main contact.
  const opportunity = useMemo(() => opportunities.data?.find((o) => o.id === opportunityId) ?? null, [opportunities.data, opportunityId]);
  useEffect(() => {
    if (!opportunity || meeting) return;
    if (opportunity.account_id !== accountId) setAccountId(opportunity.account_id);
    if (!contactId && opportunity.primary_contact_id) {
      setContactId(opportunity.primary_contact_id);
      setContactGuests((current) => (current.includes(opportunity.primary_contact_id!) ? current : [...current, opportunity.primary_contact_id!]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity]);

  function toggle(list: string[], value: string, set: (next: string[]) => void) {
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  async function save() {
    setError(null);
    if (!title.trim()) return setError("Give the meeting a title.");
    if (!start) return setError("Pick when the meeting starts.");
    const begin = new Date(start);
    if (allDay) begin.setHours(0, 0, 0, 0);
    const end = allDay ? new Date(begin.getTime() + 24 * 3600 * 1000) : new Date(begin.getTime() + duration * 60000);
    const guestEmails = emails
      .split(/[\s,;]+/)
      .map((e) => e.trim())
      .filter(Boolean);
    const bad = guestEmails.find((e) => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e));
    if (bad) return setError(`“${bad}” isn't a valid email address.`);
    const usesIntegration = online && conference !== "link";
    const conferenceChanged = (online ? conference : "link") !== currentConference;
    const payload = {
      title: title.trim(),
      description: description.trim() || null,
      start_at: begin.toISOString(),
      end_at: end.toISOString(),
      is_all_day: allDay,
      is_online: online,
      meeting_url: usesIntegration ? (conference === currentConference ? meeting?.meeting_url ?? null : null) : online ? url.trim() || null : null,
      ...(meeting ? (conferenceChanged ? { conference: usesIntegration ? conference : "none" } : {}) : usesIntegration ? { conference } : {}),
      location: online ? null : location.trim() || null,
      account_id: accountId || null,
      contact_id: contactId || null,
      lead_id: leadId || null,
      opportunity_id: opportunityId || null,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      reminder_minutes: reminder === "" ? null : Number(reminder),
      attendees: [
        ...teammates.map((id) => ({ type: "user", id })),
        ...contactGuests.map((id) => ({ type: "contact", id })),
        ...(leadId && !meeting ? [{ type: "lead", id: leadId }] : []),
        ...guestEmails.map((email) => ({ type: "email", email })),
      ],
    };
    setBusy(true);
    try {
      const saved = meeting
        ? await api.patch<Meeting>(`/meetings/${meeting.id}`, { ...payload, send_updates: sendInvites })
        : await api.post<Meeting>("/meetings", { ...payload, send_invites: sendInvites });
      if (saved.conflicts?.length) toast(`Heads up: this overlaps “${saved.conflicts[0].title}”.`, "danger");
      for (const warning of saved.integration_warnings ?? []) toast(warning, "danger");
      if (!meeting) rememberConference(online ? conference : "link");
      const fromCalendar = saved.calendar_sync?.invites && saved.calendar_sync.state === "synced" && sendInvites;
      toast(
        meeting
          ? "Meeting updated."
          : fromCalendar
            ? `Meeting scheduled. ${saved.calendar_sync?.label} sent the invitations.`
            : saved.invites_sent
              ? `Meeting scheduled. ${saved.invites_sent} invitation${saved.invites_sent === 1 ? "" : "s"} emailed.`
              : "Meeting scheduled.",
      );
      onSaved(saved);
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      size="lg"
      title={meeting ? "Edit meeting" : "Schedule a meeting"}
      onClose={onClose}
      footer={
        <>
          <label className="ui-check" style={{ marginRight: "auto" }}>
            <input type="checkbox" checked={sendInvites} onChange={(e) => setSendInvites(e.target.checked)} />{" "}
            {meeting ? "Send updates to guests" : meetingOptions.data?.calendar?.invites_from_calendar ? `Send invitations from ${meetingOptions.data.calendar.label}` : "Email invitations to guests"}
          </label>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null}
            {meeting ? "Save changes" : "Schedule"}
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-form-grid">
        <Field label="Title" required className="span-2">
          <input className="ui-input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Discovery call with Northwind" />
        </Field>
        <Field label={allDay ? "Date" : "Starts"} required>
          <input className="ui-input" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Length">
          <select className="ui-select" value={allDay ? "all" : String(duration)} onChange={(e) => (e.target.value === "all" ? setAllDay(true) : (setAllDay(false), setDuration(Number(e.target.value))))}>
            {[...new Set([...DURATIONS, duration])].sort((a, b) => a - b).map((d) => (
              <option key={d} value={d}>
                {d < 60 ? `${d} minutes` : `${d / 60} hour${d === 60 ? "" : "s"}`}
              </option>
            ))}
            <option value="all">All day</option>
          </select>
        </Field>
        <Field label="Where">
          <select className="ui-select" value={online ? "online" : "in_person"} onChange={(e) => setOnline(e.target.value === "online")}>
            <option value="online">Online</option>
            <option value="in_person">In person</option>
          </select>
        </Field>
        {online && (conferenceChoices.length || currentConference !== "link") && !pulled ? (
          <div className="ui-field">
            <span className="ui-label">Video call</span>
            <div className="imc-conference" role="radiogroup" aria-label="Video call">
              {conferenceChoices.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  role="radio"
                  aria-checked={conference === option.key}
                  className={`imc-conference-option${conference === option.key ? " on" : ""}`}
                  onClick={() => setConference(option.key)}
                  title={option.account ? `Created in ${option.account}` : undefined}
                >
                  <Video size={14} /> {option.label}
                </button>
              ))}
              {currentConference !== "link" && !conferenceChoices.some((o) => o.key === currentConference) ? (
                <button type="button" role="radio" aria-checked={conference === currentConference} className={`imc-conference-option${conference === currentConference ? " on" : ""}`} onClick={() => setConference(currentConference)}>
                  <Video size={14} /> {meeting?.conference?.label}
                </button>
              ) : null}
              <button type="button" role="radio" aria-checked={conference === "link"} className={`imc-conference-option${conference === "link" ? " on" : ""}`} onClick={() => setConference("link")}>
                <Link2 size={14} /> Paste a link
              </button>
            </div>
            {conference === "link" ? (
              <input className="ui-input" style={{ marginTop: 8 }} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" aria-label="Meeting link" />
            ) : (
              <div className="imc-conference-note">
                {conference === currentConference && meeting?.meeting_url ? (
                  <>
                    Link:{" "}
                    <a href={meeting.meeting_url} target="_blank" rel="noopener noreferrer">
                      {meeting.meeting_url}
                    </a>
                  </>
                ) : (
                  `AIVA creates the ${conferenceChoices.find((o) => o.key === conference)?.label ?? "meeting"} link when you save and adds it to the invitation.`
                )}
              </div>
            )}
          </div>
        ) : online ? (
          <Field label="Meeting link" hint={<>Zoom, Teams, Google Meet or any https link. <Link href="/integrations">Connect one</Link> to create links automatically.</>}>
            <input className="ui-input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
          </Field>
        ) : (
          <Field label="Location">
            <input className="ui-input" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Office, address or room" />
          </Field>
        )}
        <Field label="Deal">
          <select className="ui-select" value={opportunityId} onChange={(e) => setOpportunityId(e.target.value)}>
            <option value="">No deal</option>
            {(opportunities.data ?? []).map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Account">
          <select className="ui-select" value={accountId} onChange={(e) => (setAccountId(e.target.value), setContactId(""), setContactGuests([]))}>
            <option value="">No account</option>
            {(accounts.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Main contact">
          <select className="ui-select" value={contactId} onChange={(e) => (setContactId(e.target.value), e.target.value && !contactGuests.includes(e.target.value) && setContactGuests([...contactGuests, e.target.value]))} disabled={!accountId}>
            <option value="">{accountId ? "No contact" : "Choose an account first"}</option>
            {(contacts.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.first_name} {c.last_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Reminder">
          <select className="ui-select" value={reminder} onChange={(e) => setReminder(e.target.value)}>
            {REMINDERS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
        <div className="ui-field span-2">
          <span className="ui-label">Teammates</span>
          <div className="check-grid">
            {(members.data ?? []).map((m) => (
              <label key={m.id} className="ui-check">
                <input type="checkbox" checked={teammates.includes(m.id)} onChange={() => toggle(teammates, m.id, setTeammates)} /> {m.full_name}
              </label>
            ))}
          </div>
        </div>
        {accountId && (contacts.data ?? []).length ? (
          <div className="ui-field span-2">
            <span className="ui-label">Customer guests</span>
            <div className="check-grid">
              {(contacts.data ?? []).map((c) => (
                <label key={c.id} className="ui-check">
                  <input type="checkbox" checked={contactGuests.includes(c.id)} onChange={() => toggle(contactGuests, c.id, setContactGuests)} /> {c.first_name} {c.last_name}
                  {c.email ? <span className="ui-muted"> · {c.email}</span> : null}
                </label>
              ))}
            </div>
          </div>
        ) : null}
        <Field label="Other guests" hint="Email addresses, separated by commas." className="span-2">
          <input className="ui-input" value={emails} onChange={(e) => setEmails(e.target.value)} placeholder="name@company.com" />
        </Field>
        <Field label="Agenda" className="span-2">
          <textarea className="ui-textarea" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        {meeting?.calendar_sync ? (
          <div className="span-2 imc-calendar-badge">
            <CalendarCheck size={14} />
            {meeting.calendar_sync.origin === "pulled" ? `From ${meeting.calendar_sync.label}` : `In ${meeting.calendar_sync.label}`}
            {meeting.calendar_sync.state === "pending" ? " · waiting to sync" : meeting.calendar_sync.state === "error" ? ` · not synced: ${meeting.calendar_sync.error ?? "error"}` : ""}
            {pulled && !meeting.calendar_sync.organizer_self ? " · changes here stay in AIVA" : ""}
          </div>
        ) : !meeting && meetingOptions.data?.calendar ? (
          <div className="span-2 imc-calendar-badge">
            <CalendarCheck size={14} /> Also added to {meetingOptions.data.calendar.label}
            {meetingOptions.data.calendar.account ? ` (${meetingOptions.data.calendar.account})` : ""}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
