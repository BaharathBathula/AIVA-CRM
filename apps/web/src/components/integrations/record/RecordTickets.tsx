"use client";

import Link from "next/link";
import { LifeBuoy, Plus, RefreshCw } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { Alert, Card, Field, Modal, Tabs } from "@/components/ui";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

import { DeskFields, deskDefaults, deskPayload, noun, TicketRow, type Desk, type DeskFieldsValue, type TicketItem } from "./support-kit";

type RecordType = "contact" | "lead" | "account";

type RecordTicketsData = {
  items: TicketItem[];
  errors: Record<string, string>;
  emails: string[];
  desks: Desk[];
  contacts: { id: string; email: string; name: string }[];
  can_create: boolean;
};

/**
 * Help desk tickets and conversations (Zendesk, Freshdesk, Intercom, Help Scout,
 * Jira Service Management) for a contact, lead or account, with "New ticket".
 *
 *   <RecordTickets recordType="contact" recordId={contact.id} />
 *   <RecordTickets recordType="account" recordId={account.id} />
 *
 * Renders nothing without the help desk plan feature, or when no desk is on and
 * connected and nothing was ever found for the record.
 */
export function RecordTickets({ recordType, recordId, title = "Support tickets" }: { recordType: RecordType; recordId: string; title?: string }) {
  const { has } = useSession();
  const toast = useToast();
  const enabled = has("helpdesk_sync");
  const data = useApi<RecordTicketsData>(enabled ? `/helpdesk/records/${recordType}/${recordId}` : null);
  const [tab, setTab] = useState<"open" | "all">("open");
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const desks = useMemo(() => Object.fromEntries((data.data?.desks ?? []).map((d) => [d.key, d])) as Record<string, Desk>, [data.data]);
  if (!enabled) return null;
  const items = data.data?.items ?? [];
  const ready = (data.data?.desks ?? []).filter((d) => d.ready);
  if (data.data && !ready.length && !items.length) return null;
  const open = items.filter((i) => i.is_open);
  const shown = tab === "open" ? open : items;
  const errors = Object.entries(data.data?.errors ?? {});

  async function refresh() {
    setRefreshing(true);
    try {
      data.setData(await api.get<RecordTicketsData>(`/helpdesk/records/${recordType}/${recordId}${query({ refresh: true })}`));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <Card
      title={title}
      icon={<LifeBuoy size={16} />}
      actions={
        <>
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => void refresh()} disabled={refreshing || !ready.length} aria-label="Refresh tickets" title="Check the help desks again">
            {refreshing ? <span className="ui-spinner" /> : <RefreshCw size={14} />}
          </button>
          {data.data?.can_create && ready.length ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setCreating(true)}>
              <Plus size={14} /> New ticket
            </button>
          ) : null}
        </>
      }
    >
      {data.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{data.error}</p> : null}
      {errors.length ? (
        <div className="isup-errors">
          {errors.map(([key, message]) => (
            <p key={key} className="ui-small">
              {desks[key]?.name ?? key}: {message}
            </p>
          ))}
        </div>
      ) : null}
      {!data.data ? (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>Checking the help desks…</p>
      ) : (
        <>
          {items.length ? (
            <div className="isup-tabs">
              <Tabs
                label="Which tickets"
                value={tab}
                onChange={setTab}
                items={[
                  { value: "open", label: "Open", count: open.length },
                  { value: "all", label: "All", count: items.length },
                ]}
              />
            </div>
          ) : null}
          {shown.length ? (
            <ul className="mini-list isup-list">
              {shown.slice(0, 25).map((item) => (
                <TicketRow
                  key={`${item.provider}:${item.external_id}`}
                  item={item}
                  desk={desks[item.provider]}
                  meta={item.case_id ? <> · <Link href={`/cases/${item.case_id}`}>AIVA case</Link></> : null}
                />
              ))}
            </ul>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              {items.length
                ? "No open tickets. Everything is solved."
                : recordType !== "account" && !data.data.emails.length
                  ? `Add an email address to this ${recordType} to see their tickets.`
                  : `No tickets in ${ready.map((d) => d.name).join(", ") || "your help desks"}${data.data.emails.length ? ` for ${data.data.emails.slice(0, 2).join(", ")}${data.data.emails.length > 2 ? "…" : ""}` : ""}.`}
            </p>
          )}
          {shown.length > 25 ? <p className="ui-muted ui-small" style={{ margin: "8px 0 0" }}>Showing the 25 most recent.</p> : null}
        </>
      )}
      {creating && data.data ? (
        <NewTicketModal
          recordType={recordType}
          recordId={recordId}
          desks={ready}
          contacts={data.data.contacts}
          emails={data.data.emails}
          onClose={() => setCreating(false)}
          onCreated={(ticket) => {
            setCreating(false);
            setTab("open");
            data.setData((current) => (current ? { ...current, items: [ticket, ...current.items.filter((i) => !(i.provider === ticket.provider && i.external_id === ticket.external_id))] } : current));
          }}
        />
      ) : null}
    </Card>
  );
}

function NewTicketModal({
  recordType,
  recordId,
  desks,
  contacts,
  emails,
  onClose,
  onCreated,
}: {
  recordType: RecordType;
  recordId: string;
  desks: Desk[];
  contacts: RecordTicketsData["contacts"];
  emails: string[];
  onClose: () => void;
  onCreated: (ticket: TicketItem) => void;
}) {
  const toast = useToast();
  const [deskKey, setDeskKey] = useState(desks[0]?.key ?? "");
  const desk = desks.find((d) => d.key === deskKey);
  const [requester, setRequester] = useState(contacts[0]?.email ?? "");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [fields, setFields] = useState<DeskFieldsValue>(() => deskDefaults(desks[0]));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noEmail = recordType !== "account" && !emails.length;

  function pickDesk(key: string) {
    setDeskKey(key as Desk["key"]);
    setFields(deskDefaults(desks.find((d) => d.key === key), fields.priority));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!desk) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ ticket: TicketItem }>("/helpdesk/tickets", {
        provider: desk.key,
        record_type: recordType,
        record_id: recordId,
        subject: subject.trim(),
        body: body.trim(),
        requester_email: recordType === "account" ? requester : null,
        ...deskPayload(desk, fields),
      });
      toast(`${desk.name} ${noun(desk)} ${result.ticket.number} opened.`);
      onCreated(result.ticket);
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
      title={`New ${noun(desk)}`}
      description={desk?.key === "intercom" ? "Sends the message to the person from your Intercom inbox, which starts a conversation." : "Opens it in the help desk on the person's behalf."}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="isup-new-ticket" className="ui-btn ui-btn-primary" disabled={busy || noEmail || !desk || !subject.trim() || !body.trim() || (recordType === "account" && !requester)}>
            {busy ? <span className="ui-spinner" /> : null} Open {noun(desk)}
          </button>
        </>
      }
    >
      <form id="isup-new-ticket" className="ui-form-grid" onSubmit={(event) => void submit(event)}>
        {error ? (
          <div className="span-2">
            <Alert>{error}</Alert>
          </div>
        ) : null}
        {noEmail ? (
          <div className="span-2">
            <Alert tone="warning">Add an email address to this {recordType} first; the help desk needs one.</Alert>
          </div>
        ) : null}
        {desks.length > 1 ? (
          <Field label="Help desk" className={recordType === "account" ? undefined : "span-2"}>
            <select className="ui-select" value={deskKey} onChange={(event) => pickDesk(event.target.value)}>
              {desks.map((d) => (
                <option key={d.key} value={d.key}>
                  {d.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {recordType === "account" ? (
          <Field label="For" required hint={contacts.length ? undefined : "Add a contact with an email address to this account first."}>
            <select className="ui-select" value={requester} onChange={(event) => setRequester(event.target.value)}>
              {contacts.map((c) => (
                <option key={c.id} value={c.email}>
                  {c.name ? `${c.name} · ${c.email}` : c.email}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label="Subject" required className="span-2">
          <input className="ui-input" value={subject} maxLength={250} onChange={(event) => setSubject(event.target.value)} />
        </Field>
        <Field label="Message" required className="span-2">
          <textarea className="ui-textarea" rows={5} value={body} maxLength={20000} onChange={(event) => setBody(event.target.value)} />
        </Field>
        {desk ? <DeskFields desk={desk} value={fields} onChange={setFields} /> : null}
      </form>
    </Modal>
  );
}

export default RecordTickets;
