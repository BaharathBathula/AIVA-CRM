"use client";

import Link from "next/link";
import { CalendarPlus, Clock, HandPlatter, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useToast } from "@/components/providers";
import { Alert, Badge, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { duration, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { LOCATION_TYPES, type Member, type ServiceItem } from "@/lib/workspace";

export default function ServicesPage() {
  return (
    <FeaturePage feature="services">
      <Services />
    </FeaturePage>
  );
}

function Services() {
  const toast = useToast();
  const services = useApi<{ items: ServiceItem[]; can_manage: boolean }>("/services");
  const [editing, setEditing] = useState<ServiceItem | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<ServiceItem | null>(null);
  const manage = Boolean(services.data?.can_manage);

  async function toggle(service: ServiceItem) {
    try {
      await api.patch(`/services/${service.id}`, { is_active: !service.is_active });
      await services.reload();
      toast(service.is_active ? `${service.name} turned off. It can't be booked.` : `${service.name} can be booked again.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove(service: ServiceItem) {
    try {
      await api.del(`/services/${service.id}`);
      toast(`${service.name} deleted.`);
      setDeleting(null);
      await services.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setDeleting(null);
    }
  }

  const items = services.data?.items ?? [];
  return (
    <>
      <PageHeader
        title="Services"
        description="What your team delivers — installs, consultations, training, repairs — with how long each takes, what it costs and who can do it."
        actions={
          <>
            <Link className="ui-btn" href="/appointments">
              <CalendarPlus size={16} /> Appointments
            </Link>
            {manage ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing(null)}>
                <Plus size={16} /> New service
              </button>
            ) : null}
          </>
        }
      />
      {services.error ? <Alert>{services.error}</Alert> : null}
      <div className="ui-card">
        {services.loading && !services.data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Service</th>
                  <th>Length</th>
                  <th>Where</th>
                  <th>Delivered by</th>
                  <th className="ui-num">Upcoming</th>
                  <th className="ui-num">Price</th>
                  <th>Status</th>
                  {manage ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {items.map((service) => (
                  <tr key={service.id}>
                    <td>
                      <div className="cell-title">{service.name}</div>
                      <div className="cell-sub ui-truncate" style={{ maxWidth: 320 }}>
                        {[service.category, service.description].filter(Boolean).join(" · ") || "—"}
                      </div>
                    </td>
                    <td className="ui-nowrap">
                      <Clock size={13} style={{ verticalAlign: -2 }} /> {duration(service.duration_minutes)}
                      {service.buffer_minutes ? <div className="cell-sub">+{service.buffer_minutes} min buffer</div> : null}
                    </td>
                    <td>
                      {LOCATION_TYPES[service.location_type]}
                      {service.location ? <div className="cell-sub">{service.location}</div> : null}
                    </td>
                    <td className="ui-truncate" style={{ maxWidth: 220 }}>
                      {service.staff.length ? service.staff.map((s) => s.name).join(", ") : <span className="ui-muted">Anyone</span>}
                    </td>
                    <td className="ui-num">{service.upcoming_appointments ?? 0}</td>
                    <td className="ui-num ui-strong">{service.price ? moneyExact(service.price, service.currency) : "Free"}</td>
                    <td>
                      <Badge tone={service.is_active ? "success" : "neutral"}>{service.is_active ? "Bookable" : "Off"}</Badge>
                    </td>
                    {manage ? (
                      <td className="ui-nowrap">
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setEditing(service)} aria-label={`Edit ${service.name}`}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void toggle(service)}>
                          {service.is_active ? "Turn off" : "Turn on"}
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setDeleting(service)} aria-label={`Delete ${service.name}`}>
                          <Trash2 size={14} />
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<HandPlatter size={20} />} title="No services yet" message="Add the services customers book with you, then schedule appointments for them." />
        )}
      </div>
      <ServiceModal service={editing} onClose={() => setEditing(undefined)} onSaved={() => (setEditing(undefined), void services.reload(), toast("Service saved."))} />
      <ConfirmDialog
        open={deleting !== null}
        title={`Delete ${deleting?.name ?? "service"}?`}
        message="Services with appointments can't be deleted — turn them off instead."
        onConfirm={() => deleting && void remove(deleting)}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function ServiceModal({ service, onClose, onSaved }: { service: ServiceItem | null | undefined; onClose: () => void; onSaved: () => void }) {
  const open = service !== undefined;
  const members = useApi<Member[]>(open ? "/meta/members" : null);
  const [form, setForm] = useState({ name: "", category: "", description: "", duration_minutes: 60, buffer_minutes: 0, price: 0, currency: "USD", location_type: "online", location: "" });
  const [staff, setStaff] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setForm(
      service
        ? {
            name: service.name,
            category: service.category ?? "",
            description: service.description ?? "",
            duration_minutes: service.duration_minutes,
            buffer_minutes: service.buffer_minutes,
            price: service.price,
            currency: service.currency,
            location_type: service.location_type,
            location: service.location ?? "",
          }
        : { name: "", category: "", description: "", duration_minutes: 60, buffer_minutes: 0, price: 0, currency: "USD", location_type: "online", location: "" }
    );
    setStaff(service?.staff_user_ids ?? []);
  }, [open, service]);

  async function save() {
    setError(null);
    if (!form.name.trim()) return setError("Name the service.");
    const payload = {
      ...form,
      name: form.name.trim(),
      category: form.category.trim() || null,
      description: form.description.trim() || null,
      location: form.location.trim() || null,
      currency: form.currency.trim().toUpperCase() || "USD",
      duration_minutes: Number(form.duration_minutes) || 60,
      buffer_minutes: Number(form.buffer_minutes) || 0,
      price: Number(form.price) || 0,
      staff_user_ids: staff,
    };
    setBusy(true);
    try {
      if (service) await api.patch(`/services/${service.id}`, payload);
      else await api.post("/services", payload);
      onSaved();
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
      title={service ? `Edit ${service.name}` : "New service"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null} Save
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
          <input className="ui-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Site survey" />
        </Field>
        <Field label="Category">
          <input className="ui-input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="Installation" />
        </Field>
        <Field label="Length (minutes)">
          <input className="ui-input" type="number" min={5} step={5} value={form.duration_minutes} onChange={(e) => setForm({ ...form, duration_minutes: Number(e.target.value) })} />
        </Field>
        <Field label="Buffer after (minutes)" hint="Travel or prep time kept free after each booking.">
          <input className="ui-input" type="number" min={0} step={5} value={form.buffer_minutes} onChange={(e) => setForm({ ...form, buffer_minutes: Number(e.target.value) })} />
        </Field>
        <Field label="Price">
          <input className="ui-input" type="number" min={0} step="0.01" value={form.price} onChange={(e) => setForm({ ...form, price: Number(e.target.value) })} />
        </Field>
        <Field label="Currency">
          <input className="ui-input" maxLength={3} value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} />
        </Field>
        <Field label="Where">
          <select className="ui-select" value={form.location_type} onChange={(e) => setForm({ ...form, location_type: e.target.value })}>
            {Object.entries(LOCATION_TYPES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label={form.location_type === "online" ? "Meeting link" : "Address or details"}>
          <input className="ui-input" value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} />
        </Field>
        <Field label="Description" className="span-2">
          <textarea className="ui-textarea" rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        <div className="ui-field span-2">
          <span className="ui-label">Who delivers it</span>
          <span className="ui-muted ui-small">Leave everyone unticked if anyone on the team can.</span>
          <div className="check-grid">
            {(members.data ?? []).map((m) => (
              <label key={m.id} className="ui-check">
                <input type="checkbox" checked={staff.includes(m.id)} onChange={() => setStaff(staff.includes(m.id) ? staff.filter((s) => s !== m.id) : [...staff, m.id])} /> {m.full_name}
              </label>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}
