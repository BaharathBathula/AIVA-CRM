"use client";

import Link from "next/link";
import { CreditCard, ExternalLink, Mail, Receipt } from "lucide-react";
import { useEffect, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { SettingRow } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { date, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Invoice = {
  id: string;
  number: string;
  plan_name: string;
  seats: number;
  amount: number;
  status: string;
  description: string;
  issued_at: string;
  provider: string;
};

type Billing = {
  plan: string;
  plan_name: string;
  status: string;
  provider: string;
  stripe_enabled: boolean;
  billing_email: string | null;
  payment_method: string | null;
  seats_billed: number;
  price_per_seat: number;
  monthly_total: number;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  can_manage: boolean;
  invoices: Invoice[];
};

const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  paid: "success",
  open: "warning",
  void: "neutral",
  uncollectible: "danger",
};

export default function InvoicesPage() {
  return (
    <SettingsShell>
      <Invoices />
    </SettingsShell>
  );
}

function Invoices() {
  const toast = useToast();
  const { can } = useSession();
  const billing = useApi<Billing>("/billing");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const data = billing.data;
  const manage = can("admin.settings");

  useEffect(() => {
    if (data) setEmail(data.billing_email ?? "");
  }, [data]);

  async function openPortal() {
    setBusy(true);
    try {
      const result = await api.post<{ redirect_url: string | null }>("/billing/portal");
      if (result.redirect_url) window.location.href = result.redirect_url;
      else toast("Payment details are managed in Stripe once you're on a paid plan.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function saveEmail() {
    setBusy(true);
    try {
      await api.patch("/billing/email", { billing_email: email.trim() });
      toast("Billing email saved.");
      await billing.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const year = new Date().getFullYear();
  const paidThisYear = (data?.invoices ?? []).filter((i) => new Date(i.issued_at).getFullYear() === year && i.status === "paid").reduce((sum, i) => sum + Number(i.amount), 0);

  return (
    <>
      <PageHeader title="Billing" description="Invoices, payment method and where receipts go. Change your plan in Plan overview." />
      {billing.error ? <Alert>{billing.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <div className="ui-stack">
          <div className="settings-kpis three">
            <Card>
              <div className="ui-kpi-label">Plan</div>
              <div className="ui-kpi-value" style={{ fontSize: 22 }}>
                {data.plan_name}
              </div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                {data.seats_billed} user{data.seats_billed === 1 ? "" : "s"} × {moneyExact(data.price_per_seat)} ={" "}
                {moneyExact(data.monthly_total)} a month
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">{data.cancel_at_period_end ? "Ends on" : "Next renewal"}</div>
              <div className="ui-kpi-value" style={{ fontSize: 22 }}>
                {data.current_period_end ? date(data.current_period_end) : "—"}
              </div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                {data.cancel_at_period_end ? "Your plan won't renew." : "Billed monthly per active user."}
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">Paid in {year}</div>
              <div className="ui-kpi-value" style={{ fontSize: 22 }}>
                {moneyExact(paidThisYear)}
              </div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                Across {data.invoices.filter((i) => new Date(i.issued_at).getFullYear() === year).length} invoices, including add-ons
              </p>
            </Card>
          </div>

          <Card title="Payment" icon={<CreditCard size={17} />}>
            <div className="settings-rows">
              <SettingRow
                label="Payment method"
                hint={data.stripe_enabled ? "Cards and invoices are handled securely by Stripe." : "This server is in demo billing mode: nothing is charged."}
              >
                <strong className="ui-small">
                  {data.payment_method ?? (data.provider === "stripe" ? "Card on file" : data.plan === "trial" ? "Not needed yet" : "Demo — no charge")}
                </strong>
                {manage && data.provider === "stripe" ? (
                  <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void openPortal()}>
                    <ExternalLink size={14} /> Manage in Stripe
                  </button>
                ) : null}
              </SettingRow>
              <SettingRow label="Billing email" hint="Receipts and payment problems go here.">
                {manage ? (
                  <>
                    <input className="ui-input sm" type="email" aria-label="Billing email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="billing@company.com" style={{ maxWidth: 260 }} />
                    <button type="button" className="ui-btn ui-btn-sm" disabled={busy || email.trim().length < 3 || email === (data.billing_email ?? "")} onClick={() => void saveEmail()}>
                      <Mail size={14} /> Save
                    </button>
                  </>
                ) : (
                  <span className="ui-small">{data.billing_email ?? "Not set"}</span>
                )}
              </SettingRow>
            </div>
          </Card>

          <Card title="Invoices" icon={<Receipt size={17} />} flush>
            {data.invoices.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Invoice</th>
                      <th>Date</th>
                      <th>Description</th>
                      <th>Status</th>
                      <th className="ui-num">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.invoices.map((invoice) => (
                      <tr key={invoice.id}>
                        <td>
                          <Link href={`/settings/billing/invoices/${invoice.id}`} className="cell-title">
                            {invoice.number}
                          </Link>
                        </td>
                        <td className="ui-nowrap">{date(invoice.issued_at)}</td>
                        <td className="ui-small" style={{ maxWidth: 420 }}>
                          {invoice.description || `${invoice.plan_name} · ${invoice.seats} users`}
                        </td>
                        <td>
                          <Badge tone={STATUS_TONE[invoice.status] ?? "neutral"}>{invoice.status}</Badge>
                        </td>
                        <td className="ui-num ui-strong">{moneyExact(invoice.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon={<Receipt size={20} />} title="No invoices yet" message="Invoices appear here once you're on a paid plan or buy an add-on." />
            )}
          </Card>
        </div>
      )}
    </>
  );
}
