"use client";

import { ArrowDown, ArrowUp, ClipboardCheck, Pencil, Plus, Trash2, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Question = { id?: string | null; text: string; kind: "rating" | "yes_no"; weight: number };

type Scorecard = {
  id: string;
  name: string;
  description: string | null;
  questions: Question[];
  is_active: boolean;
  reviews: number;
  average: number;
  by_rep: { user_id: string | null; name: string; reviews: number; average: number }[];
};

type Draft = { name: string; description: string; questions: (Question & { key: string })[]; is_active: boolean };

let counter = 0;
const key = () => `q${Date.now().toString(36)}${(counter += 1)}`;

const STARTER: Omit<Question, "id">[] = [
  { text: "Set an agenda and confirmed the time at the start", kind: "yes_no", weight: 1 },
  { text: "Asked open questions to understand the problem", kind: "rating", weight: 2 },
  { text: "Uncovered budget, timeline and who decides", kind: "rating", weight: 2 },
  { text: "Handled objections clearly", kind: "rating", weight: 1 },
  { text: "Agreed a dated next step", kind: "yes_no", weight: 2 },
];

function scoreTone(value: number) {
  return value >= 75 ? "success" : value >= 50 ? "warning" : "danger";
}

export default function ScorecardsPage() {
  return (
    <SettingsShell feature="conversation_intelligence">
      <Scorecards />
    </SettingsShell>
  );
}

function Scorecards() {
  const toast = useToast();
  const scorecards = useApi<{ items: Scorecard[]; can_edit: boolean }>("/scorecards");
  const [editing, setEditing] = useState<Scorecard | "new" | null>(null);
  const [deleting, setDeleting] = useState<Scorecard | null>(null);
  const [busy, setBusy] = useState(false);
  const items = scorecards.data?.items ?? [];
  const editable = Boolean(scorecards.data?.can_edit);

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/scorecards/${deleting.id}`);
      toast("Scorecard deleted.");
      setDeleting(null);
      await scorecards.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setDeleting(null);
    } finally {
      setBusy(false);
    }
  }

  async function toggle(scorecard: Scorecard) {
    try {
      await api.put(`/scorecards/${scorecard.id}`, {
        name: scorecard.name,
        description: scorecard.description,
        questions: scorecard.questions,
        is_active: !scorecard.is_active,
      });
      toast(scorecard.is_active ? "Scorecard turned off." : "Scorecard turned on.");
      await scorecards.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Scorecards"
        description="Coaching scorecards for reviewing calls. Reviewers answer each question on a conversation; AIVA turns the answers into a 0–100 score and shows averages by rep."
        actions={
          editable ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New scorecard
            </button>
          ) : null
        }
      />
      {scorecards.error ? <Alert>{scorecards.error}</Alert> : null}
      {scorecards.loading && !scorecards.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : items.length ? (
        <div className="ui-stack">
          {items.map((scorecard) => (
            <Card
              key={scorecard.id}
              title={
                <span className="ui-row" style={{ gap: 8 }}>
                  {scorecard.name}
                  {scorecard.is_active ? null : <Badge>Off</Badge>}
                </span>
              }
              icon={<ClipboardCheck size={16} />}
              actions={
                editable ? (
                  <>
                    <Toggle label={`${scorecard.name} is on`} checked={scorecard.is_active} onChange={() => void toggle(scorecard)} />
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${scorecard.name}`} onClick={() => setEditing(scorecard)}>
                      <Pencil size={14} />
                    </button>
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${scorecard.name}`} onClick={() => setDeleting(scorecard)}>
                      <Trash2 size={14} />
                    </button>
                  </>
                ) : null
              }
            >
              <div className="ui-grid ui-grid-2">
                <div>
                  {scorecard.description ? <p className="ui-muted ui-small" style={{ marginTop: 0 }}>{scorecard.description}</p> : null}
                  <ol className="conv-questions" style={{ gap: 6 }}>
                    {scorecard.questions.map((question) => (
                      <li key={question.id ?? question.text} className="ui-small">
                        <span>
                          {question.text}{" "}
                          <span className="ui-muted">
                            · {question.kind === "yes_no" ? "Yes / no" : "1–5"}
                            {question.weight > 1 ? ` · ×${question.weight}` : ""}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
                <div>
                  <div className="ui-row" style={{ gap: 10 }}>
                    {scorecard.reviews ? <span className={`conv-score tone-${scoreTone(scorecard.average)}`}>{Math.round(scorecard.average)}</span> : null}
                    <span className="ui-small">
                      <strong>{scorecard.reviews}</strong> review{scorecard.reviews === 1 ? "" : "s"}
                      {scorecard.reviews ? <span className="ui-muted"> · average score</span> : <span className="ui-muted"> yet</span>}
                    </span>
                  </div>
                  {scorecard.by_rep.length ? (
                    <ul className="conv-rep-list">
                      {scorecard.by_rep.map((rep) => (
                        <li key={rep.user_id ?? rep.name}>
                          <span>{rep.name}</span>
                          <span>
                            {Math.round(rep.average)} <span className="ui-muted">({rep.reviews})</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <EmptyState
            icon={<ClipboardCheck size={20} />}
            title="No scorecards yet"
            message="A scorecard is a short list of things a great call includes. Start from our discovery-call template and adjust it."
            action={
              editable ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                  <Plus size={16} /> New scorecard
                </button>
              ) : undefined
            }
          />
        </Card>
      )}

      {editing ? (
        <ScorecardModal
          scorecard={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await scorecards.reload();
          }}
        />
      ) : null}
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete scorecard?"
        message={
          deleting?.reviews
            ? "Conversations were scored with this scorecard, so it can't be deleted. Turn it off instead to keep the history."
            : `“${deleting?.name ?? ""}” will be deleted.`
        }
        confirmLabel={deleting?.reviews ? (deleting.is_active ? "Turn it off" : "OK") : "Delete"}
        danger={!deleting?.reviews}
        busy={busy}
        onConfirm={() => {
          if (deleting?.reviews) {
            if (deleting.is_active) void toggle(deleting);
            setDeleting(null);
          } else void remove();
        }}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function ScorecardModal({ scorecard, onClose, onSaved }: { scorecard: Scorecard | "new"; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const existing = scorecard === "new" ? null : scorecard;
  const [draft, setDraft] = useState<Draft>(() => ({
    name: existing?.name ?? "Discovery call",
    description: existing?.description ?? "",
    is_active: existing?.is_active ?? true,
    questions: (existing?.questions ?? STARTER).map((q) => ({ ...q, key: key() })),
  }));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function update(index: number, patch: Partial<Question>) {
    setDraft((current) => ({ ...current, questions: current.questions.map((q, i) => (i === index ? { ...q, ...patch } : q)) }));
  }

  function move(index: number, delta: number) {
    setDraft((current) => {
      const questions = [...current.questions];
      const target = index + delta;
      if (target < 0 || target >= questions.length) return current;
      [questions[index], questions[target]] = [questions[target], questions[index]];
      return { ...current, questions };
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = {
      name: draft.name.trim(),
      description: draft.description.trim() || null,
      is_active: draft.is_active,
      questions: draft.questions
        .filter((q) => q.text.trim())
        .map((q) => ({ id: q.id ?? null, text: q.text.trim(), kind: q.kind, weight: q.weight })),
    };
    try {
      if (existing) await api.put(`/scorecards/${existing.id}`, payload);
      else await api.post("/scorecards", payload);
      toast(existing ? "Scorecard saved." : "Scorecard created.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  const ready = draft.name.trim() && draft.questions.some((q) => q.text.trim());

  return (
    <Modal
      open
      title={existing ? "Edit scorecard" : "New scorecard"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="scorecard-form" className="ui-btn ui-btn-primary" disabled={saving || !ready}>
            {saving ? <span className="ui-spinner" /> : null} Save scorecard
          </button>
        </>
      }
    >
      <form id="scorecard-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        {existing?.reviews ? (
          <Alert tone="info">Changing questions doesn&apos;t change past scores. Answers to removed questions stay on old reviews.</Alert>
        ) : null}
        <div className="ui-form-grid">
          <Field label="Name" required>
            <input className="ui-input" value={draft.name} maxLength={160} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          </Field>
          <Field label="Status">
            <span className="ui-row" style={{ height: 44 }}>
              <Toggle label="Scorecard is on" checked={draft.is_active} onChange={(is_active) => setDraft({ ...draft, is_active })} />
              <span className="ui-small">{draft.is_active ? "On — reviewers can use it" : "Off — hidden from reviewers"}</span>
            </span>
          </Field>
          <Field label="When to use it" className="span-2">
            <input
              className="ui-input"
              value={draft.description}
              maxLength={2000}
              placeholder="First calls with new prospects"
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            />
          </Field>
        </div>

        <div className="conv-question-editor">
          <span className="ui-small ui-strong">Questions</span>
          <div className="settings-list">
            {draft.questions.map((question, index) => (
              <div key={question.key} className="settings-list-row conv-question-row">
                <span className="settings-list-handle">
                  <button type="button" aria-label="Move up" disabled={index === 0} onClick={() => move(index, -1)}>
                    <ArrowUp size={13} />
                  </button>
                  <button type="button" aria-label="Move down" disabled={index === draft.questions.length - 1} onClick={() => move(index, 1)}>
                    <ArrowDown size={13} />
                  </button>
                </span>
                <span className="settings-list-fields">
                  <input
                    className="ui-input sm grow"
                    value={question.text}
                    maxLength={300}
                    placeholder="What should a great call include?"
                    aria-label={`Question ${index + 1}`}
                    onChange={(event) => update(index, { text: event.target.value })}
                  />
                  <select
                    className="ui-select sm"
                    aria-label={`Answer type for question ${index + 1}`}
                    value={question.kind}
                    onChange={(event) => update(index, { kind: event.target.value as Question["kind"] })}
                  >
                    <option value="rating">Rating 1–5</option>
                    <option value="yes_no">Yes / no</option>
                  </select>
                  <label className="conv-weight">
                    Weight
                    <input
                      className="ui-input sm"
                      type="number"
                      min={1}
                      max={10}
                      value={question.weight}
                      onChange={(event) => update(index, { weight: Math.max(1, Math.min(10, Number(event.target.value) || 1)) })}
                    />
                  </label>
                </span>
                <button
                  type="button"
                  className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                  aria-label={`Remove question ${index + 1}`}
                  disabled={draft.questions.length === 1}
                  onClick={() => setDraft((current) => ({ ...current, questions: current.questions.filter((_, i) => i !== index) }))}
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
          <div>
            <button
              type="button"
              className="ui-btn ui-btn-sm"
              disabled={draft.questions.length >= 40}
              onClick={() => setDraft((current) => ({ ...current, questions: [...current.questions, { text: "", kind: "rating", weight: 1, key: key() }] }))}
            >
              <Plus size={14} /> Add question
            </button>
          </div>
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Ratings count 1 as 0% and 5 as 100%; “yes” counts as 100% and “no” as 0%. Weight makes a question count more toward the total.
          </p>
        </div>
      </form>
    </Modal>
  );
}
