"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { PackageCheck, Plus, Search } from "lucide-react";
import { Suspense, useEffect, useMemo, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession } from "@/components/providers";
import { Alert, Badge, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { compactMoney, date, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";
import { SO_STATUS, type PriceBook, type SalesOrder } from "@/lib/workspace";
import type { Account } from "@/types/account";
import type { Contact } from "@/types/contact";
import type { Opportunity } from "@/types/opportunity";

type Filter = "all" | "draft" | "confirmed" | "overdue" | "fulfilled" | "cancelled";

export default function SalesOrdersPage() {
  return (
    <FeaturePage feature="sales_orders">
      <Suspense>
        <SalesOrders />
      </Suspense>
    </FeaturePage>
  );
}

function SalesOrders() {
  const router = useRouter();
  const params = useSearchParams();
  const { can } = useSession();
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const [creating, setCreating] = useState(params.get("new") === "1");
  const query = new URLSearchParams();
  if (filter !== "all") query.set("status", filter);
  if (debounced.trim()) query.set("search", debounced.trim());
  const orders = useApi<{
    items: SalesOrder[];
    summary: { to_fulfil: number; to_fulfil_count: number; overdue: number; fulfilled_30d: number; drafts: number };
    can_manage: boolean;
  }>(`/sales-orders${query.toString() ? `?${query}` : ""}`);
  const s = orders.data?.summary;

  return (
    <>
      <PageHeader
        title="Sales orders"
        description="Confirmed customer orders: what to ship, what's late and what's ready to invoice."
        actions={
          can("sales.orders") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New sales order
            </button>
          ) : null
        }
      />
      <div className="ui-stack">
        <div className="ui-grid ui-grid-4">
          <Kpi label="To fulfil" value={s ? compactMoney(s.to_fulfil) : "—"} note={s ? `${s.to_fulfil_count} confirmed orders` : undefined} />
          <Kpi label="Past due" value={s?.overdue ?? "—"} tone={s?.overdue ? "danger" : undefined} note="Confirmed, due date passed" />
          <Kpi label="Fulfilled in 30 days" value={s ? compactMoney(s.fulfilled_30d) : "—"} tone={s?.fulfilled_30d ? "success" : undefined} />
          <Kpi label="Drafts" value={s?.drafts ?? "—"} note="Not yet confirmed" />
        </div>
        <div className="ui-toolbar" style={{ marginBottom: 0 }}>
          <Tabs
            label="Order status"
            value={filter}
            onChange={setFilter}
            items={[
              { value: "all", label: "All" },
              { value: "draft", label: "Draft" },
              { value: "confirmed", label: "Confirmed" },
              { value: "overdue", label: "Past due", count: s?.overdue, hot: true },
              { value: "fulfilled", label: "Fulfilled" },
              { value: "cancelled", label: "Cancelled" },
            ]}
          />
          <span className="ui-spacer" />
          <div className="ui-search">
            <Search size={15} />
            <input className="ui-input sm" placeholder="Search orders or SO number" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search sales orders" />
          </div>
        </div>
        {orders.error ? <Alert>{orders.error}</Alert> : null}
        <div className="ui-card">
          {orders.loading && !orders.data ? (
            <LoadingRows />
          ) : orders.data?.items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Account</th>
                    <th>Status</th>
                    <th>Ordered</th>
                    <th>Due</th>
                    <th>Owner</th>
                    <th className="ui-num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.data.items.map((order) => (
                    <tr key={order.id} className="clickable" onClick={() => router.push(`/sales-orders/${order.id}`)}>
                      <td>
                        <Link href={`/sales-orders/${order.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                          {order.subject}
                        </Link>
                        <div className="cell-sub">
                          {order.display_number} · {order.line_count} line{order.line_count === 1 ? "" : "s"}
                          {order.customer_po ? ` · PO ${order.customer_po}` : ""}
                        </div>
                      </td>
                      <td>{order.account_name ?? "—"}</td>
                      <td>
                        <Badge tone={order.is_overdue ? "danger" : SO_STATUS[order.status].tone}>{order.is_overdue ? "Past due" : SO_STATUS[order.status].label}</Badge>
                      </td>
                      <td>{date(order.order_date)}</td>
                      <td>{date(order.due_date)}</td>
                      <td>{order.owner_name ?? "—"}</td>
                      <td className="ui-num ui-strong">{moneyExact(order.total, order.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<PackageCheck size={20} />}
              title={filter !== "all" || search ? "No orders match" : "No sales orders yet"}
              message={
                filter !== "all" || search ? (
                  "Try another status or search."
                ) : (
                  <>
                    Open an accepted quote and choose <strong>Create sales order</strong>, or start one here.
                  </>
                )
              }
            />
          )}
        </div>
      </div>
      <NewSalesOrder open={creating} onClose={() => setCreating(false)} onCreated={(order) => router.push(`/sales-orders/${order.id}`)} />
    </>
  );
}

function NewSalesOrder({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (order: SalesOrder) => void }) {
  const { has } = useSession();
  const accounts = useApi<Account[]>(open ? "/accounts?limit=100" : null);
  const opportunities = useApi<Opportunity[]>(open ? "/opportunities?limit=100" : null);
  const books = useApi<{ items: PriceBook[] }>(open && has("price_books") ? "/price-books?active=true" : null);
  const [subject, setSubject] = useState("");
  const [accountId, setAccountId] = useState("");
  const [contactId, setContactId] = useState("");
  const [opportunityId, setOpportunityId] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [customerPo, setCustomerPo] = useState("");
  const [bookId, setBookId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const contacts = useApi<Contact[]>(open && accountId ? `/contacts?account_id=${accountId}&limit=100` : null);
  const opportunity = useMemo(() => opportunities.data?.find((o) => o.id === opportunityId), [opportunities.data, opportunityId]);

  useEffect(() => {
    if (!opportunity) return;
    setAccountId(opportunity.account_id);
    if (!subject) setSubject(opportunity.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity]);

  async function create() {
    setError(null);
    if (!subject.trim()) return setError("Give the order a subject.");
    setBusy(true);
    try {
      const order = await api.post<SalesOrder>("/sales-orders", {
        subject: subject.trim(),
        account_id: accountId || null,
        contact_id: contactId || null,
        opportunity_id: opportunityId || null,
        due_date: dueDate || null,
        customer_po: customerPo.trim() || null,
        price_book_id: bookId || null,
      });
      onCreated(order);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      size="lg"
      title="New sales order"
      description="Add the line items on the next screen."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void create()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null} Create order
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-form-grid">
        <Field label="Subject" required className="span-2">
          <input className="ui-input" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Spring equipment order" />
        </Field>
        <Field label="Deal">
          <select className="ui-select" value={opportunityId} onChange={(e) => setOpportunityId(e.target.value)}>
            <option value="">No deal</option>
            {(opportunities.data ?? []).map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Account">
          <select className="ui-select" value={accountId} onChange={(e) => (setAccountId(e.target.value), setContactId(""))} disabled={Boolean(opportunity)}>
            <option value="">No account</option>
            {(accounts.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Contact">
          <select className="ui-select" value={contactId} onChange={(e) => setContactId(e.target.value)} disabled={!accountId}>
            <option value="">{accountId ? "No contact" : "Choose an account first"}</option>
            {(contacts.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.first_name} {c.last_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Due date">
          <input className="ui-input" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
        </Field>
        <Field label="Customer PO number">
          <input className="ui-input" value={customerPo} onChange={(e) => setCustomerPo(e.target.value)} />
        </Field>
        {has("price_books") ? (
          <Field label="Price book" hint="Catalog lines are priced from this book.">
            <select className="ui-select" value={bookId} onChange={(e) => setBookId(e.target.value)}>
              <option value="">Standard prices</option>
              {(books.data?.items ?? []).map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
      </div>
    </Modal>
  );
}
