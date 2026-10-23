"use client";

import { useEffect, useState } from "react";

import { Alert, Field, Modal } from "@/components/ui";
import { api, errorMessage } from "@/lib/use-api";
import type { Vendor } from "@/lib/workspace";

const EMPTY = {
  name: "",
  category: "",
  contact_name: "",
  email: "",
  phone: "",
  website: "",
  payment_terms: "Net 30",
  currency: "USD",
  gl_account: "",
  tax_id: "",
  street: "",
  city: "",
  state: "",
  postal_code: "",
  country: "",
  notes: "",
};

/** Create or edit a vendor. */
export function VendorModal({ open, vendor, onClose, onSaved }: { open: boolean; vendor?: Vendor | null; onClose: () => void; onSaved: (vendor: Vendor) => void }) {
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setForm(
      vendor
        ? Object.fromEntries(Object.keys(EMPTY).map((key) => [key, (vendor[key as keyof Vendor] as string | null) ?? ""])) as typeof EMPTY
        : EMPTY
    );
  }, [open, vendor]);

  const set = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: e.target.value });

  async function save() {
    setError(null);
    if (!form.name.trim()) return setError("Enter the vendor's name.");
    const payload = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim() || null]));
    payload.currency = (form.currency.trim() || "USD").toUpperCase();
    setBusy(true);
    try {
      onSaved(vendor ? await api.patch<Vendor>(`/vendors/${vendor.id}`, payload) : await api.post<Vendor>("/vendors", payload));
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
      title={vendor ? `Edit ${vendor.name}` : "New vendor"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null} {vendor ? "Save" : "Add vendor"}
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
        <Field label="Name" required>
          <input className="ui-input" value={form.name} onChange={set("name")} placeholder="Acme Components" />
        </Field>
        <Field label="Category">
          <input className="ui-input" value={form.category} onChange={set("category")} placeholder="Hardware, Services…" />
        </Field>
        <Field label="Contact person">
          <input className="ui-input" value={form.contact_name} onChange={set("contact_name")} />
        </Field>
        <Field label="Email">
          <input className="ui-input" type="email" value={form.email} onChange={set("email")} />
        </Field>
        <Field label="Phone">
          <input className="ui-input" value={form.phone} onChange={set("phone")} />
        </Field>
        <Field label="Website">
          <input className="ui-input" value={form.website} onChange={set("website")} placeholder="https://" />
        </Field>
        <Field label="Payment terms">
          <input className="ui-input" value={form.payment_terms} onChange={set("payment_terms")} />
        </Field>
        <Field label="Currency">
          <input className="ui-input" value={form.currency} onChange={set("currency")} maxLength={3} />
        </Field>
        <Field label="GL account" hint="For your accounting system.">
          <input className="ui-input" value={form.gl_account} onChange={set("gl_account")} />
        </Field>
        <Field label="Tax ID">
          <input className="ui-input" value={form.tax_id} onChange={set("tax_id")} />
        </Field>
        <Field label="Street" className="span-2">
          <input className="ui-input" value={form.street} onChange={set("street")} />
        </Field>
        <Field label="City">
          <input className="ui-input" value={form.city} onChange={set("city")} />
        </Field>
        <Field label="State / region">
          <input className="ui-input" value={form.state} onChange={set("state")} />
        </Field>
        <Field label="Postal code">
          <input className="ui-input" value={form.postal_code} onChange={set("postal_code")} />
        </Field>
        <Field label="Country">
          <input className="ui-input" value={form.country} onChange={set("country")} />
        </Field>
        <Field label="Notes" className="span-2">
          <textarea className="ui-textarea" rows={2} value={form.notes} onChange={set("notes")} />
        </Field>
      </div>
    </Modal>
  );
}
