"use client";

import Link from "next/link";
import { ShieldCheck } from "lucide-react";

import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Alert, Badge, Card, LoadingRows, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { PLAN_NAMES, type PlanKey } from "@/lib/plans";
import type { Role } from "@/lib/platform-types";
import { useApi } from "@/lib/use-api";

type LicenseSettings = {
  default_role: string;
  auto_add_seats: boolean;
  credit_limit_default: number | null;
  credit_limits: Record<string, number>;
  ai_word_limit_default: number | null;
  ai_word_limits: Record<string, number>;
};

type LicenseOverview = {
  plan: PlanKey;
  seats_purchased: number;
  seats_active: number;
  seats_invited: number;
  allowances: { credits_total: number; ai_words_total: number };
  users: {
    id: string;
    name: string;
    email: string;
    status: string;
    role: string;
    role_name: string;
    last_login_at: string | null;
    credits_used: number;
    ai_words_used: number;
    mfa_enabled: boolean;
  }[];
};

export default function LicensesPage() {
  return (
    <SettingsShell permission="admin.users" what="license settings">
      <Licenses />
    </SettingsShell>
  );
}

function Licenses() {
  const overview = useApi<LicenseOverview>("/licenses");
  const roles = useApi<Role[]>("/admin/roles");
  const state = useWorkspaceSettings<LicenseSettings>("licenses");
  const off = !state.canEdit;
  const data = overview.data;

  function setLimit(kind: "credit_limits" | "ai_word_limits", userId: string, value: number | null) {
    if (!state.draft) return;
    const next = { ...state.draft[kind] };
    if (value === null) delete next[userId];
    else next[userId] = value;
    state.set(kind, next);
  }

  return (
    <>
      <PageHeader
        title="License settings"
        description="How seats are added, which role new people get, and how much of the shared credit and AI word pool each person can use."
      />
      {overview.error ? <Alert>{overview.error}</Alert> : null}
      {data ? (
        <div className="settings-kpis" style={{ marginBottom: 18 }}>
          <Card>
            <div className="ui-kpi-label">Plan</div>
            <div className="ui-kpi-value" style={{ fontSize: 22 }}>
              {PLAN_NAMES[data.plan] ?? data.plan}
            </div>
            <Link href="/settings/billing" className="ui-small">
              Change plan
            </Link>
          </Card>
          <Card>
            <div className="ui-kpi-label">Seats in use</div>
            <div className="ui-kpi-value">
              {data.seats_active}
              <span className="ui-muted" style={{ fontSize: 15 }}>
                {" "}
                / {Math.max(data.seats_purchased, data.seats_active)}
              </span>
            </div>
            <span className="ui-muted ui-small">{data.seats_invited} invited</span>
          </Card>
          <Card>
            <div className="ui-kpi-label">Credits a month</div>
            <div className="ui-kpi-value">{data.allowances.credits_total.toLocaleString()}</div>
          </Card>
          <Card>
            <div className="ui-kpi-label">AI words a month</div>
            <div className="ui-kpi-value">{data.allowances.ai_words_total.toLocaleString()}</div>
          </Card>
        </div>
      ) : null}
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Seats and new people">
              <div className="settings-rows">
                <SettingRow label="Add seats automatically" hint="When off, admins can't invite more people than the seats you pay for; buy more seats in Plan overview first.">
                  <Toggle label="Add seats automatically" checked={draft.auto_add_seats} disabled={off} onChange={(value) => state.set("auto_add_seats", value)} />
                </SettingRow>
                <SettingRow label="Default role for invites" hint="Pre-selected when someone is invited. You can still pick another role per invite.">
                  <select className="ui-select sm" aria-label="Default role" value={draft.default_role} disabled={off} onChange={(event) => state.set("default_role", event.target.value)} style={{ width: 220 }}>
                    {(roles.data ?? [{ key: draft.default_role, name: draft.default_role } as Role]).map((role) => (
                      <option key={role.key} value={role.key}>
                        {role.name}
                      </option>
                    ))}
                  </select>
                </SettingRow>
              </div>
            </Card>

            <Card title="Usage limits">
              <div className="settings-rows">
                <SettingRow label="Credits per person each month" hint="Leave empty for no limit. Stops one person using the whole pool.">
                  <NumberInput label="Default credit limit" allowEmpty value={draft.credit_limit_default} min={0} disabled={off} onChange={(value) => state.set("credit_limit_default", value)} suffix="credits" />
                </SettingRow>
                <SettingRow label="AI words per person each month" hint="Leave empty for no limit.">
                  <NumberInput label="Default AI word limit" allowEmpty value={draft.ai_word_limit_default} min={0} disabled={off} onChange={(value) => state.set("ai_word_limit_default", value)} suffix="words" width={140} />
                </SettingRow>
              </div>
            </Card>

            <Card title="People" flush>
              {!data ? (
                <LoadingRows />
              ) : (
                <div className="ui-table-wrap">
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>Person</th>
                        <th>Role</th>
                        <th>Last sign-in</th>
                        <th className="ui-num">Credits used</th>
                        <th>Credit limit</th>
                        <th className="ui-num">AI words used</th>
                        <th>AI word limit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.users.map((user) => (
                        <tr key={user.id}>
                          <td>
                            <div className="cell-title">
                              {user.name} {user.mfa_enabled ? <ShieldCheck size={13} aria-label="Two-step on" style={{ verticalAlign: -2, color: "var(--success)" }} /> : null}
                            </div>
                            <div className="ui-muted ui-small">{user.email}</div>
                          </td>
                          <td>
                            {user.role_name}
                            {user.status !== "active" ? (
                              <>
                                {" "}
                                <Badge tone={user.status === "invited" ? "info" : "neutral"}>{user.status}</Badge>
                              </>
                            ) : null}
                          </td>
                          <td className="ui-small ui-muted ui-nowrap">{user.last_login_at ? relativeTime(user.last_login_at) : "Never"}</td>
                          <td className="ui-num">{user.credits_used.toLocaleString()}</td>
                          <td>
                            <NumberInput
                              label={`Credit limit for ${user.name}`}
                              allowEmpty
                              width={110}
                              value={draft.credit_limits[user.id] ?? null}
                              min={0}
                              disabled={off}
                              onChange={(value) => setLimit("credit_limits", user.id, value)}
                            />
                          </td>
                          <td className="ui-num">{user.ai_words_used.toLocaleString()}</td>
                          <td>
                            <NumberInput
                              label={`AI word limit for ${user.name}`}
                              allowEmpty
                              width={120}
                              value={draft.ai_word_limits[user.id] ?? null}
                              min={0}
                              disabled={off}
                              onChange={(value) => setLimit("ai_word_limits", user.id, value)}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              A personal limit overrides the default above. Empty means the default applies. Manage people and roles in <Link href="/settings/users">Users</Link>.
            </p>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
