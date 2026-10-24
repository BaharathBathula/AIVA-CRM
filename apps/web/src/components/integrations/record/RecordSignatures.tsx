"use client";

import Link from "next/link";
import { BellRing, Ban, ChevronDown, ChevronRight, FileCheck2, RefreshCw, Signature } from "lucide-react";
import { useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { Badge, Card, Field, Modal } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

import { ProviderMark, RECIPIENT_STATUS, SIGN_PROVIDER_LOOK, SIGNATURE_STATUS, SignatureBadge, type SignatureRequestItem, type SignProvider } from "./documents-shared";
import { SendForSignature } from "./SendForSignature";

type Listing = { items: SignatureRequestItem[]; enabled: boolean; connected: boolean; can_send: boolean; providers?: SignProvider[] };

/**
 * Signature requests for a record from every e-signature provider (DocuSign,
 * Adobe Acrobat Sign, PandaDoc), with "Send for signature".
 *
 * Wire it on deals, quotes, accounts, contacts, leads and documents:
 *   <RecordSignatures recordType="quote" recordId={quote.id} quoteId={quote.id} />
 *   <RecordSignatures recordType="opportunity" recordId={deal.id} />
 * `quoteId` / `documentId` preselect what is sent. Renders nothing when
 * every provider is off and nothing was ever sent from the record.
 */
export function RecordSignatures({
  recordType,
  recordId,
  quoteId,
  documentId,
  onChanged,
}: {
  recordType: string;
  recordId: string;
  quoteId?: string;
  documentId?: string;
  /** Called after sending, voiding or a status change (e.g. to reload a quote's status). */
  onChanged?: () => void;
}) {
  const { has, can } = useSession();
  const enabled = has("e_signature");
  const listing = useApi<Listing>(enabled ? `/e-signatures${query({ record_type: recordType, record_id: recordId })}` : null);
  const [sending, setSending] = useState(false);
  if (!enabled) return null;
  const data = listing.data;
  if (data && !data.enabled && !data.items.length) return null;
  const canSend = Boolean(data?.enabled && data.connected && can("records.write"));

  async function changed() {
    await listing.reload();
    onChanged?.();
  }

  return (
    <Card
      title="Signatures"
      icon={<Signature size={16} />}
      actions={
        canSend ? (
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setSending(true)}>
            <Signature size={14} /> Send for signature
          </button>
        ) : null
      }
    >
      {listing.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{listing.error}</p> : null}
      {data && data.enabled && !data.connected ? <NotConnected providers={data.providers} /> : null}
      {data?.items.length ? (
        <ul className="idoc-requests">
          {data.items.map((item) => (
            <SignatureRow key={item.id} item={item} onChanged={() => void changed()} />
          ))}
        </ul>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          {listing.loading && !data ? "Loading…" : canSend ? "Nothing sent for signature yet." : "No signature requests."}
        </p>
      )}
      <SendForSignature
        open={sending}
        onClose={() => setSending(false)}
        recordType={recordType}
        recordId={recordId}
        quoteId={quoteId}
        documentId={documentId}
        onSent={() => void changed()}
      />
    </Card>
  );
}

/** "X isn't connected yet. Set it up." for the providers that are on but not connected. */
function NotConnected({ providers }: { providers?: SignProvider[] }) {
  // Older API responses have no provider list: that was DocuSign only.
  const waiting = providers ? providers.filter((p) => p.enabled && !p.connected) : [{ key: "docusign" as const, name: "DocuSign" }];
  if (!waiting.length) return null;
  const names = waiting.map((p) => p.name);
  const label = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return (
    <p className="ui-muted ui-small" style={{ margin: "0 0 8px" }}>
      {label} {waiting.length === 1 ? "isn\u2019t" : "aren\u2019t"} connected yet. <Link href={`/integrations/${waiting[0].key}`}>Set it up</Link>.
    </p>
  );
}

/** One request: status, progress, recipients and its timeline; refresh, remind and void. */
export function SignatureRow({ item, onChanged, showRecord }: { item: SignatureRequestItem; onChanged: () => void; showRecord?: boolean }) {
  const { session, can } = useSession();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState("");
  const waiting = item.status === "sent" || item.status === "delivered" || item.status === "created";
  const mine = item.sent_by_user_id === session?.user?.id;
  const canManage = mine || can("records.delete") || can("admin.settings");
  const look = SIGN_PROVIDER_LOOK[item.provider] ?? SIGN_PROVIDER_LOOK.docusign;
  const canRemind = item.supports?.remind !== false;
  const canVoid = item.supports?.void !== false;

  async function act(name: "refresh" | "resend" | "void") {
    setBusy(name);
    try {
      if (name === "void") {
        await api.post(`/e-signatures/${item.id}/void`, { reason: reason.trim() });
        toast("Voided. Recipients can no longer sign.");
        setVoiding(false);
        setReason("");
      } else if (name === "resend") {
        await api.post(`/e-signatures/${item.id}/resend`);
        toast("Reminder sent.");
      } else {
        const next = await api.post<SignatureRequestItem>(`/e-signatures/${item.id}/refresh`);
        toast(next.status === item.status ? "No change yet." : `Now: ${(SIGNATURE_STATUS[next.status]?.label ?? next.status).toLowerCase()}.`);
      }
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className={`idoc-request status-${item.status}`}>
      <button type="button" className="idoc-request-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? <ChevronDown size={15} aria-hidden /> : <ChevronRight size={15} aria-hidden />}
        <span title={`Sent with ${item.provider_name ?? look.name}`} className="isal-request-mark">
          <ProviderMark provider={look} size={18} />
        </span>
        <span className="idoc-request-title">
          <strong>{item.document_name ?? item.subject}</strong>
          <span className="ui-muted ui-small">
            {item.signer_count ? `${item.signed_count} of ${item.signer_count} signed` : ""}
            {item.sent_at ? ` · sent ${relativeTime(item.sent_at)}` : ` · ${relativeTime(item.created_at)}`}
            {item.sent_by ? ` by ${item.sent_by}` : ""}
            {` · ${item.provider_name ?? look.name}`}
            {showRecord && item.record ? ` · ${item.record.kind} ${item.record.label}` : ""}
          </span>
        </span>
        <SignatureBadge status={item.status} />
      </button>
      {item.signer_count ? (
        <div className="idoc-progress" aria-hidden>
          <span style={{ width: `${Math.round((item.signed_count / item.signer_count) * 100)}%` }} />
        </div>
      ) : null}
      {open ? (
        <div className="idoc-request-body">
          {item.error && item.status !== "completed" ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{item.error}</p> : null}
          <ul className="idoc-recipient-list">
            {item.recipients.map((r) => {
              const info = RECIPIENT_STATUS[r.status] ?? { label: r.status, tone: "neutral" as const };
              return (
                <li key={r.recipient_id}>
                  <span className="idoc-order-dot" title={`Signing order ${r.routing_order}`}>
                    {r.routing_order}
                  </span>
                  <span className="idoc-recipient-text">
                    <strong>{r.name}</strong> <span className="ui-muted ui-small">{r.email}{r.role === "cc" ? " · gets a copy" : ""}</span>
                    {r.decline_reason ? <span className="ui-small" style={{ color: "var(--danger)" }}>“{r.decline_reason}”</span> : null}
                  </span>
                  <Badge tone={info.tone}>{info.label}</Badge>
                  <span className="ui-muted ui-small ui-nowrap">{r.signed_at ? dateTime(r.signed_at) : r.declined_at ? dateTime(r.declined_at) : r.delivered_at ? `opened ${relativeTime(r.delivered_at)}` : ""}</span>
                </li>
              );
            })}
          </ul>
          {item.timeline.length ? (
            <ol className="idoc-timeline">
              {item.timeline.map((event, index) => (
                <li key={`${event.at}-${index}`} className={`level-${event.level}`}>
                  <span className="idoc-timeline-dot" aria-hidden />
                  <span>{event.message}</span>
                  <span className="ui-muted ui-small ui-nowrap">{dateTime(event.at)}</span>
                </li>
              ))}
            </ol>
          ) : null}
          <div className="ui-actions idoc-request-actions">
            {item.signed_document_id ? (
              <Link className="ui-btn ui-btn-sm ui-btn-primary" href={`/documents/${item.signed_document_id}`}>
                <FileCheck2 size={14} /> Signed copy
              </Link>
            ) : null}
            {waiting && item.envelope_id ? (
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => void act("refresh")} disabled={busy !== null}>
                {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Check status
              </button>
            ) : null}
            {waiting && item.envelope_id && canRemind && can("records.write") ? (
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => void act("resend")} disabled={busy !== null}>
                {busy === "resend" ? <span className="ui-spinner" /> : <BellRing size={14} />} Remind
              </button>
            ) : null}
            {waiting && item.envelope_id && canVoid && canManage ? (
              <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost ui-btn-danger" onClick={() => setVoiding(true)} disabled={busy !== null}>
                <Ban size={14} /> Void
              </button>
            ) : null}
            {item.last_checked_at ? <span className="ui-muted ui-small">Checked {relativeTime(item.last_checked_at)}</span> : null}
          </div>
        </div>
      ) : null}
      <Modal
        open={voiding}
        onClose={() => setVoiding(false)}
        size="sm"
        title="Void this request?"
        description="Recipients get an email saying it was voided and can no longer sign."
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setVoiding(false)} disabled={busy === "void"}>
              Keep it
            </button>
            <button type="button" className="ui-btn ui-btn-danger solid" onClick={() => void act("void")} disabled={busy === "void" || reason.trim().length < 3}>
              {busy === "void" ? <span className="ui-spinner" /> : null} Void
            </button>
          </>
        }
      >
        <Field label="Reason" required hint="Shown to the recipients.">
          <input className="ui-input" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="For example: sent the wrong version" />
        </Field>
      </Modal>
    </li>
  );
}
