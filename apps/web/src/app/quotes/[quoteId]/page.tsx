"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  Copy,
  FileText,
  PackageCheck,
  Pencil,
  Plus,
  Printer,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession, useToast } from "@/components/providers";
import { QuoteDetailsFields, detailsPayload, type QuoteDetails } from "@/components/quote-details-form";
import { ApprovalModal, approvalFromError, type ApprovalNeeded } from "@/components/record-extras";
import { Alert, Badge, Card, ConfirmDialog, LoadingRows, Modal, PageHeader, QUOTE_STATUS_TONE } from "@/components/ui";
import { date, dateTime, moneyExact, titleCase } from "@/lib/format";
import type { Product, Quote, QuoteLine, QuoteStatus } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { bookPrice, type PriceBook } from "@/lib/workspace";
import { PinButton } from "@/components/pins";
import { RecordCloudFiles } from "@/components/integrations/record/RecordCloudFiles";
import { RecordSignatures } from "@/components/integrations/record/RecordSignatures";
import { OrderErp } from "@/components/integrations/record/RecordErp";
import { ShareToSlackButton } from "@/components/integrations/record/ShareToSlackButton";

function round(value: number) {
  return Math.round(value * 100) / 100;
}

function lineTotals(lines: QuoteLine[]) {
  let subtotal = 0;
  let discount = 0;
  let tax = 0;
  for (const line of lines) {
    const gross = round((Number(line.quantity) || 0) * (Number(line.unit_price) || 0));
    const lineDiscount = round((gross * (Number(line.discount_percent) || 0)) / 100);
    const net = gross - lineDiscount;
    subtotal += gross;
    discount += lineDiscount;
    tax += round((net * (Number(line.tax_percent) || 0)) / 100);
  }
  return { subtotal: round(subtotal), discount: round(discount), tax: round(tax), total: round(subtotal - discount + tax) };
}

export default function QuotePage() {
  return (
    <AppShell>
      <QuoteBuilder />
    </AppShell>
  );
}

function QuoteBuilder() {
  const { quoteId } = useParams<{ quoteId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can, has } = useSession();
  const quote = useApi<Quote>(`/quotes/${quoteId}`);
  const [approval, setApproval] = useState<ApprovalNeeded | null>(null);
  const products = useApi<Product[]>("/products?active=true");
  const [lines, setLines] = useState<QuoteLine[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [details, setDetails] = useState<QuoteDetails | null>(null);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [pick, setPick] = useState("");

  const q = quote.data;
  const usesBooks = has("price_books");
  const book = useApi<PriceBook>(usesBooks && q?.price_book_id ? `/price-books/${q.price_book_id}` : null);
  const books = useApi<{ items: PriceBook[] }>(usesBooks ? "/price-books?active=true" : null);
  const canEdit = can("sales.quotes");
  const locked = !q || q.status === "accepted" || q.status === "declined" || !canEdit;
  const totals = useMemo(() => lineTotals(lines), [lines]);

  // Take the server's lines unless the user has unsaved edits.
  useEffect(() => {
    if (!q || dirty) return;
    setLines(q.line_items.map((line) => ({ ...line })));
  }, [q, dirty]);

  // Warn before leaving with unsaved line edits.
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  function updateLine(index: number, changes: Partial<QuoteLine>) {
    setLines((current) =>
      current.map((line, i) => {
        if (i !== index) return line;
        const merged = { ...line, ...changes };
        // A new quantity can move a price-book line into another volume tier.
        if (changes.quantity !== undefined && merged.product_id) {
          const price = bookPrice(book.data?.entries, merged.product_id, Number(merged.quantity) || 0);
          if (price && Number(merged.unit_price) === price.unit_price) merged.discount_percent = price.discount_percent;
        }
        return merged;
      })
    );
    setDirty(true);
  }

  function addProduct(productId: string) {
    const product = products.data?.find((item) => item.id === productId);
    if (!product) return;
    const price = bookPrice(book.data?.entries, product.id, 1);
    setLines((current) => [
      ...current,
      {
        product_id: product.id,
        name: product.name,
        description: product.description,
        quantity: 1,
        unit_price: price?.unit_price ?? product.unit_price,
        discount_percent: price?.discount_percent ?? 0,
        tax_percent: product.tax_rate,
      },
    ]);
    setDirty(true);
    setPick("");
  }

  function addCustom() {
    setLines((current) => [
      ...current,
      { product_id: null, name: "Custom item", description: null, quantity: 1, unit_price: 0, discount_percent: 0, tax_percent: 0 },
    ]);
    setDirty(true);
  }

  async function saveLines() {
    setBusy(true);
    try {
      const result = await api.put<Quote>(
        `/quotes/${quoteId}/line-items`,
        lines.map((line) => ({
          product_id: line.product_id,
          name: line.name.trim() || "Item",
          description: line.description,
          quantity: Number(line.quantity) || 1,
          unit_price: Number(line.unit_price) || 0,
          discount_percent: Number(line.discount_percent) || 0,
          tax_percent: Number(line.tax_percent) || 0,
        }))
      );
      setDirty(false);
      quote.setData(result);
      toast("Line items saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: QuoteStatus) {
    if (dirty) await saveLines();
    setBusy(true);
    try {
      const result = await api.post<Quote & { stock_warnings?: string[] }>(`/quotes/${quoteId}/status`, { status, sync_opportunity_amount: true });
      quote.setData(result);
      if (result.stock_warnings?.length) toast(`Stock is short: ${result.stock_warnings.join("; ")}.`, "danger");
      toast(
        status === "accepted"
          ? q?.opportunity_id
            ? "Quote accepted. The opportunity amount now matches this quote."
            : "Quote accepted."
          : status === "sent"
            ? "Marked as sent."
            : `Quote ${status}.`
      );
    } catch (caught) {
      const needed = approvalFromError(caught);
      if (needed) setApproval(needed);
      else toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function createInvoice() {
    setBusy(true);
    try {
      const invoice = await api.post<{ id: string; display_number: string }>(`/invoices/from-quote/${quoteId}`, { due_in_days: 30 });
      toast(`Created ${invoice.display_number} as a draft.`);
      router.push(`/invoices/${invoice.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  async function applyBook(bookId: string) {
    if (dirty) await saveLines();
    setBusy(true);
    try {
      const result = await api.post<Quote & { repriced_lines: number }>(`/quotes/${quoteId}/price-book`, { price_book_id: bookId || null });
      setDirty(false);
      quote.setData(result);
      toast(bookId ? `Price book applied. ${result.repriced_lines} line${result.repriced_lines === 1 ? "" : "s"} re-priced.` : "Back to standard prices.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function createSalesOrder() {
    setBusy(true);
    try {
      const order = await api.post<{ id: string; display_number: string }>(`/sales-orders/from-quote/${quoteId}`);
      toast(`Created ${order.display_number}.`);
      router.push(`/sales-orders/${order.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  async function duplicate() {
    setBusy(true);
    try {
      const copy = await api.post<Quote>(`/quotes/${quoteId}/duplicate`);
      toast(`Created ${copy.display_number} as a draft copy.`);
      router.push(`/quotes/${copy.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/quotes/${quoteId}`);
      toast("Quote deleted.");
      router.replace("/quotes");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  async function saveDetails() {
    if (!details) return;
    setBusy(true);
    setDetailsError(null);
    try {
      const result = await api.patch<Quote>(`/quotes/${quoteId}`, detailsPayload(details));
      quote.setData(result);
      setEditOpen(false);
      toast("Quote details saved.");
    } catch (caught) {
      setDetailsError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (quote.error) return <Alert>{quote.error}</Alert>;
  if (!q) return <div className="ui-card"><LoadingRows rows={10} /></div>;

  return (
    <>
      <div className="no-print" style={{ marginBottom: 10 }}>
        <Link href="/quotes" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All quotes
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            {q.display_number} · <Badge tone={QUOTE_STATUS_TONE[q.status]}>{titleCase(q.status)}</Badge>
          </>
        }
        title={q.name}
        description={
          <>
            {q.account_name ?? "No account"}
            {q.opportunity_name ? (
              <>
                {" · "}
                <Link href={`/opportunities/${q.opportunity_id}`} style={{ color: "var(--primary)" }}>
                  {q.opportunity_name}
                </Link>
              </>
            ) : null}
            {q.owner_name ? ` · Owner ${q.owner_name}` : ""}
          </>
        }
        actions={
          <div className="ui-actions no-print">
            <PinButton type="quote" id={q.id} />
            <ShareToSlackButton recordType="quote" recordId={q.id} className="ui-btn" />
            <button type="button" className="ui-btn" onClick={() => window.print()}>
              <Printer size={15} /> Print / PDF
            </button>
            {q.status === "accepted" && has("sales_orders") && can("sales.orders") ? (
              <button type="button" className="ui-btn" onClick={() => void createSalesOrder()} disabled={busy}>
                <PackageCheck size={15} /> Create sales order
              </button>
            ) : null}
            {q.status === "accepted" && has("cpq") && can("sales.invoices") ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => void createInvoice()} disabled={busy}>
                <FileText size={15} /> Create invoice
              </button>
            ) : null}
            {canEdit ? (
              <button type="button" className="ui-btn" onClick={() => void duplicate()} disabled={busy}>
                <Copy size={15} /> Duplicate
              </button>
            ) : null}
            {canEdit && q.status === "draft" ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => void setStatus("sent")} disabled={busy || !lines.length}>
                <Send size={15} /> Mark as sent
              </button>
            ) : null}
            {can("sales.quotes_approve") && (q.status === "sent" || q.status === "draft" || q.status === "expired") ? (
              <>
                <button type="button" className="ui-btn ui-btn-danger" onClick={() => void setStatus("declined")} disabled={busy || !lines.length}>
                  <X size={15} /> Declined
                </button>
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => void setStatus("accepted")} disabled={busy || !lines.length}>
                  <Check size={15} /> Accepted
                </button>
              </>
            ) : null}
          </div>
        }
      />

      {q.status === "accepted" || q.status === "declined" ? (
        <div style={{ marginBottom: 16 }} className="no-print">
          <Alert tone={q.status === "accepted" ? "success" : "warning"}>
            This quote was {q.status} {dateTime(q.accepted_at ?? q.declined_at)} and is locked. Duplicate it to revise pricing.
          </Alert>
        </div>
      ) : null}

      <div className="ui-split">
        <Card
          title="Line items"
          className="quote-doc"
          flush
          footer={
            !locked ? (
              <>
                <select className="ui-select sm" style={{ width: 260, marginRight: "auto" }} value={pick} onChange={(e) => addProduct(e.target.value)} aria-label="Add a product">
                  <option value="">+ Add product from catalog…</option>
                  {(products.data ?? []).map((product) => (
                    <option key={product.id} value={product.id}>
                      {product.name} — {moneyExact(product.unit_price, product.currency)}
                    </option>
                  ))}
                </select>
                <button type="button" className="ui-btn ui-btn-sm" onClick={addCustom}>
                  <Plus size={14} /> Custom line
                </button>
                <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => void saveLines()} disabled={!dirty || busy}>
                  {dirty ? "Save changes" : "Saved"}
                </button>
              </>
            ) : null
          }
        >
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th style={{ minWidth: 220 }}>Item</th>
                  <th className="ui-num" style={{ width: 90 }}>
                    Qty
                  </th>
                  <th className="ui-num" style={{ width: 130 }}>
                    Unit price
                  </th>
                  <th className="ui-num" style={{ width: 90 }}>
                    Disc %
                  </th>
                  <th className="ui-num" style={{ width: 80 }}>
                    Tax %
                  </th>
                  <th className="ui-num" style={{ width: 120 }}>
                    Amount
                  </th>
                  {!locked ? <th style={{ width: 40 }} aria-label="Remove" /> : null}
                </tr>
              </thead>
              <tbody>
                {lines.map((line, index) => {
                  const gross = (Number(line.quantity) || 0) * (Number(line.unit_price) || 0);
                  const net = gross - (gross * (Number(line.discount_percent) || 0)) / 100;
                  return (
                    <tr key={line.id ?? `new-${index}`}>
                      <td>
                        {locked ? (
                          <>
                            <div className="cell-title">{line.name}</div>
                            {line.description ? <div className="cell-sub">{line.description}</div> : null}
                          </>
                        ) : (
                          <div style={{ display: "grid", gap: 4 }}>
                            <input className="ui-input sm" value={line.name} onChange={(e) => updateLine(index, { name: e.target.value })} aria-label="Item name" />
                            <input
                              className="ui-input sm"
                              value={line.description ?? ""}
                              placeholder="Description (optional)"
                              onChange={(e) => updateLine(index, { description: e.target.value || null })}
                              aria-label="Item description"
                              style={{ fontSize: 12 }}
                            />
                          </div>
                        )}
                      </td>
                      {(["quantity", "unit_price", "discount_percent", "tax_percent"] as const).map((key) => (
                        <td key={key} className="ui-num">
                          {locked ? (
                            key === "unit_price" ? (
                              moneyExact(line[key], q.currency)
                            ) : (
                              `${Number(line[key])}${key === "quantity" ? "" : "%"}`
                            )
                          ) : (
                            <input
                              className="ui-input sm"
                              type="number"
                              min={0}
                              max={key.endsWith("percent") ? 100 : undefined}
                              step={key === "quantity" ? "1" : "0.01"}
                              value={line[key]}
                              onChange={(e) => updateLine(index, { [key]: e.target.value === "" ? 0 : Number(e.target.value) })}
                              style={{ textAlign: "right" }}
                              aria-label={key.replace("_", " ")}
                            />
                          )}
                        </td>
                      ))}
                      <td className="ui-num ui-strong">{moneyExact(net, q.currency)}</td>
                      {!locked ? (
                        <td>
                          <button
                            type="button"
                            className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                            aria-label={`Remove ${line.name}`}
                            onClick={() => {
                              setLines((current) => current.filter((_, i) => i !== index));
                              setDirty(true);
                            }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
                {!lines.length ? (
                  <tr>
                    <td colSpan={7} className="ui-muted" style={{ textAlign: "center", padding: 28 }}>
                      No line items yet. Add products from your catalog or a custom line.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "16px 18px" }}>
            <div className="quote-totals">
              <span className="ui-muted">Subtotal</span>
              <span className="ui-num">{moneyExact(totals.subtotal, q.currency)}</span>
              <span className="ui-muted">Discounts</span>
              <span className="ui-num">−{moneyExact(totals.discount, q.currency)}</span>
              <span className="ui-muted">Tax</span>
              <span className="ui-num">{moneyExact(totals.tax, q.currency)}</span>
              <span className="grand">Total</span>
              <span className="grand ui-num">{moneyExact(totals.total, q.currency)}</span>
            </div>
          </div>
        </Card>

        <div className="ui-stack">
          <Card
            title="Details"
            actions={
              !locked ? (
                <button
                  type="button"
                  className="ui-btn ui-btn-ghost ui-btn-sm no-print"
                  onClick={() => {
                    setDetails({
                      name: q.name,
                      opportunity_id: q.opportunity_id ?? "",
                      account_id: q.account_id ?? "",
                      contact_id: q.contact_id ?? "",
                      valid_until: q.valid_until ?? "",
                      terms: q.terms ?? "",
                      notes: q.notes ?? "",
                    });
                    setDetailsError(null);
                    setEditOpen(true);
                  }}
                >
                  <Pencil size={14} /> Edit
                </button>
              ) : null
            }
          >
            <dl className="ui-dl">
              <dt>Account</dt>
              <dd>{q.account_id ? <Link href={`/accounts/${q.account_id}`}>{q.account_name}</Link> : "—"}</dd>
              <dt>Prepared for</dt>
              <dd>{q.contact_id ? <Link href={`/contacts/${q.contact_id}`}>{q.contact_name}</Link> : "—"}</dd>
              <dt>Valid until</dt>
              <dd>{date(q.valid_until)}</dd>
              {usesBooks ? (
                <>
                  <dt>Price book</dt>
                  <dd>
                    {!locked ? (
                      <select className="ui-select sm no-print" value={q.price_book_id ?? ""} onChange={(e) => void applyBook(e.target.value)} disabled={busy} aria-label="Price book">
                        <option value="">Standard prices</option>
                        {(books.data?.items ?? []).map((b) => (
                          <option key={b.id} value={b.id}>
                            {b.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      books.data?.items.find((b) => b.id === q.price_book_id)?.name ?? (q.price_book_id ? "Price book" : "Standard prices")
                    )}
                  </dd>
                </>
              ) : null}
              <dt>Created</dt>
              <dd>{dateTime(q.created_at)}</dd>
              <dt>Sent</dt>
              <dd>{q.sent_at ? dateTime(q.sent_at) : "Not yet"}</dd>
            </dl>
          </Card>
          <Card title="Terms">
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>{q.terms || "No terms."}</p>
            {q.notes ? (
              <p className="no-print" style={{ margin: "12px 0 0", fontSize: 12.5, color: "var(--muted)", whiteSpace: "pre-wrap" }}>
                <strong>Internal note:</strong> {q.notes}
              </p>
            ) : null}
          </Card>
          <div className="int-slot no-print">
            <RecordSignatures recordType="quote" recordId={q.id} quoteId={q.id} onChanged={() => void quote.reload()} />
          </div>
          <div className="int-slot no-print">
            <OrderErp recordType="quote" recordId={q.id} />
          </div>
          <div className="int-slot no-print">
            <RecordCloudFiles recordType="quote" recordId={q.id} />
          </div>
          {canEdit && q.status !== "accepted" ? (
            <button type="button" className="ui-btn ui-btn-danger no-print" onClick={() => setDeleting(true)}>
              <Trash2 size={15} /> Delete quote
            </button>
          ) : null}
        </div>
      </div>

      <Modal
        open={editOpen}
        size="lg"
        title="Edit quote details"
        onClose={() => setEditOpen(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setEditOpen(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void saveDetails()} disabled={busy}>
              Save details
            </button>
          </>
        }
      >
        {detailsError ? (
          <div style={{ marginBottom: 12 }}>
            <Alert>{detailsError}</Alert>
          </div>
        ) : null}
        {details ? <QuoteDetailsFields value={details} onChange={setDetails} /> : null}
      </Modal>

      <ApprovalModal needed={approval} objectType="quote" recordId={quoteId} onClose={() => setApproval(null)} onSubmitted={() => void quote.reload()} />

      <ConfirmDialog
        open={deleting}
        title={`Delete ${q.display_number}?`}
        message="The quote and its line items are removed. The opportunity is not changed."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}
