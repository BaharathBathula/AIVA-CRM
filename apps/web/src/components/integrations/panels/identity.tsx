"use client";

import { Activity, ExternalLink, Globe, KeyRound, Plus, RefreshCw, ShieldCheck, Trash2, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";

import { CopyField } from "@/components/copy-field";
import { useSession, useToast } from "@/components/providers";
import { SettingRow, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, Field, Kpi, LoadingRows, type Tone } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { UrlRow } from "../common";
import type { PanelProps } from "../types";

/* ------------------------------------------------------------------ shapes (providers/identity.py → detail.extra) */

type IdpKey = "entra_id" | "okta";

type TokenInfo = { hint: string; created_at: string | null; created_by: string | null; last_used_at: string | null };

type SsoConnection = {
  name: string;
  issuer: string;
  client_id: string;
  has_secret: boolean;
  domains: string[];
  default_role: string;
  auto_provision: boolean;
  is_active: boolean;
  updated_at: string | null;
};

type Mapping = { group_id: string | null; group_name: string; role: string | null; team_id: string | null };

type PushedGroup = { id: string; name: string; external_id: string | null; members: number; auto_team_id: string | null; updated_at: string | null };

type DirectoryGroup = { id: string; name: string; description: string };

type ProvisioningEvent = { id: string; level: "info" | "success" | "warning" | "error"; action: string; message: string; at: string | null };

type Stats = {
  users: number;
  active: number;
  invited: number;
  deactivated: number;
  groups: number;
  last_30_days: Record<string, number>;
  last_event_at: string | null;
};

type IdentityExtra = {
  connected?: boolean;
  scim?: { base_url: string; has_token: boolean; token?: TokenInfo | null };
  sso?: {
    redirect_uri: string;
    issuer: string | null;
    configured: boolean;
    active: boolean;
    matches: boolean;
    login_url: string;
    connection?: SsoConnection | null;
    authorization_server?: string;
    default_name?: string;
  };
  stats?: Stats;
  mapping?: { default_role: string; group_mappings: Mapping[]; auto_teams: boolean };
  roles?: { key: string; name: string }[];
  teams?: { id: string; name: string }[];
  groups?: PushedGroup[];
  directory_api?: boolean;
  provisioning?: ProvisioningEvent[];
  policy?: { require_sso: boolean; verified: string[] };
  error?: string;
};

type DomainRow = {
  domain: string;
  status: "pending" | "verified" | "failed";
  record_name: string;
  alternate_record_name: string;
  record_value: string;
  added_at: string | null;
  checked_at: string | null;
  verified_at: string | null;
  error: string | null;
  in_sso: boolean;
};

type Policy = {
  require_sso: boolean;
  enforced: boolean;
  enforced_domains: string[];
  changed_at: string | null;
  domains: DomainRow[];
  sso: { name: string; issuer: string; active: boolean; domains: string[] } | null;
  covered_members: number;
  problems: string[];
  you: { owner: boolean; covered: boolean; signed_in_with_sso: boolean };
  check?: { domain: string; ok: boolean; message: string };
};

const LABEL: Record<IdpKey, string> = { entra_id: "Microsoft Entra ID", okta: "Okta" };

/* ------------------------------------------------------------------ provider-specific set-up text */

function ssoSteps(key: IdpKey): ReactNode[] {
  if (key === "entra_id") {
    return [
      <>Microsoft Entra admin center → <strong>App registrations</strong> → <strong>New registration</strong>. Name it AIVA and choose <em>Accounts in this organizational directory only</em>.</>,
      <>Under <strong>Redirect URI</strong>, pick <em>Web</em> and paste the redirect URI below.</>,
      <><strong>Certificates &amp; secrets</strong> → <strong>New client secret</strong>. Copy the secret&apos;s <em>Value</em> (shown once).</>,
      <>Optional: <strong>Token configuration</strong> → add the <code>email</code> optional claim to the ID token. Without it AIVA uses the sign-in name (UPN).</>,
      <>Paste the <strong>Application (client) ID</strong> and the secret here, add your email domains and save. Then assign people in <strong>Enterprise applications → AIVA → Users and groups</strong>.</>,
    ];
  }
  return [
    <>Okta Admin Console → <strong>Applications</strong> → <strong>Create App Integration</strong> → <em>OIDC – OpenID Connect</em> → <em>Web Application</em>.</>,
    <>Grant type: <em>Authorization Code</em>. <strong>Sign-in redirect URI</strong>: paste the redirect URI below. <strong>Initiate login URI</strong> (optional): the sign-in page below.</>,
    <><strong>Assignments</strong>: the people or groups who use AIVA.</>,
    <>Copy the <strong>Client ID</strong> and <strong>Client secret</strong> here, add your email domains and save. Leave the authorization server empty to use the Okta org server, or enter one such as <code>default</code>.</>,
  ];
}

function scimSteps(key: IdpKey): ReactNode[] {
  if (key === "entra_id") {
    return [
      <>Entra admin center → <strong>Enterprise applications</strong> → <strong>New application</strong> → <strong>Create your own application</strong> → <em>Integrate any other application you don&apos;t find in the gallery</em>. (Or open the AIVA app you created for sign-in.)</>,
      <><strong>Provisioning</strong> → <strong>Get started</strong> → Provisioning Mode <em>Automatic</em>.</>,
      <>Admin Credentials: <strong>Tenant URL</strong> = the SCIM URL below, <strong>Secret Token</strong> = a token created here. Choose <strong>Test Connection</strong>, then <strong>Save</strong>.</>,
      <><strong>Mappings</strong>: keep <code>userName</code> ← userPrincipalName, <code>emails[type eq &quot;work&quot;].value</code> ← mail and <code>active</code>. Turn on group provisioning to map Entra groups to AIVA roles and teams.</>,
      <><strong>Users and groups</strong>: assign who should have AIVA, set <strong>Provisioning Status</strong> to <em>On</em> and save. Entra syncs about every 40 minutes; <em>Provision on demand</em> tests one person straight away.</>,
    ];
  }
  return [
    <>In the AIVA app integration: <strong>General</strong> → <strong>App Settings</strong> → <strong>Edit</strong> → Provisioning <em>SCIM</em> → <strong>Save</strong>.</>,
    <><strong>Provisioning</strong> → <strong>Integration</strong> → <strong>Edit</strong>: SCIM connector base URL = the SCIM URL below; Unique identifier field for users = <code>userName</code>.</>,
    <>Supported provisioning actions: <em>Import New Users and Profile Updates</em>, <em>Push New Users</em>, <em>Push Profile Updates</em>, <em>Push Groups</em>. Authentication Mode: <em>HTTP Header</em>, paste a token created here. <strong>Test Connector Configuration</strong> → <strong>Save</strong>.</>,
    <><strong>Provisioning</strong> → <strong>To App</strong> → <strong>Edit</strong>: turn on <em>Create Users</em>, <em>Update User Attributes</em> and <em>Deactivate Users</em>.</>,
    <><strong>Push Groups</strong>: push the Okta groups you want to map to AIVA roles and teams.</>,
  ];
}

/* ------------------------------------------------------------------ panel */

function IdentityPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as IdentityExtra;
  if (!detail.enabled) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;
  const key = detail.integration.key as IdpKey;
  const stats = extra.stats;
  const connected = Boolean(detail.connection);

  return (
    <>
      {stats && stats.users > 0 ? (
        <div className="settings-kpis int-kpis iid-kpis">
          <Kpi label="Provisioned people" value={stats.users} />
          <Kpi label="Active" value={stats.active + stats.invited} note={stats.invited ? `${stats.invited} yet to sign in` : undefined} />
          <Kpi label="Deactivated" value={stats.deactivated} />
          <Kpi label="Groups" value={stats.groups} />
        </div>
      ) : null}
      {!detail.can_manage ? (
        <MemberView detail={detail} setDetail={setDetail} reload={reload} />
      ) : (
        <>
          {!connected ? (
            <Alert tone="info">
              Enter your {key === "entra_id" ? "directory (tenant) ID" : "Okta domain"} above to see the exact single sign-on and provisioning values for {LABEL[key]}.
            </Alert>
          ) : (
            <>
              <SsoCard detail={detail} setDetail={setDetail} reload={reload} />
              <ScimCard detail={detail} setDetail={setDetail} reload={reload} />
              <MappingCard detail={detail} setDetail={setDetail} reload={reload} />
            </>
          )}
          <PolicyCard />
          {connected ? <ActivityCard detail={detail} setDetail={setDetail} reload={reload} /> : null}
        </>
      )}
    </>
  );
}

function MemberView({ detail }: PanelProps) {
  const extra = detail.extra as IdentityExtra;
  const key = detail.integration.key as IdpKey;
  const on = Boolean(extra.sso?.matches && extra.sso.active);
  return (
    <Card title="Signing in" icon={<KeyRound size={16} />}>
      <p className="iid-text">
        {on
          ? `Your workspace signs in through ${LABEL[key]}: on the sign-in page choose “Log In with your Organization” and enter your work email.`
          : `Your admins manage ${LABEL[key]} for this workspace.`}{" "}
        {extra.scim?.has_token ? `People and groups are kept up to date from ${LABEL[key]}.` : null}
      </p>
    </Card>
  );
}

function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className="int-steps iid-steps">
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ol>
  );
}

function StepTitle({ n, children }: { n: number; children: ReactNode }) {
  return (
    <span className="iid-step-title">
      <span className="iid-step-n" aria-hidden>
        {n}
      </span>
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ 1 · single sign-on */

function SsoCard({ detail, reload }: PanelProps) {
  const toast = useToast();
  const { session } = useSession();
  const key = detail.integration.key as IdpKey;
  const extra = detail.extra as IdentityExtra;
  const sso = extra.sso;
  const existing = sso?.connection ?? null;
  const ours = Boolean(sso?.matches);
  const roles = extra.roles ?? [];
  const oktaDomain = String(detail.connection?.config?.okta_domain ?? "");

  const [clientId, setClientId] = useState(ours && existing ? existing.client_id : "");
  const [secret, setSecret] = useState("");
  const [domains, setDomains] = useState((existing?.domains ?? []).join(", "));
  const [server, setServer] = useState(sso?.authorization_server ?? "");
  const [role, setRole] = useState(existing?.default_role ?? "member");
  const [autoCreate, setAutoCreate] = useState(existing?.auto_provision ?? !extra.scim?.has_token);
  const [active, setActive] = useState(existing?.is_active ?? true);
  const [testEmail, setTestEmail] = useState(session?.user.email ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<string | null>(null);

  useEffect(() => {
    if (!testEmail && session?.user.email) setTestEmail(session.user.email);
  }, [session?.user.email, testEmail]);

  if (!sso) return null;
  const issuer = key === "okta" && oktaDomain ? `https://${oktaDomain}${server.trim() ? `/oauth2/${server.trim()}` : ""}` : sso.issuer;
  const status: { tone: Tone; label: string } = !sso.configured
    ? { tone: "neutral", label: "Not set up" }
    : !ours
      ? { tone: "warning", label: "Uses another provider" }
      : sso.active
        ? { tone: "success", label: "On" }
        : { tone: "neutral", label: "Off" };
  const domainList = domains.split(/[\s,]+/).map((d) => d.trim()).filter(Boolean);
  const testUrl = `/api/session/oauth/sso/start?intent=login&next=${encodeURIComponent(`/integrations/${key}`)}&email=${encodeURIComponent(testEmail.trim())}`;

  async function check() {
    setBusy(true);
    setError(null);
    setChecked(null);
    try {
      const result = await api.post<{ issuer: string; authorization_endpoint: string; scopes_ok: boolean }>(
        `/identity/${key}/sso/check`,
        key === "okta" ? { authorization_server: server.trim() } : {},
      );
      setChecked(
        `Found ${result.issuer}. Sign-in page: ${new URL(result.authorization_endpoint).host}.${result.scopes_ok ? "" : " The provider doesn't list the openid, email and profile scopes — check the app's settings."}`,
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.put(`/identity/${key}/sso`, {
        client_id: clientId.trim(),
        client_secret: secret.trim() || null,
        domains: domainList,
        default_role: role,
        auto_provision: autoCreate,
        is_active: active,
        authorization_server: key === "okta" ? server.trim() : null,
      });
      setSecret("");
      toast(active ? `Single sign-on with ${LABEL[key]} is on.` : "Single sign-on saved and turned off.");
      await reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={<StepTitle n={1}>Single sign-on</StepTitle>} icon={<KeyRound size={16} />} actions={<Badge tone={status.tone} dot>{status.label}</Badge>}>
      <div className="ui-stack">
        {sso.configured && !ours && existing ? (
          <Alert tone="warning">
            This workspace signs in with {existing.name} ({existing.issuer}). Saving here replaces it with {LABEL[key]}.
          </Alert>
        ) : null}
        <Steps items={ssoSteps(key)} />
        <div className="iid-urls">
          <UrlRow label="Redirect URI" value={sso.redirect_uri} />
          {issuer ? <UrlRow label="Issuer" value={issuer} hint="AIVA checks that every sign-in token comes from exactly this issuer." /> : null}
          {key === "okta" ? <UrlRow label="Sign-in page (initiate login URI)" value={sso.login_url} /> : null}
        </div>
        {error ? <Alert>{error}</Alert> : null}
        {checked ? <Alert tone="success">{checked}</Alert> : null}
        <div className="ui-form-grid">
          <Field label={key === "entra_id" ? "Application (client) ID" : "Client ID"} required>
            <input className="ui-input ui-mono" value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
          <Field
            label="Client secret"
            required={!(ours && existing?.has_secret)}
            hint={ours && existing?.has_secret ? "Saved. Leave empty to keep it." : "Stored encrypted; never shown again."}
          >
            <input className="ui-input ui-mono" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />
          </Field>
          <Field label="Email domains" required hint="People with these addresses are sent to your provider. Separate with commas.">
            <input className="ui-input" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="acme.com, acme.co.uk" />
          </Field>
          {key === "okta" ? (
            <Field label="Authorization server" hint="Empty for the Okta org server; or a custom server ID, e.g. default.">
              <input className="ui-input ui-mono" value={server} onChange={(e) => setServer(e.target.value)} placeholder="(org server)" spellCheck={false} />
            </Field>
          ) : null}
        </div>
        <SettingRow
          label="Create accounts at first sign-in"
          hint={extra.scim?.has_token ? "Usually off when provisioning (SCIM) creates people for you." : "People on your domains who aren't in AIVA yet get an account when they first sign in."}
        >
          <Toggle checked={autoCreate} onChange={setAutoCreate} label="Create accounts at first sign-in" />
        </SettingRow>
        {autoCreate ? (
          <SettingRow label="Their role">
            <select className="ui-select" value={role} onChange={(e) => setRole(e.target.value)}>
              {roles.map((r) => (
                <option key={r.key} value={r.key}>
                  {r.name}
                </option>
              ))}
            </select>
          </SettingRow>
        ) : null}
        <SettingRow label="Single sign-on is on">
          <Toggle checked={active} onChange={setActive} label="Single sign-on is on" />
        </SettingRow>
        <div className="iid-actions">
          <button type="button" className="ui-btn" disabled={busy} onClick={() => void check()}>
            <RefreshCw size={14} /> Check configuration
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-primary"
            disabled={busy || !clientId.trim() || !domainList.length || (!(ours && existing?.has_secret) && !secret.trim())}
            onClick={() => void save()}
          >
            {busy ? <span className="ui-spinner" /> : null}
            Save
          </button>
        </div>
        {ours && sso.active ? (
          <div className="iid-test">
            <Field label="Test sign-in" hint="Opens your provider's sign-in in a new tab. Finishing signs that tab's browser in as the person you test with, so use a private window to test as someone else.">
              <div className="iid-inline">
                <input className="ui-input" type="email" value={testEmail} onChange={(e) => setTestEmail(e.target.value)} placeholder="you@company.com" />
                <a
                  className={`ui-btn${testEmail.includes("@") ? "" : " iid-disabled"}`}
                  href={testEmail.includes("@") ? testUrl : undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-disabled={!testEmail.includes("@")}
                >
                  <ExternalLink size={14} /> Test sign-in
                </a>
              </div>
            </Field>
          </div>
        ) : null}
        {existing?.updated_at ? <span className="ui-muted ui-small">Single sign-on last saved {dateTime(existing.updated_at)}</span> : null}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ 2 · provisioning (SCIM) */

function ScimCard({ detail, reload }: PanelProps) {
  const toast = useToast();
  const key = detail.integration.key as IdpKey;
  const extra = detail.extra as IdentityExtra;
  const scim = extra.scim;
  const info = scim?.token ?? null;
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"rotate" | "revoke" | null>(null);
  if (!scim) return null;

  async function create() {
    setBusy(true);
    try {
      const result = await api.post<{ token: string }>(`/identity/${key}/scim-token`);
      setFresh(result.token);
      setConfirm(null);
      toast(info ? "New token created. The old one stopped working." : "Token created. Copy it now.");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    setBusy(true);
    try {
      await api.del(`/identity/${key}/scim-token`);
      setFresh(null);
      setConfirm(null);
      toast("Token revoked. Provisioning stopped; nobody was removed.");
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title={<StepTitle n={2}>Provisioning (SCIM 2.0)</StepTitle>}
      icon={<Users size={16} />}
      actions={<Badge tone={info?.last_used_at ? "success" : info ? "info" : "neutral"} dot>{info?.last_used_at ? "Receiving updates" : info ? "Token ready" : "No token"}</Badge>}
    >
      <div className="ui-stack">
        <p className="iid-text">
          {LABEL[key]} creates, updates and deactivates AIVA users and keeps group membership in sync. Deactivated people are signed out of this workspace and
          their API keys here are revoked; their other workspaces aren&apos;t affected. Turning this integration off pauses provisioning without removing anyone.
        </p>
        <Steps items={scimSteps(key)} />
        <UrlRow label={key === "entra_id" ? "Tenant URL" : "SCIM connector base URL"} value={scim.base_url} />
        <div className="iid-token">
          <div className="settings-row-label">{key === "entra_id" ? "Secret token" : "API token (HTTP header)"}</div>
          {fresh ? (
            <>
              <Alert tone="warning">Copy this token now. AIVA keeps only a fingerprint of it, so it can&apos;t be shown again.</Alert>
              <CopyField value={fresh} label="Copy SCIM token" />
            </>
          ) : info ? (
            <div className="iid-token-meta">
              <code className="ui-mono">{info.hint}</code>
              <span className="ui-muted ui-small">
                Created {info.created_at ? relativeTime(info.created_at) : ""}
                {info.created_by ? ` by ${info.created_by}` : ""} · {info.last_used_at ? `last used ${relativeTime(info.last_used_at)}` : "not used yet"}
              </span>
            </div>
          ) : (
            <span className="ui-muted ui-small">No token yet. Create one and paste it into {LABEL[key]}.</span>
          )}
          <div className="iid-actions">
            <button type="button" className={`ui-btn${info ? "" : " ui-btn-primary"}`} disabled={busy} onClick={() => (info ? setConfirm("rotate") : void create())}>
              {busy && !confirm ? <span className="ui-spinner" /> : <KeyRound size={14} />} {info ? "Replace token" : "Create token"}
            </button>
            {info ? (
              <button type="button" className="ui-btn ui-btn-ghost" disabled={busy} onClick={() => setConfirm("revoke")}>
                <Trash2 size={14} /> Revoke
              </button>
            ) : null}
          </div>
        </div>
      </div>
      <ConfirmDialog
        open={confirm === "rotate"}
        title="Replace the SCIM token?"
        message={`The current token stops working at once. Paste the new one into ${LABEL[key]} to keep provisioning running.`}
        confirmLabel="Replace token"
        danger={false}
        busy={busy}
        onConfirm={() => void create()}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "revoke"}
        title="Revoke the SCIM token?"
        message={`${LABEL[key]} won't be able to create, update or deactivate people until you create a new token. Nobody is removed.`}
        confirmLabel="Revoke"
        busy={busy}
        onConfirm={() => void revoke()}
        onClose={() => setConfirm(null)}
      />
    </Card>
  );
}

/* ------------------------------------------------------------------ 3 · group mapping */

function MappingCard({ detail, reload }: PanelProps) {
  const toast = useToast();
  const key = detail.integration.key as IdpKey;
  const extra = detail.extra as IdentityExtra;
  const mapping = extra.mapping;
  const roles = extra.roles ?? [];
  const teams = extra.teams ?? [];
  const pushed = extra.groups ?? [];
  const [defaultRole, setDefaultRole] = useState(mapping?.default_role ?? "member");
  const [autoTeams, setAutoTeams] = useState(Boolean(mapping?.auto_teams));
  const [rows, setRows] = useState<Mapping[]>(mapping?.group_mappings ?? []);
  const [directory, setDirectory] = useState<DirectoryGroup[] | null>(null);
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const known = useMemo(() => {
    const byName = new Map<string, { id: string; name: string; source: string }>();
    for (const group of directory ?? []) byName.set(group.name.toLowerCase(), { id: group.id, name: group.name, source: "directory" });
    for (const group of pushed) byName.set(group.name.toLowerCase(), { id: group.external_id || group.id, name: group.name, source: "pushed" });
    return byName;
  }, [directory, pushed]);
  const teamName = (id: string | null) => teams.find((t) => t.id === id)?.name ?? null;
  const listId = `iid-groups-${key}`;

  async function loadDirectory() {
    setLoadingGroups(true);
    setError(null);
    try {
      const result = await api.get<{ items: DirectoryGroup[] }>(`/identity/${key}/directory-groups`);
      setDirectory(result.items);
      toast(`${result.items.length} group${result.items.length === 1 ? "" : "s"} loaded from ${key === "entra_id" ? "Microsoft Graph" : "Okta"}.`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingGroups(false);
    }
  }

  function update(index: number, change: Partial<Mapping>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...change } : row)));
  }

  function setGroup(index: number, name: string) {
    const match = known.get(name.trim().toLowerCase());
    update(index, { group_name: name, group_id: match ? match.id : null });
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.put<{ changed: number }>(`/identity/${key}/mapping`, {
        default_role: defaultRole,
        auto_teams: autoTeams,
        group_mappings: rows.filter((r) => r.group_name.trim() && (r.role || r.team_id)),
      });
      toast(result.changed ? `Mapping saved. Roles or teams changed for ${result.changed} ${result.changed === 1 ? "person" : "people"}.` : "Mapping saved.");
      await reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={<StepTitle n={3}>Groups → roles and teams</StepTitle>} icon={<ShieldCheck size={16} />}>
      <div className="ui-stack">
        <p className="iid-text">
          When {LABEL[key]} adds someone to a mapped group, they get that role and join that team. If someone is in several mapped groups, the first one in this
          list decides their role. Owners never change, and a role an admin sets by hand is kept until a mapped group applies again.
        </p>
        {error ? <Alert>{error}</Alert> : null}
        <SettingRow label="Role for new people" hint="Given to everyone provisioning creates, until a mapped group says otherwise.">
          <select className="ui-select" value={defaultRole} onChange={(e) => setDefaultRole(e.target.value)}>
            {roles.map((r) => (
              <option key={r.key} value={r.key}>
                {r.name}
              </option>
            ))}
          </select>
        </SettingRow>
        <SettingRow label="Create a team for every other group" hint="Groups without a team below get an AIVA team with the same name. Teams are never deleted.">
          <Toggle checked={autoTeams} onChange={setAutoTeams} label="Create a team for every other group" />
        </SettingRow>
        <datalist id={listId}>
          {[...known.values()].map((g) => (
            <option key={`${g.source}-${g.id}`} value={g.name} />
          ))}
        </datalist>
        <div className="iid-mappings">
          {rows.length ? (
            <div className="iid-map-head" aria-hidden>
              <span>{LABEL[key]} group</span>
              <span>AIVA role</span>
              <span>AIVA team</span>
              <span />
            </div>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>No groups mapped yet.</p>
          )}
          {rows.map((row, index) => (
            <div className="iid-map-row" key={index}>
              <input
                className="ui-input"
                list={listId}
                value={row.group_name}
                onChange={(e) => setGroup(index, e.target.value)}
                placeholder="Group name"
                aria-label="Group"
              />
              <select className="ui-select" value={row.role ?? ""} onChange={(e) => update(index, { role: e.target.value || null })} aria-label="Role">
                <option value="">No role change</option>
                {roles.map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.name}
                  </option>
                ))}
              </select>
              <select className="ui-select" value={row.team_id ?? ""} onChange={(e) => update(index, { team_id: e.target.value || null })} aria-label="Team">
                <option value="">No team</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" onClick={() => setRows((current) => current.filter((_, i) => i !== index))} aria-label="Remove mapping">
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
        <div className="iid-actions">
          <button type="button" className="ui-btn" onClick={() => setRows((current) => [...current, { group_id: null, group_name: "", role: null, team_id: null }])} disabled={rows.length >= 200}>
            <Plus size={14} /> Add group
          </button>
          {extra.directory_api ? (
            <button type="button" className="ui-btn ui-btn-ghost" onClick={() => void loadDirectory()} disabled={loadingGroups}>
              {loadingGroups ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Load groups from {key === "entra_id" ? "Entra" : "Okta"}
            </button>
          ) : null}
          <button type="button" className="ui-btn ui-btn-primary iid-push" onClick={() => void save()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null}
            Save and apply
          </button>
        </div>
        {!extra.directory_api ? (
          <span className="ui-muted ui-small">
            Group names come from the groups {LABEL[key]} pushes. To pick from every group in your directory, add{" "}
            {key === "entra_id" ? "an app registration with Group.Read.All" : "a read-only API token"} to the connection above.
          </span>
        ) : null}
        {pushed.length ? (
          <div className="ui-table-wrap iid-table">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Pushed group</th>
                  <th>Members</th>
                  <th>Maps to</th>
                </tr>
              </thead>
              <tbody>
                {pushed.map((group) => {
                  const match = (mapping?.group_mappings ?? []).find(
                    (m) => (m.group_id && (m.group_id === group.id || m.group_id === group.external_id)) || m.group_name.toLowerCase() === group.name.toLowerCase(),
                  );
                  const role = match?.role ? roles.find((r) => r.key === match.role)?.name ?? match.role : null;
                  const team = teamName(match?.team_id ?? null) ?? (group.auto_team_id ? teamName(group.auto_team_id) : null);
                  return (
                    <tr key={group.id}>
                      <td>{group.name}</td>
                      <td className="ui-num">{group.members}</td>
                      <td className="ui-muted">{[role, team].filter(Boolean).join(" · ") || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ 4 · verified domains and require single sign-on */

const DOMAIN_TONE: Record<DomainRow["status"], Tone> = { verified: "success", pending: "neutral", failed: "danger" };

function PolicyCard() {
  const toast = useToast();
  const policy = useApi<Policy>("/identity/policy");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const data = policy.data;

  async function run(label: string, action: () => Promise<Policy>, done?: (next: Policy) => string | null) {
    setBusy(label);
    setError(null);
    try {
      const next = await action();
      policy.setData(next);
      const message = done?.(next);
      if (message) toast(message);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  if (policy.error) return <Alert tone="warning">{policy.error}</Alert>;
  return (
    <Card title={<StepTitle n={4}>Verified domains and required sign-on</StepTitle>} icon={<Globe size={16} />}>
      {!data ? (
        <LoadingRows rows={3} />
      ) : (
        <div className="ui-stack">
          {error ? <Alert>{error}</Alert> : null}
          <p className="iid-text">
            Prove your company owns its email domains with a DNS TXT record. With <strong>Require single sign-on</strong> on, members whose email is on a verified
            domain must sign in through single sign-on — passwords and Google, Microsoft or Apple sign-in are refused for this workspace. Owners keep their password
            as a break-glass, API keys keep working, and the person&apos;s other workspaces aren&apos;t affected.
          </p>
          <div className="iid-domains">
            {data.domains.map((row) => (
              <div key={row.domain} className="iid-domain">
                <div className="iid-domain-head">
                  <strong>{row.domain}</strong>
                  <Badge tone={DOMAIN_TONE[row.status]} dot>
                    {row.status === "verified" ? "Verified" : row.status === "failed" ? "Not found yet" : "Waiting for DNS"}
                  </Badge>
                  {row.status === "verified" && !row.in_sso ? <Badge tone="warning">Not in single sign-on domains</Badge> : null}
                  <span className="iid-spacer" />
                  <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void run(`check:${row.domain}`, () => api.post<Policy>(`/identity/domains/${encodeURIComponent(row.domain)}/verify`), (next) => next.check?.message ?? null)}>
                    {busy === `check:${row.domain}` ? <span className="ui-spinner" /> : <RefreshCw size={13} />} {row.status === "verified" ? "Re-check" : "Check now"}
                  </button>
                  <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" disabled={busy !== null} onClick={() => setRemoving(row.domain)} aria-label={`Remove ${row.domain}`}>
                    <Trash2 size={14} />
                  </button>
                </div>
                {row.status !== "verified" ? (
                  <div className="iid-record">
                    <span className="ui-muted ui-small">
                      Add a TXT record on <code>{row.record_name}</code> (or <code>{row.alternate_record_name}</code>) with this value:
                    </span>
                    <CopyField value={row.record_value} label={`Copy TXT value for ${row.domain}`} />
                  </div>
                ) : null}
                {row.error ? <span className={`ui-small ${row.status === "verified" ? "ui-muted" : "iid-error"}`}>{row.error}</span> : null}
                <span className="ui-muted ui-small">
                  {row.verified_at ? `Verified ${relativeTime(row.verified_at)}` : row.added_at ? `Added ${relativeTime(row.added_at)}` : ""}
                  {row.checked_at ? ` · last checked ${relativeTime(row.checked_at)}` : ""}
                </span>
              </div>
            ))}
          </div>
          <form
            className="iid-inline"
            onSubmit={(event) => {
              event.preventDefault();
              if (!domain.trim()) return;
              void run("add", () => api.post<Policy>("/identity/domains", { domain: domain.trim() }), () => {
                setDomain("");
                return "Domain added. Publish the TXT record, then choose Check now.";
              });
            }}
          >
            <input className="ui-input" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.com" aria-label="Domain to verify" />
            <button type="submit" className="ui-btn" disabled={busy !== null || !domain.trim()}>
              <Plus size={14} /> Add domain
            </button>
          </form>
          <div className="iid-require">
            <SettingRow
              label="Require single sign-on"
              hint={
                data.enforced
                  ? `On for ${data.enforced_domains.map((d) => `@${d}`).join(", ")} — ${data.covered_members} member${data.covered_members === 1 ? "" : "s"} must use single sign-on.`
                  : data.require_sso
                    ? "Turned on, but not enforced until the problems below are fixed — nobody is locked out meanwhile."
                    : "Off. Members can also sign in with a password, Google, Microsoft or Apple."
              }
            >
              <Toggle
                checked={data.require_sso}
                disabled={busy !== null}
                onChange={(value) =>
                  void run("require", () => api.put<Policy>("/identity/policy", { require_sso: value }), () =>
                    value ? "Single sign-on is now required for your verified domains." : "Single sign-on is no longer required.",
                  )
                }
                label="Require single sign-on"
              />
            </SettingRow>
            {data.problems.length ? (
              <ul className="iid-problems">
                {data.problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            ) : null}
            {!data.you.owner && data.you.covered && !data.you.signed_in_with_sso ? (
              <Alert tone="warning">You&apos;re covered by this policy. Use Test sign-in above once before requiring single sign-on, so you can&apos;t be locked out.</Alert>
            ) : null}
          </div>
        </div>
      )}
      <ConfirmDialog
        open={removing !== null}
        title={`Remove ${removing ?? ""}?`}
        message="Single sign-on stops being required for this domain. You can add it again later; it will need a new DNS check."
        confirmLabel="Remove"
        busy={busy !== null}
        onConfirm={() => {
          const target = removing;
          setRemoving(null);
          if (target) void run(`remove:${target}`, () => api.del<Policy>(`/identity/domains/${encodeURIComponent(target)}`), () => `${target} removed.`);
        }}
        onClose={() => setRemoving(null)}
      />
    </Card>
  );
}

/* ------------------------------------------------------------------ provisioning activity */

const EVENT_LABELS: Record<string, string> = {
  scim_user_created: "created",
  scim_user_updated: "updated",
  scim_user_deactivated: "deactivated",
  scim_user_reactivated: "reactivated",
  scim_user_removed: "removed",
  scim_group_created: "groups created",
  scim_group_updated: "group changes",
  scim_group_deleted: "groups deleted",
};

function ActivityCard({ detail }: PanelProps) {
  const key = detail.integration.key as IdpKey;
  const extra = detail.extra as IdentityExtra;
  const events = extra.provisioning ?? [];
  const month = extra.stats?.last_30_days ?? {};
  const summary = Object.entries(EVENT_LABELS)
    .filter(([action]) => month[action])
    .map(([action, label]) => `${month[action]} ${label}`);
  return (
    <Card
      title="Provisioning activity"
      icon={<Activity size={16} />}
      actions={
        <Link className="ui-btn ui-btn-ghost ui-btn-sm" href={`/integrations/activity?provider=${key}`}>
          All activity
        </Link>
      }
    >
      {summary.length ? <p className="iid-text iid-summary">Last 30 days: {summary.join(" · ")}.</p> : null}
      {events.length ? (
        <ul className="int-logs">
          {events.map((event) => (
            <li key={event.id} className={`int-log level-${event.level}`}>
              <span className="int-log-dot" aria-hidden />
              <div>
                <div className="int-log-message">{event.message || event.action.replace(/_/g, " ")}</div>
                <div className="ui-muted ui-small">{event.at ? relativeTime(event.at) : ""}</div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Nothing provisioned yet. Changes from {LABEL[key]} appear here as they arrive.
        </p>
      )}
    </Card>
  );
}

/** Provider panels for this group, by integration key. */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  entra_id: IdentityPanel,
  okta: IdentityPanel,
};
