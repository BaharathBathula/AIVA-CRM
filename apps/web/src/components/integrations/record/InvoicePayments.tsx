"use client";

/**
 * "Online payment" on a sales invoice: a payment link per connected processor
 * (Stripe, PayPal, Square) for the balance due, copied or emailed to the invoice's
 * contact from the sender's own mailbox, plus the payments and refunds that came
 * back from the processors. Admins with invoice rights can refund.
 *
 *   <InvoicePayments invoiceId={invoice.id} onChanged={() => void invoice.reload()} />
 *
 * Renders nothing when the plan has no online payments, or when no processor is
 * on and nothing was ever paid online for this invoice.
 */

import Link from "next/link";
import { CreditCard, ExternalLink, Link2, Mail, RefreshCw, Search, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";

import { CopyField } from "@/components/copy-field";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, Field, Modal } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { amountText, PaymentsTable, type InvoicePaymentsData, type PaymentLink, type Processor } from "./payments-kit";

export function InvoicePayments({ invoiceId, onChanged }: { invoiceId: string; onChanged?: () => void }) {
  const { has } = useSession();
  const toast = useToast();
  const enabled = has("online_payments");
  const listing = useApi<InvoicePaymentsData>(enabled ? `/payments/invoices/${invoiceId}` : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [emailing, setEmailing] = useState<PaymentLink | null>(null);
  if (!enabled) return null;
  const data = listing.data;
  if (!data && !listing.error) return null;
  if (listing.error && !data) return null;
  if (!data) return null;
  const ready = data.processors.filter((p) => p.enabled && p.connected);
  if (!ready.length && !data.links.length && !data.payments.length) return null;

  const invoice = data.invoice;
  const balance = Number(invoice.balance);
  const closed = invoice.status === "paid" || invoice.status === "void";

  function apply(next: InvoicePaymentsData, changed = true) {
    listing.setData(next);
    if (next.message) toast(next.message);
    if (changed) onChanged?.();
  }

  async function run(key: string, action: () => Promise<InvoicePaymentsData>, success?: string) {
    setBusy(key);
    try {
      const next = await action();
      apply(next);
      if (success && !next.message) toast(success);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const linkFor = (key: string) => data.links.find((l) => l.provider === key && l.state === "open") ?? null;

  return (
    <Card
      title="Online payment"
      icon={<CreditCard size={16} />}
      actions={
        data.can_send && data.links.length ? (
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy !== null} onClick={() => void run("check", () => api.post(`/payments/invoices/${invoiceId}/check`))}>
            {busy === "check" ? <span className="ui-spinner" /> : <Search size={14} />} Check for payments
          </button>
        ) : null
      }
    >
      <div className="ipay-invoice">
        <div className="ipay-balance">
          <span className="ui-muted ui-small">Balance due</span>
          <strong>{amountText(invoice.balance, invoice.currency)}</strong>
          <span className="ui-muted ui-small">
            {amountText(invoice.amount_paid, invoice.currency)} paid of {amountText(invoice.total, invoice.currency)}
          </span>
        </div>

        {invoice.status === "void" ? <Alert tone="warning">This invoice is void; payment links can&apos;t be created.</Alert> : null}

        <ul className="ipay-processors">
          {(ready.length ? ready : data.processors.filter((p) => data.links.some((l) => l.provider === p.key))).map((processor) => (
            <ProcessorRow
              key={processor.key}
              processor={processor}
              link={linkFor(processor.key)}
              lastLink={data.links.find((l) => l.provider === processor.key) ?? null}
              canSend={data.can_send && processor.enabled && processor.connected}
              closed={closed || balance <= 0}
              busy={busy}
              onCreate={() => void run(`create:${processor.key}`, () => api.post(`/payments/invoices/${invoiceId}/links`, { provider: processor.key }), `${processor.name} link ready.`)}
              onClose={() => void run(`close:${processor.key}`, () => api.del(`/payments/invoices/${invoiceId}/links/${processor.key}`), "Link closed.")}
              onEmail={(link) => setEmailing(link)}
            />
          ))}
        </ul>
        {!ready.length ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            No payment processor is on. <Link href="/integrations">Turn on Stripe, PayPal or Square</Link>.
          </p>
        ) : null}

        <div>
          <h3 className="ipay-subhead">Payments</h3>
          <PaymentsTable rows={data.payments} canRefund={data.can_refund} onRefunded={(next) => apply(next)} empty="Nothing paid online yet." />
        </div>
      </div>
      {emailing ? (
        <EmailDialog
          invoiceId={invoiceId}
          link={emailing}
          data={data}
          onClose={() => setEmailing(null)}
          onSent={(next) => {
            setEmailing(null);
            apply(next, false);
          }}
        />
      ) : null}
    </Card>
  );
}

function ProcessorRow({
  processor,
  link,
  lastLink,
  canSend,
  closed,
  busy,
  onCreate,
  onClose,
  onEmail,
}: {
  processor: Processor;
  link: PaymentLink | null;
  lastLink: PaymentLink | null;
  canSend: boolean;
  closed: boolean;
  busy: string | null;
  onCreate: () => void;
  onClose: () => void;
  onEmail: (link: PaymentLink) => void;
}) {
  const creating = busy === `create:${processor.key}`;
  return (
    <li className="ipay-processor">
      <div className="ipay-processor-head">
        <span className={`ipay-mark ipay-mark-${processor.key}`} aria-hidden>
          {processor.name.slice(0, 1)}
        </span>
        <strong>{processor.name}</strong>
        {processor.mode && ["test", "sandbox"].includes(processor.mode) ? <Badge tone="warning">{processor.mode === "test" ? "Test mode" : "Sandbox"}</Badge> : null}
        {!processor.connected ? <Badge tone="neutral">Not connected</Badge> : null}
        {!link && lastLink?.state === "paid" ? <Badge tone="success" dot>Paid through this link</Badge> : null}
        <span className="ui-spacer" />
        {link && canSend ? (
          <>
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => onEmail(link)} disabled={busy !== null}>
              <Mail size={13} /> Email
            </button>
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={onCreate} disabled={busy !== null || closed} title="Make a new link for the current balance">
              {creating ? <span className="ui-spinner" /> : <RefreshCw size={13} />} Refresh
            </button>
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={onClose} disabled={busy !== null} aria-label={`Close the ${processor.name} link`} title="Close the link">
              <XCircle size={14} />
            </button>
          </>
        ) : null}
        {!link && canSend && !closed ? (
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={onCreate} disabled={busy !== null}>
            {creating ? <span className="ui-spinner" /> : <Link2 size={13} />} Create link
          </button>
        ) : null}
      </div>
      {link?.url ? (
        <div className="ipay-link">
          <CopyField value={link.url} label={`Copy the ${processor.name} payment link`} />
          <div className="ipay-link-meta ui-small ui-muted">
            For {amountText(link.amount, link.currency)} · made {relativeTime(link.created_at)}
            {link.emailed_at ? ` · emailed ${relativeTime(link.emailed_at)}` : ""}
            <a href={link.url} target="_blank" rel="noopener noreferrer" className="ipay-open">
              Open <ExternalLink size={12} />
            </a>
          </div>
          {link.stale ? <Alert tone="warning">The balance changed since this link was made. Refresh it so the customer pays the right amount.</Alert> : null}
          {link.last_error ? <Alert tone="warning">{link.last_error}</Alert> : null}
        </div>
      ) : null}
    </li>
  );
}

function EmailDialog({
  invoiceId,
  link,
  data,
  onClose,
  onSent,
}: {
  invoiceId: string;
  link: PaymentLink;
  data: InvoicePaymentsData;
  onClose: () => void;
  onSent: (data: InvoicePaymentsData) => void;
}) {
  const toast = useToast();
  const invoice = data.invoice;
  const [to, setTo] = useState(invoice.contact_email ?? "");
  const [subject, setSubject] = useState(`Invoice ${invoice.label}`);
  const [message, setMessage] = useState(`Here's a secure link to pay invoice ${invoice.label} (${amountText(invoice.balance, invoice.currency)}).`);
  const [busy, setBusy] = useState(false);

  async function send(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      onSent(await api.post<InvoicePaymentsData>(`/payments/invoices/${invoiceId}/email`, { provider: link.provider, to: to.trim(), subject: subject.trim(), message: message.trim() }));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={`Email the ${link.provider_name} link`}
      description="Sent from your own mailbox, with a Pay button that opens the link."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="ipay-email" className="ui-btn ui-btn-primary" disabled={busy || !to.trim()}>
            {busy ? <span className="ui-spinner" /> : <Mail size={14} />} Send
          </button>
        </>
      }
    >
      <form id="ipay-email" className="ui-stack" style={{ gap: 12 }} onSubmit={(event) => void send(event)}>
        <Field label="To" required>
          <input className="ui-input" type="email" value={to} onChange={(event) => setTo(event.target.value)} placeholder="name@company.com" />
        </Field>
        <Field label="Subject">
          <input className="ui-input" maxLength={200} value={subject} onChange={(event) => setSubject(event.target.value)} />
        </Field>
        <Field label="Message" hint="The link and a Pay button are added below your message.">
          <textarea className="ui-input" rows={4} maxLength={4000} value={message} onChange={(event) => setMessage(event.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}

export default InvoicePayments;
