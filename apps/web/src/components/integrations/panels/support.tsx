"use client";

import { KeyRound, LifeBuoy, RefreshCw, Settings2, Webhook } from "lucide-react";
import { useState, type ComponentType } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { NumberInput, SettingRow, Toggle, useMembers } from "@/components/settings-kit";
import { Alert, Badge, Card, Kpi } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

import { UrlRow } from "../common";
import { TicketRow, type DeskKey, type DeskOptions, type TicketItem } from "../record/support-kit";
import type { IntegrationDetail, PanelProps } from "../types";

/* ------------------------------------------------------------------ shape of detail.extra */

type HelpdeskSettings = {
  match_accounts_by_domain?: boolean;
  create_cases?: boolean;
  sync_case_status?: boolean;
  notify_customer?: boolean;
  backfill_days?: number;
  create_leads?: boolean;
  lead_owner_id?: string | null;
  default_mailbox_id?: string | null;
  service_desk_id?: string | null;
  request_type_id?: string | null;
};

type HelpdeskExtra = {
  kind?: "ticket" | "conversation";
  settings?: HelpdeskSettings;
  stats?: { total: number; open: number; matched: number; cases: number };
  webhook_url?: string | null;
  webhook?: { mode: "automatic" | "manual"; status: string | null; error?: string | null; signed: boolean; last_received_at: string | null };
  sync?: { last_sync_at: string | null; catching_up: boolean; backoff_until: string | null };
  options?: DeskOptions;
  account?: Record<string, string>;
  recent?: TicketItem[];
  error?: string;
};

/* ------------------------------------------------------------------ per-desk webhook set-up */

const MANUAL_STEPS: Partial<Record<DeskKey, (url: string) => string[]>> = {
  freshdesk: () => [
    "In Freshdesk: Admin → Workflows → Automations → Ticket updates → New rule.",
    "When: “Ticket is created” or “Ticket is updated” (any change you care about, like status or priority). Performed by: anyone.",
    "Action: Trigger webhook → POST to the URL below, encoding JSON, content: {\"ticket_id\": \"{{ticket.id}}\"}.",
    "If you created a secret below, add a custom header X-AIVA-Webhook-Secret with it.",
    "Save the rule. AIVA only takes the ticket number from the webhook and reads the ticket from Freshdesk.",
  ],
  intercom: () => [
    "In Intercom: Developer Hub → your app → Webhooks.",
    "Paste the URL below as the endpoint.",
    "Subscribe to: conversation.user.created, conversation.user.replied, conversation.admin.replied, conversation.admin.closed, conversation.admin.snoozed, conversation.admin.opened and contact.lead.created.",
    "Paste the app's client secret (Basic information) into the Intercom connection above, so AIVA can check every webhook's signature.",
  ],
  jira_service_management: () => [
    "In Jira: Settings (⚙) → System → WebHooks → Create a WebHook.",
    "URL: paste the URL below. Secret: create one below and paste it.",
    "Events: Issue created and Issue updated. JQL: project = your service desk's project key.",
    "Save. AIVA reads the request back from Jira before changing anything.",
  ],
};

const DESK_TERMS: Record<DeskKey, { item: string; items: string }> = {
  zendesk: { item: "ticket", items: "tickets" },
  freshdesk: { item: "ticket", items: "tickets" },
  intercom: { item: "conversation", items: "conversations" },
  help_scout: { item: "conversation", items: "conversations" },
  jira_service_management: { item: "request", items: "requests" },
};

/* ------------------------------------------------------------------ panel */

function HelpdeskPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as HelpdeskExtra;
  const key = detail.integration.key as DeskKey;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;
  const terms = DESK_TERMS[key];
  const stats = extra.stats ?? { total: 0, open: 0, matched: 0, cases: 0 };
  const connected = Boolean(detail.connection);

  return (
    <>
      {connected ? (
        <div className="isup-kpis">
          <Kpi label={`${terms.items[0].toUpperCase()}${terms.items.slice(1)} in AIVA`} value={stats.total} />
          <Kpi label="Open" value={stats.open} />
          <Kpi label="On a contact or account" value={stats.matched} />
          <Kpi label="Linked to cases" value={stats.cases} />
        </div>
      ) : null}
      {connected ? <WebhookCard detail={detail} setDetail={setDetail} reload={reload} /> : null}
      <SettingsCard detail={detail} setDetail={setDetail} reload={reload} />
      {connected ? <SyncCard detail={detail} setDetail={setDetail} reload={reload} /> : null}
      {connected && extra.recent ? (
        <Card title={`Recent ${terms.items}`} icon={<LifeBuoy size={16} />}>
          {extra.recent.length ? (
            <ul className="mini-list isup-list">
              {extra.recent.map((item) => (
                <TicketRow key={item.external_id} item={item} />
              ))}
            </ul>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Nothing yet. {terms.items[0].toUpperCase()}
              {terms.items.slice(1)} appear here as they change, and on contacts and accounts when someone opens them.
            </p>
          )}
        </Card>
      ) : null}
    </>
  );
}

function WebhookCard({ detail, setDetail, reload }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as HelpdeskExtra;
  const key = detail.integration.key as DeskKey;
  const webhook = extra.webhook;
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  if (!webhook) return null;
  const manage = detail.can_manage;
  const steps = MANUAL_STEPS[key]?.(extra.webhook_url ?? "") ?? [];
  const secretDesk = key === "freshdesk" || key === "jira_service_management";

  async function register() {
    setBusy(true);
    try {
      await api.post(`/helpdesk/${key}/webhook/register`);
      toast("Webhook registered again.");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function newSecret() {
    setBusy(true);
    try {
      const result = await api.post<{ secret: string }>(`/helpdesk/${key}/webhook-secret`);
      setSecret(result.secret);
      setDetail({ ...detail, extra: { ...extra, webhook: { ...webhook!, signed: true } } } as IntegrationDetail);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function removeSecret() {
    setBusy(true);
    try {
      await api.del(`/helpdesk/${key}/webhook-secret`);
      setSecret(null);
      setDetail({ ...detail, extra: { ...extra, webhook: { ...webhook!, signed: false } } } as IntegrationDetail);
      toast("Secret removed.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const statusBadge =
    webhook.mode === "automatic" ? (
      webhook.status === "active" ? <Badge tone="success" dot>Registered</Badge> : <Badge tone="warning" dot>Not registered</Badge>
    ) : webhook.last_received_at ? (
      <Badge tone="success" dot>Receiving</Badge>
    ) : (
      <Badge tone="neutral" dot>Waiting for the first update</Badge>
    );

  return (
    <Card title="Live updates" icon={<Webhook size={16} />} actions={statusBadge}>
      <div className="ui-stack" style={{ gap: 12 }}>
        {webhook.mode === "automatic" ? (
          <p className="ui-small isup-text">
            AIVA registered a {webhook.signed ? "signed " : ""}webhook in {detail.integration.name}, so changes arrive within seconds. Everything also syncs every few minutes.
            {webhook.last_received_at ? ` Last update ${relativeTime(webhook.last_received_at)}.` : ""}
          </p>
        ) : (
          <p className="ui-small isup-text">
            Changes sync every few minutes. For updates within seconds, add AIVA&apos;s webhook in {detail.integration.name}
            {webhook.last_received_at ? ` (last update ${relativeTime(webhook.last_received_at)})` : ""}.
          </p>
        )}
        {webhook.error ? <Alert tone="warning">{webhook.error}</Alert> : null}
        {manage && steps.length ? (
          <ol className="isup-steps">
            {steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        ) : null}
        {manage ? <UrlRow label="Webhook URL" value={extra.webhook_url} hint={webhook.mode === "automatic" ? "Already registered for you; shown for reference." : undefined} /> : null}
        {manage && key === "intercom" ? (
          <p className="ui-small isup-text">
            Signature checks: {webhook.signed ? <Badge tone="success">On</Badge> : <Badge tone="warning">Off: add the client secret to the connection</Badge>}
          </p>
        ) : null}
        {manage && secretDesk ? (
          <div className="isup-secret">
            <div className="ui-small isup-text">
              {webhook.signed
                ? key === "jira_service_management"
                  ? "A webhook secret is set: Jira must sign each webhook with it (X-Hub-Signature)."
                  : "A webhook secret is set: Freshdesk must send it in the X-AIVA-Webhook-Secret header."
                : "Optional: a secret makes AIVA refuse webhooks that don't carry it."}
            </div>
            {secret ? (
              <Alert tone="info">
                Copy this secret now; it isn&apos;t shown again.
                <div style={{ marginTop: 8 }}>
                  <CopyField value={secret} label="Copy the webhook secret" />
                </div>
              </Alert>
            ) : null}
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void newSecret()}>
                <KeyRound size={13} /> {webhook.signed ? "Create a new secret" : "Create a secret"}
              </button>
              {webhook.signed ? (
                <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy} onClick={() => void removeSecret()}>
                  Remove the secret
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
        {manage && webhook.mode === "automatic" && webhook.status !== "active" ? (
          <div>
            <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void register()}>
              {busy ? <span className="ui-spinner" /> : <RefreshCw size={13} />} Register the webhook again
            </button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function SettingsCard({ detail, setDetail, reload }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as HelpdeskExtra;
  const key = detail.integration.key as DeskKey;
  const terms = DESK_TERMS[key];
  const saved = extra.settings ?? {};
  const [draft, setDraft] = useState<HelpdeskSettings>(saved);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const { members } = useMembers();
  const locked = !detail.can_manage;
  const options = extra.options ?? {};
  const changed = JSON.stringify(draft) !== JSON.stringify(saved);

  async function save() {
    setBusy(true);
    try {
      const next = await api.put<IntegrationDetail>(`/integration-hub/${key}/settings`, { config: draft });
      setDetail(next);
      setDraft(((next.extra as HelpdeskExtra).settings ?? draft) as HelpdeskSettings);
      toast("Settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function refreshLists() {
    setRefreshing(true);
    try {
      await api.post(`/helpdesk/${key}/options/refresh`);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setRefreshing(false);
    }
  }

  const set = (patch: Partial<HelpdeskSettings>) => setDraft((current) => ({ ...current, ...patch }));

  return (
    <Card title="Settings" icon={<Settings2 size={16} />}>
      <div className="isup-settings">
        <SettingRow label="Match accounts by email domain" hint={`A ${terms.item} from someone@globex.com shows on the Globex account even when they aren't a contact yet.`}>
          <Toggle label="Match accounts by email domain" checked={draft.match_accounts_by_domain ?? true} disabled={locked} onChange={(v) => set({ match_accounts_by_domain: v })} />
        </SettingRow>
        <SettingRow label={`Open an AIVA case for new ${terms.items}`} hint={`Only ${terms.items} created after you turn this on, never the ones AIVA opened.`}>
          <Toggle label="Open AIVA cases" checked={Boolean(draft.create_cases)} disabled={locked} onChange={(v) => set({ create_cases: v })} />
        </SettingRow>
        <SettingRow label="Resolve linked cases" hint={`When a linked ${terms.item} is solved or closed, resolve the AIVA case too. Status changes are always noted on the case.`}>
          <Toggle label="Resolve linked cases" checked={Boolean(draft.sync_case_status)} disabled={locked} onChange={(v) => set({ sync_case_status: v })} />
        </SettingRow>
        {key === "zendesk" ? (
          <SettingRow label="Public first message" hint="Tickets opened from AIVA start with a public comment, which Zendesk emails to the customer. Off: an internal note.">
            <Toggle label="Public first message" checked={Boolean(draft.notify_customer)} disabled={locked} onChange={(v) => set({ notify_customer: v })} />
          </SettingRow>
        ) : null}
        <SettingRow label="History to sync" hint={`How far back AIVA reads ${terms.items} when it starts.`}>
          <NumberInput label="Days of history" value={draft.backfill_days ?? 30} min={0} max={365} disabled={locked} suffix="days" width={90} onChange={(v) => set({ backfill_days: v ?? 30 })} />
        </SettingRow>
        {key === "intercom" ? (
          <>
            <SettingRow label="Create AIVA leads from Intercom leads" hint="New Intercom leads with an email address become AIVA leads (needs the contact.lead.created webhook topic).">
              <Toggle label="Create leads" checked={Boolean(draft.create_leads)} disabled={locked} onChange={(v) => set({ create_leads: v })} />
            </SettingRow>
            {draft.create_leads ? (
              <SettingRow label="Lead owner">
                <select className="ui-select sm" value={draft.lead_owner_id ?? ""} disabled={locked} style={{ width: 220 }} onChange={(event) => set({ lead_owner_id: event.target.value || null })}>
                  <option value="">Assignment rules decide</option>
                  {members
                    .filter((m) => m.is_active)
                    .map((m) => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.full_name}
                      </option>
                    ))}
                </select>
              </SettingRow>
            ) : null}
          </>
        ) : null}
        {key === "help_scout" && detail.connection ? (
          <SettingRow label="Default mailbox" hint="Where conversations opened from AIVA go.">
            <span className="ui-row" style={{ gap: 6 }}>
              <select className="ui-select sm" value={draft.default_mailbox_id ?? ""} disabled={locked} style={{ width: 220 }} onChange={(event) => set({ default_mailbox_id: event.target.value || null })}>
                <option value="">Ask each time</option>
                {(options.mailboxes ?? []).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name ?? m.email ?? m.id}
                  </option>
                ))}
              </select>
              {!locked ? (
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => void refreshLists()} disabled={refreshing} aria-label="Reload mailboxes">
                  {refreshing ? <span className="ui-spinner" /> : <RefreshCw size={13} />}
                </button>
              ) : null}
            </span>
          </SettingRow>
        ) : null}
        {key === "jira_service_management" && detail.connection ? (
          <>
            <SettingRow label="Service desk" hint="Where escalations and new requests go.">
              <span className="ui-row" style={{ gap: 6 }}>
                <select
                  className="ui-select sm"
                  value={draft.service_desk_id ?? options.service_desk_id ?? ""}
                  disabled={locked}
                  style={{ width: 220 }}
                  onChange={(event) => set({ service_desk_id: event.target.value, request_type_id: null })}
                >
                  {(options.service_desks ?? []).map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name ?? d.id}
                      {d.project_key ? ` (${d.project_key})` : ""}
                    </option>
                  ))}
                </select>
                {!locked ? (
                  <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => void refreshLists()} disabled={refreshing} aria-label="Reload service desks">
                    {refreshing ? <span className="ui-spinner" /> : <RefreshCw size={13} />}
                  </button>
                ) : null}
              </span>
            </SettingRow>
            <SettingRow label="Request type" hint={(draft.service_desk_id ?? options.service_desk_id) !== options.service_desk_id ? "Save to load the new desk's request types." : undefined}>
              <select
                className="ui-select sm"
                value={draft.request_type_id ?? options.request_type_id ?? ""}
                disabled={locked || (draft.service_desk_id ?? options.service_desk_id) !== options.service_desk_id}
                style={{ width: 220 }}
                onChange={(event) => set({ request_type_id: event.target.value })}
              >
                {(options.request_types ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name ?? t.id}
                  </option>
                ))}
              </select>
            </SettingRow>
          </>
        ) : null}
      </div>
      {!locked ? (
        <div className="isup-save">
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy || !changed} onClick={() => void save()}>
            {busy ? <span className="ui-spinner" /> : null} Save settings
          </button>
        </div>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: "10px 0 0" }}>
          Only admins can change these.
        </p>
      )}
    </Card>
  );
}

function SyncCard({ detail, reload }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as HelpdeskExtra;
  const key = detail.integration.key as DeskKey;
  const terms = DESK_TERMS[key];
  const sync = extra.sync;
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      const result = await api.post<{ stored: number; catching_up: boolean; last_error: string | null }>(`/helpdesk/${key}/sync`);
      if (result.last_error) toast(result.last_error, "danger");
      else toast(`Read ${result.stored} ${result.stored === 1 ? terms.item : terms.items}${result.catching_up ? "; the rest follows in the background" : ""}.`);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const backoff = sync?.backoff_until && new Date(sync.backoff_until) > new Date();
  return (
    <Card
      title="Sync"
      icon={<RefreshCw size={16} />}
      actions={
        detail.can_manage ? (
          <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void run()}>
            {busy ? <span className="ui-spinner" /> : <RefreshCw size={13} />} Sync now
          </button>
        ) : null
      }
    >
      <p className="ui-small isup-text" style={{ margin: 0 }}>
        {sync?.last_sync_at ? `Last synced ${relativeTime(sync.last_sync_at)}.` : "Not synced yet; the first sync starts within a few minutes."}
        {sync?.catching_up ? ` Still reading older ${terms.items}.` : ""}
        {backoff ? ` ${detail.integration.name} asked AIVA to slow down; syncing resumes ${relativeTime(sync!.backoff_until!)}.` : ""}
      </p>
    </Card>
  );
}

export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  zendesk: HelpdeskPanel,
  freshdesk: HelpdeskPanel,
  intercom: HelpdeskPanel,
  help_scout: HelpdeskPanel,
  jira_service_management: HelpdeskPanel,
};
