"use client";

import Link from "next/link";
import { Inbox, Pencil, Plug, Send } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, EmptyState, Field, LoadingRows, Modal, PageHeader, type Tone } from "@/components/ui";
import { percent, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Mailbox = {
  id: string;
  user_id: string;
  user_name: string;
  email: string;
  provider: string;
  status: string;
  last_error: string | null;
  last_sync_at: string | null;
  reply_sync: boolean;
  domain: string;
  domain_status: string | null;
  sent_today: number;
  sent_last_hour: number;
  daily_limit: number;
  hourly_limit: number;
  last_sent_at: string | null;
  bounce_rate: number;
  sample: number;
  paused: boolean;
  sent_30_days: number;
  replied_30_days: number;
  health: number;
  issues: string[];
  custom_limit: boolean;
};

type MailboxList = {
  items: Mailbox[];
  defaults: { daily_limit: number; hourly_limit: number };
  server_mail: boolean;
  can_edit: boolean;
};

const PROVIDERS: Record<string, string> = { google: "Gmail", gmail: "Gmail", microsoft: "Outlook", outlook: "Outlook", smtp: "SMTP", imap: "IMAP" };

function healthTone(score: number): Tone {
  if (score >= 85) return "success";
  if (score >= 60) return "warning";
  return "danger";
}

export default function MailboxesPage() {
  return (
    <SettingsShell>
      <Mailboxes />
    </SettingsShell>
  );
}

function Mailboxes() {
  const { session } = useSession();
  const toast = useToast();
  const mailboxes = useApi<MailboxList>("/outbound/mailboxes");
  const [editing, setEditing] = useState<Mailbox | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const data = mailboxes.data;
  const mine = data?.items.find((item) => item.user_id === session?.user.id);

  async function test(mailbox: Mailbox) {
    setTesting(mailbox.id);
    try {
      const result = await api.post<{ ok: boolean; message: string }>(`/outbound/mailboxes/${mailbox.id}/test`);
      toast(result.message, result.ok ? "success" : "danger");
      await mailboxes.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setTesting(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Mailboxes"
        description="Each rep's connected mailbox sends their sequence emails. See how much each one sends, how many bounce, and whether replies are being picked up."
        actions={
          <Link href="/settings/integrations" className="ui-btn ui-btn-primary">
            <Plug size={15} /> {mine ? "Manage my mailbox" : "Connect my mailbox"}
          </Link>
        }
      />
      {mailboxes.error ? <Alert>{mailboxes.error}</Alert> : null}
      {data && !data.items.length ? (
        <Alert tone={data.server_mail ? "info" : "warning"}>
          {data.server_mail
            ? "Nobody has connected a mailbox yet, so sequence emails go out from the server's address. Replies won't come back to reps until they connect Gmail or Outlook."
            : "Nobody has connected a mailbox yet, and there's no server mail set up, so sequence emails can't be delivered. Connect a mailbox in Integrations."}
        </Alert>
      ) : null}

      <Card
        flush
        title="Connected mailboxes"
        actions={
          data ? (
            <span className="ui-muted ui-small">
              Default limit {data.defaults.daily_limit} a day · {data.defaults.hourly_limit} an hour (
              <Link href="/settings/outbound/protection">change</Link>)
            </span>
          ) : null
        }
      >
        {!data ? (
          <LoadingRows />
        ) : data.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Mailbox</th>
                  <th>Status</th>
                  <th>Health</th>
                  <th>Sent today</th>
                  <th>Bounces</th>
                  <th>Replies</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((mailbox) => {
                  const own = mailbox.user_id === session?.user.id;
                  return (
                    <tr key={mailbox.id}>
                      <td style={{ minWidth: 220 }}>
                        <div className="cell-title">{mailbox.email}</div>
                        <div className="ui-muted ui-small">
                          {mailbox.user_name} · {PROVIDERS[mailbox.provider] ?? mailbox.provider}
                        </div>
                      </td>
                      <td>
                        {mailbox.status === "connected" ? (
                          <Badge tone="success" dot>
                            Connected
                          </Badge>
                        ) : (
                          <Badge tone="danger" dot>
                            {mailbox.status === "error" ? "Error" : mailbox.status}
                          </Badge>
                        )}
                        {mailbox.paused ? (
                          <div style={{ marginTop: 4 }}>
                            <Badge tone="danger">Sending paused</Badge>
                          </div>
                        ) : null}
                      </td>
                      <td style={{ minWidth: 200 }}>
                        <Badge tone={healthTone(mailbox.health)}>{mailbox.health}/100</Badge>
                        {mailbox.issues.length ? (
                          <ul className="outbound-issues">
                            {mailbox.issues.map((issue) => (
                              <li key={issue}>{issue}</li>
                            ))}
                          </ul>
                        ) : (
                          <div className="ui-muted ui-small" style={{ marginTop: 4 }}>
                            No problems found
                          </div>
                        )}
                      </td>
                      <td className="ui-nowrap">
                        <strong>{mailbox.sent_today}</strong> <span className="ui-muted">of {mailbox.daily_limit}</span>
                        <div className="ui-muted ui-small">{mailbox.custom_limit ? "Custom limit" : "Workspace limit"}</div>
                      </td>
                      <td className="ui-nowrap">
                        <span style={{ color: mailbox.paused ? "var(--danger)" : undefined, fontWeight: 650 }}>{percent(mailbox.bounce_rate, 1)}</span>
                        <div className="ui-muted ui-small">of last {mailbox.sample} emails</div>
                      </td>
                      <td className="ui-nowrap">
                        {mailbox.reply_sync ? (
                          <>
                            <span>{mailbox.replied_30_days} in 30 days</span>
                            <div className="ui-muted ui-small">Synced {mailbox.last_sync_at ? relativeTime(mailbox.last_sync_at) : "not yet"}</div>
                          </>
                        ) : (
                          <Badge tone="warning">Not synced</Badge>
                        )}
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {own || data.can_edit ? (
                          <button type="button" className="ui-btn ui-btn-sm" disabled={testing === mailbox.id} onClick={() => void test(mailbox)}>
                            {testing === mailbox.id ? <span className="ui-spinner" /> : <Send size={14} />} Test
                          </button>
                        ) : null}
                        {data.can_edit ? (
                          <button
                            type="button"
                            className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                            aria-label={`Change the daily limit for ${mailbox.email}`}
                            title="Change daily limit"
                            onClick={() => setEditing(mailbox)}
                          >
                            <Pencil size={14} />
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Inbox size={20} />}
            title="No mailboxes connected"
            message="Reps connect Gmail, Outlook or any IMAP/SMTP mailbox in Settings → Integrations. Emails then go out from their own address and replies stop sequences automatically."
            action={
              <Link href="/settings/integrations" className="ui-btn ui-btn-primary">
                <Plug size={15} /> Connect a mailbox
              </Link>
            }
          />
        )}
      </Card>

      <LimitModal
        mailbox={editing}
        defaultLimit={data?.defaults.daily_limit ?? 200}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await mailboxes.reload();
        }}
      />
    </>
  );
}

function LimitModal({ mailbox, defaultLimit, onClose, onSaved }: { mailbox: Mailbox | null; defaultLimit: number; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [custom, setCustom] = useState(false);
  const [limit, setLimit] = useState(defaultLimit);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!mailbox) return;
    setCustom(mailbox.custom_limit);
    setLimit(mailbox.daily_limit);
    setError(null);
  }, [mailbox]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!mailbox) return;
    setSaving(true);
    setError(null);
    try {
      await api.put(`/outbound/mailboxes/${mailbox.user_id}/limit`, { daily_limit: custom ? limit : null });
      toast("Daily limit saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={Boolean(mailbox)}
      title="Daily sending limit"
      description={mailbox ? `${mailbox.email} (${mailbox.user_name})` : undefined}
      size="sm"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="limit-form" className="ui-btn ui-btn-primary" disabled={saving}>
            {saving ? <span className="ui-spinner" /> : null} Save
          </button>
        </>
      }
    >
      <form id="limit-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <label className="ui-check">
          <input type="radio" name="limit-mode" checked={!custom} onChange={() => setCustom(false)} /> Use the workspace limit ({defaultLimit} a day)
        </label>
        <label className="ui-check">
          <input type="radio" name="limit-mode" checked={custom} onChange={() => setCustom(true)} /> Set a limit for this mailbox
        </label>
        {custom ? (
          <Field label="Emails per day" hint="New mailboxes do best starting around 30–50 a day and increasing each week.">
            <input className="ui-input" type="number" min={1} max={5000} value={limit} onChange={(event) => setLimit(Math.max(1, Math.min(5000, Number(event.target.value) || 1)))} />
          </Field>
        ) : null}
      </form>
    </Modal>
  );
}
