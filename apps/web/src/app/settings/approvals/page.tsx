"use client";

import Link from "next/link";
import { ClipboardCheck, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ConditionEditor, cleanConditions, type Condition } from "@/components/condition-editor";
import { useRefOptions } from "@/components/field-inputs";
import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import type { Catalog, FieldDef } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Rule {
  id: string;
  name: string;
  object_type: "opportunity" | "quote";
  trigger: "stage_change" | "quote_send";
  target_stage_id: string | null;
  target_stage_name: string | null;
  conditions: Condition[];
  approver_type: "user" | "role" | "manager";
  approver_value: string | null;
  approver_label: string | null;
  lock_record: boolean;
  is_active: boolean;
}

const QUOTE_FIELDS: FieldDef[] = [
  ["total", "Total", "currency"],
  ["subtotal", "Subtotal", "currency"],
  ["discount_total", "Discount amount", "currency"],
  ["discount_percent", "Discount %", "number"],
  ["tax_total", "Tax", "currency"],
  ["line_count", "Number of lines", "number"],
  ["currency", "Currency", "string"],
].map(([key, label, type]) => ({
  key,
  label,
  type: type as FieldDef["type"],
  options: [],
  ref: null,
  groupable: false,
  aggregatable: false,
  updatable: false,
  virtual: false,
}));

export default function ApprovalRulesPage() {
  return (
    <SettingsShell permission="admin.settings" what="approval rules" feature="approvals">
      <ApprovalRules />
    </SettingsShell>
  );
}

function ApprovalRules() {
  const toast = useToast();
  const rules = useApi<{ items: Rule[] }>("/approval-rules");
  const catalog = useApi<Catalog>("/meta/catalog");
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle(rule: Rule) {
    try {
      const { id: _id, target_stage_name: _s, approver_label: _a, ...body } = rule;
      await api.put(`/approval-rules/${rule.id}`, { ...body, is_active: !rule.is_active });
      await rules.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/approval-rules/${deleting.id}`);
      toast("Rule deleted.");
      setDeleting(null);
      await rules.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Approval rules"
        description="Require sign-off before a deal enters a protected stage or a quote goes out with a big discount. Requests land in the approver's inbox."
        actions={
          <>
            <Link href="/approvals" className="ui-btn">
              Approvals inbox
            </Link>
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New rule
            </button>
          </>
        }
      />
      {rules.error ? <Alert>{rules.error}</Alert> : null}
      <Card flush>
        {rules.loading && !rules.data ? (
          <LoadingRows />
        ) : rules.data?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>When</th>
                  <th>Approver</th>
                  <th>Active</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rules.data.items.map((rule) => (
                  <tr key={rule.id}>
                    <td className="cell-title">
                      {rule.name}
                      {rule.lock_record ? <div className="ui-muted ui-small">Locks the record while pending</div> : null}
                    </td>
                    <td>
                      {rule.object_type === "opportunity" ? `A deal moves to ${rule.target_stage_name ?? "a stage"}` : "A quote is sent"}
                      {rule.conditions.length ? <div className="ui-muted ui-small">and {rule.conditions.length} condition{rule.conditions.length === 1 ? "" : "s"} match</div> : null}
                    </td>
                    <td>{rule.approver_type === "role" ? <Badge>{rule.approver_label?.replace(/_/g, " ")}</Badge> : rule.approver_label ?? "—"}</td>
                    <td>
                      <label className="ui-switch">
                        <input type="checkbox" checked={rule.is_active} onChange={() => void toggle(rule)} aria-label={`${rule.name} active`} />
                        <span />
                      </label>
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${rule.name}`} onClick={() => setEditing(rule)}>
                        <Pencil size={14} />
                      </button>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${rule.name}`} onClick={() => setDeleting(rule)}>
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<ClipboardCheck size={20} />} title="No approval rules" message="For example: deals over $50,000 need a sales manager's approval to move to Negotiation." />
        )}
      </Card>
      {catalog.data ? (
        <RuleEditor
          rule={editing}
          catalog={catalog.data}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void rules.reload();
          }}
        />
      ) : null}
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.name ?? "rule"}?`}
        message="Pending requests from this rule stay in the inbox."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function RuleEditor({ rule, catalog, onClose, onSaved }: { rule: Rule | "new" | null; catalog: Catalog; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const open = rule !== null;
  const existing = rule && rule !== "new" ? rule : null;
  const stages = useRefOptions(open ? "stage" : null);
  const users = useRefOptions(open ? "user" : null);
  const roles = useApi<{ key: string; name: string }[]>(open ? "/admin/roles" : null);
  const [name, setName] = useState("");
  const [objectType, setObjectType] = useState<"opportunity" | "quote">("opportunity");
  const [stageId, setStageId] = useState("");
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [approverType, setApproverType] = useState<Rule["approver_type"]>("role");
  const [approverValue, setApproverValue] = useState("sales_manager");
  const [lock, setLock] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(existing?.name ?? "");
    setObjectType(existing?.object_type ?? "opportunity");
    setStageId(existing?.target_stage_id ?? "");
    setConditions(existing?.conditions ?? []);
    setApproverType(existing?.approver_type ?? "role");
    setApproverValue(existing?.approver_value ?? "sales_manager");
    setLock(existing?.lock_record ?? true);
    setError(null);
  }, [open, existing]);

  const fields = useMemo(
    () => (objectType === "quote" ? QUOTE_FIELDS : (catalog.objects.find((o) => o.key === "opportunity")?.fields ?? []).filter((f) => !f.virtual)),
    [objectType, catalog],
  );

  async function save() {
    setBusy(true);
    setError(null);
    const body = {
      name: name.trim(),
      object_type: objectType,
      trigger: objectType === "quote" ? "quote_send" : "stage_change",
      target_stage_id: objectType === "opportunity" ? stageId || null : null,
      conditions: cleanConditions(conditions),
      approver_type: approverType,
      approver_value: approverType === "manager" ? null : approverValue,
      lock_record: lock,
      is_active: existing?.is_active ?? true,
    };
    try {
      if (existing) await api.put(`/approval-rules/${existing.id}`, body);
      else await api.post("/approval-rules", body);
      toast(existing ? "Rule saved." : "Rule created.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={existing ? "Edit approval rule" : "New approval rule"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim()} onClick={() => void save()}>
            Save rule
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Rule name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Big deals need manager sign-off" />
        </Field>
        <div className="ui-form-grid">
          <Field label="Applies to">
            <select
              className="ui-select"
              value={objectType}
              disabled={Boolean(existing)}
              onChange={(e) => {
                setObjectType(e.target.value as "opportunity" | "quote");
                setConditions([]);
              }}
            >
              <option value="opportunity">Deals changing stage</option>
              <option value="quote">Quotes being sent</option>
            </select>
          </Field>
          {objectType === "opportunity" ? (
            <Field label="Moving into" required>
              <select className="ui-select" value={stageId} onChange={(e) => setStageId(e.target.value)}>
                <option value="">Choose a stage…</option>
                {stages.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
        </div>
        <Field label="Only when">
          <ConditionEditor
            fields={fields}
            operators={catalog.operators}
            conditions={conditions}
            onChange={setConditions}
            emptyLabel={objectType === "quote" ? "Every quote needs approval before it's sent." : "Every deal moving into this stage needs approval."}
          />
        </Field>
        <div className="ui-form-grid">
          <Field label="Approved by">
            <select className="ui-select" value={approverType} onChange={(e) => setApproverType(e.target.value as Rule["approver_type"])}>
              <option value="role">Anyone with a role</option>
              <option value="user">A specific person</option>
              <option value="manager">The requester&apos;s manager</option>
            </select>
          </Field>
          {approverType === "role" ? (
            <Field label="Role">
              <select className="ui-select" value={approverValue} onChange={(e) => setApproverValue(e.target.value)}>
                {(roles.data ?? []).map((role) => (
                  <option key={role.key} value={role.key}>
                    {role.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : approverType === "user" ? (
            <Field label="Person">
              <select className="ui-select" value={approverValue} onChange={(e) => setApproverValue(e.target.value)}>
                <option value="">Choose…</option>
                {users.map((u) => (
                  <option key={u.value} value={u.value}>
                    {u.label}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <p className="ui-muted ui-small" style={{ alignSelf: "end" }}>
              Uses the manager set in Settings → Hierarchy. Admins can approve when nobody is set.
            </p>
          )}
        </div>
        <label className="ui-check">
          <input type="checkbox" checked={lock} onChange={(e) => setLock(e.target.checked)} /> Lock the record for edits while the request is pending
        </label>
      </div>
    </Modal>
  );
}
