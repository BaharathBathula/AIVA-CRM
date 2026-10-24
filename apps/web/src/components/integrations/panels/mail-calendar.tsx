"use client";

import Link from "next/link";
import { AlertTriangle, CalendarClock, CalendarSync, Inbox, Mail, MessagesSquare, RefreshCw, Send, Settings2, Video } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";

import { useToast } from "@/components/providers";
import { Alert, Badge, Card, Field } from "@/components/ui";
import { dateTime, number, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import type { IntegrationDetail, PanelProps } from "../types";

/*
 * Provider panels for the mail, calendar and meetings group (Gmail, Outlook Mail,
 * Google / Outlook Calendar, Google Meet, Zoom, Microsoft Teams). Each panel renders
 * below the shared connection card on /integrations/[key]; `detail.extra` comes from
 * the provider module's detail() hook in apps/api/app/integrations/providers.
 */

const MC = "/integration-hub/mail-calendar";

// ---------------------------------------------------------------- shared bits

function SwitchRow({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint?: ReactNode;
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="settings-row imc-row">
      <div>
        <div className="settings-row-label">{label}</div>
        {hint ? <div className="settings-row-hint">{hint}</div> : null}
      </div>
      <div className="settings-row-control">
        <label className="ui-switch">
          <input type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
          <span />
        </label>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="imc-stat">
      <div className="imc-stat-value">{value}</div>
      <div className="imc-stat-label">{label}</div>
    </div>
  );
}

/** POST an action, toast its message, then refresh the page's data. */
function useAction(reload: () => Promise<void>) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  async function run(name: string, path: string, body?: unknown) {
    setBusy(name);
    try {
      const result = await api.post<{ message?: string }>(path, body);
      if (result?.message) toast(result.message);
      await reload();
      return true;
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      await reload();
      return false;
    } finally {
      setBusy(null);
    }
  }
  return { run, busy };
}

/** Save options on your own connection (PUT /integration-hub/{key}/my-settings). */
function useMySettings(detail: IntegrationDetail, setDetail: (detail: IntegrationDetail) => void) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  async function save(config: Record<string, unknown>, message = "Saved.") {
    setSaving(true);
    try {
      const next = await api.put<IntegrationDetail>(`/integration-hub/${detail.integration.key}/my-settings`, { config });
      setDetail(next);
      toast(message);
      return true;
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      return false;
    } finally {
      setSaving(false);
    }
  }
  return { save, saving };
}

function ProviderError({ detail }: { detail: IntegrationDetail }) {
  return detail.extra?.error ? <Alert tone="warning">{detail.extra.error}</Alert> : null;
}

function mine(detail: IntegrationDetail) {
  return Boolean(detail.enabled && detail.connection && detail.connection.user_id);
}

// ---------------------------------------------------------------- Gmail / Outlook Mail

type MailExtra = {
  mailbox: { email: string; display_name: string | null; status: string; last_error: string | null; last_sync_at: string | null; sync_enabled: boolean } | null;
  other_mailbox: { email: string; kind: string } | null;
  sync_inbox: boolean | null;
  display_name: string | null;
  stats: { imported_total?: number; imported_week?: number; last_checked?: number; last_imported?: number };
  sync_every_minutes: number;
  server_mail: boolean;
};

function MailPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as unknown as Partial<MailExtra>;
  const vendor = detail.integration.key === "gmail" ? "Gmail" : "Outlook";
  const { run, busy } = useAction(reload);
  const { save, saving } = useMySettings(detail, setDetail);
  const mailbox = extra.mailbox ?? null;
  const [name, setName] = useState(mailbox?.display_name ?? "");
  useEffect(() => setName(mailbox?.display_name ?? ""), [mailbox?.display_name]);

  if (!detail.enabled) return null;
  if (!detail.connection) {
    return extra.other_mailbox ? (
      <Alert tone="info">
        You send from <strong>{extra.other_mailbox.email}</strong> today ({extra.other_mailbox.kind === "app_password" ? "app password" : "another mail integration"}). Connecting {vendor} replaces it, so your emails go out through {vendor}
        &apos;s API with no app password.
      </Alert>
    ) : null;
  }

  const otherLabel = extra.other_mailbox ? (extra.other_mailbox.kind === "app_password" ? "an app password" : extra.other_mailbox.kind === "gmail" ? "Gmail" : "Outlook Mail") : null;

  return (
    <Card title="Your mailbox" icon={<Mail size={17} />}>
      <div className="ui-stack" style={{ gap: 14 }}>
        <ProviderError detail={detail} />
        {mailbox ? (
          <>
            <div className="imc-identity">
              <span className="imc-identity-icon" aria-hidden>
                <Send size={16} />
              </span>
              <div style={{ minWidth: 0 }}>
                <div className="ui-small ui-muted">AIVA sends as</div>
                <div className="imc-identity-main ui-truncate">
                  {mailbox.display_name ? `${mailbox.display_name} <${mailbox.email}>` : mailbox.email}
                </div>
              </div>
              <Badge tone={mailbox.status === "connected" && !mailbox.last_error ? "success" : "warning"} dot>
                {mailbox.status === "connected" && !mailbox.last_error ? `Through ${vendor}` : "Check the connection"}
              </Badge>
            </div>
            {mailbox.last_error ? <Alert tone="warning">{mailbox.last_error}</Alert> : null}
            <p className="int-section-note">
              One-off emails, campaigns, sequence steps and meeting invitations you send from AIVA go out through {vendor} and appear in your Sent folder.
              {extra.server_mail ? " If an admin turns this integration off, your mail falls back to the workspace's mail server." : ""}
            </p>
            <div className="settings-rows">
              <div className="settings-row imc-row">
                <div>
                  <div className="settings-row-label">Sender name</div>
                  <div className="settings-row-hint">How your name shows in people&apos;s inboxes.</div>
                </div>
                <div className="settings-row-control">
                  <input className="ui-input imc-name-input" value={name} maxLength={200} onChange={(event) => setName(event.target.value)} aria-label="Sender name" />
                  <button type="button" className="ui-btn" disabled={saving || name.trim() === (mailbox.display_name ?? "")} onClick={() => void save({ display_name: name.trim() }, "Sender name saved.")}>
                    Save
                  </button>
                </div>
              </div>
              <SwitchRow
                label="Read my inbox for customer emails"
                hint={`Every ${extra.sync_every_minutes ?? 10} minutes AIVA looks for new mail from your contacts and adds it to their timeline; replies stop sequences. Other mail stays out of AIVA.`}
                checked={Boolean(extra.sync_inbox)}
                disabled={saving}
                onChange={(next) => void save({ sync_inbox: next }, next ? "AIVA will read your inbox for customer replies." : "AIVA stopped reading your inbox.")}
              />
            </div>
            <div className="imc-stats">
              <Stat label="Customer emails this week" value={number(extra.stats?.imported_week ?? 0)} />
              <Stat label="Added in total" value={number(extra.stats?.imported_total ?? 0)} />
              <Stat label="Last checked" value={mailbox.last_sync_at ? relativeTime(mailbox.last_sync_at) : "Not yet"} />
            </div>
            <div className="ui-actions">
              <button type="button" className="ui-btn" disabled={busy !== null || !extra.sync_inbox} onClick={() => void run("sync", `${MC}/${detail.integration.key}/sync`)}>
                {busy === "sync" ? <span className="ui-spinner" /> : <Inbox size={15} />} Check inbox now
              </button>
              <button type="button" className="ui-btn" disabled={busy !== null} onClick={() => void run("test", `${MC}/${detail.integration.key}/send-test`)}>
                {busy === "test" ? <span className="ui-spinner" /> : <Send size={15} />} Email me a test
              </button>
            </div>
          </>
        ) : (
          <>
            <Alert tone="info">
              {otherLabel ? (
                <>
                  AIVA sends your email from <strong>{extra.other_mailbox?.email}</strong> through {otherLabel}. Switch to {vendor} to send from {detail.connection.account_label} instead.
                </>
              ) : (
                <>Your {vendor} account is connected but isn&apos;t your AIVA mailbox.</>
              )}
            </Alert>
            <div>
              <button type="button" className="ui-btn ui-btn-primary" disabled={busy !== null} onClick={() => void run("use", `${MC}/${detail.integration.key}/use-mailbox`)}>
                {busy === "use" ? <span className="ui-spinner" /> : <Mail size={15} />} Send from {detail.connection.account_label ?? vendor}
              </button>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- Google / Outlook Calendar

type CalendarSettings = {
  push_meetings: boolean;
  invites_from_calendar: boolean;
  pull_events: boolean;
  import_mode: "matched" | "external" | "all";
  calendar_id: string | null;
};

type CalendarExtra = {
  connected: boolean;
  settings: CalendarSettings;
  sync_every_minutes: number;
  window: { past_days: number; future_days: number };
  stats: { created_total?: number; updated_total?: number; cancelled_total?: number; upcoming_linked?: number };
  problems: { id: string; title: string; start_at: string; state: string; error: string | null }[];
};

const IMPORT_MODES: { value: CalendarSettings["import_mode"]; label: string; hint: string }[] = [
  { value: "matched", label: "Meetings with my contacts and leads", hint: "Only events where someone is already in AIVA." },
  { value: "external", label: "Meetings with anyone outside the company", hint: "Any event with a guest from another domain." },
  { value: "all", label: "All events", hint: "Everything on the calendar you haven't declined." },
];

function CalendarPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as unknown as Partial<CalendarExtra>;
  const key = detail.integration.key;
  const vendor = key === "google_calendar" ? "Google Calendar" : "Outlook";
  const { run, busy } = useAction(reload);
  const { save, saving } = useMySettings(detail, setDetail);
  const connected = mine(detail) && extra.connected;
  const calendars = useApi<{ items: { id: string | null; name: string; primary: boolean }[]; selected: string | null }>(connected ? `${MC}/${key}/calendars` : null);
  const initial = useMemo<CalendarSettings>(
    () => ({ push_meetings: true, invites_from_calendar: true, pull_events: true, import_mode: "matched", calendar_id: null, ...(extra.settings ?? {}) }),
    [extra.settings],
  );
  const [form, setForm] = useState<CalendarSettings>(initial);
  const [customMode, setCustomMode] = useState(false);
  useEffect(() => setForm(initial), [initial]);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);

  if (!connected) return null;

  const list = calendars.data?.items?.length ? calendars.data.items : [{ id: null, name: "Primary calendar", primary: true }];
  const showCustom = key === "google_calendar" && (customMode || !list.some((c) => (c.id ?? "") === (form.calendar_id ?? "")));
  const set = <K extends keyof CalendarSettings>(name: K, value: CalendarSettings[K]) => setForm((current) => ({ ...current, [name]: value }));

  return (
    <>
      <Card title="Sync settings" icon={<Settings2 size={17} />}>
        <div className="ui-stack" style={{ gap: 12 }}>
          <ProviderError detail={detail} />
          <div className="settings-rows">
            <div className="settings-row imc-row">
              <div>
                <div className="settings-row-label">Calendar</div>
                <div className="settings-row-hint">Where AIVA adds your meetings and which events it reads.</div>
              </div>
              <div className="settings-row-control">
                {calendars.loading && !calendars.data ? (
                  <span className="ui-spinner" />
                ) : (
                  <select
                    className="ui-select imc-select"
                    aria-label="Calendar"
                    value={showCustom ? "__custom" : form.calendar_id ?? ""}
                    onChange={(event) => {
                      const value = event.target.value;
                      setCustomMode(value === "__custom");
                      if (value !== "__custom") set("calendar_id", value || null);
                    }}
                  >
                    {list.map((c) => (
                      <option key={c.id ?? "primary"} value={c.id ?? ""}>
                        {c.name}
                        {c.primary ? " (primary)" : ""}
                      </option>
                    ))}
                    {key === "google_calendar" ? <option value="__custom">Another calendar (by ID)…</option> : null}
                  </select>
                )}
              </div>
            </div>
            {showCustom ? (
              <div className="settings-row imc-row">
                <div>
                  <div className="settings-row-label">Calendar ID</div>
                  <div className="settings-row-hint">In Google Calendar: the calendar&apos;s Settings → Integrate calendar → Calendar ID.</div>
                </div>
                <div className="settings-row-control">
                  <input
                    className="ui-input imc-select"
                    value={form.calendar_id ?? ""}
                    onChange={(event) => set("calendar_id", event.target.value.trim() || null)}
                    placeholder="team@group.calendar.google.com"
                    aria-label="Calendar ID"
                  />
                </div>
              </div>
            ) : null}
            <SwitchRow
              label="Add meetings I schedule in AIVA"
              hint={`New and changed AIVA meetings you organize appear in ${vendor}; cancelling in AIVA removes them.`}
              checked={form.push_meetings}
              onChange={(next) => set("push_meetings", next)}
            />
            <SwitchRow
              label={`Let ${vendor} send the invitations`}
              hint={`Guests get a normal ${vendor} invite and their replies show up in AIVA. Off: AIVA emails the invitation (.ics) itself.`}
              checked={form.invites_from_calendar}
              disabled={!form.push_meetings}
              onChange={(next) => set("invites_from_calendar", next)}
            />
            <SwitchRow
              label="Bring calendar events into AIVA"
              hint={`Checked every ${extra.sync_every_minutes ?? 10} minutes, from ${extra.window?.past_days ?? 14} days back to ${extra.window?.future_days ?? 90} days ahead. Meetings are linked to the matching contact, account or lead.`}
              checked={form.pull_events}
              onChange={(next) => set("pull_events", next)}
            />
            <div className="settings-row imc-row">
              <div>
                <div className="settings-row-label">Which events</div>
                <div className="settings-row-hint">{IMPORT_MODES.find((m) => m.value === form.import_mode)?.hint}</div>
              </div>
              <div className="settings-row-control">
                <select className="ui-select imc-select" aria-label="Which events" value={form.import_mode} disabled={!form.pull_events} onChange={(event) => set("import_mode", event.target.value as CalendarSettings["import_mode"])}>
                  {IMPORT_MODES.map((mode) => (
                    <option key={mode.value} value={mode.value}>
                      {mode.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
          <div className="ui-actions">
            <button type="button" className="ui-btn ui-btn-primary" disabled={!dirty || saving} onClick={() => void save({ ...form }, "Calendar settings saved.")}>
              {saving ? <span className="ui-spinner" /> : null} Save settings
            </button>
            {dirty ? (
              <button type="button" className="ui-btn ui-btn-ghost" onClick={() => setForm(initial)}>
                Discard
              </button>
            ) : null}
          </div>
        </div>
      </Card>

      <Card
        title="Sync"
        icon={<CalendarSync size={17} />}
        actions={
          <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void run("sync", `${MC}/${key}/sync`)}>
            {busy === "sync" ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Sync now
          </button>
        }
      >
        <div className="ui-stack" style={{ gap: 14 }}>
          <div className="imc-stats">
            <Stat label="Upcoming meetings linked" value={number(extra.stats?.upcoming_linked ?? 0)} />
            <Stat label="Brought in" value={number(extra.stats?.created_total ?? 0)} />
            <Stat label="Changes picked up" value={number((extra.stats?.updated_total ?? 0) + (extra.stats?.cancelled_total ?? 0))} />
            <Stat label="Last synced" value={detail.connection?.last_sync_at ? relativeTime(detail.connection.last_sync_at) : "Soon"} />
          </div>
          {extra.problems?.length ? (
            <div className="imc-problems">
              <div className="settings-row-label">
                <AlertTriangle size={14} style={{ verticalAlign: -2, color: "var(--warning)" }} /> Meetings that didn&apos;t reach your calendar
              </div>
              <ul>
                {extra.problems.map((problem) => (
                  <li key={problem.id}>
                    <div style={{ minWidth: 0 }}>
                      <Link href={`/meetings?open=${problem.id}`} className="imc-problem-title ui-truncate">
                        {problem.title}
                      </Link>
                      <div className="ui-small ui-muted">
                        {dateTime(problem.start_at)} · {problem.state === "pending" ? "AIVA will try again" : "Gave up"}
                        {problem.error ? ` · ${problem.error}` : ""}
                      </div>
                    </div>
                    <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void run(`retry-${problem.id}`, `${MC}/meetings/${problem.id}/retry`)}>
                      {busy === `retry-${problem.id}` ? <span className="ui-spinner" /> : null} Retry
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="int-section-note">Everything is in sync.</p>
          )}
        </div>
      </Card>
    </>
  );
}

// ---------------------------------------------------------------- Google Meet

type MeetExtra = { calendar_connected: boolean; calendar_account: string | null; upcoming_with_link: number };

function GoogleMeetPanel({ detail }: PanelProps) {
  const extra = detail.extra as unknown as Partial<MeetExtra>;
  if (!detail.enabled) return null;
  return (
    <Card title="Google Meet links" icon={<Video size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <ProviderError detail={detail} />
        {extra.calendar_connected ? (
          <>
            <p className="int-section-note">
              When you schedule a meeting, choose <strong>Google Meet</strong> as the video call. AIVA creates the event in {extra.calendar_account ?? "your Google Calendar"} with a Meet link and puts the link on the meeting and in the invitation.
            </p>
            <div className="imc-stats">
              <Stat label="Upcoming meetings with a Meet link" value={number(extra.upcoming_with_link ?? 0)} />
            </div>
          </>
        ) : (
          <Alert tone="warning">
            Meet links are created through your Google Calendar. <Link href="/integrations/google_calendar">Connect Google Calendar</Link> first.
          </Alert>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- Zoom

type ZoomExtra = {
  settings: { waiting_room: boolean; join_before_host: boolean; mute_upon_entry: boolean };
  upcoming: { id: string; title: string; start_at: string; join_url: string | null }[];
};

function ZoomPanel({ detail, setDetail }: PanelProps) {
  const extra = detail.extra as unknown as Partial<ZoomExtra>;
  const { save, saving } = useMySettings(detail, setDetail);
  if (!mine(detail)) return null;
  const settings = extra.settings ?? { waiting_room: true, join_before_host: false, mute_upon_entry: true };
  return (
    <>
      <Card title="New Zoom meetings" icon={<Settings2 size={17} />}>
        <div className="ui-stack" style={{ gap: 12 }}>
          <ProviderError detail={detail} />
          <p className="int-section-note">
            Choose <strong>Zoom</strong> as the video call when scheduling. AIVA creates the meeting in {detail.connection?.account_label ?? "your Zoom account"}, and keeps it in step when you reschedule or cancel.
          </p>
          <div className="settings-rows">
            <SwitchRow label="Waiting room" hint="Guests wait until you let them in." checked={settings.waiting_room} disabled={saving} onChange={(next) => void save({ waiting_room: next })} />
            <SwitchRow label="Let guests join before you" checked={settings.join_before_host} disabled={saving} onChange={(next) => void save({ join_before_host: next })} />
            <SwitchRow label="Mute guests when they join" checked={settings.mute_upon_entry} disabled={saving} onChange={(next) => void save({ mute_upon_entry: next })} />
          </div>
        </div>
      </Card>
      <Card title="Your upcoming Zoom meetings" icon={<CalendarClock size={17} />}>
        {extra.upcoming?.length ? (
          <ul className="imc-list">
            {extra.upcoming.map((meeting) => (
              <li key={meeting.id}>
                <div style={{ minWidth: 0 }}>
                  <Link href={`/meetings?open=${meeting.id}`} className="imc-problem-title ui-truncate">
                    {meeting.title}
                  </Link>
                  <div className="ui-small ui-muted">{dateTime(meeting.start_at)}</div>
                </div>
                {meeting.join_url ? (
                  <a className="ui-btn ui-btn-sm" href={meeting.join_url} target="_blank" rel="noopener noreferrer">
                    <Video size={14} /> Join
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="int-section-note">No upcoming Zoom meetings yet.</p>
        )}
      </Card>
    </>
  );
}

// ---------------------------------------------------------------- Microsoft Teams

type TeamsExtra = {
  events: string[];
  event_options: { key: string; label: string }[];
  channel: { active: boolean; last_used_at: string | null; last_error: string | null };
  recent: { id: string; event: string; title: string | null; text: string | null; status: string; error: string | null; at: string }[];
  week: { sent: number; failed: number; pending: number };
  meeting_links: { outlook_calendar_enabled: boolean; outlook_calendar_connected: boolean; account: string | null };
};

const POST_STATUS: Record<string, { label: string; tone: "success" | "danger" | "warning" | "neutral" }> = {
  sent: { label: "Posted", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  pending: { label: "Queued", tone: "warning" },
};

function TeamsPanel({ detail, setDetail }: PanelProps) {
  const extra = detail.extra as unknown as Partial<TeamsExtra>;
  const toast = useToast();
  const [events, setEvents] = useState<string[]>(extra.events ?? []);
  const [saving, setSaving] = useState(false);
  useEffect(() => setEvents(extra.events ?? []), [extra.events]);
  if (!detail.enabled) return null;
  const connected = Boolean(detail.connection);
  const dirty = JSON.stringify([...events].sort()) !== JSON.stringify([...(extra.events ?? [])].sort());

  async function saveEvents() {
    setSaving(true);
    try {
      setDetail(await api.put<IntegrationDetail>("/integration-hub/microsoft_teams/settings", { config: { events } }));
      toast("Teams alerts saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSaving(false);
    }
  }

  const links = extra.meeting_links;
  return (
    <>
      {connected ? (
        <Card title="What to post" icon={<MessagesSquare size={17} />}>
          <div className="ui-stack" style={{ gap: 12 }}>
            <ProviderError detail={detail} />
            {extra.channel?.last_error ? <Alert tone="warning">The last post failed: {extra.channel.last_error}</Alert> : null}
            <div className="check-grid imc-events">
              {(extra.event_options ?? []).map((option) => (
                <label key={option.key} className="ui-check">
                  <input
                    type="checkbox"
                    checked={events.includes(option.key)}
                    disabled={!detail.can_manage}
                    onChange={() => setEvents((current) => (current.includes(option.key) ? current.filter((e) => e !== option.key) : [...current, option.key]))}
                  />{" "}
                  {option.label}
                </label>
              ))}
            </div>
            <p className="int-section-note">Workflows can also post to Teams with the “Post to chat” action.</p>
            {detail.can_manage ? (
              <div>
                <button type="button" className="ui-btn ui-btn-primary" disabled={!dirty || saving} onClick={() => void saveEvents()}>
                  {saving ? <span className="ui-spinner" /> : null} Save alerts
                </button>
              </div>
            ) : null}
          </div>
        </Card>
      ) : null}

      {connected ? (
        <Card title="Recent posts" icon={<Send size={17} />} flush>
          <div className="imc-stats imc-stats-padded">
            <Stat label="Posted this week" value={number(extra.week?.sent ?? 0)} />
            <Stat label="Failed" value={number(extra.week?.failed ?? 0)} />
            <Stat label="Queued" value={number(extra.week?.pending ?? 0)} />
          </div>
          {extra.recent?.length ? (
            <ul className="imc-list imc-list-flush">
              {extra.recent.map((post) => (
                <li key={post.id}>
                  <div style={{ minWidth: 0 }}>
                    <div className="imc-problem-title ui-truncate">{post.title ?? post.event}</div>
                    <div className="ui-small ui-muted ui-truncate">
                      {relativeTime(post.at)}
                      {post.text ? ` · ${post.text}` : ""}
                      {post.error ? ` · ${post.error}` : ""}
                    </div>
                  </div>
                  <Badge tone={POST_STATUS[post.status]?.tone ?? "neutral"}>{POST_STATUS[post.status]?.label ?? post.status}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <p className="int-section-note" style={{ padding: "0 22px 18px" }}>
              Nothing posted yet.
            </p>
          )}
        </Card>
      ) : null}

      <Card title="Teams meeting links" icon={<Video size={17} />}>
        {links?.outlook_calendar_connected ? (
          <p className="int-section-note">
            Choose <strong>Microsoft Teams</strong> as the video call when scheduling. AIVA creates the Teams meeting through your Outlook Calendar ({links.account}). Teams links need a Microsoft 365 work or school account.
          </p>
        ) : (
          <div className="ui-stack" style={{ gap: 10 }}>
            <p className="int-section-note">
              Teams meeting links are created through each person&apos;s Outlook Calendar, so the meeting is in their calendar too.
            </p>
            {links?.outlook_calendar_enabled ? (
              <div>
                <Link className="ui-btn" href="/integrations/outlook_calendar">
                  Connect Outlook Calendar
                </Link>
              </div>
            ) : (
              <Alert tone="info">
                {detail.can_manage ? (
                  <>
                    Turn on <Link href="/integrations/outlook_calendar">Outlook Calendar</Link> so people can connect it.
                  </>
                ) : (
                  "Ask an admin to turn on Outlook Calendar."
                )}
              </Alert>
            )}
          </div>
        )}
      </Card>
    </>
  );
}

export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  gmail: MailPanel,
  outlook_mail: MailPanel,
  google_calendar: CalendarPanel,
  outlook_calendar: CalendarPanel,
  google_meet: GoogleMeetPanel,
  zoom: ZoomPanel,
  microsoft_teams: TeamsPanel,
};
