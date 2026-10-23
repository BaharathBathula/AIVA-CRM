"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { PinButton } from "@/components/pins";
import { PriceBookModal } from "@/components/price-book-modal";
import { useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, LoadingRows, PageHeader } from "@/components/ui";
import { date, moneyExact } from "@/lib/format";
import type { Product } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";
import type { PriceBook, PriceBookEntry, PriceTier } from "@/lib/workspace";

export default function PriceBookPage() {
  return (
    <FeaturePage feature="price_books">
      <PriceBookView />
    </FeaturePage>
  );
}

type Row = { product_id: string; product_name: string; sku: string | null; standard_price: number; list_price: number; tiers: PriceTier[] };

function PriceBookView() {
  const { bookId } = useParams<{ bookId: string }>();
  const router = useRouter();
  const toast = useToast();
  const book = useApi<PriceBook>(`/price-books/${bookId}`);
  const products = useApi<Product[]>("/products?active=true");
  const [rows, setRows] = useState<Row[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState("");
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const b = book.data;
  const manage = Boolean(b?.can_manage);
  const tiered = b?.pricing_model === "differential";

  useEffect(() => {
    if (!b || dirty) return;
    setRows((b.entries ?? []).map((e: PriceBookEntry) => ({ ...e, tiers: e.tiers.map((t) => ({ ...t })) })));
  }, [b, dirty]);

  function change(index: number, next: Partial<Row>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...next } : row)));
    setDirty(true);
  }

  function add(productId: string) {
    const product = products.data?.find((p) => p.id === productId);
    if (!product || rows.some((r) => r.product_id === productId)) return;
    setRows([...rows, { product_id: product.id, product_name: product.name, sku: product.sku, standard_price: product.unit_price, list_price: product.unit_price, tiers: [] }]);
    setDirty(true);
    setPick("");
  }

  async function save() {
    setBusy(true);
    try {
      const saved = await api.put<PriceBook>(
        `/price-books/${bookId}/entries`,
        rows.map((r) => ({
          product_id: r.product_id,
          list_price: Number(r.list_price) || 0,
          tiers: tiered ? r.tiers.map((t) => ({ from_qty: Number(t.from_qty) || 1, to_qty: t.to_qty === null || (t.to_qty as unknown) === "" ? null : Number(t.to_qty), discount_percent: Number(t.discount_percent) || 0 })) : [],
        }))
      );
      book.setData({ ...saved, can_manage: b?.can_manage });
      setDirty(false);
      toast("Prices saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/price-books/${bookId}`);
      toast("Price book deleted.");
      router.replace("/price-books");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  if (book.error) return <Alert>{book.error}</Alert>;
  if (!b) return <div className="ui-card"><LoadingRows rows={8} /></div>;
  const available = (products.data ?? []).filter((p) => !rows.some((r) => r.product_id === p.id));

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/price-books" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> All price books
        </Link>
      </div>
      <PageHeader
        eyebrow={<Badge tone={b.is_current ? "success" : "neutral"}>{b.is_current ? "In use" : b.is_active ? "Not in date range" : "Inactive"}</Badge>}
        title={b.name}
        description={`${tiered ? "List price + volume tiers" : "List price"} · ${b.currency}${b.valid_from || b.valid_until ? ` · ${date(b.valid_from) || "Any time"} – ${date(b.valid_until) || "No end"}` : ""}`}
        actions={
          <>
            <PinButton type="price_book" id={b.id} />
            {manage ? (
              <>
                <button type="button" className="ui-btn" onClick={() => setEditing(true)}>
                  <Pencil size={15} /> Edit
                </button>
                <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDeleting(true)}>
                  <Trash2 size={15} /> Delete
                </button>
              </>
            ) : null}
          </>
        }
      />
      {b.description ? <p className="ui-muted" style={{ marginTop: -6 }}>{b.description}</p> : null}
      <Card
        title="Products and prices"
        flush
        footer={
          manage ? (
            <>
              <select className="ui-select sm" style={{ width: 280, marginRight: "auto" }} value={pick} onChange={(e) => add(e.target.value)} aria-label="Add a product">
                <option value="">+ Add product…</option>
                {available.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} — {moneyExact(p.unit_price, p.currency)}
                  </option>
                ))}
              </select>
              <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => void save()} disabled={!dirty || busy}>
                {dirty ? "Save prices" : "Saved"}
              </button>
            </>
          ) : null
        }
      >
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Product</th>
                <th className="ui-num">Standard price</th>
                <th className="ui-num" style={{ width: 150 }}>
                  List price
                </th>
                {tiered ? <th>Volume tiers (quantity → discount)</th> : null}
                {manage ? <th style={{ width: 40 }} aria-label="Remove" /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.product_id}>
                  <td>
                    <div className="cell-title">{row.product_name}</div>
                    {row.sku ? <div className="cell-sub">{row.sku}</div> : null}
                  </td>
                  <td className="ui-num ui-muted">{moneyExact(row.standard_price, b.currency)}</td>
                  <td className="ui-num">
                    {manage ? (
                      <input className="ui-input sm" type="number" min={0} step="0.01" value={row.list_price} onChange={(e) => change(index, { list_price: e.target.value === "" ? 0 : Number(e.target.value) })} style={{ textAlign: "right" }} aria-label={`List price for ${row.product_name}`} />
                    ) : (
                      moneyExact(row.list_price, b.currency)
                    )}
                  </td>
                  {tiered ? (
                    <td>
                      <div className="tier-list">
                        {row.tiers.map((tier, t) => (
                          <span key={t} className="tier">
                            {manage ? (
                              <>
                                <input className="ui-input sm" type="number" min={1} value={tier.from_qty} onChange={(e) => change(index, { tiers: row.tiers.map((x, i) => (i === t ? { ...x, from_qty: Number(e.target.value) } : x)) })} aria-label="From quantity" />
                                –
                                <input className="ui-input sm" type="number" min={1} value={tier.to_qty ?? ""} placeholder="∞" onChange={(e) => change(index, { tiers: row.tiers.map((x, i) => (i === t ? { ...x, to_qty: e.target.value === "" ? null : Number(e.target.value) } : x)) })} aria-label="To quantity" />
                                <input className="ui-input sm" type="number" min={0} max={100} step="0.5" value={tier.discount_percent} onChange={(e) => change(index, { tiers: row.tiers.map((x, i) => (i === t ? { ...x, discount_percent: Number(e.target.value) } : x)) })} aria-label="Discount percent" />
                                %
                                <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove tier" onClick={() => change(index, { tiers: row.tiers.filter((_, i) => i !== t) })}>
                                  <X size={13} />
                                </button>
                              </>
                            ) : (
                              `${tier.from_qty}–${tier.to_qty ?? "∞"}: ${tier.discount_percent}% off`
                            )}
                          </span>
                        ))}
                        {manage ? (
                          <button
                            type="button"
                            className="ui-btn ui-btn-ghost ui-btn-sm"
                            onClick={() => {
                              const last = row.tiers[row.tiers.length - 1];
                              const start = last ? (last.to_qty ?? last.from_qty) + 1 : 1;
                              change(index, { tiers: [...row.tiers.map((x, i) => (i === row.tiers.length - 1 && x.to_qty === null ? { ...x, to_qty: start - 1 } : x)), { from_qty: start, to_qty: null, discount_percent: 0 }] });
                            }}
                          >
                            <Plus size={13} /> Tier
                          </button>
                        ) : null}
                        {!row.tiers.length && !manage ? <span className="ui-muted">No volume discount</span> : null}
                      </div>
                    </td>
                  ) : null}
                  {manage ? (
                    <td>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Remove ${row.product_name}`} onClick={() => (setRows(rows.filter((_, i) => i !== index)), setDirty(true))}>
                        <Trash2 size={14} />
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
              {!rows.length ? (
                <tr>
                  <td colSpan={5} className="ui-muted" style={{ textAlign: "center", padding: 28 }}>
                    Add the products this price book covers. Products that aren't here keep their standard price.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Card>
      <PriceBookModal open={editing} book={b} onClose={() => setEditing(false)} onSaved={(saved) => (book.setData({ ...saved, can_manage: b.can_manage }), setEditing(false), setDirty(false), toast("Price book saved."))} />
      <ConfirmDialog open={deleting} title={`Delete ${b.name}?`} message="Quotes and orders keep the prices they already have." busy={busy} onConfirm={() => void remove()} onClose={() => setDeleting(false)} />
    </>
  );
}
