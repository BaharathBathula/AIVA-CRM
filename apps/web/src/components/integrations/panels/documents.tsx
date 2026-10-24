"use client";

import Link from "next/link";
import { CalendarCheck, Cloud, ExternalLink, FolderOpen, KeyRound, RefreshCw, Settings2, ShieldCheck, Signature, Webhook } from "lucide-react";
import { useState, type ComponentType, type FormEvent } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { NumberInput, SettingRow, Toggle, useMembers } from "@/components/settings-kit";
import { Alert, Badge, Card, Kpi } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

import { CredentialsForm, UrlRow } from "../common";
import { FileBrowser, type CalendlyLinks, type FileLink, type FileProvider, type SignatureRequestItem } from "../record/documents-shared";
import { SignatureRow } from "../record/RecordSignatures";
import type { IntegrationDetail, PanelProps } from "../types";

/** Saves workspace options for an integration and hands back the refreshed page data. */
async function saveSettings(key: string, config: Record<string, unknown>) {
  return api.put<IntegrationDetail>(`/integration-hub/${key}/settings`, { config });
}

// ===========================================================================
// Google Drive, OneDrive, Dropbox
// ===========================================================================

type FilesExtra = {
  links_total: number;
  links_mine: number;
  links_this_week: number;
  allow_copy: boolean;
  max_mb: number;
  recent: FileLink[];
};

function CloudFilesPanel({ detail, setDetail }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as Partial<FilesExtra> & { error?: string };
  const item = detail.integration;
  const [busy, setBusy] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  if (!detail.enabled) return null;

  async function setAllowCopy(value: boolean) {
    setBusy(true);
    try {
      setDetail(await saveSettings(item.key, { allow_copy: value }));
      toast(value ? "People can copy files into Documents." : "Copying into Documents is off.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const provider: FileProvider = {
    key: item.key as FileProvider["key"],
    name: item.name,
    vendor: item.vendor,
    color: item.color,
    monogram: item.monogram,
    available: detail.available,
    enabled: detail.enabled,
    connected: detail.connection?.status === "connected",
    status: detail.connection?.status ?? "not_connected",
    account_label: detail.connection?.account_label ?? null,
    allow_copy: extra.allow_copy ?? true,
  };

  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      <Card title={`${item.name} in AIVA`} icon={<Cloud size={17} />}>
        <div className="ui-stack" style={{ gap: 16 }}>
          <div className="settings-kpis three">
            <Kpi label="Files attached" value={extra.links_total ?? 0} />
            <Kpi label="Attached by you" value={extra.links_mine ?? 0} />
            <Kpi label="This week" value={extra.links_this_week ?? 0} />
          </div>
          {extra.recent?.length ? (
            <div>
              <div className="settings-row-label" style={{ marginBottom: 6 }}>Recently attached</div>
              <ul className="mini-list">
                {extra.recent.map((link) => (
                  <li key={link.id}>
                    <FolderOpen size={16} className="mini-icon" aria-hidden />
                    <div className="mini-main">
                      {link.web_url ? (
                        <a className="mini-title" href={link.web_url} target="_blank" rel="noopener noreferrer">
                          {link.name}
                        </a>
                      ) : (
                        <span className="mini-title">{link.name}</span>
                      )}
                      <span className="mini-sub">
                        {link.record ? (
                          <>
                            on <Link href={link.record.href}>{link.record.label}</Link> ·{" "}
                          </>
                        ) : null}
                        {link.added_by ? `${link.added_by} · ` : ""}
                        {relativeTime(link.added_at)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Nothing attached yet. Open any account, contact, deal or quote and use <strong>Cloud files → Attach</strong>.
            </p>
          )}
          <div className="idoc-panel-actions">
            {provider.connected ? (
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => setBrowsing((v) => !v)} aria-expanded={browsing}>
                <FolderOpen size={14} /> {browsing ? "Hide your files" : "Browse your files"}
              </button>
            ) : null}
          </div>
          {browsing ? <FileBrowser providers={[provider]} returnTo={`/integrations/${item.key}`} /> : null}
        </div>
      </Card>
      {detail.can_manage ? (
        <Card title="Workspace options" icon={<Settings2 size={17} />}>
          <SettingRow
            label="Allow copying into AIVA Documents"
            hint={`People can save a copy of a ${item.vendor} file in Documents (up to ${extra.max_mb ?? 25} MB). Turn off to keep files only in ${item.vendor}.`}
          >
            <Toggle checked={extra.allow_copy ?? true} onChange={(value) => void setAllowCopy(value)} disabled={busy} label="Allow copying into Documents" />
          </SettingRow>
        </Card>
      ) : null}
    </>
  );
}

// ===========================================================================
// DocuSign
// ===========================================================================

type DocuSignSettings = {
  save_signed_copy: boolean;
  include_certificate: boolean;
  mark_quote_accepted: boolean;
  notify_sender: boolean;
  reminder_days: number;
  expire_days: number;
};

type DocuSignExtra = {
  counts: Record<string, number>;
  recent: SignatureRequestItem[];
  settings: DocuSignSettings;
  connect: { push: boolean; webhook_url: string | null; hmac_configured: boolean };
  account: { id: string | null; environment: string | null; email: string | null };
};

function DocuSignPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as Partial<DocuSignExtra> & { error?: string };
  if (!detail.enabled) return null;
  const counts = extra.counts ?? {};
  const waiting = (counts.sent ?? 0) + (counts.delivered ?? 0);
  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      <Card title="Signature requests" icon={<Signature size={17} />}>
        <div className="ui-stack" style={{ gap: 16 }}>
          <div className="settings-kpis three">
            <Kpi label="Waiting for signatures" value={waiting} />
            <Kpi label="Signed" value={counts.completed ?? 0} tone={counts.completed ? "success" : undefined} />
            <Kpi label="Declined or voided" value={(counts.declined ?? 0) + (counts.voided ?? 0)} tone={counts.declined ? "danger" : undefined} />
          </div>
          {extra.recent?.length ? (
            <ul className="idoc-requests">
              {extra.recent.map((item) => (
                <SignatureRow key={item.id} item={item} showRecord onChanged={() => void reload()} />
              ))}
            </ul>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Nothing sent yet. Use <strong>Send for signature</strong> on a deal, quote, account, contact or document.
            </p>
          )}
          {extra.account?.id ? (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              DocuSign account {extra.account.id} · {extra.account.environment === "production" ? "production" : "developer sandbox"}
              {extra.account.email ? ` · signed in as ${extra.account.email}` : ""}
            </p>
          ) : null}
        </div>
      </Card>
      {detail.connection ? <DocuSignUpdates detail={detail} connect={extra.connect} onSaved={reload} /> : null}
      {detail.can_manage && extra.settings ? <DocuSignSettingsCard detail={detail} initial={extra.settings} setDetail={setDetail} /> : null}
    </>
  );
}

function DocuSignUpdates({ detail, connect, onSaved }: { detail: IntegrationDetail; connect?: DocuSignExtra["connect"]; onSaved: () => Promise<void> }) {
  const toast = useToast();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(value: string | null) {
    setBusy(true);
    try {
      await api.put("/e-signatures/connect-key", { hmac_key: value });
      toast(value ? "HMAC key saved." : "HMAC key removed.");
      setKey("");
      await onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Status updates" icon={<Webhook size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        {connect?.push ? (
          <>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              Each envelope asks DocuSign Connect to push changes here as they happen. AIVA always reads the envelope back from DocuSign before
              changing anything, and also checks open envelopes every 15 minutes.
            </p>
            <UrlRow label="AIVA's Connect address" value={connect.webhook_url} />
          </>
        ) : (
          <Alert tone="info">
            AIVA checks open envelopes with DocuSign every 15 minutes. Run AIVA on a public https address and DocuSign will push changes the moment they happen.
          </Alert>
        )}
        {detail.can_manage ? (
          <form
            className="idoc-inline-form"
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              void save(key.trim());
            }}
          >
            <SettingRow
              label={
                <span className="ui-row" style={{ gap: 8 }}>
                  Connect HMAC key {connect?.hmac_configured ? <Badge tone="success" dot>Required on pushes</Badge> : <Badge>Not set</Badge>}
                </span>
              }
              hint={
                <>
                  In DocuSign eSignature Admin → Connect → Connect Keys, add a key and paste it here. Pushes without a matching
                  <code> X-DocuSign-Signature</code> are then refused.
                </>
              }
              stacked
            >
              <input className="ui-input sm" type="password" autoComplete="off" placeholder={connect?.hmac_configured ? "Paste a new key to replace it" : "Paste the HMAC key"} value={key} onChange={(event) => setKey(event.target.value)} style={{ maxWidth: 360 }} aria-label="Connect HMAC key" />
              <button type="submit" className="ui-btn ui-btn-sm" disabled={busy || key.trim().length < 16}>
                {busy ? <span className="ui-spinner" /> : <KeyRound size={14} />} Save key
              </button>
              {connect?.hmac_configured ? (
                <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy} onClick={() => void save(null)}>
                  Remove
                </button>
              ) : null}
            </SettingRow>
          </form>
        ) : null}
      </div>
    </Card>
  );
}

function DocuSignSettingsCard({ detail, initial, setDetail }: { detail: IntegrationDetail; initial: DocuSignSettings; setDetail: (d: IntegrationDetail) => void }) {
  const toast = useToast();
  const [values, setValues] = useState<DocuSignSettings>(initial);
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(values) !== JSON.stringify(initial);
  const set = <K extends keyof DocuSignSettings>(name: K, value: DocuSignSettings[K]) => setValues((current) => ({ ...current, [name]: value }));

  async function save() {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail.integration.key, values));
      toast("DocuSign settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="When something is signed"
      icon={<Settings2 size={17} />}
      footer={
        <div className="ui-actions" style={{ justifyContent: "flex-end" }}>
          {dirty ? (
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => setValues(initial)} disabled={busy}>
              Discard
            </button>
          ) : null}
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => void save()} disabled={busy || !dirty}>
            {busy ? <span className="ui-spinner" /> : null} Save
          </button>
        </div>
      }
    >
      <SettingRow label="Save the signed copy in Documents" hint="The signed PDF is linked to the record (and the quote and deal).">
        <Toggle checked={values.save_signed_copy} onChange={(v) => set("save_signed_copy", v)} label="Save the signed copy" />
      </SettingRow>
      <SettingRow label="Include the certificate of completion" hint="DocuSign's audit page with who signed, when and from where.">
        <Toggle checked={values.include_certificate} onChange={(v) => set("include_certificate", v)} disabled={!values.save_signed_copy} label="Include the certificate" />
      </SettingRow>
      <SettingRow label="Mark signed quotes as accepted" hint="Signed quotes become Accepted, and tracked stock is taken out like any accepted quote.">
        <Toggle checked={values.mark_quote_accepted} onChange={(v) => set("mark_quote_accepted", v)} label="Mark signed quotes accepted" />
      </SettingRow>
      <SettingRow label="Tell the sender" hint="An AIVA notification when it's signed, declined or voided.">
        <Toggle checked={values.notify_sender} onChange={(v) => set("notify_sender", v)} label="Notify the sender" />
      </SettingRow>
      <SettingRow label="Remind signers every" hint="0 uses your DocuSign account's default.">
        <NumberInput value={values.reminder_days} onChange={(v) => set("reminder_days", v ?? 0)} min={0} max={30} suffix="days" label="Reminder interval in days" width={90} />
      </SettingRow>
      <SettingRow label="Expire unsigned requests after" hint="0 uses your DocuSign account's default.">
        <NumberInput value={values.expire_days} onChange={(v) => set("expire_days", v ?? 0)} min={0} max={365} suffix="days" label="Expiry in days" width={90} />
      </SettingRow>
    </Card>
  );
}

// ===========================================================================
// Calendly
// ===========================================================================

type CalendlyExtra = {
  settings: { create_leads: "new" | "never"; lead_owner: string };
  bookings: { last_30_days: number; upcoming: number; recent: { id: string; title: string; start_at: string; status: string; href: string }[] };
  signing: { app_source: "workspace" | "server" | null; workspace_key_set: boolean; server_key_set: boolean };
  auth?: "token" | "oauth";
  webhook: { url: string | null; subscribed: boolean; scope: string | null; state: string | null; checked_at: string | null; verified: boolean } | null;
  links: CalendlyLinks | null;
  links_error?: string;
};

function CalendlyPanel({ detail, setDetail }: PanelProps) {
  const extra = detail.extra as Partial<CalendlyExtra> & { error?: string };
  if (!detail.enabled) return null;
  const connection = detail.connection;
  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      {!connection && detail.can_manage ? (
        <Card title="Or use a personal access token" icon={<KeyRound size={17} />}>
          <div className="ui-stack" style={{ gap: 12 }}>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              No Calendly app? A Calendly admin can create a personal access token (Calendly → Integrations → API &amp; Webhooks) and paste it here.
              AIVA then subscribes to bookings with its own signing key.
            </p>
            <CredentialsForm detail={detail} onSaved={setDetail} submitLabel="Connect with token" />
          </div>
        </Card>
      ) : null}
      {connection ? <CalendlyUpdates detail={detail} extra={extra} setDetail={setDetail} /> : null}
      {connection ? <CalendlyLinksCard extra={extra} /> : null}
      <CalendlyBookings extra={extra} />
      {detail.can_manage && extra.settings ? <CalendlySettings detail={detail} initial={extra.settings} setDetail={setDetail} /> : null}
    </>
  );
}

function CalendlyUpdates({ detail, extra, setDetail }: { detail: IntegrationDetail; extra: Partial<CalendlyExtra>; setDetail: (d: IntegrationDetail) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const webhook = extra.webhook;
  const signing = extra.signing;
  const workspaceApp = extra.auth === "oauth" && signing?.app_source === "workspace";

  async function resubscribe() {
    setBusy("subscribe");
    try {
      setDetail(await api.post<IntegrationDetail>("/calendly/resubscribe"));
      toast("Subscribed to bookings again.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function saveKey(value: string | null) {
    setBusy("key");
    try {
      setDetail(await api.put<IntegrationDetail>("/calendly/signing-key", { signing_key: value }));
      toast(value ? "Signing key saved." : "Signing key removed.");
      setKey("");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const active = webhook?.subscribed && webhook.state === "active";
  return (
    <Card title="Booking updates" icon={<CalendarCheck size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
          <Badge tone={active ? "success" : "warning"} dot>
            {active ? "Receiving bookings" : webhook?.subscribed ? `Subscription ${webhook.state}` : "Not subscribed"}
          </Badge>
          {webhook?.scope ? <Badge>{webhook.scope === "organization" ? "Whole Calendly organization" : "Connected user only"}</Badge> : null}
          <Badge tone={webhook?.verified ? "success" : "warning"}>
            <ShieldCheck size={12} /> {webhook?.verified ? "Signatures checked" : "Each booking double-checked with Calendly"}
          </Badge>
          {extra.auth ? <span className="ui-muted ui-small">{extra.auth === "token" ? "Personal access token" : "Signed in with Calendly"}</span> : null}
        </div>
        {webhook?.scope === "user" ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            The connected Calendly user isn&apos;t an organization admin, so only their own bookings arrive. Reconnect as a Calendly admin to get everyone&apos;s.
          </p>
        ) : null}
        <UrlRow label="Webhook address" value={webhook?.url} hint={webhook?.checked_at ? `Checked ${relativeTime(webhook.checked_at)}` : undefined} />
        {detail.can_manage ? (
          <div>
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => void resubscribe()} disabled={busy !== null}>
              {busy === "subscribe" ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Subscribe again
            </button>
          </div>
        ) : null}
        {detail.can_manage && workspaceApp ? (
          <form
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              void saveKey(key.trim());
            }}
          >
            <SettingRow
              label="Your Calendly app's webhook signing key"
              hint="Calendly shows it with your OAuth app in the developer console. With it saved, AIVA refuses unsigned messages; without it, AIVA reads every booking back from Calendly."
              stacked
            >
              <input className="ui-input sm" type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} placeholder={signing?.workspace_key_set ? "Saved. Paste a new key to replace it" : "Paste the signing key"} style={{ maxWidth: 360 }} aria-label="Webhook signing key" />
              <button type="submit" className="ui-btn ui-btn-sm" disabled={busy !== null || key.trim().length < 16}>
                {busy === "key" ? <span className="ui-spinner" /> : <KeyRound size={14} />} Save key
              </button>
              {signing?.workspace_key_set ? (
                <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy !== null} onClick={() => void saveKey(null)}>
                  Remove
                </button>
              ) : null}
            </SettingRow>
          </form>
        ) : null}
        {extra.auth === "oauth" && signing?.app_source === "server" && !signing.server_key_set ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            The server&apos;s Calendly app has no signing key set (CALENDLY_WEBHOOK_SIGNING_KEY), so AIVA reads each booking back from Calendly before using it.
          </p>
        ) : null}
      </div>
    </Card>
  );
}

function CalendlyLinksCard({ extra }: { extra: Partial<CalendlyExtra> }) {
  const links = extra.links;
  return (
    <Card title="Your scheduling links" icon={<ExternalLink size={17} />}>
      {extra.links_error ? <Alert tone="warning">{extra.links_error}</Alert> : null}
      {links?.items.length ? (
        <ul className="idoc-event-types">
          {links.items.map((item) => (
            <li key={item.uri}>
              <div className="idoc-event-type-head">
                <span className="idoc-event-dot" style={{ background: item.color || "var(--primary)" }} aria-hidden />
                <strong>{item.name}</strong>
                <span className="ui-muted ui-small">
                  {item.duration ? `${item.duration} min` : ""}
                  {item.owner && links.scope !== "mine" ? ` · ${item.owner}` : ""}
                </span>
              </div>
              <CopyField value={item.scheduling_url} label={`Copy the ${item.name} link`} />
            </li>
          ))}
        </ul>
      ) : !extra.links_error ? (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          No active event types found for you in Calendly.
        </p>
      ) : null}
      <p className="ui-muted ui-small" style={{ margin: "10px 0 0" }}>
        On a lead or contact, <strong>Scheduling links</strong> fills in their name and email for them.
      </p>
    </Card>
  );
}

function CalendlyBookings({ extra }: { extra: Partial<CalendlyExtra> }) {
  const bookings = extra.bookings;
  if (!bookings) return null;
  return (
    <Card title="Bookings" icon={<CalendarCheck size={17} />}>
      <div className="ui-stack" style={{ gap: 14 }}>
        <div className="settings-kpis three">
          <Kpi label="Booked in the last 30 days" value={bookings.last_30_days} />
          <Kpi label="Upcoming" value={bookings.upcoming} />
          <Kpi label="All bookings" value={<Link href="/meetings">Meetings</Link>} />
        </div>
        {bookings.recent.length ? (
          <ul className="mini-list">
            {bookings.recent.map((meeting) => (
              <li key={meeting.id}>
                <CalendarCheck size={16} className="mini-icon" aria-hidden />
                <div className="mini-main">
                  <Link className="mini-title" href={meeting.href}>
                    {meeting.title}
                  </Link>
                  <span className="mini-sub">{dateTime(meeting.start_at)}</span>
                </div>
                <Badge tone={meeting.status === "cancelled" ? "neutral" : meeting.status === "scheduled" ? "info" : "success"}>{meeting.status}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            No bookings yet. New Calendly bookings appear here and in Meetings within seconds.
          </p>
        )}
      </div>
    </Card>
  );
}

function CalendlySettings({ detail, initial, setDetail }: { detail: IntegrationDetail; initial: CalendlyExtra["settings"]; setDetail: (d: IntegrationDetail) => void }) {
  const toast = useToast();
  const { members } = useMembers();
  const [values, setValues] = useState(initial);
  const [busy, setBusy] = useState(false);
  const dirty = values.create_leads !== initial.create_leads || values.lead_owner !== initial.lead_owner;

  async function save() {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail.integration.key, values));
      toast("Calendly settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="New people who book"
      icon={<Settings2 size={17} />}
      footer={
        <div className="ui-actions" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => void save()} disabled={busy || !dirty}>
            {busy ? <span className="ui-spinner" /> : null} Save
          </button>
        </div>
      }
    >
      <SettingRow label="Create a lead" hint="Bookings from people who aren't a contact or lead yet. Known people are always linked.">
        <select className="ui-select sm" value={values.create_leads} onChange={(event) => setValues({ ...values, create_leads: event.target.value as "new" | "never" })} aria-label="Create a lead" style={{ width: 220 }}>
          <option value="new">Yes, for new people</option>
          <option value="never">No, just record the meeting</option>
        </select>
      </SettingRow>
      <SettingRow label="Lead owner" hint="Who owns leads created from bookings.">
        <select className="ui-select sm" value={values.lead_owner} onChange={(event) => setValues({ ...values, lead_owner: event.target.value })} aria-label="Lead owner" style={{ width: 220 }} disabled={values.create_leads === "never"}>
          <option value="host">The person who was booked</option>
          <option value="none">Nobody (unassigned)</option>
          {members
            .filter((m) => m.is_active)
            .map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.full_name}
              </option>
            ))}
        </select>
      </SettingRow>
    </Card>
  );
}

export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  google_drive: CloudFilesPanel,
  onedrive: CloudFilesPanel,
  dropbox: CloudFilesPanel,
  docusign: DocuSignPanel,
  calendly: CalendlyPanel,
};
