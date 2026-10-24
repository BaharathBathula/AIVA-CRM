"use client";

import Link from "next/link";
import { Check, KeyRound, ShieldCheck, TriangleAlert } from "lucide-react";

import { useSession } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, SettingRow, SettingsBody, TagsInput, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Alert, Badge, Card, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/use-api";

type Security = {
  require_mfa: boolean;
  ip_allowlist: string[];
  allowed_email_domains: string[];
  password_min_length: number;
  allow_api_keys: boolean;
};

type Overview = {
  your_ip: string | null;
  members: number;
  without_mfa: { id: string; name: string; email: string }[];
  sso_enabled: boolean;
  hipaa_mode: boolean;
  session_timeout_minutes: number | null;
  settings: Security;
};

export default function WorkspaceSecurityPage() {
  return (
    <SettingsShell permission="admin.settings" what="workspace security">
      <WorkspaceSecurity />
    </SettingsShell>
  );
}

function WorkspaceSecurity() {
  const { session } = useSession();
  const overview = useApi<Overview>("/security/overview");
  const state = useWorkspaceSettings<Security>("security");
  const off = !state.canEdit;
  const data = overview.data;
  const meWithoutMfa = Boolean(session && !session.user.mfa_enabled);

  const checks = data
    ? [
        {
          ok: data.without_mfa.length === 0,
          title: data.without_mfa.length === 0 ? "Everyone uses two-step verification" : `${data.without_mfa.length} of ${data.members} people don't use two-step verification`,
          detail: data.without_mfa.length ? data.without_mfa.slice(0, 6).map((u) => u.name).join(", ") + (data.without_mfa.length > 6 ? "…" : "") : null,
        },
        { ok: data.sso_enabled, title: data.sso_enabled ? "Single sign-on is on" : "Single sign-on isn't set up", link: { href: "/settings/sso", label: "Set up SSO" } },
        {
          ok: Boolean(data.session_timeout_minutes && data.session_timeout_minutes <= 60),
          title: data.session_timeout_minutes ? `Idle sessions end after ${data.session_timeout_minutes} minutes` : "Idle sessions don't time out",
          link: { href: "/settings/organization", label: "Change" },
        },
        { ok: data.hipaa_mode, title: data.hipaa_mode ? "HIPAA mode is on" : "HIPAA mode is off", link: { href: "/settings/compliance", label: "Review" }, optional: true },
      ]
    : [];

  return (
    <>
      <PageHeader
        title="Security"
        description="Workspace-wide sign-in rules. Your own password and two-step verification live in Password & security."
      />
      {data ? (
        <Card title="Security checkup" icon={<ShieldCheck size={17} />} className="admin-gap">
          <div className="settings-checks">
            {checks.map((check) => (
              <div key={check.title} className="settings-check">
                <span className={`settings-check-icon ${check.ok ? "pass" : check.optional ? "info" : "warn"}`}>{check.ok ? <Check size={14} /> : <TriangleAlert size={13} />}</span>
                <div>
                  <strong>{check.title}</strong>
                  {check.detail ? <p>{check.detail}</p> : null}
                  {!check.ok && check.link ? (
                    <p>
                      <Link href={check.link.href}>{check.link.label}</Link>
                    </p>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Sign-in">
              <div className="settings-rows">
                <SettingRow
                  label="Require two-step verification"
                  hint="Everyone must turn on an authenticator app before they can use AIVA. People without it are asked to set it up when they sign in."
                >
                  <Toggle
                    label="Require two-step verification"
                    checked={draft.require_mfa}
                    disabled={off || (meWithoutMfa && !draft.require_mfa)}
                    onChange={(value) => state.set("require_mfa", value)}
                  />
                </SettingRow>
                {meWithoutMfa && !draft.require_mfa ? (
                  <Alert tone="info">
                    Turn on two-step verification for your own account first in <Link href="/settings/security">Password &amp; security</Link>, so you don&apos;t lock
                    yourself out.
                  </Alert>
                ) : null}
                <SettingRow label="Minimum password length" hint="Applies to new and changed passwords. Passwords must also mix at least three of lowercase, uppercase, numbers and symbols, and can't contain the person's email name.">
                  <NumberInput label="Minimum password length" value={draft.password_min_length} min={10} max={64} disabled={off} onChange={(value) => state.set("password_min_length", value ?? 10)} suffix="characters" />
                </SettingRow>
                <SettingRow label="Allowed email domains for invites" hint="Leave empty to allow any address. Example: yourcompany.com" stacked>
                  <TagsInput
                    label="Allowed email domains"
                    value={draft.allowed_email_domains}
                    disabled={off}
                    placeholder="yourcompany.com"
                    transform={(value) => value.toLowerCase().replace(/^@/, "").replace(/^https?:\/\//, "").replace(/\/.*$/, "")}
                    onChange={(value) => state.set("allowed_email_domains", value)}
                  />
                </SettingRow>
              </div>
            </Card>

            <Card title="Network">
              <div className="settings-rows">
                <SettingRow
                  label="IP allowlist"
                  hint={
                    <>
                      Only these addresses or ranges can reach the workspace (people and API keys). Leave empty to allow any network. Your address right now is{" "}
                      <strong>{data?.your_ip ?? "unknown"}</strong>.
                    </>
                  }
                  stacked
                >
                  <TagsInput label="Allowed IP addresses" value={draft.ip_allowlist} disabled={off} placeholder="203.0.113.0/24" onChange={(value) => state.set("ip_allowlist", value)} />
                  {data?.your_ip && draft.ip_allowlist.length && !draft.ip_allowlist.includes(data.your_ip) ? (
                    <button type="button" className="ui-btn ui-btn-sm" disabled={off} onClick={() => state.set("ip_allowlist", [...draft.ip_allowlist, data.your_ip!])}>
                      Add my address
                    </button>
                  ) : null}
                </SettingRow>
              </div>
            </Card>

            <Card title="API access">
              <div className="settings-rows">
                <SettingRow
                  label="Allow API keys"
                  hint={
                    <>
                      Turning this off stops every API key and MCP connection straight away. Manage keys in <Link href="/settings/api-keys">API keys</Link>.
                    </>
                  }
                >
                  {!draft.allow_api_keys ? <Badge tone="warning">Off</Badge> : null}
                  <Toggle label="Allow API keys" checked={draft.allow_api_keys} disabled={off} onChange={(value) => state.set("allow_api_keys", value)} />
                </SettingRow>
              </div>
            </Card>
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              <KeyRound size={12} style={{ verticalAlign: -1 }} /> Changes are recorded in the <Link href="/settings/audit">system activity log</Link>.
            </p>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
