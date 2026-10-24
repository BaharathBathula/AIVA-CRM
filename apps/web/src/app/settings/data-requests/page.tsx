"use client";

import Link from "next/link";
import { Search, UserRoundSearch, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { useMembers } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, Field, LoadingRows, Modal, PageHeader, Tabs, type Tone } from "@/components/ui";
import { date, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type DataRequest = {
  id: string;
  record_type: "lead" | "contact";
  record_id: string;
  record_label: string;
  href: string;
  fields: string[];
  field_labels: string[];
  status: "open" | "fulfilled" | "not_found" | "cancelled";
  note: string | null;
  requested_by: string | null;
  requested_by_user_id: string | null;
  assigned_to: string | null;
  assigned_to_user_id: string | null;
  fulfilled_by: string | null;
  fulfilled_at: string | null;
  result: Record<string, string>;
  credits_charged: number | null;
  credit_cost: number;
  due_at: string | null;
  created_at: string;
  overdue: boolean;
};

type RequestList = {
  items: DataRequest[];
  counts: Record<string, number>;
  fields: { key: string; label: string; credits: number }[];
  can_work: boolean;
};

type StatusTab = "open" | "fulfilled" | "not_found" | "cancelled" | "all";

const STATUS: Record<DataRequest["status"], { label: string; tone: Tone }> = {
  open: { label: "Open", tone: "info" },
  fulfilled: { label: "Found", tone: "success" },
  not_found: { label: "Not found", tone: "warning" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export default function DataRequestsPage() {
  return (
    <SettingsShell>
      <DataRequests />
    </SettingsShell>
  );
}

function DataRequests() {
  const toast = useToast();
  const [tab, setTab] = useState<StatusTab>("open");
  const [mine, setMine] = useState(false);
  const [term, setTerm] = useState("");
  const requests = useApi<RequestList>(`/data-requests?${tab !== "all" ? `status=${tab}&` : ""}${mine ? "mine=true" : ""}`);
  const [working, setWorking] = useState<DataRequest | null>(null);
  const data = requests.data;
  const items = (data?.items ?? []).filter((r) => !term || `${r.record_label} ${r.requested_by ?? ""}`.toLowerCase().includes(term.toLowerCase()));

  async function cancel(request: DataRequest) {
    try {
      await api.post(`/data-requests/${request.id}/cancel`);
      toast("Request cancelled.");
      await requests.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const total = Object.values(data?.counts ?? {}).reduce((a, b) => a + b, 0);

  return (
    <>
      <PageHeader
        title="Data requests"
        description={
          <>
            When a contact or lead is missing an email, phone or title, anyone can ask for it from the record page. Requests that are found use credits; nothing is
            charged when the data can&apos;t be found.
          </>
        }
      />
      {requests.error ? <Alert>{requests.error}</Alert> : null}
      <Card flush>
        <div className="admin-toolbar">
          <Tabs<StatusTab>
            label="Request status"
            value={tab}
            onChange={setTab}
            items={[
              { value: "open", label: "Open", count: data?.counts.open ?? 0, hot: true },
              { value: "fulfilled", label: "Found", count: data?.counts.fulfilled ?? 0 },
              { value: "not_found", label: "Not found", count: data?.counts.not_found ?? 0 },
              { value: "cancelled", label: "Cancelled", count: data?.counts.cancelled ?? 0 },
              { value: "all", label: "All", count: total },
            ]}
          />
          <span className="ui-spacer" />
          <label className="ui-check">
            <input type="checkbox" checked={mine} onChange={(event) => setMine(event.target.checked)} /> Only mine
          </label>
          <label className="settings-nav-search" style={{ width: 220 }}>
            <Search size={15} aria-hidden />
            <input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search" aria-label="Search requests" />
          </label>
        </div>
        {requests.loading && !data ? (
          <LoadingRows />
        ) : items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Needs</th>
                  <th>Asked by</th>
                  <th>Status</th>
                  <th>Due</th>
                  <th className="ui-num">Credits</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {items.map((request) => (
                  <tr key={request.id}>
                    <td>
                      <Link href={request.href} className="cell-title">
                        {request.record_label}
                      </Link>
                      <div className="ui-muted ui-small">{request.record_type === "lead" ? "Lead" : "Contact"}</div>
                    </td>
                    <td className="ui-small" style={{ maxWidth: 260 }}>
                      {request.field_labels.join(", ")}
                      {request.note ? <div className="ui-muted ui-truncate">“{request.note}”</div> : null}
                      {request.status === "fulfilled" ? (
                        <div className="text-success ui-truncate">
                          {Object.entries(request.result)
                            .filter(([key]) => key !== "note")
                            .map(([, value]) => value)
                            .join(" · ")}
                        </div>
                      ) : null}
                    </td>
                    <td className="ui-small">
                      {request.requested_by ?? "—"}
                      <div className="ui-muted">{relativeTime(request.created_at)}</div>
                    </td>
                    <td>
                      <Badge tone={STATUS[request.status].tone}>{STATUS[request.status].label}</Badge>
                      {request.assigned_to && request.status === "open" ? <div className="ui-muted ui-small">With {request.assigned_to}</div> : null}
                    </td>
                    <td className={`ui-small ui-nowrap${request.overdue ? " text-danger" : ""}`}>{request.status === "open" ? date(request.due_at) : request.fulfilled_at ? date(request.fulfilled_at) : "—"}</td>
                    <td className="ui-num">{request.credits_charged ?? <span className="ui-muted">up to {request.credit_cost}</span>}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {request.status === "open" && data?.can_work ? (
                        <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => setWorking(request)}>
                          Work on it
                        </button>
                      ) : null}
                      {request.status === "open" && !data?.can_work ? (
                        <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => void cancel(request)}>
                          Cancel
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<UserRoundSearch size={20} />}
            title={tab === "open" ? "No open requests" : "Nothing here"}
            message="Open a contact or lead and choose “Request data” to ask for a missing email, phone or title."
          />
        )}
      </Card>
      {data ? (
        <div className="ui-muted ui-small">
          Cost per field found:{" "}
          {data.fields.map((f) => `${f.label} ${f.credits} credit${f.credits === 1 ? "" : "s"}`).join(" · ")}. See the balance in{" "}
          <Link href="/settings/credits">Credit usage</Link>.
        </div>
      ) : null}
      <WorkModal
        request={working}
        fields={data?.fields ?? []}
        onClose={() => setWorking(null)}
        onDone={async () => {
          setWorking(null);
          await requests.reload();
        }}
      />
    </>
  );
}

function WorkModal({
  request,
  fields,
  onClose,
  onDone,
}: {
  request: DataRequest | null;
  fields: RequestList["fields"];
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const { members } = useMembers();
  const [values, setValues] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tried, setTried] = useState<string | null>(null);

  useEffect(() => {
    setValues({});
    setNote("");
    setError(null);
    setTried(null);
  }, [request]);

  if (!request) return null;
  const labels = Object.fromEntries(fields.map((f) => [f.key, f.label]));

  async function run(kind: "fulfil" | "not-found" | "auto" | "cancel") {
    if (!request) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === "fulfil") {
        await api.post(`/data-requests/${request.id}/fulfil`, { values, note: note || null });
        toast(`Saved to ${request.record_label}.`);
        onDone();
      } else if (kind === "not-found") {
        await api.post(`/data-requests/${request.id}/not-found`, { note: note || null });
        toast("Marked not found. Nothing was charged.");
        onDone();
      } else if (kind === "cancel") {
        await api.post(`/data-requests/${request.id}/cancel`);
        toast("Request cancelled.");
        onDone();
      } else {
        const result = await api.post<{ found: Record<string, string>; tried: { provider: string; label?: string; found?: string[] }[] }>(`/data-requests/${request.id}/auto`);
        if (Object.keys(result.found).length) {
          toast(`Found ${Object.keys(result.found).map((k) => labels[k] ?? k).join(", ")} and saved it.`);
          onDone();
        } else {
          setTried(`Enrichment didn't find it (${result.tried.map((t) => t.label ?? t.provider).join(", ") || "no providers on"}). Enter it yourself or mark it not found.`);
        }
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function assign(userId: string) {
    if (!request) return;
    try {
      await api.post(`/data-requests/${request.id}/assign`, { user_id: userId || null });
      toast(userId ? "Assigned." : "Unassigned.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <Modal
      open
      title={`Find data for ${request.record_label}`}
      description={`${request.requested_by ?? "Someone"} asked ${relativeTime(request.created_at)}${request.note ? `: “${request.note}”` : "."}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn ui-btn-ghost" disabled={Boolean(busy)} onClick={() => void run("cancel")}>
            Cancel request
          </button>
          <span className="ui-spacer" />
          <button type="button" className="ui-btn" disabled={Boolean(busy)} onClick={() => void run("not-found")}>
            Not found
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={Boolean(busy) || !Object.values(values).some((v) => v.trim())} onClick={() => void run("fulfil")}>
            {busy === "fulfil" ? <span className="ui-spinner" /> : null} Save to record
          </button>
        </>
      }
    >
      <div className="ui-stack" style={{ gap: 14 }}>
        {error ? <Alert>{error}</Alert> : null}
        {tried ? <Alert tone="info">{tried}</Alert> : null}
        <button type="button" className="ui-btn" disabled={Boolean(busy)} onClick={() => void run("auto")} style={{ justifySelf: "start" }}>
          {busy === "auto" ? <span className="ui-spinner" /> : <Wand2 size={15} />} Try waterfall enrichment first
        </button>
        <div className="ui-form-grid">
          {request.fields.map((key) => (
            <Field key={key} label={labels[key] ?? key}>
              <input
                className="ui-input"
                value={values[key] ?? ""}
                type={key === "email" ? "email" : key === "phone" || key === "mobile" ? "tel" : "text"}
                onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))}
              />
            </Field>
          ))}
          <Field label="Note (optional)" className="span-2">
            <input className="ui-input" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} placeholder="Where you found it, or why it couldn't be found" />
          </Field>
          <Field label="Assigned to" className="span-2">
            <select className="ui-select" defaultValue={request.assigned_to_user_id ?? ""} onChange={(event) => void assign(event.target.value)}>
              <option value="">Nobody</option>
              {members
                .filter((m) => m.is_active)
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.full_name}
                  </option>
                ))}
            </select>
          </Field>
        </div>
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Saving fills the record&apos;s fields and charges {request.requested_by ?? "the requester"}&apos;s credits only for what you enter.
        </p>
      </div>
    </Modal>
  );
}
