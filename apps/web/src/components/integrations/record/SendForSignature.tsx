"use client";

import Link from "next/link";
import { FileText, LayoutTemplate, Plus, ReceiptText, Send, Trash2, UserPlus } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { Alert, Field, LoadingRows, Modal } from "@/components/ui";
import { money } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

import { bytes, ProviderMark, SIGN_PROVIDER_LOOK, SIGN_TAGS, type SignatureRequestItem, type SignProvider, type SignProviderKey } from "./documents-shared";

type Sources = {
  record: { label: string; kind: string };
  quotes: { id: string; label: string; status: string; total: string; currency: string; lines: number }[];
  can_send_quotes: boolean;
  documents: { id: string; name: string; content_type: string; size_bytes: number; signable: boolean; signable_by?: Partial<Record<SignProviderKey, boolean>> }[];
  recipients: { name: string; email: string; source: string }[];
  me: { name: string; email: string };
  /** Every e-signature provider with whether it's on and connected (older API: missing, meaning DocuSign). */
  providers?: SignProvider[];
};

type PandaTemplates = { items: { id: string; name: string; modified_at: string | null }[]; default_quote_template_id: string | null };
type PandaTemplate = { id: string; name: string; roles: { name: string; signing_order: number | null }[]; tokens: string[]; pricing_tables: string[] };

type Row = { key: number; name: string; email: string; role: "signer" | "cc"; routing_order: number };

/** "quote:<id>", "document:<id>" or "template" (a PandaDoc template on its own). */
type Source = string;

const DOCUSIGN_ONLY: SignProvider[] = [
  { key: "docusign", name: "DocuSign", enabled: true, connected: true, supports: { quotes: true, documents: true, templates: false, void: true, remind: true } },
];

function readyProviders(data: Sources): SignProvider[] {
  if (!data.providers) return DOCUSIGN_ONLY;
  return data.providers.filter((p) => p.enabled && p.connected);
}

/**
 * Send a quote or document for signature with DocuSign, Adobe Acrobat Sign or
 * PandaDoc (whichever are connected; the sender picks when there are several).
 *
 * Use from a record (deal, quote, account, contact, lead, document...):
 *   <SendForSignature open={open} onClose={() => setOpen(false)} recordType="quote" recordId={quote.id}
 *                     quoteId={quote.id} onSent={() => reload()} />
 * `quoteId` / `documentId` preselect what is sent; `provider` preselects the service.
 */
export function SendForSignature({
  open,
  onClose,
  recordType,
  recordId,
  quoteId,
  documentId,
  provider,
  onSent,
}: {
  open: boolean;
  onClose: () => void;
  recordType: string;
  recordId: string;
  quoteId?: string;
  documentId?: string;
  provider?: SignProviderKey;
  onSent?: (request: SignatureRequestItem) => void;
}) {
  const sources = useApi<Sources>(open ? `/e-signatures/sources${query({ record_type: recordType, record_id: recordId })}` : null);
  const [busy, setBusy] = useState(false);
  const data = open ? sources.data : null;
  const ready = data ? readyProviders(data) : [];
  const onlyOne = ready.length === 1 ? ready[0].name : null;
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Send for signature"
      description={data ? `From ${data.record.kind.toLowerCase()} ${data.record.label}${onlyOne ? `, with ${onlyOne}` : ""}.` : undefined}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="idoc-send-form" className="ui-btn ui-btn-primary" disabled={busy || !data || !ready.length}>
            {busy ? <span className="ui-spinner" /> : <Send size={15} />} Send
          </button>
        </>
      }
    >
      {sources.error ? <Alert>{sources.error}</Alert> : null}
      {!data && !sources.error ? <LoadingRows rows={5} /> : null}
      {data && !ready.length ? (
        <Alert tone="warning">
          No e-signature service is connected. An admin can connect DocuSign, Adobe Acrobat Sign or PandaDoc in <Link href="/integrations">Integrations</Link>.
        </Alert>
      ) : null}
      {data && ready.length ? (
        <SendForm
          data={data}
          ready={ready}
          recordType={recordType}
          recordId={recordId}
          quoteId={quoteId}
          documentId={documentId}
          initialProvider={provider}
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
  ready,
  recordType,
  recordId,
  quoteId,
  documentId,
  initialProvider,
  onBusy,
  onSent,
}: {
  data: Sources;
  ready: SignProvider[];
  recordType: string;
  recordId: string;
  quoteId?: string;
  documentId?: string;
  initialProvider?: SignProviderKey;
  onBusy: (busy: boolean) => void;
  onSent: (request: SignatureRequestItem) => void;
}) {
  const toast = useToast();
  const [provider, setProvider] = useState<SignProviderKey>(() => (initialProvider && ready.some((p) => p.key === initialProvider) ? initialProvider : ready[0].key));
  const docOk = (doc: Sources["documents"][number], key: SignProviderKey) => doc.signable_by?.[key] ?? doc.signable;
  const firstSource = (key: SignProviderKey): Source => {
    const firstQuote = data.quotes.find((q) => q.lines)?.id;
    const firstDoc = data.documents.find((d) => docOk(d, key))?.id;
    if (quoteId && data.quotes.some((q) => q.id === quoteId)) return `quote:${quoteId}`;
    if (documentId && data.documents.some((d) => d.id === documentId && docOk(d, key))) return `document:${documentId}`;
    return firstQuote ? `quote:${firstQuote}` : firstDoc ? `document:${firstDoc}` : key === "pandadoc" ? "template" : "";
  };
  const [source, setSource] = useState<Source>(() => firstSource(provider));
  const [templateId, setTemplateId] = useState("");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [rows, setRows] = useState<Row[]>(() => [
    { key: 0, name: data.recipients[0]?.name ?? "", email: data.recipients[0]?.email ?? "", role: "signer", routing_order: 1 },
  ]);
  const [ordered, setOrdered] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [counter, setCounter] = useState(1);

  const panda = provider === "pandadoc";
  const templates = useApi<PandaTemplates>(panda ? "/pandadoc/templates" : null);
  const usesTemplate = panda && (source === "template" || source.startsWith("quote:"));
  // A quote with no template picked uses the workspace's default quote template.
  const effectiveTemplate = usesTemplate ? templateId || (source === "template" ? "" : templates.data?.default_quote_template_id ?? "") : "";
  const template = useApi<PandaTemplate>(effectiveTemplate ? `/pandadoc/templates/${encodeURIComponent(effectiveTemplate)}` : null);
  const roles = effectiveTemplate ? template.data?.roles ?? [] : [];

  // Switching provider: keep the choice when that provider can take it, otherwise pick what it can.
  useEffect(() => {
    setSource((current) => {
      if (current === "template") return provider === "pandadoc" ? current : firstSource(provider);
      if (current.startsWith("document:")) {
        const doc = data.documents.find((d) => `document:${d.id}` === current);
        return doc && docOk(doc, provider) ? current : firstSource(provider);
      }
      return current || firstSource(provider);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const chosenName = (() => {
    if (source === "template") return templates.data?.items.find((t) => t.id === templateId)?.name ?? "";
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
    if (usesTemplate && !effectiveTemplate) {
      setError(source === "template" ? "Choose a PandaDoc template." : "Choose the PandaDoc template the quote goes into.");
      return;
    }
    const [kind, id] = source.split(":");
    onBusy(true);
    setError(null);
    try {
      const request = await api.post<SignatureRequestItem>("/e-signatures", {
        provider,
        record_type: recordType,
        record_id: recordId,
        quote_id: kind === "quote" ? id : null,
        document_id: kind === "document" ? id : null,
        template_id: usesTemplate && templateId ? templateId : null,
        subject: subject.trim() || null,
        message: message.trim() || null,
        recipients: rows
          .filter((r) => r.name.trim() || r.email.trim())
          .map((r) => ({ name: r.name.trim(), email: r.email.trim(), role: r.role, routing_order: ordered ? r.routing_order : 1 })),
      });
      toast(
        request.status === "created"
          ? `${request.provider_name} is preparing “${request.document_name}”. It goes out as soon as it's ready.`
          : `Sent “${request.document_name}” for signature with ${request.provider_name}.`,
      );
      onSent(request);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      onBusy(false);
    }
  }

  const suggestions = [...data.recipients, { ...data.me, source: "You" }].filter((p) => p.email && !rows.some((r) => r.email.toLowerCase() === p.email.toLowerCase()));
  const signableDocs = data.documents.filter((d) => docOk(d, provider));
  const anything = data.quotes.length + signableDocs.length + (panda ? 1 : 0);
  const look = SIGN_PROVIDER_LOOK[provider];
  const tags = SIGN_TAGS[provider];
  const signers = useMemo(() => rows.filter((r) => r.role === "signer"), [rows]);
  const tooManySigners = roles.length > 0 && signers.length > roles.length;

  return (
    <form id="idoc-send-form" className="ui-stack" style={{ gap: 16 }} onSubmit={(event) => void submit(event)}>
      {error ? <Alert>{error}</Alert> : null}

      {ready.length > 1 ? (
        <fieldset className="idoc-fieldset">
          <legend>Send with</legend>
          <div className="isal-providers" role="radiogroup" aria-label="E-signature service">
            {ready.map((p) => (
              <label key={p.key} className={`isal-provider${provider === p.key ? " on" : ""}`}>
                <input type="radio" name="sign-provider" checked={provider === p.key} onChange={() => setProvider(p.key)} />
                <ProviderMark provider={SIGN_PROVIDER_LOOK[p.key]} size={20} />
                <span>{p.name}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}

      <fieldset className="idoc-fieldset">
        <legend>What to send</legend>
        {!anything ? (
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
            {data.documents.map((doc) => {
              const ok = docOk(doc, provider);
              return (
                <label key={doc.id} className={`idoc-source${source === `document:${doc.id}` ? " on" : ""}${ok ? "" : " disabled"}`}>
                  <input type="radio" name="source" checked={source === `document:${doc.id}`} onChange={() => setSource(`document:${doc.id}`)} disabled={!ok} />
                  <FileText size={16} aria-hidden />
                  <span className="idoc-source-text">
                    <strong>{doc.name}</strong>
                    <span className="ui-muted ui-small">{ok ? bytes(doc.size_bytes) : `${look.name} can't take this file type or size`}</span>
                  </span>
                </label>
              );
            })}
            {panda ? (
              <label className={`idoc-source${source === "template" ? " on" : ""}`}>
                <input type="radio" name="source" checked={source === "template"} onChange={() => setSource("template")} />
                <LayoutTemplate size={16} aria-hidden />
                <span className="idoc-source-text">
                  <strong>A PandaDoc template</strong>
                  <span className="ui-muted ui-small">Filled in from this record&apos;s contact, account and deal</span>
                </span>
              </label>
            ) : null}
          </div>
        )}
        {usesTemplate ? (
          <div className="isal-template">
            <Field
              label={source === "template" ? "Template" : "Quote template"}
              hint={
                source === "template"
                  ? "Tokens like [Client.FirstName] and [Deal.Name] are filled in from the record."
                  : "The quote's lines go into the template's pricing table; tokens like [Quote.Total] are filled in."
              }
            >
              <select className="ui-select" value={templateId} onChange={(event) => setTemplateId(event.target.value)} disabled={templates.loading && !templates.data}>
                <option value="">
                  {source === "template"
                    ? "Choose a template"
                    : templates.data?.default_quote_template_id
                      ? `Workspace default (${templates.data.items.find((t) => t.id === templates.data?.default_quote_template_id)?.name ?? "quote template"})`
                      : "Choose a template"}
                </option>
                {(templates.data?.items ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </Field>
            {templates.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{templates.error}</p> : null}
            {roles.length ? (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Signers fill the template&apos;s roles in order: {roles.map((r) => r.name).join(", ")}.
              </p>
            ) : null}
            {template.data && !roles.length ? (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                This template has no roles, so signers are added as plain recipients.
              </p>
            ) : null}
          </div>
        ) : null}
        {source.startsWith("document:") ? (
          <p className="ui-muted ui-small" style={{ margin: "8px 0 0" }}>
            Tip: type <code>{tags.signature}</code> where signer 1 signs and <code>{tags.date}</code> for the date (<code>{tags.next}</code> for signer 2…).{" "}
            {provider === "pandadoc" ? "PandaDoc needs them: without them the document has no signature field." : "Without them, signers place their own signature."}
          </p>
        ) : null}
      </fieldset>

      <fieldset className="idoc-fieldset">
        <legend>Who signs</legend>
        <div className="idoc-recipients">
          {rows.map((row, index) => {
            const signerIndex = signers.findIndex((s) => s.key === row.key);
            const role = row.role === "signer" && signerIndex >= 0 ? roles[signerIndex]?.name : undefined;
            return (
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
                  <option value="signer">{role ? `Signs as ${role}` : "Signs"}</option>
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
            );
          })}
        </div>
        {tooManySigners ? (
          <p className="ui-small" style={{ margin: "8px 0 0", color: "var(--danger)" }}>
            The template has {roles.length} signer role{roles.length === 1 ? "" : "s"}; remove a signer or make them a copy.
          </p>
        ) : null}
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
