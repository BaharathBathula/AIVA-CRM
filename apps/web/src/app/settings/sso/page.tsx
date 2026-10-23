"use client";

import { KeyRound, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, PageHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Connection {
  id: string;
  name: string;
  issuer: string;
  client_id: string;
  has_secret: boolean;
  domains: string[];
  default_role: string;
  auto_provision: boolean;
  is_active: boolean;
  updated_at: string;
}

interface SsoSettings {
  connection: Connection | null;
  redirect_uri: string;
  available: boolean;
  roles: { key: string; name: string }[];
}

export default function SsoPage() {
  return (
    <SettingsShell permission="admin.settings" what="single sign-on" feature="sso">
      <SsoSettingsView />
    </SettingsShell>
  );
}

function SsoSettingsView() {
  const toast = useToast();
  const data = useApi<SsoSettings>("/sso");
  const [name, setName] = useState("");
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [domains, setDomains] = useState("");
  const [role, setRole] = useState("member");
  const [autoProvision, setAutoProvision] = useState(true);
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tested, setTested] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const connection = data.data?.connection ?? null;

  useEffect(() => {
    if (!data.data) return;
    const c = data.data.connection;
    setName(c?.name ?? "Okta");
    setIssuer(c?.issuer ?? "");
    setClientId(c?.client_id ?? "");
    setDomains((c?.domains ?? []).join(", "));
    setRole(c?.default_role ?? "member");
    setAutoProvision(c?.auto_provision ?? true);
    setActive(c?.is_active ?? true);
  }, [data.data]);

  async function test() {
    setBusy(true);
    setError(null);
    setTested(null);
    try {
      const result = await api.post<{ issuer: string; authorization_endpoint: string }>("/sso/test", { issuer });
      setTested(`Found ${result.issuer}. Sign-in page: ${new URL(result.authorization_endpoint).host}.`);
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
      const saved = await api.put<Connection>("/sso", {
        name: name.trim(),
        issuer: issuer.trim(),
        client_id: clientId.trim(),
        client_secret: secret.trim() || null,
        domains: domains
          .split(/[\s,]+/)
          .map((d) => d.trim())
          .filter(Boolean),
        default_role: role,
        auto_provision: autoProvision,
        is_active: active,
      });
      setSecret("");
      data.setData(data.data ? { ...data.data, connection: saved } : data.data);
      toast(saved.is_active ? "Single sign-on is on." : "Single sign-on saved and turned off.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del("/sso");
      toast("Single sign-on removed.");
      setRemoving(false);
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  if (data.error) return <Alert>{data.error}</Alert>;
  if (!data.data) {
    return (
      <Card>
        <LoadingRows rows={8} />
      </Card>
    );
  }

  return (
    <>
      <PageHeader
        title="Single sign-on"
        description="Let your team sign in through your company's identity provider — Okta, Microsoft Entra ID, Google Workspace, OneLogin, JumpCloud or any OpenID Connect provider — with “Log In with your Organization”."
        actions={
          connection ? (
            <Badge tone={connection.is_active ? "success" : "neutral"}>
              {connection.is_active ? "On" : "Off"}
            </Badge>
          ) : null
        }
      />
      <div className="ui-split" style={{ alignItems: "start" }}>
        <Card title="Identity provider" icon={<KeyRound size={16} />}>
          <div className="ui-stack">
            {error ? <Alert>{error}</Alert> : null}
            {tested ? (
              <Alert tone="success">{tested}</Alert>
            ) : null}
            <div className="ui-form-grid">
              <Field label="Name people see" hint="Shown on the sign-in page, e.g. Okta or Microsoft Entra ID.">
                <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Issuer URL" required hint="From your provider, e.g. https://acme.okta.com or https://login.microsoftonline.com/<tenant>/v2.0">
                <input className="ui-input ui-mono" value={issuer} onChange={(e) => setIssuer(e.target.value)} placeholder="https://acme.okta.com" />
              </Field>
              <Field label="Client ID" required>
                <input className="ui-input ui-mono" value={clientId} onChange={(e) => setClientId(e.target.value)} />
              </Field>
              <Field label="Client secret" required={!connection?.has_secret} hint={connection?.has_secret ? "Saved. Leave empty to keep it." : "Stored encrypted."}>
                <input className="ui-input ui-mono" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />
              </Field>
            </div>
            <Field label="Email domains" required hint="People with these email addresses are sent to your provider. Separate with commas.">
              <input className="ui-input" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="acme.com, acme.co.uk" />
            </Field>
            <div className="ui-form-grid">
              <Field label="Role for people signing in the first time">
                <select className="ui-select" value={role} onChange={(e) => setRole(e.target.value)} disabled={!autoProvision}>
                  {data.data.roles.map((r) => (
                    <option key={r.key} value={r.key}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="ui-stack" style={{ gap: 10, paddingTop: 26 }}>
                <label className="ui-check">
                  <input type="checkbox" checked={autoProvision} onChange={(e) => setAutoProvision(e.target.checked)} /> Create accounts for new people automatically
                </label>
                <label className="ui-check">
                  <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Single sign-on is on
                </label>
              </div>
            </div>
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="ui-btn" disabled={busy || !issuer.trim()} onClick={() => void test()}>
                Test connection
              </button>
              <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !issuer.trim() || !clientId.trim() || !domains.trim()} onClick={() => void save()}>
                Save
              </button>
              {connection ? (
                <button type="button" className="ui-btn ui-btn-ghost" style={{ marginLeft: "auto" }} onClick={() => setRemoving(true)}>
                  <Trash2 size={14} /> Remove
                </button>
              ) : null}
            </div>
            {connection ? <span className="ui-muted ui-small">Last saved {dateTime(connection.updated_at)}</span> : null}
          </div>
        </Card>
        <Card title="Set up your provider">
          <ol className="install-steps">
            <li>In your identity provider, create an OpenID Connect web application for AIVA.</li>
            <li>Add this sign-in redirect URI:</li>
          </ol>
          <div style={{ margin: "10px 0 14px" }}>
            <CopyField value={data.data.redirect_uri} label="Copy redirect URI" />
          </div>
          <ol className="install-steps" start={3}>
            <li>Allow the scopes openid, email and profile, and assign the app to the people who should use AIVA.</li>
            <li>Paste the issuer URL, client ID and client secret here, add your email domains and save.</li>
            <li>
              People choose <strong>Log In with your Organization</strong> on the sign-in page and enter their work email.
            </li>
          </ol>
          <p className="ui-muted ui-small" style={{ marginBottom: 0 }}>
            Sessions started this way only open this workspace. Two-step verification in AIVA still applies to people who turned it on.
          </p>
        </Card>
      </div>
      <ConfirmDialog
        open={removing}
        title="Remove single sign-on?"
        message="People who only sign in through your provider will need a password (Forgot password) or Google/Microsoft to get back in."
        confirmLabel="Remove"
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setRemoving(false)}
      />
    </>
  );
}
