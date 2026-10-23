"use client";

import { History, PackageCheck, PackageOpen, Settings2 } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs, type Tone } from "@/components/ui";
import { compactMoney, dateTime, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface StockRow {
  id: string;
  name: string;
  sku: string | null;
  family: string | null;
  unit_price: number;
  unit_cost: number | null;
  track_inventory: boolean;
  stock_on_hand: number;
  reorder_level: number;
  status: "ok" | "low" | "out";
  stock_value: number;
  is_active: boolean;
}

interface Inventory {
  items: StockRow[];
  summary: { tracked: number; low: number; out: number; stock_value: number };
  can_manage: boolean;
}

interface Movement {
  id: string;
  product_id: string;
  product_name: string;
  kind: "receive" | "adjust" | "return" | "sale";
  quantity: number;
  balance_after: number;
  reference: string | null;
  note: string | null;
  by: string | null;
  created_at: string;
}

const STATUS: Record<StockRow["status"], { label: string; tone: Tone }> = {
  ok: { label: "In stock", tone: "success" },
  low: { label: "Low", tone: "warning" },
  out: { label: "Out of stock", tone: "danger" },
};

const KIND_LABEL: Record<Movement["kind"], string> = { receive: "Received", adjust: "Adjusted", return: "Returned", sale: "Sold" };

export default function InventoryPage() {
  return (
    <FeaturePage feature="inventory">
      <InventoryView />
    </FeaturePage>
  );
}

function InventoryView() {
  const inventory = useApi<Inventory>("/inventory");
  const [view, setView] = useState<"stock" | "movements">("stock");
  const [filter, setFilter] = useState<"all" | "attention">("all");
  const movements = useApi<Movement[]>(view === "movements" ? "/inventory/movements" : null);
  const [moving, setMoving] = useState<StockRow | null>(null);
  const [configuring, setConfiguring] = useState<StockRow | null>(null);
  const s = inventory.data?.summary;
  const manage = inventory.data?.can_manage ?? false;
  const rows = (inventory.data?.items ?? []).filter((row) => filter === "all" || (row.track_inventory && row.status !== "ok"));

  return (
    <>
      <PageHeader
        title="Inventory"
        description="Stock on hand for every product you track. Accepted quotes take stock out automatically; you get a notification when something drops to its reorder level."
      />
      {inventory.error ? <Alert>{inventory.error}</Alert> : null}
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Tracked products" value={s?.tracked ?? "—"} />
        <Kpi label="Low stock" value={s?.low ?? "—"} />
        <Kpi label="Out of stock" value={s?.out ?? "—"} />
        <Kpi label="Stock value" value={s ? compactMoney(s.stock_value) : "—"} note="At cost where set" />
      </div>
      <Card
        flush
        title={
          <Tabs
            label="View"
            value={view}
            onChange={setView}
            items={[
              { value: "stock", label: "Stock levels" },
              { value: "movements", label: "Movements" },
            ]}
          />
        }
        actions={
          view === "stock" ? (
            <Tabs
              label="Filter"
              value={filter}
              onChange={setFilter}
              items={[
                { value: "all", label: "All" },
                { value: "attention", label: "Needs attention", count: s ? s.low + s.out : undefined },
              ]}
            />
          ) : null
        }
      >
        {view === "stock" ? (
          inventory.loading && !inventory.data ? (
            <LoadingRows />
          ) : rows.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Status</th>
                    <th className="ui-num">On hand</th>
                    <th className="ui-num">Reorder at</th>
                    <th className="ui-num">Unit cost</th>
                    <th className="ui-num">Stock value</th>
                    {manage ? <th aria-label="Actions" /> : null}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td className="cell-title">
                        {row.name}
                        <div className="ui-muted ui-small">{[row.sku, row.family].filter(Boolean).join(" · ") || "—"}</div>
                      </td>
                      <td>{row.track_inventory ? <Badge tone={STATUS[row.status].tone}>{STATUS[row.status].label}</Badge> : <span className="ui-muted ui-small">Not tracked</span>}</td>
                      <td className="ui-num ui-strong">{row.track_inventory ? row.stock_on_hand.toLocaleString() : "—"}</td>
                      <td className="ui-num">{row.track_inventory ? row.reorder_level.toLocaleString() : "—"}</td>
                      <td className="ui-num">{row.unit_cost !== null ? moneyExact(row.unit_cost) : "—"}</td>
                      <td className="ui-num">{row.track_inventory ? moneyExact(row.stock_value) : "—"}</td>
                      {manage ? (
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          {row.track_inventory ? (
                            <button type="button" className="ui-btn ui-btn-sm" onClick={() => setMoving(row)}>
                              <PackageCheck size={14} /> Update stock
                            </button>
                          ) : null}{" "}
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Stock settings for ${row.name}`} onClick={() => setConfiguring(row)}>
                            <Settings2 size={14} />
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState icon={<PackageOpen size={20} />} title={filter === "all" ? "No products yet" : "Nothing needs attention"} message={filter === "all" ? "Add products to your catalog, then turn on stock tracking." : "Every tracked product is above its reorder level."} />
          )
        ) : movements.loading && !movements.data ? (
          <LoadingRows />
        ) : movements.data?.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Movement</th>
                  <th className="ui-num">Change</th>
                  <th className="ui-num">Balance</th>
                  <th>Reference</th>
                  <th>By</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {movements.data.map((m) => (
                  <tr key={m.id}>
                    <td className="cell-title">{m.product_name}</td>
                    <td>
                      {KIND_LABEL[m.kind]}
                      {m.note ? <div className="ui-muted ui-small">{m.note}</div> : null}
                    </td>
                    <td className="ui-num" style={{ color: m.quantity < 0 ? "var(--danger)" : "var(--success)" }}>
                      {m.quantity > 0 ? "+" : ""}
                      {m.quantity.toLocaleString()}
                    </td>
                    <td className="ui-num">{m.balance_after.toLocaleString()}</td>
                    <td>{m.reference ?? "—"}</td>
                    <td>{m.by ?? "System"}</td>
                    <td className="ui-muted">{dateTime(m.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<History size={20} />} title="No stock movements yet" message="Receipts, adjustments and sales from accepted quotes are listed here." />
        )}
      </Card>
      <MovementModal row={moving} onClose={() => setMoving(null)} onSaved={() => void inventory.reload()} />
      <SettingsModal row={configuring} onClose={() => setConfiguring(null)} onSaved={() => void inventory.reload()} />
    </>
  );
}

function MovementModal({ row, onClose, onSaved }: { row: StockRow | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState<"receive" | "adjust" | "return" | "count">("receive");
  const [quantity, setQuantity] = useState("");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!row) return;
    setBusy(true);
    try {
      const result = await api.post<StockRow>(`/inventory/products/${row.id}/movements`, {
        kind,
        quantity: Number(quantity),
        reference: reference || null,
        note: note || null,
      });
      toast(`${row.name}: ${result.stock_on_hand.toLocaleString()} on hand.`);
      setQuantity("");
      setReference("");
      setNote("");
      onSaved();
      onClose();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(row)}
      title={row ? `Update stock · ${row.name}` : "Update stock"}
      description={row ? `${row.stock_on_hand.toLocaleString()} on hand now.` : undefined}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || quantity === ""} onClick={() => void save()}>
            Save
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <Tabs
          label="Kind"
          value={kind}
          onChange={setKind}
          items={[
            { value: "receive", label: "Receive" },
            { value: "return", label: "Return" },
            { value: "adjust", label: "Adjust ±" },
            { value: "count", label: "Stock count" },
          ]}
        />
        <div className="ui-form-grid">
          <Field
            label={kind === "count" ? "Units counted" : kind === "adjust" ? "Change (use − to remove)" : "Units"}
            hint={kind === "count" ? "Sets stock on hand to exactly this number." : undefined}
          >
            <input className="ui-input" type="number" step="1" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </Field>
          <Field label="Reference">
            <input className="ui-input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="PO-1042" />
          </Field>
        </div>
        <Field label="Note">
          <input className="ui-input" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function SettingsModal({ row, onClose, onSaved }: { row: StockRow | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [track, setTrack] = useState(false);
  const [reorder, setReorder] = useState("0");
  const [cost, setCost] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  if (row && loadedFor !== row.id) {
    setLoadedFor(row.id);
    setTrack(row.track_inventory);
    setReorder(String(row.reorder_level));
    setCost(row.unit_cost !== null ? String(row.unit_cost) : "");
  }

  async function save() {
    if (!row) return;
    setBusy(true);
    try {
      await api.patch(`/inventory/products/${row.id}`, { track_inventory: track, reorder_level: Number(reorder) || 0, unit_cost: cost === "" ? null : Number(cost) });
      toast("Stock settings saved.");
      onSaved();
      setLoadedFor(null);
      onClose();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(row)}
      title={row ? `Stock settings · ${row.name}` : "Stock settings"}
      onClose={() => {
        setLoadedFor(null);
        onClose();
      }}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
            Save
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <label className="ui-check">
          <input type="checkbox" checked={track} onChange={(e) => setTrack(e.target.checked)} /> Track stock for this product
        </label>
        <div className="ui-form-grid">
          <Field label="Reorder level" hint="You're notified when stock reaches this.">
            <input className="ui-input" type="number" min={0} value={reorder} disabled={!track} onChange={(e) => setReorder(e.target.value)} />
          </Field>
          <Field label="Unit cost" hint="Used for stock value. Leave empty to use the price.">
            <input className="ui-input" type="number" min={0} step="0.01" value={cost} onChange={(e) => setCost(e.target.value)} />
          </Field>
        </div>
      </div>
    </Modal>
  );
}
