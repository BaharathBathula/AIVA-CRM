"use client";

import Link from "next/link";
import { BarChart3, Download, FileCode2, KeyRound, ListChecks, MousePointerClick, RefreshCcw, RotateCw, Save, Send, ShieldCheck, Table2 } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { SettingRow, TagsInput, Toggle, useMembers } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, Field, Kpi, LoadingRows, type Tone } from "@/components/ui";
import { money, number, relativeTime } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

import { UrlRow } from "../common";
import type { IntegrationDetail, PanelProps } from "../types";

/* -------------------------------------------------------------------------- Shared */

type Moment = { key: string; label: string; valued: boolean };

type OutboxItem = {
  id: string;
  title: string | null;
  moment: string | null;
  status: "pending" | "sent" | "skipped" | "failed" | "duplicate";
  value: number | null;
  currency: string | null;
  attempts: number;
  message: string | null;
  identifier: string | null;
  next_at: string | null;
  occurred_at: string | null;
  finished_at: string | null;
  href: string | null;
};

type Stats = Partial<Record<OutboxItem["status"], number>>;

const OUTBOX_TONE: Record<OutboxItem["status"], Tone> = { pending: "info", sent: "success", skipped: "neutral", failed: "danger", duplicate: "neutral" };
const OUTBOX_LABEL: Record<OutboxItem["status"], string> = { pending: "Waiting", sent: "Sent", skipped: "Skipped", failed: "Failed", duplicate: "Already sent" };

async function saveSettings(detail: IntegrationDetail, config: Record<string, unknown>) {
  return api.put<IntegrationDetail>(`/integration-hub/${detail.integration.key}/settings`, { config });
}

function StatChips({ stats }: { stats: Stats | undefined }) {
  if (!stats) return null;
  return (
    <div className="ian-chips">
      {(Object.keys(OUTBOX_LABEL) as OutboxItem["status"][]).map((key) => (
        <span key={key} className="ian-chip">
          <Badge tone={OUTBOX_TONE[key]} dot>
            {OUTBOX_LABEL[key]}
          </Badge>
          <strong>{number(stats[key] ?? 0)}</strong>
        </span>
      ))}
      <span className="ui-muted ui-small">Last 30 days</span>
    </div>
  );
}

function OutboxList({ items, retryPath, onChanged, empty }: { items: OutboxItem[] | undefined; retryPath: (id: string) => string; onChanged: () => void; empty: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  if (!items) return null;
  if (!items.length) return <p className="ui-muted ui-small" style={{ margin: 0 }}>{empty}</p>;

  async function retry(item: OutboxItem) {
    setBusy(item.id);
    try {
      await api.post(retryPath(item.id));
      toast("Queued again. It goes out within a minute.");
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <ul className="ian-outbox">
      {items.map((item) => (
        <li key={item.id}>
          <div className="ian-outbox-main">
            <div className="ian-outbox-title">
              <Badge tone={OUTBOX_TONE[item.status]} dot>
                {OUTBOX_LABEL[item.status]}
              </Badge>
              {item.href ? <Link href={item.href}>{item.title}</Link> : <span>{item.title}</span>}
              {item.value !== null ? <span className="ui-muted ui-small">{money(item.value, item.currency ?? "USD")}</span> : null}
            </div>
            {item.message ? <div className="ui-small ian-outbox-message">{item.message}</div> : null}
            <div className="ui-muted ui-small">
              {relativeTime(item.occurred_at)}
              {item.attempts ? ` · ${item.attempts} attempt${item.attempts === 1 ? "" : "s"}` : ""}
              {item.status === "pending" && item.next_at ? ` · next try ${relativeTime(item.next_at)}` : ""}
            </div>
          </div>
          {item.status === "failed" || item.status === "skipped" ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy === item.id} onClick={() => void retry(item)}>
              {busy === item.id ? <span className="ui-spinner" /> : <RotateCw size={13} />} Try again
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/* -------------------------------------------------------------------------- Google Ads */

type AdsAccount = { id: string; name: string; currency: string | null; time_zone: string | null; test: boolean; manager: boolean; login_customer_id: string | null; via: string | null };
type ConversionAction = { resource_name: string; id: string; name: string; category: string | null };
type ConversionRule = { conversion_action: string; value: "none" | "record" | "fixed"; fixed_value?: number; currency?: string };
type LeadFormSettings = { lead_source: string; owner_user_id: string | null; on_duplicate: "update" | "skip" | "create"; tags: string[] };
type AdsSettings = {
  customer_id: string | null;
  login_customer_id: string | null;
  customer_name: string | null;
  currency: string;
  time_zone: string;
  conversions: Record<string, ConversionRule>;
  consent: "unspecified" | "granted" | "denied";
  sync_performance: boolean;
  lead_form: LeadFormSettings;
};
type AdsExtra = {
  api_version: string;
  developer_token: { set: boolean; source: "workspace" | "server" | null; workspace: boolean };
  settings: AdsSettings;
  moments: Moment[];
  accounts?: AdsAccount[];
  accounts_at?: string | null;
  conversion_actions?: ConversionAction[];
  conversion_actions_at?: string | null;
  performance_synced_at?: string | null;
  webhook_url?: string | null;
  lead_form_key?: string | null;
  stats?: Stats;
  recent?: OutboxItem[];
  error?: string;
};

type Performance = {
  days: number;
  currency: string;
  synced_at: string | null;
  campaigns: {
    campaign_id: string;
    name: string;
    status: string | null;
    cost: number;
    clicks: number;
    impressions: number;
    ads_conversions: number;
    leads: number;
    won_deals: number;
    won_revenue: number;
    cost_per_lead: number | null;
    roas: number | null;
  }[];
  totals: { cost: number; clicks: number; impressions: number; ads_conversions: number; leads: number; won_deals: number; won_revenue: number; roas: number | null };
  unmatched: { leads: number; won_deals: number; won_revenue: number };
};

function prettyCustomer(id: string | null | undefined) {
  if (!id || id.length !== 10) return id ?? "";
  return `${id.slice(0, 3)}-${id.slice(3, 6)}-${id.slice(6)}`;
}

function DeveloperTokenCard({ detail, extra, reload }: { detail: IntegrationDetail; extra: AdsExtra; reload: () => Promise<void> }) {
  const toast = useToast();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  if (!detail.can_manage) {
    return extra.developer_token.set ? null : <Alert tone="warning">Google Ads needs a developer token. Ask an admin to add one.</Alert>;
  }

  async function save(value: string | null) {
    setBusy(true);
    try {
      const result = await api.put<{ set: boolean; accounts_found: number | null }>("/ads/google/developer-token", { token: value });
      toast(value ? `Developer token saved${result.accounts_found !== null ? `; found ${result.accounts_found} account${result.accounts_found === 1 ? "" : "s"}` : ""}.` : "Developer token removed.");
      setToken("");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const { set, source, workspace } = extra.developer_token;
  return (
    <Card title="Developer token" icon={<KeyRound size={16} />} actions={set ? <Badge tone="success" dot>{source === "server" ? "From the server" : "Saved"}</Badge> : <Badge tone="warning" dot>Missing</Badge>}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          Every Google Ads API call needs a developer token: in Google Ads, open <strong>Tools → API Center</strong> of a manager account. Test-level tokens only work with test accounts; apply for Basic access to reach live accounts.
          {source === "server" ? " This server already has one; add your own to use your quota instead." : ""}
        </p>
        <form
          className="ian-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (token.trim()) void save(token.trim());
          }}
        >
          <input className="ui-input" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={workspace ? "Saved — paste a new one to replace it" : "Paste the developer token"} aria-label="Developer token" />
          <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || !token.trim()}>
            {busy ? <span className="ui-spinner" /> : <Save size={14} />} Save
          </button>
          {workspace ? (
            <button type="button" className="ui-btn ui-btn-ghost" disabled={busy} onClick={() => void save(null)}>
              Remove
            </button>
          ) : null}
        </form>
      </div>
    </Card>
  );
}

function AccountCard({ detail, setDetail, extra, reload }: PanelProps & { extra: AdsExtra }) {
  const toast = useToast();
  const [choice, setChoice] = useState(extra.settings.customer_id ?? "");
  const [busy, setBusy] = useState<"save" | "refresh" | null>(null);
  useEffect(() => setChoice(extra.settings.customer_id ?? ""), [extra.settings.customer_id]);
  const accounts = extra.accounts ?? [];
  const current = accounts.find((a) => a.id === extra.settings.customer_id);

  async function refresh() {
    setBusy("refresh");
    try {
      const result = await api.post<{ accounts: AdsAccount[] }>("/ads/google/accounts/refresh");
      toast(`Found ${result.accounts.length} account${result.accounts.length === 1 ? "" : "s"}.`);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    setBusy("save");
    try {
      setDetail(await saveSettings(detail, { customer_id: choice || null }));
      toast("Google Ads account saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Google Ads account"
      icon={<MousePointerClick size={16} />}
      actions={
        detail.can_manage ? (
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy !== null} onClick={() => void refresh()}>
            {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCcw size={13} />} Refresh list
          </button>
        ) : null
      }
    >
      {!accounts.length ? (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          {extra.developer_token.set ? "No Google Ads accounts found for the connected Google user yet. Refresh the list, or reconnect with the Google account that manages your ads." : "Add the developer token, then refresh the list."}
        </p>
      ) : (
        <div className="ui-stack" style={{ gap: 12 }}>
          <Field label="Account" hint="Client accounts under a manager account are reached through that manager.">
            <select className="ui-select" value={choice} disabled={!detail.can_manage} onChange={(e) => setChoice(e.target.value)}>
              <option value="">Choose an account</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name} ({prettyCustomer(account.id)}){account.via ? ` · via ${account.via}` : ""}
                  {account.test ? " · test" : ""}
                </option>
              ))}
            </select>
          </Field>
          {current ? (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Currency {current.currency ?? extra.settings.currency} · time zone {current.time_zone ?? extra.settings.time_zone}
              {extra.accounts_at ? ` · list refreshed ${relativeTime(extra.accounts_at)}` : ""}
            </p>
          ) : null}
          {detail.can_manage ? (
            <div>
              <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy !== null || choice === (extra.settings.customer_id ?? "")} onClick={() => void save()}>
                {busy === "save" ? <span className="ui-spinner" /> : <Save size={14} />} Use this account
              </button>
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

const VALUE_LABELS: Record<ConversionRule["value"], string> = { record: "Deal amount or quote total", fixed: "A fixed value", none: "No value" };

function ConversionsCard({ detail, setDetail, extra, reload }: PanelProps & { extra: AdsExtra }) {
  const toast = useToast();
  const [rules, setRules] = useState<Record<string, ConversionRule>>(extra.settings.conversions);
  const [consent, setConsent] = useState(extra.settings.consent);
  const [busy, setBusy] = useState<"save" | "refresh" | null>(null);
  useEffect(() => setRules(extra.settings.conversions), [extra.settings.conversions]);
  useEffect(() => setConsent(extra.settings.consent), [extra.settings.consent]);
  const actions = extra.conversion_actions ?? [];
  const locked = !detail.can_manage;
  const dirty = JSON.stringify(rules) !== JSON.stringify(extra.settings.conversions) || consent !== extra.settings.consent;

  function update(moment: Moment, change: Partial<ConversionRule> | null) {
    setRules((current) => {
      const next = { ...current };
      if (change === null) delete next[moment.key];
      else next[moment.key] = { ...(next[moment.key] ?? { conversion_action: "", value: moment.valued ? "record" : "none" }), ...change };
      return next;
    });
  }

  async function refreshActions() {
    setBusy("refresh");
    try {
      await api.get(`/ads/google/conversion-actions${query({ refresh: true })}`);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    setBusy("save");
    try {
      const cleaned = Object.fromEntries(Object.entries(rules).filter(([, rule]) => rule.conversion_action));
      setDetail(await saveSettings(detail, { conversions: cleaned, consent }));
      toast("Conversions saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  if (!extra.settings.customer_id) {
    return (
      <Card title="Offline conversions" icon={<ListChecks size={16} />}>
        <p className="ui-muted ui-small" style={{ margin: 0 }}>Pick the Google Ads account first.</p>
      </Card>
    );
  }

  return (
    <Card
      title="Offline conversions"
      icon={<ListChecks size={16} />}
      actions={
        detail.can_manage ? (
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy !== null} onClick={() => void refreshActions()}>
            {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCcw size={13} />} Refresh actions
          </button>
        ) : null
      }
      footer={
        detail.can_manage ? (
          <div className="ian-save-row">
            <span className="ui-muted ui-small">{dirty ? "Unsaved changes" : "Each moment of a record is uploaded once."}</span>
            <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy !== null} onClick={() => void save()}>
              {busy === "save" ? <span className="ui-spinner" /> : <Save size={14} />} Save
            </button>
          </div>
        ) : undefined
      }
    >
      <div className="ui-stack" style={{ gap: 14 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          When a CRM moment happens to someone who came from a Google ad, AIVA uploads it to the conversion action you pick, so Smart Bidding learns which clicks become pipeline and revenue. Use conversion actions of the type <strong>Import → CRM / clicks</strong>.
        </p>
        {!actions.length ? (
          <Alert tone="info">
            No conversion actions that import clicks yet. In Google Ads: Goals → Conversions → New conversion action → Import → CRMs, files or other data sources → Track conversions from clicks. Then refresh the list.
          </Alert>
        ) : null}
        <div className="ian-rules">
          {extra.moments.map((moment) => {
            const rule = rules[moment.key];
            return (
              <div key={moment.key} className={`ian-rule${rule?.conversion_action ? " on" : ""}`}>
                <div className="ian-rule-label">{moment.label}</div>
                <select
                  className="ui-select sm"
                  value={rule?.conversion_action ?? ""}
                  disabled={locked || !actions.length}
                  aria-label={`Conversion action for ${moment.label}`}
                  onChange={(e) => (e.target.value ? update(moment, { conversion_action: e.target.value }) : update(moment, null))}
                >
                  <option value="">Don&apos;t send</option>
                  {actions.map((action) => (
                    <option key={action.resource_name} value={action.resource_name}>
                      {action.name}
                    </option>
                  ))}
                </select>
                {rule?.conversion_action ? (
                  <div className="ian-rule-value">
                    <select className="ui-select sm" value={rule.value} disabled={locked} aria-label={`Value for ${moment.label}`} onChange={(e) => update(moment, { value: e.target.value as ConversionRule["value"] })}>
                      {(Object.keys(VALUE_LABELS) as ConversionRule["value"][])
                        .filter((value) => value !== "record" || moment.valued)
                        .map((value) => (
                          <option key={value} value={value}>
                            {VALUE_LABELS[value]}
                          </option>
                        ))}
                    </select>
                    {rule.value === "fixed" ? (
                      <input
                        className="ui-input sm"
                        type="number"
                        min={0}
                        step="0.01"
                        disabled={locked}
                        value={rule.fixed_value ?? ""}
                        aria-label={`Fixed value for ${moment.label}`}
                        onChange={(e) => update(moment, { fixed_value: e.target.value === "" ? undefined : Number(e.target.value) })}
                        placeholder={extra.settings.currency}
                      />
                    ) : null}
                  </div>
                ) : (
                  <span />
                )}
              </div>
            );
          })}
        </div>
        <SettingRow label="Ad user data consent" hint="Sent with every conversion. EEA traffic needs consent; leave it unspecified if your site's consent banner already reports it to Google.">
          <select className="ui-select sm" value={consent} disabled={locked} onChange={(e) => setConsent(e.target.value as AdsSettings["consent"])} style={{ width: 200 }}>
            <option value="unspecified">Not specified</option>
            <option value="granted">Granted</option>
            <option value="denied">Denied</option>
          </select>
        </SettingRow>
      </div>
    </Card>
  );
}

const DUPLICATES: { value: LeadFormSettings["on_duplicate"]; label: string }[] = [
  { value: "update", label: "Update the existing lead" },
  { value: "skip", label: "Skip it" },
  { value: "create", label: "Create another lead" },
];

function LeadFormCard({ detail, setDetail, extra }: PanelProps & { extra: AdsExtra }) {
  const toast = useToast();
  const { members } = useMembers();
  const [draft, setDraft] = useState<LeadFormSettings>(extra.settings.lead_form);
  const [busy, setBusy] = useState<"save" | "rotate" | null>(null);
  const [key, setKey] = useState<string | null>(extra.lead_form_key ?? null);
  useEffect(() => setDraft(extra.settings.lead_form), [extra.settings.lead_form]);
  useEffect(() => setKey(extra.lead_form_key ?? null), [extra.lead_form_key]);
  if (!detail.can_manage) return null;
  const dirty = JSON.stringify(draft) !== JSON.stringify(extra.settings.lead_form);

  async function save() {
    setBusy("save");
    try {
      setDetail(await saveSettings(detail, { lead_form: draft }));
      toast("Lead form settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function rotate() {
    setBusy("rotate");
    try {
      const result = await api.post<{ key: string }>("/ads/google/lead-form-key");
      setKey(result.key);
      toast("New key created. Paste it into each Google Ads lead form.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Lead forms"
      icon={<Send size={16} />}
      footer={
        <div className="ian-save-row">
          <span className="ui-muted ui-small">{dirty ? "Unsaved changes" : "Applies to every lead form that posts here."}</span>
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy !== null} onClick={() => void save()}>
            {busy === "save" ? <span className="ui-spinner" /> : <Save size={14} />} Save
          </button>
        </div>
      }
    >
      <div className="ui-stack" style={{ gap: 14 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          In Google Ads, open the lead form asset → <strong>Lead delivery</strong> → Webhook integration, paste this URL and key, and click <strong>Send test data</strong>. Leads keep their click ID, campaign, ad group and form, so they can be sent back as conversions.
        </p>
        <UrlRow label="Webhook URL" value={extra.webhook_url} />
        {key ? (
          <div className="int-url-row">
            <div className="settings-row-label">Key</div>
            <div className="ian-inline-form">
              <div style={{ flex: 1, minWidth: 0 }}>
                <CopyField value={key} label="Copy key" />
              </div>
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy !== null} onClick={() => void rotate()}>
                {busy === "rotate" ? <span className="ui-spinner" /> : <RotateCw size={13} />} New key
              </button>
            </div>
          </div>
        ) : null}
        <div className="settings-rows">
          <SettingRow label="Lead source" hint="Shown on the lead and in reports.">
            <input className="ui-input sm" value={draft.lead_source} maxLength={80} onChange={(e) => setDraft({ ...draft, lead_source: e.target.value })} style={{ width: 220 }} />
          </SettingRow>
          <SettingRow label="Lead owner" hint="Assignment rules and territories still apply.">
            <select className="ui-select sm" value={draft.owner_user_id ?? ""} onChange={(e) => setDraft({ ...draft, owner_user_id: e.target.value || null })} style={{ width: 220 }}>
              <option value="">Whoever connected Google Ads</option>
              {members
                .filter((m) => m.is_active)
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.full_name}
                  </option>
                ))}
            </select>
          </SettingRow>
          <SettingRow label="Same email again">
            <select className="ui-select sm" value={draft.on_duplicate} onChange={(e) => setDraft({ ...draft, on_duplicate: e.target.value as LeadFormSettings["on_duplicate"] })} style={{ width: 220 }}>
              {DUPLICATES.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
          </SettingRow>
          <SettingRow label="Tags" stacked>
            <TagsInput value={draft.tags} onChange={(tags) => setDraft({ ...draft, tags })} placeholder="paid, search" label="Tags" />
          </SettingRow>
        </div>
      </div>
    </Card>
  );
}

function PerformanceCard({ detail, setDetail, extra }: { detail: IntegrationDetail; setDetail: PanelProps["setDetail"]; extra: AdsExtra }) {
  const toast = useToast();
  const [days, setDays] = useState(30);
  const report = useApi<Performance>(extra.settings.customer_id ? `/ads/google/performance${query({ days })}` : null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  if (!extra.settings.customer_id) return null;

  async function toggleSync(value: boolean) {
    setSaving(true);
    try {
      setDetail(await saveSettings(detail, { sync_performance: value }));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSaving(false);
    }
  }
  const data = report.data;
  const currency = data?.currency ?? extra.settings.currency;

  async function sync() {
    setBusy(true);
    try {
      const result = await api.post<{ handled: number; error: string | null }>("/ads/google/sync");
      if (result.error) toast(result.error, "danger");
      else toast("Synced with Google Ads.");
      await report.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Campaigns and revenue"
      icon={<BarChart3 size={16} />}
      flush
      actions={
        <div className="ui-row" style={{ gap: 8 }}>
          <select className="ui-select sm" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period" style={{ width: 130 }}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
          </select>
          {detail.can_manage ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy} onClick={() => void sync()}>
              {busy ? <span className="ui-spinner" /> : <RefreshCcw size={13} />} Sync now
            </button>
          ) : null}
        </div>
      }
    >
      {report.error ? <div style={{ padding: "12px 18px" }}><Alert>{report.error}</Alert></div> : null}
      {!data && !report.error ? <div style={{ padding: 18 }}><LoadingRows rows={4} /></div> : null}
      {data ? (
        <>
          <div className="ian-kpis">
            <Kpi label="Spend" value={money(data.totals.cost, currency)} note={`${number(data.totals.clicks)} clicks`} />
            <Kpi label="Leads in AIVA" value={number(data.totals.leads)} note={data.totals.leads && data.totals.cost ? `${money(data.totals.cost / data.totals.leads, currency)} per lead` : undefined} />
            <Kpi label="Won revenue" value={money(data.totals.won_revenue, currency)} note={`${number(data.totals.won_deals)} won deals`} />
            <Kpi label="Return on ad spend" value={data.totals.roas !== null ? `${data.totals.roas.toFixed(2)}×` : "—"} tone={data.totals.roas !== null && data.totals.roas >= 1 ? "success" : undefined} />
          </div>
          {data.campaigns.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Campaign</th>
                    <th className="ui-num">Spend</th>
                    <th className="ui-num">Clicks</th>
                    <th className="ui-num">Ads conv.</th>
                    <th className="ui-num">AIVA leads</th>
                    <th className="ui-num">Won</th>
                    <th className="ui-num">Revenue</th>
                    <th className="ui-num">ROAS</th>
                  </tr>
                </thead>
                <tbody>
                  {data.campaigns.map((c) => (
                    <tr key={c.campaign_id}>
                      <td className="cell-title">
                        {c.name}
                        {c.status && c.status !== "ENABLED" ? <span className="ui-muted ui-small"> · {c.status.toLowerCase()}</span> : null}
                      </td>
                      <td className="ui-num">{money(c.cost, currency)}</td>
                      <td className="ui-num">{number(c.clicks)}</td>
                      <td className="ui-num">{number(c.ads_conversions, 1)}</td>
                      <td className="ui-num">{number(c.leads)}</td>
                      <td className="ui-num">{number(c.won_deals)}</td>
                      <td className="ui-num">{money(c.won_revenue, currency)}</td>
                      <td className="ui-num">{c.roas !== null ? `${c.roas.toFixed(2)}×` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ padding: "4px 18px 18px" }}>
              <EmptyState icon={<BarChart3 size={20} />} title="No campaign data yet" message="Spend and clicks arrive with the next sync (every six hours)." />
            </div>
          )}
          <p className="ui-muted ui-small" style={{ margin: 0, padding: "10px 18px 16px" }}>
            Leads count when they carry the campaign (lead forms) or a click ID AIVA matched to it.
            {data.unmatched.leads ? ` ${number(data.unmatched.leads)} lead${data.unmatched.leads === 1 ? "" : "s"} came from campaigns outside this period.` : ""}
            {data.synced_at ? ` Synced ${relativeTime(data.synced_at)}.` : ""}
          </p>
        </>
      ) : null}
      {detail.can_manage ? (
        <div className="ian-card-setting">
          <SettingRow label="Import campaign spend" hint="Every six hours AIVA reads the last 30 days of campaign cost, clicks and conversions.">
            <Toggle checked={extra.settings.sync_performance} disabled={saving} label="Import campaign spend" onChange={(value) => void toggleSync(value)} />
          </SettingRow>
        </div>
      ) : null}
    </Card>
  );
}

function GoogleAdsPanel(props: PanelProps) {
  const { detail, reload } = props;
  const extra = detail.extra as AdsExtra;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert>{extra.error}</Alert>;
  if (!extra.settings) return null;
  return (
    <>
      <DeveloperTokenCard detail={detail} extra={extra} reload={reload} />
      {detail.connection ? (
        <>
          <AccountCard {...props} extra={extra} />
          <ConversionsCard {...props} extra={extra} />
          <PerformanceCard detail={detail} setDetail={props.setDetail} extra={extra} />
          <LeadFormCard {...props} extra={extra} />
          {detail.can_manage ? (
            <Card title="Upload log" icon={<ListChecks size={16} />}>
              <div className="ui-stack" style={{ gap: 12 }}>
                <StatChips stats={extra.stats} />
                <OutboxList
                  items={extra.recent}
                  retryPath={(id) => `/ads/google/conversions/${id}/retry`}
                  onChanged={() => void reload()}
                  empty="Nothing uploaded yet. Conversions appear here as CRM moments happen."
                />
              </div>
            </Card>
          ) : null}
        </>
      ) : null}
    </>
  );
}

/* -------------------------------------------------------------------------- Google Analytics 4 */

type GaRule = { enabled: boolean; event: string };
type GaSettings = {
  events: Record<string, GaRule>;
  send_user_id: boolean;
  pseudo_client_id: boolean;
  eu_endpoint: boolean;
  consent: "unspecified" | "granted" | "denied";
  non_personalized_ads: boolean;
};
type GaExtra = { settings: GaSettings; moments: Moment[]; measurement_id: string | null; stats?: Stats; recent?: OutboxItem[]; error?: string };
type TestResult = { valid: boolean; sent: boolean; messages: { field: string | null; description: string | null; code: string | null }[]; payload: Record<string, unknown> };

function GaEventsCard({ detail, setDetail, extra }: PanelProps & { extra: GaExtra }) {
  const toast = useToast();
  const [draft, setDraft] = useState<GaSettings>(extra.settings);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(extra.settings), [extra.settings]);
  const locked = !detail.can_manage;
  const dirty = JSON.stringify(draft) !== JSON.stringify(extra.settings);

  function rule(key: string): GaRule {
    return draft.events[key] ?? { enabled: false, event: key.startsWith("stage:") ? "deal_stage_reached" : key };
  }

  async function save() {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail, draft as unknown as Record<string, unknown>));
      toast("Google Analytics events saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="CRM events"
      icon={<ListChecks size={16} />}
      footer={
        detail.can_manage ? (
          <div className="ian-save-row">
            <span className="ui-muted ui-small">{dirty ? "Unsaved changes" : "Mark the events as key events in GA4 to report on them."}</span>
            <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy} onClick={() => void save()}>
              {busy ? <span className="ui-spinner" /> : <Save size={14} />} Save
            </button>
          </div>
        ) : undefined
      }
    >
      <div className="ui-stack" style={{ gap: 14 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          Each moment is sent once per record, with the deal amount or quote total where there is one. Events carry the visitor&apos;s GA client ID from AIVA&apos;s tracking script or forms; names, emails and phone numbers are never sent.
        </p>
        <div className="ian-rules">
          {extra.moments.map((moment) => {
            const current = rule(moment.key);
            return (
              <div key={moment.key} className={`ian-rule ga${current.enabled ? " on" : ""}`}>
                <Toggle
                  checked={current.enabled}
                  disabled={locked}
                  label={`Send ${moment.label}`}
                  onChange={(enabled) => setDraft({ ...draft, events: { ...draft.events, [moment.key]: { ...current, enabled } } })}
                />
                <div className="ian-rule-label">{moment.label}</div>
                <input
                  className="ui-input sm ui-mono"
                  value={current.event}
                  disabled={locked || !current.enabled}
                  maxLength={40}
                  aria-label={`GA4 event name for ${moment.label}`}
                  onChange={(e) => setDraft({ ...draft, events: { ...draft.events, [moment.key]: { ...current, event: e.target.value.replace(/[^A-Za-z0-9_]/g, "_") } } })}
                />
              </div>
            );
          })}
        </div>
        <div className="settings-rows">
          <SettingRow label="Stand-in client ID" hint="When AIVA never saw the person's GA client ID, send the event with a stable stand-in ID. It still counts as a key event, but GA can't credit the channel or campaign that brought them.">
            <Toggle checked={draft.pseudo_client_id} disabled={locked} label="Use a stand-in client ID" onChange={(value) => setDraft({ ...draft, pseudo_client_id: value })} />
          </SettingRow>
          <SettingRow label="Send AIVA's person ID as user_id" hint="An opaque AIVA ID (never an email), so GA can join events about the same person.">
            <Toggle checked={draft.send_user_id} disabled={locked} label="Send user_id" onChange={(value) => setDraft({ ...draft, send_user_id: value })} />
          </SettingRow>
          <SettingRow label="EU data collection" hint="Send to region1.google-analytics.com so data is collected in the EU.">
            <Toggle checked={draft.eu_endpoint} disabled={locked} label="Use the EU endpoint" onChange={(value) => setDraft({ ...draft, eu_endpoint: value })} />
          </SettingRow>
          <SettingRow label="Consent">
            <select className="ui-select sm" value={draft.consent} disabled={locked} onChange={(e) => setDraft({ ...draft, consent: e.target.value as GaSettings["consent"] })} style={{ width: 200 }}>
              <option value="unspecified">Not specified</option>
              <option value="granted">Granted</option>
              <option value="denied">Denied</option>
            </select>
          </SettingRow>
          <SettingRow label="Non-personalized ads" hint="Ask Google not to use these events for ad personalization.">
            <Toggle checked={draft.non_personalized_ads} disabled={locked} label="Non-personalized ads" onChange={(value) => setDraft({ ...draft, non_personalized_ads: value })} />
          </SettingRow>
        </div>
      </div>
    </Card>
  );
}

function GaTestCard({ detail, extra }: { detail: IntegrationDetail; extra: GaExtra }) {
  const toast = useToast();
  const choices = extra.moments.filter((m) => extra.settings.events[m.key]);
  const [moment, setMoment] = useState(() => (choices.find((m) => extra.settings.events[m.key]?.enabled) ?? choices[0])?.key ?? "lead_created");
  const [busy, setBusy] = useState<"validate" | "send" | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  if (!detail.can_manage || !detail.connection) return null;

  async function run(send: boolean) {
    setBusy(send ? "send" : "validate");
    try {
      const outcome = await api.post<TestResult>("/ga4/test-event", { moment, send });
      setResult(outcome);
      if (outcome.valid) toast(outcome.sent ? "Sample event sent. Look for it in GA4 → Reports → Realtime." : "Google validated the sample event.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Test an event" icon={<ShieldCheck size={16} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <div className="ian-inline-form">
          <select className="ui-select sm" value={moment} onChange={(e) => setMoment(e.target.value)} aria-label="Moment" style={{ maxWidth: 260 }}>
            {choices.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label} → {extra.settings.events[m.key]?.event}
              </option>
            ))}
          </select>
          <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void run(false)}>
            {busy === "validate" ? <span className="ui-spinner" /> : <ShieldCheck size={13} />} Validate
          </button>
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy !== null} onClick={() => void run(true)}>
            {busy === "send" ? <span className="ui-spinner" /> : <Send size={13} />} Send to GA4
          </button>
        </div>
        {result ? (
          <>
            {result.valid ? (
              <Alert tone="success">{result.sent ? "Sent. It shows in GA4 Realtime within a minute." : "Google found no problems with this event."}</Alert>
            ) : (
              <Alert tone="warning">
                {result.messages.map((m, index) => (
                  <div key={index}>
                    {m.field ? <code>{m.field}</code> : null} {m.description ?? m.code}
                  </div>
                ))}
              </Alert>
            )}
            <pre className="ian-code">{JSON.stringify(result.payload, null, 2)}</pre>
          </>
        ) : null}
      </div>
    </Card>
  );
}

function GoogleAnalyticsPanel(props: PanelProps) {
  const { detail, reload } = props;
  const extra = detail.extra as GaExtra;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert>{extra.error}</Alert>;
  if (!extra.settings) return null;
  return (
    <>
      <GaEventsCard {...props} extra={extra} />
      <GaTestCard detail={detail} extra={extra} />
      <Card title="Where the GA client ID comes from" icon={<MousePointerClick size={16} />}>
        <ul className="int-list">
          <li>
            <strong>AIVA&apos;s tracking script</strong> (Marketing → Visits) reads the <code>_ga</code> cookie on every page, so leads and contacts who visited your site are matched by email.
          </li>
          <li>
            <strong>AIVA forms</strong> embedded on a page with the tracking script receive the client ID (and ad click IDs) from the page and keep them on the new lead.
          </li>
          <li>
            Other forms can pass a <code>ga_client_id</code> field; the <code>_ga</code> cookie value works as is.
          </li>
        </ul>
      </Card>
      {detail.can_manage && detail.connection ? (
        <Card title="Event log" icon={<ListChecks size={16} />}>
          <div className="ui-stack" style={{ gap: 12 }}>
            <StatChips stats={extra.stats} />
            <OutboxList items={extra.recent} retryPath={(id) => `/ga4/events/${id}/retry`} onChanged={() => void reload()} empty="No events yet. They appear here as CRM moments happen." />
          </div>
        </Card>
      ) : null}
    </>
  );
}

/* -------------------------------------------------------------------------- Power BI and Looker Studio */

type FeedDataset = { key: string; label: string; description: string; columns: number };
type FeedExtra = {
  base_url: string;
  feed_url: string;
  api_keys_url: string;
  datasets: FeedDataset[];
  page_size: number;
  power_query?: string;
  power_query_examples?: string;
  connector_download?: string;
  hipaa?: boolean;
  can_read?: boolean;
  error?: string;
};
type FeedPage = { dataset: string; count: number; items: Record<string, unknown>[]; next_cursor: string | null };

function FeedDatasetsCard({ extra }: { extra: FeedExtra }) {
  const [dataset, setDataset] = useState(extra.datasets[0]?.key ?? "leads");
  const preview = useApi<FeedPage>(extra.can_read ? `/analytics-feed/${dataset}${query({ limit: 5 })}` : null);
  const columns = useMemo(() => Object.keys(preview.data?.items[0] ?? {}).slice(0, 7), [preview.data]);

  return (
    <Card title="Datasets" icon={<Table2 size={16} />} flush>
      <ul className="ian-datasets">
        {extra.datasets.map((d) => (
          <li key={d.key}>
            <button type="button" className={dataset === d.key ? "on" : ""} onClick={() => setDataset(d.key)}>
              <strong>{d.label}</strong>
              <code>{d.key}</code>
            </button>
            <span className="ui-muted ui-small">{d.description}</span>
          </li>
        ))}
      </ul>
      {extra.can_read ? (
        <div className="ian-preview">
          <div className="ui-small ui-muted" style={{ padding: "0 18px 8px" }}>
            Preview of <code>{dataset}</code> (first 5 rows you can see; API keys see what their creator can see)
          </div>
          {preview.error ? <div style={{ padding: "0 18px 14px" }}><Alert>{preview.error}</Alert></div> : null}
          {!preview.data && !preview.error ? <div style={{ padding: "0 18px 14px" }}><LoadingRows rows={3} /></div> : null}
          {preview.data && preview.data.items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.data.items.map((row, index) => (
                    <tr key={index}>
                      {columns.map((column) => (
                        <td key={column} className="ui-small ui-truncate" style={{ maxWidth: 180 }}>
                          {row[column] === null || row[column] === undefined ? "—" : String(row[column])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {preview.data && !preview.data.items.length ? <p className="ui-muted ui-small" style={{ margin: 0, padding: "0 18px 16px" }}>No rows yet.</p> : null}
        </div>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0, padding: "4px 18px 16px" }}>Previews need permission to export records.</p>
      )}
    </Card>
  );
}

function FeedBasics({ extra, tool }: { extra: FeedExtra; tool: string }) {
  return (
    <Card title="Connect with an API key" icon={<KeyRound size={16} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        {extra.hipaa ? <Alert tone="warning">HIPAA mode is on: every read of the feed is recorded in the audit log.</Alert> : null}
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          {tool} reads AIVA through a read-only feed. Create a <strong>read-only</strong> API key for it in <Link href={extra.api_keys_url}>Settings → API keys</Link>. The key sees exactly what the person who created it can see, so create it as someone who can see the records you want to report on.
        </p>
        <UrlRow label="Feed URL" value={extra.feed_url} />
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Pages of up to {number(extra.page_size)} rows; follow <code>next_cursor</code> until it&apos;s empty, and add <code>updated_since</code> for incremental refreshes. Encrypted fields and free-text notes are never included.
        </p>
      </div>
    </Card>
  );
}

function PowerBiPanel({ detail }: PanelProps) {
  const extra = detail.extra as FeedExtra;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert>{extra.error}</Alert>;
  if (!extra.datasets) return null;
  return (
    <>
      <FeedBasics extra={extra} tool="Power BI" />
      <Card
        title="Power Query"
        icon={<FileCode2 size={16} />}
        actions={
          <a className="ui-btn ui-btn-ghost ui-btn-sm" href="/api/aiva/analytics-feed/connectors/power-query.m" download>
            <Download size={13} /> Download
          </a>
        }
      >
        <div className="ui-stack" style={{ gap: 12 }}>
          <ol className="int-steps">
            <li>In Power BI Desktop: Home → Transform data → Manage parameters → New parameter named <code>AivaApiKey</code> (Text) holding the API key.</li>
            <li>New source → Blank query → Advanced editor → paste the function below, and name the query <code>AivaFeed</code>.</li>
            <li>For each dataset, add a Blank query <code>= AivaFeed(&quot;leads&quot;)</code> (see the list below), then Close &amp; Apply.</li>
            <li>
              After publishing, in the Power BI service open the semantic model&apos;s settings → Data source credentials → <strong>Anonymous</strong>, privacy level Organizational, and tick <strong>Skip test connection</strong>. The key travels in the request header, so scheduled refresh works.
            </li>
          </ol>
          <CopyField value={extra.power_query ?? ""} label="Copy function" multiline />
          <pre className="ian-code">{extra.power_query_examples}</pre>
        </div>
      </Card>
      <FeedDatasetsCard extra={extra} />
    </>
  );
}

function LookerPanel({ detail }: PanelProps) {
  const extra = detail.extra as FeedExtra;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert>{extra.error}</Alert>;
  if (!extra.datasets) return null;
  return (
    <>
      <FeedBasics extra={extra} tool="Looker Studio" />
      <Card
        title="Community connector"
        icon={<FileCode2 size={16} />}
        actions={
          extra.connector_download ? (
            <a className="ui-btn ui-btn-primary ui-btn-sm" href={`/api/aiva${extra.connector_download}`} download>
              <Download size={13} /> Download connector
            </a>
          ) : null
        }
      >
        <ol className="int-steps">
          <li>Download the connector. It already points at {extra.base_url}.</li>
          <li>
            At <a href="https://script.google.com" target="_blank" rel="noopener noreferrer">script.google.com</a>, create a project, paste <code>Code.gs</code>, and (Project Settings → show the manifest) replace <code>appsscript.json</code>.
          </li>
          <li>Deploy → Test deployments → copy the Head deployment ID.</li>
          <li>
            Open <code>lookerstudio.google.com/datasources/create?connectorId=&lt;ID&gt;</code>, authorize, paste the API key and pick a dataset. Add one data source per dataset and blend them on the ID columns.
          </li>
        </ol>
      </Card>
      <FeedDatasetsCard extra={extra} />
    </>
  );
}

/* -------------------------------------------------------------------------- Registry */

export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  google_ads: GoogleAdsPanel,
  google_analytics: GoogleAnalyticsPanel,
  power_bi: PowerBiPanel,
  looker_studio: LookerPanel,
};
