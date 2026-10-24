"use client";

import Link from "next/link";
import { ArrowUpRight, Megaphone, MessageSquareText, Phone, RefreshCw, Send, Settings2, ShieldCheck, Webhook } from "lucide-react";
import { useState, type ComponentType, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { Alert, Badge, Card, Field, Kpi, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { CredentialsForm, UrlRow } from "../common";
import { prettyNumber } from "../record/messaging-kit";
import type { IntegrationDetail, PanelProps } from "../types";

/* ------------------------------------------------------------------ shapes of detail.extra */

type Option = { key: string; label: string };

type SlackExtra = {
  mode: "bot" | "webhook" | null;
  team_name: string | null;
  team_url: string | null;
  default_channel_id: string | null;
  default_channel_name: string | null;
  routes: Record<string, string>;
  channel_names: Record<string, string>;
  events: string[];
  event_options: Option[];
  routable: Option[];
  legacy_webhook: boolean;
  alerts_active: boolean;
  error?: string;
};

type Stats = { sent: number; received: number; failed: number; delivered: number };

type ChannelExtra = { default_country_code?: string; notify_owner?: boolean; stats?: Stats; webhook_url?: string | null; error?: string };

type WaTemplateRow = { name: string; language: string; status: string; category: string | null; body: string };

type WhatsAppExtra = ChannelExtra & {
  business_number?: string | null;
  display_phone_number?: string | null;
  verified_name?: string | null;
  quality_rating?: string | null;
  messaging_limit_tier?: string | null;
  app_subscribed?: boolean;
  webhook_override?: boolean;
  templates?: WaTemplateRow[];
  templates_synced_at?: string | null;
  approved_templates?: number;
  verify_token?: string | null;
};

type TwilioNumber = { sid: string; phone_number: string; friendly_name: string | null; sms_capable: boolean; points_here: boolean; sms_url_set: boolean };

type TwilioExtra = ChannelExtra & {
  status_callback_url?: string | null;
  from_number?: string | null;
  messaging_service_sid?: string | null;
  messaging_service_name?: string | null;
  account_name?: string | null;
  account_type?: string | null;
  number_points_here?: boolean;
  numbers?: TwilioNumber[];
  numbers_error?: string;
};

type SlackChannel = { id: string; name: string; is_private: boolean; is_member: boolean };

/* ------------------------------------------------------------------ shared bits */

function StatsRow({ stats }: { stats?: Stats }) {
  if (!stats) return null;
  return (
    <div className="imsg-kpis">
      <Kpi label="Sent (7 days)" value={stats.sent} />
      <Kpi label="Delivered" value={stats.delivered} />
      <Kpi label="Received" value={stats.received} />
      <Kpi label="Failed" value={stats.failed} tone={stats.failed ? "danger" : undefined} />
    </div>
  );
}

function ChannelSettings({ detail, setDetail }: Pick<PanelProps, "detail" | "setDetail">) {
  const toast = useToast();
  const extra = detail.extra as ChannelExtra;
  const [code, setCode] = useState(extra.default_country_code ?? "1");
  const [notify, setNotify] = useState(extra.notify_owner ?? true);
  const [busy, setBusy] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const next = await api.put<IntegrationDetail>(`/integration-hub/${detail.integration.key}/settings`, { config: { default_country_code: code, notify_owner: notify } });
      toast("Settings saved.");
      setDetail(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Settings" icon={<Settings2 size={16} />}>
      <form className="ui-stack" style={{ gap: 14 }} onSubmit={(event) => void save(event)}>
        <div className="ui-form-grid">
          <Field label="Default country code" hint="Used for numbers saved without one, like (214) 555-0100. Shared by SMS and WhatsApp.">
            <input className="ui-input" inputMode="numeric" value={code} onChange={(event) => setCode(event.target.value.replace(/[^\d]/g, "").slice(0, 3))} disabled={!detail.can_manage} />
          </Field>
          <div className="ui-field">
            <label>Replies</label>
            <label className="ui-check">
              <input type="checkbox" checked={notify} onChange={(event) => setNotify(event.target.checked)} disabled={!detail.can_manage} />
              Notify the record&apos;s owner (or whoever last messaged the number) when a reply comes in
            </label>
          </div>
        </div>
        {detail.can_manage ? (
          <div>
            <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || !code}>
              {busy ? <span className="ui-spinner" /> : null} Save settings
            </button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

function InboxLink() {
  return (
    <Link className="ui-btn ui-btn-sm" href="/integrations/messages">
      <MessageSquareText size={14} /> Open Messages
    </Link>
  );
}

/* ------------------------------------------------------------------ Slack */

function SlackPanel({ detail, setDetail }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as SlackExtra;
  const [events, setEvents] = useState<string[]>(extra.events ?? []);
  const [defaultChannel, setDefaultChannel] = useState(extra.default_channel_id ?? "");
  const [routes, setRoutes] = useState<Record<string, string>>(extra.routes ?? {});
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const bot = extra.mode === "bot";
  const channels = useApi<{ items: SlackChannel[] }>(detail.enabled && bot ? "/messaging/slack/channels" : null);
  const list = channels.data?.items ?? [];
  const nameOf = (id: string) => list.find((c) => c.id === id)?.name ?? extra.channel_names?.[id] ?? (id === extra.default_channel_id ? extra.default_channel_name : null) ?? id;

  if (!detail.enabled) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;

  async function refresh() {
    setRefreshing(true);
    try {
      const data = await api.get<{ items: SlackChannel[] }>("/messaging/slack/channels?refresh=true");
      channels.setData(data);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setRefreshing(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const cleanRoutes = Object.fromEntries(Object.entries(routes).filter(([, id]) => id));
    const used = new Set([defaultChannel, ...Object.values(cleanRoutes)].filter(Boolean));
    try {
      const config: Record<string, unknown> = { events };
      if (bot) {
        config.default_channel_id = defaultChannel || null;
        config.default_channel_name = defaultChannel ? nameOf(defaultChannel) : null;
        config.routes = cleanRoutes;
        config.channel_names = Object.fromEntries([...used].map((id) => [id, nameOf(id)]));
      }
      const next = await api.put<IntegrationDetail>("/integration-hub/slack/settings", { config });
      toast("Slack alerts saved.");
      setDetail(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const webhookField = detail.integration.credential_fields.filter((f) => f.key === "webhook_url");

  return (
    <>
      <Card
        title="Alerts"
        icon={<Megaphone size={16} />}
        actions={extra.alerts_active ? <Badge tone="success" dot>Posting alerts</Badge> : <Badge tone="neutral" dot>Not posting</Badge>}
      >
        {!extra.mode ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Connect Slack above, or paste an incoming webhook below, to post alerts.
          </p>
        ) : (
          <form className="ui-stack" style={{ gap: 16 }} onSubmit={(event) => void save(event)}>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              {bot
                ? `AIVA posts as its own app${extra.team_name ? ` in ${extra.team_name}` : ""}. Public channels work right away; for a private channel, type /invite @AIVA in it first.`
                : "Alerts go to the channel your incoming webhook was made for. Connect the AIVA app to choose channels per alert."}
            </p>
            <fieldset className="imsg-fieldset">
              <legend>Post these events</legend>
              <div className="imsg-checks">
                {extra.event_options.map((option) => (
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
              <p className="ui-muted ui-small" style={{ margin: "8px 0 0" }}>
                Workflows can also post to Slack with the &ldquo;Post to Slack or Teams&rdquo; action.
              </p>
            </fieldset>
            {bot ? (
              <>
                <div className="ui-row" style={{ gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
                  <Field label="Default channel" className="imsg-grow">
                    <select className="ui-select" value={defaultChannel} onChange={(event) => setDefaultChannel(event.target.value)} disabled={!detail.can_manage}>
                      <option value="">{channels.loading && !list.length ? "Loading channels…" : "Choose a channel"}</option>
                      {defaultChannel && !list.some((c) => c.id === defaultChannel) ? <option value={defaultChannel}>#{nameOf(defaultChannel)}</option> : null}
                      {list.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.is_private ? "🔒 " : "# "}
                          {c.name}
                          {c.is_private && !c.is_member ? " (invite AIVA first)" : ""}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {detail.can_manage ? (
                    <button type="button" className="ui-btn" disabled={refreshing} onClick={() => void refresh()}>
                      {refreshing ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Refresh list
                    </button>
                  ) : null}
                </div>
                {channels.error ? <Alert tone="warning">{channels.error}</Alert> : null}
                <div>
                  <div className="settings-row-label">Send some alerts elsewhere</div>
                  <div className="ui-table-wrap" style={{ marginTop: 8 }}>
                    <table className="ui-table">
                      <thead>
                        <tr>
                          <th>Alert</th>
                          <th>Channel</th>
                        </tr>
                      </thead>
                      <tbody>
                        {extra.routable.map((option) => (
                          <tr key={option.key}>
                            <td>{option.label}</td>
                            <td>
                              <select
                                className="ui-select sm"
                                aria-label={`Channel for ${option.label}`}
                                value={routes[option.key] ?? ""}
                                disabled={!detail.can_manage}
                                onChange={(event) => setRoutes((current) => ({ ...current, [option.key]: event.target.value }))}
                              >
                                <option value="">Default channel</option>
                                {routes[option.key] && !list.some((c) => c.id === routes[option.key]) ? <option value={routes[option.key]}>#{nameOf(routes[option.key])}</option> : null}
                                {list.map((c) => (
                                  <option key={c.id} value={c.id}>
                                    {c.is_private ? "🔒 " : "# "}
                                    {c.name}
                                  </option>
                                ))}
                              </select>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            ) : null}
            {detail.can_manage ? (
              <div className="ui-row" style={{ gap: 10 }}>
                <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || (bot && !defaultChannel)}>
                  {busy ? <span className="ui-spinner" /> : null} Save alerts
                </button>
                {bot && !defaultChannel ? <span className="ui-muted ui-small">Choose a default channel first.</span> : null}
              </div>
            ) : null}
          </form>
        )}
      </Card>

      <Card title="Share records" icon={<Send size={16} />}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          Anyone can post a lead, contact, account, deal, case or quote to Slack with a note, from the record&apos;s <strong>Share to Slack</strong> button. The post links back to the record in AIVA.
        </p>
      </Card>

      {detail.can_manage ? (
        <Card title="Incoming webhook (optional)" icon={<Webhook size={16} />}>
          <div className="ui-stack" style={{ gap: 12 }}>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              Only if you can&apos;t install the AIVA app: create an incoming webhook in Slack (Apps → Incoming WebHooks) and paste it here. Alerts then go to that one channel.
              {bot ? " While the app is connected, the app is used and the webhook is a fallback." : ""}
            </p>
            {extra.legacy_webhook ? <Alert tone="info">An incoming webhook saved earlier is also in use; saving one here replaces it.</Alert> : null}
            {webhookField.length ? <CredentialsForm detail={detail} onSaved={setDetail} fields={webhookField} submitLabel="Save webhook" /> : null}
          </div>
        </Card>
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ WhatsApp */

const QUALITY: Record<string, Tone> = { GREEN: "success", YELLOW: "warning", RED: "danger" };
const TEMPLATE_TONE: Record<string, Tone> = { APPROVED: "success", PENDING: "warning", REJECTED: "danger", PAUSED: "warning", DISABLED: "neutral" };

function WhatsAppPanel({ detail, setDetail, reload }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as WhatsAppExtra;
  const [syncing, setSyncing] = useState(false);
  const [settingUp, setSettingUp] = useState(false);
  if (!detail.enabled) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;
  const connected = Boolean(detail.connection);

  async function sync() {
    setSyncing(true);
    try {
      const result = await api.post<{ synced: number; detail: WhatsAppExtra }>("/messaging/whatsapp/templates/sync");
      toast(`Synced ${result.synced} template${result.synced === 1 ? "" : "s"}.`);
      setDetail({ ...detail, extra: result.detail });
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSyncing(false);
    }
  }

  async function setupWebhook() {
    setSettingUp(true);
    try {
      await api.post("/messaging/whatsapp/webhook-setup");
      toast("Meta verified the URL. Messages will arrive in AIVA.");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSettingUp(false);
    }
  }

  const templates = extra.templates ?? [];

  return (
    <>
      {connected ? (
        <>
          <Card title="Phone number" icon={<Phone size={16} />} actions={<InboxLink />}>
            <div className="imsg-facts">
              <div>
                <span className="ui-muted ui-small">Business name</span>
                <strong>{extra.verified_name ?? "—"}</strong>
              </div>
              <div>
                <span className="ui-muted ui-small">Number</span>
                <strong>{extra.display_phone_number ?? prettyNumber(extra.business_number)}</strong>
              </div>
              <div>
                <span className="ui-muted ui-small">Quality rating</span>
                {extra.quality_rating ? <Badge tone={QUALITY[extra.quality_rating] ?? "neutral"} dot>{extra.quality_rating.toLowerCase()}</Badge> : <strong>—</strong>}
              </div>
              <div>
                <span className="ui-muted ui-small">Messaging limit</span>
                <strong>{extra.messaging_limit_tier ? extra.messaging_limit_tier.replace(/^TIER_/, "").toLowerCase() + " / day" : "—"}</strong>
              </div>
            </div>
            <StatsRow stats={extra.stats} />
          </Card>

          <Card
            title="Webhook"
            icon={<Webhook size={16} />}
            actions={extra.app_subscribed ? <Badge tone="success" dot>App subscribed</Badge> : <Badge tone="warning" dot>Not subscribed</Badge>}
          >
            <div className="ui-stack" style={{ gap: 12 }}>
              <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
                Replies and delivery receipts come to AIVA through this webhook. In your Meta app, open <strong>WhatsApp → Configuration</strong>, paste the callback URL and verify token, then subscribe to the <strong>messages</strong> field.
                {detail.can_manage ? " Or let AIVA set it on your business account for you." : ""}
              </p>
              <UrlRow label="Callback URL" value={extra.webhook_url} />
              {extra.verify_token ? <UrlRow label="Verify token" value={extra.verify_token} /> : null}
              {detail.can_manage ? (
                <div className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
                  <button type="button" className="ui-btn" disabled={settingUp} onClick={() => void setupWebhook()}>
                    {settingUp ? <span className="ui-spinner" /> : <ShieldCheck size={14} />} {extra.webhook_override ? "Set it again" : "Set it up for me"}
                  </button>
                  <span className="ui-muted ui-small">Every webhook is checked against your app secret before AIVA reads it.</span>
                </div>
              ) : null}
            </div>
          </Card>

          <Card
            title="Message templates"
            icon={<MessageSquareText size={16} />}
            flush
            actions={
              detail.can_manage ? (
                <button type="button" className="ui-btn ui-btn-sm" disabled={syncing} onClick={() => void sync()}>
                  {syncing ? <span className="ui-spinner" /> : <RefreshCw size={13} />} Sync
                </button>
              ) : null
            }
          >
            <p className="ui-muted ui-small" style={{ margin: 0, padding: "0 22px 12px" }}>
              Outside the 24-hour reply window, WhatsApp only allows approved templates. {extra.approved_templates ?? 0} approved
              {extra.templates_synced_at ? ` · synced ${relativeTime(extra.templates_synced_at)}` : ""}. Create templates in WhatsApp Manager.
            </p>
            {templates.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Template</th>
                      <th>Language</th>
                      <th>Category</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {templates.map((t) => (
                      <tr key={`${t.name}-${t.language}`}>
                        <td>
                          <div className="cell-title">{t.name}</div>
                          <div className="ui-muted ui-small ui-truncate" style={{ maxWidth: 380 }}>
                            {t.body}
                          </div>
                        </td>
                        <td className="ui-small">{t.language}</td>
                        <td className="ui-small">{(t.category ?? "—").toLowerCase()}</td>
                        <td>
                          <Badge tone={TEMPLATE_TONE[t.status] ?? "neutral"}>{t.status.toLowerCase()}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="ui-muted ui-small" style={{ margin: 0, padding: "0 22px 18px" }}>
                No templates yet.
              </p>
            )}
          </Card>
        </>
      ) : null}
      <ChannelSettings detail={detail} setDetail={setDetail} />
    </>
  );
}

/* ------------------------------------------------------------------ Twilio SMS */

function TwilioPanel({ detail, setDetail }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as TwilioExtra;
  const [busySid, setBusySid] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  if (!detail.enabled) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;
  const connected = Boolean(detail.connection);

  function withNumbers(numbers: TwilioNumber[]) {
    const mine = numbers.find((n) => n.phone_number === extra.from_number);
    setDetail({ ...detail, extra: { ...extra, numbers, number_points_here: mine ? mine.points_here : extra.number_points_here } });
  }

  async function point(sid: string) {
    setBusySid(sid);
    try {
      const result = await api.post<{ items: TwilioNumber[] }>(`/messaging/twilio/numbers/${sid}/use-aiva`);
      toast("Incoming texts to that number now come to AIVA.");
      withNumbers(result.items);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusySid(null);
    }
  }

  async function refresh() {
    setRefreshing(true);
    try {
      const result = await api.get<{ items: TwilioNumber[] }>("/messaging/twilio/numbers?refresh=true");
      withNumbers(result.items);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <>
      {connected ? (
        <>
          <Card title="Texting" icon={<Phone size={16} />} actions={<InboxLink />}>
            <div className="imsg-facts">
              <div>
                <span className="ui-muted ui-small">Account</span>
                <strong>
                  {extra.account_name ?? "—"}
                  {extra.account_type === "Trial" ? <Badge tone="warning">Trial</Badge> : null}
                </strong>
              </div>
              <div>
                <span className="ui-muted ui-small">Sends from</span>
                <strong>{extra.from_number ? prettyNumber(extra.from_number) : extra.messaging_service_name ?? "—"}</strong>
              </div>
              <div>
                <span className="ui-muted ui-small">Replies</span>
                {extra.number_points_here ? <Badge tone="success" dot>Arriving in AIVA</Badge> : <Badge tone="warning" dot>Webhook not set</Badge>}
              </div>
            </div>
            {extra.account_type === "Trial" ? <Alert tone="info">Twilio trial accounts can only text numbers you&apos;ve verified in Twilio.</Alert> : null}
            <StatsRow stats={extra.stats} />
          </Card>

          <Card title="Incoming texts" icon={<Webhook size={16} />}>
            <div className="ui-stack" style={{ gap: 12 }}>
              <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
                In Twilio, set the number&apos;s <strong>A message comes in</strong> webhook to this URL (HTTP POST)
                {extra.messaging_service_sid ? ", or the messaging service's incoming-message URL" : ""}. Delivery updates come back on their own; AIVA asks for them with every text.
                Every request is checked against your auth token&apos;s signature.
              </p>
              <UrlRow label="Webhook URL" value={extra.webhook_url} />
              {detail.can_manage ? (
                extra.numbers_error ? (
                  <Alert tone="warning">{extra.numbers_error}</Alert>
                ) : extra.numbers?.length ? (
                  <div className="ui-table-wrap">
                    <table className="ui-table">
                      <thead>
                        <tr>
                          <th>Number</th>
                          <th>Incoming texts</th>
                          <th style={{ textAlign: "right" }}>
                            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={refreshing} onClick={() => void refresh()} aria-label="Refresh numbers">
                              {refreshing ? <span className="ui-spinner" /> : <RefreshCw size={13} />}
                            </button>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {extra.numbers.map((n) => (
                          <tr key={n.sid}>
                            <td>
                              <div className="cell-title">{prettyNumber(n.phone_number)}</div>
                              <div className="ui-muted ui-small">
                                {n.friendly_name}
                                {n.phone_number === extra.from_number ? " · sends AIVA texts" : ""}
                              </div>
                            </td>
                            <td>
                              {n.points_here ? (
                                <Badge tone="success" dot>To AIVA</Badge>
                              ) : n.sms_url_set ? (
                                <Badge tone="neutral">Elsewhere</Badge>
                              ) : (
                                <Badge tone="warning">Not set</Badge>
                              )}
                            </td>
                            <td style={{ textAlign: "right" }}>
                              {!n.points_here && n.sms_capable ? (
                                <button type="button" className="ui-btn ui-btn-sm" disabled={busySid === n.sid} onClick={() => void point(n.sid)} title={n.sms_url_set ? "Replaces the number's current webhook" : undefined}>
                                  {busySid === n.sid ? <span className="ui-spinner" /> : null} Point at AIVA
                                </button>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null
              ) : null}
            </div>
          </Card>

          <Card title="Opt-outs" icon={<ShieldCheck size={16} />}>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              When someone replies STOP (or UNSUBSCRIBE, CANCEL, END, QUIT), their number goes on the{" "}
              <Link href="/settings/removal-requests">do-not-contact list</Link> and AIVA won&apos;t text or WhatsApp it. START takes them off again.
              <Link className="imsg-inline-link" href="/settings/removal-requests">
                Review the list <ArrowUpRight size={12} />
              </Link>
            </p>
          </Card>
        </>
      ) : null}
      <ChannelSettings detail={detail} setDetail={setDetail} />
    </>
  );
}

/**
 * Provider panels for the messaging group. Each panel renders the integration's own
 * settings and tools below the shared connection card on /integrations/[key].
 */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  slack: SlackPanel,
  whatsapp: WhatsAppPanel,
  twilio_sms: TwilioPanel,
};
