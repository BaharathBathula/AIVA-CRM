"use client";

import Link from "next/link";
import { PhoneCall, PhoneIncoming, PhoneOutgoing } from "lucide-react";
import { useState } from "react";

import { CallDialog, type CallTarget } from "@/components/call-dialog";
import { useRefOptions } from "@/components/field-inputs";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs, type Tone } from "@/components/ui";
import { dateTime, titleCase } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

interface CallRow {
  id: string;
  subject: string;
  notes: string | null;
  direction: "inbound" | "outbound";
  outcome: string;
  duration_seconds: number;
  phone: string | null;
  occurred_at: string;
  by: string | null;
  contact_name: string | null;
  account_name: string | null;
  link: string | null;
}

interface Calls {
  items: CallRow[];
  stats: { total: number; today: number; connected: number; connect_rate: number; average_duration_seconds: number; talk_time_seconds: number };
  outcomes: string[];
}

const OUTCOME_TONE: Record<string, Tone> = {
  connected: "success",
  left_voicemail: "info",
  no_answer: "warning",
  busy: "warning",
  wrong_number: "danger",
  scheduled_callback: "primary",
};

function clock(seconds: number) {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}h ${m}m` : `${m}:${String(s).padStart(2, "0")}`;
}

export default function CallsPage() {
  return (
    <FeaturePage feature="calling">
      <CallsView />
    </FeaturePage>
  );
}

function CallsView() {
  const { can } = useSession();
  const [days, setDays] = useState<"1" | "7" | "30" | "90">("30");
  const [mine, setMine] = useState(false);
  const calls = useApi<Calls>(`/calls${query({ days, mine: mine || undefined })}`);
  const [picking, setPicking] = useState(false);
  const [target, setTarget] = useState<CallTarget | null>(null);
  const s = calls.data?.stats;

  return (
    <>
      <PageHeader
        title="Calls"
        description="Call from any lead, contact or account with one click. AIVA times the call, logs the outcome and notes, and can schedule the follow-up."
        actions={
          can("records.write") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setPicking(true)}>
              <PhoneCall size={16} /> New call
            </button>
          ) : null
        }
      />
      {calls.error ? <Alert>{calls.error}</Alert> : null}
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Calls" value={s?.total ?? "—"} note={s ? `${s.today} today` : undefined} />
        <Kpi label="Connect rate" value={s ? `${s.connect_rate}%` : "—"} note={s ? `${s.connected} conversations` : undefined} />
        <Kpi label="Average call" value={s ? clock(s.average_duration_seconds) : "—"} />
        <Kpi label="Talk time" value={s ? clock(s.talk_time_seconds) : "—"} />
      </div>
      <Card
        title="Call log"
        flush
        actions={
          <div className="ui-row" style={{ gap: 10 }}>
            {can("records.view_all") ? (
              <label className="ui-check ui-small">
                <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only my calls
              </label>
            ) : null}
            <Tabs
              label="Period"
              value={days}
              onChange={setDays}
              items={[
                { value: "1", label: "Today" },
                { value: "7", label: "7 days" },
                { value: "30", label: "30 days" },
                { value: "90", label: "90 days" },
              ]}
            />
          </div>
        }
      >
        {calls.loading && !calls.data ? (
          <LoadingRows />
        ) : calls.data?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Call</th>
                  <th>Outcome</th>
                  <th>Duration</th>
                  <th>By</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {calls.data.items.map((call) => (
                  <tr key={call.id}>
                    <td className="cell-title">
                      <div className="ui-row" style={{ gap: 8 }}>
                        <span className="type-icon">{call.direction === "inbound" ? <PhoneIncoming size={14} /> : <PhoneOutgoing size={14} />}</span>
                        <div>
                          {call.link ? <Link href={call.link}>{call.subject}</Link> : call.subject}
                          <div className="ui-muted ui-small">
                            {[call.phone, call.account_name].filter(Boolean).join(" · ")}
                            {call.notes ? ` — ${call.notes.slice(0, 90)}${call.notes.length > 90 ? "…" : ""}` : ""}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <Badge tone={OUTCOME_TONE[call.outcome] ?? "neutral"}>{titleCase(call.outcome.replace(/_/g, " "))}</Badge>
                    </td>
                    <td className="ui-num">{clock(call.duration_seconds)}</td>
                    <td>{call.by ?? "—"}</td>
                    <td className="ui-muted">{dateTime(call.occurred_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<PhoneCall size={20} />} title="No calls in this period" message="Use the Call button on any lead, contact or account, or start one here." />
        )}
      </Card>
      <PickContact
        open={picking}
        onClose={() => setPicking(false)}
        onPicked={(next) => {
          setPicking(false);
          setTarget(next);
        }}
      />
      {target ? (
        <CallDialog
          target={target}
          open
          onClose={() => setTarget(null)}
          onLogged={() => {
            setTarget(null);
            void calls.reload();
          }}
        />
      ) : null}
    </>
  );
}

function PickContact({ open, onClose, onPicked }: { open: boolean; onClose: () => void; onPicked: (target: CallTarget) => void }) {
  const toast = useToast();
  const [kind, setKind] = useState<"contact" | "lead" | "number">("contact");
  const contacts = useRefOptions(open ? "contact" : null);
  const leads = useRefOptions(open ? "lead" : null);
  const [recordId, setRecordId] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const options = kind === "contact" ? contacts : leads;

  async function next() {
    if (kind === "number") {
      onPicked({ name: phone, phone });
      return;
    }
    setBusy(true);
    try {
      const record = await api.get<{ first_name?: string; last_name?: string; full_name?: string; phone?: string | null; mobile?: string | null; mobile_phone?: string | null; account_id?: string | null }>(
        `/${kind}s/${recordId}`,
      );
      const name = record.full_name || `${record.first_name ?? ""} ${record.last_name ?? ""}`.trim() || "Unknown";
      onPicked({
        name,
        phone: record.phone || record.mobile || record.mobile_phone || null,
        ...(kind === "contact" ? { contact_id: recordId, account_id: record.account_id ?? undefined } : { lead_id: recordId }),
      });
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New call"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || (kind === "number" ? !phone.trim() : !recordId)} onClick={() => void next()}>
            Continue
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <Tabs
          label="Who"
          value={kind}
          onChange={(value) => {
            setKind(value);
            setRecordId("");
          }}
          items={[
            { value: "contact", label: "Contact" },
            { value: "lead", label: "Lead" },
            { value: "number", label: "Any number" },
          ]}
        />
        {kind === "number" ? (
          <Field label="Phone number">
            <input className="ui-input" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 415 555 0100" />
          </Field>
        ) : (
          <Field label={kind === "contact" ? "Contact" : "Lead"}>
            <select className="ui-select" value={recordId} onChange={(e) => setRecordId(e.target.value)}>
              <option value="">Choose…</option>
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        )}
      </div>
    </Modal>
  );
}
