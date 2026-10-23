"use client";

import { ListChecks, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, Field, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface StageRule {
  required_fields: string[];
  checklist: string[];
  exit_requires_checklist: boolean;
  guidance: string | null;
}

interface Stage {
  id: string;
  name: string;
  category: "open" | "won" | "lost";
  is_active: boolean;
  rule: StageRule | null;
}

interface StageRules {
  fields: { key: string; label: string }[];
  pipelines: { id: string; name: string; stages: Stage[] }[];
}

export default function ProcessesPage() {
  return (
    <SettingsShell permission="admin.settings" what="sales processes" feature="process_management">
      <Processes />
    </SettingsShell>
  );
}

function Processes() {
  const toast = useToast();
  const data = useApi<StageRules>("/process/stage-rules");
  const [pipelineId, setPipelineId] = useState<string>("");
  const [editing, setEditing] = useState<Stage | null>(null);
  const pipelines = data.data?.pipelines ?? [];
  const pipeline = pipelines.find((p) => p.id === pipelineId) ?? pipelines[0];
  const labels = Object.fromEntries((data.data?.fields ?? []).map((f) => [f.key, f.label]));

  async function clear(stage: Stage) {
    try {
      await api.del(`/process/stage-rules/${stage.id}`);
      toast(`Playbook for ${stage.name} removed.`);
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Sales processes"
        description="Give each deal stage a playbook: fields reps must fill before a deal can enter it, a checklist to work through, and guidance on what good looks like."
      />
      {data.error ? <Alert>{data.error}</Alert> : null}
      {pipelines.length > 1 ? (
        <div style={{ marginBottom: 14 }}>
          <Tabs label="Pipeline" value={pipeline?.id ?? ""} onChange={setPipelineId} items={pipelines.map((p) => ({ value: p.id, label: p.name }))} />
        </div>
      ) : null}
      {!data.data ? (
        <Card>
          <LoadingRows />
        </Card>
      ) : pipeline ? (
        <ol className="process-flow">
          {pipeline.stages
            .filter((s) => s.is_active)
            .map((stage, index) => (
              <li key={stage.id} className="ui-card process-stage">
                <div className="ui-row" style={{ gap: 10 }}>
                  <span className="process-index">{index + 1}</span>
                  <strong style={{ flex: 1 }}>{stage.name}</strong>
                  {stage.category !== "open" ? <Badge tone={stage.category === "won" ? "success" : "danger"}>{stage.category}</Badge> : null}
                  <button type="button" className="ui-btn ui-btn-sm" onClick={() => setEditing(stage)}>
                    {stage.rule ? <Pencil size={13} /> : <Plus size={13} />} {stage.rule ? "Edit" : "Add playbook"}
                  </button>
                  {stage.rule ? (
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Remove playbook for ${stage.name}`} onClick={() => void clear(stage)}>
                      <Trash2 size={14} />
                    </button>
                  ) : null}
                </div>
                {stage.rule ? (
                  <div className="process-rule">
                    {stage.rule.required_fields.length ? (
                      <div>
                        <span className="ui-muted ui-small">Required to enter</span>
                        <div className="chip-list">
                          {stage.rule.required_fields.map((key) => (
                            <Badge key={key} tone="warning">
                              {labels[key] ?? key}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {stage.rule.checklist.length ? (
                      <div>
                        <span className="ui-muted ui-small">
                          Checklist{stage.rule.exit_requires_checklist ? " · must be finished to move on" : ""}
                        </span>
                        <ul className="process-checklist">
                          {stage.rule.checklist.map((item) => (
                            <li key={item}>
                              <ListChecks size={13} /> {item}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {stage.rule.guidance ? <p className="ui-small" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{stage.rule.guidance}</p> : null}
                  </div>
                ) : (
                  <p className="ui-muted ui-small" style={{ margin: 0 }}>
                    No playbook. Deals can move in and out freely.
                  </p>
                )}
              </li>
            ))}
        </ol>
      ) : (
        <Card>
          <p className="empty-hint">Create a pipeline first.</p>
        </Card>
      )}
      <RuleEditor
        stage={editing}
        fields={data.data?.fields ?? []}
        onClose={() => setEditing(null)}
        onSaved={(message) => {
          toast(message);
          setEditing(null);
          void data.reload();
        }}
      />
    </>
  );
}

function RuleEditor({ stage, fields, onClose, onSaved }: { stage: Stage | null; fields: { key: string; label: string }[]; onClose: () => void; onSaved: (message: string) => void }) {
  const [required, setRequired] = useState<string[]>([]);
  const [checklist, setChecklist] = useState<string[]>([]);
  const [item, setItem] = useState("");
  const [exitRequires, setExitRequires] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!stage) return;
    setRequired(stage.rule?.required_fields ?? []);
    setChecklist(stage.rule?.checklist ?? []);
    setExitRequires(stage.rule?.exit_requires_checklist ?? false);
    setGuidance(stage.rule?.guidance ?? "");
    setItem("");
    setError(null);
  }, [stage]);

  function addItem() {
    const text = item.trim();
    if (!text || checklist.includes(text)) return;
    setChecklist([...checklist, text]);
    setItem("");
  }

  async function save() {
    if (!stage) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.put<{ message: string }>(`/process/stage-rules/${stage.id}`, {
        required_fields: required,
        checklist,
        exit_requires_checklist: exitRequires,
        guidance: guidance.trim() || null,
      });
      onSaved(result.message);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(stage)}
      title={stage ? `Playbook · ${stage.name}` : "Playbook"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
            Save playbook
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Fields required before a deal can enter this stage">
          <div className="chip-list">
            {fields.map((f) => {
              const on = required.includes(f.key);
              return (
                <button key={f.key} type="button" className={`chip${on ? " on" : ""}`} aria-pressed={on} onClick={() => setRequired(on ? required.filter((k) => k !== f.key) : [...required, f.key])}>
                  {f.label}
                </button>
              );
            })}
          </div>
        </Field>
        <Field label="Checklist">
          <div className="ui-stack" style={{ gap: 8 }}>
            {checklist.map((entry, index) => (
              <div key={entry} className="ui-row" style={{ gap: 8 }}>
                <ListChecks size={14} />
                <span style={{ flex: 1 }}>{entry}</span>
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Remove ${entry}`} onClick={() => setChecklist(checklist.filter((_, i) => i !== index))}>
                  <X size={13} />
                </button>
              </div>
            ))}
            <div className="ui-row" style={{ gap: 8 }}>
              <input
                className="ui-input"
                value={item}
                onChange={(e) => setItem(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addItem();
                  }
                }}
                placeholder="Confirm budget with the economic buyer"
                aria-label="New checklist item"
              />
              <button type="button" className="ui-btn" onClick={addItem} disabled={!item.trim()}>
                Add
              </button>
            </div>
          </div>
        </Field>
        <label className="ui-check">
          <input type="checkbox" checked={exitRequires} disabled={!checklist.length} onChange={(e) => setExitRequires(e.target.checked)} /> Deals can&apos;t leave this stage until the checklist is done
        </label>
        <Field label="Guidance for reps">
          <textarea className="ui-textarea" rows={4} value={guidance} onChange={(e) => setGuidance(e.target.value)} placeholder="What a good discovery call covers, who to involve, links to templates…" />
        </Field>
      </div>
    </Modal>
  );
}
