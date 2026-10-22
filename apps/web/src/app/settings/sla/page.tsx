"use client";

import { Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader, PRIORITY_TONE } from "@/components/ui";
import { duration } from "@/lib/format";
import type { CasePriority, SlaPolicy } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const PRIORITIES: CasePriority[] = ["urgent", "high", "medium", "low"];

export default function SlaPage() {
  return (
    <SettingsShell permission="service.cases" what="SLA policies">
      <SlaPolicies />
    </SettingsShell>
  );
}

function SlaPolicies() {
  const { can } = useSession();
  const toast = useToast();
  const editable = can("service.sla_manage");
  const policies = useApi<SlaPolicy[]>("/sla-policies");
  const [editing, setEditing] = useState<SlaPolicy | "new" | null>(null);
  const [deleting, setDeleting] = useState<SlaPolicy | null>(null);
  const [busy, setBusy] = useState(false);

  const sorted = [...(policies.data ?? [])].sort((a, b) => PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority));

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/sla-policies/${deleting.id}`);
      toast("Policy deleted.");
      setDeleting(null);
      await policies.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="SLA policies"
        description="Response and resolution targets by priority. New cases get due times from the matching active policy; the clock shows on every case."
        actions={
          editable ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New policy
            </button>
          ) : null
        }
      />
      {policies.error ? <Alert>{policies.error}</Alert> : null}
      <Card flush>
        {policies.loading && !policies.data ? (
          <LoadingRows />
        ) : sorted.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Policy</th>
                  <th>Priority</th>
                  <th>First response within</th>
                  <th>Resolve within</th>
                  <th>Status</th>
                  {editable ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {sorted.map((policy) => (
                  <tr key={policy.id}>
                    <td className="cell-title">{policy.name}</td>
                    <td>
                      <Badge tone={PRIORITY_TONE[policy.priority]}>{policy.priority}</Badge>
                    </td>
                    <td>{duration(policy.first_response_minutes)}</td>
                    <td>{duration(policy.resolution_minutes)}</td>
                    <td>{policy.is_active ? <Badge tone="success">Active</Badge> : <Badge>Inactive</Badge>}</td>
                    {editable ? (
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Edit policy" onClick={() => setEditing(policy)}>
                          <Pencil size={14} />
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Delete policy" onClick={() => setDeleting(policy)}>
                          <Trash2 size={14} />
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No SLA policies" message="Without a policy, cases have no response or resolution targets." />
        )}
      </Card>

      <PolicyModal
        policy={editing}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await policies.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete SLA policy?"
        message="Existing cases keep their due times. New cases of this priority won't get targets."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function splitMinutes(total: number) {
  return { hours: Math.floor(total / 60), minutes: total % 60 };
}

function PolicyModal({ policy, onClose, onSaved }: { policy: SlaPolicy | "new" | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [priority, setPriority] = useState<CasePriority>("medium");
  const [responseH, setResponseH] = useState(4);
  const [responseM, setResponseM] = useState(0);
  const [resolveH, setResolveH] = useState(24);
  const [resolveM, setResolveM] = useState(0);
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!policy) return;
    const p = policy === "new" ? null : policy;
    setName(p?.name ?? "");
    setPriority(p?.priority ?? "medium");
    const response = splitMinutes(p?.first_response_minutes ?? 240);
    const resolve = splitMinutes(p?.resolution_minutes ?? 1440);
    setResponseH(response.hours);
    setResponseM(response.minutes);
    setResolveH(resolve.hours);
    setResolveM(resolve.minutes);
    setActive(p?.is_active ?? true);
    setError(null);
  }, [policy]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = {
      name: name.trim(),
      priority,
      first_response_minutes: responseH * 60 + responseM,
      resolution_minutes: resolveH * 60 + resolveM,
      is_active: active,
    };
    if (payload.resolution_minutes < payload.first_response_minutes) {
      setError("Resolution time should be at least as long as the first-response time.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (policy === "new") await api.post("/sla-policies", payload);
      else if (policy) await api.patch(`/sla-policies/${policy.id}`, payload);
      toast("SLA policy saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(policy)}
      title={policy === "new" ? "New SLA policy" : "Edit SLA policy"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="sla-form" className="ui-btn ui-btn-primary" disabled={busy}>
            Save policy
          </button>
        </>
      }
    >
      <form id="sla-form" className="ui-stack" onSubmit={submit}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Policy name" required>
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <Field label="Applies to priority">
            <select className="ui-select" value={priority} onChange={(e) => setPriority(e.target.value as CasePriority)}>
              {PRIORITIES.map((item) => (
                <option key={item} value={item}>
                  {item[0].toUpperCase() + item.slice(1)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="First response within">
            <div className="ui-row">
              <input className="ui-input" type="number" min={0} value={responseH} onChange={(e) => setResponseH(Number(e.target.value))} aria-label="Hours" />
              <span className="ui-muted ui-small">h</span>
              <input className="ui-input" type="number" min={0} max={59} value={responseM} onChange={(e) => setResponseM(Number(e.target.value))} aria-label="Minutes" />
              <span className="ui-muted ui-small">m</span>
            </div>
          </Field>
          <Field label="Resolve within">
            <div className="ui-row">
              <input className="ui-input" type="number" min={0} value={resolveH} onChange={(e) => setResolveH(Number(e.target.value))} aria-label="Hours" />
              <span className="ui-muted ui-small">h</span>
              <input className="ui-input" type="number" min={0} max={59} value={resolveM} onChange={(e) => setResolveM(Number(e.target.value))} aria-label="Minutes" />
              <span className="ui-muted ui-small">m</span>
            </div>
          </Field>
        </div>
        <label className="ui-check">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Active
        </label>
      </form>
    </Modal>
  );
}
