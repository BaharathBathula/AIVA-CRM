"use client";

/**
 * Shared pieces for the documents, e-signature and scheduling integrations:
 * shapes returned by /cloud-files, /e-signatures and /calendly
 * (apps/api/app/api/routes/int_documents.py), the cloud file browser and the
 * signature status badge.
 */

import { ChevronRight, ExternalLink, FileText, Folder, Plus, Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useConnect } from "@/components/integrations/common";
import { Alert, Badge, EmptyState, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, query, useDebounced } from "@/lib/use-api";

// ---------------------------------------------------------------- Types

export type FileProvider = {
  key: "google_drive" | "onedrive" | "dropbox";
  name: string;
  vendor: string;
  color: string;
  monogram: string;
  available: boolean;
  enabled: boolean;
  connected: boolean;
  status: string;
  account_label: string | null;
  allow_copy: boolean;
};

export type FileItem = {
  id: string;
  name: string;
  mime_type: string | null;
  size: number | null;
  modified_at: string | null;
  web_url: string | null;
  is_folder: boolean;
  kind: string;
  owner: string | null;
  path: string | null;
  copyable: boolean;
};

export type RecordRef = { type: string; id: string; label: string; href: string; kind: string };

export type FileLink = {
  id: string;
  provider: FileProvider["key"];
  file_id: string;
  name: string;
  mime_type: string | null;
  kind: string;
  is_folder: boolean;
  size: number | null;
  web_url: string | null;
  modified_at: string | null;
  record_type: string;
  record_id: string;
  record: RecordRef | null;
  added_by_user_id: string | null;
  added_by: string | null;
  added_at: string;
  can_remove?: boolean;
};

export type SignatureStatus = "created" | "sent" | "delivered" | "completed" | "declined" | "voided" | "error";

export type SignatureRecipient = {
  recipient_id: string;
  name: string;
  email: string;
  role: "signer" | "cc";
  routing_order: number;
  status: string;
  delivered_at: string | null;
  signed_at: string | null;
  declined_at: string | null;
  decline_reason: string | null;
};

export type SignatureEvent = { action: string; message: string; level: string; at: string; signed_document_id?: string | null };

/** E-signature providers AIVA can send with (see apps/api/app/integrations/providers/_signing.py). */
export type SignProviderKey = "docusign" | "adobe_sign" | "pandadoc";

/** What a provider can do: send quotes / documents, start from its own templates, void, send reminders. */
export type SignSupports = { quotes: boolean; documents: boolean; templates: boolean; void: boolean; remind: boolean };

export type SignProvider = { key: SignProviderKey; name: string; enabled: boolean; connected: boolean; supports: SignSupports };

/** Brand marks for the e-signature providers (match the integration catalog). */
export const SIGN_PROVIDER_LOOK: Record<SignProviderKey, { name: string; color: string; monogram: string }> = {
  docusign: { name: "DocuSign", color: "#ffc820", monogram: "D" },
  adobe_sign: { name: "Adobe Acrobat Sign", color: "#eb1000", monogram: "AS" },
  pandadoc: { name: "PandaDoc", color: "#25b07c", monogram: "PD" },
};

/** How to mark where each signer signs in an uploaded document, per provider. */
export const SIGN_TAGS: Record<SignProviderKey, { signature: string; date: string; next: string }> = {
  docusign: { signature: "/sn1/", date: "/ds1/", next: "/sn2/" },
  adobe_sign: { signature: "{{Sig1_es_:signer1:signature}}", date: "{{Dte1_es_:signer1:date}}", next: "{{Sig2_es_:signer2:signature}}" },
  pandadoc: { signature: "{signature:Signer1}", date: "{date:Signer1}", next: "{signature:Signer2}" },
};

export type SignatureRequestItem = {
  id: string;
  /** Which provider sent it; older rows are always DocuSign. */
  provider: SignProviderKey;
  provider_name: string;
  supports: Partial<SignSupports>;
  envelope_id: string | null;
  subject: string;
  message: string | null;
  status: SignatureStatus;
  document_name: string | null;
  document_id: string | null;
  quote_id: string | null;
  record_type: string | null;
  record_id: string | null;
  record: RecordRef | null;
  recipients: SignatureRecipient[];
  signed_count: number;
  signer_count: number;
  sent_by: string | null;
  sent_by_user_id: string | null;
  sent_at: string | null;
  completed_at: string | null;
  last_checked_at: string | null;
  error: string | null;
  signed_document_id: string | null;
  timeline: SignatureEvent[];
  created_at: string;
};

export type CalendlyEventType = {
  uri: string;
  name: string;
  scheduling_url: string;
  duration: number | null;
  kind: string | null;
  color: string | null;
  description: string | null;
  owner: string | null;
  share_url?: string | null;
};

export type CalendlyLinks = {
  scope: "mine" | "organization" | "connected_user";
  scheduling_url: string | null;
  items: CalendlyEventType[];
  person?: { name: string; email: string } | null;
};

// ---------------------------------------------------------------- Small pieces

export function ProviderMark({ provider, size = 22 }: { provider: Pick<FileProvider, "color" | "monogram" | "name">; size?: number }) {
  return (
    <span className="int-tile idoc-mark" aria-hidden style={{ width: size, height: size, background: provider.color, fontSize: size * 0.48, borderRadius: Math.round(size * 0.3) }}>
      {provider.monogram || provider.name.slice(0, 1)}
    </span>
  );
}

export function bytes(size: number | null | undefined) {
  if (size === null || size === undefined) return null;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

export const SIGNATURE_STATUS: Record<SignatureStatus, { label: string; tone: Tone }> = {
  created: { label: "Preparing", tone: "neutral" },
  sent: { label: "Waiting for signature", tone: "info" },
  delivered: { label: "Viewed", tone: "primary" },
  completed: { label: "Signed", tone: "success" },
  declined: { label: "Declined", tone: "danger" },
  voided: { label: "Voided", tone: "neutral" },
  error: { label: "Didn't send", tone: "danger" },
};

export function SignatureBadge({ status }: { status: SignatureStatus }) {
  const info = SIGNATURE_STATUS[status] ?? SIGNATURE_STATUS.created;
  return (
    <Badge tone={info.tone} dot>
      {info.label}
    </Badge>
  );
}

export const RECIPIENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  created: { label: "Waiting their turn", tone: "neutral" },
  sent: { label: "Email sent", tone: "info" },
  delivered: { label: "Opened", tone: "primary" },
  signed: { label: "Signed", tone: "success" },
  completed: { label: "Done", tone: "success" },
  declined: { label: "Declined", tone: "danger" },
  autoresponded: { label: "Email bounced", tone: "warning" },
};

// ---------------------------------------------------------------- File browser

type Crumb = { id: string | null; name: string };
type View = "files" | "recent" | "shared";

const ROOT_NAME: Record<FileProvider["key"], string> = { google_drive: "My Drive", onedrive: "My files", dropbox: "Dropbox" };
const VIEWS: Record<FileProvider["key"], View[]> = { google_drive: ["files", "recent", "shared"], onedrive: ["files", "recent", "shared"], dropbox: ["files"] };
const VIEW_LABEL: Record<View, string> = { files: "Files", recent: "Recent", shared: "Shared with me" };

/** Browse and search the signed-in person's Drive, OneDrive or Dropbox. */
export function FileBrowser({
  providers,
  onPick,
  pickLabel = "Attach",
  picked,
  returnTo,
}: {
  providers: FileProvider[];
  /** Called with a file or folder; omit for a browse-only view. */
  onPick?: (provider: FileProvider, item: FileItem) => Promise<void>;
  pickLabel?: string;
  /** "provider:file id" pairs already attached (shown as attached). */
  picked?: Set<string>;
  /** Where to come back to after connecting an account. */
  returnTo?: string;
}) {
  const usable = providers.filter((p) => p.available && p.enabled);
  const [key, setKey] = useState<FileProvider["key"] | null>(() => (usable.find((p) => p.connected) ?? usable[0])?.key ?? null);
  const provider = usable.find((p) => p.key === key) ?? null;
  const [view, setView] = useState<View>("files");
  const [term, setTerm] = useState("");
  const search = useDebounced(term.trim(), 350);
  const [trail, setTrail] = useState<Crumb[]>([]);
  const [items, setItems] = useState<FileItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const folder = trail.length ? trail[trail.length - 1].id : null;
  const providerKey = provider?.key ?? null;
  const providerConnected = Boolean(provider?.connected);
  const latest = useRef(0);

  const load = useCallback(
    async (next: string | null) => {
      if (!providerKey || !providerConnected) return;
      // Only the newest request may change the list (typing quickly fires several).
      const ticket = ++latest.current;
      setLoading(true);
      setError(null);
      try {
        const page = await api.get<{ items: FileItem[]; cursor: string | null }>(
          `/cloud-files/${providerKey}/browse${query({ q: search || null, folder_id: search ? null : folder, view: search || folder ? "files" : view, cursor: next })}`,
        );
        if (ticket !== latest.current) return;
        setItems((current) => (next ? [...current, ...page.items] : page.items));
        setCursor(page.cursor);
      } catch (caught) {
        if (ticket !== latest.current) return;
        setError(errorMessage(caught));
        if (!next) setItems([]);
      } finally {
        if (ticket === latest.current) setLoading(false);
      }
    },
    [providerKey, providerConnected, search, folder, view],
  );

  useEffect(() => {
    void load(null);
  }, [load]);

  if (!usable.length) {
    return (
      <EmptyState
        title="No file storage is turned on"
        message="An admin can turn on Google Drive, OneDrive or Dropbox in Integrations."
      />
    );
  }

  function choose(next: FileProvider["key"]) {
    setKey(next);
    setTrail([]);
    setView("files");
    setTerm("");
    setItems([]);
    setCursor(null);
  }

  async function pick(item: FileItem) {
    if (!onPick || !provider) return;
    setBusy(item.id);
    try {
      await onPick(provider, item);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="idoc-browser">
      {usable.length > 1 ? (
        <div className="idoc-providers" role="tablist" aria-label="File service">
          {usable.map((p) => (
            <button key={p.key} type="button" role="tab" aria-selected={p.key === key} className={`idoc-provider${p.key === key ? " on" : ""}`} onClick={() => choose(p.key)}>
              <ProviderMark provider={p} size={18} />
              {p.name}
              {!p.connected ? <span className="ui-muted ui-small">· not connected</span> : null}
            </button>
          ))}
        </div>
      ) : null}

      {provider && !provider.connected ? <ConnectPrompt provider={provider} returnTo={returnTo} /> : null}

      {provider?.connected ? (
        <>
          <div className="idoc-toolbar">
            <label className="idoc-search">
              <Search size={15} aria-hidden />
              <input value={term} onChange={(event) => setTerm(event.target.value)} placeholder={`Search ${provider.name}`} aria-label={`Search ${provider.name}`} />
            </label>
            {VIEWS[provider.key].length > 1 && !term ? (
              <div className="idoc-views" role="tablist" aria-label="Show">
                {VIEWS[provider.key].map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="tab"
                    aria-selected={view === v && !trail.length}
                    className={`idoc-view${view === v && !trail.length ? " on" : ""}`}
                    onClick={() => {
                      setView(v);
                      setTrail([]);
                    }}
                  >
                    {VIEW_LABEL[v]}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {!term && (trail.length || view === "files") ? (
            <nav className="idoc-crumbs" aria-label="Folder">
              <button type="button" onClick={() => setTrail([])} disabled={!trail.length}>
                {ROOT_NAME[provider.key]}
              </button>
              {trail.map((crumb, index) => (
                <span key={`${crumb.id}-${index}`} className="idoc-crumb">
                  <ChevronRight size={13} aria-hidden />
                  <button type="button" onClick={() => setTrail(trail.slice(0, index + 1))} disabled={index === trail.length - 1}>
                    {crumb.name}
                  </button>
                </span>
              ))}
            </nav>
          ) : null}

          {error ? <Alert>{error}</Alert> : null}

          <ul className="idoc-files" aria-busy={loading}>
            {items.map((item) => {
              const attached = picked?.has(`${provider.key}:${item.id}`);
              return (
                <li key={item.id} className="idoc-file">
                  {item.is_folder ? <Folder size={18} className="idoc-file-icon folder" aria-hidden /> : <FileText size={18} className="idoc-file-icon" aria-hidden />}
                  <div className="idoc-file-main">
                    {item.is_folder && !term ? (
                      <button type="button" className="idoc-file-name as-link" onClick={() => setTrail([...trail, { id: item.id, name: item.name }])}>
                        {item.name}
                      </button>
                    ) : (
                      <span className="idoc-file-name">{item.name}</span>
                    )}
                    <span className="idoc-file-meta">
                      {[item.kind, bytes(item.size), item.modified_at ? `edited ${relativeTime(item.modified_at)}` : null, item.owner].filter(Boolean).join(" · ")}
                    </span>
                  </div>
                  <div className="idoc-file-actions">
                    {item.web_url ? (
                      <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={item.web_url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${item.name} in ${provider.name}`}>
                        <ExternalLink size={14} />
                      </a>
                    ) : null}
                    {onPick ? (
                      attached ? (
                        <span className="ui-small ui-muted idoc-attached">Attached</span>
                      ) : (
                        <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void pick(item)}>
                          {busy === item.id ? <span className="ui-spinner" /> : <Plus size={13} />} {pickLabel}
                        </button>
                      )
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
          {!loading && !items.length && !error ? (
            <p className="ui-muted ui-small idoc-empty">{term ? "Nothing matches that search." : trail.length ? "This folder is empty." : "Nothing here yet."}</p>
          ) : null}
          {loading ? (
            <div className="idoc-loading">
              <span className="ui-spinner" /> Loading…
            </div>
          ) : cursor ? (
            <button type="button" className="ui-btn ui-btn-sm idoc-more" onClick={() => void load(cursor)}>
              Load more
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function ConnectPrompt({ provider, returnTo }: { provider: FileProvider; returnTo?: string }) {
  const { connect, busy } = useConnect(provider.key);
  return (
    <div className="idoc-connect">
      <ProviderMark provider={provider} size={36} />
      <div>
        <strong>Connect your {provider.name}</strong>
        <p className="ui-muted ui-small" style={{ margin: "2px 0 0" }}>
          {provider.status === "needs_reauth"
            ? `Your ${provider.name} sign-in expired. Reconnect to keep browsing your files.`
            : `Each person connects their own account; you'll only see files your ${provider.vendor} account can open.`}
        </p>
      </div>
      <button
        type="button"
        className="ui-btn ui-btn-primary ui-btn-sm"
        disabled={busy}
        onClick={() => void connect(returnTo ?? (typeof window !== "undefined" ? window.location.pathname + window.location.search : undefined))}
      >
        {busy ? <span className="ui-spinner" /> : null} {provider.status === "needs_reauth" ? "Reconnect" : "Connect"}
      </button>
    </div>
  );
}
