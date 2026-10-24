"use client";

import { FileText, Plus, ReceiptText, Send, Trash2, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { Alert, Field, LoadingRows, Modal } from "@/components/ui";
import { money } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

import { bytes, type SignatureRequestItem } from "./documents-shared";

type Sources = {
  record: { label: string; kind: string };
  quotes: { id: string; label: string; status: string; total: string; currency: string; lines: number }[];
  can_send_quotes: boolean;
  documents: { id: string; name: string; content_type: string; size_bytes: number; signable: boolean }[];
  recipients: { name: string; email: string; source: string }[];
  me: { name: string; email: string };
};

type Row = { key: number; name: string; email: string; role: "signer" | "cc"; routing_order: number };

/**
 * Send a quote or document for signature with DocuSign.
 *
 * Use from a record (deal, quote, account, contact, lead, document...):
 *   <SendForSignature open={open} onClose={() => setOpen(false)} recordType="quote" recordId={quote.id}
 *                     quoteId={quote.id} onSent={() => reload()} />
 * `quoteId` / `documentId` preselect what is sent.
 */
export function SendForSignature({
  open,
  onClose,
  recordType,
  recordId,
  quoteId,
  documentId,
  onSent,
}: {
  open: boolean;
  onClose: () => void;
  recordType: string;
  recordId: string;
  quoteId?: string;
  documentId?: string;
  onSent?: (request: SignatureRequestItem) => void;
}) {
  const sources = useApi<Sources>(open ? `/e-signatures/sources${query({ record_type: recordType, record_id: recordId })}` : null);
  const [busy, setBusy] = useState(false);
  const data = open ? sources.data : null;
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Send for signature"
      description={data ? `From ${data.record.kind.toLowerCase()} ${data.record.label}, with DocuSign.` : "With DocuSign."}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="idoc-send-form" className="ui-btn ui-btn-primary" disabled={busy || !data}>
            {busy ? <span className="ui-spinner" /> : <Send size={15} />} Send
          </button>
        </>
      }
    >
      {sources.error ? <Alert>{sources.error}</Alert> : null}
      {!data && !sources.error ? <LoadingRows rows={5} /> : null}
      {data ? (
        <SendForm
          data={data}
          recordType={recordType}
          recordId={recordId}
          quoteId={quoteId}
          documentId={documentId}
          onBusy={setBusy}
          onSent={(request) => {
            onSent?.(request);
            onClose();
          }}
        />
      ) : null}
    </Modal>
  );
}

function SendForm({
  data,
  recordType,
  recordId,
  quoteId,
  documentId,
  onBusy,
  onSent,
}: {
  data: Sources;
  recordType: string;
  recordId: string;
  quoteId?: string;
  documentId?: string;
  onBusy: (busy: boolean) => void;
  onSent: (request: SignatureRequestItem) => void;
}) {
  const toast = useToast();
  const [source, setSource] = useState<string>(() => {
    const firstQuote = data.quotes.find((q) => q.lines)?.id;
    const firstDoc = data.documents.find((d) => d.signable)?.id;
    if (quoteId && data.quotes.some((q) => q.id === quoteId)) return `quote:${quoteId}`;
    if (documentId && data.documents.some((d) => d.id === documentId && d.signable)) return `document:${documentId}`;
    return firstQuote ? `quote:${firstQuote}` : firstDoc ? `document:${firstDoc}` : "";
  });
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [rows, setRows] = useState<Row[]>(() => [
    { key: 0, name: data.recipients[0]?.name ?? "", email: data.recipients[0]?.email ?? "", role: "signer", routing_order: 1 },
  ]);
  const [ordered, setOrdered] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [counter, setCounter] = useState(1);

  const chosenName = (() => {
    if (!source) return "";
    const [kind, id] = source.split(":");
    return kind === "quote" ? data.quotes.find((q) => q.id === id)?.label ?? "" : data.documents.find((d) => d.id === id)?.name ?? "";
  })();

  function add(person?: { name: string; email: string }, role: Row["role"] = "signer") {
    setRows((current) => {
      if (person && current.some((r) => r.email.toLowerCase() === person.email.toLowerCase())) return current;
      const blank = current.findIndex((r) => !r.name && !r.email);
      const next = { key: counter, name: person?.name ?? "", email: person?.email ?? "", role, routing_order: ordered ? current.length + 1 : 1 };
      return person && blank >= 0 ? current.map((r, i) => (i === blank ? next : r)) : [...current, next];
    });
    setCounter((c) => c + 1);
  }

  function update(key: number, patch: Partial<Row>) {
    setRows((current) => current.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!source) {
      setError("Choose what to send.");
      return;
    }
    const [kind, id] = source.split(":");
    onBusy(true);
    setError(null);
    try {
      const request = await api.post<SignatureRequestItem>("/e-signatures", {
        record_type: recordType,
        record_id: recordId,
        quote_id: kind === "quote" ? id : null,
        document_id: kind === "document" ? id : null,
        subject: subject.trim() || null,
        message: message.trim() || null,
        recipients: rows
          .filter((r) => r.name.trim() || r.email.trim())
          .map((r) => ({ name: r.name.trim(), email: r.email.trim(), role: r.role, routing_order: ordered ? r.routing_order : 1 })),
      });
      toast(`Sent “${request.document_name}” for signature.`);
      onSent(request);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      onBusy(false);
    }
  }

  const suggestions = [...data.recipients, { ...data.me, source: "You" }].filter((p) => p.email && !rows.some((r) => r.email.toLowerCase() === p.email.toLowerCase()));
  const signable = data.quotes.length + data.documents.filter((d) => d.signable).length;

  return (
    <form id="idoc-send-form" className="ui-stack" style={{ gap: 16 }} onSubmit={(event) => void submit(event)}>
      {error ? <Alert>{error}</Alert> : null}
      <fieldset className="idoc-fieldset">
        <legend>What to send</legend>
        {!signable ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Nothing to send yet. Upload a PDF or Word document to this record{data.can_send_quotes ? " or create a quote" : ""} first.
          </p>
        ) : (
          <div className="idoc-sources">
            {data.quotes.map((quote) => (
              <label key={quote.id} className={`idoc-source${source === `quote:${quote.id}` ? " on" : ""}`}>
                <input type="radio" name="source" checked={source === `quote:${quote.id}`} onChange={() => setSource(`quote:${quote.id}`)} disabled={!quote.lines} />
                <ReceiptText size={16} aria-hidden />
                <span className="idoc-source-text">
                  <strong>{quote.label}</strong>
                  <span className="ui-muted ui-small">
                    {quote.lines ? `${money(quote.total, quote.currency)} · ${quote.status}` : "No line items yet"}
                  </span>
                </span>
              </label>
            ))}
            {data.documents.map((doc) => (
              <label key={doc.id} className={`idoc-source${source === `document:${doc.id}` ? " on" : ""}${doc.signable ? "" : " disabled"}`}>
                <input type="radio" name="source" checked={source === `document:${doc.id}`} onChange={() => setSource(`document:${doc.id}`)} disabled={!doc.signable} />
                <FileText size={16} aria-hidden />
                <span className="idoc-source-text">
                  <strong>{doc.name}</strong>
                  <span className="ui-muted ui-small">{doc.signable ? bytes(doc.size_bytes) : "DocuSign can't take this file type or size"}</span>
                </span>
              </label>
            ))}
          </div>
        )}
        {source.startsWith("document:") ? (
          <p className="ui-muted ui-small" style={{ margin: "8px 0 0" }}>
            Tip: type <code>/sn1/</code> where signer 1 signs and <code>/ds1/</code> for the date (use <code>/sn2/</code> for signer 2…). Without them, signers place their own signature.
          </p>
        ) : null}
      </fieldset>

      <fieldset className="idoc-fieldset">
        <legend>Who signs</legend>
        <div className="idoc-recipients">
          {rows.map((row, index) => (
            <div key={row.key} className="idoc-recipient">
              {ordered ? (
                <input
                  className="ui-input sm idoc-order"
                  type="number"
                  min={1}
                  max={20}
                  aria-label={`Signing order for recipient ${index + 1}`}
                  value={row.routing_order}
                  onChange={(event) => update(row.key, { routing_order: Math.max(1, Math.min(20, Number(event.target.value) || 1)) })}
                />
              ) : null}
              <input className="ui-input sm" placeholder="Name" aria-label={`Name of recipient ${index + 1}`} value={row.name} onChange={(event) => update(row.key, { name: event.target.value })} required />
              <input
                className="ui-input sm"
                type="email"
                placeholder="Email"
                aria-label={`Email of recipient ${index + 1}`}
                value={row.email}
                onChange={(event) => update(row.key, { email: event.target.value })}
                required
              />
              <select className="ui-select sm" aria-label={`Role of recipient ${index + 1}`} value={row.role} onChange={(event) => update(row.key, { role: event.target.value as Row["role"] })}>
                <option value="signer">Signs</option>
                <option value="cc">Gets a copy</option>
              </select>
              <button
                type="button"
                className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                aria-label={`Remove recipient ${index + 1}`}
                disabled={rows.length === 1}
                onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
        <div className="idoc-recipient-tools">
          <button type="button" className="ui-btn ui-btn-sm" onClick={() => add()} disabled={rows.length >= 20}>
            <Plus size={13} /> Add recipient
          </button>
          <label className="ui-check ui-small">
            <input
              type="checkbox"
              checked={ordered}
              onChange={(event) => {
                setOrdered(event.target.checked);
                if (event.target.checked) setRows((current) => current.map((r, i) => ({ ...r, routing_order: i + 1 })));
              }}
            />
            Set signing order
          </label>
        </div>
        {suggestions.length ? (
          <div className="idoc-suggestions" aria-label="Suggested recipients">
            {suggestions.slice(0, 6).map((person) => (
              <button key={person.email} type="button" className="idoc-suggestion" onClick={() => add(person, person.source === "You" ? "cc" : "signer")}>
                <UserPlus size={13} aria-hidden /> {person.name} <span className="ui-muted">· {person.source === "You" ? "you, as a copy" : person.source.toLowerCase()}</span>
              </button>
            ))}
          </div>
        ) : null}
      </fieldset>

      <div className="ui-form-grid">
        <Field label="Email subject" hint="Up to 100 characters." className="span-2">
          <input className="ui-input" maxLength={100} value={subject} placeholder={chosenName ? `Please sign: ${chosenName}`.slice(0, 100) : "Please sign"} onChange={(event) => setSubject(event.target.value)} />
        </Field>
        <Field label="Message" className="span-2">
          <textarea className="ui-textarea" rows={3} maxLength={10000} value={message} placeholder="Add a short note for the signers (optional)." onChange={(event) => setMessage(event.target.value)} />
        </Field>
      </div>
    </form>
  );
}
