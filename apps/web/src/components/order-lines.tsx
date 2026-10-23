"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { moneyExact } from "@/lib/format";
import { bookPrice, lineTotals, type OrderLine, type PriceBookEntry } from "@/lib/workspace";

export interface ProductOption {
  id: string;
  name: string;
  sku: string | null;
  description?: string | null;
  unit_price: number;
  unit_cost?: number | null;
  tax_rate?: number;
}

/**
 * Editable line items for sales and purchase orders. Sales lines are priced
 * from the order's price book when there is one; purchase lines start at the
 * product's last cost.
 */
export function OrderLines({
  lines,
  onChange,
  locked,
  currency,
  products,
  priceEntries,
  mode = "sale",
  shipping = 0,
  showReceived = false,
}: {
  lines: OrderLine[];
  onChange?: (lines: OrderLine[]) => void;
  locked: boolean;
  currency: string;
  products: ProductOption[];
  priceEntries?: PriceBookEntry[];
  mode?: "sale" | "purchase";
  shipping?: number;
  showReceived?: boolean;
}) {
  const [pick, setPick] = useState("");
  const totals = lineTotals(lines, shipping);

  function update(index: number, changes: Partial<OrderLine>) {
    const next = lines.map((line, i) => {
      if (i !== index) return line;
      const merged = { ...line, ...changes };
      // A new quantity can move the line into another volume tier.
      if (mode === "sale" && changes.quantity !== undefined && merged.product_id) {
        const price = bookPrice(priceEntries, merged.product_id, Number(merged.quantity) || 0);
        if (price && merged.unit_price === price.unit_price) merged.discount_percent = price.discount_percent;
      }
      return merged;
    });
    onChange?.(next);
  }

  function addProduct(productId: string) {
    const product = products.find((p) => p.id === productId);
    if (!product) return;
    const price = mode === "sale" ? bookPrice(priceEntries, product.id, 1) : null;
    onChange?.([
      ...lines,
      {
        product_id: product.id,
        name: product.name,
        description: product.description ?? null,
        sku: product.sku,
        quantity: 1,
        unit_price: price?.unit_price ?? (mode === "purchase" ? product.unit_cost ?? product.unit_price : product.unit_price),
        discount_percent: price?.discount_percent ?? 0,
        tax_percent: mode === "sale" ? product.tax_rate ?? 0 : 0,
        ...(showReceived ? { received_quantity: 0 } : {}),
      },
    ]);
    setPick("");
  }

  function addCustom() {
    onChange?.([...lines, { product_id: null, name: mode === "purchase" ? "Item" : "Custom item", description: null, quantity: 1, unit_price: 0, discount_percent: 0, tax_percent: 0 }]);
  }

  return (
    <>
      <div className="ui-table-wrap">
        <table className="ui-table order-lines">
          <thead>
            <tr>
              <th style={{ minWidth: 220 }}>Item</th>
              <th className="ui-num" style={{ width: 90 }}>
                Qty
              </th>
              {showReceived ? (
                <th className="ui-num" style={{ width: 96 }}>
                  Received
                </th>
              ) : null}
              <th className="ui-num" style={{ width: 130 }}>
                {mode === "purchase" ? "Unit cost" : "Unit price"}
              </th>
              <th className="ui-num" style={{ width: 86 }}>
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
              const fromBook = mode === "sale" && line.product_id && priceEntries?.some((e) => e.product_id === line.product_id);
              return (
                <tr key={index}>
                  <td>
                    {locked ? (
                      <>
                        <div className="cell-title">{line.name}</div>
                        <div className="cell-sub">{[line.sku, line.description].filter(Boolean).join(" · ")}</div>
                      </>
                    ) : (
                      <div style={{ display: "grid", gap: 4 }}>
                        <input className="ui-input sm" value={line.name} onChange={(e) => update(index, { name: e.target.value })} aria-label="Item name" />
                        <input
                          className="ui-input sm"
                          value={line.description ?? ""}
                          placeholder="Description (optional)"
                          onChange={(e) => update(index, { description: e.target.value || null })}
                          aria-label="Item description"
                          style={{ fontSize: 12 }}
                        />
                        {fromBook ? <span className="ui-muted" style={{ fontSize: 11.5 }}>Priced from the price book</span> : null}
                      </div>
                    )}
                  </td>
                  <td className="ui-num">
                    {locked ? (
                      Number(line.quantity)
                    ) : (
                      <input className="ui-input sm" type="number" min={0} step="1" value={line.quantity} onChange={(e) => update(index, { quantity: e.target.value === "" ? 0 : Number(e.target.value) })} style={{ textAlign: "right" }} aria-label="Quantity" />
                    )}
                  </td>
                  {showReceived ? (
                    <td className="ui-num">
                      <span className={Number(line.received_quantity) >= Number(line.quantity) ? "ui-strong" : "ui-muted"}>{Number(line.received_quantity ?? 0)}</span>
                    </td>
                  ) : null}
                  {(["unit_price", "discount_percent", "tax_percent"] as const).map((key) => (
                    <td key={key} className="ui-num">
                      {locked ? (
                        key === "unit_price" ? moneyExact(line[key], currency) : `${Number(line[key])}%`
                      ) : (
                        <input
                          className="ui-input sm"
                          type="number"
                          min={0}
                          max={key === "unit_price" ? undefined : 100}
                          step="0.01"
                          value={line[key]}
                          onChange={(e) => update(index, { [key]: e.target.value === "" ? 0 : Number(e.target.value) })}
                          style={{ textAlign: "right" }}
                          aria-label={key === "unit_price" ? (mode === "purchase" ? "Unit cost" : "Unit price") : key === "discount_percent" ? "Discount percent" : "Tax percent"}
                        />
                      )}
                    </td>
                  ))}
                  <td className="ui-num ui-strong">{moneyExact(net, currency)}</td>
                  {!locked ? (
                    <td>
                      <button
                        type="button"
                        className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                        aria-label={`Remove ${line.name}`}
                        onClick={() => onChange?.(lines.filter((_, i) => i !== index))}
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
                <td colSpan={8} className="ui-muted" style={{ textAlign: "center", padding: 28 }}>
                  No line items yet. Add products from your catalog or a custom line.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {!locked ? (
        <div className="order-lines-add no-print">
          <select className="ui-select sm" value={pick} onChange={(e) => addProduct(e.target.value)} aria-label="Add a product">
            <option value="">+ Add product from catalog…</option>
            {products.map((product) => {
              const price = mode === "sale" ? bookPrice(priceEntries, product.id, 1)?.unit_price ?? product.unit_price : product.unit_cost ?? product.unit_price;
              return (
                <option key={product.id} value={product.id}>
                  {product.name}
                  {product.sku ? ` (${product.sku})` : ""} — {moneyExact(price, currency)}
                </option>
              );
            })}
          </select>
          <button type="button" className="ui-btn ui-btn-sm" onClick={addCustom}>
            <Plus size={14} /> Custom line
          </button>
        </div>
      ) : null}
      <div style={{ padding: "16px 18px" }}>
        <div className="quote-totals">
          <span className="ui-muted">Subtotal</span>
          <span className="ui-num">{moneyExact(totals.subtotal, currency)}</span>
          <span className="ui-muted">Discounts</span>
          <span className="ui-num">−{moneyExact(totals.discount, currency)}</span>
          <span className="ui-muted">Tax</span>
          <span className="ui-num">{moneyExact(totals.tax, currency)}</span>
          {shipping ? (
            <>
              <span className="ui-muted">Shipping</span>
              <span className="ui-num">{moneyExact(totals.shipping, currency)}</span>
            </>
          ) : null}
          <span className="grand">Total</span>
          <span className="grand ui-num">{moneyExact(totals.total, currency)}</span>
        </div>
      </div>
    </>
  );
}

/** Line payload for the API (numbers cleaned up, received quantities left to the server). */
export function linesPayload(lines: OrderLine[]) {
  return lines.map((line) => ({
    product_id: line.product_id,
    name: line.name.trim() || "Item",
    description: line.description,
    sku: line.sku ?? null,
    quantity: Number(line.quantity) || 1,
    unit_price: Number(line.unit_price) || 0,
    discount_percent: Number(line.discount_percent) || 0,
    tax_percent: Number(line.tax_percent) || 0,
  }));
}
