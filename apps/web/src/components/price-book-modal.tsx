"use client";

import { useEffect, useState } from "react";

import { Alert, Field, Modal } from "@/components/ui";
import { api, errorMessage } from "@/lib/use-api";
import type { PriceBook } from "@/lib/workspace";

/** Create a price book or edit its header. */
export function PriceBookModal({ open, book, onClose, onSaved }: { open: boolean; book?: PriceBook | null; onClose: () => void; onSaved: (book: PriceBook) => void }) {
  const [form, setForm] = useState({ name: "", description: "", currency: "USD", pricing_model: "flat", valid_from: "", valid_until: "", is_active: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setForm(
      book
        ? {
            name: book.name,
            description: book.description ?? "",
            currency: book.currency,
            pricing_model: book.pricing_model,
            valid_from: book.valid_from ?? "",
            valid_until: book.valid_until ?? "",
            is_active: book.is_active,
          }
        : { name: "", description: "", currency: "USD", pricing_model: "flat", valid_from: "", valid_until: "", is_active: true }
    );
  }, [open, book]);

  async function save() {
    setError(null);
    if (!form.name.trim()) return setError("Name the price book.");
    const payload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      currency: form.currency.trim().toUpperCase() || "USD",
      pricing_model: form.pricing_model,
      valid_from: form.valid_from || null,
      valid_until: form.valid_until || null,
      is_active: form.is_active,
    };
    setBusy(true);
    try {
      onSaved(book ? await api.patch<PriceBook>(`/price-books/${book.id}`, payload) : await api.post<PriceBook>("/price-books", payload));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={book ? "Edit price book" : "New price book"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null} {book ? "Save" : "Create"}
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
        <Field label="Name" required className="span-2">
          <input className="ui-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Wholesale 2027" />
        </Field>
        <Field label="Pricing" hint={form.pricing_model === "differential" ? "List price plus a discount by quantity." : "One list price per product."}>
          <select className="ui-select" value={form.pricing_model} onChange={(e) => setForm({ ...form, pricing_model: e.target.value })}>
            <option value="flat">List price</option>
            <option value="differential">List price + volume tiers</option>
          </select>
        </Field>
        <Field label="Currency">
          <input className="ui-input" value={form.currency} maxLength={3} onChange={(e) => setForm({ ...form, currency: e.target.value })} />
        </Field>
        <Field label="Valid from">
          <input className="ui-input" type="date" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} />
        </Field>
        <Field label="Valid until">
          <input className="ui-input" type="date" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} />
        </Field>
        <Field label="Description" className="span-2">
          <textarea className="ui-textarea" rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        <label className="ui-check span-2">
          <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Active (can be picked on quotes and orders)
        </label>
      </div>
    </Modal>
  );
}
