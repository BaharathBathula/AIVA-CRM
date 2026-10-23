"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Pencil, Plus, Power, Trash2, Truck } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { PinButton } from "@/components/pins";
import { useToast } from "@/components/providers";
import { RecordDocuments } from "@/components/record-panels";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Kpi, LoadingRows, PageHeader } from "@/components/ui";
import { VendorModal } from "@/components/vendor-modal";
import { compactMoney, date, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { PO_STATUS, type PurchaseOrder, type Vendor } from "@/lib/workspace";

interface VendorDetail extends Vendor {
  purchase_orders: PurchaseOrder[];
  products: { product_id: string | null; name: string; quantity: number; last_cost: number | null }[];
  can_manage: boolean;
}

export default function VendorPage() {
  return (
    <FeaturePage feature="purchase_orders">
      <VendorView />
    </FeaturePage>
  );
}

function VendorView() {
  const { vendorId } = useParams<{ vendorId: string }>();
  const router = useRouter();
  const toast = useToast();
  const vendor = useApi<VendorDetail>(`/vendors/${vendorId}`);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const v = vendor.data;

  async function toggleActive() {
    if (!v) return;
    setBusy(true);
    try {
      await api.patch(`/vendors/${v.id}`, { is_active: !v.is_active });
      await vendor.reload();
      toast(v.is_active ? "Marked inactive. It can't be used on new purchase orders." : "Vendor reactivated.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/vendors/${vendorId}`);
      toast("Vendor deleted.");
      router.replace("/vendors");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
      setDeleting(false);
    }
  }

  if (vendor.error) return <Alert>{vendor.error}</Alert>;
  if (!v) return <div className="ui-card"><LoadingRows rows={8} /></div>;
  const address = [v.street, [v.city, v.state, v.postal_code].filter(Boolean).join(", "), v.country].filter(Boolean).join("\n");

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/vendors" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All vendors
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            {v.category ?? "Vendor"} {!v.is_active ? <Badge>Inactive</Badge> : null}
          </>
        }
        title={v.name}
        description={[v.contact_name, v.email, v.phone].filter(Boolean).join(" · ") || "No contact details yet"}
        actions={
          <>
            <PinButton type="vendor" id={v.id} />
            {v.can_manage ? (
              <>
                <button type="button" className="ui-btn" onClick={() => setEditing(true)}>
                  <Pencil size={15} /> Edit
                </button>
                <button type="button" className="ui-btn" onClick={() => void toggleActive()} disabled={busy}>
                  <Power size={15} /> {v.is_active ? "Mark inactive" : "Reactivate"}
                </button>
                {v.is_active ? (
                  <Link className="ui-btn ui-btn-primary" href={`/purchase-orders?new=1&vendor=${v.id}`}>
                    <Plus size={15} /> New purchase order
                  </Link>
                ) : null}
              </>
            ) : null}
          </>
        }
      />
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Total spend" value={compactMoney(v.total_spend ?? 0, v.currency)} />
        <Kpi label="Open orders" value={v.open_orders ?? 0} />
        <Kpi label="All orders" value={v.order_count ?? 0} />
        <Kpi label="Payment terms" value={v.payment_terms ?? "—"} />
      </div>
      <div className="ui-split">
        <div className="ui-stack">
          <Card title="Purchase orders" icon={<Truck size={16} />} flush>
            {v.purchase_orders.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Purchase order</th>
                      <th>Status</th>
                      <th>Ordered</th>
                      <th>Expected</th>
                      <th className="ui-num">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.purchase_orders.map((po) => (
                      <tr key={po.id} className="clickable" onClick={() => router.push(`/purchase-orders/${po.id}`)}>
                        <td>
                          <Link href={`/purchase-orders/${po.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                            {po.subject}
                          </Link>
                          <div className="cell-sub">{po.display_number}</div>
                        </td>
                        <td>
                          <Badge tone={po.is_late ? "danger" : PO_STATUS[po.status].tone}>{po.is_late ? "Late" : PO_STATUS[po.status].label}</Badge>
                        </td>
                        <td>{date(po.order_date)}</td>
                        <td>{date(po.expected_date)}</td>
                        <td className="ui-num ui-strong">{moneyExact(po.total, po.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon={<Truck size={20} />} title="No purchase orders yet" />
            )}
          </Card>
          <Card title="What you buy here" flush>
            {v.products.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th className="ui-num">Ordered</th>
                      <th className="ui-num">Last cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.products.map((p) => (
                      <tr key={p.product_id ?? p.name}>
                        <td className="cell-title">{p.name}</td>
                        <td className="ui-num">{p.quantity}</td>
                        <td className="ui-num">{p.last_cost !== null ? moneyExact(p.last_cost, v.currency) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="ui-muted ui-small" style={{ padding: 18, margin: 0 }}>
                Items appear here once you order them from this vendor.
              </p>
            )}
          </Card>
        </div>
        <div className="ui-stack">
          <Card title="Details">
            <dl className="ui-dl">
              <dt>Website</dt>
              <dd>{v.website ? <a href={v.website} target="_blank" rel="noopener noreferrer">{v.website}</a> : "—"}</dd>
              <dt>Currency</dt>
              <dd>{v.currency}</dd>
              <dt>GL account</dt>
              <dd>{v.gl_account ?? "—"}</dd>
              <dt>Tax ID</dt>
              <dd>{v.tax_id ?? "—"}</dd>
              <dt>Address</dt>
              <dd className="address">{address || "—"}</dd>
              <dt>Owner</dt>
              <dd>{v.owner_name ?? "—"}</dd>
            </dl>
            {v.notes ? <p className="ui-muted ui-small" style={{ margin: "12px 0 0", whiteSpace: "pre-wrap" }}>{v.notes}</p> : null}
          </Card>
          <RecordDocuments recordType="vendor" recordId={v.id} />
          {v.can_manage && !v.order_count ? (
            <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDeleting(true)}>
              <Trash2 size={15} /> Delete vendor
            </button>
          ) : null}
        </div>
      </div>
      <VendorModal open={editing} vendor={v} onClose={() => setEditing(false)} onSaved={() => (setEditing(false), void vendor.reload(), toast("Vendor saved."))} />
      <ConfirmDialog open={deleting} title={`Delete ${v.name}?`} message="The vendor is removed permanently." busy={busy} onConfirm={() => void remove()} onClose={() => setDeleting(false)} />
    </>
  );
}
