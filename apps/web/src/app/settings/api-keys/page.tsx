"use client";

import { KeyRound, Plus } from "lucide-react";
import { useState } from "react";

import { CopyField } from "@/components/copy-field";
import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader, type Tone } from "@/components/ui";
import { date, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface ApiKeyRow {
  id: string;
  name: string;
  prefix: string;
  scope: "read" | "read_write";
  created_by: string | null;
  created_by_user_id: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  status: "active" | "expired" | "revoked";
}

interface ApiKeys {
  items: ApiKeyRow[];
  organization_id: string;
  base_url: string;
  docs_url: string;
}

const STATUS_TONE: Record<ApiKeyRow["status"], Tone> = { active: "success", expired: "warning", revoked: "neutral" };

export default function ApiKeysPage() {
  return (
    <SettingsShell feature="api_access">
      <ApiKeysSettings />
    </SettingsShell>
  );
}

function ApiKeysSettings() {
  const toast = useToast();
  const { can, session } = useSession();
  const keys = useApi<ApiKeys>("/api-keys");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "read_write">("read_write");
  const [expires, setExpires] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiKeyRow | null>(null);
  const [busy, setBusy] = useState(false);
  const d = keys.data;

  async function create() {
    setBusy(true);
    try {
      const result = await api.post<{ key: string }>("/api-keys", { name: name.trim(), scope, expires_in_days: expires ? Number(expires) : null });
      setCreated(result.key);
      setCreating(false);
      setName("");
      await keys.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!revoking) return;
    setBusy(true);
    try {
      await api.del(`/api-keys/${revoking.id}`);
      toast(`${revoking.name} revoked. Apps using it stop working right away.`);
      setRevoking(null);
      await keys.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const example = d ? `curl ${d.base_url}/leads?limit=5 \\\n  -H "Authorization: Bearer aiva_sk_…"` : "";

  return (
    <>
      <PageHeader
        title="API keys"
        description="Connect your own scripts and tools to AIVA's REST API. A key acts as the person who created it, with their role's permissions."
        actions={
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
            <Plus size={16} /> New API key
          </button>
        }
      />
      {keys.error ? <Alert>{keys.error}</Alert> : null}
      <div className="ui-split" style={{ alignItems: "start" }}>
        <Card flush title="Keys">
          {!d ? (
            <LoadingRows />
          ) : d.items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Access</th>
                    <th>Last used</th>
                    <th>Status</th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {d.items.map((key) => (
                    <tr key={key.id}>
                      <td className="cell-title">
                        {key.name}
                        <div className="ui-muted ui-small ui-mono">{key.prefix}…</div>
                        <div className="ui-muted ui-small">
                          {key.created_by ?? "—"} · {date(key.created_at)}
                          {key.expires_at ? ` · expires ${date(key.expires_at)}` : ""}
                        </div>
                      </td>
                      <td>{key.scope === "read" ? "Read only" : "Read and write"}</td>
                      <td className="ui-muted">{key.last_used_at ? relativeTime(key.last_used_at) : "Never"}</td>
                      <td>
                        <Badge tone={STATUS_TONE[key.status]}>{key.status}</Badge>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {key.status === "active" && (key.created_by_user_id === session?.user.id || can("admin.settings")) ? (
                          <button type="button" className="ui-btn ui-btn-sm" onClick={() => setRevoking(key)}>
                            Revoke
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState icon={<KeyRound size={20} />} title="No API keys" message="Create one to read and write CRM data from your own code." />
          )}
        </Card>
        {d ? (
          <Card title="Using the API">
            <div className="ui-stack">
              <Field label="Base URL">
                <CopyField value={d.base_url} />
              </Field>
              <Field label="Example">
                <pre className="code-block">{example}</pre>
              </Field>
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Every endpoint the app uses is available. Send the key as a Bearer token. Read-only keys can only make GET requests.{" "}
                <a href={`${d.base_url.replace(/\/api\/v1$/, "")}${d.docs_url}`} target="_blank" rel="noopener noreferrer">
                  API reference
                </a>
              </p>
            </div>
          </Card>
        ) : null}
      </div>
      <Modal
        open={creating}
        title="New API key"
        onClose={() => setCreating(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setCreating(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim()} onClick={() => void create()}>
              Create key
            </button>
          </>
        }
      >
        <div className="ui-stack">
          <Field label="Name" required>
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nightly warehouse sync" />
          </Field>
          <div className="ui-form-grid">
            <Field label="Access">
              <select className="ui-select" value={scope} onChange={(e) => setScope(e.target.value as "read" | "read_write")}>
                <option value="read_write">Read and write</option>
                <option value="read">Read only</option>
              </select>
            </Field>
            <Field label="Expires">
              <select className="ui-select" value={expires} onChange={(e) => setExpires(e.target.value)}>
                <option value="">Never</option>
                <option value="30">In 30 days</option>
                <option value="90">In 90 days</option>
                <option value="365">In a year</option>
              </select>
            </Field>
          </div>
        </div>
      </Modal>
      <Modal open={Boolean(created)} title="Copy your new key" onClose={() => setCreated(null)}>
        <div className="ui-stack">
          <Alert tone="warning">This is the only time the full key is shown. Store it somewhere safe.</Alert>
          {created ? <CopyField value={created} label="Copy key" /> : null}
        </div>
      </Modal>
      <ConfirmDialog
        open={Boolean(revoking)}
        title={`Revoke ${revoking?.name ?? "key"}?`}
        message="Anything using this key stops working immediately. This can't be undone."
        confirmLabel="Revoke key"
        busy={busy}
        onConfirm={() => void revoke()}
        onClose={() => setRevoking(null)}
      />
    </>
  );
}
