"use client";

import Link from "next/link";
import { ArrowUpRight, Undo2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { Badge, Field, Modal, type Tone } from "@/components/ui";
import { date, moneyExact } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

/** Shapes returned by /payments (apps/api/app/api/routes/int_payments.py). */

export type ProcessorKey = "stripe" | "paypal" | "square";

export type Processor = {
  key: ProcessorKey;
  name: string;
  enabled: boolean;
  connected: boolean;
  mode: string | null;
  currency: string | null;
};

export type RecordRef = { type: string; id: string; label: string; href: string; kind: string };

export type PaymentRow = {
  id: string;
  provider: string;
  provider_name: string;
  type: "payment" | "refund" | "subscription";
  external_id: string;
  title: string | null;
  status: string | null;
  amount: string | null;
  currency: string | null;
  email: string | null;
  url: string | null;
  occurred_at: string | null;
  invoice_id: string | null;
  invoice: RecordRef | null;
  refunded: string | null;
  method: string | null;
  applied: boolean;
  reason: string | null;
  account_id: string | null;
  data: {
    plan?: string | null;
    billing_period?: string | null;
    mrr?: string | null;
    next_billing_at?: string | null;
    current_term_end?: string | null;
    cancelled_at?: string | null;
    cancel_reason?: string | null;
    trial_end?: string | null;
    status_label?: string | null;
  };
};

export type PaymentLink = {
  provider: ProcessorKey;
  provider_name: string;
  external_id: string;
  url: string | null;
  amount: string | null;
  currency: string | null;
  state: "open" | "paid" | "closed" | "superseded";
  stale: boolean;
  status: string;
  last_error: string | null;
  created_at: string | null;
  emailed_at: string | null;
  checked_at: string | null;
};

export type InvoicePaymentsData = {
  invoice: {
    id: string;
    label: string;
    status: string;
    currency: string;
    total: string;
    amount_paid: string;
    balance: string;
    contact_name: string | null;
    contact_email: string | null;
  };
  processors: Processor[];
  links: PaymentLink[];
  payments: PaymentRow[];
  can_send: boolean;
  can_refund: boolean;
  message?: string;
};

export const PAYMENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  succeeded: { label: "Paid", tone: "success" },
  partially_refunded: { label: "Part refunded", tone: "warning" },
  refunded: { label: "Refunded", tone: "neutral" },
};

export const SUBSCRIPTION_TONE: Record<string, Tone> = {
  active: "success",
  in_trial: "info",
  future: "info",
  non_renewing: "warning",
  paused: "warning",
  cancelled: "neutral",
  transferred: "neutral",
};

export function amountText(value: string | null | undefined, currency: string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  try {
    return moneyExact(Number(value), (currency || "USD").toUpperCase());
  } catch {
    return `${currency ?? ""} ${value}`.trim();
  }
}

export function refundable(row: PaymentRow) {
  if (row.type !== "payment" || !row.amount) return 0;
  return Math.max(0, Math.round((Number(row.amount) - Number(row.refunded ?? 0)) * 100) / 100);
}

export function PaymentStatus({ row }: { row: PaymentRow }) {
  if (row.type === "refund") return <Badge tone="neutral">Refund</Badge>;
  const status = PAYMENT_STATUS[row.status ?? ""] ?? { label: row.status ?? "—", tone: "neutral" as Tone };
  return (
    <span className="ipay-status">
      <Badge tone={status.tone} dot>
        {status.label}
      </Badge>
      {!row.applied && row.type === "payment" ? (
        <span className="ui-muted ui-small" title="Not added to the invoice: different currency, a void invoice, or no AIVA invoice">
          not on invoice
        </span>
      ) : null}
    </span>
  );
}

/** A list of payments and refunds (newest first), with refunds for admins. */
export function PaymentsTable({
  rows,
  showInvoice,
  canRefund,
  onRefunded,
  empty = "No payments yet.",
}: {
  rows: PaymentRow[];
  showInvoice?: boolean;
  canRefund?: boolean;
  onRefunded?: (data: InvoicePaymentsData) => void;
  empty?: string;
}) {
  const [refunding, setRefunding] = useState<PaymentRow | null>(null);
  if (!rows.length) return <p className="ui-muted ui-small" style={{ margin: 0 }}>{empty}</p>;
  return (
    <>
      <ul className="ipay-rows">
        {rows.map((row) => (
          <li key={row.id} className={row.type === "refund" ? "refund" : undefined}>
            <div className="ipay-row-main">
              <strong className={row.type === "refund" ? "ipay-negative" : undefined}>
                {row.type === "refund" ? "−" : ""}
                {amountText(row.amount, row.currency)}
              </strong>
              <span className="ui-muted ui-small">
                {row.provider_name}
                {row.method ? ` · ${row.method}` : ""} · {date(row.occurred_at)}
                {showInvoice && row.invoice ? (
                  <>
                    {" · "}
                    <Link href={row.invoice.href}>{row.invoice.label}</Link>
                  </>
                ) : null}
                {row.reason ? ` · ${row.reason}` : ""}
              </span>
            </div>
            <div className="ipay-row-side">
              <PaymentStatus row={row} />
              {row.url ? (
                <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={row.url} target="_blank" rel="noopener noreferrer" aria-label={`Open in ${row.provider_name}`}>
                  <ArrowUpRight size={14} />
                </a>
              ) : null}
              {canRefund && refundable(row) > 0 && row.provider !== "chargebee" ? (
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => setRefunding(row)}>
                  <Undo2 size={13} /> Refund
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {refunding ? <RefundDialog row={refunding} onClose={() => setRefunding(null)} onDone={(data) => { setRefunding(null); onRefunded?.(data); }} /> : null}
    </>
  );
}

function RefundDialog({ row, onClose, onDone }: { row: PaymentRow; onClose: () => void; onDone: (data: InvoicePaymentsData) => void }) {
  const toast = useToast();
  const max = refundable(row);
  const [amount, setAmount] = useState(String(max.toFixed(2)));
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const value = Number(amount);
  const valid = Number.isFinite(value) && value > 0 && value <= max;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    try {
      const result = await api.post<InvoicePaymentsData>("/payments/refunds", { payment_id: row.id, amount: value.toFixed(2), reason: reason.trim() || null });
      toast(result.message ?? "Refund sent.");
      onDone(result);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      size="sm"
      title={`Refund a ${row.provider_name} payment`}
      description={`Money goes back to the customer through ${row.provider_name}. This can't be undone.`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="ipay-refund" className={`ui-btn ${confirming ? "ui-btn-danger solid" : "ui-btn-primary"}`} disabled={busy || !valid}>
            {busy ? <span className="ui-spinner" /> : null}
            {confirming ? `Yes, refund ${amountText(value.toFixed(2), row.currency)}` : "Continue"}
          </button>
        </>
      }
    >
      <form id="ipay-refund" className="ui-stack" style={{ gap: 12 }} onSubmit={(event) => void submit(event)}>
        <Field label="Amount" hint={`Up to ${amountText(max.toFixed(2), row.currency)}.`}>
          <input className="ui-input" inputMode="decimal" value={amount} onChange={(event) => { setAmount(event.target.value.replace(/[^\d.]/g, "")); setConfirming(false); }} />
        </Field>
        <Field label="Reason (optional)" hint="Kept on the payment in AIVA; PayPal and Square show it to the customer.">
          <input className="ui-input" maxLength={255} value={reason} onChange={(event) => setReason(event.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}
