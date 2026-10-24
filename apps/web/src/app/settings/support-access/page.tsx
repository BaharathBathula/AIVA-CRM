"use client";

import { LifeBuoy, ShieldOff } from "lucide-react";
import { useState } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, PageHeader } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Grant = {
  id: string;
  access_level: "read_only" | "full";
  reason: string | null;
  granted_by: string | null;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
  last_used_at: string | null;
  use_count: number;
  active: boolean;
};

type SupportAccess = {
  active: Grant | null;
  history: Grant[];
  activity: { at: string; who: string; path: string | null; ip: string | null }[];
};

const DURATIONS = [
  { hours: 2, label: "2 hours" },
  { hours: 8, label: "8 hours" },
  { hours: 24, label: "1 day" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "1 week" },
  { hours: 336, label: "2 weeks" },
];

export default function SupportAccessPage() {
  return (
    <SettingsShell permission="admin.settings" what="support access">
      <SupportAccessView />
    </SettingsShell>
  );
}

function SupportAccessView() {
  const toast = useToast();
  const access = useApi<SupportAccess>("/support-access");
  const [level, setLevel] = useState<"read_only" | "full">("read_only");
  const [hours, setHours] = useState(24);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const data = access.data;

  async function grant() {
    setBusy(true);
    try {
      await api.post("/support-access", { access_level: level, hours, reason: reason.trim() || null });
      toast("Support access granted. Let the AIVA support team know they can look now.");
      setReason("");
      await access.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!data?.active) return;
    setBusy(true);
    try {
      await api.post(`/support-access/${data.active.id}/revoke`);
      toast("Support access ended.");
      setRevoking(false);
      await access.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Support access"
        description="AIVA support can't see your workspace unless you let them in. Access is time-limited, can be ended at any moment, and everything they open is recorded below and in the system activity log."
      />
      {access.error ? <Alert>{access.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <div className="ui-stack">
          {data.active ? (
            <Card title="Support can access your workspace" icon={<LifeBuoy size={17} />}>
              <div className="support-active">
                <div>
                  <Badge tone={data.active.access_level === "full" ? "warning" : "info"} dot>
                    {data.active.access_level === "full" ? "Full access" : "Read-only"}
                  </Badge>
                  <p>
                    Until <strong>{dateTime(data.active.expires_at)}</strong> ({relativeTime(data.active.expires_at)}). Granted by {data.active.granted_by ?? "an admin"}{" "}
                    {relativeTime(data.active.created_at)}
                    {data.active.reason ? <> for “{data.active.reason}”</> : null}.
                  </p>
                  <p className="ui-muted ui-small">
                    {data.active.use_count ? `Used ${data.active.use_count} times, last ${relativeTime(data.active.last_used_at)}.` : "Not used yet."}
                  </p>
                </div>
                <button type="button" className="ui-btn ui-btn-danger" onClick={() => setRevoking(true)}>
                  <ShieldOff size={15} /> End access now
                </button>
              </div>
            </Card>
          ) : (
            <Card title="Let support in">
              <div className="ui-form-grid">
                <Field label="Access" hint={level === "full" ? "Support can make changes, like fixing an import or a workflow." : "Support can look, but not change anything."}>
                  <select className="ui-select" value={level} onChange={(event) => setLevel(event.target.value as "read_only" | "full")}>
                    <option value="read_only">Read-only</option>
                    <option value="full">Full access</option>
                  </select>
                </Field>
                <Field label="For">
                  <select className="ui-select" value={hours} onChange={(event) => setHours(Number(event.target.value))}>
                    {DURATIONS.map((d) => (
                      <option key={d.hours} value={d.hours}>
                        {d.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Reason (optional)" className="span-2" hint="Helps support know where to look, like a ticket number.">
                  <input className="ui-input" value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} placeholder="Ticket #4821: import mapping question" />
                </Field>
              </div>
              <div className="ui-row" style={{ marginTop: 14 }}>
                <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void grant()}>
                  {busy ? <span className="ui-spinner" /> : <LifeBuoy size={15} />} Grant access
                </button>
              </div>
            </Card>
          )}

          <Card title="What support opened" flush>
            {data.activity.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Who</th>
                      <th>Page or action</th>
                      <th>IP address</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.activity.map((row, index) => (
                      <tr key={`${row.at}-${index}`}>
                        <td className="ui-nowrap ui-small">{dateTime(row.at)}</td>
                        <td>{row.who}</td>
                        <td className="ui-mono">{row.path ?? "—"}</td>
                        <td className="ui-mono">{row.ip ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon={<LifeBuoy size={20} />} title="Support hasn't opened anything" />
            )}
          </Card>

          {data.history.length ? (
            <Card title="Past access" flush>
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Granted</th>
                      <th>Access</th>
                      <th>Reason</th>
                      <th>Ended</th>
                      <th className="ui-num">Uses</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.history.map((grant) => (
                      <tr key={grant.id}>
                        <td className="ui-small">
                          {dateTime(grant.created_at)}
                          <div className="ui-muted">by {grant.granted_by ?? "an admin"}</div>
                        </td>
                        <td>{grant.access_level === "full" ? "Full" : "Read-only"}</td>
                        <td className="ui-small">{grant.reason ?? "—"}</td>
                        <td className="ui-small">
                          {grant.active ? (
                            <Badge tone="success">Active</Badge>
                          ) : grant.revoked_at ? (
                            <>
                              {dateTime(grant.revoked_at)}
                              <div className="ui-muted">ended by {grant.revoked_by ?? "an admin"}</div>
                            </>
                          ) : (
                            <>
                              {dateTime(grant.expires_at)}
                              <div className="ui-muted">expired</div>
                            </>
                          )}
                        </td>
                        <td className="ui-num">{grant.use_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : null}
        </div>
      )}
      <ConfirmDialog
        open={revoking}
        title="End support access?"
        message="AIVA support loses access straight away, even if they're in the middle of something."
        confirmLabel="End access"
        busy={busy}
        onConfirm={() => void revoke()}
        onClose={() => setRevoking(false)}
      />
    </>
  );
}
