"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Ban, CalendarClock, Check, ChevronLeft, ChevronRight, Plus, Trash2, UserX, X } from "lucide-react";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { RecordSearch, type FoundRecord } from "@/components/record-search";
import { Alert, Badge, ConfirmDialog, Field, Kpi, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { compactMoney, moneyExact } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { APPOINTMENT_STATUS, type Appointment, type Member, type ServiceItem } from "@/lib/workspace";

export default function AppointmentsPage() {
  return (
    <FeaturePage feature="services">
      <Suspense>
        <Appointments />
      </Suspense>
    </FeaturePage>
  );
}

const DAY = 24 * 3600 * 1000;
const time = (value: string) => new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function startOfWeek(date: Date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
}

function sameDay(a: Date, b: Date) {
  return a.toDateString() === b.toDateString();
}

interface Summary {
  today: number;
  next_7_days: number;
  completed_30d: number;
  revenue_30d: number;
  no_show_rate: number;
}

function Appointments() {
  const params = useSearchParams();
  const router = useRouter();
  const { can } = useSession();
  const [anchor, setAnchor] = useState(() => startOfWeek(new Date()));
  const [staff, setStaff] = useState("");
  const [booking, setBooking] = useState<{ day?: Date } | null>(params.get("new") === "1" ? {} : null);
  const [openId, setOpenId] = useState<string | null>(params.get("open"));
  const members = useApi<Member[]>("/meta/members");
  const end = new Date(anchor.getTime() + 7 * DAY);
  const list = useApi<{ items: Appointment[]; summary: Summary; timezone: string }>(
    `/appointments?start=${anchor.toISOString()}&end=${end.toISOString()}${staff ? `&staff_user_id=${staff}` : ""}`
  );
  const items = list.data?.items ?? [];
  const s = list.data?.summary;
  const days = Array.from({ length: 7 }, (_, i) => new Date(anchor.getTime() + i * DAY));
  const now = new Date();

  useEffect(() => {
    const id = params.get("open");
    if (id) setOpenId(id);
  }, [params]);

  const close = useCallback(() => {
    setOpenId(null);
    if (params.get("open")) router.replace("/appointments");
  }, [params, router]);

  return (
    <>
      <PageHeader
        title="Appointments"
        description="Bookings for your services. AIVA checks each person's calendar so nobody is double-booked, and confirms with the customer by email."
        actions={
          <>
            <Link className="ui-btn" href="/services">
              Services
            </Link>
            {can("records.write") ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setBooking({})}>
                <Plus size={16} /> Book appointment
              </button>
            ) : null}
          </>
        }
      />
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Today" value={s?.today ?? "—"} note="Scheduled" />
        <Kpi label="Next 7 days" value={s?.next_7_days ?? "—"} />
        <Kpi label="Completed in 30 days" value={s ? `${s.completed_30d}` : "—"} note={s ? `${compactMoney(s.revenue_30d)} delivered` : undefined} tone="success" />
        <Kpi label="No-show rate" value={s ? `${s.no_show_rate}%` : "—"} tone={s && s.no_show_rate > 10 ? "danger" : undefined} />
      </div>
      <div className="cal-toolbar">
        <button type="button" className="ui-btn ui-btn-icon" aria-label="Previous week" onClick={() => setAnchor(new Date(anchor.getTime() - 7 * DAY))}>
          <ChevronLeft size={16} />
        </button>
        <button type="button" className="ui-btn" onClick={() => setAnchor(startOfWeek(new Date()))}>
          Today
        </button>
        <button type="button" className="ui-btn ui-btn-icon" aria-label="Next week" onClick={() => setAnchor(new Date(anchor.getTime() + 7 * DAY))}>
          <ChevronRight size={16} />
        </button>
        <h2 className="cal-title">
          {anchor.toLocaleDateString([], { month: "short", day: "numeric" })} – {new Date(anchor.getTime() + 6 * DAY).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}
        </h2>
        <span className="ui-spacer" />
        <select className="ui-select" value={staff} onChange={(e) => setStaff(e.target.value)} aria-label="Staff member">
          <option value="">Everyone</option>
          {(members.data ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.full_name}
            </option>
          ))}
        </select>
      </div>
      {list.error ? <Alert>{list.error}</Alert> : null}
      {list.loading && !list.data ? (
        <div className="ui-card">
          <LoadingRows rows={6} />
        </div>
      ) : (
        <div className="cal-week">
          {days.map((day) => (
            <section key={day.toISOString()} className={`cal-day${sameDay(day, now) ? " today" : ""}${day.getDay() % 6 === 0 ? " weekend" : ""}`} aria-label={day.toDateString()}>
              <div className="cal-day-head">
                {day.toLocaleDateString([], { weekday: "short" })} <strong>{day.getDate()}</strong>
              </div>
              {items
                .filter((a) => sameDay(new Date(a.start_at), day))
                .map((a) => (
                  <button key={a.id} type="button" className={`cal-event ${a.status}`} onClick={() => setOpenId(a.id)}>
                    <span className="time">
                      {time(a.start_at)} · {a.staff_name ?? "Unassigned"}
                    </span>
                    <span className="title">
                      {a.title} — {a.contact_name ?? a.lead_name ?? a.account_name}
                    </span>
                  </button>
                ))}
              {can("records.write") && day >= new Date(now.toDateString()) ? (
                <button type="button" className="cal-add" onClick={() => setBooking({ day })}>
                  + Book
                </button>
              ) : null}
            </section>
          ))}
        </div>
      )}
      <BookModal state={booking} onClose={() => setBooking(null)} onBooked={() => (setBooking(null), void list.reload())} />
      <AppointmentDetail id={openId} onClose={close} onChanged={() => void list.reload()} />
    </>
  );
}

function localDate(date: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function BookModal({ state, onClose, onBooked }: { state: { day?: Date } | null; onClose: () => void; onBooked: () => void }) {
  const toast = useToast();
  const open = state !== null;
  const services = useApi<{ items: ServiceItem[] }>(open ? "/services?active=true" : null);
  const [serviceId, setServiceId] = useState("");
  const [day, setDay] = useState(localDate(new Date()));
  const [slot, setSlot] = useState<{ start_at: string; staff_user_id: string } | null>(null);
  const [customer, setCustomer] = useState<FoundRecord | null>(null);
  const [notes, setNotes] = useState("");
  const [notify, setNotify] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overlap, setOverlap] = useState(false);
  const slots = useApi<{ slots: { start_at: string; end_at: string; staff_user_id: string; staff_name: string | null }[]; timezone: string }>(
    open && serviceId && day ? `/services/${serviceId}/availability?date=${day}` : null
  );
  const service = useMemo(() => services.data?.items.find((s) => s.id === serviceId), [services.data, serviceId]);

  useEffect(() => {
    if (!open) return;
    setDay(localDate(state?.day && state.day > new Date() ? state.day : new Date()));
    setSlot(null);
    setCustomer(null);
    setNotes("");
    setError(null);
    setOverlap(false);
  }, [open, state]);

  useEffect(() => {
    if (!serviceId && services.data?.items.length) setServiceId(services.data.items[0].id);
  }, [services.data, serviceId]);

  async function book() {
    setError(null);
    if (!slot) return setError("Pick a time.");
    if (!customer) return setError("Choose who the appointment is with.");
    setBusy(true);
    try {
      const created = await api.post<Appointment>("/appointments", {
        service_id: serviceId,
        staff_user_id: slot.staff_user_id,
        start_at: slot.start_at,
        contact_id: customer.type === "contact" ? customer.id : null,
        lead_id: customer.type === "lead" ? customer.id : null,
        account_id: customer.type === "account" ? customer.id : null,
        notes: notes.trim() || null,
        notify_customer: notify,
        allow_overlap: overlap,
      });
      toast(created.customer_emailed ? "Booked. The customer has a confirmation email." : "Booked.");
      onBooked();
    } catch (caught) {
      const message = errorMessage(caught);
      setError(message);
      if (message.includes("already booked")) setOverlap(true);
    } finally {
      setBusy(false);
    }
  }

  const byStaff = new Map<string, { name: string; slots: { start_at: string; staff_user_id: string }[] }>();
  for (const s of slots.data?.slots ?? []) {
    const entry = byStaff.get(s.staff_user_id) ?? { name: s.staff_name ?? "Team member", slots: [] };
    entry.slots.push(s);
    byStaff.set(s.staff_user_id, entry);
  }

  return (
    <Modal
      open={open}
      size="lg"
      title="Book an appointment"
      onClose={onClose}
      footer={
        <>
          <label className="ui-check" style={{ marginRight: "auto" }}>
            <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> Email the customer a confirmation
          </label>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={`ui-btn ${overlap ? "ui-btn-danger solid" : "ui-btn-primary"}`} onClick={() => void book()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null} {overlap ? "Book anyway" : "Book"}
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert tone={overlap ? "warning" : "danger"}>{error}</Alert>
        </div>
      ) : null}
      {services.data && !services.data.items.length ? (
        <Alert tone="info">
          Add a service first. <Link href="/services">Go to services</Link>
        </Alert>
      ) : (
        <div className="ui-form-grid">
          <Field label="Service" required>
            <select className="ui-select" value={serviceId} onChange={(e) => (setServiceId(e.target.value), setSlot(null))}>
              {(services.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} · {s.duration_minutes} min{s.price ? ` · ${moneyExact(s.price, s.currency)}` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Day" required>
            <input className="ui-input" type="date" min={localDate(new Date())} value={day} onChange={(e) => (setDay(e.target.value), setSlot(null))} />
          </Field>
          <div className="ui-field span-2">
            <span className="ui-label">Free times{slots.data ? ` (${slots.data.timezone})` : ""}</span>
            {slots.loading ? (
              <span className="ui-muted ui-small">Checking calendars…</span>
            ) : byStaff.size ? (
              [...byStaff.entries()].map(([staffId, entry]) => (
                <div key={staffId} className="ui-stack" style={{ gap: 6 }}>
                  <span className="ui-small ui-muted">{entry.name}</span>
                  <div className="slot-grid">
                    {entry.slots.map((s) => (
                      <button
                        key={s.start_at}
                        type="button"
                        className="slot"
                        aria-pressed={slot?.start_at === s.start_at && slot.staff_user_id === staffId}
                        onClick={() => (setSlot({ start_at: s.start_at, staff_user_id: staffId }), setOverlap(false), setError(null))}
                      >
                        {time(s.start_at)}
                      </button>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <span className="ui-muted ui-small">No free times that day (business hours are 9:00–17:00, Monday to Friday). Try another day.</span>
            )}
          </div>
          <div className="ui-field span-2">
            <span className="ui-label">With</span>
            {customer ? (
              <span className="record-chip" style={{ justifySelf: "start" }}>
                {customer.title} <span className="ui-muted">· {customer.type}</span>
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Change customer" onClick={() => setCustomer(null)}>
                  <X size={13} />
                </button>
              </span>
            ) : (
              <RecordSearch types={["contact", "lead", "account"]} placeholder="Search contacts, leads or accounts" onPick={setCustomer} />
            )}
          </div>
          <Field label="Notes" className="span-2">
            <textarea className="ui-textarea" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={service?.description ?? ""} />
          </Field>
        </div>
      )}
    </Modal>
  );
}

function AppointmentDetail({ id, onClose, onChanged }: { id: string | null; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const item = useApi<Appointment>(id ? `/appointments/${id}` : null);
  const [mode, setMode] = useState<"view" | "complete" | "cancel" | "reschedule" | "delete">("view");
  const [text, setText] = useState("");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const a = id ? item.data : null;

  useEffect(() => {
    setMode("view");
    setText("");
  }, [id]);

  async function run(fn: () => Promise<Appointment | void>, message: string) {
    setBusy(true);
    try {
      const result = await fn();
      if (result) item.setData(result);
      toast(message);
      setMode("view");
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  if (!id) return null;
  const started = a ? new Date(a.start_at) <= new Date() : false;
  const editable = can("records.write") && a?.status === "scheduled";

  return (
    <>
      <Modal
        open={mode !== "delete"}
        title={a ? `${a.display_number} · ${a.title}` : "Appointment"}
        description={a ? new Date(a.start_at).toLocaleString([], { weekday: "long", month: "long", day: "numeric", hour: "numeric", minute: "2-digit" }) + ` – ${time(a.end_at)}` : undefined}
        onClose={onClose}
        footer={
          !a ? null : mode === "view" ? (
            <>
              {can("records.delete") ? (
                <button type="button" className="ui-btn ui-btn-ghost" style={{ marginRight: "auto" }} onClick={() => setMode("delete")}>
                  <Trash2 size={15} /> Delete
                </button>
              ) : null}
              {editable ? (
                <>
                  <button type="button" className="ui-btn ui-btn-danger" onClick={() => (setText(""), setMode("cancel"))}>
                    <Ban size={15} /> Cancel
                  </button>
                  <button
                    type="button"
                    className="ui-btn"
                    onClick={() => {
                      const d = new Date(a.start_at);
                      const pad = (n: number) => String(n).padStart(2, "0");
                      setWhen(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`);
                      setMode("reschedule");
                    }}
                  >
                    <CalendarClock size={15} /> Reschedule
                  </button>
                  {started ? (
                    <>
                      <button type="button" className="ui-btn" onClick={() => void run(() => api.post<Appointment>(`/appointments/${id}/status`, { status: "no_show" }), "Marked as a no-show.")}>
                        <UserX size={15} /> No-show
                      </button>
                      <button type="button" className="ui-btn ui-btn-primary" onClick={() => (setText(""), setMode("complete"))}>
                        <Check size={15} /> Complete
                      </button>
                    </>
                  ) : null}
                </>
              ) : null}
            </>
          ) : (
            <>
              <button type="button" className="ui-btn" onClick={() => setMode("view")}>
                Back
              </button>
              <button
                type="button"
                className={`ui-btn ${mode === "cancel" ? "ui-btn-danger solid" : "ui-btn-primary"}`}
                disabled={busy}
                onClick={() =>
                  void (mode === "complete"
                    ? run(() => api.post<Appointment>(`/appointments/${id}/status`, { status: "completed", outcome: text.trim() || null }), "Completed and logged on the customer's timeline.")
                    : mode === "cancel"
                      ? run(() => api.post<Appointment>(`/appointments/${id}/status`, { status: "cancelled", cancel_reason: text.trim() || null }), "Appointment cancelled.")
                      : run(() => api.patch<Appointment>(`/appointments/${id}`, { start_at: new Date(when).toISOString() }), "Rescheduled."))
                }
              >
                {mode === "complete" ? "Save" : mode === "cancel" ? "Cancel appointment" : "Move appointment"}
              </button>
            </>
          )
        }
      >
        {item.error ? <Alert>{item.error}</Alert> : null}
        {!a ? (
          <LoadingRows rows={4} />
        ) : mode === "complete" ? (
          <Field label="Outcome" hint="Logged on the contact and account timeline.">
            <textarea className="ui-textarea" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
        ) : mode === "cancel" ? (
          <Field label="Reason (optional)" hint="The customer is emailed if the appointment hasn't started.">
            <input className="ui-input" value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
        ) : mode === "reschedule" ? (
          <Field label="New start time" hint="The end time moves with it. The customer is emailed.">
            <input className="ui-input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
          </Field>
        ) : (
          <div className="ui-stack">
            <div>
              <Badge tone={APPOINTMENT_STATUS[a.status].tone}>{APPOINTMENT_STATUS[a.status].label}</Badge>
            </div>
            <dl className="ui-dl">
              <dt>Service</dt>
              <dd>{a.service_name ?? "—"}</dd>
              <dt>With</dt>
              <dd>
                {a.contact_id ? <Link href={`/contacts/${a.contact_id}`}>{a.contact_name}</Link> : a.lead_id ? <Link href={`/leads/${a.lead_id}`}>{a.lead_name}</Link> : null}
                {a.account_id ? (
                  <>
                    {a.contact_id || a.lead_id ? " · " : ""}
                    <Link href={`/accounts/${a.account_id}`}>{a.account_name}</Link>
                  </>
                ) : null}
              </dd>
              <dt>Staff</dt>
              <dd>{a.staff_name ?? "—"}</dd>
              <dt>Where</dt>
              <dd>{a.location ?? "—"}</dd>
              <dt>Price</dt>
              <dd>{a.price ? moneyExact(a.price, a.currency) : "Free"}</dd>
            </dl>
            {a.notes ? <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{a.notes}</p> : null}
            {a.outcome ? (
              <p style={{ margin: 0 }}>
                <strong>Outcome:</strong> {a.outcome}
              </p>
            ) : null}
            {a.cancel_reason ? <Alert tone="warning">Cancelled: {a.cancel_reason}</Alert> : null}
          </div>
        )}
      </Modal>
      <ConfirmDialog
        open={mode === "delete"}
        title="Delete this appointment?"
        message="It's removed from AIVA. The customer isn't told — cancel it instead if they should be."
        busy={busy}
        onConfirm={() =>
          void run(async () => {
            await api.del(`/appointments/${id}`);
            onClose();
          }, "Deleted.")
        }
        onClose={() => setMode("view")}
      />
    </>
  );
}
