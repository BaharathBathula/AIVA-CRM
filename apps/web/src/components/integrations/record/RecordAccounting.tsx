"use client";

import { ExternalLink, Landmark, Link2, RefreshCw, Search, Unlink } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, Modal, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { amount, PaymentsList, type AccountingPayment } from "../panels/accounting";

export type AccountingActive = {
  key: "quickbooks" | "xero" | "freshbooks" | "sage_accounting";
  name: string;
  connected: boolean;
  status: string;
  company: string | null;
  home_url: string | null;
  sync_mode: "auto" | "manual";
};

type RemoteInvoiceRow = { id: string; number: string | null; date: string | null; due_date: string | null; total: string; balance: string; status: string; currency: string | null; url: string | null };

type CustomerLink = {
  id: string;
  provider: string;
  provider_name: string;
  external_id: string | null;
  external_url: string | null;
  name: string | null;
  linked_by: string | null;
  open_balance: string | null;
  overdue_balance: string | null;
  overdue_count: number | null;
  currency: string | null;
  invoices: RemoteInvoiceRow[];
  refreshed_at: string | null;
};

type AccountAccounting = {
  active: AccountingActive | null;
  link: CustomerLink | null;
  payments: AccountingPayment[];
  can_link: boolean;
};

export const REMOTE_STATUS: Record<string, { label: string; tone: Tone }> = {
  open: { label: "Open", tone: "info" },
  overdue: { label: "Overdue", tone: "danger" },
  paid: { label: "Paid", tone: "success" },
  void: { label: "Void", tone: "neutral" },
  draft: { label: "Draft", tone: "neutral" },
  deleted: { label: "Deleted", tone: "warning" },
};

/**
 * The account's customer in the workspace's accounting system (QuickBooks, Xero,
 * FreshBooks or Sage): open and overdue balance, recent invoices and payments,
 * and linking to an existing customer. Renders nothing when no accounting system
 * is on and connected.
 *
 *   <RecordAccounting accountId={account.id} />
 */
export function RecordAccounting({ accountId }: { accountId: string }) {
  const { has } = useSession();
  const toast = useToast();
  const enabled = has("accounting_sync");
  const data = useApi<AccountAccounting>(enabled ? `/accounting/accounts/${accountId}` : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [unlinking, setUnlinking] = useState(false);
  if (!enabled || !data.data?.active?.connected) return null;
  const { active, link, payments, can_link: canLink } = data.data;
  const currency = link?.currency ?? null;

  async function act(name: string, run: () => Promise<AccountAccounting>, done: string) {
    setBusy(name);
    try {
      data.setData(await run());
      toast(done);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title={active!.name}
      icon={<Landmark size={16} />}
      actions={
        link ? (
          <>
            {link.external_url ? (
              <a className="ui-btn ui-btn-ghost ui-btn-sm ui-btn-icon" href={link.external_url} target="_blank" rel="noopener noreferrer" aria-label={`Open in ${active!.name}`} title={`Open in ${active!.name}`}>
                <ExternalLink size={14} />
              </a>
            ) : null}
            <button
              type="button"
              className="ui-btn ui-btn-ghost ui-btn-sm ui-btn-icon"
              aria-label="Refresh balance"
              title="Refresh balance"
              disabled={busy !== null}
              onClick={() => void act("refresh", () => api.post<AccountAccounting>(`/accounting/accounts/${accountId}/refresh`), "Balance refreshed.")}
            >
              {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCw size={14} />}
            </button>
          </>
        ) : null
      }
    >
      {data.error ? <Alert>{data.error}</Alert> : null}
      {link ? (
        <div className="ui-stack" style={{ gap: 12 }}>
          <div className="iac-customer">
            <span className="ui-small ui-muted">Customer</span>
            <strong>{link.name ?? link.external_id}</strong>
          </div>
          <div className="iac-balances">
            <div>
              <span className="ui-small ui-muted">Open balance</span>
              <strong>{amount(link.open_balance, currency)}</strong>
            </div>
            <div>
              <span className="ui-small ui-muted">Overdue</span>
              <strong className={Number(link.overdue_balance) > 0 ? "iac-danger" : undefined}>{amount(link.overdue_balance, currency)}</strong>
            </div>
          </div>
          {link.invoices?.length ? (
            <ul className="iac-list compact">
              {link.invoices.slice(0, 5).map((invoice) => (
                <li key={invoice.id}>
                  <div className="iac-list-main">
                    {invoice.url ? (
                      <a className="iac-list-title" href={invoice.url} target="_blank" rel="noopener noreferrer">
                        {invoice.number ?? "Invoice"}
                      </a>
                    ) : (
                      <span className="iac-list-title">{invoice.number ?? "Invoice"}</span>
                    )}
                    <span className="iac-list-sub">
                      {invoice.date ?? ""} · {amount(invoice.total, invoice.currency ?? currency)}
                      {Number(invoice.balance) > 0 && invoice.status !== "void" ? ` · ${amount(invoice.balance, invoice.currency ?? currency)} due` : ""}
                    </span>
                  </div>
                  <Badge tone={REMOTE_STATUS[invoice.status]?.tone ?? "neutral"}>{REMOTE_STATUS[invoice.status]?.label ?? invoice.status}</Badge>
                </li>
              ))}
            </ul>
          ) : null}
          {payments.length ? (
            <div>
              <div className="ui-small ui-muted" style={{ marginBottom: 4 }}>
                Recent payments
              </div>
              <PaymentsList payments={payments.slice(0, 5)} />
            </div>
          ) : null}
          <div className="iac-foot">
            <span className="ui-small ui-muted">{link.refreshed_at ? `Updated ${relativeTime(link.refreshed_at)}` : "Not refreshed yet"}</span>
            {canLink ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setUnlinking(true)} disabled={busy !== null}>
                <Unlink size={13} /> Unlink
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="ui-stack" style={{ gap: 10 }}>
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            Not linked to a {active!.name} customer yet. AIVA matches or creates one when this account&apos;s first invoice goes over.
          </p>
          {canLink ? (
            <div className="iac-actions">
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => setPicking(true)} disabled={busy !== null}>
                <Search size={14} /> Link to a customer
              </button>
              <button
                type="button"
                className="ui-btn ui-btn-ghost ui-btn-sm"
                disabled={busy !== null}
                onClick={() => void act("create", () => api.post<AccountAccounting>(`/accounting/accounts/${accountId}/link`, { create: true }), "Linked.")}
              >
                {busy === "create" ? <span className="ui-spinner" /> : <Link2 size={14} />} Find or create now
              </button>
            </div>
          ) : null}
        </div>
      )}
      {picking ? (
        <CustomerPicker
          active={active!}
          onClose={() => setPicking(false)}
          onPick={async (customerId) => {
            await act("link", () => api.post<AccountAccounting>(`/accounting/accounts/${accountId}/link`, { customer_id: customerId }), "Linked.");
            setPicking(false);
          }}
        />
      ) : null}
      <ConfirmDialog
        open={unlinking}
        title={`Unlink from ${active!.name}?`}
        message="Invoices already sent stay where they are. The next invoice for this account matches or creates a customer again."
        confirmLabel="Unlink"
        danger={false}
        busy={busy === "unlink"}
        onClose={() => setUnlinking(false)}
        onConfirm={async () => {
          setUnlinking(false);
          await act("unlink", () => api.del<AccountAccounting>(`/accounting/accounts/${accountId}/link`), "Unlinked.");
        }}
      />
    </Card>
  );
}

function CustomerPicker({ active, onClose, onPick }: { active: AccountingActive; onClose: () => void; onPick: (customerId: string) => Promise<void> }) {
  const [term, setTerm] = useState("");
  const [results, setResults] = useState<{ id: string; name: string; email: string | null }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function search(event: FormEvent) {
    event.preventDefault();
    if (term.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const found = await api.get<{ items: { id: string; name: string; email: string | null }[] }>(`/accounting/${active.key}/customers?q=${encodeURIComponent(term.trim())}`);
      setResults(found.items);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Link to a ${active.name} customer`} description="Search by name, then pick the customer this account is.">
      <form className="iac-inline" onSubmit={(event) => void search(event)}>
        <input className="ui-input" value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Customer name" aria-label="Customer name" autoFocus />
        <button type="submit" className="ui-btn" disabled={busy || term.trim().length < 2}>
          {busy ? <span className="ui-spinner" /> : <Search size={14} />} Search
        </button>
      </form>
      {error ? <Alert>{error}</Alert> : null}
      {results ? (
        results.length ? (
          <ul className="iac-list picker">
            {results.map((customer) => (
              <li key={customer.id}>
                <div className="iac-list-main">
                  <span className="iac-list-title">{customer.name}</span>
                  {customer.email ? <span className="iac-list-sub">{customer.email}</span> : null}
                </div>
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => void onPick(customer.id)}>
                  Link
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="ui-small ui-muted">No customers match.</p>
        )
      ) : null}
    </Modal>
  );
}

export default RecordAccounting;
