"use client";

import { AlertTriangle, Boxes, ExternalLink, PackageCheck, RefreshCw, Send } from "lucide-react";
import { useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { Badge, Card, type Tone } from "@/components/ui";
import { moneyExact, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

// Shapes from apps/api/app/api/routes/int_erp.py.

export type ErpStatus = {
  enabled: boolean;
  connected: boolean;
  status: string;
  account_id: string | null;
  sandbox: boolean;
  home_url: string | null;
  orders_from: ("sales_order" | "quote" | "deal")[];
  order_mode: "auto" | "manual";
  items_sync: "off" | "update" | "create";
  stock_sync: boolean;
  can_manage: boolean;
};

export type ErpLink = {
  id: string;
  record_type: string;
  record_id: string;
  external_id: string | null;
  external_url: string | null;
  status: "linked" | "pending" | "error" | "removed";
  number: string | null;
  ns_status: string | null;
  status_name: string | null;
  total: string | null;
  currency: string | null;
  name: string | null;
  sku: string | null;
  type: string | null;
  price: string | null;
  on_hand: string | null;
  available: string | null;
  locations: Record<string, string>;
  inventory: boolean;
  inactive: boolean;
  adopted_from: string | null;
  last_synced_at: string | null;
  checked_at: string | null;
  last_error: string | null;
  retry_at: string | null;
};

type OrderData = { erp: ErpStatus; link: ErpLink | null; source_on: boolean; blocked_reason: string | null; can_push: boolean };
type ProductData = { erp: ErpStatus; link: ErpLink | null; can_refresh: boolean };

export type ErpRecordType = "sales_order" | "quote" | "opportunity";

const ORDER_TONE: Record<string, Tone> = { A: "warning", B: "info", C: "neutral", D: "info", E: "info", F: "primary", G: "success", H: "neutral" };
const WORD: Record<ErpRecordType, string> = { sales_order: "sales order", quote: "quote", opportunity: "deal" };
const SOURCE: Record<ErpRecordType, ErpStatus["orders_from"][number]> = { sales_order: "sales_order", quote: "quote", opportunity: "deal" };
const ITEM_TYPE: Record<string, string> = {
  InvtPart: "Inventory item", Assembly: "Assembly", Kit: "Kit", NonInvtPart: "Non-inventory item", Service: "Service", OthCharge: "Other charge",
  DwnLdItem: "Download",
};

function amount(value: string | null | undefined, currency: string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  return moneyExact(Number(value), currency || "USD");
}

function quantity(value: string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 2 }) : value;
}

/**
 * The NetSuite sales order for an AIVA sales order, accepted quote or won deal:
 * its NetSuite number and status, errors with their retry time, and "Send to
 * NetSuite" / "Check status". Renders nothing unless NetSuite is on and
 * connected, and (before anything was sent) unless that kind of record is set to
 * become a sales order. ``onChanged`` reloads the record after NetSuite's status
 * changed it (fulfilled or cancelled).
 *
 *   <OrderErp recordType="sales_order" recordId={order.id} onChanged={() => void order.reload()} />
 *   <OrderErp recordType="quote" recordId={quote.id} />
 *   <OrderErp recordType="opportunity" recordId={deal.id} />
 */
export function OrderErp({ recordType, recordId, onChanged }: { recordType: ErpRecordType; recordId: string; onChanged?: () => void }) {
  const { has, can } = useSession();
  const toast = useToast();
  const enabled = has("erp_sync");
  const data = useApi<OrderData>(enabled ? `/erp/orders/${recordType}/${recordId}` : null);
  const [busy, setBusy] = useState<"push" | "refresh" | null>(null);
  if (!enabled || !data.data?.erp.connected) return null;
  const { erp, link, source_on: sourceOn, blocked_reason: blocked, can_push: canPush } = data.data;
  if (!link && !sourceOn) return null;
  const sent = link && link.status === "linked" && link.external_id;
  const editable = !link?.ns_status || link.ns_status === "A" || link.ns_status === "B";

  async function act(action: "push" | "refresh") {
    setBusy(action);
    try {
      data.setData(await api.post<OrderData>(`/erp/orders/${recordType}/${recordId}/${action}`));
      toast(action === "push" ? (sent ? "Updated in NetSuite." : "Sent to NetSuite.") : "Status checked.");
      onChanged?.();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      void data.reload();
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="NetSuite"
      icon={<PackageCheck size={16} />}
      actions={
        link?.external_url ? (
          <a className="ui-btn ui-btn-ghost ui-btn-sm" href={link.external_url} target="_blank" rel="noopener noreferrer">
            Open <ExternalLink size={13} />
          </a>
        ) : null
      }
    >
      <div className="ui-stack" style={{ gap: 12 }}>
        {sent ? (
          <div className="ierp-status">
            <div>
              <span className="ui-small ui-muted">Sales order</span>
              <strong>{link.number ?? link.external_id}</strong>
            </div>
            {link.status_name ? <Badge tone={ORDER_TONE[link.ns_status ?? ""] ?? "neutral"}>{link.status_name}</Badge> : null}
          </div>
        ) : (
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            {link?.last_error
              ? "Not in NetSuite yet."
              : blocked
                ? `Not in NetSuite yet. ${blocked}`
                : erp.order_mode === "auto"
                  ? `Not in NetSuite yet. It goes over automatically${recordType === "sales_order" ? " once it's confirmed" : ""}.`
                  : "Not in NetSuite yet."}
          </p>
        )}
        {sent && link.total ? (
          <div className="ierp-facts">
            <div>
              <span className="ui-small ui-muted">Total in NetSuite</span>
              <strong>{amount(link.total, link.currency)}</strong>
            </div>
            <div>
              <span className="ui-small ui-muted">Changes from AIVA</span>
              <strong>{editable ? "Sent while pending" : "Locked in NetSuite"}</strong>
            </div>
          </div>
        ) : null}
        {link?.adopted_from ? (
          <p className="ui-small ui-muted" style={{ margin: 0 }}>
            First sent from its {link.adopted_from.startsWith("quote:") ? "quote" : "deal"}; this {WORD[recordType]} now keeps it up to date.
          </p>
        ) : null}
        {link?.last_error ? (
          <div className="ierp-error" role="status">
            <AlertTriangle size={14} aria-hidden />
            <span>
              {link.last_error}
              {link.retry_at ? ` AIVA tries again ${relativeTime(link.retry_at)}.` : ""}
            </span>
          </div>
        ) : null}
        <div className="ierp-foot">
          <span className="ui-small ui-muted">{link?.checked_at ? `Checked ${relativeTime(link.checked_at)}` : link?.last_synced_at ? `Synced ${relativeTime(link.last_synced_at)}` : ""}</span>
          <div className="ierp-actions">
            {sent && can("records.read") ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void act("refresh")} disabled={busy !== null}>
                {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Check status
              </button>
            ) : null}
            {canPush && editable && (sent || erp.orders_from.includes(SOURCE[recordType])) ? (
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => void act("push")} disabled={busy !== null}>
                {busy === "push" ? <span className="ui-spinner" /> : <Send size={14} />} {sent ? "Update in NetSuite" : "Send to NetSuite"}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </Card>
  );
}

/**
 * A product's NetSuite item: type, price at the chosen price level, and stock on
 * hand by location. Renders nothing unless NetSuite is on and connected and the
 * product is linked to an item (imported, or matched by SKU).
 *
 *   <ProductErp productId={product.id} />
 */
export function ProductErp({ productId, compact = false }: { productId: string; compact?: boolean }) {
  const { has } = useSession();
  const toast = useToast();
  const enabled = has("erp_sync");
  const data = useApi<ProductData>(enabled ? `/erp/products/${productId}` : null);
  const [busy, setBusy] = useState(false);
  if (!enabled || !data.data?.erp.connected || !data.data.link) return null;
  const { link, can_refresh: canRefresh } = data.data;
  const locations = Object.entries(link.locations ?? {});

  async function refresh() {
    setBusy(true);
    try {
      data.setData(await api.post<ProductData>(`/erp/products/${productId}/refresh`));
      toast("Read again from NetSuite.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const body = (
    <div className="ui-stack" style={{ gap: 12 }}>
      <div className="ierp-status">
        <div>
          <span className="ui-small ui-muted">{ITEM_TYPE[link.type ?? ""] ?? "Item"}</span>
          <strong>{link.sku ?? link.name ?? link.external_id}</strong>
        </div>
        {link.inactive ? <Badge tone="neutral">Inactive in NetSuite</Badge> : <Badge tone="success">Linked</Badge>}
      </div>
      <div className="ierp-facts">
        <div>
          <span className="ui-small ui-muted">Price</span>
          <strong>{amount(link.price, link.currency)}</strong>
        </div>
        {link.inventory ? (
          <div>
            <span className="ui-small ui-muted">On hand</span>
            <strong>{quantity(link.on_hand)}</strong>
          </div>
        ) : null}
        {link.inventory ? (
          <div>
            <span className="ui-small ui-muted">Available</span>
            <strong>{quantity(link.available)}</strong>
          </div>
        ) : null}
      </div>
      {link.inventory && locations.length > 1 && !compact ? (
        <ul className="ierp-locations">
          {locations.map(([name, qty]) => (
            <li key={name}>
              <span>{name}</span>
              <strong>{quantity(qty)}</strong>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="ierp-foot">
        <span className="ui-small ui-muted">{link.checked_at ? `Checked ${relativeTime(link.checked_at)}` : link.last_synced_at ? `Synced ${relativeTime(link.last_synced_at)}` : ""}</span>
        <div className="ierp-actions">
          {link.external_url ? (
            <a className="ui-btn ui-btn-ghost ui-btn-sm" href={link.external_url} target="_blank" rel="noopener noreferrer">
              Open <ExternalLink size={13} />
            </a>
          ) : null}
          {canRefresh ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void refresh()} disabled={busy}>
              {busy ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Read again
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );

  if (compact) return <div className="ierp-compact">{body}</div>;
  return (
    <Card title="NetSuite item" icon={<Boxes size={16} />}>
      {body}
    </Card>
  );
}

export default OrderErp;
