"use client";

import Link from "next/link";
import { ArrowDownToLine, Cloud, ExternalLink, Folder, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { Card, ConfirmDialog, Modal } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

import { FileBrowser, ProviderMark, bytes, type FileItem, type FileLink, type FileProvider } from "./documents-shared";

type Providers = { items: FileProvider[]; max_mb: number };

/**
 * Google Drive, OneDrive and Dropbox files attached to a record.
 *
 * Wire it on any record that supports Documents (account, contact, lead,
 * opportunity, quote, case, invoice, sales_order, purchase_order, vendor,
 * price_book, project, appointment, meeting):
 *   <RecordCloudFiles recordType="opportunity" recordId={deal.id} />
 * It renders nothing when no file service is on and nothing is attached.
 */
export function RecordCloudFiles({ recordType, recordId, title = "Cloud files" }: { recordType: string; recordId: string; title?: string }) {
  const { has, can } = useSession();
  const toast = useToast();
  const enabled = has("documents");
  const providers = useApi<Providers>(enabled ? "/cloud-files/providers" : null);
  const links = useApi<{ items: FileLink[] }>(enabled ? `/cloud-files/links${query({ record_type: recordType, record_id: recordId })}` : null);
  const [picking, setPicking] = useState(false);
  const [copying, setCopying] = useState<FileLink | null>(null);
  const [removing, setRemoving] = useState<FileLink | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const byKey = useMemo(() => Object.fromEntries((providers.data?.items ?? []).map((p) => [p.key, p])) as Record<string, FileProvider>, [providers.data]);
  const picked = useMemo(() => new Set((links.data?.items ?? []).map((l) => `${l.provider}:${l.file_id}`)), [links.data]);
  if (!enabled) return null;
  const items = links.data?.items ?? [];
  const anyOn = (providers.data?.items ?? []).some((p) => p.enabled && p.available);
  if (providers.data && !anyOn && !items.length) return null;

  async function attach(provider: FileProvider, item: FileItem) {
    try {
      const result = await api.post<{ created: boolean; link: FileLink }>("/cloud-files/links", {
        provider: provider.key,
        file_id: item.id,
        record_type: recordType,
        record_id: recordId,
      });
      toast(result.created ? `Attached ${item.name}.` : `${item.name} was already attached.`);
      await links.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function refresh(link: FileLink) {
    setBusy(link.id);
    try {
      await api.post(`/cloud-files/links/${link.id}/refresh`);
      await links.reload();
      toast("File details updated.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(removing.id);
    try {
      await api.del(`/cloud-files/links/${removing.id}`);
      toast(`Removed ${removing.name}.`);
      setRemoving(null);
      await links.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title={title}
      icon={<Cloud size={16} />}
      actions={
        can("records.write") && anyOn ? (
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setPicking(true)}>
            <Plus size={14} /> Attach
          </button>
        ) : null
      }
    >
      {links.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{links.error}</p> : null}
      {items.length ? (
        <ul className="mini-list idoc-links">
          {items.map((link) => {
            const provider = byKey[link.provider];
            const canCopy = can("records.write") && !link.is_folder && provider?.enabled && provider.connected && provider.allow_copy;
            return (
              <li key={link.id}>
                {provider ? <ProviderMark provider={provider} /> : <Folder size={16} className="mini-icon" />}
                <div className="mini-main">
                  {link.web_url ? (
                    <a href={link.web_url} target="_blank" rel="noopener noreferrer" className="mini-title" title={`Open in ${provider?.name ?? "the provider"}`}>
                      {link.name}
                    </a>
                  ) : (
                    <span className="mini-title">{link.name}</span>
                  )}
                  <span className="mini-sub">
                    {[link.kind, bytes(link.size), link.added_by ? `added by ${link.added_by}` : null, relativeTime(link.added_at)].filter(Boolean).join(" · ")}
                  </span>
                </div>
                <div className="idoc-row-actions">
                  {link.web_url ? (
                    <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={link.web_url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${link.name}`}>
                      <ExternalLink size={14} />
                    </a>
                  ) : null}
                  {canCopy ? (
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => setCopying(link)} aria-label={`Copy ${link.name} into Documents`} title="Copy into AIVA Documents">
                      <ArrowDownToLine size={14} />
                    </button>
                  ) : null}
                  {can("records.write") && provider?.connected ? (
                    <button
                      type="button"
                      className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                      onClick={() => void refresh(link)}
                      disabled={busy === link.id}
                      aria-label={`Refresh details of ${link.name}`}
                      title="Refresh name and size"
                    >
                      {busy === link.id ? <span className="ui-spinner" /> : <RefreshCw size={14} />}
                    </button>
                  ) : null}
                  {link.can_remove ? (
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => setRemoving(link)} aria-label={`Remove ${link.name}`} title="Remove from this record">
                      <Trash2 size={14} />
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          {links.loading || providers.loading ? "Loading…" : "No cloud files yet. Attach proposals, contracts or a deal folder from Google Drive, OneDrive or Dropbox."}
        </p>
      )}

      <Modal open={picking} onClose={() => setPicking(false)} title="Attach a cloud file" description="Pick a file or a whole folder; it stays in your cloud storage and is linked here." size="lg">
        {providers.data ? <FileBrowser providers={providers.data.items} onPick={attach} picked={picked} /> : null}
      </Modal>

      <CopyDialog link={copying} provider={copying ? byKey[copying.provider] : undefined} maxMb={providers.data?.max_mb ?? 25} onClose={() => setCopying(null)} />

      <ConfirmDialog
        open={Boolean(removing)}
        title="Remove this file?"
        message={<>“{removing?.name}” will be unlinked from this record. The file itself stays in {removing ? byKey[removing.provider]?.name ?? "the cloud" : ""}.</>}
        confirmLabel="Remove"
        busy={busy === removing?.id}
        onConfirm={() => void remove()}
        onClose={() => setRemoving(null)}
      />
    </Card>
  );
}

function CopyDialog({ link, provider, maxMb, onClose }: { link: FileLink | null; provider: FileProvider | undefined; maxMb: number; onClose: () => void }) {
  const toast = useToast();
  const [format, setFormat] = useState<"pdf" | "office">("pdf");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ id: string; name: string } | null>(null);
  const google = Boolean(link?.mime_type?.startsWith("application/vnd.google-apps."));

  async function copy() {
    if (!link) return;
    setBusy(true);
    try {
      const result = await api.post<{ document: { id: string; name: string } }>(`/cloud-files/links/${link.id}/copy`, { format });
      setDone(result.document);
      toast(`Copied ${result.document.name} into Documents.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  function close() {
    setDone(null);
    setFormat("pdf");
    onClose();
  }

  return (
    <Modal
      open={Boolean(link)}
      onClose={close}
      size="sm"
      title="Copy into Documents"
      footer={
        done ? (
          <>
            <button type="button" className="ui-btn" onClick={close}>
              Close
            </button>
            <Link className="ui-btn ui-btn-primary" href={`/documents/${done.id}`}>
              Open document
            </Link>
          </>
        ) : (
          <>
            <button type="button" className="ui-btn" onClick={close} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void copy()} disabled={busy}>
              {busy ? <span className="ui-spinner" /> : <ArrowDownToLine size={15} />} Copy
            </button>
          </>
        )
      }
    >
      {done ? (
        <p className="ui-small" style={{ margin: 0 }}>
          “{done.name}” is now in AIVA Documents and linked to this record.
        </p>
      ) : (
        <div className="ui-stack" style={{ gap: 12 }}>
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            AIVA keeps its own copy of “{link?.name}” from {provider?.name ?? "your cloud storage"}, linked to this record. Later changes in {provider?.vendor ?? "the cloud"} won&apos;t update the copy. Files up to {maxMb} MB.
          </p>
          {google ? (
            <fieldset className="idoc-formats">
              <legend className="ui-small">Save it as</legend>
              <label className="ui-check">
                <input type="radio" name="format" checked={format === "pdf"} onChange={() => setFormat("pdf")} /> PDF
              </label>
              <label className="ui-check">
                <input type="radio" name="format" checked={format === "office"} onChange={() => setFormat("office")} /> Microsoft Office (Word, Excel or PowerPoint)
              </label>
            </fieldset>
          ) : null}
        </div>
      )}
    </Modal>
  );
}
