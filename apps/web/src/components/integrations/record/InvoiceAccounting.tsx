"use client";

import { AlertTriangle, ExternalLink, Landmark, RefreshCw, Send } from "lucide-react";
import { useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { amount, PaymentsList, type AccountingPayment } from "../panels/accounting";
import { REMOTE_STATUS, type AccountingActive } from "./RecordAccounting";

type InvoiceLink = {
  id: string;
  provider: string;
  provider_name: string;
  external_id: string | null;
  external_url: string | null;
  status: "linked" | "pending" | "error" | "removed";
  number: string | null;
  remote_status: string | null;
  total: string | null;
  balance: string | null;
  currency: string | null;
  total_mismatch: { aiva: string; provider: string } | null;
  last_synced_at: string | null;
  checked_at: string | null;
  last_error: string | null;
  retry_at: string | null;
};

type InvoiceAccountingData = {
  active: AccountingActive | null;
  link: InvoiceLink | null;
  payments: AccountingPayment[];
  invoice_status: string;
  can_push: boolean;
  blocked_reason: string | null;
};

/**
 * An AIVA invoice in the workspace's accounting system: whether it has gone over
 * (and as which number), errors with their retry time, payments recorded there,
 * and "Send to …" / "Check payments". Renders nothing when no accounting system
 * is on and connected. Call ``onChanged`` to reload the invoice after a payment
 * marks it paid.
 *
 *   <InvoiceAccounting invoiceId={invoice.id} onChanged={() => void invoice.reload()} />
 */
export function InvoiceAccounting({ invoiceId, onChanged }: { invoiceId: string; onChanged?: () => void }) {
  const { has, can } = useSession();
  const toast = useToast();
  const enabled = has("accounting_sync");
  const data = useApi<InvoiceAccountingData>(enabled ? `/accounting/invoices/${invoiceId}` : null);
  const [busy, setBusy] = useState<"push" | "refresh" | null>(null);
  if (!enabled || !data.data?.active?.connected) return null;
  const { active, link, payments, can_push: canPush, blocked_reason: blocked } = data.data;
  const name = active!.name;
  const sent = link && link.status !== "pending" && link.external_id;

  async function act(action: "push" | "refresh") {
    setBusy(action);
    try {
      const result = await api.post<InvoiceAccountingData>(`/accounting/invoices/${invoiceId}/${action}`);
      data.setData(result);
      toast(action === "push" ? (sent ? `Updated in ${name}.` : `Sent to ${name}.`) : "Payments checked.");
      onChanged?.();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      void data.reload();
    } finally {
      setBusy(null);
    }
  }

  const remote = link?.remote_status ? REMOTE_STATUS[link.remote_status] : null;
  return (
    <Card
      title={name}
      icon={<Landmark size={16} />}
      actions={
        link?.external_url ? (
          <a className="ui-btn ui-btn-ghost ui-btn-sm" href={link.external_url} target="_blank" rel="noopener noreferrer">
            Open <ExternalLink size={13} />
          </a>
        ) : null
      }
    >
      <div className="ui-stack" style={{ gap: 12 }}>
        {sent ? (
          <div className="iac-status">
            <div>
              <span className="ui-small ui-muted">In {name} as</span>
              <strong>{link.number ?? link.external_id}</strong>
            </div>
            {remote ? <Badge tone={remote.tone}>{remote.label}</Badge> : null}
          </div>
        ) : (
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            {link?.status === "pending" && link.last_error
              ? `Not in ${name} yet.`
              : blocked
                ? `Not in ${name} yet. ${blocked}`
                : active!.sync_mode === "auto"
                  ? `Not in ${name} yet. It goes over automatically once it's sent.`
                  : `Not in ${name} yet.`}
          </p>
        )}
        {sent && link.balance !== null && link.remote_status !== "void" ? (
          <div className="iac-balances">
            <div>
              <span className="ui-small ui-muted">Total in {name}</span>
              <strong>{amount(link.total, link.currency)}</strong>
            </div>
            <div>
              <span className="ui-small ui-muted">Balance due</span>
              <strong>{amount(link.balance, link.currency)}</strong>
            </div>
          </div>
        ) : null}
        {link?.total_mismatch ? (
          <Alert tone="warning">
            {name} totals this invoice at {amount(link.total_mismatch.provider, link.currency)}; AIVA has {amount(link.total_mismatch.aiva, link.currency)}. Check the tax
            and discount settings on the {name} integration page.
          </Alert>
        ) : null}
        {link?.last_error ? (
          <div className="iac-error" role="status">
            <AlertTriangle size={14} aria-hidden />
            <span>
              {link.last_error}
              {link.retry_at ? ` AIVA tries again ${relativeTime(link.retry_at)}.` : ""}
            </span>
          </div>
        ) : null}
        {link?.remote_status === "void" && data.data.invoice_status !== "void" ? (
          <Alert tone="warning">This invoice was voided in {name} but is still open in AIVA.</Alert>
        ) : null}
        {payments.length ? (
          <div>
            <div className="ui-small ui-muted" style={{ marginBottom: 4 }}>
              Payments recorded in {name}
            </div>
            <PaymentsList payments={payments} showInvoice={false} />
          </div>
        ) : null}
        <div className="iac-foot">
          <span className="ui-small ui-muted">
            {link?.checked_at ? `Checked ${relativeTime(link.checked_at)}` : link?.last_synced_at ? `Synced ${relativeTime(link.last_synced_at)}` : ""}
          </span>
          <div className="iac-actions">
            {sent && can("sales.invoices") ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void act("refresh")} disabled={busy !== null}>
                {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Check payments
              </button>
            ) : null}
            {canPush && link?.remote_status !== "paid" && link?.remote_status !== "void" ? (
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => void act("push")} disabled={busy !== null}>
                {busy === "push" ? <span className="ui-spinner" /> : <Send size={14} />} {sent ? `Update in ${name}` : `Send to ${name}`}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </Card>
  );
}

export default InvoiceAccounting;
