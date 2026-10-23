"use client";

import Image from "next/image";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Printer } from "lucide-react";

import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, LoadingRows } from "@/components/ui";
import { date, moneyExact } from "@/lib/format";
import { useApi } from "@/lib/use-api";

interface InvoiceDetail {
  number: string;
  plan_name: string;
  seats: number;
  unit_price: number;
  amount: number;
  currency: string;
  status: string;
  description: string;
  period_start: string;
  period_end: string;
  issued_at: string;
  paid_at: string | null;
  provider: string;
  organization_name: string;
  billing_email: string | null;
}

export default function BillingInvoicePage() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const invoice = useApi<InvoiceDetail>(`/billing/invoices/${invoiceId}`);
  const data = invoice.data;
  return (
    <SettingsShell>
      <div className="ui-row no-print" style={{ marginBottom: 14 }}>
        <Link href="/settings/billing" className="ui-btn ui-btn-ghost">
          <ArrowLeft size={16} /> Billing
        </Link>
        <span className="ui-spacer" />
        <button type="button" className="ui-btn" onClick={() => window.print()}>
          <Printer size={16} /> Print or save as PDF
        </button>
      </div>
      {invoice.error ? <Alert>{invoice.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <article className="ui-card invoice-doc">
          <header>
            <div>
              <div className="ui-row" style={{ gap: 10, marginBottom: 10 }}>
                <Image src="/aiva-mark.png" alt="" width={34} height={34} />
                <strong style={{ fontSize: 18 }}>AIVA CRM</strong>
              </div>
              <h1>Invoice {data.number}</h1>
              <p className="ui-muted" style={{ margin: "6px 0 0" }}>
                Issued {date(data.issued_at)}
              </p>
            </div>
            <div style={{ textAlign: "right" }}>
              <Badge tone={data.status === "paid" ? "success" : "warning"}>{data.status}</Badge>
              <p style={{ margin: "12px 0 0", fontWeight: 700 }}>{data.organization_name}</p>
              {data.billing_email ? <p className="ui-muted ui-small" style={{ margin: 0 }}>{data.billing_email}</p> : null}
            </div>
          </header>
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Description</th>
                  <th className="ui-num">Users</th>
                  <th className="ui-num">Price</th>
                  <th className="ui-num">Amount</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>
                    AIVA {data.plan_name}
                    <div className="ui-muted ui-small">
                      {date(data.period_start)} – {date(data.period_end)}
                    </div>
                  </td>
                  <td className="ui-num">{data.seats}</td>
                  <td className="ui-num">{moneyExact(data.unit_price, data.currency)}</td>
                  <td className="ui-num">{moneyExact(data.amount, data.currency)}</td>
                </tr>
                <tr className="total-row">
                  <td colSpan={3}>Total</td>
                  <td className="ui-num">{moneyExact(data.amount, data.currency)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="ui-muted ui-small" style={{ marginTop: 22 }}>
            {data.provider === "demo"
              ? "Demo billing: this invoice records a plan change; no payment was taken."
              : `Paid ${date(data.paid_at)} by card through Stripe.`}
          </p>
        </article>
      )}
    </SettingsShell>
  );
}
