"use client";

import Link from "next/link";
import { AlertTriangle, ExternalLink, Landmark, ListChecks, PackageSearch, RefreshCw, Receipt, RotateCcw, Settings2, Webhook } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingRow, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, Field, Kpi, LoadingRows } from "@/components/ui";
import { moneyExact, relativeTime } from "@/lib/format";
import type { Product } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { UrlRow } from "../common";
import type { IntegrationDetail, PanelProps } from "../types";

// ===========================================================================
// Types shared with the record components
// ===========================================================================

export type AccountingKey = "quickbooks" | "xero" | "freshbooks" | "sage_accounting";

export type AccountingPayment = {
  id: string;
  provider: string;
  provider_name: string;
  external_id: string;
  amount: string | null;
  currency: string | null;
  status: string;
  paid_on: string | null;
  reference: string | null;
  method: string | null;
  invoice_id: string | null;
  invoice_number: string | null;
  title: string | null;
  at: string | null;
};

type Option = { id: string; name: string; sku?: string | null; type?: string; rate?: number | string | null; taxable?: boolean };
type Options = { items: Option[]; accounts: Option[]; tax_codes: Option[]; terms: Option[]; cached_at?: string };

type AccountingSettings = {
  sync_mode: "auto" | "manual";
  sync_from: string | null;
  numbering: "provider" | "aiva";
  default_item: string | null;
  item_map: Record<string, string>;
  income_account: string | null;
  tax_code: string | null;
  tax_code_exempt: string | null;
  terms: string | null;
  xero_status: "AUTHORISED" | "DRAFT";
  notify_owner: boolean;
  create_customers: boolean;
};

type AccountingExtra = {
  other_active: { key: string; name: string } | null;
  company: { name?: string; currency?: string; country?: string; multi_currency?: boolean } | null;
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
  settings?: AccountingSettings;
  webhooks?: { mode: "app" | "callbacks" | "polling"; server_key?: boolean; workspace_key?: boolean; public?: boolean; callbacks?: number; verified?: number };
  app_webhook_url?: string | null;
  error?: string;
};

const COMPANY_WORD: Record<AccountingKey, string> = {
  quickbooks: "company",
  xero: "organisation",
  freshbooks: "business",
  sage_accounting: "business",
};

export function amount(value: string | number | null | undefined, currency: string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  return moneyExact(Number(value), currency || "USD");
}

async function saveSettings(key: string, config: Partial<AccountingSettings>) {
  return api.put<IntegrationDetail>(`/integration-hub/${key}/settings`, { config });
}

// ===========================================================================
// Panel
// ===========================================================================

function AccountingPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as Partial<AccountingExtra>;
  const key = detail.integration.key as AccountingKey;
  const connected = detail.connection?.status === "connected";
  if (!detail.enabled) return null;
  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      {connected ? <StatusCard detail={detail} extra={extra} reload={reload} /> : <OneSystemNote name={detail.integration.name} />}
      {connected && extra.errors?.length ? <ErrorsCard extra={extra} /> : null}
      {connected && extra.recent_payments?.length ? <PaymentsCard payments={extra.recent_payments} /> : null}
      {connected && detail.can_manage && (extra.choices?.length ?? 0) > 1 ? <CompanyCard detail={detail} extra={extra} reload={reload} /> : null}
      {connected && detail.can_manage && extra.settings ? <SettingsCard detail={detail} accountingKey={key} initial={extra.settings} setDetail={setDetail} /> : null}
      {connected && detail.can_manage && extra.webhooks ? <UpdatesCard detail={detail} extra={extra} reload={reload} /> : null}
    </>
  );
}

function OneSystemNote({ name }: { name: string }) {
  return (
    <p className="ui-muted ui-small" style={{ margin: 0 }}>
      AIVA sends invoices to one accounting system at a time. Turning {name} on turns off any other accounting integration; their connections are kept.
    </p>
  );
}

function StatusCard({ detail, extra, reload }: { detail: IntegrationDetail; extra: Partial<AccountingExtra>; reload: () => Promise<void> }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const key = detail.integration.key as AccountingKey;
  const counts = extra.counts ?? {};
  const customers = counts.customer_linked ?? 0;
  const invoices = counts.invoice_linked ?? 0;
  const attention = (counts.invoice_error ?? 0) + (counts.invoice_pending ?? 0);
  const company = extra.company;

  async function run(action: "sync" | "retry") {
    setBusy(action);
    try {
      if (action === "sync") {
        const result = await api.post<{ finished: boolean; counts: Record<string, number> }>(`/accounting/${key}/sync`);
        const c = result.counts ?? {};
        const parts = [c.sent ? `${c.sent} sent` : null, c.refreshed ? `${c.refreshed} updated from ${detail.integration.name}` : null, c.failed ? `${c.failed} failed` : null].filter(Boolean);
        toast(parts.length ? `Synced: ${parts.join(", ")}.` : result.finished ? "Everything is up to date." : "Started. AIVA carries on in the background.");
      } else {
        const result = await api.post<{ count: number }>(`/accounting/${key}/retry`);
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
      title="Invoices and payments"
      icon={<Receipt size={17} />}
      actions={
        <>
          {extra.home_url ? (
            <a className="ui-btn ui-btn-ghost ui-btn-sm" href={extra.home_url} target="_blank" rel="noopener noreferrer">
              Open {detail.integration.vendor === "Intuit" ? "QuickBooks" : detail.integration.vendor} <ExternalLink size={13} />
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
        {company?.name ? (
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            Sending invoices to <strong>{company.name}</strong>
            {company.currency ? ` · ${company.currency}` : ""}
            {company.multi_currency ? " · multi-currency" : ""}. {extra.sync_mode === "manual" ? "Invoices go over when someone clicks Send." : "Invoices go over automatically when they're sent in AIVA."}
          </p>
        ) : null}
        {extra.paused_until ? (
          <Alert tone="warning">
            {detail.integration.name} asked AIVA to slow down. Syncing resumes {relativeTime(extra.paused_until)}.
          </Alert>
        ) : null}
        {extra.last_error ? <Alert tone="warning">The last sync stopped: {extra.last_error}</Alert> : null}
        <div className="settings-kpis">
          <Kpi label="Customers linked" value={customers} />
          <Kpi label="Invoices in sync" value={invoices} />
          <Kpi label="Waiting to send" value={extra.waiting ?? 0} />
          <Kpi label="Need attention" value={attention} tone={attention ? "danger" : undefined} />
        </div>
        {extra.no_account ? (
          <p className="ui-small ui-muted" style={{ margin: 0 }}>
            {extra.no_account} sent invoice{extra.no_account === 1 ? " has" : "s have"} no account, so {extra.no_account === 1 ? "it" : "they"} can&apos;t go to {detail.integration.name}.
            Add an account on the invoice.
          </p>
        ) : null}
        <div className="iac-foot">
          <span className="ui-small ui-muted">
            {extra.last_run_at ? `Last checked ${relativeTime(extra.last_run_at)}` : "Not checked yet"}
            {extra.last_counts && (extra.last_counts.sent || extra.last_counts.refreshed)
              ? ` · ${extra.last_counts.sent ?? 0} sent, ${extra.last_counts.refreshed ?? 0} updated`
              : ""}
          </span>
          {detail.can_manage && attention ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void run("retry")} disabled={busy !== null}>
              <RotateCcw size={14} /> Retry failed now
            </button>
          ) : null}
        </div>
        <OneSystemNote name={detail.integration.name} />
      </div>
    </Card>
  );
}

function ErrorsCard({ extra }: { extra: Partial<AccountingExtra> }) {
  return (
    <Card title="Invoices that didn't go over" icon={<AlertTriangle size={17} />} flush>
      <ul className="iac-list">
        {(extra.errors ?? []).map((item) => (
          <li key={item.invoice_id}>
            <div className="iac-list-main">
              <Link href={`/invoices/${item.invoice_id}`} className="iac-list-title">
                {item.number}
              </Link>
              <span className="iac-list-sub">{item.message}</span>
            </div>
            <span className="ui-small ui-muted ui-nowrap">{item.retry_at ? `Retries ${relativeTime(item.retry_at)}` : relativeTime(item.at)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function PaymentsList({ payments, showInvoice = true }: { payments: AccountingPayment[]; showInvoice?: boolean }) {
  return (
    <ul className="iac-list">
      {payments.map((payment) => (
        <li key={payment.id}>
          <div className="iac-list-main">
            <span className="iac-list-title">
              {amount(payment.amount, payment.currency)}
              {payment.status === "deleted" ? (
                <>
                  {" "}
                  <Badge tone="danger">Removed</Badge>
                </>
              ) : null}
            </span>
            <span className="iac-list-sub">
              {showInvoice && payment.invoice_id ? (
                <>
                  <Link href={`/invoices/${payment.invoice_id}`}>{payment.title?.replace("Payment for ", "") ?? "Invoice"}</Link> ·{" "}
                </>
              ) : null}
              {[payment.method, payment.reference].filter(Boolean).join(" · ") || payment.provider_name}
            </span>
          </div>
          <span className="ui-small ui-muted ui-nowrap">{payment.paid_on ?? relativeTime(payment.at)}</span>
        </li>
      ))}
    </ul>
  );
}

function PaymentsCard({ payments }: { payments: AccountingPayment[] }) {
  return (
    <Card title="Recent payments" icon={<Landmark size={17} />} flush>
      <PaymentsList payments={payments} />
    </Card>
  );
}

function CompanyCard({ detail, extra, reload }: { detail: IntegrationDetail; extra: Partial<AccountingExtra>; reload: () => Promise<void> }) {
  const toast = useToast();
  const key = detail.integration.key as AccountingKey;
  const [choice, setChoice] = useState(extra.scope_id ?? "");
  const [busy, setBusy] = useState(false);
  const word = COMPANY_WORD[key];

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.put(`/accounting/${key}/company`, { id: choice });
      toast(`AIVA now uses this ${word}.`);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={`Which ${word}`} icon={<Landmark size={17} />}>
      <form className="iac-inline" onSubmit={(event) => void save(event)}>
        <select className="ui-select" value={choice} onChange={(event) => setChoice(event.target.value)} aria-label={`${word} to use`}>
          {(extra.choices ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <button type="submit" className="ui-btn" disabled={busy || !choice || choice === extra.scope_id}>
          {busy ? <span className="ui-spinner" /> : null} Use this {word}
        </button>
      </form>
      <p className="ui-small ui-muted" style={{ margin: "10px 0 0" }}>
        Invoices already sent stay linked to the {word} they went to.
      </p>
    </Card>
  );
}

// ===========================================================================
// Settings
// ===========================================================================

function SettingsCard({
  detail,
  accountingKey: key,
  initial,
  setDetail,
}: {
  detail: IntegrationDetail;
  accountingKey: AccountingKey;
  initial: AccountingSettings;
  setDetail: (detail: IntegrationDetail) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<AccountingSettings>(initial);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const options = useApi<Options>(`/accounting/${key}/options${refresh ? "?refresh=true" : ""}`);
  const usesItems = key === "quickbooks" || key === "xero";
  const usesAccount = key === "xero" || key === "sage_accounting";
  const name = detail.integration.name;

  useEffect(() => setForm(initial), [initial]);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);

  function set<K extends keyof AccountingSettings>(field: K, value: AccountingSettings[K]) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      setDetail(await saveSettings(key, form));
      toast("Accounting settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const list = options.data;
  const taxLabel = key === "xero" ? "tax rate" : key === "freshbooks" ? "tax name" : key === "sage_accounting" ? "tax rate" : "tax code";

  return (
    <Card
      title="Sending invoices"
      icon={<Settings2 size={17} />}
      actions={
        <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setRefresh((n) => n + 1)} disabled={options.loading}>
          <RefreshCw size={14} /> Reload lists from {name}
        </button>
      }
    >
      <form className="ui-stack" style={{ gap: 0 }} onSubmit={(event) => void save(event)}>
        {options.error ? <Alert tone="warning">Couldn&apos;t load lists from {name}: {options.error}</Alert> : null}
        <SettingRow label="When invoices go over" hint={`Automatic sends each AIVA invoice to ${name} as soon as it's marked sent.`}>
          <select className="ui-select sm" value={form.sync_mode} onChange={(event) => set("sync_mode", event.target.value as AccountingSettings["sync_mode"])} aria-label="When invoices go over">
            <option value="auto">Automatically</option>
            <option value="manual">Only when someone clicks Send</option>
          </select>
        </SettingRow>
        <SettingRow label="Send invoices issued from" hint="Older invoices are only sent by hand, so switching this on doesn't copy your history.">
          <input type="date" className="ui-input sm" value={form.sync_from ?? ""} onChange={(event) => set("sync_from", event.target.value || null)} aria-label="Send invoices issued from" />
        </SettingRow>
        <SettingRow label="Invoice numbers" hint={`Either ${name} numbers invoices (AIVA's number is kept in the memo or reference), or they keep AIVA's INV- number.`}>
          <select className="ui-select sm" value={form.numbering} onChange={(event) => set("numbering", event.target.value as AccountingSettings["numbering"])} aria-label="Invoice numbers">
            <option value="provider">{name} numbers them</option>
            <option value="aiva">Use AIVA&apos;s numbers</option>
          </select>
        </SettingRow>
        {key === "xero" ? (
          <SettingRow label="New Xero invoices are" hint="Approved invoices can be emailed and paid; drafts wait for someone to approve them in Xero.">
            <select className="ui-select sm" value={form.xero_status} onChange={(event) => set("xero_status", event.target.value as AccountingSettings["xero_status"])} aria-label="New Xero invoices are">
              <option value="AUTHORISED">Approved</option>
              <option value="DRAFT">Drafts</option>
            </select>
          </SettingRow>
        ) : null}
        {key === "quickbooks" ? (
          <SettingRow label="Default product/service" hint="Used for invoice lines whose AIVA product isn't mapped below. Required.">
            <OptionSelect value={form.default_item} options={list?.items} loading={options.loading} onChange={(value) => set("default_item", value)} label="Default product or service" />
          </SettingRow>
        ) : null}
        {usesAccount ? (
          <SettingRow label={key === "xero" ? "Sales account" : "Sales ledger account"} hint="Where invoice lines are booked. Required.">
            <OptionSelect value={form.income_account} options={list?.accounts} loading={options.loading} onChange={(value) => set("income_account", value)} label="Sales account" />
          </SettingRow>
        ) : null}
        <SettingRow
          label={`Taxable lines use ${key === "freshbooks" ? "the" : "this"} ${taxLabel}`}
          hint={key === "freshbooks" ? "AIVA sends each line's tax percentage under this name." : `${name} works out the tax with it; AIVA warns you if its total differs from AIVA's.`}
        >
          {key === "freshbooks" ? (
            <input className="ui-input sm" value={form.tax_code ?? ""} placeholder="Tax" onChange={(event) => set("tax_code", event.target.value || null)} aria-label="Tax name" list="iac-fb-taxes" />
          ) : (
            <OptionSelect value={form.tax_code} options={list?.tax_codes} loading={options.loading} onChange={(value) => set("tax_code", value)} label="Tax for taxable lines" />
          )}
          {key === "freshbooks" ? (
            <datalist id="iac-fb-taxes">
              {(list?.tax_codes ?? []).map((t) => (
                <option key={t.id} value={t.id} />
              ))}
            </datalist>
          ) : null}
        </SettingRow>
        {key !== "freshbooks" ? (
          <SettingRow label={`Lines without tax use this ${taxLabel}`}>
            <OptionSelect value={form.tax_code_exempt} options={list?.tax_codes} loading={options.loading} onChange={(value) => set("tax_code_exempt", value)} label="Tax for lines without tax" />
          </SettingRow>
        ) : null}
        {key === "quickbooks" && list?.terms?.length ? (
          <SettingRow label="Payment terms">
            <OptionSelect value={form.terms} options={list.terms} loading={options.loading} onChange={(value) => set("terms", value)} label="Payment terms" emptyLabel="Use the invoice's due date" />
          </SettingRow>
        ) : null}
        <SettingRow label="Create customers" hint={`When no ${name} customer matches the account (by email, then name), create one.`}>
          <Toggle checked={form.create_customers} onChange={(value) => set("create_customers", value)} label="Create customers" />
        </SettingRow>
        <SettingRow label="Tell the invoice owner about payments">
          <Toggle checked={form.notify_owner} onChange={(value) => set("notify_owner", value)} label="Notify the invoice owner" />
        </SettingRow>
        {usesItems ? <ItemMapping form={form} setMap={(map) => set("item_map", map)} items={list?.items ?? []} name={name} /> : null}
        <div className="iac-save">
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
    <select className="ui-select sm iac-select" value={value ?? ""} onChange={(event) => onChange(event.target.value || null)} aria-label={label} disabled={loading && !options}>
      <option value="">{loading && !options ? "Loading…" : emptyLabel}</option>
      {value && !known ? <option value={value}>{value}</option> : null}
      {(options ?? []).map((option) => (
        <option key={option.id} value={option.id}>
          {option.name}
          {option.sku ? ` (${option.sku})` : ""}
        </option>
      ))}
    </select>
  );
}

function ItemMapping({
  form,
  setMap,
  items,
  name,
}: {
  form: AccountingSettings;
  setMap: (map: Record<string, string>) => void;
  items: Option[];
  name: string;
}) {
  const products = useApi<Product[]>("/products?active=true");
  const [open, setOpen] = useState(Object.keys(form.item_map ?? {}).length > 0);
  const suggestions = useMemo(() => {
    const out: Record<string, string> = {};
    for (const product of products.data ?? []) {
      const match =
        items.find((item) => product.sku && item.sku && item.sku.toLowerCase() === product.sku.toLowerCase()) ??
        items.find((item) => item.name.toLowerCase() === product.name.toLowerCase());
      if (match) out[product.id] = match.id;
    }
    return out;
  }, [products.data, items]);
  const unmappedSuggestions = Object.entries(suggestions).filter(([productId]) => !form.item_map?.[productId]);

  return (
    <div className="iac-mapping">
      <div className="iac-mapping-head">
        <div>
          <div className="settings-row-label">
            <PackageSearch size={14} style={{ verticalAlign: -2 }} /> Products in {name}
          </div>
          <div className="settings-row-hint">Map AIVA products to {name} items so invoice lines land on the right item and income account.</div>
        </div>
        <button type="button" className="ui-btn ui-btn-sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "Hide" : "Map products"}
        </button>
      </div>
      {open ? (
        products.loading && !products.data ? (
          <LoadingRows rows={3} />
        ) : (products.data ?? []).length === 0 ? (
          <p className="ui-small ui-muted" style={{ margin: 0 }}>
            No active products in AIVA yet.
          </p>
        ) : (
          <>
            {unmappedSuggestions.length ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setMap({ ...(form.item_map ?? {}), ...Object.fromEntries(unmappedSuggestions) })}>
                <ListChecks size={14} /> Match {unmappedSuggestions.length} by SKU or name
              </button>
            ) : null}
            <div className="ui-table-wrap">
              <table className="ui-table iac-map-table">
                <thead>
                  <tr>
                    <th>AIVA product</th>
                    <th>{name} item</th>
                  </tr>
                </thead>
                <tbody>
                  {(products.data ?? []).slice(0, 200).map((product) => (
                    <tr key={product.id}>
                      <td>
                        <div className="cell-title">{product.name}</div>
                        {product.sku ? <div className="ui-small ui-muted">{product.sku}</div> : null}
                      </td>
                      <td>
                        <OptionSelect
                          value={form.item_map?.[product.id] ?? null}
                          options={items}
                          loading={false}
                          emptyLabel="Default"
                          label={`${name} item for ${product.name}`}
                          onChange={(value) => {
                            const next = { ...(form.item_map ?? {}) };
                            if (value) next[product.id] = value;
                            else delete next[product.id];
                            setMap(next);
                          }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )
      ) : null}
    </div>
  );
}

// ===========================================================================
// Instant updates (webhooks)
// ===========================================================================

function UpdatesCard({ detail, extra, reload }: { detail: IntegrationDetail; extra: Partial<AccountingExtra>; reload: () => Promise<void> }) {
  const toast = useToast();
  const key = detail.integration.key as AccountingKey;
  const hooks = extra.webhooks!;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const name = detail.integration.name;

  async function saveKey(remove = false) {
    setBusy(true);
    try {
      await api.put(`/accounting/${key}/webhook-key`, { value: remove ? null : value });
      setValue("");
      toast(remove ? "Webhook key removed." : "Webhook key saved.");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  if (hooks.mode === "polling") {
    return (
      <Card title="Payment updates" icon={<Webhook size={17} />}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          {name} doesn&apos;t send notifications, so AIVA checks for new payments every few minutes and re-checks open invoices every few hours.
        </p>
      </Card>
    );
  }
  if (hooks.mode === "callbacks") {
    return (
      <Card title="Instant updates" icon={<Webhook size={17} />} actions={<Badge tone={hooks.verified ? "success" : "neutral"} dot>{hooks.verified ? "On" : "Not set up"}</Badge>}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          {hooks.public
            ? hooks.verified
              ? `${name} tells AIVA about payments and invoice changes as they happen (${hooks.verified} of ${hooks.callbacks} notifications confirmed).`
              : `AIVA asked ${name} to send payment and invoice notifications; they start once ${name} confirms. Reconnect if this doesn't change.`
            : `${name} can only notify a public https address, so AIVA checks for payments every few minutes instead.`}
        </p>
      </Card>
    );
  }
  const portal = key === "quickbooks" ? "the Intuit Developer portal (your app → Webhooks)" : "the Xero Developer portal (your app → Webhooks)";
  const keyName = key === "quickbooks" ? "Verifier token" : "Webhooks key";
  const events = key === "quickbooks" ? "Invoice and Payment" : "Invoices";
  return (
    <Card
      title="Instant updates"
      icon={<Webhook size={17} />}
      actions={<Badge tone={hooks.server_key || hooks.workspace_key ? "success" : "neutral"} dot>{hooks.server_key || hooks.workspace_key ? "Ready" : "Optional"}</Badge>}
    >
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          AIVA already checks {name} for payments every few minutes. For instant updates, add this address in {portal} with the {events} events.
          {hooks.public ? "" : " It needs AIVA on a public https address."}
        </p>
        <UrlRow label="Webhook URL" value={extra.app_webhook_url} />
        {hooks.server_key ? (
          <p className="ui-small ui-muted" style={{ margin: 0 }}>
            This server&apos;s {key === "quickbooks" ? "Intuit" : "Xero"} app already has its {keyName.toLowerCase()} set.
          </p>
        ) : null}
        <form
          className="iac-inline"
          onSubmit={(event) => {
            event.preventDefault();
            void saveKey();
          }}
        >
          <Field label={`${keyName} (your own app)`} hint={hooks.workspace_key ? "Saved. Paste a new one to replace it." : `From ${portal}.`}>
            <input type="password" className="ui-input" value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" placeholder={hooks.workspace_key ? "••••••••" : ""} />
          </Field>
          <div className="iac-inline-actions">
            <button type="submit" className="ui-btn" disabled={busy || !value.trim()}>
              Save key
            </button>
            {hooks.workspace_key ? (
              <button type="button" className="ui-btn ui-btn-ghost" disabled={busy} onClick={() => void saveKey(true)}>
                Remove
              </button>
            ) : null}
          </div>
        </form>
        <p className="ui-small ui-muted" style={{ margin: 0 }}>
          Notifications are only a nudge: AIVA always reads the invoice back from {name} before changing anything, and refuses notifications with a wrong signature.
        </p>
      </div>
    </Card>
  );
}

/** Provider panels for this group, by integration key. */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  quickbooks: AccountingPanel,
  xero: AccountingPanel,
  freshbooks: AccountingPanel,
  sage_accounting: AccountingPanel,
};
