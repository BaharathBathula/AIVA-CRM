"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Ban, PackageCheck, Pencil, Printer, Send, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { OrderLines, linesPayload, type ProductOption } from "@/components/order-lines";
import { PinButton } from "@/components/pins";
import { useSession, useToast } from "@/components/providers";
import { RecordDocuments } from "@/components/record-panels";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, Modal, PageHeader, Progress } from "@/components/ui";
import { date, dateTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { PO_STATUS, formatAddress, type Address, type OrderLine, type PurchaseOrder } from "@/lib/workspace";

export default function PurchaseOrderPage() {
  return (
    <AppShell>
      <PurchaseOrderView />
    </AppShell>
  );
}

interface StockItem {
  id: string;
  name: string;
  sku: string | null;
  unit_price: number;
  unit_cost: number | null;
  is_active: boolean;
}

function PurchaseOrderView() {
  const { orderId } = useParams<{ orderId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can, has } = useSession();
  const order = useApi<PurchaseOrder>(`/purchase-orders/${orderId}`);
  const stock = useApi<{ items: StockItem[] }>(has("inventory") ? "/inventory" : null);
  const catalog = useApi<ProductOption[]>(!has("inventory") ? "/products?active=true" : null);
  const products: ProductOption[] = has("inventory") ? (stock.data?.items ?? []).filter((p) => p.is_active) : catalog.data ?? [];
  const o = order.data;
  const [lines, setLines] = useState<OrderLine[]>([]);
  const [shipping, setShipping] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [receiving, setReceiving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"cancel" | "delete" | null>(null);
  const manage = can("sales.purchasing");
  const canEdit = manage && o?.status === "draft";

  useEffect(() => {
    if (!o || dirty) return;
    setLines((o.line_items ?? []).map((l) => ({ ...l })));
    setShipping(o.shipping);
  }, [o, dirty]);

  async function saveLines() {
    setBusy(true);
    try {
      order.setData(await api.patch<PurchaseOrder>(`/purchase-orders/${orderId}`, { line_items: linesPayload(lines), shipping }));
      setDirty(false);
      toast("Purchase order saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: "issued" | "cancelled") {
    if (dirty) await saveLines();
    setBusy(true);
    try {
      order.setData(await api.post<PurchaseOrder>(`/purchase-orders/${orderId}/status`, { status }));
      setConfirmAction(null);
      toast(status === "issued" ? "Issued. Print or download it to send to the vendor." : "Purchase order cancelled.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/purchase-orders/${orderId}`);
      toast("Purchase order deleted.");
      router.replace("/purchase-orders");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
      setConfirmAction(null);
    }
  }

  if (order.error) return <Alert>{order.error}</Alert>;
  if (!o) return <div className="ui-card"><LoadingRows rows={10} /></div>;

  return (
    <>
      <div className="no-print" style={{ marginBottom: 10 }}>
        <Link href="/purchase-orders" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All purchase orders
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            {o.display_number} · <Badge tone={o.is_late ? "danger" : PO_STATUS[o.status].tone}>{o.is_late ? "Late" : PO_STATUS[o.status].label}</Badge>
          </>
        }
        title={o.subject}
        description={
          <>
            {o.vendor_id ? <Link href={`/vendors/${o.vendor_id}`}>{o.vendor_name}</Link> : "No vendor"}
            {o.owner_name ? ` · Raised by ${o.owner_name}` : ""}
          </>
        }
        actions={
          <div className="ui-actions no-print">
            <PinButton type="purchase_order" id={o.id} />
            <button type="button" className="ui-btn" onClick={() => window.print()}>
              <Printer size={15} /> Print / PDF
            </button>
            {manage && o.status === "draft" ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => void setStatus("issued")} disabled={busy || !lines.length}>
                <Send size={15} /> Issue to vendor
              </button>
            ) : null}
            {manage && (o.status === "issued" || o.status === "partially_received") ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setReceiving(true)}>
                <PackageCheck size={15} /> Receive goods
              </button>
            ) : null}
            {manage && (o.status === "draft" || o.status === "issued") ? (
              <button type="button" className="ui-btn ui-btn-danger" onClick={() => setConfirmAction("cancel")} disabled={busy}>
                <Ban size={15} /> Cancel
              </button>
            ) : null}
          </div>
        }
      />
      {o.status === "received" ? (
        <div style={{ marginBottom: 16 }} className="no-print">
          <Alert tone="success">Everything arrived {dateTime(o.received_at)}. Stock and product costs were updated.</Alert>
        </div>
      ) : null}

      <div className="ui-split">
        <Card
          title="Line items"
          className="quote-doc"
          flush
          footer={
            canEdit ? (
              <>
                <label className="ui-row" style={{ gap: 8, marginRight: "auto", fontSize: 13 }}>
                  Shipping
                  <input
                    className="ui-input sm"
                    type="number"
                    min={0}
                    step="0.01"
                    value={shipping}
                    onChange={(e) => (setShipping(Number(e.target.value) || 0), setDirty(true))}
                    style={{ width: 110, textAlign: "right" }}
                    aria-label="Shipping"
                  />
                </label>
                <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => void saveLines()} disabled={!dirty || busy}>
                  {dirty ? "Save changes" : "Saved"}
                </button>
              </>
            ) : null
          }
        >
          <OrderLines
            lines={lines}
            onChange={(next) => (setLines(next), setDirty(true))}
            locked={!canEdit}
            currency={o.currency}
            products={products}
            mode="purchase"
            shipping={shipping}
            showReceived={o.status !== "draft"}
          />
        </Card>

        <div className="ui-stack">
          {o.status !== "draft" && o.status !== "cancelled" ? (
            <Card title="Delivery">
              <div className="ui-row" style={{ gap: 10 }}>
                <Progress value={o.received_percent} tone={o.received_percent >= 100 ? "success" : undefined} />
                <strong>{o.received_percent}%</strong>
              </div>
              <p className="ui-muted ui-small" style={{ margin: "8px 0 0" }}>
                Issued {dateTime(o.issued_at)}. Expected {date(o.expected_date)}.
              </p>
            </Card>
          ) : null}
          <Card
            title="Vendor"
            actions={
              manage && o.status !== "cancelled" && o.status !== "received" ? (
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm no-print" onClick={() => setEditing(true)}>
                  <Pencil size={14} /> Edit
                </button>
              ) : null
            }
          >
            <dl className="ui-dl">
              <dt>Vendor</dt>
              <dd>{o.vendor ? <Link href={`/vendors/${o.vendor.id}`}>{o.vendor.name}</Link> : "—"}</dd>
              <dt>Contact</dt>
              <dd>{o.vendor?.contact_name ?? "—"}</dd>
              <dt>Email</dt>
              <dd>{o.vendor?.email ? <a href={`mailto:${o.vendor.email}`}>{o.vendor.email}</a> : "—"}</dd>
              <dt>Phone</dt>
              <dd>{o.vendor?.phone ?? "—"}</dd>
              <dt>Their reference</dt>
              <dd>{o.vendor_reference ?? "—"}</dd>
              <dt>Ordered</dt>
              <dd>{date(o.order_date)}</dd>
              <dt>Expected</dt>
              <dd style={{ color: o.is_late ? "var(--danger)" : undefined }}>{date(o.expected_date)}</dd>
              <dt>For order</dt>
              <dd>{o.sales_order ? <Link href={`/sales-orders/${o.sales_order.id}`}>{o.sales_order.display_number}</Link> : "—"}</dd>
            </dl>
          </Card>
          <Card title="Ship to">
            <p className="address" style={{ margin: 0 }}>
              {formatAddress(o.ship_to) || `${o.organization_name || "Our"} main address`}
            </p>
          </Card>
          <Card title="Terms">
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>{o.terms || "No terms."}</p>
            {o.notes ? (
              <p className="no-print" style={{ margin: "12px 0 0", fontSize: 12.5, color: "var(--muted)", whiteSpace: "pre-wrap" }}>
                <strong>Internal note:</strong> {o.notes}
              </p>
            ) : null}
          </Card>
          <div className="no-print">
            <RecordDocuments recordType="purchase_order" recordId={o.id} />
          </div>
          {manage && (o.status === "draft" || o.status === "cancelled") ? (
            <button type="button" className="ui-btn ui-btn-danger no-print" onClick={() => setConfirmAction("delete")}>
              <Trash2 size={15} /> Delete purchase order
            </button>
          ) : null}
        </div>
      </div>

      <ReceiveModal open={receiving} order={o} onClose={() => setReceiving(false)} onDone={(saved) => (order.setData(saved), setReceiving(false))} />
      <EditPurchaseOrder open={editing} order={o} onClose={() => setEditing(false)} onSaved={(saved) => (order.setData(saved), setEditing(false), toast("Saved."))} />
      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction === "delete" ? `Delete ${o.display_number}?` : `Cancel ${o.display_number}?`}
        message={confirmAction === "delete" ? "The purchase order is removed." : "Let the vendor know you no longer need these items."}
        confirmLabel={confirmAction === "delete" ? "Delete" : "Cancel purchase order"}
        busy={busy}
        onConfirm={() => void (confirmAction === "delete" ? remove() : setStatus("cancelled"))}
        onClose={() => setConfirmAction(null)}
      />
    </>
  );
}

function ReceiveModal({ open, order, onClose, onDone }: { open: boolean; order: PurchaseOrder; onClose: () => void; onDone: (order: PurchaseOrder) => void }) {
  const toast = useToast();
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [note, setNote] = useState("");
  const [updateCost, setUpdateCost] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lines = order.line_items ?? [];

  useEffect(() => {
    if (!open) return;
    const initial: Record<number, string> = {};
    lines.forEach((line, index) => {
      const remaining = Number(line.quantity) - Number(line.received_quantity ?? 0);
      initial[index] = remaining > 0 ? String(remaining) : "0";
    });
    setQuantities(initial);
    setNote("");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, order]);

  async function receive() {
    const payload = Object.entries(quantities)
      .map(([index, value]) => ({ index: Number(index), quantity: Number(value) }))
      .filter((line) => line.quantity > 0);
    if (!payload.length) return setError("Enter how many of at least one item arrived.");
    setBusy(true);
    setError(null);
    try {
      const saved = await api.post<PurchaseOrder>(`/purchase-orders/${order.id}/receive`, { lines: payload, note: note.trim() || null, update_cost: updateCost });
      toast(saved.status === "received" ? "Everything received. Stock is updated." : "Received. The rest is still on order.");
      onDone(saved);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      size="lg"
      title="Receive goods"
      description="Received quantities of tracked products go straight into stock."
      onClose={onClose}
      footer={
        <>
          <label className="ui-check" style={{ marginRight: "auto" }}>
            <input type="checkbox" checked={updateCost} onChange={(e) => setUpdateCost(e.target.checked)} /> Update product costs from this order
          </label>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void receive()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : <PackageCheck size={15} />} Receive
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-table-wrap">
        <table className="ui-table">
          <thead>
            <tr>
              <th>Item</th>
              <th className="ui-num">Ordered</th>
              <th className="ui-num">Already in</th>
              <th className="ui-num" style={{ width: 130 }}>
                Arrived now
              </th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line, index) => {
              const remaining = Number(line.quantity) - Number(line.received_quantity ?? 0);
              return (
                <tr key={index}>
                  <td className="cell-title">{line.name}</td>
                  <td className="ui-num">{Number(line.quantity)}</td>
                  <td className="ui-num">{Number(line.received_quantity ?? 0)}</td>
                  <td className="ui-num">
                    <input
                      className="ui-input sm"
                      type="number"
                      min={0}
                      max={remaining}
                      step="1"
                      value={quantities[index] ?? "0"}
                      disabled={remaining <= 0}
                      onChange={(e) => setQuantities({ ...quantities, [index]: e.target.value })}
                      style={{ textAlign: "right" }}
                      aria-label={`Arrived now: ${line.name}`}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Field label="Note" hint="Shown in the stock history, e.g. the delivery note number." className="span-2">
        <input className="ui-input" value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

function EditPurchaseOrder({ open, order, onClose, onSaved }: { open: boolean; order: PurchaseOrder; onClose: () => void; onSaved: (order: PurchaseOrder) => void }) {
  const [form, setForm] = useState({ subject: "", expected_date: "", vendor_reference: "", terms: "", notes: "" });
  const [shipTo, setShipTo] = useState<Address>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setForm({ subject: order.subject, expected_date: order.expected_date ?? "", vendor_reference: order.vendor_reference ?? "", terms: order.terms ?? "", notes: order.notes ?? "" });
    setShipTo(order.ship_to ?? {});
    setError(null);
  }, [open, order]);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: e.target.value });
  const setAddress = (key: keyof Address) => (e: React.ChangeEvent<HTMLInputElement>) => setShipTo({ ...shipTo, [key]: e.target.value || null });

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onSaved(
        await api.patch<PurchaseOrder>(`/purchase-orders/${order.id}`, {
          subject: form.subject.trim(),
          expected_date: form.expected_date || null,
          vendor_reference: form.vendor_reference || null,
          terms: form.terms || null,
          notes: form.notes || null,
          ship_to: shipTo,
        })
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      size="lg"
      title="Edit purchase order"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            Save
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
        <Field label="Subject" className="span-2">
          <input className="ui-input" value={form.subject} onChange={set("subject")} />
        </Field>
        <Field label="Expected delivery">
          <input className="ui-input" type="date" value={form.expected_date} onChange={set("expected_date")} />
        </Field>
        <Field label="Vendor reference">
          <input className="ui-input" value={form.vendor_reference} onChange={set("vendor_reference")} />
        </Field>
        <Field label="Ship to street" className="span-2">
          <input className="ui-input" value={shipTo.street ?? ""} onChange={setAddress("street")} placeholder="Leave empty to use your main address" />
        </Field>
        <Field label="City">
          <input className="ui-input" value={shipTo.city ?? ""} onChange={setAddress("city")} />
        </Field>
        <Field label="State / region">
          <input className="ui-input" value={shipTo.state ?? ""} onChange={setAddress("state")} />
        </Field>
        <Field label="Postal code">
          <input className="ui-input" value={shipTo.postal_code ?? ""} onChange={setAddress("postal_code")} />
        </Field>
        <Field label="Country">
          <input className="ui-input" value={shipTo.country ?? ""} onChange={setAddress("country")} />
        </Field>
        <Field label="Terms" className="span-2">
          <textarea className="ui-textarea" rows={2} value={form.terms} onChange={set("terms")} />
        </Field>
        <Field label="Internal notes" className="span-2">
          <textarea className="ui-textarea" rows={2} value={form.notes} onChange={set("notes")} />
        </Field>
      </div>
    </Modal>
  );
}
