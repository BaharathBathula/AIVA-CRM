"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FilePenLine, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession } from "@/components/providers";
import { Alert, Badge, EmptyState, Kpi, LoadingRows, PageHeader, QUOTE_STATUS_TONE, Tabs } from "@/components/ui";
import { compactMoney, date, moneyExact, number, titleCase } from "@/lib/format";
import type { QuoteStatus, QuoteSummary } from "@/lib/platform-types";
import { useApi } from "@/lib/use-api";

type Filter = "all" | QuoteStatus;

export default function QuotesPage() {
  return (
    <AppShell>
      <Quotes />
    </AppShell>
  );
}

function Quotes() {
  const router = useRouter();
  const { can } = useSession();
  const quotes = useApi<QuoteSummary[]>("/quotes");
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");

  const list = useMemo(() => quotes.data ?? [], [quotes.data]);
  const count = (status: QuoteStatus) => list.filter((quote) => quote.status === status).length;
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return list.filter(
      (quote) =>
        (filter === "all" || quote.status === filter) &&
        (!term ||
          quote.name.toLowerCase().includes(term) ||
          quote.display_number.toLowerCase().includes(term) ||
          (quote.account_name ?? "").toLowerCase().includes(term))
    );
  }, [filter, list, search]);

  const open = list.filter((quote) => quote.status === "sent");
  const accepted = list.filter((quote) => quote.status === "accepted");
  const decided = list.filter((quote) => quote.status === "accepted" || quote.status === "declined").length;

  return (
    <>
      <PageHeader
        title="Quotes"
        description="Priced proposals built from your product catalog, with discounts, tax and approval."
        actions={
          can("sales.quotes") ? (
            <Link href="/quotes/new" className="ui-btn ui-btn-primary">
              <Plus size={16} /> New quote
            </Link>
          ) : null
        }
      />

      <div className="ui-stack">
        <div className="ui-grid ui-grid-4">
          <Kpi label="Awaiting decision" value={compactMoney(open.reduce((sum, q) => sum + q.total, 0))} note={`${open.length} sent quotes`} />
          <Kpi
            label="Accepted"
            value={compactMoney(accepted.reduce((sum, q) => sum + q.total, 0))}
            note={`${accepted.length} quotes`}
            tone={accepted.length ? "success" : undefined}
          />
          <Kpi label="Acceptance rate" value={decided ? `${Math.round((accepted.length / decided) * 100)}%` : "—"} note="Of accepted + declined" />
          <Kpi label="Drafts" value={number(count("draft"))} note="Not yet sent" />
        </div>

        <div className="ui-toolbar" style={{ marginBottom: 0 }}>
          <Tabs
            label="Quote status"
            value={filter}
            onChange={setFilter}
            items={[
              { value: "all", label: "All", count: list.length },
              { value: "draft", label: "Draft", count: count("draft") },
              { value: "sent", label: "Sent", count: count("sent") },
              { value: "accepted", label: "Accepted", count: count("accepted") },
              { value: "declined", label: "Declined", count: count("declined") },
              { value: "expired", label: "Expired", count: count("expired") },
            ]}
          />
          <span className="ui-spacer" />
          <div className="ui-search">
            <Search size={15} />
            <input className="ui-input sm" placeholder="Search quotes" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>

        {quotes.error ? <Alert>{quotes.error}</Alert> : null}

        <div className="ui-card">
          {quotes.loading && !quotes.data ? (
            <LoadingRows />
          ) : rows.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Quote</th>
                    <th>Account</th>
                    <th>Opportunity</th>
                    <th>Status</th>
                    <th>Owner</th>
                    <th>Valid until</th>
                    <th className="ui-num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((quote) => {
                    const expired = quote.status === "sent" && quote.valid_until && new Date(`${quote.valid_until}T23:59:59`) < new Date();
                    return (
                      <tr key={quote.id} className="clickable" onClick={() => router.push(`/quotes/${quote.id}`)}>
                        <td>
                          <Link href={`/quotes/${quote.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                            {quote.name}
                          </Link>
                          <div className="cell-sub">
                            {quote.display_number} · {quote.line_count} line{quote.line_count === 1 ? "" : "s"}
                          </div>
                        </td>
                        <td>{quote.account_name ?? "—"}</td>
                        <td className="ui-truncate" style={{ maxWidth: 220 }}>
                          {quote.opportunity_name ?? "—"}
                        </td>
                        <td>
                          <Badge tone={QUOTE_STATUS_TONE[quote.status]}>{titleCase(quote.status)}</Badge>
                        </td>
                        <td>{quote.owner_name ?? "—"}</td>
                        <td style={{ color: expired ? "var(--danger)" : undefined }}>{date(quote.valid_until)}</td>
                        <td className="ui-num ui-strong">{moneyExact(quote.total, quote.currency)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<FilePenLine size={20} />}
              title={list.length ? "No quotes match" : "No quotes yet"}
              message={list.length ? "Try another status or search." : "Create a quote from an opportunity to send pricing to a customer."}
              action={
                !list.length && can("sales.quotes") ? (
                  <Link href="/quotes/new" className="ui-btn ui-btn-primary">
                    <Plus size={16} /> New quote
                  </Link>
                ) : null
              }
            />
          )}
        </div>
      </div>
    </>
  );
}
