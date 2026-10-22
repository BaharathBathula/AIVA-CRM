"use client";

import { Box, Pencil, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { CURRENCIES } from "@/lib/constants";
import { moneyExact, number } from "@/lib/format";
import type { Product } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const FREQUENCY: Record<Product["billing_frequency"], string> = {
  one_time: "One-time",
  monthly: "Monthly",
  quarterly: "Quarterly",
  annual: "Annual",
};

export default function ProductsPage() {
  return (
    <AppShell>
      <Products />
    </AppShell>
  );
}

function Products() {
  const { can } = useSession();
  const toast = useToast();
  const editable = can("sales.products");
  const products = useApi<Product[]>("/products");
  const [status, setStatus] = useState<"active" | "inactive" | "all">("active");
  const [family, setFamily] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Product | "new" | null>(null);
  const [deleting, setDeleting] = useState<Product | null>(null);
  const [busy, setBusy] = useState(false);

  const families = useMemo(
    () => [...new Set((products.data ?? []).map((product) => product.family).filter(Boolean) as string[])].sort(),
    [products.data]
  );

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (products.data ?? []).filter(
      (product) =>
        (status === "all" || (status === "active" ? product.is_active : !product.is_active)) &&
        (!family || product.family === family) &&
        (!term || product.name.toLowerCase().includes(term) || (product.sku ?? "").toLowerCase().includes(term))
    );
  }, [family, products.data, search, status]);

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/products/${deleting.id}`);
      toast(`${deleting.name} deleted.`);
      setDeleting(null);
      setEditing(null);
      await products.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Sales"
        title="Products & price book"
        description="The catalog reps add to quotes. Prices here are the defaults; reps can discount per line."
        actions={
          editable ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New product
            </button>
          ) : null
        }
      />

      <div className="ui-toolbar">
        <Tabs
          label="Product status"
          value={status}
          onChange={setStatus}
          items={[
            { value: "active", label: "Active", count: (products.data ?? []).filter((p) => p.is_active).length },
            { value: "inactive", label: "Inactive", count: (products.data ?? []).filter((p) => !p.is_active).length },
            { value: "all", label: "All", count: products.data?.length ?? 0 },
          ]}
        />
        <select className="ui-select sm" style={{ width: 180 }} value={family} onChange={(e) => setFamily(e.target.value)} aria-label="Product family">
          <option value="">All families</option>
          {families.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
        <span className="ui-spacer" />
        <div className="ui-search">
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search name or SKU" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>

      {products.error ? <Alert>{products.error}</Alert> : null}

      <div className="ui-card">
        {products.loading && !products.data ? (
          <LoadingRows />
        ) : rows.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>SKU</th>
                  <th>Family</th>
                  <th>Billing</th>
                  <th className="ui-num">List price</th>
                  <th className="ui-num">Tax</th>
                  <th>Status</th>
                  {editable ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((product) => (
                  <tr key={product.id} className={editable ? "clickable" : undefined} onClick={editable ? () => setEditing(product) : undefined}>
                    <td>
                      <div className="cell-title">{product.name}</div>
                      {product.description ? <div className="cell-sub ui-truncate" style={{ maxWidth: 380 }}>{product.description}</div> : null}
                    </td>
                    <td className="ui-mono">{product.sku ?? "—"}</td>
                    <td>{product.family ?? "—"}</td>
                    <td>{FREQUENCY[product.billing_frequency]}</td>
                    <td className="ui-num ui-strong">{moneyExact(product.unit_price, product.currency)}</td>
                    <td className="ui-num">{number(product.tax_rate, 2)}%</td>
                    <td>{product.is_active ? <Badge tone="success">Active</Badge> : <Badge>Inactive</Badge>}</td>
                    {editable ? (
                      <td style={{ textAlign: "right" }}>
                        <Pencil size={14} color="var(--muted)" aria-hidden />
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Box size={20} />}
            title={products.data?.length ? "No products match" : "Your price book is empty"}
            message={products.data?.length ? "Try a different filter." : "Add the products and services you sell so reps can build quotes."}
          />
        )}
      </div>

      <ProductModal
        product={editing}
        families={families}
        onClose={() => setEditing(null)}
        onDelete={(product) => setDeleting(product)}
        onSaved={async () => {
          setEditing(null);
          await products.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.name ?? "product"}?`}
        message="Quotes that already include it keep their lines. If it's on quotes, consider marking it inactive instead."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function ProductModal({
  product,
  families,
  onClose,
  onDelete,
  onSaved,
}: {
  product: Product | "new" | null;
  families: string[];
  onClose: () => void;
  onDelete: (product: Product) => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState({
    name: "",
    sku: "",
    description: "",
    family: "",
    unit_price: "0",
    currency: "USD",
    billing_frequency: "one_time" as Product["billing_frequency"],
    tax_rate: "0",
    is_active: true,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!product) return;
    const p = product === "new" ? null : product;
    setForm({
      name: p?.name ?? "",
      sku: p?.sku ?? "",
      description: p?.description ?? "",
      family: p?.family ?? "",
      unit_price: String(p?.unit_price ?? 0),
      currency: p?.currency ?? "USD",
      billing_frequency: p?.billing_frequency ?? "one_time",
      tax_rate: String(p?.tax_rate ?? 0),
      is_active: p?.is_active ?? true,
    });
    setError(null);
  }, [product]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const payload = {
      ...form,
      name: form.name.trim(),
      sku: form.sku.trim() || null,
      description: form.description.trim() || null,
      family: form.family.trim() || null,
      unit_price: Number(form.unit_price) || 0,
      tax_rate: Number(form.tax_rate) || 0,
    };
    try {
      if (product === "new") await api.post("/products", payload);
      else if (product) await api.patch(`/products/${product.id}`, payload);
      toast(product === "new" ? "Product added to the price book." : "Product saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const set = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [key]: event.target.value });

  return (
    <Modal
      open={Boolean(product)}
      size="lg"
      title={product === "new" ? "New product" : "Edit product"}
      onClose={onClose}
      footer={
        <>
          {product && product !== "new" ? (
            <button type="button" className="ui-btn ui-btn-danger" style={{ marginRight: "auto" }} onClick={() => onDelete(product)}>
              Delete
            </button>
          ) : null}
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="product-form" className="ui-btn ui-btn-primary" disabled={busy}>
            Save product
          </button>
        </>
      }
    >
      <form id="product-form" className="ui-stack" onSubmit={submit}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Product name" required className="span-2">
            <input className="ui-input" value={form.name} onChange={set("name")} required />
          </Field>
          <Field label="SKU" hint="Unique code used on quotes and invoices.">
            <input className="ui-input ui-mono" value={form.sku} onChange={set("sku")} />
          </Field>
          <Field label="Family">
            <input className="ui-input" list="product-families" value={form.family} onChange={set("family")} placeholder="Subscriptions" />
            <datalist id="product-families">
              {families.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </Field>
          <Field label="List price" required>
            <input className="ui-input" type="number" min={0} step="0.01" value={form.unit_price} onChange={set("unit_price")} required />
          </Field>
          <Field label="Currency">
            <select className="ui-select" value={form.currency} onChange={set("currency")}>
              {CURRENCIES.map((code) => (
                <option key={code}>{code}</option>
              ))}
            </select>
          </Field>
          <Field label="Billing frequency">
            <select className="ui-select" value={form.billing_frequency} onChange={set("billing_frequency")}>
              {Object.entries(FREQUENCY).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Default tax rate (%)">
            <input className="ui-input" type="number" min={0} max={100} step="0.01" value={form.tax_rate} onChange={set("tax_rate")} />
          </Field>
          <Field label="Description" className="span-2">
            <textarea className="ui-textarea" rows={3} value={form.description} onChange={set("description")} />
          </Field>
        </div>
        <label className="ui-check">
          <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
          Available on new quotes
        </label>
      </form>
    </Modal>
  );
}
