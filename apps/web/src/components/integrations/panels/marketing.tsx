"use client";

import { ArrowUpRight, ListChecks, Megaphone, Plus, RefreshCw, Rows3, ShieldCheck, Trash2, Zap } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, Field, Kpi } from "@/components/ui";
import { dateTime, number, percent, relativeTime, shortDate } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { UrlRow } from "../common";
import type { IntegrationDetail, PanelProps } from "../types";

/* ------------------------------------------------------------------ shapes of detail.extra */

type Option = { key: string; label: string };
type ListRow = { id: string; name: string; count: number | null };

type Settings = {
  list_id: string | null;
  list_name: string | null;
  record_types: ("contact" | "lead")[];
  lead_statuses: string[];
  contact_tags: string[];
  skip_converted: boolean;
  status_for_new: "subscribed" | "pending" | "transactional";
  subscribe_mode: "add" | "subscribe";
  tags: string[];
  field_map: Record<string, string>;
  events: string[];
  sync_paused: boolean;
};

type SyncResult = { sent?: number; unchanged?: number; skipped?: number; errors?: number; unsubscribed_in_tool?: number; suppressed_in_aiva?: number; activities?: number; events?: number; campaigns?: number };

type SyncInfo = {
  last_run_at: string | null;
  next_run_at: string | null;
  last_error: string | null;
  last_result: SyncResult;
  backlog: boolean;
  caught_up: Record<string, boolean>;
  list_id: string | null;
  counts: { in_audience: number; pending: number; opted_out: number; errors: number };
};

type CampaignRow = {
  id: string;
  title: string;
  status: string | null;
  sent_at: string | null;
  url: string | null;
  stats: { sent?: number; unique_opens?: number; open_rate?: number; clicks?: number; click_rate?: number; unsubscribes?: number; subject?: string };
};

type MarketingExtra = {
  capabilities: { tags?: boolean; status_for_new?: boolean; subscribe_mode?: boolean; events?: boolean; free_fields?: boolean; webhooks?: boolean; campaigns?: boolean; engagement?: boolean };
  list_label: string;
  list_required: boolean;
  builtin_fields: { target: string; source: string }[];
  sources: Option[];
  crm_events: Option[];
  settings: Settings;
  connected: boolean;
  sync?: SyncInfo;
  webhook?: { supported: boolean; active: boolean; error: string | null; note: string | null; public_address: boolean };
  campaigns?: CampaignRow[];
  webhook_url?: string | null;
  error?: string;
};

const LEAD_STATUSES: Option[] = [
  { key: "new", label: "New" },
  { key: "contacted", label: "Contacted" },
  { key: "qualified", label: "Qualified" },
  { key: "nurture", label: "Nurture" },
  { key: "unqualified", label: "Unqualified" },
];

function splitList(value: string): string[] {
  return [...new Set(value.split(",").map((part) => part.trim()).filter(Boolean))];
}

function rate(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : percent(value * 100, value < 0.1 ? 1 : 0);
}

async function saveSettings(key: string, config: Partial<Settings>): Promise<IntegrationDetail> {
  return api.put<IntegrationDetail>(`/integration-hub/${key}/settings`, { config });
}

/* ------------------------------------------------------------------ audience & who syncs */

function AudienceCard({ detail, setDetail }: Pick<PanelProps, "detail" | "setDetail">) {
  const toast = useToast();
  const extra = detail.extra as MarketingExtra;
  const key = detail.integration.key;
  const settings = extra.settings;
  const caps = extra.capabilities;
  const manage = detail.can_manage;
  const lists = useApi<{ items: ListRow[] }>(manage && extra.connected ? `/email-marketing/${key}/lists` : null);
  const [listId, setListId] = useState(settings.list_id ?? "");
  const [types, setTypes] = useState<string[]>(settings.record_types);
  const [statuses, setStatuses] = useState<string[]>(settings.lead_statuses);
  const [contactTags, setContactTags] = useState(settings.contact_tags.join(", "));
  const [skipConverted, setSkipConverted] = useState(settings.skip_converted);
  const [statusForNew, setStatusForNew] = useState(settings.status_for_new);
  const [mode, setMode] = useState(settings.subscribe_mode);
  const [tags, setTags] = useState(settings.tags.join(", "));
  const [paused, setPaused] = useState(settings.sync_paused);
  const [busy, setBusy] = useState(false);
  const items = lists.data?.items ?? [];
  const label = extra.list_label.toLowerCase();

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!types.length) {
      toast("Sync contacts, leads or both.", "danger");
      return;
    }
    setBusy(true);
    try {
      const chosen = items.find((row) => row.id === listId);
      const config: Partial<Settings> = {
        list_id: listId || null,
        list_name: chosen?.name ?? (listId === settings.list_id ? settings.list_name : null),
        record_types: types as Settings["record_types"],
        lead_statuses: statuses,
        contact_tags: splitList(contactTags),
        skip_converted: skipConverted,
        sync_paused: paused,
      };
      if (caps.status_for_new) config.status_for_new = statusForNew;
      if (caps.subscribe_mode) config.subscribe_mode = mode;
      if (caps.tags) config.tags = splitList(tags);
      const next = await saveSettings(key, config);
      toast(listId && listId !== settings.list_id ? `Saved. AIVA starts filling the ${label} within a few minutes.` : "Sync settings saved.");
      setDetail(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  function toggle(list: string[], value: string, on: boolean) {
    return on ? [...new Set([...list, value])] : list.filter((item) => item !== value);
  }

  return (
    <Card title={`${extra.list_label} and who syncs`} icon={<ListChecks size={16} />}>
      <form className="ui-stack" style={{ gap: 16 }} onSubmit={(event) => void save(event)}>
        <div className="imk-row">
          <Field label={extra.list_label} hint={extra.list_required ? undefined : "Optional for Klaviyo: without a list, people become profiles only."} className="imk-grow">
            {manage ? (
              <select className="ui-select" value={listId} onChange={(event) => setListId(event.target.value)}>
                <option value="">{lists.loading && !items.length ? "Loading…" : extra.list_required ? `Choose a ${label}` : "No list (profiles only)"}</option>
                {listId && !items.some((row) => row.id === listId) ? <option value={listId}>{settings.list_name ?? listId}</option> : null}
                {items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                    {row.count !== null && row.count !== undefined ? ` (${number(row.count)})` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <input className="ui-input" value={settings.list_name ?? settings.list_id ?? "Not chosen yet"} disabled />
            )}
          </Field>
          {manage ? (
            <button type="button" className="ui-btn" disabled={lists.loading} onClick={() => void lists.reload()}>
              {lists.loading ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Refresh
            </button>
          ) : null}
        </div>
        {lists.error ? <Alert tone="warning">{lists.error}</Alert> : null}
        {listId && settings.list_id && listId !== settings.list_id ? (
          <Alert tone="info">Switching {label}s starts a fresh sync into the new one. People stay in the old {label}.</Alert>
        ) : null}

        <fieldset className="imk-fieldset">
          <legend>Who goes to {detail.integration.name}</legend>
          <div className="imk-checks">
            <label className="ui-check">
              <input type="checkbox" checked={types.includes("contact")} disabled={!manage} onChange={(event) => setTypes((t) => toggle(t, "contact", event.target.checked))} />
              Contacts with an email address
            </label>
            <label className="ui-check">
              <input type="checkbox" checked={types.includes("lead")} disabled={!manage} onChange={(event) => setTypes((t) => toggle(t, "lead", event.target.checked))} />
              Leads with an email address
            </label>
          </div>
          <p className="ui-muted ui-small" style={{ margin: "8px 0 0" }}>
            Nobody on AIVA&apos;s do-not-contact list, or who opted out of email, is ever sent. A contact and a lead with the same address count once.
          </p>
        </fieldset>

        {types.includes("lead") ? (
          <fieldset className="imk-fieldset">
            <legend>Lead statuses</legend>
            <div className="imk-checks">
              {LEAD_STATUSES.map((option) => (
                <label key={option.key} className="ui-check">
                  <input type="checkbox" checked={statuses.includes(option.key)} disabled={!manage} onChange={(event) => setStatuses((s) => toggle(s, option.key, event.target.checked))} />
                  {option.label}
                </label>
              ))}
              <label className="ui-check">
                <input type="checkbox" checked={skipConverted} disabled={!manage} onChange={(event) => setSkipConverted(event.target.checked)} />
                Skip converted leads
              </label>
            </div>
            <p className="ui-muted ui-small" style={{ margin: "8px 0 0" }}>None ticked means every status.</p>
          </fieldset>
        ) : null}

        <div className="ui-form-grid">
          {types.includes("contact") ? (
            <Field label="Only contacts tagged" hint="Comma-separated. Leave empty for every contact.">
              <input className="ui-input" value={contactTags} disabled={!manage} onChange={(event) => setContactTags(event.target.value)} placeholder="newsletter, customer" />
            </Field>
          ) : null}
          {caps.tags ? (
            <Field label={`Tags in ${detail.integration.name}`} hint="Added to everyone AIVA syncs. Comma-separated.">
              <input className="ui-input" value={tags} disabled={!manage} onChange={(event) => setTags(event.target.value)} placeholder="AIVA" />
            </Field>
          ) : null}
          {caps.status_for_new ? (
            <Field label="New people join as" hint="People already in the audience keep their status, so nobody who unsubscribed is re-subscribed.">
              <select className="ui-select" value={statusForNew} disabled={!manage} onChange={(event) => setStatusForNew(event.target.value as Settings["status_for_new"])}>
                <option value="subscribed">Subscribed (they agreed to marketing email)</option>
                <option value="pending">Pending: Mailchimp sends a confirmation email (double opt-in)</option>
                <option value="transactional">Transactional: in the audience, not for campaigns</option>
              </select>
            </Field>
          ) : null}
          {caps.subscribe_mode ? (
            <Field label="Email consent" hint="Klaviyo never re-subscribes anyone it says unsubscribed or suppressed.">
              <select className="ui-select" value={mode} disabled={!manage} onChange={(event) => setMode(event.target.value as Settings["subscribe_mode"])}>
                <option value="add">Only add to the list (consent unchanged)</option>
                <option value="subscribe">Add and subscribe to email marketing</option>
              </select>
            </Field>
          ) : null}
        </div>

        <label className="ui-check">
          <input type="checkbox" checked={paused} disabled={!manage} onChange={(event) => setPaused(event.target.checked)} />
          Pause the sync (unsubscribes still come back to AIVA when it resumes)
        </label>

        {manage ? (
          <div>
            <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || (extra.list_required && !listId)}>
              {busy ? <span className="ui-spinner" /> : null} Save
            </button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

/* ------------------------------------------------------------------ field mapping */

function MappingCard({ detail, setDetail }: Pick<PanelProps, "detail" | "setDetail">) {
  const toast = useToast();
  const extra = detail.extra as MarketingExtra;
  const key = detail.integration.key;
  const manage = detail.can_manage;
  const free = Boolean(extra.capabilities.free_fields);
  const listId = extra.settings.list_id;
  const fields = useApi<{ items: Option[] }>(manage && !free && extra.connected && (listId || !extra.list_required) ? `/email-marketing/${key}/fields${listId ? `?list_id=${encodeURIComponent(listId)}` : ""}` : null);
  const [rows, setRows] = useState<{ target: string; source: string }[]>(() => Object.entries(extra.settings.field_map).map(([target, source]) => ({ target, source })));
  const [busy, setBusy] = useState(false);
  const sourceLabel = useMemo(() => Object.fromEntries(extra.sources.map((s) => [s.key, s.label])), [extra.sources]);
  const targets = fields.data?.items ?? [];

  useEffect(() => {
    setRows(Object.entries(extra.settings.field_map).map(([target, source]) => ({ target, source })));
  }, [extra.settings.field_map]);

  async function save() {
    setBusy(true);
    try {
      const field_map = Object.fromEntries(rows.filter((r) => r.target.trim() && r.source).map((r) => [r.target.trim(), r.source]));
      const next = await saveSettings(key, { field_map });
      toast("Field mapping saved. Changed people are sent on the next sync.");
      setDetail(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Fields" icon={<Rows3 size={16} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <div className="ui-table-wrap">
          <table className="ui-table imk-map">
            <thead>
              <tr>
                <th>{detail.integration.name} field</th>
                <th>AIVA field</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {extra.builtin_fields.map((row) => (
                <tr key={row.target}>
                  <td>{row.target}</td>
                  <td className="ui-muted">{row.source === "email" ? "Email" : sourceLabel[row.source] ?? row.source}</td>
                  <td className="ui-small ui-muted">Always sent</td>
                </tr>
              ))}
              {rows.map((row, index) => (
                <tr key={index}>
                  <td>
                    {free ? (
                      <input
                        className="ui-input sm"
                        aria-label="Property name"
                        value={row.target}
                        disabled={!manage}
                        placeholder="Property name"
                        onChange={(event) => setRows((current) => current.map((r, i) => (i === index ? { ...r, target: event.target.value } : r)))}
                      />
                    ) : (
                      <select
                        className="ui-select sm"
                        aria-label={`${detail.integration.name} field`}
                        value={row.target}
                        disabled={!manage}
                        onChange={(event) => setRows((current) => current.map((r, i) => (i === index ? { ...r, target: event.target.value } : r)))}
                      >
                        <option value="">Choose…</option>
                        {row.target && !targets.some((t) => t.key === row.target) ? <option value={row.target}>{row.target}</option> : null}
                        {targets.map((t) => (
                          <option key={t.key} value={t.key}>
                            {t.label}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td>
                    <select
                      className="ui-select sm"
                      aria-label="AIVA field"
                      value={row.source}
                      disabled={!manage}
                      onChange={(event) => setRows((current) => current.map((r, i) => (i === index ? { ...r, source: event.target.value } : r)))}
                    >
                      <option value="">Choose…</option>
                      {extra.sources.map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {manage ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove field" onClick={() => setRows((current) => current.filter((_, i) => i !== index))}>
                        <Trash2 size={14} />
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {fields.error ? <Alert tone="warning">{fields.error}</Alert> : null}
        {!free && !listId && extra.list_required ? <p className="ui-muted ui-small" style={{ margin: 0 }}>Choose the {extra.list_label.toLowerCase()} first to see its fields.</p> : null}
        {manage ? (
          <div className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
            <button type="button" className="ui-btn" disabled={rows.length >= 30} onClick={() => setRows((current) => [...current, { target: "", source: "" }])}>
              <Plus size={14} /> Add a field
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
              {busy ? <span className="ui-spinner" /> : null} Save fields
            </button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ Klaviyo CRM events */

function EventsCard({ detail, setDetail }: Pick<PanelProps, "detail" | "setDetail">) {
  const toast = useToast();
  const extra = detail.extra as MarketingExtra;
  const [events, setEvents] = useState<string[]>(extra.settings.events);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const next = await saveSettings(detail.integration.key, { events });
      toast("Events saved. New ones are sent from now on.");
      setDetail(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="CRM events" icon={<Zap size={16} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          Send these as Klaviyo events on the person&apos;s profile, so they can start flows. Deals and quotes use the primary contact&apos;s email. Past events aren&apos;t replayed.
        </p>
        <div className="imk-checks">
          {extra.crm_events.map((option) => (
            <label key={option.key} className="ui-check">
              <input
                type="checkbox"
                checked={events.includes(option.key)}
                disabled={!detail.can_manage}
                onChange={(event) => setEvents((current) => (event.target.checked ? [...current, option.key] : current.filter((k) => k !== option.key)))}
              />
              {option.label}
            </label>
          ))}
        </div>
        {detail.can_manage ? (
          <div>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
              {busy ? <span className="ui-spinner" /> : null} Save events
            </button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ sync status */

function resultLine(result: SyncResult, name: string) {
  const parts: string[] = [];
  if (result.sent) parts.push(`${number(result.sent)} sent`);
  if (result.unsubscribed_in_tool) parts.push(`${number(result.unsubscribed_in_tool)} unsubscribed in ${name}`);
  if (result.suppressed_in_aiva) parts.push(`${number(result.suppressed_in_aiva)} added to the do-not-contact list`);
  if (result.errors) parts.push(`${number(result.errors)} not accepted`);
  if (result.activities) parts.push(`${number(result.activities)} opens and clicks on timelines`);
  if (result.events) parts.push(`${number(result.events)} events`);
  return parts.length ? parts.join(" · ") : "Nothing new to send.";
}

function SyncCard({ detail, reload }: Pick<PanelProps, "detail" | "reload">) {
  const toast = useToast();
  const extra = detail.extra as MarketingExtra;
  const sync = extra.sync;
  const hook = extra.webhook;
  const name = detail.integration.name;
  const [busy, setBusy] = useState<"sync" | "resend" | null>(null);
  const [confirming, setConfirming] = useState(false);
  if (!sync) return null;
  const counts = sync.counts;
  const backfilling = Object.values(sync.caught_up ?? {}).some((done) => done === false) || sync.backlog;

  async function run(resend: boolean) {
    setBusy(resend ? "resend" : "sync");
    try {
      const result = await api.post<{ ok: boolean; message: string }>(`/email-marketing/${detail.integration.key}/sync`, { resend_everyone: resend });
      toast(result.message, result.ok ? undefined : "danger");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
      setConfirming(false);
    }
  }

  return (
    <Card
      title="Sync"
      icon={<RefreshCw size={16} />}
      actions={
        detail.can_manage ? (
          <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void run(false)}>
            {busy === "sync" ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Sync now
          </button>
        ) : null
      }
    >
      <div className="ui-stack" style={{ gap: 14 }}>
        <div className="imk-kpis">
          <Kpi label={`In the ${extra.list_label.toLowerCase()}`} value={number(counts.in_audience)} note={counts.pending ? `${number(counts.pending)} waiting to confirm` : undefined} />
          <Kpi label="Unsubscribed or bounced" value={number(counts.opted_out)} />
          <Kpi label="Not accepted" value={number(counts.errors)} tone={counts.errors ? "danger" : undefined} />
          <Kpi label="Last sync" value={sync.last_run_at ? relativeTime(sync.last_run_at) : "Not yet"} note={sync.next_run_at ? `Next ${relativeTime(sync.next_run_at)}` : undefined} />
        </div>
        {sync.last_error ? <Alert tone={sync.last_error.startsWith("Choose") || sync.last_error.startsWith("Sync is paused") ? "info" : "warning"}>{sync.last_error}</Alert> : null}
        {sync.last_run_at ? (
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            Last run: {resultLine(sync.last_result ?? {}, name)}
            {backfilling ? " Still working through your records; it carries on every minute until everyone is sent." : ""}
          </p>
        ) : null}
        <div className="imk-facts">
          <div>
            <span className="ui-muted ui-small">Unsubscribes from {name}</span>
            <strong>
              <ShieldCheck size={14} />
              {hook?.active ? "Arrive instantly (webhook)" : "Checked every 15 minutes"}
            </strong>
            {hook?.supported && !hook.active ? (
              <span className="ui-muted ui-small">{hook.error ?? hook.note ?? "AIVA registers its webhook on the next sync."}</span>
            ) : null}
          </div>
          <div>
            <span className="ui-muted ui-small">AIVA&apos;s do-not-contact list</span>
            <strong>
              <ShieldCheck size={14} /> Sent to {name} on every sync
            </strong>
          </div>
        </div>
        {detail.can_manage && hook?.supported && !hook.active && extra.webhook_url ? (
          <UrlRow label="Webhook URL" value={extra.webhook_url} hint={`If AIVA can't register it, add this URL as a webhook in ${name} for unsubscribes and bounces.`} />
        ) : null}
        {detail.can_manage ? (
          <div>
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy !== null} onClick={() => setConfirming(true)}>
              Send everyone again
            </button>
          </div>
        ) : null}
      </div>
      <ConfirmDialog
        open={confirming}
        title={`Send everyone to ${name} again?`}
        message={`AIVA re-sends every contact and lead that matches your settings, even if nothing changed. People who unsubscribed stay unsubscribed. Large audiences take a few minutes.`}
        confirmLabel="Send again"
        danger={false}
        busy={busy === "resend"}
        onConfirm={() => void run(true)}
        onClose={() => setConfirming(false)}
      />
    </Card>
  );
}

/* ------------------------------------------------------------------ campaigns */

function CampaignsCard({ detail }: Pick<PanelProps, "detail">) {
  const extra = detail.extra as MarketingExtra;
  const campaigns = extra.campaigns ?? [];
  if (!extra.capabilities.campaigns) return null;
  const withStats = campaigns.some((c) => c.stats.sent !== undefined);
  return (
    <Card title="Recent campaigns" icon={<Megaphone size={16} />} flush={campaigns.length > 0}>
      {campaigns.length ? (
        <div className="ui-table-wrap">
          <table className="ui-table imk-campaigns">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Sent</th>
                {withStats ? (
                  <>
                    <th className="num">Recipients</th>
                    <th className="num">Opened</th>
                    <th className="num">Clicked</th>
                  </>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.id}>
                  <td className="cell-title">
                    {c.url ? (
                      <a href={c.url} target="_blank" rel="noopener noreferrer">
                        {c.title} <ArrowUpRight size={12} style={{ verticalAlign: -1 }} />
                      </a>
                    ) : (
                      c.title
                    )}
                    {c.stats.subject && c.stats.subject !== c.title ? <div className="ui-muted ui-small ui-truncate">{c.stats.subject}</div> : null}
                  </td>
                  <td className="ui-nowrap ui-small" title={dateTime(c.sent_at)}>
                    {shortDate(c.sent_at)}
                  </td>
                  {withStats ? (
                    <>
                      <td className="num">{c.stats.sent !== undefined ? number(c.stats.sent) : "—"}</td>
                      <td className="num">{rate(c.stats.open_rate)}</td>
                      <td className="num">{rate(c.stats.click_rate)}</td>
                    </>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Campaigns sent in the last 30 days appear here after the next hourly check.
        </p>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ the panel */

function MarketingPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as MarketingExtra;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;
  if (!extra.connected || !extra.settings) return null;
  return (
    <>
      <AudienceCard detail={detail} setDetail={setDetail} />
      <MappingCard detail={detail} setDetail={setDetail} />
      {extra.capabilities.events ? <EventsCard detail={detail} setDetail={setDetail} /> : null}
      <SyncCard detail={detail} reload={reload} />
      <CampaignsCard detail={detail} />
    </>
  );
}

export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  mailchimp: MarketingPanel,
  activecampaign: MarketingPanel,
  brevo: MarketingPanel,
  constant_contact: MarketingPanel,
  klaviyo: MarketingPanel,
};
