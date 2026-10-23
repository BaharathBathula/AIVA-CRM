"use client";

import Link from "next/link";
import { CheckCircle2, ShieldPlus, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { PlanChip } from "@/components/plan";
import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, LoadingRows, PageHeader } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Compliance {
  available: boolean;
  hipaa_mode: boolean;
  session_timeout_minutes: number;
  encryption_available: boolean;
  members: { name: string; email: string; mfa_enabled: boolean }[];
  members_without_mfa: number;
}

export default function CompliancePage() {
  return (
    <SettingsShell permission="admin.settings" what="compliance settings">
      <ComplianceSettings />
    </SettingsShell>
  );
}

function ComplianceSettings() {
  const toast = useToast();
  const data = useApi<Compliance>("/compliance");
  const [confirming, setConfirming] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const d = data.data;

  async function save(on: boolean) {
    setBusy(true);
    try {
      data.setData(await api.put<Compliance>("/compliance", { hipaa_mode: on }));
      toast(on ? "HIPAA mode is on." : "HIPAA mode is off.");
      setConfirming(null);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  if (data.error) return <Alert>{data.error}</Alert>;
  if (!d) {
    return (
      <Card>
        <LoadingRows />
      </Card>
    );
  }

  return (
    <>
      <PageHeader
        title="Compliance"
        description="HIPAA mode tightens the workspace for teams that handle protected health information."
      />
      <Card
        title={
          <>
            HIPAA mode <PlanChip feature="hipaa" />
          </>
        }
        icon={<ShieldPlus size={16} />}
        actions={
          d.available ? (
            <label className="ui-switch">
              <input type="checkbox" checked={d.hipaa_mode} disabled={busy} onChange={(e) => setConfirming(e.target.checked)} aria-label="HIPAA mode" />
              <span />
            </label>
          ) : null
        }
      >
        <div className="ui-stack">
          <p style={{ margin: 0 }}>
            {d.hipaa_mode ? <Badge tone="success">On</Badge> : <Badge>Off</Badge>} When it&apos;s on:
          </p>
          <ul className="compliance-list">
            <li>Everyone must use two-step verification to open any record.</li>
            <li>Idle sessions sign out after {d.hipaa_mode ? d.session_timeout_minutes : 15} minutes.</li>
            <li>Every time someone opens a record, it&apos;s written to the audit log — not just changes.</li>
            <li>
              Sensitive custom fields can be encrypted at rest and masked for people without permission
              {d.encryption_available ? "." : <> — <PlanChip feature="field_encryption" /></>}
            </li>
          </ul>
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            AIVA&apos;s HIPAA mode provides technical safeguards. Your organization is still responsible for a Business Associate Agreement with its hosting provider and
            for its own policies.
          </p>
          {!d.available ? (
            <Link href="/settings/billing?plan=starter" className="ui-btn ui-btn-primary" style={{ width: "fit-content" }}>
              Upgrade to turn on HIPAA mode
            </Link>
          ) : null}
        </div>
      </Card>
      <Card
        title="Two-step verification"
        actions={d.members_without_mfa ? <Badge tone="warning">{d.members_without_mfa} without it</Badge> : <Badge tone="success">Everyone</Badge>}
        flush
      >
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Two-step verification</th>
              </tr>
            </thead>
            <tbody>
              {d.members.map((m) => (
                <tr key={m.email}>
                  <td className="cell-title">
                    {m.name}
                    <div className="ui-muted ui-small">{m.email}</div>
                  </td>
                  <td>
                    {m.mfa_enabled ? (
                      <span className="ui-row" style={{ gap: 6, color: "var(--success)" }}>
                        <CheckCircle2 size={15} /> On
                      </span>
                    ) : (
                      <span className="ui-row" style={{ gap: 6, color: "var(--warning)" }}>
                        <TriangleAlert size={15} /> Not set up
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <p className="ui-muted ui-small">
        People turn on two-step verification from <Link href="/settings/security">Password and security</Link>.
      </p>
      <ConfirmDialog
        open={confirming !== null}
        title={confirming ? "Turn on HIPAA mode?" : "Turn off HIPAA mode?"}
        message={
          confirming
            ? d.members_without_mfa
              ? `${d.members_without_mfa} people don't have two-step verification yet. They'll be asked to set it up before they can open records.`
              : "Sessions get shorter and every record view is logged."
            : "Record views stop being logged and the stricter sign-in rules are lifted."
        }
        confirmLabel={confirming ? "Turn on" : "Turn off"}
        danger={!confirming}
        busy={busy}
        onConfirm={() => void save(Boolean(confirming))}
        onClose={() => setConfirming(null)}
      />
    </>
  );
}
