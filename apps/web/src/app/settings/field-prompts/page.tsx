"use client";

import { MessageSquareQuote, Pencil, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { TagsInput, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

type ObjectType = "opportunity" | "account" | "contact";

type Prompt = {
  id: string;
  name: string;
  object_type: ObjectType;
  field_key: string;
  field_label: string;
  prompt: string;
  keywords: string[];
  apply_mode: "suggest" | "auto";
  is_active: boolean;
  position: number;
};

type Target = { key: string; label: string; type: string };

type PromptList = { items: Prompt[]; targets: Record<ObjectType, Target[]>; ai: boolean; can_edit: boolean };

type Draft = Omit<Prompt, "id" | "field_label" | "position">;

const OBJECTS: { value: ObjectType; label: string }[] = [
  { value: "opportunity", label: "Deal" },
  { value: "account", label: "Account" },
  { value: "contact", label: "Contact" },
];

const objectLabel = (value: ObjectType) => OBJECTS.find((o) => o.value === value)?.label ?? value;

const EXAMPLES: Draft[] = [
  { name: "Budget", object_type: "opportunity", field_key: "amount", prompt: "What budget or deal size did the customer mention?", keywords: ["budget", "spend", "price"], apply_mode: "suggest", is_active: true },
  { name: "Competitor", object_type: "opportunity", field_key: "competitor", prompt: "Which other vendors is the customer comparing us with?", keywords: ["comparing", "looking at", "also talking to"], apply_mode: "suggest", is_active: true },
  { name: "Next step", object_type: "opportunity", field_key: "next_step", prompt: "What next step did both sides agree to?", keywords: ["next step", "follow up", "send over"], apply_mode: "suggest", is_active: true },
];

export default function FieldPromptsPage() {
  return (
    <SettingsShell feature="conversation_intelligence">
      <FieldPrompts />
    </SettingsShell>
  );
}

function FieldPrompts() {
  const toast = useToast();
  const prompts = useApi<PromptList>("/field-prompts");
  const [editing, setEditing] = useState<Prompt | Draft | null>(null);
  const [deleting, setDeleting] = useState<Prompt | null>(null);
  const [busy, setBusy] = useState(false);
  const items = prompts.data?.items ?? [];
  const editable = Boolean(prompts.data?.can_edit);
  const blank: Draft = { name: "", object_type: "opportunity", field_key: "amount", prompt: "", keywords: [], apply_mode: "suggest", is_active: true };

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/field-prompts/${deleting.id}`);
      toast("Prompt deleted.");
      setDeleting(null);
      await prompts.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Custom field prompts"
        description="Questions AIVA answers from every call to keep CRM fields current — budget, competitor, next step. Answers show on the conversation for someone to apply, or go straight in."
        actions={
          editable ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing(blank)}>
              <Plus size={16} /> New prompt
            </button>
          ) : null
        }
      />
      {prompts.error ? <Alert>{prompts.error}</Alert> : null}
      {prompts.data && !prompts.data.ai ? (
        <Alert tone="info">
          AI isn&apos;t connected, so AIVA answers prompts by finding the lines that mention your keywords and pulling out the value (amounts, numbers, dates or
          the sentence). Add good keywords to each prompt.
        </Alert>
      ) : null}
      <div className="ui-stack" style={{ marginTop: prompts.data && !prompts.data.ai ? 16 : 0 }}>
        <Card flush>
          {prompts.loading && !prompts.data ? (
            <LoadingRows />
          ) : items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Prompt</th>
                    <th>Fills in</th>
                    <th>Keywords</th>
                    <th>When answered</th>
                    <th>Status</th>
                    {editable ? <th aria-label="Actions" /> : null}
                  </tr>
                </thead>
                <tbody>
                  {items.map((prompt) => (
                    <tr key={prompt.id}>
                      <td style={{ maxWidth: 320 }}>
                        <div className="cell-title">{prompt.name}</div>
                        <div className="cell-sub">{prompt.prompt}</div>
                      </td>
                      <td className="ui-nowrap">
                        {objectLabel(prompt.object_type)} → <strong>{prompt.field_label}</strong>
                      </td>
                      <td style={{ maxWidth: 220 }}>
                        {prompt.keywords.length ? (
                          <div className="conv-keywords">
                            {prompt.keywords.slice(0, 5).map((keyword) => (
                              <span key={keyword} className="conv-keyword">
                                {keyword}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="ui-muted">—</span>
                        )}
                      </td>
                      <td>{prompt.apply_mode === "auto" ? <Badge tone="warning">Updates the field</Badge> : <Badge tone="info">Suggests</Badge>}</td>
                      <td>{prompt.is_active ? <Badge tone="success">On</Badge> : <Badge>Off</Badge>}</td>
                      {editable ? (
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${prompt.name}`} onClick={() => setEditing(prompt)}>
                            <Pencil size={14} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${prompt.name}`} onClick={() => setDeleting(prompt)}>
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
            <EmptyState
              icon={<MessageSquareQuote size={20} />}
              title="No prompts yet"
              message="Prompts only run on conversations linked to a deal, account or contact."
            />
          )}
        </Card>

        {editable && !items.length ? (
          <Card title="Start with an example">
            <div className="settings-rows">
              {EXAMPLES.map((example) => (
                <div key={example.name} className="settings-row">
                  <div className="settings-row-text">
                    <div className="settings-row-label">{example.name}</div>
                    <div className="settings-row-hint">{example.prompt}</div>
                  </div>
                  <div className="settings-row-control">
                    <button type="button" className="ui-btn ui-btn-sm" onClick={() => setEditing(example)}>
                      <Plus size={14} /> Use this
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        ) : null}
      </div>

      {editing && prompts.data ? (
        <PromptModal
          prompt={editing}
          targets={prompts.data.targets}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await prompts.reload();
          }}
        />
      ) : null}
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete prompt?"
        message={`AIVA stops answering “${deleting?.name ?? ""}” on new conversations. Suggestions already made stay on their conversations.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function PromptModal({
  prompt,
  targets,
  onClose,
  onSaved,
}: {
  prompt: Prompt | Draft;
  targets: Record<ObjectType, Target[]>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const existing = "id" in prompt ? prompt : null;
  const [draft, setDraft] = useState<Draft>({
    name: prompt.name,
    object_type: prompt.object_type,
    field_key: prompt.field_key,
    prompt: prompt.prompt,
    keywords: [...prompt.keywords],
    apply_mode: prompt.apply_mode,
    is_active: prompt.is_active,
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const fields = targets[draft.object_type] ?? [];

  function setObject(object_type: ObjectType) {
    const first = (targets[object_type] ?? [])[0]?.key ?? "";
    setDraft((current) => ({ ...current, object_type, field_key: (targets[object_type] ?? []).some((t) => t.key === current.field_key) ? current.field_key : first }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { ...draft, name: draft.name.trim(), prompt: draft.prompt.trim() };
    try {
      if (existing) await api.put(`/field-prompts/${existing.id}`, payload);
      else await api.post("/field-prompts", payload);
      toast(existing ? "Prompt saved." : "Prompt created.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      title={existing ? "Edit prompt" : "New prompt"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="prompt-form" className="ui-btn ui-btn-primary" disabled={saving || !draft.name.trim() || draft.prompt.trim().length < 5 || !draft.field_key}>
            {saving ? <span className="ui-spinner" /> : null} Save prompt
          </button>
        </>
      }
    >
      <form id="prompt-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Name" required>
            <input className="ui-input" value={draft.name} maxLength={160} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Budget" />
          </Field>
          <Field label="Status">
            <span className="ui-row" style={{ height: 44 }}>
              <Toggle label="Prompt is on" checked={draft.is_active} onChange={(is_active) => setDraft({ ...draft, is_active })} />
              <span className="ui-small">{draft.is_active ? "On" : "Off"}</span>
            </span>
          </Field>
          <Field label="Record">
            <select className="ui-select" value={draft.object_type} onChange={(event) => setObject(event.target.value as ObjectType)}>
              {OBJECTS.map((object) => (
                <option key={object.value} value={object.value}>
                  {object.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Field to fill in" hint="Custom fields you've added show here too.">
            <select className="ui-select" value={draft.field_key} onChange={(event) => setDraft({ ...draft, field_key: event.target.value })}>
              {fields.map((field) => (
                <option key={field.key} value={field.key}>
                  {field.label}
                  {field.key.startsWith("cf.") ? " (custom)" : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Question for AIVA" required className="span-2" hint="Ask it the way you'd ask a colleague who listened to the call.">
            <textarea
              className="ui-textarea"
              rows={3}
              value={draft.prompt}
              maxLength={2000}
              placeholder="What budget or deal size did the customer mention?"
              onChange={(event) => setDraft({ ...draft, prompt: event.target.value })}
            />
          </Field>
          <Field label="Keywords" className="span-2" hint="Words that usually appear near the answer. AIVA uses them to find it when AI isn't connected.">
            <TagsInput label="Keywords" value={draft.keywords} placeholder="budget, spend" onChange={(keywords) => setDraft({ ...draft, keywords: keywords.slice(0, 30) })} />
          </Field>
        </div>
        <div className="ui-stack" style={{ gap: 8 }}>
          <span className="ui-small ui-strong">When AIVA finds an answer</span>
          <label className="ui-check">
            <input type="radio" name="apply_mode" checked={draft.apply_mode === "suggest"} onChange={() => setDraft({ ...draft, apply_mode: "suggest" })} />
            <span>Suggest it on the conversation so someone can review and apply it</span>
          </label>
          <label className="ui-check">
            <input type="radio" name="apply_mode" checked={draft.apply_mode === "auto"} onChange={() => setDraft({ ...draft, apply_mode: "auto" })} />
            <span>Update the {objectLabel(draft.object_type).toLowerCase()} right away</span>
          </label>
        </div>
      </form>
    </Modal>
  );
}
