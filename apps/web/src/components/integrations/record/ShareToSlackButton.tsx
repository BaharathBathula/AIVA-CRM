"use client";

import { ExternalLink, Hash, Lock, Send } from "lucide-react";
import { useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { Alert, Field, Modal } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

type SlackChannel = { id: string; name: string; is_private: boolean; is_member: boolean; members?: number | null };
type SlackChannels = {
  enabled: boolean;
  ready: boolean;
  mode: "bot" | "webhook" | null;
  default_channel_id: string | null;
  default_channel_name: string | null;
  items: SlackChannel[];
};

/**
 * "Share to Slack" for any record (lead, contact, account, opportunity, case, quote…).
 * Shows only when Slack is on and connected for the workspace.
 */
export function ShareToSlackButton({ recordType, recordId, className = "ui-btn ui-btn-sm", label = "Share to Slack" }: { recordType: string; recordId: string; className?: string; label?: string }) {
  const { has } = useSession();
  const status = useApi<SlackChannels>(has("chat_integrations") ? "/messaging/slack/channels?include_channels=false" : null);
  const [open, setOpen] = useState(false);
  if (!status.data?.enabled || !status.data.ready) return null;
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>
        <Send size={14} /> {label}
      </button>
      {open ? <ShareModal recordType={recordType} recordId={recordId} mode={status.data.mode} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function ShareModal({ recordType, recordId, mode, onClose }: { recordType: string; recordId: string; mode: SlackChannels["mode"]; onClose: () => void }) {
  const toast = useToast();
  const channels = useApi<SlackChannels>(mode === "bot" ? "/messaging/slack/channels" : null);
  const [channelId, setChannelId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ permalink: string | null; name: string | null } | null>(null);
  const chosen = channelId ?? channels.data?.default_channel_id ?? "";
  const list = channels.data?.items ?? [];

  async function share() {
    setBusy(true);
    try {
      const result = await api.post<{ permalink: string | null; channel_name: string | null; channel_id: string | null }>("/messaging/slack/share", {
        record_type: recordType,
        record_id: recordId,
        channel_id: mode === "bot" ? chosen || null : null,
        note: note.trim() || null,
      });
      const name = result.channel_name ?? list.find((c) => c.id === result.channel_id)?.name ?? null;
      toast(name ? `Shared to #${name}.` : "Shared to Slack.");
      setDone({ permalink: result.permalink, name });
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title="Share to Slack"
      description={mode === "webhook" ? "Posts to the channel your Slack incoming webhook was made for." : "Post this record, with a note, to a Slack channel."}
      onClose={onClose}
      footer={
        done ? (
          <>
            {done.permalink ? (
              <a className="ui-btn" href={done.permalink} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={14} /> Open in Slack
              </a>
            ) : null}
            <button type="button" className="ui-btn ui-btn-primary" onClick={onClose}>
              Done
            </button>
          </>
        ) : (
          <>
            <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void share()} disabled={busy || (mode === "bot" && !chosen)}>
              {busy ? <span className="ui-spinner" /> : <Send size={14} />} Share
            </button>
          </>
        )
      }
    >
      {done ? (
        <Alert tone="success">Shared{done.name ? ` to #${done.name}` : ""}.</Alert>
      ) : (
        <div className="ui-stack" style={{ gap: 14 }}>
          {channels.error ? <Alert>{channels.error}</Alert> : null}
          {mode === "bot" ? (
            <Field label="Channel" hint={list.find((c) => c.id === chosen)?.is_private && !list.find((c) => c.id === chosen)?.is_member ? "AIVA isn't in this private channel yet. Type /invite @AIVA there first." : undefined}>
              <select className="ui-select" value={chosen} onChange={(event) => setChannelId(event.target.value)} disabled={channels.loading && !list.length}>
                <option value="">{channels.loading && !list.length ? "Loading channels…" : "Choose a channel"}</option>
                {list.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.is_private ? "🔒 " : "# "}
                    {c.name}
                    {c.id === channels.data?.default_channel_id ? " (default)" : ""}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          <Field label="Note" hint="Optional. Shown above the record.">
            <textarea className="ui-textarea" rows={3} maxLength={1500} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Why you're sharing it, or what you need" />
          </Field>
          {mode === "bot" ? (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              <Hash size={12} style={{ verticalAlign: -1 }} /> Public channels work right away. <Lock size={12} style={{ verticalAlign: -1 }} /> Private channels need AIVA invited.
            </p>
          ) : null}
        </div>
      )}
    </Modal>
  );
}

export default ShareToSlackButton;
