"use client";

import Link from "next/link";
import { AlertTriangle, Boxes, Building2, ExternalLink, KeyRound, Landmark, PackageSearch, RefreshCw, RotateCcw, Settings2, ShieldCheck } from "lucide-react";
import { useEffect, useState, type ComponentType, type FormEvent, type ReactNode } from "react";

import { useToast } from "@/components/providers";
import { SettingRow, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, Kpi } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import type { IntegrationDetail, PanelProps } from "../types";
import { PaymentsList, type AccountingPayment } from "./accounting";

// ===========================================================================
// Shapes (apps/api/app/integrations/providers/netsuite.py detail())
// ===========================================================================

type Option = { id: string; name: string; sku?: string | null; type?: string; rate?: string | null };
type Options = {
  items: Option[];
  tax_codes: Option[];
  terms: Option[];
  locations: Option[];
  departments: Option[];
  classes: Option[];
  price_levels: Option[];
  cached_at?: string;
};

type OrderSource = "sales_order" | "quote" | "deal";

export type NetSuiteSettings = {
  sync_mode: "auto" | "manual";
  sync_from: string | null;
  numbering: "provider" | "aiva";
  default_item: string | null;
  item_map: Record<string, string>;
  tax_code: string | null;
  tax_code_exempt: string | null;
  terms: string | null;
  notify_owner: boolean;
  create_customers: boolean;
  location: string | null;
  department: string | null;
  class_id: string | null;
  items_sync: "off" | "update" | "create";
  price_level: string | null;
  stock_sync: boolean;
  stock_location: string | null;
  orders_from: OrderSource[];
  order_mode: "auto" | "manual";
  order_status: "A" | "B";
  order_form: string | null;
  order_status_back: boolean;
  contacts_sync: boolean;
  customers_from: "needed" | "customers";
};

type ErpInfo = {
  account_id?: string | null;
  sandbox?: boolean;
  subsidiary?: { id: string; name: string | null } | null;
  items?: { linked: number; removed: number; caught_up: boolean; checked_at: string | null; imported: number; stock_checked_at: string | null };
  orders?: Record<string, number>;
  contacts?: Record<string, number>;
  order_errors?: { record_type: string; record_id: string; label: string | null; message: string; at: string; retry_at: string | null }[];
  credentials?: { consumer_key: string | null; token_id: string | null };
};

type NetSuiteExtra = {
  company: { name?: string; currency?: string; multi_currency?: boolean; one_world?: boolean; sandbox?: boolean } | null;
  choices: { id: string; name: string }[];
  scope_id: string | null;
  counts: Record<string, number>;
  waiting: number;
  no_account: number;
  errors: { invoice_id: string; number: string; message: string; at: string; retry_at: string | null }[];
  recent_payments: AccountingPayment[];
  last_run_at: string | null;
  last_counts: Record<string, number>;
  paused_until: string | null;
  last_error: string | null;
  home_url: string | null;
  sync_mode: string;
  settings?: NetSuiteSettings & Record<string, unknown>;
  erp?: ErpInfo;
  plan?: { inventory: boolean; sales_orders: boolean };
  error?: string;
};

const SOURCE_LABEL: Record<OrderSource, string> = {
  sales_order: "Confirmed sales orders",
  quote: "Accepted quotes (until they become a sales order)",
  deal: "Won deals without a quote or order",
};

const RECORD_HREF: Record<string, (id: string) => string> = {
  sales_order: (id) => `/sales-orders/${id}`,
  quote: (id) => `/quotes/${id}`,
  opportunity: (id) => `/opportunities/${id}`,
};

const SETTING_KEYS: (keyof NetSuiteSettings)[] = [
  "sync_mode", "sync_from", "numbering", "default_item", "item_map", "tax_code", "tax_code_exempt", "terms", "notify_owner", "create_customers",
  "location", "department", "class_id", "items_sync", "price_level", "stock_sync", "stock_location", "orders_from", "order_mode", "order_status",
  "order_form", "order_status_back", "contacts_sync", "customers_from",
];

function pickSettings(raw: NetSuiteExtra["settings"]): NetSuiteSettings | null {
  if (!raw) return null;
  return Object.fromEntries(SETTING_KEYS.map((key) => [key, raw[key]])) as NetSuiteSettings;
}

function sum(values: Record<string, number> | undefined, ...keys: string[]) {
  return keys.reduce((total, key) => total + (values?.[key] ?? 0), 0);
}

// ===========================================================================
// Panel
// ===========================================================================

function NetSuitePanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as Partial<NetSuiteExtra>;
  const connected = detail.connection?.status === "connected";
  const settings = pickSettings(extra.settings);
  if (!detail.enabled) return null;
  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      {connected ? <OverviewCard detail={detail} extra={extra} reload={reload} /> : <SetupCard detail={detail} />}
      {connected && detail.can_manage && (extra.choices?.length ?? 0) > 1 ? <SubsidiaryCard extra={extra} reload={reload} /> : null}
      {connected && ((extra.errors?.length ?? 0) > 0 || (extra.erp?.order_errors?.length ?? 0) > 0) ? <AttentionCard extra={extra} /> : null}
      {connected ? <ItemsCard detail={detail} extra={extra} reload={reload} /> : null}
      {connected && extra.recent_payments?.length ? (
        <Card title="Recent payments from NetSuite" icon={<Landmark size={17} />} flush>
          <PaymentsList payments={extra.recent_payments} />
        </Card>
      ) : null}
      {connected && detail.can_manage && settings ? <SettingsCard initial={settings} extra={extra} setDetail={setDetail} /> : null}
      {connected && detail.can_manage ? <CredentialsCard extra={extra} /> : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// Before connecting
// ---------------------------------------------------------------------------

function SetupCard({ detail }: { detail: IntegrationDetail }) {
  return (
    <Card title="Before you connect" icon={<ShieldCheck size={17} />}>
      <ol className="ierp-steps">
        <li>
          In NetSuite, turn on <strong>Token-Based Authentication</strong> and <strong>REST Web Services</strong> (Setup → Company → Enable Features →
          SuiteCloud).
        </li>
        <li>
          Create an integration record (Setup → Integration → Manage Integrations → New) with <em>Token-based authentication</em> ticked. Copy the consumer key
          and secret; NetSuite shows them once.
        </li>
        <li>
          Give a role the permissions below, then create an access token for a user with that role and the integration (Setup → Users/Roles → Access Tokens).
          Copy the token ID and secret.
        </li>
        <li>Paste the account ID and the four values above. A sandbox account ID ends in _SB1 (or _SB2…).</li>
      </ol>
      <div className="ierp-perms">
        <div className="settings-row-label">Role permissions AIVA needs</div>
        <ul>
          <li>Setup: <em>REST Web Services</em>, <em>Log in using Access Tokens</em> (Full)</li>
          <li>Reports: <em>SuiteAnalytics Workbook</em> (for SuiteQL)</li>
          <li>Lists: <em>Customers</em>, <em>Contacts</em> (Full); <em>Items</em>, <em>Subsidiaries</em>, <em>Currency</em>, <em>Locations</em> (View)</li>
          <li>Transactions: <em>Invoice</em>, <em>Sales Order</em>, <em>Credit Memo</em> (Create/Edit); <em>Customer Payment</em> (View)</li>
        </ul>
      </div>
      {!detail.can_manage ? (
        <p className="ui-small ui-muted" style={{ margin: 0 }}>
          An admin connects NetSuite for the whole workspace.
        </p>
      ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function OverviewCard({ detail, extra, reload }: { detail: IntegrationDetail; extra: Partial<NetSuiteExtra>; reload: () => Promise<void> }) {
  const toast = useToast();
  const [busy, setBusy] = useState<"sync" | "retry" | null>(null);
  const counts = extra.counts ?? {};
  const erp = extra.erp ?? {};
  const invoiceTrouble = sum(counts, "invoice_error", "invoice_pending");
  const orderTrouble = sum(erp.orders, "error", "pending");
  const company = extra.company;

  async function run(action: "sync" | "retry") {
    setBusy(action);
    try {
      if (action === "sync") {
        const result = await api.post<{ finished: boolean; counts: Record<string, number> }>("/accounting/netsuite/sync");
        const c = result.counts ?? {};
        const parts = [
          c.sent ? `${c.sent} invoice${c.sent === 1 ? "" : "s"} sent` : null,
          c.orders ? `${c.orders} order${c.orders === 1 ? "" : "s"} sent` : null,
          c.items ? `${c.items} item${c.items === 1 ? "" : "s"} read` : null,
          c.refreshed ? `${c.refreshed} invoice${c.refreshed === 1 ? "" : "s"} updated` : null,
          c.failed ? `${c.failed} failed` : null,
        ].filter(Boolean);
        toast(parts.length ? `Synced: ${parts.join(", ")}.` : result.finished ? "Everything is up to date." : "Started. AIVA carries on in the background.");
      } else {
        const result = await api.post<{ count: number }>("/accounting/netsuite/retry");
        toast(result.count ? `${result.count} invoice${result.count === 1 ? "" : "s"} will be tried again on the next run.` : "Nothing to retry.");
      }
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="NetSuite account"
      icon={<Building2 size={17} />}
      actions={
        <>
          {extra.home_url ? (
            <a className="ui-btn ui-btn-ghost ui-btn-sm" href={extra.home_url} target="_blank" rel="noopener noreferrer">
              Open NetSuite <ExternalLink size={13} />
            </a>
          ) : null}
          {detail.can_manage ? (
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => void run("sync")} disabled={busy !== null}>
              {busy === "sync" ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Sync now
            </button>
          ) : null}
        </>
      }
    >
      <div className="ui-stack" style={{ gap: 16 }}>
        <div className="ierp-account">
          <div>
            <span className="ui-small ui-muted">Account</span>
            <strong>
              {company?.name ?? "NetSuite"} <span className="ierp-mono">{erp.account_id ?? extra.scope_id}</span>
            </strong>
          </div>
          <div className="ierp-badges">
            {erp.sandbox ? <Badge tone="warning">Sandbox</Badge> : <Badge tone="success">Production</Badge>}
            {company?.one_world ? <Badge tone="info">OneWorld</Badge> : null}
            {company?.currency ? <Badge>{company.currency}{company.multi_currency ? " · multi-currency" : ""}</Badge> : null}
          </div>
        </div>
        {company?.one_world ? (
          erp.subsidiary ? (
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              New customers and contacts go to <strong>{erp.subsidiary.name ?? erp.subsidiary.id}</strong>.
            </p>
          ) : (
            <Alert tone="warning">Pick the subsidiary new customers belong to below. Until then AIVA can only use customers that already exist.</Alert>
          )
        ) : null}
        {extra.paused_until ? <Alert tone="warning">NetSuite asked AIVA to slow down. Syncing resumes {relativeTime(extra.paused_until)}.</Alert> : null}
        {extra.last_error ? <Alert tone="warning">The last sync stopped: {extra.last_error}</Alert> : null}
        <div className="settings-kpis">
          <Kpi label="Customers linked" value={counts.customer_linked ?? 0} />
          <Kpi label="Invoices in sync" value={counts.invoice_linked ?? 0} note={extra.waiting ? `${extra.waiting} waiting` : undefined} />
          <Kpi label="Sales orders" value={erp.orders?.linked ?? 0} />
          <Kpi label="Items linked" value={erp.items?.linked ?? 0} />
          <Kpi label="Contacts" value={erp.contacts?.linked ?? 0} />
          <Kpi label="Need attention" value={invoiceTrouble + orderTrouble} tone={invoiceTrouble + orderTrouble ? "danger" : undefined} />
        </div>
        {extra.no_account ? (
          <p className="ui-small ui-muted" style={{ margin: 0 }}>
            {extra.no_account} sent invoice{extra.no_account === 1 ? " has" : "s have"} no account, so {extra.no_account === 1 ? "it" : "they"} can&apos;t go to NetSuite.
          </p>
        ) : null}
        <div className="ierp-foot">
          <span className="ui-small ui-muted">
            {extra.last_run_at ? `Last checked ${relativeTime(extra.last_run_at)}` : "Not checked yet"}. Changes in NetSuite are read every few minutes.
          </span>
          {detail.can_manage && invoiceTrouble ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void run("retry")} disabled={busy !== null}>
              <RotateCcw size={14} /> Retry failed invoices
            </button>
          ) : null}
        </div>
        <p className="ui-small ui-muted" style={{ margin: 0 }}>
          NetSuite is also the workspace&apos;s accounting system: turning it on turns QuickBooks, Xero, FreshBooks or Sage off (their connections are kept).
        </p>
      </div>
    </Card>
  );
}

function SubsidiaryCard({ extra, reload }: { extra: Partial<NetSuiteExtra>; reload: () => Promise<void> }) {
  const toast = useToast();
  const current = extra.erp?.subsidiary?.id ?? "";
  const [choice, setChoice] = useState(current);
  const [busy, setBusy] = useState(false);
  useEffect(() => setChoice(current), [current]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.put("/accounting/netsuite/company", { id: choice });
      toast("New customers go to this subsidiary.");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Subsidiary" icon={<Landmark size={17} />}>
      <form className="ierp-inline" onSubmit={(event) => void save(event)}>
        <select className="ui-select" value={choice} onChange={(event) => setChoice(event.target.value)} aria-label="Subsidiary for new customers">
          {!current ? <option value="">Choose a subsidiary…</option> : null}
          {(extra.choices ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <button type="submit" className="ui-btn" disabled={busy || !choice || choice === current}>
          {busy ? <span className="ui-spinner" /> : null} Use this subsidiary
        </button>
      </form>
      <p className="ui-small ui-muted" style={{ margin: "10px 0 0" }}>
        New customers and contacts are created in it. Customers AIVA already linked keep their own subsidiary, and invoices follow the customer.
      </p>
    </Card>
  );
}

function AttentionCard({ extra }: { extra: Partial<NetSuiteExtra> }) {
  return (
    <Card title="Didn't go to NetSuite" icon={<AlertTriangle size={17} />} flush>
      <ul className="ierp-list">
        {(extra.errors ?? []).map((item) => (
          <li key={`invoice-${item.invoice_id}`}>
            <div className="ierp-list-main">
              <Link href={`/invoices/${item.invoice_id}`} className="ierp-list-title">
                {item.number}
              </Link>
              <span className="ierp-list-sub">{item.message}</span>
            </div>
            <span className="ui-small ui-muted ui-nowrap">{item.retry_at ? `Retries ${relativeTime(item.retry_at)}` : relativeTime(item.at)}</span>
          </li>
        ))}
        {(extra.erp?.order_errors ?? []).map((item) => (
          <li key={`order-${item.record_id}`}>
            <div className="ierp-list-main">
              <Link href={(RECORD_HREF[item.record_type] ?? RECORD_HREF.sales_order)(item.record_id)} className="ierp-list-title">
                {item.label ?? "Order"}
              </Link>
              <span className="ierp-list-sub">{item.message}</span>
            </div>
            <span className="ui-small ui-muted ui-nowrap">{item.retry_at ? `Retries ${relativeTime(item.retry_at)}` : relativeTime(item.at)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Items and stock
// ---------------------------------------------------------------------------

function ItemsCard({ detail, extra, reload }: { detail: IntegrationDetail; extra: Partial<NetSuiteExtra>; reload: () => Promise<void> }) {
  const toast = useToast();
  const [busy, setBusy] = useState<"import" | "restart" | null>(null);
  const [confirm, setConfirm] = useState(false);
  const items = extra.erp?.items;
  const mode = extra.settings?.items_sync ?? "create";
  const stockOn = Boolean(extra.settings?.stock_sync) && extra.plan?.inventory !== false;

  async function run(action: "import" | "restart") {
    setBusy(action);
    try {
      if (action === "import") {
        const result = await api.post<{ count: number; caught_up: boolean }>("/erp/netsuite/items/import");
        toast(result.count ? `Read ${result.count} changed item${result.count === 1 ? "" : "s"}${result.caught_up ? "." : "; the rest follow in the background."}` : "Items are up to date.");
      } else {
        await api.post("/erp/netsuite/items/restart");
        toast("Every item will be read again over the next few minutes.");
        setConfirm(false);
      }
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Items, prices and stock"
      icon={<Boxes size={17} />}
      actions={
        detail.can_manage && mode !== "off" ? (
          <button type="button" className="ui-btn ui-btn-sm" onClick={() => void run("import")} disabled={busy !== null}>
            {busy === "import" ? <span className="ui-spinner" /> : <PackageSearch size={14} />} Import changes now
          </button>
        ) : null
      }
    >
      <div className="ui-stack" style={{ gap: 12 }}>
        {mode === "off" ? (
          <p className="ui-small ui-muted" style={{ margin: 0 }}>
            Importing items is off. Turn it on in the settings below to keep AIVA&apos;s products, prices and stock in line with NetSuite.
          </p>
        ) : (
          <>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              {mode === "create"
                ? "NetSuite items become AIVA products (matched by SKU first), with the chosen price level's price."
                : "AIVA products whose SKU matches a NetSuite item are kept up to date; other items are left out."}{" "}
              {stockOn ? "Stock on hand is refreshed every half hour and recorded as a stock adjustment." : "Stock isn't synced."}
            </p>
            <div className="ierp-facts">
              <div>
                <span className="ui-small ui-muted">Products linked</span>
                <strong>{items?.linked ?? 0}</strong>
              </div>
              <div>
                <span className="ui-small ui-muted">Import</span>
                <strong>{items?.caught_up ? "Up to date" : items?.checked_at ? "In progress" : "Starting"}</strong>
              </div>
              <div>
                <span className="ui-small ui-muted">Last checked</span>
                <strong>{items?.checked_at ? relativeTime(items.checked_at) : "—"}</strong>
              </div>
            </div>
            {detail.can_manage ? (
              <div>
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setConfirm(true)} disabled={busy !== null}>
                  <RotateCcw size={14} /> Read every item again
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>
      <ConfirmDialog
        open={confirm}
        title="Read every item again?"
        message="AIVA reads all NetSuite items from the start, a few hundred at a time. Linked products are updated, not duplicated; products you deleted in AIVA stay deleted."
        confirmLabel="Read again"
        danger={false}
        busy={busy === "restart"}
        onConfirm={() => void run("restart")}
        onClose={() => setConfirm(false)}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function OptionSelect({
  value,
  options,
  loading,
  onChange,
  label,
  emptyLabel = "Choose…",
}: {
  value: string | null;
  options: Option[] | undefined;
  loading: boolean;
  onChange: (value: string | null) => void;
  label: string;
  emptyLabel?: string;
}) {
  const known = (options ?? []).some((o) => o.id === value);
  return (
    <select className="ui-select sm ierp-select" value={value ?? ""} onChange={(event) => onChange(event.target.value || null)} aria-label={label} disabled={loading && !options}>
      <option value="">{loading && !options ? "Loading…" : emptyLabel}</option>
      {value && !known ? <option value={value}>#{value}</option> : null}
      {(options ?? []).map((option) => (
        <option key={option.id} value={option.id}>
          {option.name}
          {option.sku && option.sku !== option.name ? ` (${option.sku})` : ""}
        </option>
      ))}
    </select>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="ierp-section">
      <div className="ierp-section-title">{title}</div>
      {children}
    </div>
  );
}

function SettingsCard({ initial, extra, setDetail }: { initial: NetSuiteSettings; extra: Partial<NetSuiteExtra>; setDetail: (detail: IntegrationDetail) => void }) {
  const toast = useToast();
  const [form, setForm] = useState<NetSuiteSettings>(initial);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const options = useApi<Options>(`/accounting/netsuite/options${refresh ? "?refresh=true" : ""}`);
  const list = options.data;
  const inventory = extra.plan?.inventory !== false;

  useEffect(() => setForm(initial), [initial]);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);

  function set<K extends keyof NetSuiteSettings>(field: K, value: NetSuiteSettings[K]) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleSource(source: OrderSource, on: boolean) {
    const next = new Set(form.orders_from ?? []);
    if (on) next.add(source);
    else next.delete(source);
    set("orders_from", (["sales_order", "quote", "deal"] as OrderSource[]).filter((s) => next.has(s)));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      setDetail(await api.put<IntegrationDetail>("/integration-hub/netsuite/settings", { config: form }));
      toast("NetSuite settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="What syncs, and how"
      icon={<Settings2 size={17} />}
      actions={
        <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setRefresh((n) => n + 1)} disabled={options.loading}>
          <RefreshCw size={14} /> Reload lists from NetSuite
        </button>
      }
    >
      <form className="ui-stack" style={{ gap: 0 }} onSubmit={(event) => void save(event)}>
        {options.error ? <Alert tone="warning">Couldn&apos;t load lists from NetSuite: {options.error}</Alert> : null}

        <Section title="Invoices and payments">
          <SettingRow label="When invoices go over" hint="Automatic sends each AIVA invoice to NetSuite as soon as it's marked sent. Payments always come back.">
            <select className="ui-select sm" value={form.sync_mode} onChange={(event) => set("sync_mode", event.target.value as NetSuiteSettings["sync_mode"])} aria-label="When invoices go over">
              <option value="auto">Automatically</option>
              <option value="manual">Only when someone clicks Send</option>
            </select>
          </SettingRow>
          <SettingRow label="Send invoices issued from" hint="Older invoices (and orders) are only sent by hand, so turning this on doesn't copy your history.">
            <input type="date" className="ui-input sm" value={form.sync_from ?? ""} onChange={(event) => set("sync_from", event.target.value || null)} aria-label="Send invoices issued from" />
          </SettingRow>
          <SettingRow label="Invoice numbers" hint="NetSuite numbers invoices (AIVA's number goes in the memo), or keeps AIVA's INV- number if your numbering allows overrides.">
            <select className="ui-select sm" value={form.numbering} onChange={(event) => set("numbering", event.target.value as NetSuiteSettings["numbering"])} aria-label="Invoice numbers">
              <option value="provider">NetSuite numbers them</option>
              <option value="aiva">Use AIVA&apos;s numbers</option>
            </select>
          </SettingRow>
          <SettingRow label="Default item" hint="For invoice and order lines whose product isn't a NetSuite item. Every NetSuite line needs an item.">
            <OptionSelect value={form.default_item} options={list?.items} loading={options.loading} onChange={(value) => set("default_item", value)} label="Default item" />
          </SettingRow>
          <SettingRow label="Taxable lines use" hint="Leave empty to let NetSuite apply its own tax rules. AIVA warns you when NetSuite's total differs.">
            <OptionSelect value={form.tax_code} options={list?.tax_codes} loading={options.loading} onChange={(value) => set("tax_code", value)} label="Tax code for taxable lines" emptyLabel="NetSuite decides" />
          </SettingRow>
          <SettingRow label="Lines without tax use">
            <OptionSelect value={form.tax_code_exempt} options={list?.tax_codes} loading={options.loading} onChange={(value) => set("tax_code_exempt", value)} label="Tax code for lines without tax" emptyLabel="NetSuite decides" />
          </SettingRow>
          <SettingRow label="Payment terms">
            <OptionSelect value={form.terms} options={list?.terms} loading={options.loading} onChange={(value) => set("terms", value)} label="Payment terms" emptyLabel="Use the invoice's due date" />
          </SettingRow>
          <SettingRow label="Tell the invoice owner about payments">
            <Toggle checked={form.notify_owner} onChange={(value) => set("notify_owner", value)} label="Notify the invoice owner" />
          </SettingRow>
        </Section>

        <Section title="Customers and contacts">
          <SettingRow label="Accounts become customers" hint="Matched by AIVA's link, then email, then company name; otherwise created in the subsidiary above.">
            <select className="ui-select sm" value={form.customers_from} onChange={(event) => set("customers_from", event.target.value as NetSuiteSettings["customers_from"])} aria-label="Accounts become customers">
              <option value="needed">When an invoice or order needs one</option>
              <option value="customers">Every account at the Customer stage</option>
            </select>
          </SettingRow>
          <SettingRow label="Create customers" hint="When no NetSuite customer matches, create one.">
            <Toggle checked={form.create_customers} onChange={(value) => set("create_customers", value)} label="Create customers" />
          </SettingRow>
          <SettingRow label="Contacts follow" hint="Contacts of linked accounts become NetSuite contacts on the customer, and are updated when they change in AIVA.">
            <Toggle checked={form.contacts_sync} onChange={(value) => set("contacts_sync", value)} label="Send contacts" />
          </SettingRow>
        </Section>

        <Section title="Sales orders">
          <SettingRow label="These become NetSuite sales orders" stacked>
            <div className="ierp-checks">
              {(Object.keys(SOURCE_LABEL) as OrderSource[]).map((source) => (
                <label key={source} className="ierp-check">
                  <input type="checkbox" checked={(form.orders_from ?? []).includes(source)} onChange={(event) => toggleSource(source, event.target.checked)} />
                  <span>{SOURCE_LABEL[source]}</span>
                </label>
              ))}
            </div>
          </SettingRow>
          <SettingRow label="When orders go over">
            <select className="ui-select sm" value={form.order_mode} onChange={(event) => set("order_mode", event.target.value as NetSuiteSettings["order_mode"])} aria-label="When orders go over">
              <option value="auto">Automatically</option>
              <option value="manual">Only when someone clicks Send</option>
            </select>
          </SettingRow>
          <SettingRow label="New NetSuite orders are" hint="AIVA only changes an order while NetSuite has it pending approval or fulfilment.">
            <select className="ui-select sm" value={form.order_status} onChange={(event) => set("order_status", event.target.value as NetSuiteSettings["order_status"])} aria-label="New NetSuite orders are">
              <option value="A">Pending approval</option>
              <option value="B">Pending fulfillment</option>
            </select>
          </SettingRow>
          <SettingRow label="Sales order form" hint="A custom form's internal ID, if your account requires one.">
            <input className="ui-input sm ierp-short" inputMode="numeric" value={form.order_form ?? ""} placeholder="Default" onChange={(event) => set("order_form", event.target.value.replace(/\D/g, "") || null)} aria-label="Sales order form ID" />
          </SettingRow>
          <SettingRow label="Status comes back" hint="Fulfilled or billed in NetSuite marks the AIVA sales order fulfilled; cancelled there cancels it here.">
            <Toggle checked={form.order_status_back} onChange={(value) => set("order_status_back", value)} label="Update AIVA sales orders from NetSuite" />
          </SettingRow>
        </Section>

        <Section title="Items, prices and stock">
          <SettingRow label="Items from NetSuite">
            <select className="ui-select sm" value={form.items_sync} onChange={(event) => set("items_sync", event.target.value as NetSuiteSettings["items_sync"])} aria-label="Items from NetSuite">
              <option value="create">Become AIVA products</option>
              <option value="update">Only update products with the same SKU</option>
              <option value="off">Off</option>
            </select>
          </SettingRow>
          <SettingRow label="Price level" hint="Its price (lowest quantity tier) becomes the product's unit price.">
            <OptionSelect value={form.price_level} options={list?.price_levels} loading={options.loading} onChange={(value) => set("price_level", value ?? "1")} label="Price level" emptyLabel="Base price" />
          </SettingRow>
          <SettingRow label="Stock on hand" hint={inventory ? "Inventory items track stock in AIVA, set from NetSuite." : "Needs inventory management in your plan."}>
            <Toggle checked={form.stock_sync && inventory} disabled={!inventory} onChange={(value) => set("stock_sync", value)} label="Stock from NetSuite" />
          </SettingRow>
          {form.stock_sync && inventory ? (
            <SettingRow label="Stock from">
              <OptionSelect value={form.stock_location} options={list?.locations} loading={options.loading} onChange={(value) => set("stock_location", value)} label="Stock location" emptyLabel="All locations added up" />
            </SettingRow>
          ) : null}
        </Section>

        <Section title="Classification on new transactions">
          <SettingRow label="Location">
            <OptionSelect value={form.location} options={list?.locations} loading={options.loading} onChange={(value) => set("location", value)} label="Location" emptyLabel="None" />
          </SettingRow>
          {list?.departments?.length || form.department ? (
            <SettingRow label="Department">
              <OptionSelect value={form.department} options={list?.departments} loading={options.loading} onChange={(value) => set("department", value)} label="Department" emptyLabel="None" />
            </SettingRow>
          ) : null}
          {list?.classes?.length || form.class_id ? (
            <SettingRow label="Class">
              <OptionSelect value={form.class_id} options={list?.classes} loading={options.loading} onChange={(value) => set("class_id", value)} label="Class" emptyLabel="None" />
            </SettingRow>
          ) : null}
        </Section>

        <div className="ierp-save">
          <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || !dirty}>
            {busy ? <span className="ui-spinner" /> : null} Save settings
          </button>
          {dirty ? (
            <button type="button" className="ui-btn ui-btn-ghost" onClick={() => setForm(initial)} disabled={busy}>
              Undo changes
            </button>
          ) : null}
        </div>
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------

function CredentialsCard({ extra }: { extra: Partial<NetSuiteExtra> }) {
  const hints = extra.erp?.credentials;
  return (
    <Card title="Token-based authentication" icon={<KeyRound size={17} />}>
      <div className="ui-stack" style={{ gap: 10 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          AIVA signs every request with your integration&apos;s consumer key and the access token (OAuth 1.0a, HMAC-SHA256). All four values are stored
          encrypted and never shown again.
        </p>
        <div className="ierp-facts">
          <div>
            <span className="ui-small ui-muted">Consumer key</span>
            <strong className="ierp-mono">{hints?.consumer_key ?? "—"}</strong>
          </div>
          <div>
            <span className="ui-small ui-muted">Token ID</span>
            <strong className="ierp-mono">{hints?.token_id ?? "—"}</strong>
          </div>
        </div>
        <p className="ui-small ui-muted" style={{ margin: 0 }}>
          To rotate the token, create a new one in NetSuite and paste all four values again above, then revoke the old token. Revoking AIVA&apos;s token in
          NetSuite stops the sync straight away.
        </p>
      </div>
    </Card>
  );
}

/** Provider panels for this group, by integration key. */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  netsuite: NetSuitePanel,
};
