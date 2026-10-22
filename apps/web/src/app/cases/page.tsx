"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Headset, Plus, Search } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { AppShell } from "@/components/app-shell";
import { CASE_STATUS_LABEL, CHANNEL_LABEL, SlaBadge } from "@/components/case-bits";
import { useSession, useToast } from "@/components/providers";
import {
  Alert,
  Avatar,
  Badge,
  CASE_STATUS_TONE,
  EmptyState,
  Field,
  Kpi,
  LoadingRows,
  Modal,
  PageHeader,
  PRIORITY_TONE,
  Tabs,
} from "@/components/ui";
import { number, relativeTime } from "@/lib/format";
import type { CaseDetail, CaseStats, CaseSummary, Member, Paged, Team } from "@/lib/platform-types";
import { api, errorMessage, query, useApi, useDebounced } from "@/lib/use-api";
import type { Account } from "@/types/account";
import type { Contact } from "@/types/contact";

type View = "my_open" | "unassigned" | "all_open" | "breached" | "resolved" | "all";

export default function CasesPage() {
  return (
    <AppShell permission="service.cases" what="cases">
      <Cases />
    </AppShell>
  );
}

function Cases() {
  const router = useRouter();
  const { can } = useSession();
  const [view, setView] = useState<View>("all_open");
  const [priority, setPriority] = useState("");
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const term = useDebounced(search.trim(), 250);
  const stats = useApi<CaseStats>("/cases/stats");
  const cases = useApi<Paged<CaseSummary>>(`/cases${query({ view, priority, search: term, page_size: 100 })}`);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initial = params.get("view") as View | null;
    if (initial && ["my_open", "unassigned", "all_open", "breached", "resolved", "all"].includes(initial)) setView(initial);
    if (params.get("new") === "1") setCreateOpen(true);
  }, []);

  const s = stats.data;

  return (
    <>
      <PageHeader
        eyebrow="Service"
        title="Cases"
        description="Customer issues with SLA clocks, queues and a shared conversation history."
        actions={
          can("records.write") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreateOpen(true)}>
              <Plus size={16} /> New case
            </button>
          ) : null
        }
      />

      <div className="ui-stack">
        <div className="ui-grid ui-grid-6">
          <Kpi label="Open" value={number(s?.open ?? 0)} note={`${number(s?.unassigned ?? 0)} unassigned`} />
          <Kpi label="Past SLA" value={number(s?.breached ?? 0)} tone={s?.breached ? "danger" : undefined} note="Response or resolution" />
          <Kpi label="Resolved today" value={number(s?.resolved_today ?? 0)} tone={s?.resolved_today ? "success" : undefined} />
          <Kpi label="First response" value={s?.avg_first_response_hours != null ? `${number(s.avg_first_response_hours, 1)}h` : "—"} note="Average, last 30 days" />
          <Kpi label="Resolution time" value={s?.avg_resolution_hours != null ? `${number(s.avg_resolution_hours, 1)}h` : "—"} note="Average, last 30 days" />
          <Kpi label="CSAT" value={s?.csat != null ? `${number(s.csat, 1)} / 5` : "—"} note={s?.sla_met_percent != null ? `${number(s.sla_met_percent)}% SLA met` : undefined} />
        </div>

        <div className="ui-toolbar" style={{ marginBottom: 0 }}>
          <Tabs
            label="Case queue"
            value={view}
            onChange={setView}
            items={[
              { value: "my_open", label: "My open", count: s?.mine },
              { value: "unassigned", label: "Unassigned", count: s?.unassigned },
              { value: "all_open", label: "All open", count: s?.open },
              { value: "breached", label: "Past SLA", count: s?.breached, hot: true },
              { value: "resolved", label: "Resolved" },
              { value: "all", label: "All" },
            ]}
          />
          <select className="ui-select sm" style={{ width: 150 }} value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Priority">
            <option value="">Any priority</option>
            {["urgent", "high", "medium", "low"].map((item) => (
              <option key={item} value={item}>
                {item[0].toUpperCase() + item.slice(1)}
              </option>
            ))}
          </select>
          <span className="ui-spacer" />
          <div className="ui-search">
            <Search size={15} />
            <input className="ui-input sm" placeholder="Search subject or number" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>

        {cases.error ? <Alert>{cases.error}</Alert> : null}

        <div className="ui-card">
          {cases.loading && !cases.data ? (
            <LoadingRows />
          ) : cases.data?.items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Customer</th>
                    <th>Priority</th>
                    <th>Status</th>
                    <th>SLA</th>
                    <th>Assigned to</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.data.items.map((item) => (
                    <tr key={item.id} className="clickable" onClick={() => router.push(`/cases/${item.id}`)}>
                      <td style={{ maxWidth: 360 }}>
                        <Link href={`/cases/${item.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                          {item.subject}
                        </Link>
                        <div className="cell-sub">
                          {item.display_number} · {CHANNEL_LABEL[item.channel] ?? item.channel}
                          {item.category ? ` · ${item.category}` : ""}
                        </div>
                      </td>
                      <td>
                        <div>{item.account_name ?? "—"}</div>
                        <div className="cell-sub">{item.contact_name ?? ""}</div>
                      </td>
                      <td>
                        <Badge tone={PRIORITY_TONE[item.priority]}>{item.priority}</Badge>
                      </td>
                      <td>
                        <Badge tone={CASE_STATUS_TONE[item.status]}>{CASE_STATUS_LABEL[item.status]}</Badge>
                      </td>
                      <td>
                        <SlaBadge sla={item.sla} />
                      </td>
                      <td>
                        {item.assigned_to_name ? (
                          <div className="ui-row">
                            <Avatar name={item.assigned_to_name} size={24} />
                            <span className="ui-nowrap">{item.assigned_to_name}</span>
                          </div>
                        ) : (
                          <span className="ui-muted">{item.team_name ? `${item.team_name} queue` : "Unassigned"}</span>
                        )}
                      </td>
                      <td className="ui-muted ui-nowrap">{relativeTime(item.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<Headset size={20} />}
              title={view === "breached" ? "Nothing past SLA" : "No cases here"}
              message={view === "my_open" ? "No open cases are assigned to you." : "Try another queue or clear the filters."}
            />
          )}
        </div>
      </div>

      <NewCaseModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(created) => {
          setCreateOpen(false);
          router.push(`/cases/${created.id}`);
        }}
      />
    </>
  );
}

function NewCaseModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (created: CaseDetail) => void }) {
  const toast = useToast();
  const accounts = useApi<Account[]>(open ? "/accounts?limit=100" : null);
  const members = useApi<Member[]>(open ? "/meta/members" : null);
  const teams = useApi<Team[]>(open ? "/admin/teams" : null);
  const [form, setForm] = useState({
    subject: "",
    description: "",
    priority: "medium",
    channel: "email",
    category: "",
    account_id: "",
    contact_id: "",
    assigned_to_user_id: "",
    team_id: "",
  });
  const contacts = useApi<Contact[]>(open && form.account_id ? `/contacts?account_id=${form.account_id}&limit=100` : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setForm({ subject: "", description: "", priority: "medium", channel: "email", category: "", account_id: "", contact_id: "", assigned_to_user_id: "", team_id: "" });
      setError(null);
    }
  }, [open]);

  const set = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [key]: event.target.value, ...(key === "account_id" ? { contact_id: "" } : {}) });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<CaseDetail>("/cases", {
        subject: form.subject.trim(),
        description: form.description.trim() || null,
        priority: form.priority,
        channel: form.channel,
        category: form.category.trim() || null,
        account_id: form.account_id || null,
        contact_id: form.contact_id || null,
        assigned_to_user_id: form.assigned_to_user_id || null,
        team_id: form.team_id || null,
      });
      toast(`${created.display_number} opened.`);
      onCreated(created);
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
      title="New case"
      description="SLA targets are set automatically from the priority."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="case-form" className="ui-btn ui-btn-primary" disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null}
            Open case
          </button>
        </>
      }
    >
      <form id="case-form" className="ui-stack" onSubmit={submit}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Subject" required className="span-2">
            <input className="ui-input" value={form.subject} onChange={set("subject")} required placeholder="Can't export reports to CSV" />
          </Field>
          <Field label="Description" className="span-2">
            <textarea className="ui-textarea" rows={4} value={form.description} onChange={set("description")} placeholder="What happened, who's affected, steps to reproduce." />
          </Field>
          <Field label="Account">
            <select className="ui-select" value={form.account_id} onChange={set("account_id")}>
              <option value="">No account</option>
              {(accounts.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Contact">
            <select className="ui-select" value={form.contact_id} onChange={set("contact_id")} disabled={!form.account_id}>
              <option value="">{form.account_id ? "No contact" : "Choose an account first"}</option>
              {(contacts.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.first_name} {item.last_name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Priority">
            <select className="ui-select" value={form.priority} onChange={set("priority")}>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </Field>
          <Field label="Channel">
            <select className="ui-select" value={form.channel} onChange={set("channel")}>
              {Object.entries(CHANNEL_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Category">
            <input className="ui-input" value={form.category} onChange={set("category")} placeholder="Billing, Integrations…" />
          </Field>
          <Field label="Team queue">
            <select className="ui-select" value={form.team_id} onChange={set("team_id")}>
              <option value="">No team</option>
              {(teams.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Assign to">
            <select className="ui-select" value={form.assigned_to_user_id} onChange={set("assigned_to_user_id")}>
              <option value="">Leave unassigned</option>
              {(members.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.full_name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </form>
    </Modal>
  );
}
