"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Ban, BadgeDollarSign, Printer, Send, Trash2 } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, ConfirmDialog, Field, LoadingRows, Modal } from "@/components/ui";
import { date, moneyExact, toDateInput } from "@/lib/format";
import { INVOICE_STATUS, type InvoiceDetail } from "@/lib/invoices";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function InvoicePage() {
  return (
    <FeaturePage feature="cpq">
      <Invoice />
    </FeaturePage>
  );
}

function Invoice() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const invoice = useApi<InvoiceDetail>(`/invoices/${invoiceId}`);
  const [paying, setPaying] = useState(false);
  const [voiding, setVoiding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const manage = can("sales.invoices");
  const d = invoice.data;

  async function patch(body: Record<string, unknown>, message: string) {
    setBusy(true);
    try {
      invoice.setData(await api.patch<InvoiceDetail>(`/invoices/${invoiceId}`, body));
      toast(message);
      setVoiding(false);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/invoices/${invoiceId}`);
      toast("Draft invoice deleted.");
      router.replace("/invoices");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  if (invoice.error) return <Alert>{invoice.error}</Alert>;
  if (!d) {
    return (
      <div className="ui-card">
        <LoadingRows rows={10} />
      </div>
    );
  }
  const status = INVOICE_STATUS[d.status];

  return (
    <>
      <div className="ui-row no-print" style={{ marginBottom: 14, gap: 8, flexWrap: "wrap" }}>
        <Link href="/invoices" className="ui-btn ui-btn-ghost">
          <ArrowLeft size={16} /> Invoices
        </Link>
        <span className="ui-spacer" />
        <button type="button" className="ui-btn" onClick={() => window.print()}>
          <Printer size={15} /> Print or save as PDF
        </button>
        {manage && d.status === "draft" ? (
          <>
            <button type="button" className="ui-btn ui-btn-danger" disabled={busy} onClick={() => setDeleting(true)}>
              <Trash2 size={15} /> Delete
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void patch({ status: "sent" }, `${d.display_number} marked as sent.`)}>
              <Send size={15} /> Mark as sent
            </button>
          </>
        ) : null}
        {manage && (d.status === "sent" || d.status === "overdue") ? (
          <>
            <button type="button" className="ui-btn" disabled={busy} onClick={() => setVoiding(true)}>
              <Ban size={15} /> Void
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => setPaying(true)}>
              <BadgeDollarSign size={15} /> Record payment
            </button>
          </>
        ) : null}
      </div>
      {d.status === "overdue" ? (
        <div className="no-print" style={{ marginBottom: 14 }}>
          <Alert tone="warning">
            This invoice was due {date(d.due_date)} and still has {moneyExact(d.balance, d.currency)} outstanding.
          </Alert>
        </div>
      ) : null}
      <article className="ui-card invoice-doc">
        <header>
          <div>
            <strong style={{ fontSize: 18 }}>{d.organization_name}</strong>
            <h1 style={{ marginTop: 14 }}>Invoice {d.display_number}</h1>
            <p className="ui-muted" style={{ margin: "6px 0 0" }}>
              Issued {date(d.issue_date)}
              {d.due_date ? ` · Due ${date(d.due_date)}` : ""}
            </p>
          </div>
          <div style={{ textAlign: "right" }}>
            <Badge tone={status.tone}>{status.label}</Badge>
            <p className="ui-muted ui-small" style={{ margin: "14px 0 2px" }}>
              Bill to
            </p>
            <p style={{ margin: 0, fontWeight: 700 }}>{d.bill_to?.name ?? d.account_name ?? "—"}</p>
            {d.bill_to ? (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                {[d.bill_to.city, d.bill_to.state, d.bill_to.postal_code, d.bill_to.country].filter(Boolean).join(", ")}
              </p>
            ) : null}
            {d.contact_name ? (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Attn: {d.contact_name}
              </p>
            ) : null}
          </div>
        </header>
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Item</th>
                <th className="ui-num">Qty</th>
                <th className="ui-num">Price</th>
                <th className="ui-num">Disc</th>
                <th className="ui-num">Tax</th>
                <th className="ui-num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {d.line_items.map((line, index) => (
                <tr key={`${line.name}-${index}`}>
                  <td>
                    {line.name}
                    {line.description ? <div className="ui-muted ui-small">{line.description}</div> : null}
                  </td>
                  <td className="ui-num">{line.quantity}</td>
                  <td className="ui-num">{moneyExact(line.unit_price, d.currency)}</td>
                  <td className="ui-num">{line.discount_percent ? `${line.discount_percent}%` : "—"}</td>
                  <td className="ui-num">{line.tax_percent ? `${line.tax_percent}%` : "—"}</td>
                  <td className="ui-num">{moneyExact(line.line_total, d.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="quote-totals" style={{ marginTop: 18 }}>
          <span className="ui-muted">Subtotal</span>
          <span className="ui-num">{moneyExact(d.subtotal, d.currency)}</span>
          <span className="ui-muted">Discounts</span>
          <span className="ui-num">−{moneyExact(d.discount_total, d.currency)}</span>
          <span className="ui-muted">Tax</span>
          <span className="ui-num">{moneyExact(d.tax_total, d.currency)}</span>
          <span className="grand">Total</span>
          <span className="grand ui-num">{moneyExact(d.total, d.currency)}</span>
          <span className="ui-muted">Paid</span>
          <span className="ui-num">{moneyExact(d.amount_paid, d.currency)}</span>
          <span className="ui-strong">Balance due</span>
          <span className="ui-num ui-strong">{moneyExact(d.balance, d.currency)}</span>
        </div>
        {d.terms ? (
          <p className="ui-small" style={{ marginTop: 26, whiteSpace: "pre-wrap" }}>
            <strong>Terms:</strong> {d.terms}
          </p>
        ) : null}
        <div className="no-print ui-muted ui-small" style={{ marginTop: 18, display: "flex", gap: 14, flexWrap: "wrap" }}>
          {d.quote_id ? <Link href={`/quotes/${d.quote_id}`}>From quote</Link> : null}
          {d.account_id ? <Link href={`/accounts/${d.account_id}`}>Account</Link> : null}
          {d.owner_name ? <span>Owner {d.owner_name}</span> : null}
          {d.paid_at ? <span>Paid in full {date(d.paid_at)}</span> : null}
          {d.exported_at ? <span>Exported to QuickBooks {date(d.exported_at)}</span> : null}
        </div>
        {manage && d.status === "draft" ? <DueDate invoice={d} onSaved={(next) => invoice.setData(next)} /> : null}
      </article>
      <PaymentModal invoice={paying ? d : null} onClose={() => setPaying(false)} onSaved={(next) => invoice.setData(next)} />
      <ConfirmDialog
        open={voiding}
        title={`Void ${d.display_number}?`}
        message="A void invoice stays on record but no longer counts as money owed."
        confirmLabel="Void invoice"
        busy={busy}
        onConfirm={() => void patch({ status: "void" }, `${d.display_number} voided.`)}
        onClose={() => setVoiding(false)}
      />
      <ConfirmDialog
        open={deleting}
        title={`Delete ${d.display_number}?`}
        message="The draft is removed. The quote stays accepted and can be invoiced again."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}

function DueDate({ invoice, onSaved }: { invoice: InvoiceDetail; onSaved: (next: InvoiceDetail) => void }) {
  const toast = useToast();
  const [due, setDue] = useState(toDateInput(invoice.due_date));
  async function save() {
    try {
      onSaved(await api.patch<InvoiceDetail>(`/invoices/${invoice.id}`, { due_date: due || null }));
      toast("Due date saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }
  return (
    <div className="no-print ui-row" style={{ marginTop: 18, gap: 8, alignItems: "end" }}>
      <Field label="Due date">
        <input className="ui-input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
      </Field>
      <button type="button" className="ui-btn" disabled={due === toDateInput(invoice.due_date)} onClick={() => void save()}>
        Save
      </button>
    </div>
  );
}

function PaymentModal({ invoice, onClose, onSaved }: { invoice: InvoiceDetail | null; onClose: () => void; onSaved: (next: InvoiceDetail) => void }) {
  const toast = useToast();
  const [amount, setAmount] = useState("");
  const [paidOn, setPaidOn] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  });
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!invoice) return;
    setBusy(true);
    try {
      const next = await api.post<InvoiceDetail>(`/invoices/${invoice.id}/payments`, { amount: Number(amount || invoice.balance), paid_on: paidOn || null });
      onSaved(next);
      toast(next.status === "paid" ? `${next.display_number} is paid in full.` : `Payment recorded. ${moneyExact(next.balance, next.currency)} left to pay.`);
      setAmount("");
      onClose();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(invoice)}
      title="Record a payment"
      description={invoice ? `${moneyExact(invoice.balance, invoice.currency)} is outstanding on ${invoice.display_number}.` : undefined}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
            Record payment
          </button>
        </>
      }
    >
      <div className="ui-form-grid">
        <Field label="Amount" hint="Leave empty to pay the full balance.">
          <input className="ui-input" type="number" min={0} step="0.01" value={amount} placeholder={invoice ? String(invoice.balance) : ""} onChange={(e) => setAmount(e.target.value)} />
        </Field>
        <Field label="Paid on">
          <input className="ui-input" type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
