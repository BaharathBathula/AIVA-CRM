"use client";

import { ArrowUpRight, CreditCard, RefreshCw, Repeat, Settings2, Webhook } from "lucide-react";
import { useState, type ComponentType, type FormEvent, type ReactNode } from "react";

import { useToast } from "@/components/providers";
import { Alert, Badge, Card, Field, Kpi } from "@/components/ui";
import { date, relativeTime } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

import { UrlRow } from "../common";
import { amountText, PaymentsTable, SUBSCRIPTION_TONE, type PaymentRow } from "../record/payments-kit";
import type { IntegrationDetail, PanelProps } from "../types";

/* ------------------------------------------------------------------ shapes of detail.extra */

type Stats = { payments_30d: number; refunds_30d: number; received_30d: { currency: string; amount: string }[]; open_links: number };

type PaymentsExtra = {
  settings?: {
    notify_owner?: boolean;
    link_message?: string;
    create_accounts?: boolean;
    task_on_cancel?: boolean;
    task_on_payment_failed?: boolean;
    task_on_renewal?: boolean;
    renewal_days?: number;
  };
  stats?: Stats;
  recent?: PaymentRow[];
  webhook_url?: string | null;
  webhook_mode?: "webhook" | "polling" | null;
  webhook_ready?: boolean;
  webhook_auto?: boolean;
  account?: Record<string, string | boolean | null>;
  synced_at?: string | null;
  subscriptions?: { counts: Record<string, number>; mrr: { currency: string; amount: string }[]; next_renewal: string | null };
  accounts_linked?: number;
  error?: string;
};

const NAMES: Record<string, string> = { stripe: "Stripe", paypal: "PayPal", square: "Square", chargebee: "Chargebee" };

/* ------------------------------------------------------------------ shared bits */

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <span className="ui-muted ui-small">{label}</span>
      <strong>{children ?? "—"}</strong>
    </div>
  );
}

function ModeBadge({ mode }: { mode: unknown }) {
  if (!mode) return null;
  const test = mode === "test" || mode === "sandbox";
  return <Badge tone={test ? "warning" : "success"}>{test ? (mode === "test" ? "Test mode" : "Sandbox") : "Live"}</Badge>;
}

function StatsRow({ stats }: { stats?: Stats }) {
  if (!stats) return null;
  return (
    <div className="ipay-kpis">
      <Kpi label="Payments (30 days)" value={stats.payments_30d} />
      <Kpi label="Received" value={stats.received_30d.length ? stats.received_30d.map((r) => amountText(r.amount, r.currency)).join(" + ") : "—"} />
      <Kpi label="Open links" value={stats.open_links} />
      <Kpi label="Refunds" value={stats.refunds_30d} />
    </div>
  );
}

function RecentCard({ extra }: { extra: PaymentsExtra }) {
  return (
    <Card title="Recent payments" icon={<CreditCard size={16} />}>
      <PaymentsTable rows={extra.recent ?? []} showInvoice empty="No payments yet. Create a payment link from an invoice to get started." />
    </Card>
  );
}

function useSave(detail: IntegrationDetail, setDetail: (detail: IntegrationDetail) => void) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function save(config: Record<string, unknown>) {
    setBusy(true);
    try {
      const next = await api.put<IntegrationDetail>(`/integration-hub/${detail.integration.key}/settings`, { config });
      toast("Settings saved.");
      setDetail(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }
  return { save, busy };
}

function ProcessorSettings({ detail, setDetail }: Pick<PanelProps, "detail" | "setDetail">) {
  const extra = detail.extra as PaymentsExtra;
  const [notify, setNotify] = useState(extra.settings?.notify_owner ?? true);
  const [message, setMessage] = useState(extra.settings?.link_message ?? "");
  const { save, busy } = useSave(detail, setDetail);
  const name = NAMES[detail.integration.key] ?? detail.integration.name;
  function submit(event: FormEvent) {
    event.preventDefault();
    void save({ notify_owner: notify, link_message: message.trim() });
  }
  return (
    <Card title="Settings" icon={<Settings2 size={16} />}>
      <form className="ui-stack" style={{ gap: 14 }} onSubmit={submit}>
        <Field label="Message on the payment page" hint={`Shown by ${name} next to the payment${detail.integration.key === "stripe" ? " confirmation" : ""}. Leave blank for a plain thank-you.`}>
          <textarea className="ui-input" rows={2} maxLength={500} value={message} onChange={(event) => setMessage(event.target.value)} disabled={!detail.can_manage} />
        </Field>
        <label className="ui-check">
          <input type="checkbox" checked={notify} onChange={(event) => setNotify(event.target.checked)} disabled={!detail.can_manage} />
          Notify the invoice&apos;s owner when a payment or refund comes in
        </label>
        {detail.can_manage ? (
          <div>
            <button type="submit" className="ui-btn ui-btn-primary" disabled={busy}>
              {busy ? <span className="ui-spinner" /> : null} Save settings
            </button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

function WebhookCard({ detail, reload, children }: Pick<PanelProps, "detail" | "reload"> & { children?: ReactNode }) {
  const toast = useToast();
  const extra = detail.extra as PaymentsExtra;
  const [busy, setBusy] = useState(false);
  const key = detail.integration.key;
  const name = NAMES[key] ?? detail.integration.name;
  async function repair() {
    setBusy(true);
    try {
      const result = await api.post<{ message: string }>(`/payments/${key}/webhook`);
      toast(result.message);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }
  const polling = extra.webhook_mode === "polling";
  return (
    <Card
      title="Payment updates"
      icon={<Webhook size={16} />}
      actions={
        extra.webhook_ready && !polling ? (
          <Badge tone="success" dot>
            Webhook on
          </Badge>
        ) : (
          <Badge tone="warning" dot>
            {polling ? "Checking every 30 min" : "Webhook not set"}
          </Badge>
        )
      }
    >
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          {polling
            ? `${name} can only send webhooks to a public https address, so AIVA asks ${name} about open payment links every 30 minutes. Set APP_BASE_URL to your public address and set the webhook up again.`
            : `${name} tells AIVA the moment a payment or refund happens. Every message is verified${key === "paypal" ? " with PayPal" : " by its signature"}, and AIVA re-reads the payment from ${name} before changing an invoice.`}
        </p>
        {children}
        {detail.can_manage ? <UrlRow label="Webhook URL" value={extra.webhook_url} /> : null}
        {detail.can_manage && key !== "chargebee" ? (
          <div>
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => void repair()} disabled={busy}>
              {busy ? <span className="ui-spinner" /> : <RefreshCw size={13} />} {extra.webhook_ready ? "Set up the webhook again" : "Set up the webhook"}
            </button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function Guard({ detail, children }: { detail: IntegrationDetail; children: ReactNode }) {
  const extra = detail.extra as PaymentsExtra;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;
  return <>{children}</>;
}

/* ------------------------------------------------------------------ Stripe, PayPal, Square */

function ProcessorPanel(props: PanelProps) {
  const { detail } = props;
  const extra = detail.extra as PaymentsExtra;
  const key = detail.integration.key;
  const account = extra.account ?? {};
  const connected = Boolean(detail.connection);
  return (
    <Guard detail={detail}>
      {connected ? (
        <>
          <Card title="Taking payments" icon={<CreditCard size={16} />} actions={<ModeBadge mode={account.mode} />}>
            <div className="ui-stack" style={{ gap: 14 }}>
              <div className="ipay-facts">
                {key === "stripe" ? (
                  <>
                    <Fact label="Account">{(account.name as string) || (account.id as string) || "Stripe account"}</Fact>
                    <Fact label="Default currency">{(account.default_currency as string) || "—"}</Fact>
                    <Fact label="Key">{account.key_type === "restricted" ? "Restricted key" : "Secret key"}</Fact>
                  </>
                ) : null}
                {key === "paypal" ? (
                  <>
                    <Fact label="App">{(account.app_id as string) || "PayPal REST app"}</Fact>
                    <Fact label="Payment links">PayPal invoices (PayPal, cards, Venmo, Pay Later)</Fact>
                  </>
                ) : null}
                {key === "square" ? (
                  <>
                    <Fact label="Business">{(account.name as string) || "Square"}</Fact>
                    <Fact label="Location">{(account.location_name as string) || "Main location"}</Fact>
                    <Fact label="Currency">{(account.currency as string) || "—"}</Fact>
                  </>
                ) : null}
              </div>
              {key === "stripe" && account.charges_enabled === false ? (
                <Alert tone="warning">Stripe hasn&apos;t enabled card payments on this account yet. Finish the account setup in Stripe.</Alert>
              ) : null}
              <StatsRow stats={extra.stats} />
              <p className="ui-small ui-muted" style={{ margin: 0 }}>
                Create payment links from an invoice&apos;s <strong>Online payment</strong> card. Paid invoices update by themselves.
              </p>
            </div>
          </Card>
          <WebhookCard detail={detail} reload={props.reload}>
            {key === "square" && !extra.webhook_auto && !extra.webhook_ready ? (
              <Alert tone="info">
                If AIVA can&apos;t create the subscription with this token: in the Square Developer Console open your app → Webhooks → Add subscription, paste the URL below, pick
                payment.created, payment.updated, refund.created and refund.updated, then paste its signature key in the connection above.
              </Alert>
            ) : null}
          </WebhookCard>
          <RecentCard extra={extra} />
        </>
      ) : null}
      <ProcessorSettings detail={detail} setDetail={props.setDetail} />
    </Guard>
  );
}

/* ------------------------------------------------------------------ Chargebee */

function ChargebeePanel(props: PanelProps) {
  const { detail, setDetail, reload } = props;
  const toast = useToast();
  const extra = detail.extra as PaymentsExtra;
  const settings = extra.settings ?? {};
  const [syncing, setSyncing] = useState(false);
  const [form, setForm] = useState({
    create_accounts: settings.create_accounts ?? false,
    task_on_cancel: settings.task_on_cancel ?? true,
    task_on_payment_failed: settings.task_on_payment_failed ?? true,
    task_on_renewal: settings.task_on_renewal ?? false,
    renewal_days: String(settings.renewal_days ?? 30),
  });
  const { save, busy } = useSave(detail, setDetail);
  const connected = Boolean(detail.connection);
  const subs = extra.subscriptions;

  async function sync() {
    setSyncing(true);
    try {
      const result = await api.post<{ message: string }>("/payments/chargebee/sync");
      toast(result.message);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSyncing(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void save({ ...form, renewal_days: Number(form.renewal_days) || 30 });
  }

  const check = (name: keyof typeof form, label: string) => (
    <label className="ui-check">
      <input type="checkbox" checked={Boolean(form[name])} onChange={(event) => setForm({ ...form, [name]: event.target.checked })} disabled={!detail.can_manage} />
      {label}
    </label>
  );

  return (
    <Guard detail={detail}>
      {connected ? (
        <>
          <Card
            title="Subscriptions"
            icon={<Repeat size={16} />}
            actions={
              <>
                <ModeBadge mode={extra.account?.mode} />
                {detail.can_manage ? (
                  <button type="button" className="ui-btn ui-btn-sm" onClick={() => void sync()} disabled={syncing}>
                    {syncing ? <span className="ui-spinner" /> : <RefreshCw size={13} />} Sync now
                  </button>
                ) : null}
              </>
            }
          >
            <div className="ui-stack" style={{ gap: 14 }}>
              <div className="ipay-kpis">
                <Kpi label="MRR" value={subs?.mrr.length ? subs.mrr.map((m) => amountText(m.amount, m.currency)).join(" + ") : "—"} />
                <Kpi label="Active" value={(subs?.counts.active ?? 0) + (subs?.counts.non_renewing ?? 0)} note={subs?.counts.in_trial ? `${subs.counts.in_trial} in trial` : undefined} />
                <Kpi label="Cancelled" value={subs?.counts.cancelled ?? 0} />
                <Kpi label="Accounts linked" value={extra.accounts_linked ?? 0} />
              </div>
              <div className="ipay-chips">
                {Object.entries(subs?.counts ?? {}).map(([status, count]) => (
                  <Badge key={status} tone={SUBSCRIPTION_TONE[status] ?? "neutral"}>
                    {status.replace(/_/g, " ")}: {count}
                  </Badge>
                ))}
              </div>
              <p className="ui-small ui-muted" style={{ margin: 0 }}>
                {extra.synced_at ? `Last synced ${relativeTime(extra.synced_at)}.` : "The first sync runs within a few minutes."}
                {subs?.next_renewal ? ` Next renewal ${date(subs.next_renewal)}.` : ""} Subscriptions show on each account&apos;s Billing card.
              </p>
            </div>
          </Card>
          <WebhookCard detail={detail} reload={reload}>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              In Chargebee: <strong>Settings → Configure Chargebee → Webhooks → Add webhook</strong>, paste the URL below and leave authentication off. Chargebee can&apos;t sign
              webhooks, so AIVA reads each event back from Chargebee by its ID before using it. AIVA also syncs every 15 minutes.
            </p>
          </WebhookCard>
          <RecentCard extra={extra} />
        </>
      ) : null}
      <Card title="Settings" icon={<Settings2 size={16} />}>
        <form className="ui-stack" style={{ gap: 12 }} onSubmit={submit}>
          {check("create_accounts", "Create an AIVA account for Chargebee customers that don't match one")}
          {check("task_on_cancel", "Task for the account owner when a subscription is cancelled")}
          {check("task_on_payment_failed", "Task for the account owner when a payment fails")}
          {check("task_on_renewal", "Task for the account owner before a renewal")}
          {form.task_on_renewal ? (
            <Field label="Days before renewal">
              <input className="ui-input" inputMode="numeric" style={{ maxWidth: 120 }} value={form.renewal_days}
                     onChange={(event) => setForm({ ...form, renewal_days: event.target.value.replace(/\D/g, "").slice(0, 3) })} disabled={!detail.can_manage} />
            </Field>
          ) : null}
          <p className="ui-small ui-muted" style={{ margin: 0 }}>
            Chargebee customers are matched to accounts by company name, then email domain, then a contact&apos;s email.
          </p>
          {detail.can_manage ? (
            <div>
              <button type="submit" className="ui-btn ui-btn-primary" disabled={busy}>
                {busy ? <span className="ui-spinner" /> : null} Save settings
              </button>
            </div>
          ) : null}
        </form>
      </Card>
      {connected && detail.connection?.config?.site ? (
        <p className="ui-small" style={{ margin: 0 }}>
          <a href={`https://${String(detail.connection.config.site)}.chargebee.com`} target="_blank" rel="noopener noreferrer">
            Open {String(detail.connection.config.site)}.chargebee.com <ArrowUpRight size={12} />
          </a>
        </p>
      ) : null}
    </Guard>
  );
}

/** Provider panels for this group, by integration key. */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  stripe: ProcessorPanel,
  paypal: ProcessorPanel,
  square: ProcessorPanel,
  chargebee: ChargebeePanel,
};
