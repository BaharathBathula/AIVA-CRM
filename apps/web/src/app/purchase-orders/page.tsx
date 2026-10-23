"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { PackagePlus, Plus, Search, Truck } from "lucide-react";
import { Suspense, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Progress, Tabs } from "@/components/ui";
import { compactMoney, date, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";
import { PO_STATUS, type PurchaseOrder, type Vendor } from "@/lib/workspace";

type Filter = "all" | "draft" | "open" | "received" | "cancelled";

export default function PurchaseOrdersPage() {
  return (
    <FeaturePage feature="purchase_orders">
      <Suspense>
        <PurchaseOrders />
      </Suspense>
    </FeaturePage>
  );
}

function PurchaseOrders() {
  const router = useRouter();
  const params = useSearchParams();
  const { can, has } = useSession();
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const [creating, setCreating] = useState(params.get("new") === "1");
  const [reordering, setReordering] = useState(false);
  const query = new URLSearchParams();
  if (filter !== "all") query.set("status", filter);
  if (debounced.trim()) query.set("search", debounced.trim());
  const orders = useApi<{
    items: PurchaseOrder[];
    summary: { open_value: number; awaiting: number; late: number; received_30d: number; drafts: number };
  }>(`/purchase-orders${query.toString() ? `?${query}` : ""}`);
  const s = orders.data?.summary;

  return (
    <>
      <PageHeader
        title="Purchase orders"
        description="Buy from your vendors, then receive goods to put stock back on the shelf."
        actions={
          can("sales.purchasing") ? (
            <>
              {has("inventory") ? (
                <button type="button" className="ui-btn" onClick={() => setReordering(true)}>
                  <PackagePlus size={16} /> Reorder low stock
                </button>
              ) : null}
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
                <Plus size={16} /> New purchase order
              </button>
            </>
          ) : null
        }
      />
      <div className="ui-stack">
        <div className="ui-grid ui-grid-4">
          <Kpi label="On order" value={s ? compactMoney(s.open_value) : "—"} note={s ? `${s.awaiting} awaiting delivery` : undefined} />
          <Kpi label="Late deliveries" value={s?.late ?? "—"} tone={s?.late ? "danger" : undefined} note="Past the expected date" />
          <Kpi label="Received in 30 days" value={s ? compactMoney(s.received_30d) : "—"} />
          <Kpi label="Drafts" value={s?.drafts ?? "—"} note="Not yet issued" />
        </div>
        <div className="ui-toolbar" style={{ marginBottom: 0 }}>
          <Tabs
            label="Purchase order status"
            value={filter}
            onChange={setFilter}
            items={[
              { value: "all", label: "All" },
              { value: "draft", label: "Draft" },
              { value: "open", label: "Awaiting delivery", count: s?.awaiting },
              { value: "received", label: "Received" },
              { value: "cancelled", label: "Cancelled" },
            ]}
          />
          <span className="ui-spacer" />
          <div className="ui-search">
            <Search size={15} />
            <input className="ui-input sm" placeholder="Search or PO number" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search purchase orders" />
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
                    <th>Purchase order</th>
                    <th>Vendor</th>
                    <th>Status</th>
                    <th style={{ width: 150 }}>Received</th>
                    <th>Expected</th>
                    <th className="ui-num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.data.items.map((po) => (
                    <tr key={po.id} className="clickable" onClick={() => router.push(`/purchase-orders/${po.id}`)}>
                      <td>
                        <Link href={`/purchase-orders/${po.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                          {po.subject}
                        </Link>
                        <div className="cell-sub">
                          {po.display_number}
                          {po.vendor_reference ? ` · Ref ${po.vendor_reference}` : ""}
                        </div>
                      </td>
                      <td>{po.vendor_name ?? "—"}</td>
                      <td>
                        <Badge tone={po.is_late ? "danger" : PO_STATUS[po.status].tone}>{po.is_late ? "Late" : PO_STATUS[po.status].label}</Badge>
                      </td>
                      <td>
                        {po.status === "draft" || po.status === "cancelled" ? (
                          <span className="ui-muted">—</span>
                        ) : (
                          <div className="ui-row" style={{ gap: 8 }}>
                            <Progress value={po.received_percent} tone={po.received_percent >= 100 ? "success" : undefined} />
                            <span className="ui-small ui-muted">{po.received_percent}%</span>
                          </div>
                        )}
                      </td>
                      <td style={{ color: po.is_late ? "var(--danger)" : undefined }}>{date(po.expected_date)}</td>
                      <td className="ui-num ui-strong">{moneyExact(po.total, po.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<Truck size={20} />}
              title={filter !== "all" || search ? "No purchase orders match" : "No purchase orders yet"}
              message={filter !== "all" || search ? "Try another status or search." : "Raise a purchase order with a vendor to restock products."}
            />
          )}
        </div>
      </div>
      <NewPurchaseOrder
        open={creating}
        vendorId={params.get("vendor") ?? ""}
        onClose={() => setCreating(false)}
        onCreated={(po) => router.push(`/purchase-orders/${po.id}`)}
      />
      <ReorderModal open={reordering} onClose={() => setReordering(false)} onCreated={(po) => router.push(`/purchase-orders/${po.id}`)} />
    </>
  );
}

function useVendors(open: boolean) {
  return useApi<{ items: Vendor[] }>(open ? "/vendors?active=true" : null);
}

function NewPurchaseOrder({ open, vendorId, onClose, onCreated }: { open: boolean; vendorId: string; onClose: () => void; onCreated: (po: PurchaseOrder) => void }) {
  const vendors = useVendors(open);
  const [vendor, setVendor] = useState(vendorId);
  const [subject, setSubject] = useState("");
  const [expected, setExpected] = useState("");
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setError(null);
    if (!vendor) return setError("Choose a vendor.");
    const chosen = vendors.data?.items.find((v) => v.id === vendor);
    setBusy(true);
    try {
      const po = await api.post<PurchaseOrder>("/purchase-orders", {
        vendor_id: vendor,
        subject: subject.trim() || `Order from ${chosen?.name ?? "vendor"}`,
        expected_date: expected || null,
        vendor_reference: reference.trim() || null,
        currency: chosen?.currency ?? "USD",
        terms: chosen?.payment_terms ?? null,
      });
      onCreated(po);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New purchase order"
      description="Add the line items on the next screen."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void create()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null} Create
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
        <Field label="Vendor" required hint={vendors.data && !vendors.data.items.length ? <Link href="/vendors?new=1">Add a vendor first</Link> : undefined}>
          <select className="ui-select" value={vendor} onChange={(e) => setVendor(e.target.value)}>
            <option value="">Choose a vendor…</option>
            {(vendors.data?.items ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Expected delivery">
          <input className="ui-input" type="date" value={expected} onChange={(e) => setExpected(e.target.value)} />
        </Field>
        <Field label="Subject" className="span-2">
          <input className="ui-input" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Restock for Q4" />
        </Field>
        <Field label="Vendor reference" hint="Their quote or order number, if any.">
          <input className="ui-input" value={reference} onChange={(e) => setReference(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function ReorderModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (po: PurchaseOrder) => void }) {
  const toast = useToast();
  const vendors = useVendors(open);
  const [vendor, setVendor] = useState("");
  const [busy, setBusy] = useState(false);
  async function create() {
    if (!vendor) return;
    setBusy(true);
    try {
      const po = await api.post<PurchaseOrder>("/purchase-orders/reorder", { vendor_id: vendor });
      toast(`Drafted ${po.display_number} with ${po.line_items?.length ?? 0} products.`);
      onCreated(po);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }
  return (
    <Modal
      open={open}
      size="sm"
      title="Reorder low stock"
      description="Drafts a purchase order for every tracked product at or below its reorder level, topping each up to twice that level."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void create()} disabled={busy || !vendor}>
            {busy ? <span className="ui-spinner" /> : null} Draft order
          </button>
        </>
      }
    >
      <Field label="Order from">
        <select className="ui-select" value={vendor} onChange={(e) => setVendor(e.target.value)}>
          <option value="">Choose a vendor…</option>
          {(vendors.data?.items ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
      </Field>
    </Modal>
  );
}
