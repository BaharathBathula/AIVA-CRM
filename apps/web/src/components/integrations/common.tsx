"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle2, CircleDashed, CircleOff, ExternalLink, KeyRound, Lock, PlugZap, RefreshCw } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { Alert, Badge, Card, Field, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

import type { CatalogItem, Connection, IntegrationDef, IntegrationDetail, IntegrationLog, IntegrationStatus } from "./types";

/** Brand-coloured monogram tile (no third-party logos). */
export function IntegrationTile({ item, size = 44 }: { item: Pick<IntegrationDef, "color" | "monogram" | "name">; size?: number }) {
  const text = item.monogram || item.name.slice(0, 1);
  return (
    <span
      className="int-tile"
      aria-hidden
      style={{ width: size, height: size, background: item.color, fontSize: text.length > 2 ? size * 0.28 : text.length === 2 ? size * 0.34 : size * 0.44 }}
    >
      {text}
    </span>
  );
}

export const STATUS: Record<IntegrationStatus, { label: string; tone: Tone; icon: typeof CheckCircle2 }> = {
  connected: { label: "Connected", tone: "success", icon: CheckCircle2 },
  ready: { label: "On", tone: "success", icon: CheckCircle2 },
  not_connected: { label: "Not connected", tone: "warning", icon: CircleDashed },
  error: { label: "Problem", tone: "danger", icon: AlertTriangle },
  needs_reauth: { label: "Reconnect needed", tone: "danger", icon: AlertTriangle },
  off: { label: "Off", tone: "neutral", icon: CircleOff },
  upgrade: { label: "Upgrade", tone: "neutral", icon: Lock },
};

export function StatusBadge({ status }: { status: IntegrationStatus }) {
  const info = STATUS[status] ?? STATUS.off;
  return (
    <Badge tone={info.tone} dot>
      {info.label}
    </Badge>
  );
}

/** The on/off switch used on cards and the detail header. */
export function IntegrationSwitch({
  item,
  canManage,
  onChanged,
}: {
  item: Pick<CatalogItem, "key" | "name" | "switched_on" | "available">;
  canManage: boolean;
  onChanged: (detail: IntegrationDetail) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function flip(next: boolean) {
    setBusy(true);
    try {
      const detail = await api.put<IntegrationDetail>(`/integration-hub/${item.key}/toggle`, { enabled: next });
      toast(`${item.name} turned ${next ? "on" : "off"}.`);
      onChanged(detail);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }
  return (
    <label className={`ui-switch${busy ? " busy" : ""}`} title={!canManage ? "Only admins can turn integrations on or off" : item.switched_on ? "On" : "Off"}>
      <input
        type="checkbox"
        role="switch"
        aria-label={`${item.name} ${item.switched_on ? "on" : "off"}`}
        checked={item.switched_on}
        disabled={!canManage || busy || !item.available}
        onChange={(event) => void flip(event.target.checked)}
      />
      <span />
    </label>
  );
}

/** Starts the provider sign-in and sends the browser there. */
export function useConnect(key: string) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function connect(returnTo?: string) {
    setBusy(true);
    try {
      const result = await api.post<{ authorize_url: string }>(`/integration-hub/${key}/connect`, { return_to: returnTo ?? `/integrations/${key}` });
      window.location.href = result.authorize_url;
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }
  return { connect, busy };
}

export function ConnectButton({ detail, label }: { detail: IntegrationDetail; label?: string }) {
  const { connect, busy } = useConnect(detail.integration.key);
  const reconnect = Boolean(detail.connection);
  return (
    <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !detail.enabled} onClick={() => void connect()}>
      {busy ? <span className="ui-spinner" /> : reconnect ? <RefreshCw size={15} /> : <PlugZap size={15} />}
      {label ?? (reconnect ? "Reconnect" : `Connect ${detail.integration.personal ? "your account" : detail.integration.name}`)}
    </button>
  );
}

export function DisconnectButton({ detail, userId, onDone, label = "Disconnect" }: { detail: IntegrationDetail; userId?: string; onDone: (detail: IntegrationDetail) => void; label?: string }) {
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      const next = await api.del<IntegrationDetail>(`/integration-hub/${detail.integration.key}/connection${userId ? `?user_id=${userId}` : ""}`);
      toast("Disconnected.");
      onDone(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  }
  if (!confirm) {
    return (
      <button type="button" className="ui-btn ui-btn-ghost ui-btn-danger" onClick={() => setConfirm(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="ui-row" style={{ gap: 6 }}>
      <span className="ui-small ui-muted">Sure?</span>
      <button type="button" className="ui-btn ui-btn-sm ui-btn-danger solid" disabled={busy} onClick={() => void run()}>
        {busy ? <span className="ui-spinner" /> : null} Yes, disconnect
      </button>
      <button type="button" className="ui-btn ui-btn-sm" onClick={() => setConfirm(false)}>
        Cancel
      </button>
    </span>
  );
}

export function TestButton({ detail, label = "Send a test" }: { detail: IntegrationDetail; label?: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      const result = await api.post<{ message: string }>(`/integration-hub/${detail.integration.key}/test`);
      toast(result.message);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }
  return (
    <button type="button" className="ui-btn" disabled={busy} onClick={() => void run()}>
      {busy ? <span className="ui-spinner" /> : null} {label}
    </button>
  );
}

/** Summary of one connected account. */
export function ConnectionSummary({ connection, children }: { connection: Connection; children?: ReactNode }) {
  return (
    <div className="int-connection">
      <div className="int-connection-main">
        <StatusBadge status={connection.status} />
        <strong>{connection.account_label ?? "Connected account"}</strong>
        <span className="ui-muted ui-small">
          Connected {relativeTime(connection.connected_at)}
          {connection.last_sync_at ? ` · last synced ${relativeTime(connection.last_sync_at)}` : ""}
          {connection.last_used_at && !connection.last_sync_at ? ` · last used ${relativeTime(connection.last_used_at)}` : ""}
        </span>
      </div>
      {connection.last_error ? <Alert tone={connection.status === "connected" ? "warning" : "danger"}>{connection.last_error}</Alert> : null}
      {children ? <div className="ui-actions">{children}</div> : null}
    </div>
  );
}

/** Form for integrations that connect with keys from the provider's console. */
export function CredentialsForm({
  detail,
  onSaved,
  fields,
  submitLabel = "Save and connect",
}: {
  detail: IntegrationDetail;
  onSaved: (detail: IntegrationDetail) => void;
  fields?: IntegrationDef["credential_fields"];
  submitLabel?: string;
}) {
  const toast = useToast();
  const list = fields ?? detail.integration.credential_fields;
  const connection = detail.connection;
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(list.map((f) => [f.key, f.secret ? "" : String((connection?.config ?? {})[f.key] ?? "")])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const next = await api.put<IntegrationDetail>(`/integration-hub/${detail.integration.key}/credentials`, { values });
      toast(`${detail.integration.name} connected.`);
      setValues((current) => Object.fromEntries(Object.entries(current).map(([k, v]) => [k, list.find((f) => f.key === k)?.secret ? "" : v])));
      onSaved(next);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="ui-stack" style={{ gap: 14 }} onSubmit={(event) => void submit(event)}>
      {error ? <Alert>{error}</Alert> : null}
      <div className="ui-form-grid">
        {list.map((field) => {
          const stored = Boolean(connection?.secrets?.[field.key]);
          return (
            <Field
              key={field.key}
              label={field.label}
              required={field.required && !stored}
              hint={field.secret && stored ? "Saved. Leave blank to keep it, or paste a new one." : field.help || undefined}
              className={list.length === 1 ? "span-2" : undefined}
            >
              <input
                className="ui-input"
                type={field.secret ? "password" : "text"}
                autoComplete="off"
                placeholder={field.secret && stored ? "••••••••" : field.placeholder}
                value={values[field.key] ?? ""}
                disabled={!detail.can_manage}
                onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
              />
            </Field>
          );
        })}
      </div>
      {detail.can_manage ? (
        <div>
          <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || !detail.enabled}>
            {busy ? <span className="ui-spinner" /> : <KeyRound size={15} />} {submitLabel}
          </button>
          {!detail.enabled ? <span className="ui-muted ui-small" style={{ marginLeft: 10 }}>Turn {detail.integration.name} on first.</span> : null}
        </div>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Only admins can change these.
        </p>
      )}
    </form>
  );
}

/** Where to register AIVA with the provider, and the option to use the workspace's own app. */
export function OAuthAppCard({ detail, onSaved }: { detail: IntegrationDetail; onSaved: () => void }) {
  const toast = useToast();
  const oauth = detail.oauth;
  const [open, setOpen] = useState(false);
  const [clientId, setClientId] = useState(oauth?.workspace_client_id ?? "");
  const [clientSecret, setClientSecret] = useState("");
  const [tenant, setTenant] = useState(oauth?.options?.tenant ?? "");
  const [environment, setEnvironment] = useState(oauth?.options?.environment ?? (oauth?.family === "intuit" ? "production" : "demo"));
  const [busy, setBusy] = useState(false);
  if (!oauth || !detail.can_manage) return null;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!oauth) return;
    setBusy(true);
    try {
      await api.put(`/integration-hub/apps/${oauth.family}`, {
        client_id: clientId,
        client_secret: clientSecret || null,
        ...(oauth.family === "microsoft" ? { tenant } : {}),
        ...(oauth.family === "docusign" || oauth.family === "intuit" ? { environment } : {}),
      });
      toast(`${oauth.label} app saved.`);
      setClientSecret("");
      onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!oauth) return;
    setBusy(true);
    try {
      await api.del(`/integration-hub/apps/${oauth.family}`);
      toast("Now using the server's app.");
      setClientId("");
      onSaved();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={`${oauth.label} app`} icon={<KeyRound size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          {oauth.configured
            ? oauth.source === "workspace"
              ? `People sign in through your workspace's own ${oauth.label} app.`
              : `People sign in through this server's ${oauth.label} app.`
            : `Signing in with ${oauth.label} isn't set up yet. Create an app in the ${oauth.label} developer console and add its client ID and secret below.`}{" "}
          The redirect URI to register is:
        </p>
        <CopyField value={oauth.redirect_uri} label="Copy the redirect URI" />
        <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
          {oauth.console_url ? (
            <a className="ui-btn ui-btn-sm" href={oauth.console_url} target="_blank" rel="noopener noreferrer">
              <ExternalLink size={14} /> Open the {oauth.label} console
            </a>
          ) : null}
          <button type="button" className="ui-btn ui-btn-sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {oauth.source === "workspace" ? "Change your app" : "Use your own app"}
          </button>
          {oauth.source === "workspace" ? (
            <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy} onClick={() => void remove()}>
              Remove your app
            </button>
          ) : null}
        </div>
        {open ? (
          <form className="ui-form-grid" onSubmit={(event) => void save(event)}>
            <Field label="Client ID" required>
              <input className="ui-input" value={clientId} onChange={(event) => setClientId(event.target.value)} autoComplete="off" />
            </Field>
            <Field label="Client secret" hint={oauth.source === "workspace" ? "Leave blank to keep the saved secret." : undefined}>
              <input className="ui-input" type="password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} autoComplete="off" />
            </Field>
            {oauth.family === "microsoft" ? (
              <Field label="Tenant" hint="Your directory (tenant) ID, or leave blank for any work or personal account.">
                <input className="ui-input" value={tenant} onChange={(event) => setTenant(event.target.value)} placeholder="common" />
              </Field>
            ) : null}
            {oauth.family === "docusign" ? (
              <Field label="Environment">
                <select className="ui-select" value={environment} onChange={(event) => setEnvironment(event.target.value)}>
                  <option value="demo">Developer sandbox (demo)</option>
                  <option value="production">Production</option>
                </select>
              </Field>
            ) : null}
            {oauth.family === "intuit" ? (
              <Field label="Environment" hint="Sandbox companies are for testing your Intuit app.">
                <select className="ui-select" value={environment} onChange={(event) => setEnvironment(event.target.value)}>
                  <option value="production">Production</option>
                  <option value="sandbox">Sandbox</option>
                </select>
              </Field>
            ) : null}
            <div className="span-2">
              <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || !clientId.trim()}>
                {busy ? <span className="ui-spinner" /> : null} Save app
              </button>
            </div>
          </form>
        ) : null}
      </div>
    </Card>
  );
}

/** Copyable URL with a label, for webhook set-up. */
export function UrlRow({ label, value, hint }: { label: string; value: string | null | undefined; hint?: ReactNode }) {
  if (!value) return null;
  return (
    <div className="int-url-row">
      <div className="settings-row-label">{label}</div>
      {hint ? <div className="settings-row-hint" style={{ marginBottom: 6 }}>{hint}</div> : null}
      <CopyField value={value} label={`Copy ${label}`} />
    </div>
  );
}

export function LogList({ logs, empty = "Nothing has happened yet." }: { logs: IntegrationLog[]; empty?: string }) {
  if (!logs.length) return <p className="ui-muted ui-small" style={{ margin: 0, padding: "4px 0" }}>{empty}</p>;
  return (
    <ul className="int-logs">
      {logs.map((log) => (
        <li key={log.id} className={`int-log level-${log.level}`}>
          <span className="int-log-dot" aria-hidden />
          <div>
            <div className="int-log-message">{log.message || log.action.replace(/_/g, " ")}</div>
            <div className="ui-muted ui-small">
              {relativeTime(log.at)}
              {log.user ? ` · ${log.user}` : ""}
              {log.provider_name ? ` · ${log.provider_name}` : ""}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function UpgradeNote({ item }: { item: IntegrationDef }) {
  return (
    <Alert tone="info">
      {item.name} isn&apos;t included in your plan. <Link href="/settings/billing">See plans</Link>.
    </Alert>
  );
}
