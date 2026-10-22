"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Bell, CheckSquare, PenLine, Plus, Trash2, UserRoundCog, X, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ValueInput, operatorLabel, operatorsFor, useRefOptions } from "@/components/field-inputs";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, PageHeader } from "@/components/ui";
import { relativeTime, titleCase } from "@/lib/format";
import type { Catalog, Workflow, WorkflowAction, WorkflowCondition, WorkflowRun } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const TRIGGER_LABEL: Record<string, string> = {
  created: "is created",
  updated: "is updated",
  created_or_updated: "is created or updated",
  field_changed: "has a specific field change",
};

const ACTION_META: Record<WorkflowAction["type"], { label: string; icon: typeof Zap }> = {
  create_task: { label: "Create a task", icon: CheckSquare },
  update_field: { label: "Update a field", icon: PenLine },
  notify: { label: "Send a notification", icon: Bell },
  assign_owner: { label: "Change the owner", icon: UserRoundCog },
};

function defaultParams(type: WorkflowAction["type"]): Record<string, unknown> {
  switch (type) {
    case "create_task":
      return { title: "Follow up on {{record}}", priority: "medium", assign_to: "owner", due_in_days: 1 };
    case "notify":
      return { recipient: "owner", title: "{{record}} needs attention", message: "" };
    case "update_field":
      return { field: "", value: "" };
    case "assign_owner":
      return { user_id: "" };
  }
}

function PersonSelect({
  value,
  onChange,
  allowOwner = true,
}: {
  value: string;
  onChange: (value: string) => void;
  allowOwner?: boolean;
}) {
  const members = useRefOptions("user");
  return (
    <select className="ui-select sm" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowOwner ? <option value="owner">The record&apos;s owner</option> : <option value="">Choose a person…</option>}
      <option value="actor">Whoever made the change</option>
      {members.map((member) => (
        <option key={member.value} value={member.value}>
          {member.label}
        </option>
      ))}
    </select>
  );
}

export function WorkflowBuilder({ workflowId }: { workflowId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const catalog = useApi<Catalog>("/meta/catalog");
  const workflows = useApi<Workflow[]>(workflowId ? "/workflows" : null);
  const runs = useApi<WorkflowRun[]>(workflowId ? `/workflows/${workflowId}/runs` : null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [objectType, setObjectType] = useState("opportunity");
  const [trigger, setTrigger] = useState("created");
  const [triggerField, setTriggerField] = useState("");
  const [conditions, setConditions] = useState<WorkflowCondition[]>([]);
  const [actions, setActions] = useState<WorkflowAction[]>([{ type: "create_task", params: defaultParams("create_task") }]);
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const editable = can("automation.workflows");

  const existing = workflows.data?.find((item) => item.id === workflowId);
  useEffect(() => {
    if (!existing) return;
    setName(existing.name);
    setDescription(existing.description ?? "");
    setObjectType(existing.object_type);
    setTrigger(existing.trigger_event);
    setTriggerField(existing.trigger_field ?? "");
    setConditions(existing.conditions.map((c) => ({ ...c, value: c.value ?? "" })));
    setActions(existing.actions);
    setActive(existing.is_active);
  }, [existing]);

  const object = catalog.data?.objects.find((item) => item.key === objectType);
  const fields = useMemo(() => object?.fields ?? [], [object]);
  const fieldMap = useMemo(() => new Map(fields.map((field) => [field.key, field])), [fields]);
  const updatable = fields.filter((field) => field.updatable);
  const hasOwner = fields.some((field) => field.key === "owner_user_id" || field.key === "assigned_to_user_id");

  function setAction(index: number, next: WorkflowAction) {
    setActions((current) => current.map((action, i) => (i === index ? next : action)));
  }

  async function save() {
    setBusy(true);
    setError(null);
    const payload = {
      name: name.trim(),
      description: description.trim() || null,
      object_type: objectType,
      trigger_event: trigger,
      trigger_field: trigger === "field_changed" ? triggerField || null : null,
      conditions: conditions.map((c) => ({ ...c, value: ["is_empty", "is_not_empty"].includes(c.operator) ? null : c.value })),
      actions,
      is_active: active,
    };
    try {
      if (workflowId) {
        await api.put(`/workflows/${workflowId}`, payload);
        toast("Workflow saved.");
        await workflows.reload();
      } else {
        const created = await api.post<Workflow>("/workflows", payload);
        toast(active ? "Workflow is live." : "Workflow saved as inactive.");
        router.replace(`/workflows/${created.id}`);
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/workflows/${workflowId}`);
      toast("Workflow deleted.");
      router.replace("/workflows");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  if (!catalog.data || (workflowId && !workflows.data)) return <div className="ui-card"><LoadingRows rows={10} /></div>;
  if (workflowId && !existing) return <Alert>Workflow not found.</Alert>;

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/workflows" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> Workflows
        </Link>
      </div>
      <PageHeader
        eyebrow="Automation"
        title={workflowId ? name || "Workflow" : "New workflow"}
        description="When something happens to a record and the conditions match, AIVA runs the actions — instantly, in the same save."
        actions={
          editable ? (
            <>
              {workflowId ? (
                <button type="button" className="ui-btn ui-btn-danger ui-btn-icon" aria-label="Delete workflow" onClick={() => setDeleting(true)}>
                  <Trash2 size={15} />
                </button>
              ) : null}
              <label className="ui-check" style={{ padding: "0 8px" }}>
                <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
                Active
              </label>
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy || !name.trim() || !actions.length}>
                {busy ? <span className="ui-spinner" /> : null}
                Save workflow
              </button>
            </>
          ) : null
        }
      />

      {error ? (
        <div style={{ marginBottom: 14 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}

      <div className="ui-split">
        <div className="ui-stack">
          <Card title="1. When" icon={<Zap size={16} />}>
            <div className="ui-form-grid">
              <Field label="Name" required className="span-2">
                <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Escalate urgent cases" disabled={!editable} />
              </Field>
              <Field label="A record of type">
                <select
                  className="ui-select"
                  value={objectType}
                  disabled={!editable}
                  onChange={(e) => {
                    setObjectType(e.target.value);
                    setConditions([]);
                    setTriggerField("");
                    setActions([{ type: "create_task", params: defaultParams("create_task") }]);
                  }}
                >
                  {catalog.data.objects
                    .filter((item) => item.key !== "product")
                    .map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.singular}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Runs when it">
                <select className="ui-select" value={trigger} onChange={(e) => setTrigger(e.target.value)} disabled={!editable}>
                  {catalog.data.triggers.map((item) => (
                    <option key={item} value={item}>
                      {TRIGGER_LABEL[item] ?? item}
                    </option>
                  ))}
                </select>
              </Field>
              {trigger === "field_changed" ? (
                <Field label="Field that changes" className="span-2">
                  <select className="ui-select" value={triggerField} onChange={(e) => setTriggerField(e.target.value)} disabled={!editable}>
                    <option value="">Choose a field…</option>
                    {fields
                      .filter((field) => !["created_at", "updated_at"].includes(field.key))
                      .map((field) => (
                        <option key={field.key} value={field.key}>
                          {field.label}
                        </option>
                      ))}
                  </select>
                </Field>
              ) : null}
              <Field label="Description" className="span-2">
                <input className="ui-input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Why this workflow exists" disabled={!editable} />
              </Field>
            </div>
          </Card>

          <Card title="2. Only if (all must match)">
            <div className="ui-stack" style={{ gap: 10 }}>
              {conditions.length ? null : <span className="ui-muted ui-small">No conditions — runs for every matching {object?.singular.toLowerCase()}.</span>}
              {conditions.map((condition, index) => {
                const field = fieldMap.get(condition.field);
                const ops = operatorsFor(field, catalog.data!.operators, false);
                return (
                  <div key={index} className="ui-row" style={{ flexWrap: "wrap", gap: 8 }}>
                    <select
                      className="ui-select sm"
                      style={{ flex: "1 1 160px" }}
                      value={condition.field}
                      disabled={!editable}
                      onChange={(e) => {
                        const nextField = fieldMap.get(e.target.value);
                        setConditions(conditions.map((c, i) => (i === index ? { field: e.target.value, operator: operatorsFor(nextField, catalog.data!.operators, false)[0], value: "" } : c)));
                      }}
                      aria-label="Condition field"
                    >
                      {fields.map((item) => (
                        <option key={item.key} value={item.key}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                    <select
                      className="ui-select sm"
                      style={{ flex: "1 1 140px" }}
                      value={condition.operator}
                      disabled={!editable}
                      onChange={(e) => setConditions(conditions.map((c, i) => (i === index ? { ...c, operator: e.target.value, value: "" } : c)))}
                      aria-label="Condition operator"
                    >
                      {ops.map((op) => (
                        <option key={op} value={op}>
                          {operatorLabel(op, field)}
                        </option>
                      ))}
                    </select>
                    <div style={{ flex: "1 1 160px", minWidth: 0 }}>
                      <ValueInput
                        field={field}
                        operator={condition.operator}
                        value={condition.value ?? ""}
                        allowMe={false}
                        onChange={(value) => setConditions(conditions.map((c, i) => (i === index ? { ...c, value } : c)))}
                      />
                    </div>
                    {editable ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove condition" onClick={() => setConditions(conditions.filter((_, i) => i !== index))}>
                        <X size={14} />
                      </button>
                    ) : null}
                  </div>
                );
              })}
              {editable ? (
                <button
                  type="button"
                  className="ui-btn ui-btn-sm"
                  style={{ justifySelf: "start", width: "fit-content" }}
                  onClick={() => {
                    const field = fields[0];
                    setConditions([...conditions, { field: field.key, operator: operatorsFor(field, catalog.data!.operators, false)[0], value: "" }]);
                  }}
                >
                  <Plus size={14} /> Add condition
                </button>
              ) : null}
            </div>
          </Card>

          <Card title="3. Then do this">
            <div className="ui-stack">
              {actions.map((action, index) => {
                const Icon = ACTION_META[action.type].icon;
                const params = action.params as Record<string, string | number>;
                const set = (key: string, value: unknown) => setAction(index, { ...action, params: { ...action.params, [key]: value } });
                const updateField = fieldMap.get(String(params.field ?? ""));
                return (
                  <div key={index} style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 12, display: "grid", gap: 12 }}>
                    <div className="ui-row">
                      <span className="ui-avatar" style={{ borderRadius: 8 }}>
                        <Icon size={14} />
                      </span>
                      <select
                        className="ui-select sm"
                        style={{ maxWidth: 240 }}
                        value={action.type}
                        disabled={!editable}
                        onChange={(e) => {
                          const type = e.target.value as WorkflowAction["type"];
                          setAction(index, { type, params: defaultParams(type) });
                        }}
                        aria-label="Action"
                      >
                        {catalog.data!.actions
                          .filter((type) => type !== "assign_owner" || hasOwner)
                          .map((type) => (
                            <option key={type} value={type}>
                              {ACTION_META[type as WorkflowAction["type"]]?.label ?? type}
                            </option>
                          ))}
                      </select>
                      <span className="ui-spacer" />
                      {editable && actions.length > 1 ? (
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove action" onClick={() => setActions(actions.filter((_, i) => i !== index))}>
                          <X size={14} />
                        </button>
                      ) : null}
                    </div>

                    {action.type === "create_task" ? (
                      <div className="ui-form-grid">
                        <Field label="Task title" className="span-2" hint="Use {{record}} for the record's name, or {{field_name}} for any field.">
                          <input className="ui-input sm" value={String(params.title ?? "")} onChange={(e) => set("title", e.target.value)} disabled={!editable} />
                        </Field>
                        <Field label="Assign to">
                          <PersonSelect value={String(params.assign_to ?? "owner")} onChange={(value) => set("assign_to", value)} />
                        </Field>
                        <Field label="Priority">
                          <select className="ui-select sm" value={String(params.priority ?? "medium")} onChange={(e) => set("priority", e.target.value)} disabled={!editable}>
                            {["low", "medium", "high", "urgent"].map((item) => (
                              <option key={item} value={item}>
                                {titleCase(item)}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="Due in (days)">
                          <input
                            className="ui-input sm"
                            type="number"
                            min={0}
                            max={365}
                            value={Number(params.due_in_days ?? 1)}
                            onChange={(e) => set("due_in_days", Number(e.target.value))}
                            disabled={!editable}
                          />
                        </Field>
                      </div>
                    ) : null}

                    {action.type === "notify" ? (
                      <div className="ui-form-grid">
                        <Field label="Notify">
                          <PersonSelect value={String(params.recipient ?? "owner")} onChange={(value) => set("recipient", value)} />
                        </Field>
                        <Field label="Title">
                          <input className="ui-input sm" value={String(params.title ?? "")} onChange={(e) => set("title", e.target.value)} disabled={!editable} />
                        </Field>
                        <Field label="Message" className="span-2" hint="Shows in the bell menu with a link to the record.">
                          <input className="ui-input sm" value={String(params.message ?? "")} onChange={(e) => set("message", e.target.value)} disabled={!editable} />
                        </Field>
                      </div>
                    ) : null}

                    {action.type === "update_field" ? (
                      <div className="ui-form-grid">
                        <Field label="Set field">
                          <select
                            className="ui-select sm"
                            value={String(params.field ?? "")}
                            onChange={(e) => setAction(index, { ...action, params: { field: e.target.value, value: "" } })}
                            disabled={!editable}
                          >
                            <option value="">Choose a field…</option>
                            {updatable.map((field) => (
                              <option key={field.key} value={field.key}>
                                {field.label}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="To">
                          {updateField ? (
                            <ValueInput field={updateField} operator="equals" value={String(params.value ?? "")} allowMe={false} onChange={(value) => set("value", value)} />
                          ) : (
                            <input className="ui-input sm" disabled placeholder="Choose a field first" />
                          )}
                        </Field>
                      </div>
                    ) : null}

                    {action.type === "assign_owner" ? (
                      <Field label="New owner">
                        <PersonSelect value={String(params.user_id ?? "")} onChange={(value) => set("user_id", value)} allowOwner={false} />
                      </Field>
                    ) : null}
                  </div>
                );
              })}
              {editable ? (
                <button
                  type="button"
                  className="ui-btn ui-btn-sm"
                  style={{ width: "fit-content" }}
                  onClick={() => setActions([...actions, { type: "notify", params: defaultParams("notify") }])}
                >
                  <Plus size={14} /> Add action
                </button>
              ) : null}
            </div>
          </Card>
        </div>

        <div className="ui-stack">
          <Card title="Summary">
            <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.6, color: "var(--text-2)" }}>
              When {/^[aeiou]/i.test(object?.singular ?? "") ? "an" : "a"} <strong>{object?.singular.toLowerCase()}</strong> {TRIGGER_LABEL[trigger]}
              {trigger === "field_changed" && triggerField ? ` (${fieldMap.get(triggerField)?.label ?? triggerField})` : ""}
              {conditions.length
                ? `, and ${conditions
                    .map((c) => `${fieldMap.get(c.field)?.label ?? c.field} ${operatorLabel(c.operator, fieldMap.get(c.field))}${["is_empty", "is_not_empty"].includes(c.operator) ? "" : ` ${c.value || "…"}`}`)
                    .join(" and ")}`
                : ""}
              , AIVA will {actions.map((a) => ACTION_META[a.type].label.toLowerCase()).join(", then ")}.
            </p>
          </Card>
          {workflowId ? (
            <Card title="Recent runs" flush>
              {runs.data?.length ? (
                <div className="ui-list" style={{ maxHeight: 420, overflowY: "auto" }}>
                  {runs.data.map((run) => (
                    <div key={run.id} className="ui-list-item" style={{ alignItems: "flex-start" }}>
                      <Badge tone={run.status === "success" ? "success" : run.status === "error" ? "danger" : "neutral"}>{run.status}</Badge>
                      <div className="main">
                        <div className="title">
                          {run.link ? (
                            <Link href={run.link} style={{ color: "inherit" }}>
                              {run.entity_label ?? "Record"}
                            </Link>
                          ) : (
                            run.entity_label ?? "Record"
                          )}
                        </div>
                        <div className="sub" style={{ whiteSpace: "normal" }}>
                          {run.message ?? ""} · {relativeTime(run.created_at)}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="ui-muted ui-small" style={{ padding: 18 }}>
                  Hasn&apos;t run yet. It runs the next time a matching record is saved.
                </div>
              )}
            </Card>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={deleting}
        title="Delete this workflow?"
        message="It stops immediately. Tasks and notifications it already created stay."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}
