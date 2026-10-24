"use client";

import { ArrowUpRight, Link2, LifeBuoy, RefreshCw, Unlink } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { Alert, Card, ConfirmDialog, Field, Modal } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

import { DeskFields, deskDefaults, deskPayload, noun, TicketRow, type CaseLink, type Desk, type DeskFieldsValue } from "./support-kit";

type CaseTickets = { items: CaseLink[]; desks: Desk[]; can_escalate: boolean; contact_email: string | null };

const ORIGIN: Record<CaseLink["origin"], string> = { escalation: "Escalated", linked: "Linked", ticket: "Opened from this ticket" };

/**
 * Escalate an AIVA case to a help desk (Zendesk, Freshdesk, Intercom, Help Scout,
 * Jira Service Management), or link it to an existing ticket, and follow the
 * ticket's live status and message count.
 *
 *   <CaseEscalation caseId={c.id} onChanged={() => void caseData.reload()} />
 *
 * `onChanged` runs after escalating, linking or unlinking (each adds an internal
 * comment to the case). Renders nothing without the help desk plan feature, or
 * when no desk is on and the case has no linked tickets.
 */
export function CaseEscalation({ caseId, onChanged }: { caseId: string; onChanged?: () => void }) {
  const { has } = useSession();
  const toast = useToast();
  const enabled = has("helpdesk_sync");
  const data = useApi<CaseTickets>(enabled ? `/helpdesk/cases/${caseId}` : null);
  const [mode, setMode] = useState<"escalate" | "link" | null>(null);
  const [unlinking, setUnlinking] = useState<CaseLink | null>(null);
  const [busy, setBusy] = useState(false);
  const desks = useMemo(() => Object.fromEntries((data.data?.desks ?? []).map((d) => [d.key, d])) as Record<string, Desk>, [data.data]);
  if (!enabled) return null;
  const items = data.data?.items ?? [];
  const linkedKeys = new Set(items.map((i) => i.provider));
  const available = (data.data?.desks ?? []).filter((d) => d.ready && !linkedKeys.has(d.key));
  if (data.data && !items.length && !(data.data.desks ?? []).some((d) => d.ready)) return null;

  async function refresh() {
    setBusy(true);
    try {
      data.setData(await api.get<CaseTickets>(`/helpdesk/cases/${caseId}${query({ refresh: true })}`));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function unlink() {
    if (!unlinking) return;
    setBusy(true);
    try {
      await api.del(`/helpdesk/cases/${caseId}/links/${unlinking.link_id}`);
      toast(`Unlinked ${unlinking.provider_name} ${unlinking.number}.`);
      setUnlinking(null);
      await data.reload();
      onChanged?.();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  function done() {
    setMode(null);
    void data.reload();
    onChanged?.();
  }

  const canAct = Boolean(data.data?.can_escalate) && available.length > 0;

  return (
    <Card
      title="Help desk"
      icon={<LifeBuoy size={16} />}
      actions={
        <>
          {items.length ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => void refresh()} disabled={busy} aria-label="Refresh ticket status" title="Check the ticket's status now">
              {busy ? <span className="ui-spinner" /> : <RefreshCw size={14} />}
            </button>
          ) : null}
          {canAct ? (
            <>
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setMode("link")}>
                <Link2 size={14} /> Link
              </button>
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setMode("escalate")}>
                <ArrowUpRight size={14} /> Escalate
              </button>
            </>
          ) : null}
        </>
      }
    >
      {data.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{data.error}</p> : null}
      {!data.data ? (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>Loading…</p>
      ) : items.length ? (
        <ul className="mini-list isup-list">
          {items.map((item) => (
            <TicketRow
              key={item.link_id}
              item={item}
              desk={desks[item.provider]}
              meta={
                <>
                  {` · ${ORIGIN[item.origin] ?? "Linked"} ${relativeTime(item.linked_at)}`}
                  {item.error ? <span className="isup-link-error"> · {item.error}</span> : null}
                </>
              }
              actions={
                data.data?.can_escalate ? (
                  <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => setUnlinking(item)} aria-label={`Unlink ${item.number}`} title="Unlink from this case">
                    <Unlink size={14} />
                  </button>
                ) : null
              }
            />
          ))}
        </ul>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Not escalated. Escalate to {available.map((d) => d.name).join(" or ") || "a help desk"} when another team needs to take it on; its status shows here.
        </p>
      )}

      {mode === "escalate" ? <EscalateModal caseId={caseId} desks={available} contactEmail={data.data?.contact_email ?? null} onClose={() => setMode(null)} onDone={done} /> : null}
      {mode === "link" ? <LinkModal caseId={caseId} desks={available} onClose={() => setMode(null)} onDone={done} /> : null}
      <ConfirmDialog
        open={Boolean(unlinking)}
        title="Unlink this ticket?"
        message={<>The case stops following {unlinking?.provider_name} {unlinking?.number}. The ticket itself stays in {unlinking?.provider_name}.</>}
        confirmLabel="Unlink"
        busy={busy}
        onConfirm={() => void unlink()}
        onClose={() => setUnlinking(null)}
      />
    </Card>
  );
}

function DeskPicker({ desks, value, onChange }: { desks: Desk[]; value: string; onChange: (key: string) => void }) {
  if (desks.length < 2) return null;
  return (
    <Field label="Help desk" className="span-2">
      <select className="ui-select" value={value} onChange={(event) => onChange(event.target.value)}>
        {desks.map((d) => (
          <option key={d.key} value={d.key}>
            {d.name}
          </option>
        ))}
      </select>
    </Field>
  );
}

function EscalateModal({ caseId, desks, contactEmail, onClose, onDone }: { caseId: string; desks: Desk[]; contactEmail: string | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [deskKey, setDeskKey] = useState(desks[0]?.key ?? "");
  const desk = desks.find((d) => d.key === deskKey);
  const [requester, setRequester] = useState(contactEmail ?? "");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [fields, setFields] = useState<DeskFieldsValue>(() => deskDefaults(desks[0]));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!desk) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ item: CaseLink }>(`/helpdesk/cases/${caseId}/escalate`, {
        provider: desk.key,
        subject: subject.trim() || null,
        body: body.trim() || null,
        requester_email: requester.trim() && requester.trim() !== contactEmail ? requester.trim() : null,
        ...deskPayload(desk, fields),
      });
      toast(`Escalated to ${desk.name} ${noun(desk)} ${result.item.number}.`);
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Escalate this case"
      description={`Opens a ${noun(desk)} in ${desk?.name ?? "the help desk"} for the customer, links it to this case and notes it on the case.`}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="isup-escalate" className="ui-btn ui-btn-primary" disabled={busy || !desk || !requester.trim()}>
            {busy ? <span className="ui-spinner" /> : null} Escalate
          </button>
        </>
      }
    >
      <form id="isup-escalate" className="ui-form-grid" onSubmit={(event) => void submit(event)}>
        {error ? (
          <div className="span-2">
            <Alert>{error}</Alert>
          </div>
        ) : null}
        <DeskPicker
          desks={desks}
          value={deskKey}
          onChange={(key) => {
            setDeskKey(key as Desk["key"]);
            setFields(deskDefaults(desks.find((d) => d.key === key), fields.priority));
          }}
        />
        <Field label="Customer email" required className="span-2" hint={contactEmail ? "The case's contact." : "This case has no contact with an email address."}>
          <input className="ui-input" type="email" value={requester} onChange={(event) => setRequester(event.target.value)} placeholder="name@company.com" />
        </Field>
        <Field label="Subject" className="span-2" hint="Leave blank to use the case subject.">
          <input className="ui-input" value={subject} maxLength={250} onChange={(event) => setSubject(event.target.value)} />
        </Field>
        <Field label="Message" className="span-2" hint="Leave blank to send the case description.">
          <textarea className="ui-textarea" rows={4} value={body} maxLength={20000} onChange={(event) => setBody(event.target.value)} />
        </Field>
        {desk ? <DeskFields desk={desk} value={fields} onChange={setFields} /> : null}
      </form>
    </Modal>
  );
}

function LinkModal({ caseId, desks, onClose, onDone }: { caseId: string; desks: Desk[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [deskKey, setDeskKey] = useState(desks[0]?.key ?? "");
  const desk = desks.find((d) => d.key === deskKey);
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!desk) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ item: CaseLink }>(`/helpdesk/cases/${caseId}/link`, { provider: desk.key, reference: reference.trim() });
      toast(`Linked ${desk.name} ${result.item.number}.`);
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const example = desk?.key === "jira_service_management" ? "SUP-12 or its link" : `${noun(desk)} number or its link`;
  return (
    <Modal
      open
      onClose={onClose}
      size="sm"
      title="Link an existing ticket"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="isup-link" className="ui-btn ui-btn-primary" disabled={busy || !desk || !reference.trim()}>
            {busy ? <span className="ui-spinner" /> : null} Link
          </button>
        </>
      }
    >
      <form id="isup-link" className="ui-form-grid" onSubmit={(event) => void submit(event)}>
        {error ? (
          <div className="span-2">
            <Alert>{error}</Alert>
          </div>
        ) : null}
        <DeskPicker desks={desks} value={deskKey} onChange={(key) => setDeskKey(key as Desk["key"])} />
        <Field label={desk ? `${desk.name} ${noun(desk)}` : "Ticket"} required className="span-2">
          <input className="ui-input" value={reference} onChange={(event) => setReference(event.target.value)} placeholder={example} />
        </Field>
      </form>
    </Modal>
  );
}

export default CaseEscalation;
