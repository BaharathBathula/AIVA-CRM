"use client";

import Link from "next/link";
import { MailCheck, MailX, Megaphone, UserPlus } from "lucide-react";
import { useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, type Tone } from "@/components/ui";
import { percent, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Activity = { action: string | null; at: string | null; title: string | null; url?: string | null };

type Member = {
  status: string;
  status_note?: string | null;
  lists: string[];
  tags: string[];
  changed_at?: string | null;
  open_rate?: number | null;
  click_rate?: number | null;
  activity: Activity[];
};

type ToolRow = {
  key: string;
  name: string;
  list_label: string;
  list_name: string | null;
  configured: boolean;
  link: { status: string; synced_at: string | null; error: string | null; record_type: string; record_id: string } | null;
  member: Member | null;
  error: string | null;
};

type RecordMarketingData = {
  email: string | null;
  do_not_contact: { reason: string; note: string | null; at: string; kind: string } | null;
  opted_out: boolean;
  tools: ToolRow[];
  can_change: boolean;
};

const STATUS: Record<string, { label: string; tone: Tone }> = {
  subscribed: { label: "Subscribed", tone: "success" },
  pending: { label: "Waiting to confirm", tone: "info" },
  transactional: { label: "Transactional only", tone: "neutral" },
  unsubscribed: { label: "Unsubscribed", tone: "warning" },
  cleaned: { label: "Cleaned (bounced)", tone: "danger" },
  bounced: { label: "Bounced", tone: "danger" },
  complaint: { label: "Spam complaint", tone: "danger" },
  archived: { label: "Archived", tone: "neutral" },
  not_in_list: { label: "Not in the list", tone: "neutral" },
  not_subscribed: { label: "In the list, not subscribed", tone: "neutral" },
  unknown: { label: "Unknown", tone: "neutral" },
};

const ACTION: Record<string, string> = { open: "Opened", click: "Clicked", sent: "Received", bounce: "Bounced", unsub: "Unsubscribed", unsubscribe: "Unsubscribed", complaint: "Marked as spam", signup: "Signed up" };

const REASON: Record<string, string> = {
  unsubscribed: "unsubscribed",
  bounced: "the address bounced",
  complaint: "a spam complaint",
  removal_request: "a data removal request",
  manual: "added by a team member",
};

function statusOf(tool: ToolRow): { label: string; tone: Tone } {
  if (tool.member) return STATUS[tool.member.status] ?? STATUS.unknown;
  if (tool.link && STATUS[tool.link.status]) return STATUS[tool.link.status];
  return { label: `Not in ${tool.name}`, tone: "neutral" };
}

/**
 * The person's standing in each connected email marketing tool (Mailchimp,
 * ActiveCampaign, Brevo, Constant Contact, Klaviyo): status, list, tags and
 * recent opens and clicks, with Subscribe and an everywhere Unsubscribe.
 * Renders nothing without the mass email feature, or when no tool is connected
 * and the person isn't on the do-not-contact list.
 */
export function RecordMarketing({ recordType, recordId }: { recordType: "contact" | "lead"; recordId: string }) {
  const { has } = useSession();
  const toast = useToast();
  const enabled = has("mass_email");
  const data = useApi<RecordMarketingData>(enabled ? `/email-marketing/records/${recordType}/${recordId}` : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  if (!enabled) return null;
  const info = data.data;
  if (info && !info.tools.length && !info.do_not_contact) return null;
  if (!info && !data.loading) return null;
  const blocked = Boolean(info?.do_not_contact || info?.opted_out);

  async function subscribe(tool: ToolRow) {
    setBusy(tool.key);
    try {
      const result = await api.post<{ message: string }>(`/email-marketing/records/${recordType}/${recordId}/subscribe`, { key: tool.key });
      toast(result.message);
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function unsubscribe() {
    setBusy("unsubscribe");
    try {
      const result = await api.post<{ ok: boolean; message: string; tools: { name: string; ok: boolean; error?: string }[] }>(`/email-marketing/records/${recordType}/${recordId}/unsubscribe`);
      toast(result.message, result.ok ? undefined : "danger");
      for (const failed of result.tools.filter((t) => !t.ok)) toast(failed.error ?? `${failed.name} couldn't be updated.`, "danger");
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
      setConfirming(false);
    }
  }

  return (
    <Card
      title="Email marketing"
      icon={<Megaphone size={16} />}
      actions={
        info?.can_change && info.email && !blocked ? (
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy !== null} onClick={() => setConfirming(true)}>
            <MailX size={14} /> Unsubscribe
          </button>
        ) : null
      }
    >
      {data.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{data.error}</p> : null}
      {!info ? (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Loading…
        </p>
      ) : (
        <div className="imk-record">
          {!info.email ? <p className="ui-muted ui-small" style={{ margin: 0 }}>Add an email address to this {recordType} to sync it.</p> : null}
          {blocked ? (
            <Alert tone="info">
              No marketing email:{" "}
              {info.do_not_contact
                ? `on AIVA's do-not-contact list (${REASON[info.do_not_contact.reason] ?? info.do_not_contact.reason}${info.do_not_contact.note ? ` · ${info.do_not_contact.note}` : ""}).`
                : "this person opted out of email."}
            </Alert>
          ) : null}
          {info.email
            ? info.tools.map((tool) => {
                const state = statusOf(tool);
                const member = tool.member;
                const canAdd =
                  info.can_change && !blocked && tool.configured && !["subscribed", "pending", "transactional", "unsubscribed", "cleaned", "bounced", "complaint"].includes(member?.status ?? tool.link?.status ?? "");
                return (
                  <div key={tool.key} className="imk-tool">
                    <div className="imk-tool-head">
                      <strong>{tool.name}</strong>
                      <Badge tone={state.tone} dot>
                        {state.label}
                      </Badge>
                      <span className="ui-spacer" />
                      {canAdd ? (
                        <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void subscribe(tool)}>
                          {busy === tool.key ? <span className="ui-spinner" /> : <UserPlus size={14} />} Add to {tool.list_label.toLowerCase()}
                        </button>
                      ) : null}
                    </div>
                    {!tool.configured ? (
                      <p className="ui-muted ui-small" style={{ margin: 0 }}>
                        {tool.name} has no {tool.list_label.toLowerCase()} chosen yet.{" "}
                        <Link href={`/integrations/${tool.key}`}>Set it up</Link>
                      </p>
                    ) : (
                      <div className="imk-tool-meta ui-small ui-muted">
                        {member?.lists?.length ? <span>{member.lists.join(", ")}</span> : tool.list_name ? <span>{tool.list_name}</span> : null}
                        {member?.status_note ? <span>{member.status_note}</span> : null}
                        {member?.open_rate !== undefined && member?.open_rate !== null ? <span>Opens {percent(member.open_rate * 100)}</span> : null}
                        {tool.link?.synced_at ? <span>Synced {relativeTime(tool.link.synced_at)}</span> : null}
                      </div>
                    )}
                    {member?.tags?.length ? (
                      <div className="imk-tags">
                        {member.tags.slice(0, 8).map((tag) => (
                          <Badge key={tag}>{tag}</Badge>
                        ))}
                      </div>
                    ) : null}
                    {tool.link?.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{tool.link.error}</p> : null}
                    {tool.error ? <p className="ui-small ui-muted" style={{ margin: 0 }}>{tool.error}</p> : null}
                    {member?.activity?.length ? (
                      <ul className="imk-activity">
                        {member.activity.slice(0, 3).map((item, index) => (
                          <li key={index}>
                            <MailCheck size={13} aria-hidden />
                            <span className="ui-truncate">
                              {ACTION[item.action ?? ""] ?? item.action ?? "Activity"}
                              {item.title ? ` · ${item.title}` : ""}
                            </span>
                            <span className="ui-muted ui-nowrap">{item.at ? relativeTime(item.at) : ""}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                );
              })
            : null}
        </div>
      )}
      <ConfirmDialog
        open={confirming}
        title="Unsubscribe from marketing email?"
        message={`${info?.email ?? "This person"} goes on AIVA's do-not-contact list and is unsubscribed in every connected email marketing tool. Only someone with the privacy permission can take them off the list again.`}
        confirmLabel="Unsubscribe"
        busy={busy === "unsubscribe"}
        onConfirm={() => void unsubscribe()}
        onClose={() => setConfirming(false)}
      />
    </Card>
  );
}

export default RecordMarketing;
