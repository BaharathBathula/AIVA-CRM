"use client";

import Link from "next/link";
import { Flame, Gauge, MapPinned, Target, UserRoundSearch } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { ColorDot } from "@/components/settings-kit";
import { Alert, Badge, Field, Modal, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

/**
 * Compact "Profile" panel for lead, contact and account pages: stage, score,
 * territory, matching personas, buying intent and a "Request data" button.
 * Each piece only shows when the workspace's plan includes it.
 */

type ObjectType = "lead" | "contact" | "account";

type Stage = { key: string; label: string; category: string; color: string };

type ProfileRecord = { id: string; stage?: string | null; lifecycle_stage?: string | null; score?: number | null; territory_id?: string | null };

type PersonaChip = { id: string; name: string; color: string };

type Intent = { level: "high" | "medium" | "low" | null; score: number; topics: { name: string; views: number }[]; last_seen?: string; visitors?: number };

const PATHS: Record<ObjectType, string> = { lead: "leads", contact: "contacts", account: "accounts" };
const LEVEL_TONE: Record<string, Tone> = { high: "danger", medium: "warning", low: "info" };

function scoreTone(score: number): Tone {
  return score >= 80 ? "success" : score >= 60 ? "info" : score >= 40 ? "warning" : "neutral";
}

export function RecordProfilePanel({
  objectType,
  recordId,
  version = 0,
  onChanged,
}: {
  objectType: ObjectType;
  recordId: string;
  version?: number;
  onChanged?: () => void;
}) {
  const { has, can } = useSession();
  const toast = useToast();
  const record = useApi<ProfileRecord>(`/${PATHS[objectType]}/${recordId}?v=${version}`);
  const stages = useApi<{ data: { contact: Stage[]; account: Stage[] } }>(objectType === "account" ? null : objectType === "contact" ? "/workspace-settings/stages" : null);
  const personas = useApi<{ items: PersonaChip[] }>(objectType !== "account" && has("buying_intent") ? `/personas/for/${objectType}/${recordId}?v=${version}` : null);
  const territories = useApi<{ items: { id: string; name: string; color: string }[] }>(objectType !== "contact" && has("territories") ? "/territories" : null);
  const intent = useApi<Intent>(objectType === "account" && has("buying_intent") ? `/intent/account/${recordId}` : null);
  const [requesting, setRequesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const data = record.data;
  const territory = data?.territory_id ? territories.data?.items.find((t) => t.id === data.territory_id) : null;
  const contactStages = stages.data?.data.contact ?? [];
  const currentStage = contactStages.find((s) => s.key === data?.stage);
  const canWrite = can("records.write");

  async function changeStage(stage: string) {
    setSaving(true);
    try {
      const updated = await api.patch<ProfileRecord>(`/contacts/${recordId}`, { stage });
      record.setData(updated);
      toast(`Stage set to ${contactStages.find((s) => s.key === stage)?.label ?? stage}.`);
      onChanged?.();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSaving(false);
    }
  }

  if (!data) return null;

  const showTerritory = objectType !== "contact" && has("territories");
  const showPersonas = objectType !== "account" && has("buying_intent");
  const showIntent = objectType === "account" && has("buying_intent");
  const score = data.score ?? 0;

  return (
    <section className="detailPanel dm-profile">
      <div className="detailPanelHeader">
        <div>
          <h2 style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <Gauge size={15} />
            Profile
          </h2>
          <p>{objectType === "account" ? "Score, territory and buying intent." : "Stage, score and who this person is."}</p>
        </div>
        {objectType !== "account" && canWrite ? (
          <button type="button" className="ui-btn ui-btn-sm" onClick={() => setRequesting(true)}>
            <UserRoundSearch size={14} /> Request data
          </button>
        ) : null}
      </div>
      <div className="detailFields">
        {objectType === "contact" ? (
          <div>
            <span>Stage</span>
            {canWrite && contactStages.length ? (
              <span className="dm-stage-select">
                <ColorDot color={currentStage?.color} />
                <select
                  className="ui-select sm"
                  aria-label="Contact stage"
                  value={data.stage ?? ""}
                  disabled={saving}
                  onChange={(event) => void changeStage(event.target.value)}
                >
                  {!currentStage && data.stage ? <option value={data.stage}>{data.stage}</option> : null}
                  {contactStages.map((stage) => (
                    <option key={stage.key} value={stage.key}>
                      {stage.label}
                    </option>
                  ))}
                </select>
              </span>
            ) : (
              <strong className="ui-row" style={{ gap: 6 }}>
                <ColorDot color={currentStage?.color} /> {currentStage?.label ?? data.stage ?? "—"}
              </strong>
            )}
          </div>
        ) : null}
        <div>
          <span>Score</span>
          <strong>
            <Badge tone={scoreTone(score)}>{score} / 100</Badge>{" "}
            <Link href="/settings/lead-scoring" className="ui-muted ui-small">
              How it&apos;s scored
            </Link>
          </strong>
        </div>
        {showTerritory ? (
          <div>
            <span>Territory</span>
            {territory ? (
              <strong className="ui-row" style={{ gap: 6 }}>
                <MapPinned size={12} /> <ColorDot color={territory.color} /> {territory.name}
              </strong>
            ) : (
              <strong className="ui-muted">{data.territory_id ? "—" : "None"}</strong>
            )}
          </div>
        ) : null}
        {showPersonas ? (
          <div>
            <span>Personas</span>
            {personas.data?.items.length ? (
              <span className="dm-chips">
                {personas.data.items.map((persona) => (
                  <span key={persona.id} className="dm-chip">
                    <ColorDot color={persona.color} /> {persona.name}
                  </span>
                ))}
              </span>
            ) : (
              <strong className="ui-muted">
                {personas.loading ? "…" : "No match"}{" "}
                <Link href="/settings/personas" className="ui-small" aria-label="Manage personas">
                  <Target size={11} />
                </Link>
              </strong>
            )}
          </div>
        ) : null}
        {showIntent ? (
          <div>
            <span>Buying intent</span>
            {intent.data?.level ? (
              <span className="dm-intent">
                <Badge tone={LEVEL_TONE[intent.data.level]}>
                  <Flame size={11} /> {intent.data.level[0].toUpperCase() + intent.data.level.slice(1)} · {intent.data.score}
                </Badge>
                <span className="ui-small">{intent.data.topics.map((t) => t.name).join(", ")}</span>
                {intent.data.last_seen ? <span className="ui-muted ui-small">Last seen {relativeTime(intent.data.last_seen)}</span> : null}
              </span>
            ) : (
              <strong className="ui-muted">{intent.loading ? "…" : "None in the last 30 days"}</strong>
            )}
          </div>
        ) : null}
      </div>
      {objectType !== "account" ? (
        <RequestDataModal
          open={requesting}
          recordType={objectType}
          recordId={recordId}
          onClose={() => setRequesting(false)}
        />
      ) : null}
    </section>
  );
}

const REQUEST_FIELDS: { key: string; label: string; credits: number }[] = [
  { key: "email", label: "Email", credits: 1 },
  { key: "phone", label: "Phone", credits: 1 },
  { key: "mobile", label: "Mobile phone", credits: 5 },
  { key: "job_title", label: "Job title", credits: 1 },
  { key: "company", label: "Company", credits: 1 },
  { key: "linkedin", label: "LinkedIn profile", credits: 1 },
];

/** Ask the data team (or the paid providers) to find missing details for one person. */
export function RequestDataModal({
  open,
  recordType,
  recordId,
  onClose,
}: {
  open: boolean;
  recordType: "lead" | "contact";
  recordId: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [fields, setFields] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setFields([]);
    setNote("");
    setError(null);
  }, [open]);

  const credits = REQUEST_FIELDS.filter((f) => fields.includes(f.key)).reduce((sum, f) => sum + f.credits, 0);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("/data-requests", { record_type: recordType, record_id: recordId, fields, note: note || null });
      toast("Request sent. You'll get a notification when it's filled in.");
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Request missing data"
      description="Someone on your data team (or a paid provider) will look it up and fill it in."
      onClose={onClose}
      size="sm"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="request-data-form" className="ui-btn ui-btn-primary" disabled={saving || !fields.length}>
            {saving ? <span className="ui-spinner" /> : null} Send request
          </button>
        </>
      }
    >
      <form id="request-data-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="dm-checks" role="group" aria-label="What do you need?">
          {REQUEST_FIELDS.map((field) => (
            <label key={field.key} className="ui-check">
              <input
                type="checkbox"
                checked={fields.includes(field.key)}
                onChange={(event) => setFields(event.target.checked ? [...fields, field.key] : fields.filter((f) => f !== field.key))}
              />
              <span>
                {field.label} <span className="ui-muted ui-small">{field.credits} credit{field.credits === 1 ? "" : "s"}</span>
              </span>
            </label>
          ))}
        </div>
        <Field label="Note" hint="Anything that helps, like where you met them.">
          <textarea className="ui-textarea" rows={2} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} />
        </Field>
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          {credits ? `Up to ${credits} credit${credits === 1 ? "" : "s"}, only charged for what's found.` : "Pick at least one thing you need."}{" "}
          <Link href="/settings/data-requests">See all requests</Link>
        </p>
      </form>
    </Modal>
  );
}
