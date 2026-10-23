"use client";

import { FlaskConical, Plus, RefreshCw, Rocket, Trash2 } from "lucide-react";
import { useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { switchOrganization } from "@/lib/auth";
import { dateTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Sandbox {
  id: string;
  name: string;
  slug: string;
  created_at: string | null;
  created_by: string | null;
}

interface Sandboxes {
  is_sandbox: boolean;
  production: { id: string; name: string } | null;
  items: Sandbox[];
  max?: number;
  deployable: { key: string; label: string }[];
}

export default function SandboxPage() {
  return (
    <SettingsShell permission="admin.settings" what="sandboxes" feature="sandbox">
      <SandboxSettings />
    </SettingsShell>
  );
}

function SandboxSettings() {
  const data = useApi<Sandboxes>("/sandboxes");
  const d = data.data;
  if (data.error) return <Alert>{data.error}</Alert>;
  if (!d) {
    return (
      <Card>
        <LoadingRows />
      </Card>
    );
  }
  return d.is_sandbox ? <InSandbox data={d} /> : <Production data={d} onChanged={() => void data.reload()} />;
}

async function openWorkspace(id: string) {
  await switchOrganization(id);
  window.location.assign("/");
}

function Production({ data, onChanged }: { data: Sandboxes; onChanged: () => void }) {
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [sample, setSample] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Sandbox | null>(null);

  async function create() {
    setBusy("create");
    try {
      const result = await api.post<Sandbox & { copied: Record<string, number> }>("/sandboxes", { name: name.trim() || null, sample_data: sample });
      const copied = Object.entries(result.copied)
        .filter(([, n]) => n)
        .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`)
        .join(", ");
      toast(`${result.name} is ready${copied ? ` with ${copied}` : ""}.`);
      setCreating(false);
      setName("");
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function refresh(sandbox: Sandbox) {
    setBusy(sandbox.id);
    try {
      await api.post(`/sandboxes/${sandbox.id}/refresh`, { sample_data: true });
      toast(`${sandbox.name} refreshed from production.`);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(deleting.id);
    try {
      await api.del(`/sandboxes/${deleting.id}`);
      toast(`${deleting.name} deleted.`);
      setDeleting(null);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const full = data.items.length >= (data.max ?? 2);

  return (
    <>
      <PageHeader
        title="Sandbox"
        description="A private copy of your workspace's setup — pipelines, fields, templates, workflows, buttons and approval rules — plus sample records. Try changes there, then deploy what works."
        actions={
          <button type="button" className="ui-btn ui-btn-primary" disabled={full} title={full ? `Up to ${data.max} sandboxes` : undefined} onClick={() => setCreating(true)}>
            <Plus size={16} /> New sandbox
          </button>
        }
      />
      <Card flush>
        {data.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Sandbox</th>
                  <th>Created</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((sandbox) => (
                  <tr key={sandbox.id}>
                    <td className="cell-title">
                      <span className="ui-row" style={{ gap: 8 }}>
                        <FlaskConical size={15} /> {sandbox.name}
                      </span>
                    </td>
                    <td className="ui-muted">
                      {sandbox.created_at ? dateTime(sandbox.created_at) : "—"}
                      {sandbox.created_by ? ` by ${sandbox.created_by}` : ""}
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button type="button" className="ui-btn ui-btn-sm" disabled={busy === sandbox.id} onClick={() => void refresh(sandbox)}>
                        <RefreshCw size={13} /> Refresh
                      </button>{" "}
                      <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => void openWorkspace(sandbox.id)}>
                        Open sandbox
                      </button>{" "}
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${sandbox.name}`} onClick={() => setDeleting(sandbox)}>
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<FlaskConical size={20} />} title="No sandboxes" message="Create one to test new fields, workflows or approval rules without touching live data." />
        )}
      </Card>
      <p className="ui-muted ui-small">Refreshing replaces everything in the sandbox with a fresh copy of production&apos;s setup. Emails sent from a sandbox are not delivered.</p>
      <Modal
        open={creating}
        title="New sandbox"
        onClose={() => setCreating(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setCreating(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy === "create"} onClick={() => void create()}>
              Create sandbox
            </button>
          </>
        }
      >
        <div className="ui-stack">
          <Field label="Name">
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Q4 process changes" />
          </Field>
          <label className="ui-check">
            <input type="checkbox" checked={sample} onChange={(e) => setSample(e.target.checked)} /> Copy a sample of records (up to 50 of each)
          </label>
        </div>
      </Modal>
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.name ?? "sandbox"}?`}
        message="The sandbox and everything in it is removed. Production is not affected."
        busy={Boolean(busy)}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function InSandbox({ data }: { data: Sandboxes }) {
  const toast = useToast();
  const { session } = useSession();
  const [selected, setSelected] = useState<string[]>(data.deployable.map((d) => d.key));
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  async function deploy() {
    setBusy(true);
    try {
      const response = await api.post<{ deployed: Record<string, unknown>; production: string }>("/sandboxes/deploy", { categories: selected });
      setResult(response.deployed);
      toast(`Deployed to ${response.production}.`);
      setConfirming(false);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Deploy to production"
        description={`You're in ${session?.organization.name ?? "a sandbox"}. Choose which setup to copy to ${data.production?.name ?? "production"}. Records are never copied.`}
        actions={
          data.production ? (
            <button type="button" className="ui-btn" onClick={() => void openWorkspace(data.production!.id)}>
              Back to production
            </button>
          ) : null
        }
      />
      <Card title="What to deploy" icon={<Rocket size={16} />}>
        <div className="ui-stack" style={{ gap: 10 }}>
          {data.deployable.map((item) => (
            <label key={item.key} className="ui-check">
              <input
                type="checkbox"
                checked={selected.includes(item.key)}
                onChange={(e) => setSelected(e.target.checked ? [...selected, item.key] : selected.filter((k) => k !== item.key))}
              />{" "}
              {item.label}
            </label>
          ))}
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Items are matched by name: ones that exist in production are updated, new ones are added, and nothing in production is deleted.
          </p>
          <button type="button" className="ui-btn ui-btn-primary" style={{ width: "fit-content" }} disabled={!selected.length} onClick={() => setConfirming(true)}>
            <Rocket size={15} /> Deploy {selected.length} item type{selected.length === 1 ? "" : "s"}
          </button>
        </div>
      </Card>
      {result ? (
        <Card title="Last deployment">
          <dl className="kv">
            {Object.entries(result).map(([key, value]) => (
              <div key={key} style={{ display: "contents" }}>
                <dt>{data.deployable.find((d) => d.key === key)?.label ?? key}</dt>
                <dd>{typeof value === "object" ? Object.entries(value as Record<string, number>).map(([k, n]) => `${n} ${k}`).join(", ") : String(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : null}
      <ConfirmDialog
        open={confirming}
        title={`Deploy to ${data.production?.name ?? "production"}?`}
        message="Your production workspace's setup is updated right away. Everyone there sees the changes on their next page load."
        confirmLabel="Deploy"
        danger={false}
        busy={busy}
        onConfirm={() => void deploy()}
        onClose={() => setConfirming(false)}
      />
    </>
  );
}
