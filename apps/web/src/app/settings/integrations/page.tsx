"use client";

import Link from "next/link";
import { BrainCircuit, CalendarDays, CreditCard, Mail, MessageSquare, Video } from "lucide-react";
import { useEffect, useState } from "react";

import { CopyField } from "@/components/copy-field";
import { PlanChip, UpgradeGate } from "@/components/plan";
import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Mailbox {
  id: string;
  provider: "gmail" | "outlook" | "smtp";
  email_address: string;
  display_name: string | null;
  smtp_host: string;
  smtp_port: number;
  smtp_security: string;
  imap_host: string | null;
  imap_port: number | null;
  username: string;
  status: "connected" | "error";
  last_error: string | null;
  last_sync_at: string | null;
  sync_enabled: boolean;
}

interface ChatState {
  connected: boolean;
  is_active: boolean;
  events: string[];
  last_used_at: string | null;
  last_error: string | null;
}

interface Overview {
  can_manage: boolean;
  mailbox: Mailbox | null;
  mailbox_presets: Record<string, { label: string; smtp_host: string; smtp_port: number; smtp_security: string; imap_host: string; imap_port: number; help: string }>;
  server_mail: boolean;
  slack: ChatState;
  teams: ChatState;
  chat_events: { key: string; label: string }[];
  zoom: { personal_link: string | null };
  calendar: { url: string | null; last_accessed_at: string | null };
  google_ads: { connected: boolean };
  quickbooks: { available: boolean };
  stripe: { configured: boolean };
  ai: { configured: boolean; model: string | null };
  features: Record<string, boolean>;
}

export default function IntegrationsPage() {
  return (
    <SettingsShell>
      <Integrations />
    </SettingsShell>
  );
}

function Integrations() {
  const data = useApi<Overview>("/integrations");
  const d = data.data;
  const reload = () => void data.reload();
  return (
    <>
      <PageHeader title="Integrations" description="Connect your mailbox, calendar, Zoom, Slack and Microsoft Teams. Each person connects their own mailbox, calendar and Zoom link." />
      {data.error ? <Alert>{data.error}</Alert> : null}
      {!d ? (
        <Card>
          <LoadingRows rows={8} />
        </Card>
      ) : (
        <div className="ui-stack">
          <MailboxCard data={d} onChanged={reload} />
          <div className="ui-grid ui-grid-2">
            <CalendarCard data={d} onChanged={reload} />
            <ZoomCard data={d} />
          </div>
          <div className="ui-grid ui-grid-2">
            <ChatCard kind="slack" data={d} onChanged={reload} />
            <ChatCard kind="teams" data={d} onChanged={reload} />
          </div>
          <Card title="More connections">
            <div className="integration-list">
              <IntegrationRow icon={<MessageSquare size={16} />} name="Google Ads" status={d.google_ads.connected ? "Connected" : "Not connected"} href="/marketing/google-ads" feature="google_ads" />
              <IntegrationRow icon={<CreditCard size={16} />} name="QuickBooks Online" status={d.quickbooks.available ? "CSV export ready" : "Enterprise"} href="/invoices" feature="quickbooks" />
              <IntegrationRow icon={<CreditCard size={16} />} name="Stripe billing" status={d.stripe.configured ? "Card payments on" : "Demo billing (no card needed)"} href="/settings/billing" />
              <IntegrationRow icon={<BrainCircuit size={16} />} name="Claude AI" status={d.ai.configured ? `Connected · ${d.ai.model}` : "Built-in rules (add ANTHROPIC_API_KEY to use Claude)"} href="/ai" />
              <IntegrationRow icon={<MessageSquare size={16} />} name="REST API" status={d.features.api_access ? "Available" : "Starter and up"} href="/settings/api-keys" feature="api_access" />
            </div>
          </Card>
        </div>
      )}
    </>
  );
}

function IntegrationRow({ icon, name, status, href, feature }: { icon: React.ReactNode; name: string; status: string; href: string; feature?: string }) {
  return (
    <Link href={href} className="integration-row">
      <span className="type-icon">{icon}</span>
      <strong>{name}</strong>
      {feature ? <PlanChip feature={feature} /> : null}
      <span className="ui-spacer" />
      <span className="ui-muted ui-small">{status}</span>
    </Link>
  );
}

function MailboxCard({ data, onChanged }: { data: Overview; onChanged: () => void }) {
  const toast = useToast();
  const mailbox = data.mailbox;
  const [editing, setEditing] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [busy, setBusy] = useState(false);

  async function test() {
    setBusy(true);
    try {
      toast((await api.post<{ message: string }>("/integrations/mailbox/test")).message);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function sync() {
    setBusy(true);
    try {
      const result = await api.post<{ imported: number; checked: number }>("/integrations/mailbox/sync");
      toast(`Checked ${result.checked} messages; logged ${result.imported} new emails from your contacts.`);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api.del("/integrations/mailbox");
      toast("Mailbox disconnected.");
      setDisconnecting(false);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title={
        <>
          Email (Gmail, Outlook or any mailbox) <PlanChip feature="mailbox_sync" />
        </>
      }
      icon={<Mail size={16} />}
    >
      <UpgradeGate feature="mailbox_sync" compact>
        {mailbox ? (
          <div className="ui-stack">
            <div className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
              <strong>{mailbox.email_address}</strong>
              <Badge tone={mailbox.status === "connected" ? "success" : "danger"}>{mailbox.status}</Badge>
              <span className="ui-muted ui-small">
                {data.mailbox_presets[mailbox.provider]?.label} · {mailbox.sync_enabled ? `syncs inbox${mailbox.last_sync_at ? `, last ${relativeTime(mailbox.last_sync_at)}` : ""}` : "sending only"}
              </span>
            </div>
            {mailbox.last_error ? <Alert>{mailbox.last_error}</Alert> : null}
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Emails you send from AIVA go out from this address. Replies from your leads and contacts are logged on their timeline every few minutes.
            </p>
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void test()}>
                Send a test email
              </button>
              {mailbox.sync_enabled && mailbox.imap_host ? (
                <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void sync()}>
                  Sync now
                </button>
              ) : null}
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => setEditing(true)}>
                Change settings
              </button>
              <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => setDisconnecting(true)}>
                Disconnect
              </button>
            </div>
          </div>
        ) : (
          <div className="ui-stack">
            <p className="ui-muted" style={{ margin: 0 }}>
              Send email from your own address and log replies automatically.
              {data.server_mail ? " Until you connect, AIVA sends from the workspace's email server." : " Until you connect, emails are saved in AIVA but not delivered."}
            </p>
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing(true)}>
                Connect a mailbox
              </button>
            </div>
          </div>
        )}
      </UpgradeGate>
      <MailboxForm
        open={editing}
        current={mailbox}
        presets={data.mailbox_presets}
        onClose={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          onChanged();
        }}
      />
      <ConfirmDialog
        open={disconnecting}
        title="Disconnect your mailbox?"
        message="AIVA stops sending from this address and stops logging replies. Emails already logged stay."
        confirmLabel="Disconnect"
        busy={busy}
        onConfirm={() => void disconnect()}
        onClose={() => setDisconnecting(false)}
      />
    </Card>
  );
}

function MailboxForm({
  open,
  current,
  presets,
  onClose,
  onSaved,
}: {
  open: boolean;
  current: Mailbox | null;
  presets: Overview["mailbox_presets"];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [provider, setProvider] = useState<"gmail" | "outlook" | "smtp">("gmail");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [security, setSecurity] = useState("starttls");
  const [imapHost, setImapHost] = useState("");
  const [imapPort, setImapPort] = useState("993");
  const [sync, setSync] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setProvider(current?.provider ?? "gmail");
    setEmail(current?.email_address ?? "");
    setPassword("");
    setSmtpHost(current?.smtp_host ?? "");
    setSmtpPort(String(current?.smtp_port ?? 587));
    setSecurity(current?.smtp_security ?? "starttls");
    setImapHost(current?.imap_host ?? "");
    setImapPort(String(current?.imap_port ?? 993));
    setSync(current?.sync_enabled ?? true);
    setError(null);
  }, [open, current]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.put("/integrations/mailbox", {
        provider,
        email_address: email.trim(),
        password,
        smtp_host: provider === "smtp" ? smtpHost.trim() : null,
        smtp_port: provider === "smtp" ? Number(smtpPort) : null,
        smtp_security: provider === "smtp" ? security : null,
        imap_host: provider === "smtp" ? imapHost.trim() || null : null,
        imap_port: provider === "smtp" ? Number(imapPort) : null,
        sync_enabled: sync,
      });
      toast(`Connected ${email.trim()}.`);
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const preset = presets[provider];

  return (
    <Modal
      open={open}
      title={current ? "Mailbox settings" : "Connect a mailbox"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !email.trim() || !password} onClick={() => void save()}>
            {busy ? "Checking…" : "Connect"}
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="chip-list">
          {(["gmail", "outlook", "smtp"] as const).map((key) => (
            <button key={key} type="button" className={`chip${provider === key ? " on" : ""}`} aria-pressed={provider === key} onClick={() => setProvider(key)}>
              {presets[key]?.label ?? key}
            </button>
          ))}
        </div>
        {preset?.help ? <Alert tone="info">{preset.help}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Email address" required>
            <input className="ui-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </Field>
          <Field label={provider === "smtp" ? "Password" : "App password"} required hint="Stored encrypted. Used only to send and read mail for AIVA.">
            <input className="ui-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </Field>
        </div>
        {provider === "smtp" ? (
          <div className="ui-form-grid">
            <Field label="SMTP server" required>
              <input className="ui-input" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} placeholder="smtp.example.com" />
            </Field>
            <Field label="SMTP port">
              <input className="ui-input" type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} />
            </Field>
            <Field label="Security">
              <select className="ui-select" value={security} onChange={(e) => setSecurity(e.target.value)}>
                <option value="starttls">STARTTLS</option>
                <option value="ssl">SSL/TLS</option>
                <option value="none">None</option>
              </select>
            </Field>
            <Field label="IMAP server (for logging replies)">
              <input className="ui-input" value={imapHost} onChange={(e) => setImapHost(e.target.value)} placeholder="imap.example.com" />
            </Field>
            <Field label="IMAP port">
              <input className="ui-input" type="number" value={imapPort} onChange={(e) => setImapPort(e.target.value)} />
            </Field>
          </div>
        ) : null}
        <label className="ui-check">
          <input type="checkbox" checked={sync} onChange={(e) => setSync(e.target.checked)} /> Log emails from my leads and contacts on their timeline
        </label>
      </div>
    </Modal>
  );
}

function CalendarCard({ data, onChanged }: { data: Overview; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      await api.post("/integrations/calendar");
      toast("Calendar link ready. Subscribe to it from your calendar app.");
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del("/integrations/calendar");
      toast("Calendar link turned off.");
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title={
        <>
          Calendar sync <PlanChip feature="calendar_sync" />
        </>
      }
      icon={<CalendarDays size={16} />}
    >
      <UpgradeGate feature="calendar_sync" compact>
        <div className="ui-stack">
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Your AIVA meetings and tasks with due times, in Google Calendar, Outlook or Apple Calendar. Calendar apps refresh subscribed calendars every few hours.
          </p>
          {data.calendar.url ? (
            <>
              <CopyField value={data.calendar.url} label="Copy calendar link" />
              <span className="ui-muted ui-small">
                {data.calendar.last_accessed_at ? `Last fetched ${relativeTime(data.calendar.last_accessed_at)}` : "Not fetched yet"} · Google Calendar: Other calendars → From URL. Outlook: Add
                calendar → Subscribe from web.
              </span>
              <div className="ui-row" style={{ gap: 8 }}>
                <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void create()}>
                  New link
                </button>
                <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy} onClick={() => void remove()}>
                  Turn off
                </button>
              </div>
            </>
          ) : (
            <button type="button" className="ui-btn ui-btn-primary" style={{ width: "fit-content" }} disabled={busy} onClick={() => void create()}>
              Create my calendar link
            </button>
          )}
        </div>
      </UpgradeGate>
    </Card>
  );
}

function ZoomCard({ data }: { data: Overview }) {
  const toast = useToast();
  const [link, setLink] = useState(data.zoom.personal_link ?? "");

  async function save() {
    try {
      await api.put("/integrations/zoom", { personal_link: link || null });
      toast(link ? "Zoom link saved. It's added to meetings you schedule." : "Zoom link removed.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <Card
      title={
        <>
          Zoom <PlanChip feature="chat_integrations" />
        </>
      }
      icon={<Video size={16} />}
    >
      <UpgradeGate feature="chat_integrations" compact>
        <div className="ui-stack">
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Your personal meeting link is added to every meeting you schedule in AIVA and to the invite.
          </p>
          <Field label="Personal meeting link">
            <input className="ui-input" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://us02web.zoom.us/j/1234567890" />
          </Field>
          <button type="button" className="ui-btn" style={{ width: "fit-content" }} disabled={link === (data.zoom.personal_link ?? "")} onClick={() => void save()}>
            Save
          </button>
        </div>
      </UpgradeGate>
    </Card>
  );
}

function ChatCard({ kind, data, onChanged }: { kind: "slack" | "teams"; data: Overview; onChanged: () => void }) {
  const toast = useToast();
  const state = data[kind];
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(state.events);
  const [busy, setBusy] = useState(false);
  const name = kind === "slack" ? "Slack" : "Microsoft Teams";

  useEffect(() => setEvents(state.events), [state.events]);

  async function save() {
    setBusy(true);
    try {
      await api.put(`/integrations/chat/${kind}`, { webhook_url: url || null, events, is_active: true });
      toast(`${name} saved.`);
      setUrl("");
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    try {
      toast((await api.post<{ message: string }>(`/integrations/chat/${kind}/test`)).message);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api.del(`/integrations/chat/${kind}`);
      toast(`${name} disconnected.`);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title={
        <>
          {name} <PlanChip feature="chat_integrations" />
        </>
      }
      icon={<MessageSquare size={16} />}
      actions={state.connected ? <Badge tone={state.last_error ? "danger" : "success"}>{state.last_error ? "Error" : "Connected"}</Badge> : null}
    >
      <UpgradeGate feature="chat_integrations" compact>
        <div className="ui-stack">
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            {kind === "slack"
              ? "Post alerts to a channel. In Slack, add the Incoming Webhooks app to the channel and paste its URL."
              : "Post alerts to a channel. In Teams, open the channel → Workflows → “Post to a channel when a webhook request is received”, and paste the URL."}
          </p>
          {state.last_error ? <Alert>{state.last_error}</Alert> : null}
          {data.can_manage ? (
            <>
              <Field label={state.connected ? "Replace webhook URL (optional)" : "Incoming webhook URL"}>
                <input className="ui-input ui-mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={kind === "slack" ? "https://hooks.slack.com/services/…" : "https://…"} />
              </Field>
              <Field label="Send an alert when">
                <div className="chip-list">
                  {data.chat_events.map((event) => {
                    const on = events.includes(event.key);
                    return (
                      <button key={event.key} type="button" className={`chip${on ? " on" : ""}`} aria-pressed={on} onClick={() => setEvents(on ? events.filter((e) => e !== event.key) : [...events, event.key])}>
                        {event.label}
                      </button>
                    );
                  })}
                </div>
              </Field>
              <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy || (!state.connected && !url.trim())} onClick={() => void save()}>
                  {state.connected ? "Save" : "Connect"}
                </button>
                {state.connected ? (
                  <>
                    <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void test()}>
                      Send a test
                    </button>
                    <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy} onClick={() => void disconnect()}>
                      Disconnect
                    </button>
                  </>
                ) : null}
              </div>
              {state.last_used_at ? <span className="ui-muted ui-small">Last message {relativeTime(state.last_used_at)}</span> : null}
            </>
          ) : (
            <span className="ui-muted ui-small">{state.connected ? "Connected by an admin." : "Ask an admin to connect it."}</span>
          )}
        </div>
      </UpgradeGate>
    </Card>
  );
}
