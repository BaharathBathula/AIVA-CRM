"use client";

import Link from "next/link";
import {
  Bot,
  Check,
  ClipboardCheck,
  ExternalLink,
  ListChecks,
  MousePointerClick,
  Phone,
  Sparkles,
  TrendingUp,
  Wand2,
  X,
} from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";

import { CallDialog, type CallTarget } from "@/components/call-dialog";
import { CustomFieldsCard } from "@/components/custom-fields";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, ConfirmDialog, Field, Modal } from "@/components/ui";
import { aiSourceLabel, isAiSource } from "@/lib/ai-source";
import { AivaApiError } from "@/lib/api";
import { date, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

// ----------------------------------------------------------------- Panels

function Panel({ title, subtitle, icon, actions, children }: { title: string; subtitle?: string; icon?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="detailPanel">
      <div className="detailPanelHeader">
        <div>
          <h2 style={{ display: "flex", alignItems: "center", gap: 7 }}>
            {icon}
            {title}
          </h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

// ----------------------------------------------------------------- Actions

interface ButtonDef {
  id: string;
  label: string;
  description: string | null;
  action_type: "open_url" | "webhook" | "function";
  config: { confirm?: boolean };
}

/** Call, enrich and custom buttons for a record's header. */
export function RecordActions({
  objectType,
  recordId,
  call,
  onChanged,
}: {
  objectType: string;
  recordId: string;
  call?: CallTarget;
  onChanged?: () => void;
}) {
  const { has, can } = useSession();
  const toast = useToast();
  const [calling, setCalling] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ButtonDef | null>(null);
  const buttons = useApi<ButtonDef[]>(has("custom_buttons") ? `/buttons?object_type=${objectType}` : null);
  const enrichable = ["lead", "contact", "account"].includes(objectType) && has("data_enrichment") && can("records.write");

  async function enrich() {
    setBusy("enrich");
    try {
      const result = await api.post<{ applied: Record<string, unknown> }>(`/enrich/${objectType}/${recordId}`);
      const count = Object.keys(result.applied).length;
      toast(count ? `Filled in ${count} field${count === 1 ? "" : "s"}.` : "Nothing new to fill in; insights updated.");
      onChanged?.();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function run(button: ButtonDef) {
    setBusy(button.id);
    setConfirm(null);
    try {
      const result = await api.post<{ open_url: string | null; new_tab?: boolean; message: string }>(`/buttons/${button.id}/run`, { record_id: recordId });
      if (result.open_url) {
        if (result.new_tab === false) window.location.assign(result.open_url);
        else window.open(result.open_url, "_blank", "noopener,noreferrer");
      } else {
        toast(result.message);
        onChanged?.();
      }
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {call && has("calling") && can("records.write") ? (
        <button type="button" className="secondaryButton" onClick={() => setCalling(true)}>
          <Phone size={15} /> Call
        </button>
      ) : null}
      {enrichable ? (
        <button type="button" className="secondaryButton" disabled={busy === "enrich"} onClick={() => void enrich()}>
          <Wand2 size={15} /> {busy === "enrich" ? "Enriching…" : "Enrich"}
        </button>
      ) : null}
      {(buttons.data ?? []).map((button) => (
        <button
          key={button.id}
          type="button"
          className="secondaryButton"
          title={button.description ?? undefined}
          disabled={busy === button.id}
          onClick={() => (button.config.confirm ? setConfirm(button) : void run(button))}
        >
          {button.action_type === "open_url" ? <ExternalLink size={15} /> : <MousePointerClick size={15} />} {button.label}
        </button>
      ))}
      {call ? <CallDialog target={call} open={calling} onClose={() => setCalling(false)} onLogged={onChanged} /> : null}
      <ConfirmDialog
        open={Boolean(confirm)}
        title={confirm ? `Run “${confirm.label}”?` : ""}
        message={confirm?.description ?? "This runs the button's actions on this record."}
        confirmLabel="Run"
        danger={false}
        onConfirm={() => confirm && void run(confirm)}
        onClose={() => setConfirm(null)}
      />
    </>
  );
}

// ----------------------------------------------------------------- Approvals

export interface ApprovalNeeded {
  rule_id: string;
  rule_name: string;
  requested_change: Record<string, unknown>;
  message: string;
}

/** Turns an "approval required" API error into data for <ApprovalModal>. */
export function approvalFromError(error: unknown): ApprovalNeeded | null {
  if (error instanceof AivaApiError && error.code === "approval_required") {
    return {
      rule_id: String(error.body.rule_id),
      rule_name: String(error.body.rule_name),
      requested_change: (error.body.requested_change as Record<string, unknown>) ?? {},
      message: error.message,
    };
  }
  return null;
}

export function ApprovalModal({
  needed,
  objectType,
  recordId,
  onClose,
  onSubmitted,
}: {
  needed: ApprovalNeeded | null;
  objectType: "opportunity" | "quote";
  recordId: string;
  onClose: () => void;
  onSubmitted?: () => void;
}) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!needed) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/approvals", {
        object_type: objectType,
        record_id: recordId,
        rule_id: needed.rule_id,
        requested_change: needed.requested_change,
        comment: comment || null,
      });
      toast("Sent for approval. You'll be notified when it's decided.");
      setComment("");
      onSubmitted?.();
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(needed)}
      title="Approval needed"
      description={needed?.message}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Not now
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void submit()}>
            <ClipboardCheck size={15} /> Submit for approval
          </button>
        </>
      }
    >
      {error ? <Alert>{error}</Alert> : null}
      <Field label="Note for the approver" hint="The record is locked for edits until the request is decided.">
        <textarea className="ui-textarea" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Why this should be approved" />
      </Field>
    </Modal>
  );
}

interface ApprovalRow {
  id: string;
  rule_name: string;
  status: string;
  requested_change: Record<string, unknown>;
  requested_by: string | null;
  approver: string | null;
  decided_by: string | null;
  decision_comment: string | null;
  created_at: string;
  can_decide: boolean;
}

function ApprovalsPanel({ recordId }: { recordId: string }) {
  const rows = useApi<ApprovalRow[]>(`/approvals/record/${recordId}`);
  if (!rows.data?.length) return null;
  return (
    <Panel title="Approvals" subtitle="Requests for this record." icon={<ClipboardCheck size={15} />}>
      <div className="ui-stack" style={{ gap: 10 }}>
        {rows.data.map((row) => (
          <div key={row.id} className="ui-stack" style={{ gap: 4 }}>
            <div className="ui-row">
              <strong style={{ fontSize: 13 }}>{row.rule_name}</strong>
              <span className="ui-spacer" />
              <Badge tone={row.status === "approved" ? "success" : row.status === "rejected" ? "danger" : row.status === "pending" ? "warning" : "neutral"}>
                {row.status}
              </Badge>
            </div>
            <span className="ui-muted ui-small">
              {row.requested_by} asked {relativeTime(row.created_at)}
              {row.status === "pending" ? ` · waiting on ${row.approver ?? "an approver"}` : row.decided_by ? ` · ${row.decided_by}` : ""}
              {row.decision_comment ? ` — “${row.decision_comment}”` : ""}
            </span>
            {row.can_decide ? (
              <Link href="/approvals" className="ui-small">
                Review in Approvals
              </Link>
            ) : null}
          </div>
        ))}
      </div>
    </Panel>
  );
}

// ----------------------------------------------------------------- AI

interface Suggestion {
  id: string;
  agent_name: string;
  title: string;
  body: string | null;
  confidence: number;
  source: string;
  created_at: string;
}

function SuggestionsPanel({ objectType, recordId, onChanged }: { objectType: string; recordId: string; onChanged?: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const rows = useApi<Suggestion[]>(`/ai/suggestions?record_id=${recordId}&record_type=${objectType}`);
  const [busy, setBusy] = useState<string | null>(null);
  if (!can("automation.ai") || !rows.data?.length) return null;

  async function act(id: string, action: "accept" | "dismiss") {
    setBusy(id);
    try {
      const result = await api.post<{ message: string }>(`/ai/suggestions/${id}/${action}`);
      toast(result.message);
      await rows.reload();
      if (action === "accept") onChanged?.();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Panel title="AIVA suggests" subtitle="From your AI agents." icon={<Bot size={15} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        {rows.data.slice(0, 4).map((s) => (
          <div key={s.id} className="ui-stack" style={{ gap: 6 }}>
            <strong style={{ fontSize: 13, lineHeight: 1.4 }}>{s.title}</strong>
            {s.body ? <span className="ui-muted ui-small" style={{ whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{s.body.length > 260 ? `${s.body.slice(0, 260)}…` : s.body}</span> : null}
            <div className="ui-row" style={{ gap: 6 }}>
              <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" disabled={busy === s.id} onClick={() => void act(s.id, "accept")}>
                <Check size={13} /> Do it
              </button>
              <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy === s.id} onClick={() => void act(s.id, "dismiss")}>
                <X size={13} /> Dismiss
              </button>
              <span className="ui-spacer" />
              <span className="ui-muted ui-small">{s.agent_name}</span>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function SummaryPanel({ objectType, recordId }: { objectType: string; recordId: string }) {
  const [summary, setSummary] = useState<{ summary: string; source: string; model?: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setSummary(await api.get(`/ai/summary/${objectType}/${recordId}`));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }, [objectType, recordId]);

  return (
    <Panel
      title="AI summary"
      subtitle={summary ? (isAiSource(summary.source) ? `Written by ${aiSourceLabel(summary.source)} from this record.` : "From this record's details and activity.") : "A quick briefing on this record."}
      icon={<Sparkles size={15} />}
      actions={
        <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void load()}>
          {busy ? "Thinking…" : summary ? "Refresh" : "Summarize"}
        </button>
      }
    >
      {error ? <Alert>{error}</Alert> : null}
      {summary ? <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{summary.summary}</p> : null}
    </Panel>
  );
}

// ----------------------------------------------------------------- Enrichment

function EnrichmentPanel({ objectType, recordId, version }: { objectType: string; recordId: string; version: number }) {
  const data = useApi<{ insights: Record<string, string> | null; attribution: Record<string, string> | null }>(`/enrich/${objectType}/${recordId}?v=${version}`);
  const insights = data.data?.insights;
  const attribution = data.data?.attribution;
  if (!insights && !attribution) return null;
  const rows: [string, string | undefined][] = [
    ["Seniority", insights?.seniority],
    ["Department", insights?.department],
    ["Email", insights?.email_type],
    ["Company site", insights?.website],
    ["Country", insights?.country],
    ["Industry", insights?.industry_guess],
    ["Source", attribution?.utm_source],
    ["Campaign", attribution?.utm_campaign],
    ["Google click ID", attribution?.gclid],
    ["Web form", attribution?.form],
  ];
  const shown = rows.filter(([, value]) => value);
  return (
    <Panel title="Insights" subtitle={insights?.enriched_at ? `Enriched ${relativeTime(insights.enriched_at)}` : "Where this record came from."} icon={<Wand2 size={15} />}>
      <div className="detailFields">
        {shown.map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            {value!.startsWith("http") ? (
              <a href={value} target="_blank" rel="noopener noreferrer">
                {value!.replace(/^https?:\/\//, "")}
              </a>
            ) : (
              <strong>{value}</strong>
            )}
          </div>
        ))}
        {insights?.linkedin_search ? (
          <div>
            <span>LinkedIn</span>
            <a href={insights.linkedin_search} target="_blank" rel="noopener noreferrer">
              Search profiles
            </a>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

// ----------------------------------------------------------------- Predictions and playbooks

interface PredictionData {
  closed?: boolean;
  outcome?: string;
  probability: number;
  label: string;
  factors: { label: string; impact: string }[];
  predicted_close_date: string | null;
  model?: { closed_deals: number; confidence: string };
}

function PredictionPanel({ recordId, version }: { recordId: string; version: number }) {
  const data = useApi<PredictionData>(`/predictions/opportunities/${recordId}?v=${version}`);
  const p = data.data;
  if (!p || p.closed) return null;
  const color = p.label === "likely" ? "var(--success)" : p.label === "at_risk" ? "var(--danger)" : "var(--warning)";
  return (
    <Panel title="Win prediction" subtitle={`Learned from ${p.model?.closed_deals ?? 0} closed deals · ${p.model?.confidence ?? "low"} confidence`} icon={<TrendingUp size={15} />}>
      <div className="win-gauge" style={{ marginBottom: 12 }}>
        <div className="ring" style={{ ["--value" as string]: p.probability, ["--gauge" as string]: color }}>
          <span>{p.probability}%</span>
        </div>
        <div>
          <strong style={{ display: "block", textTransform: "capitalize" }}>{p.label.replace("_", " ")}</strong>
          <span className="ui-muted ui-small">Likely close {date(p.predicted_close_date)}</span>
        </div>
      </div>
      <ul className="factor-list">
        {p.factors.map((factor) => (
          <li key={factor.label}>
            <span className={factor.impact}>{factor.impact === "positive" ? "▲" : factor.impact === "negative" ? "▼" : "•"}</span>
            {factor.label}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

interface Playbook {
  stage_id: string;
  current: { guidance: string | null; exit_requires_checklist: boolean; checklist: { item: string; done: boolean }[] };
  stages: { id: string; name: string; is_current: boolean; missing: { key: string; label: string }[]; has_rule: boolean }[];
}

function PlaybookPanel({ recordId, version }: { recordId: string; version: number }) {
  const toast = useToast();
  const { can } = useSession();
  const data = useApi<Playbook>(`/process/opportunities/${recordId}?v=${version}`);
  const playbook = data.data;
  if (!playbook) return null;
  const upcoming = playbook.stages.filter((s) => !s.is_current && s.missing.length);
  if (!playbook.current.checklist.length && !playbook.current.guidance && !upcoming.length) return null;

  async function toggle(item: string, done: boolean) {
    try {
      data.setData(await api.post<Playbook>(`/process/opportunities/${recordId}/checklist`, { item, done }));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <Panel title="Stage playbook" subtitle={playbook.current.exit_requires_checklist ? "Finish the checklist to move on." : "Guidance for this stage."} icon={<ListChecks size={15} />}>
      {playbook.current.guidance ? <p style={{ margin: "0 0 10px", fontSize: 13, lineHeight: 1.5 }}>{playbook.current.guidance}</p> : null}
      {playbook.current.checklist.length ? (
        <ul className="checklist">
          {playbook.current.checklist.map((entry) => (
            <li key={entry.item}>
              <label>
                <input type="checkbox" checked={entry.done} disabled={!can("records.write")} onChange={(e) => void toggle(entry.item, e.target.checked)} />
                <span style={{ textDecoration: entry.done ? "line-through" : undefined, color: entry.done ? "var(--muted)" : undefined }}>{entry.item}</span>
              </label>
            </li>
          ))}
        </ul>
      ) : null}
      {upcoming.length ? (
        <div className="stage-reqs" style={{ marginTop: 10 }}>
          {upcoming.slice(0, 3).map((stage) => (
            <div key={stage.id}>
              <span>→</span>
              <span>
                To reach <strong>{stage.name}</strong>, fill in {stage.missing.map((m) => m.label.toLowerCase()).join(", ")}.
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}

// ----------------------------------------------------------------- All insights

/** Cards for a record page's side column: AI, insights, predictions, playbook, approvals, custom fields. */
export function RecordInsights({ objectType, recordId, version = 0, onChanged }: { objectType: string; recordId: string; version?: number; onChanged?: () => void }) {
  const { has, can } = useSession();
  return (
    <>
      {objectType === "opportunity" && has("predictive_intelligence") ? <PredictionPanel recordId={recordId} version={version} /> : null}
      {objectType === "opportunity" && has("process_management") ? <PlaybookPanel recordId={recordId} version={version} /> : null}
      {objectType === "opportunity" && has("approvals") ? <ApprovalsPanel recordId={recordId} /> : null}
      {has("ai_agents") && can("automation.ai") ? <SuggestionsPanel objectType={objectType} recordId={recordId} onChanged={onChanged} /> : null}
      {has("extended_ai") ? <SummaryPanel objectType={objectType} recordId={recordId} /> : null}
      {["lead", "contact", "account"].includes(objectType) && has("data_enrichment") ? (
        <EnrichmentPanel objectType={objectType} recordId={recordId} version={version} />
      ) : null}
      <CustomFieldsCard objectType={objectType} recordId={recordId} />
    </>
  );
}
