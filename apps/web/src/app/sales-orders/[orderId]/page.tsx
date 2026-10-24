"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Ban, Check, FileText, Pencil, Printer, Trash2, Truck } from "lucide-react";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { OrderLines, linesPayload, type ProductOption } from "@/components/order-lines";
import { PinButton } from "@/components/pins";
import { useSession, useToast } from "@/components/providers";
import { RecordDocuments } from "@/components/record-panels";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { date, dateTime, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { SO_STATUS, formatAddress, type Address, type OrderLine, type PriceBook, type SalesOrder } from "@/lib/workspace";
import type { Account } from "@/types/account";
import type { Contact } from "@/types/contact";
import { OrderErp } from "@/components/integrations/record/RecordErp";

export default function SalesOrderPage() {
  return (
    <AppShell>
      <SalesOrderView />
    </AppShell>
  );
}

function SalesOrderView() {
  const { orderId } = useParams<{ orderId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can, has } = useSession();
  const order = useApi<SalesOrder>(`/sales-orders/${orderId}`);
  const products = useApi<ProductOption[]>("/products?active=true");
  const o = order.data;
  const book = useApi<PriceBook>(o?.price_book_id && has("price_books") ? `/price-books/${o.price_book_id}` : null);
  const [lines, setLines] = useState<OrderLine[]>([]);
  const [shipping, setShipping] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [fulfilling, setFulfilling] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"cancel" | "delete" | null>(null);
  const [shortStock, setShortStock] = useState<string | null>(null);
  const canEdit = can("sales.orders") && o?.status === "draft";

  useEffect(() => {
    if (!o || dirty) return;
    setLines((o.line_items ?? []).map((l) => ({ ...l })));
    setShipping(o.shipping);
  }, [o, dirty]);

  async function saveLines() {
    setBusy(true);
    try {
      order.setData(await api.patch<SalesOrder>(`/sales-orders/${orderId}`, { line_items: linesPayload(lines), shipping }));
      setDirty(false);
      toast("Order saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: "confirmed" | "fulfilled" | "cancelled", extra: Record<string, unknown> = {}) {
    if (dirty) await saveLines();
    setBusy(true);
    try {
      const result = await api.post<SalesOrder>(`/sales-orders/${orderId}/status`, { status, ...extra });
      order.setData(result);
      setFulfilling(false);
      setShortStock(null);
      setConfirmAction(null);
      if (result.stock_warnings?.length) toast(`Backordered: ${result.stock_warnings.join("; ")}.`, "danger");
      toast(status === "confirmed" ? "Order confirmed." : status === "fulfilled" ? "Order fulfilled. Stock was updated." : "Order cancelled.");
    } catch (caught) {
      const message = errorMessage(caught);
      if (status === "fulfilled" && message.startsWith("Not enough stock")) setShortStock(message);
      else toast(message, "danger");
    } finally {
      setBusy(false);
    }
  }

  async function createInvoice() {
    setBusy(true);
    try {
      const invoice = await api.post<{ id: string; display_number: string }>(`/invoices/from-sales-order/${orderId}`, { due_in_days: 30 });
      toast(`Created ${invoice.display_number} as a draft.`);
      router.push(`/invoices/${invoice.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/sales-orders/${orderId}`);
      toast("Order deleted.");
      router.replace("/sales-orders");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
      setConfirmAction(null);
    }
  }

  if (order.error) return <Alert>{order.error}</Alert>;
  if (!o) return <div className="ui-card"><LoadingRows rows={10} /></div>;
  const invoiced = (o.invoices ?? []).find((i) => i.status !== "void");
  const manage = can("sales.orders");

  return (
    <>
      <div className="no-print" style={{ marginBottom: 10 }}>
        <Link href="/sales-orders" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All sales orders
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            {o.display_number} · <Badge tone={o.is_overdue ? "danger" : SO_STATUS[o.status].tone}>{o.is_overdue ? "Past due" : SO_STATUS[o.status].label}</Badge>
          </>
        }
        title={o.subject}
        description={
          <>
            {o.account_id ? <Link href={`/accounts/${o.account_id}`}>{o.account_name}</Link> : "No account"}
            {o.opportunity_id ? (
              <>
                {" · "}
                <Link href={`/opportunities/${o.opportunity_id}`}>{o.opportunity_name}</Link>
              </>
            ) : null}
            {o.owner_name ? ` · Owner ${o.owner_name}` : ""}
          </>
        }
        actions={
          <div className="ui-actions no-print">
            <PinButton type="sales_order" id={o.id} />
            <button type="button" className="ui-btn" onClick={() => window.print()}>
              <Printer size={15} /> Print / PDF
            </button>
            {manage && o.status === "draft" ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => void setStatus("confirmed")} disabled={busy || !lines.length}>
                <Check size={15} /> Confirm order
              </button>
            ) : null}
            {manage && o.status === "confirmed" ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setFulfilling(true)} disabled={busy}>
                <Truck size={15} /> Mark fulfilled
              </button>
            ) : null}
            {(o.status === "confirmed" || o.status === "fulfilled") && has("cpq") && can("sales.invoices") ? (
              invoiced ? (
                <Link className="ui-btn" href={`/invoices/${invoiced.id}`}>
                  <FileText size={15} /> {invoiced.display_number}
                </Link>
              ) : (
                <button type="button" className="ui-btn" onClick={() => void createInvoice()} disabled={busy}>
                  <FileText size={15} /> Create invoice
                </button>
              )
            ) : null}
            {manage && (o.status === "draft" || o.status === "confirmed") ? (
              <button type="button" className="ui-btn ui-btn-danger" onClick={() => setConfirmAction("cancel")} disabled={busy}>
                <Ban size={15} /> Cancel order
              </button>
            ) : null}
          </div>
        }
      />

      {o.status === "fulfilled" ? (
        <div style={{ marginBottom: 16 }} className="no-print">
          <Alert tone="success">
            Fulfilled {dateTime(o.fulfilled_at)}
            {o.carrier ? ` via ${o.carrier}` : ""}
            {o.tracking_number ? ` (tracking ${o.tracking_number})` : ""}. {o.stock_applied ? "Stock has been taken out of inventory." : ""}
          </Alert>
        </div>
      ) : null}
      {o.status === "cancelled" ? (
        <div style={{ marginBottom: 16 }} className="no-print">
          <Alert tone="warning">Cancelled {dateTime(o.cancelled_at)}.</Alert>
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
            products={products.data ?? []}
            priceEntries={book.data?.entries}
            shipping={shipping}
          />
          {o.status !== "draft" && o.status !== "cancelled" ? (
            <p className="ui-muted ui-small no-print" style={{ margin: "0 18px 16px" }}>
              Line items are locked once an order is confirmed.
            </p>
          ) : null}
        </Card>

        <div className="ui-stack">
          <Card
            title="Details"
            actions={
              manage && o.status !== "cancelled" ? (
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm no-print" onClick={() => setEditing(true)}>
                  <Pencil size={14} /> Edit
                </button>
              ) : null
            }
          >
            <dl className="ui-dl">
              <dt>Contact</dt>
              <dd>{o.contact_id ? <Link href={`/contacts/${o.contact_id}`}>{o.contact_name}</Link> : "—"}</dd>
              <dt>Customer PO</dt>
              <dd>{o.customer_po ?? "—"}</dd>
              <dt>Ordered</dt>
              <dd>{date(o.order_date)}</dd>
              <dt>Due</dt>
              <dd style={{ color: o.is_overdue ? "var(--danger)" : undefined }}>{date(o.due_date)}</dd>
              <dt>Carrier</dt>
              <dd>{o.carrier ?? "—"}</dd>
              <dt>Tracking</dt>
              <dd>{o.tracking_number ?? "—"}</dd>
              <dt>From quote</dt>
              <dd>{o.quote ? <Link href={`/quotes/${o.quote.id}`}>{o.quote.display_number}</Link> : "—"}</dd>
              <dt>Invoices</dt>
              <dd>
                {(o.invoices ?? []).length
                  ? o.invoices!.map((i) => (
                      <Link key={i.id} href={`/invoices/${i.id}`} style={{ marginRight: 8 }}>
                        {i.display_number}
                      </Link>
                    ))
                  : "—"}
              </dd>
            </dl>
          </Card>
          <Card title="Addresses">
            <div className="address-pair">
              <div>
                <div className="ui-label">Bill to</div>
                <p className="address">{formatAddress(o.billing_address) || "—"}</p>
              </div>
              <div>
                <div className="ui-label">Ship to</div>
                <p className="address">{formatAddress(o.shipping_address) || "—"}</p>
              </div>
            </div>
          </Card>
          <div className="int-slot no-print">
            <OrderErp recordType="sales_order" recordId={o.id} onChanged={() => void order.reload()} />
          </div>
          <Card title="Terms">
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>{o.terms || "No terms."}</p>
            {o.notes ? (
              <p className="no-print" style={{ margin: "12px 0 0", fontSize: 12.5, color: "var(--muted)", whiteSpace: "pre-wrap" }}>
                <strong>Internal note:</strong> {o.notes}
              </p>
            ) : null}
          </Card>
          <div className="no-print">
            <RecordDocuments recordType="sales_order" recordId={o.id} />
          </div>
          {manage && (o.status === "draft" || o.status === "cancelled") ? (
            <button type="button" className="ui-btn ui-btn-danger no-print" onClick={() => setConfirmAction("delete")}>
              <Trash2 size={15} /> Delete order
            </button>
          ) : null}
        </div>
      </div>

      <EditOrder open={editing} order={o} onClose={() => setEditing(false)} onSaved={(saved) => (order.setData(saved), setEditing(false), toast("Order details saved."))} />
      <FulfilModal
        open={fulfilling}
        busy={busy}
        shortStock={shortStock}
        onClose={() => (setFulfilling(false), setShortStock(null))}
        onConfirm={(carrier, tracking, backorder) => void setStatus("fulfilled", { carrier: carrier || null, tracking_number: tracking || null, allow_backorder: backorder })}
      />
      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction === "delete" ? `Delete ${o.display_number}?` : `Cancel ${o.display_number}?`}
        message={confirmAction === "delete" ? "The order and its line items are removed." : "The order stays on record as cancelled. Stock isn't changed."}
        confirmLabel={confirmAction === "delete" ? "Delete" : "Cancel order"}
        busy={busy}
        onConfirm={() => void (confirmAction === "delete" ? remove() : setStatus("cancelled"))}
        onClose={() => setConfirmAction(null)}
      />
    </>
  );
}

function FulfilModal({
  open,
  busy,
  shortStock,
  onClose,
  onConfirm,
}: {
  open: boolean;
  busy: boolean;
  shortStock: string | null;
  onClose: () => void;
  onConfirm: (carrier: string, tracking: string, backorder: boolean) => void;
}) {
  const [carrier, setCarrier] = useState("");
  const [tracking, setTracking] = useState("");
  return (
    <Modal
      open={open}
      title="Mark as fulfilled"
      description="Tracked products are taken out of stock."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          {shortStock ? (
            <button type="button" className="ui-btn ui-btn-danger solid" onClick={() => onConfirm(carrier, tracking, true)} disabled={busy}>
              Fulfil as backorder
            </button>
          ) : (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => onConfirm(carrier, tracking, false)} disabled={busy}>
              {busy ? <span className="ui-spinner" /> : <Truck size={15} />} Fulfil order
            </button>
          )}
        </>
      }
    >
      {shortStock ? (
        <div style={{ marginBottom: 12 }}>
          <Alert tone="warning">{shortStock}</Alert>
        </div>
      ) : null}
      <div className="ui-form-grid">
        <Field label="Carrier">
          <input className="ui-input" value={carrier} onChange={(e) => setCarrier(e.target.value)} placeholder="UPS, FedEx, own delivery…" />
        </Field>
        <Field label="Tracking number">
          <input className="ui-input" value={tracking} onChange={(e) => setTracking(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function AddressFields({ label, value, onChange }: { label: string; value: Address; onChange: (next: Address) => void }) {
  const set = (key: keyof Address) => (e: React.ChangeEvent<HTMLInputElement>) => onChange({ ...value, [key]: e.target.value || null });
  return (
    <fieldset className="address-fields span-2">
      <legend className="ui-label">{label}</legend>
      <div className="ui-form-grid">
        <Field label="Street" className="span-2">
          <input className="ui-input" value={value.street ?? ""} onChange={set("street")} />
        </Field>
        <Field label="City">
          <input className="ui-input" value={value.city ?? ""} onChange={set("city")} />
        </Field>
        <Field label="State / region">
          <input className="ui-input" value={value.state ?? ""} onChange={set("state")} />
        </Field>
        <Field label="Postal code">
          <input className="ui-input" value={value.postal_code ?? ""} onChange={set("postal_code")} />
        </Field>
        <Field label="Country">
          <input className="ui-input" value={value.country ?? ""} onChange={set("country")} />
        </Field>
      </div>
    </fieldset>
  );
}

function EditOrder({ open, order, onClose, onSaved }: { open: boolean; order: SalesOrder; onClose: () => void; onSaved: (order: SalesOrder) => void }) {
  const accounts = useApi<Account[]>(open ? "/accounts?limit=100" : null);
  const [form, setForm] = useState({ subject: "", account_id: "", contact_id: "", order_date: "", due_date: "", customer_po: "", carrier: "", tracking_number: "", terms: "", notes: "" });
  const [billing, setBilling] = useState<Address>({});
  const [shippingAddress, setShippingAddress] = useState<Address>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const contacts = useApi<Contact[]>(open && form.account_id ? `/contacts?account_id=${form.account_id}&limit=100` : null);
  const shippingOnly = order.status === "fulfilled";

  useEffect(() => {
    if (!open) return;
    setForm({
      subject: order.subject,
      account_id: order.account_id ?? "",
      contact_id: order.contact_id ?? "",
      order_date: order.order_date ?? "",
      due_date: order.due_date ?? "",
      customer_po: order.customer_po ?? "",
      carrier: order.carrier ?? "",
      tracking_number: order.tracking_number ?? "",
      terms: order.terms ?? "",
      notes: order.notes ?? "",
    });
    setBilling(order.billing_address ?? {});
    setShippingAddress(order.shipping_address ?? {});
    setError(null);
  }, [open, order]);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: e.target.value });

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const payload = shippingOnly
        ? { carrier: form.carrier || null, tracking_number: form.tracking_number || null, notes: form.notes || null }
        : {
            subject: form.subject.trim(),
            account_id: form.account_id || null,
            contact_id: form.contact_id || null,
            order_date: form.order_date || null,
            due_date: form.due_date || null,
            customer_po: form.customer_po || null,
            carrier: form.carrier || null,
            tracking_number: form.tracking_number || null,
            terms: form.terms || null,
            notes: form.notes || null,
            billing_address: billing,
            shipping_address: shippingAddress,
          };
      onSaved(await api.patch<SalesOrder>(`/sales-orders/${order.id}`, payload));
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
      title="Edit order details"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            Save details
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
        {!shippingOnly ? (
          <>
            <Field label="Subject" required className="span-2">
              <input className="ui-input" value={form.subject} onChange={set("subject")} />
            </Field>
            <Field label="Account">
              <select className="ui-select" value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value, contact_id: "" })}>
                <option value="">No account</option>
                {(accounts.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Contact">
              <select className="ui-select" value={form.contact_id} onChange={set("contact_id")} disabled={!form.account_id}>
                <option value="">No contact</option>
                {(contacts.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.first_name} {c.last_name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Order date">
              <input className="ui-input" type="date" value={form.order_date} onChange={set("order_date")} />
            </Field>
            <Field label="Due date">
              <input className="ui-input" type="date" value={form.due_date} onChange={set("due_date")} />
            </Field>
            <Field label="Customer PO number">
              <input className="ui-input" value={form.customer_po} onChange={set("customer_po")} />
            </Field>
            <span />
          </>
        ) : null}
        <Field label="Carrier">
          <input className="ui-input" value={form.carrier} onChange={set("carrier")} />
        </Field>
        <Field label="Tracking number">
          <input className="ui-input" value={form.tracking_number} onChange={set("tracking_number")} />
        </Field>
        {!shippingOnly ? (
          <>
            <AddressFields label="Bill to" value={billing} onChange={setBilling} />
            <AddressFields label="Ship to" value={shippingAddress} onChange={setShippingAddress} />
            <Field label="Terms" className="span-2">
              <textarea className="ui-textarea" rows={2} value={form.terms} onChange={set("terms")} />
            </Field>
          </>
        ) : null}
        <Field label="Internal notes" className="span-2">
          <textarea className="ui-textarea" rows={2} value={form.notes} onChange={set("notes")} />
        </Field>
      </div>
    </Modal>
  );
}
