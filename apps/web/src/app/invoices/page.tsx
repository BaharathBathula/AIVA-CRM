"use client";

import Link from "next/link";
import { Download, Receipt } from "lucide-react";
import { useState } from "react";

import { FeaturePage, PlanChip } from "@/components/plan";
import { useSession } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Kpi, LoadingRows, PageHeader, Tabs } from "@/components/ui";
import { compactMoney, date, moneyExact } from "@/lib/format";
import { INVOICE_STATUS, type InvoiceSummary } from "@/lib/invoices";
import { useApi } from "@/lib/use-api";

type Filter = "all" | "draft" | "sent" | "overdue" | "paid" | "void";

export default function InvoicesPage() {
  return (
    <FeaturePage feature="cpq">
      <Invoices />
    </FeaturePage>
  );
}

function Invoices() {
  const { has, can } = useSession();
  const [filter, setFilter] = useState<Filter>("all");
  const invoices = useApi<{ items: InvoiceSummary[]; summary: { outstanding: number; overdue: number; paid_30d: number; drafts: number } }>(
    `/invoices${filter === "all" ? "" : `?status=${filter}`}`,
  );
  const s = invoices.data?.summary;
  const quickbooks = has("quickbooks");

  return (
    <>
      <PageHeader
        title="Invoices"
        description="Turn accepted quotes into invoices, send them, and record payments as they come in."
        actions={
          can("records.export") ? (
            quickbooks ? (
              <>
                <a className="ui-btn" href="/api/aiva/quickbooks/customers.csv">
                  <Download size={15} /> QuickBooks customers
                </a>
                <a className="ui-btn ui-btn-primary" href="/api/aiva/quickbooks/invoices.csv">
                  <Download size={15} /> QuickBooks invoices
                </a>
              </>
            ) : (
              <Link className="ui-btn" href="/settings/billing?plan=enterprise">
                QuickBooks export <PlanChip feature="quickbooks" />
              </Link>
            )
          ) : null
        }
      />
      {invoices.error ? <Alert>{invoices.error}</Alert> : null}
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Outstanding" value={s ? compactMoney(s.outstanding) : "—"} />
        <Kpi label="Overdue" value={s ? compactMoney(s.overdue) : "—"} />
        <Kpi label="Paid in the last 30 days" value={s ? compactMoney(s.paid_30d) : "—"} />
        <Kpi label="Drafts" value={s?.drafts ?? "—"} />
      </div>
      <Card
        flush
        title="All invoices"
        actions={
          <Tabs
            label="Status"
            value={filter}
            onChange={setFilter}
            items={[
              { value: "all", label: "All" },
              { value: "draft", label: "Draft" },
              { value: "sent", label: "Sent" },
              { value: "overdue", label: "Overdue" },
              { value: "paid", label: "Paid" },
              { value: "void", label: "Void" },
            ]}
          />
        }
      >
        {invoices.loading && !invoices.data ? (
          <LoadingRows />
        ) : invoices.data?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>Customer</th>
                  <th>Status</th>
                  <th>Issued</th>
                  <th>Due</th>
                  <th className="ui-num">Total</th>
                  <th className="ui-num">Balance</th>
                </tr>
              </thead>
              <tbody>
                {invoices.data.items.map((invoice) => (
                  <tr key={invoice.id}>
                    <td className="cell-title">
                      <Link href={`/invoices/${invoice.id}`}>{invoice.display_number}</Link>
                      {invoice.exported_at ? <div className="ui-muted ui-small">Exported to QuickBooks</div> : null}
                    </td>
                    <td>{invoice.account_name ?? "—"}</td>
                    <td>
                      <Badge tone={INVOICE_STATUS[invoice.status].tone}>{INVOICE_STATUS[invoice.status].label}</Badge>
                    </td>
                    <td>{date(invoice.issue_date)}</td>
                    <td>{date(invoice.due_date)}</td>
                    <td className="ui-num">{moneyExact(invoice.total, invoice.currency)}</td>
                    <td className="ui-num ui-strong">{moneyExact(invoice.balance, invoice.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Receipt size={20} />}
            title="No invoices here"
            message={
              <>
                Open an accepted quote and choose <strong>Create invoice</strong>. <Link href="/quotes">Go to quotes</Link>
              </>
            }
          />
        )}
      </Card>
    </>
  );
}
