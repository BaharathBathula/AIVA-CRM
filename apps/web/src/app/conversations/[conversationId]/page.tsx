"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  ClipboardCheck,
  Download,
  ListChecks,
  MessagesSquare,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useState, type FormEvent } from "react";

import { FeaturePage } from "@/components/plan";
import { useToast } from "@/components/providers";
import { RecordSearch, type FoundRecord } from "@/components/record-search";
import { ColorDot } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { aiSourceLabel, isAiSource } from "@/lib/ai-source";
import { date, dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function ConversationPage() {
  return (
    <FeaturePage feature="conversation_intelligence">
      <ConversationView />
    </FeaturePage>
  );
}

type Utterance = { speaker: string; text: string; start: number | null; end: number | null };
type Hit = { index: number; speaker: string; keyword: string; start: number | null; text: string };
type TrackerHits = Record<string, { name: string; category: string | null; color: string | null; count: number; hits: Hit[] }>;

type Suggestion = {
  id: string;
  prompt_id: string;
  name: string;
  object_type: "opportunity" | "account" | "contact";
  record_id: string;
  field_key: string;
  field_label: string;
  value: string;
  evidence: string | null;
  /** openai | azure_openai | claude: a model wrote it; rules: keyword match. */
  source: string;
  status: "pending" | "applied" | "dismissed";
  apply_mode: "suggest" | "auto";
  decided_by?: string;
};

type Review = {
  id: string;
  scorecard_id: string;
  scorecard_name: string;
  reviewer: string | null;
  score: number;
  answers: Record<string, boolean | number | string>;
  comment: string | null;
  created_at: string;
};

type Conversation = {
  id: string;
  title: string;
  source: string;
  kind: "external" | "internal";
  status: "processing" | "ready" | "failed";
  error: string | null;
  occurred_at: string;
  duration_seconds: number | null;
  owner_name: string | null;
  account_id: string | null;
  account_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  opportunity_id: string | null;
  opportunity_name: string | null;
  meeting_id: string | null;
  participants: { name: string | null; email?: string | null; user_id?: string | null }[];
  summary: string | null;
  summary_source: string | null;
  action_items: string[];
  talk_time: {
    unit?: "seconds" | "words";
    speakers?: { speaker: string; amount: number; percent: number }[];
    longest_monologue?: number | null;
    questions?: number;
  };
  has_recording: boolean;
  transcript: Utterance[];
  tracker_hits: TrackerHits;
  field_suggestions: Suggestion[];
  reviews: Review[];
  can_review: boolean;
  can_download: boolean;
  can_delete: boolean;
  can_edit: boolean;
  retain_until: string | null;
};

type Scorecard = {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  questions: { id: string; text: string; kind: "rating" | "yes_no"; weight: number }[];
};

const SPEAKER_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-7)"];

const RECORD_PATHS: Record<string, string> = { opportunity: "opportunities", account: "accounts", contact: "contacts" };
const OBJECT_LABELS: Record<string, string> = { opportunity: "Deal", account: "Account", contact: "Contact" };

function clock(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return null;
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function minutesLabel(seconds: number | null | undefined) {
  if (!seconds) return null;
  if (seconds < 60) return `${seconds} sec`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Wraps search matches and tracker keywords in <mark>. */
function Highlight({ text, keywords, term }: { text: string; keywords: string[]; term: string }) {
  const parts = useMemo(() => {
    const words = [...(term ? [term] : []), ...keywords].filter(Boolean);
    if (!words.length) return [{ text, kind: null as null | "term" | "tracker" }];
    const pattern = new RegExp(`(${words.map(escapeRegExp).sort((a, b) => b.length - a.length).join("|")})`, "gi");
    const lowerTerm = term.toLowerCase();
    return text.split(pattern).map((piece) => {
      const lower = piece.toLowerCase();
      if (term && lower === lowerTerm) return { text: piece, kind: "term" as const };
      if (keywords.some((k) => k.toLowerCase() === lower)) return { text: piece, kind: "tracker" as const };
      return { text: piece, kind: null };
    });
  }, [text, keywords, term]);
  return (
    <>
      {parts.map((part, index) =>
        part.kind ? (
          <mark key={index} className={`conv-mark${part.kind === "tracker" ? " tracker" : ""}`}>
            {part.text}
          </mark>
        ) : (
          <Fragment key={index}>{part.text}</Fragment>
        ),
      )}
    </>
  );
}

function ConversationView() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const router = useRouter();
  const toast = useToast();
  const conversation = useApi<Conversation>(`/conversations/${conversationId}`);
  const scorecards = useApi<{ items: Scorecard[] }>("/scorecards");
  const [editing, setEditing] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [term, setTerm] = useState("");
  const [speaker, setSpeaker] = useState("");
  const [focus, setFocus] = useState<number | null>(null);
  const c = conversation.data;

  // A transcription that's still running gets checked again every 15 seconds.
  const reload = conversation.reload;
  useEffect(() => {
    if (c?.status !== "processing") return;
    const timer = window.setInterval(() => void reload(), 15000);
    return () => window.clearInterval(timer);
  }, [c?.status, reload]);

  useEffect(() => {
    if (focus === null) return;
    const element = document.getElementById(`utterance-${focus}`);
    element?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focus]);

  const speakerColors = useMemo(() => {
    const order = (c?.talk_time.speakers ?? []).map((s) => s.speaker);
    for (const u of c?.transcript ?? []) if (!order.includes(u.speaker)) order.push(u.speaker);
    return Object.fromEntries(order.map((name, index) => [name, SPEAKER_COLORS[index % SPEAKER_COLORS.length]]));
  }, [c]);

  const keywords = useMemo(() => {
    const set = new Set<string>();
    for (const tracker of Object.values(c?.tracker_hits ?? {})) for (const hit of tracker.hits ?? []) set.add(hit.keyword);
    return [...set];
  }, [c]);

  const trackers = useMemo(
    () => Object.entries(c?.tracker_hits ?? {}).filter(([key]) => !key.startsWith("_")).sort(([, a], [, b]) => b.count - a.count),
    [c],
  );

  const lines = useMemo(() => {
    const needle = term.trim().toLowerCase();
    return (c?.transcript ?? [])
      .map((u, index) => ({ ...u, index }))
      .filter((u) => (!speaker || u.speaker === speaker) && (!needle || u.text.toLowerCase().includes(needle) || u.speaker.toLowerCase().includes(needle)));
  }, [c, term, speaker]);

  if (conversation.error && !c) {
    return (
      <>
        <BackLink />
        <Alert>{conversation.error}</Alert>
      </>
    );
  }
  if (!c) {
    return (
      <div className="ui-card">
        <LoadingRows rows={10} />
      </div>
    );
  }

  async function reprocess() {
    setBusy(true);
    try {
      conversation.setData(await api.post<Conversation>(`/conversations/${conversationId}/reprocess`));
      toast("Summary, topics and suggestions refreshed.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/conversations/${conversationId}`);
      toast("Conversation deleted.");
      router.push("/conversations");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  function jump(index: number) {
    setTerm("");
    setSpeaker("");
    setFocus(index);
  }

  const pending = c.field_suggestions.filter((s) => s.status === "pending");
  const decided = c.field_suggestions.filter((s) => s.status !== "pending");
  const scorecardById = Object.fromEntries((scorecards.data?.items ?? []).map((s) => [s.id, s]));
  const meta = [date(c.occurred_at), minutesLabel(c.duration_seconds), c.owner_name ? `Owner: ${c.owner_name}` : null].filter(Boolean).join(" · ");

  return (
    <>
      <BackLink />
      <PageHeader
        eyebrow={c.kind === "internal" ? "Team meeting" : "Customer conversation"}
        title={c.title}
        description={meta}
        actions={
          <>
            {c.can_review ? (
              <button type="button" className="ui-btn" onClick={() => setReviewing(true)} disabled={!c.transcript.length}>
                <ClipboardCheck size={16} /> Review this call
              </button>
            ) : null}
            {c.can_edit ? (
              <>
                <button type="button" className="ui-btn" onClick={() => setEditing(true)}>
                  <Pencil size={16} /> Edit
                </button>
                <button type="button" className="ui-btn" onClick={() => void reprocess()} disabled={busy || !c.transcript.length} title="Run the summary, trackers and field prompts again">
                  {busy ? <span className="ui-spinner" /> : <RefreshCw size={16} />} Re-analyze
                </button>
              </>
            ) : null}
            {c.can_delete ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon" aria-label="Delete conversation" onClick={() => setDeleting(true)}>
                <Trash2 size={16} />
              </button>
            ) : null}
          </>
        }
      />

      {c.status === "processing" ? (
        <Alert tone="info">The recording is being transcribed. This page refreshes on its own; long calls can take a few minutes.</Alert>
      ) : null}
      {c.status === "failed" ? <Alert>Transcription failed: {c.error ?? "unknown error"}. Upload the meeting&apos;s transcript file instead.</Alert> : null}

      <div className="ui-split conv-detail">
        <div className="ui-stack">
          <Card
            title="Summary"
            icon={<Sparkles size={16} />}
            actions={c.summary_source ? <Badge tone={isAiSource(c.summary_source) ? "info" : "neutral"}>{isAiSource(c.summary_source) ? `Written by ${aiSourceLabel(c.summary_source)}` : "Automatic outline"}</Badge> : null}
          >
            {c.summary ? <p className="conv-summary">{c.summary}</p> : <p className="ui-muted" style={{ margin: 0 }}>No summary yet.</p>}
            {c.action_items.length ? (
              <div className="conv-actions">
                <h3>
                  <ListChecks size={15} /> Next steps and commitments
                </h3>
                <ul>
                  {c.action_items.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Card>

          {c.field_suggestions.length ? (
            <Card title="Suggested CRM updates" icon={<Sparkles size={16} />} actions={pending.length ? <Badge tone="warning">{pending.length} to review</Badge> : null}>
              <div className="conv-suggestions">
                {pending.map((suggestion) => (
                  <SuggestionRow key={suggestion.id} conversationId={c.id} suggestion={suggestion} editable={c.can_edit} onDone={conversation.setData} />
                ))}
                {decided.map((suggestion) => (
                  <SuggestionRow key={suggestion.id} conversationId={c.id} suggestion={suggestion} editable={false} onDone={conversation.setData} />
                ))}
              </div>
            </Card>
          ) : null}

          <Card
            title="Transcript"
            icon={<MessagesSquare size={16} />}
            flush
            actions={
              c.transcript.length ? (
                <div className="conv-transcript-tools">
                  <select className="ui-select sm" value={speaker} onChange={(event) => setSpeaker(event.target.value)} aria-label="Speaker">
                    <option value="">Everyone</option>
                    {Object.keys(speakerColors).map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                  <div className="ui-search">
                    <Search size={15} />
                    <input className="ui-input sm" placeholder="Search the transcript" value={term} onChange={(event) => setTerm(event.target.value)} aria-label="Search the transcript" />
                  </div>
                </div>
              ) : null
            }
          >
            {c.transcript.length ? (
              <>
                {term.trim() || speaker ? (
                  <p className="ui-muted ui-small conv-transcript-count">
                    {lines.length} of {c.transcript.length} lines
                  </p>
                ) : null}
                <ol className="conv-transcript">
                  {lines.map((line) => (
                    <li key={line.index} id={`utterance-${line.index}`} className={focus === line.index ? "focus" : undefined}>
                      <span className="conv-utt-head">
                        <span className="conv-speaker" style={{ color: speakerColors[line.speaker] }}>
                          {line.speaker}
                        </span>
                        {clock(line.start) ? <span className="conv-time">{clock(line.start)}</span> : null}
                      </span>
                      <span className="conv-utt-text">
                        <Highlight text={line.text} keywords={keywords} term={term.trim()} />
                      </span>
                    </li>
                  ))}
                  {!lines.length ? <li className="ui-muted ui-small">Nothing matches.</li> : null}
                </ol>
              </>
            ) : (
              <EmptyState title="No transcript yet" message={c.status === "processing" ? "It appears here once transcription finishes." : "This conversation has no transcript."} />
            )}
          </Card>
        </div>

        <div className="ui-stack">
          <Card title="Details">
            <dl className="ui-dl">
              <dt>When</dt>
              <dd>{dateTime(c.occurred_at)}</dd>
              {c.duration_seconds ? (
                <>
                  <dt>Length</dt>
                  <dd>{minutesLabel(c.duration_seconds)}</dd>
                </>
              ) : null}
              <dt>Owner</dt>
              <dd>{c.owner_name ?? "—"}</dd>
              <dt>Account</dt>
              <dd>{c.account_id ? <Link href={`/accounts/${c.account_id}`}>{c.account_name ?? "Account"}</Link> : "—"}</dd>
              <dt>Deal</dt>
              <dd>{c.opportunity_id ? <Link href={`/opportunities/${c.opportunity_id}`}>{c.opportunity_name ?? "Deal"}</Link> : "—"}</dd>
              <dt>Contact</dt>
              <dd>{c.contact_id ? <Link href={`/contacts/${c.contact_id}`}>{c.contact_name ?? "Contact"}</Link> : "—"}</dd>
              {c.meeting_id ? (
                <>
                  <dt>Meeting</dt>
                  <dd>
                    <Link href={`/meetings?open=${c.meeting_id}`}>Open the meeting</Link>
                  </dd>
                </>
              ) : null}
              {c.participants.length ? (
                <>
                  <dt>Participants</dt>
                  <dd>
                    {c.participants
                      .map((p) => p.name || p.email)
                      .filter(Boolean)
                      .join(", ")}
                  </dd>
                </>
              ) : null}
              {c.retain_until ? (
                <>
                  <dt>Kept until</dt>
                  <dd>{date(c.retain_until)}</dd>
                </>
              ) : null}
            </dl>
          </Card>

          {c.has_recording ? (
            <Card title="Recording">
              {c.can_download ? (
                <div className="ui-stack" style={{ gap: 10 }}>
                  <audio className="conv-audio" controls preload="none" src={`/api/aiva/conversations/${c.id}/recording`}>
                    Your browser can&apos;t play this recording.
                  </audio>
                  <a className="ui-btn ui-btn-sm" href={`/api/aiva/conversations/${c.id}/recording`} download>
                    <Download size={14} /> Download
                  </a>
                </div>
              ) : (
                <p className="ui-muted ui-small" style={{ margin: 0 }}>
                  Your workspace limits who can play and download recordings.
                </p>
              )}
            </Card>
          ) : null}

          {c.talk_time.speakers?.length ? (
            <Card title="Talk time">
              <ul className="conv-talk">
                {c.talk_time.speakers.map((s) => (
                  <li key={s.speaker}>
                    <span className="ui-truncate">{s.speaker}</span>
                    <span className="ui-muted ui-small">{Math.round(s.percent)}%</span>
                    <span className="bar">
                      <span style={{ width: `${s.percent}%`, background: speakerColors[s.speaker] }} />
                    </span>
                  </li>
                ))}
              </ul>
              <p className="ui-muted ui-small" style={{ margin: "12px 0 0" }}>
                {c.talk_time.questions ?? 0} question{c.talk_time.questions === 1 ? "" : "s"} asked
                {c.talk_time.longest_monologue ? ` · longest stretch ${clock(c.talk_time.longest_monologue)}` : ""}
                {c.talk_time.unit === "words" ? " · measured in words (the transcript has no timings)" : ""}
              </p>
            </Card>
          ) : null}

          <Card title="Topics">
            {trackers.length ? (
              <div className="conv-topics">
                {trackers.map(([id, tracker]) => (
                  <details key={id} className="conv-topic">
                    <summary>
                      <ColorDot color={tracker.color} />
                      <span className="ui-truncate">{tracker.name}</span>
                      <Badge>{tracker.count}</Badge>
                    </summary>
                    <ul>
                      {tracker.hits.map((hit, index) => (
                        <li key={`${hit.index}-${index}`}>
                          <button type="button" onClick={() => jump(hit.index)}>
                            <span className="conv-time">{clock(hit.start) ?? `Line ${hit.index + 1}`}</span>
                            <span>
                              <strong>{hit.speaker}:</strong> “{hit.keyword}”
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </details>
                ))}
              </div>
            ) : (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                No tracked topics came up. <Link href="/settings/trackers">Set up trackers</Link> for competitors, pricing or objections.
              </p>
            )}
          </Card>

          <Card
            title="Scorecard reviews"
            icon={<Star size={16} />}
            actions={
              c.can_review ? (
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => setReviewing(true)} disabled={!c.transcript.length}>
                  <Plus size={14} /> Review
                </button>
              ) : null
            }
          >
            {c.reviews.length ? (
              <div className="conv-reviews">
                {c.reviews.map((review) => (
                  <ReviewItem key={review.id} review={review} scorecard={scorecardById[review.scorecard_id]} />
                ))}
              </div>
            ) : (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                {c.can_review ? "No reviews yet. Score this call against one of your scorecards to coach the rep." : "No reviews yet."}
              </p>
            )}
          </Card>
        </div>
      </div>

      {editing ? <EditModal conversation={c} onClose={() => setEditing(false)} onSaved={(next) => conversation.setData(next)} /> : null}
      {reviewing ? (
        <ReviewModal
          conversationId={c.id}
          scorecards={(scorecards.data?.items ?? []).filter((s) => s.is_active)}
          onClose={() => setReviewing(false)}
          onSaved={(next) => conversation.setData(next)}
        />
      ) : null}
      <ConfirmDialog
        open={deleting}
        title="Delete this conversation?"
        message="The transcript, summary, recording and its scorecard reviews are deleted for good. Fields already updated from it keep their values."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}

function BackLink() {
  return (
    <Link href="/conversations" className="ui-btn ui-btn-ghost ui-btn-sm conv-back">
      <ArrowLeft size={15} /> Conversations
    </Link>
  );
}

// ---------------------------------------------------------------- Suggestions

function SuggestionRow({
  conversationId,
  suggestion,
  editable,
  onDone,
}: {
  conversationId: string;
  suggestion: Suggestion;
  editable: boolean;
  onDone: (next: Conversation) => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState(String(suggestion.value ?? ""));
  const [busy, setBusy] = useState<"apply" | "dismiss" | null>(null);
  const href = `/${RECORD_PATHS[suggestion.object_type] ?? "accounts"}/${suggestion.record_id}`;

  async function decide(action: "apply" | "dismiss") {
    setBusy(action);
    try {
      const next = await api.post<Conversation>(`/conversations/${conversationId}/suggestions/${suggestion.id}`, {
        action,
        value: action === "apply" ? value : null,
      });
      toast(action === "apply" ? `${suggestion.field_label} updated.` : "Suggestion dismissed.");
      onDone(next);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={`conv-suggestion${suggestion.status !== "pending" ? " done" : ""}`}>
      <div className="conv-suggestion-head">
        <span>
          <strong>{suggestion.field_label}</strong>{" "}
          <span className="ui-muted ui-small">
            on the <Link href={href}>{(OBJECT_LABELS[suggestion.object_type] ?? suggestion.object_type).toLowerCase()}</Link> · {suggestion.name}
          </span>
        </span>
        {suggestion.status === "applied" ? (
          <Badge tone="success">
            <Check size={12} /> Applied{suggestion.decided_by ? ` by ${suggestion.decided_by}` : suggestion.apply_mode === "auto" ? " automatically" : ""}
          </Badge>
        ) : suggestion.status === "dismissed" ? (
          <Badge>Dismissed{suggestion.decided_by ? ` by ${suggestion.decided_by}` : ""}</Badge>
        ) : (
          <Badge tone={isAiSource(suggestion.source) ? "info" : "neutral"}>{isAiSource(suggestion.source) ? `AI · ${aiSourceLabel(suggestion.source)}` : "Keyword match"}</Badge>
        )}
      </div>
      {suggestion.status === "pending" && editable ? (
        <div className="conv-suggestion-edit">
          <input className="ui-input sm" value={value} onChange={(event) => setValue(event.target.value)} aria-label={`New ${suggestion.field_label}`} />
          <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" disabled={busy !== null || !value.trim()} onClick={() => void decide("apply")}>
            {busy === "apply" ? <span className="ui-spinner" /> : <Check size={14} />} Apply
          </button>
          <button type="button" className="ui-btn ui-btn-sm" disabled={busy !== null} onClick={() => void decide("dismiss")}>
            <X size={14} /> Dismiss
          </button>
        </div>
      ) : (
        <div className="conv-suggestion-value">{String(suggestion.value)}</div>
      )}
      {suggestion.evidence ? <blockquote className="conv-evidence">{suggestion.evidence}</blockquote> : null}
    </div>
  );
}

// ---------------------------------------------------------------- Reviews

function ReviewItem({ review, scorecard }: { review: Review; scorecard?: Scorecard }) {
  const tone = review.score >= 75 ? "success" : review.score >= 50 ? "warning" : "danger";
  return (
    <details className="conv-review">
      <summary>
        <span className={`conv-score tone-${tone}`}>{Math.round(review.score)}</span>
        <span className="conv-review-main">
          <strong className="ui-truncate">{review.scorecard_name}</strong>
          <span className="ui-muted ui-small">
            {review.reviewer ?? "Reviewer"} · {relativeTime(review.created_at)}
          </span>
        </span>
      </summary>
      <div className="conv-review-body">
        {scorecard ? (
          <ul>
            {scorecard.questions.map((question) => {
              const answer = review.answers[question.id];
              return (
                <li key={question.id}>
                  <span>{question.text}</span>
                  <span className="ui-strong">
                    {answer === undefined || answer === null || answer === ""
                      ? "—"
                      : question.kind === "yes_no"
                        ? answer === true || answer === "yes" || answer === "true"
                          ? "Yes"
                          : "No"
                        : `${answer} / 5`}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : null}
        {review.comment ? <p className="conv-review-comment">{review.comment}</p> : null}
      </div>
    </details>
  );
}

function ReviewModal({
  conversationId,
  scorecards,
  onClose,
  onSaved,
}: {
  conversationId: string;
  scorecards: Scorecard[];
  onClose: () => void;
  onSaved: (next: Conversation) => void;
}) {
  const toast = useToast();
  const [scorecardId, setScorecardId] = useState(scorecards[0]?.id ?? "");
  const [answers, setAnswers] = useState<Record<string, boolean | number>>({});
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const scorecard = scorecards.find((s) => s.id === scorecardId);

  useEffect(() => {
    if (!scorecardId && scorecards[0]) setScorecardId(scorecards[0].id);
  }, [scorecardId, scorecards]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const next = await api.post<Conversation>(`/conversations/${conversationId}/reviews`, { scorecard_id: scorecardId, answers, comment: comment.trim() || null });
      toast("Review saved.");
      onSaved(next);
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      title="Review this call"
      description="Score the call against a scorecard. The rep gets a notification with the result."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="review-form" className="ui-btn ui-btn-primary" disabled={saving || !scorecard || !Object.keys(answers).length}>
            {saving ? <span className="ui-spinner" /> : null} Save review
          </button>
        </>
      }
    >
      {!scorecards.length ? (
        <EmptyState
          icon={<ClipboardCheck size={20} />}
          title="No scorecards yet"
          message={
            <>
              Create one in <Link href="/settings/scorecards">Settings → Scorecards</Link>, then come back to review.
            </>
          }
        />
      ) : (
        <form id="review-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
          {error ? <Alert>{error}</Alert> : null}
          {scorecards.length > 1 ? (
            <Field label="Scorecard">
              <select
                className="ui-select"
                value={scorecardId}
                onChange={(event) => {
                  setScorecardId(event.target.value);
                  setAnswers({});
                }}
              >
                {scorecards.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {scorecard?.description ? <p className="ui-muted ui-small" style={{ margin: 0 }}>{scorecard.description}</p> : null}
          <ol className="conv-questions">
            {scorecard?.questions.map((question) => (
              <li key={question.id}>
                <span className="conv-question-text">
                  {question.text}
                  {question.weight > 1 ? <span className="ui-muted ui-small"> · counts ×{question.weight}</span> : null}
                </span>
                {question.kind === "yes_no" ? (
                  <span className="conv-answer" role="radiogroup" aria-label={question.text}>
                    {[
                      { label: "Yes", value: true },
                      { label: "No", value: false },
                    ].map((option) => (
                      <button
                        key={option.label}
                        type="button"
                        role="radio"
                        aria-checked={answers[question.id] === option.value}
                        className={`conv-answer-btn${answers[question.id] === option.value ? " on" : ""}`}
                        onClick={() => setAnswers((current) => ({ ...current, [question.id]: option.value }))}
                      >
                        {option.label}
                      </button>
                    ))}
                  </span>
                ) : (
                  <span className="conv-answer" role="radiogroup" aria-label={`${question.text} (1 to 5)`}>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        type="button"
                        role="radio"
                        aria-checked={answers[question.id] === n}
                        aria-label={`${n} out of 5`}
                        className={`conv-answer-btn${answers[question.id] === n ? " on" : ""}`}
                        onClick={() => setAnswers((current) => ({ ...current, [question.id]: n }))}
                      >
                        {n}
                      </button>
                    ))}
                  </span>
                )}
              </li>
            ))}
          </ol>
          <Field label="Coaching note" hint="What went well and one thing to try next time.">
            <textarea className="ui-textarea" rows={4} value={comment} maxLength={5000} onChange={(event) => setComment(event.target.value)} />
          </Field>
        </form>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- Edit

type LinkKey = "account" | "contact" | "opportunity" | "lead";
type LinkValue = { id: string; title: string } | null;

function EditModal({ conversation, onClose, onSaved }: { conversation: Conversation; onClose: () => void; onSaved: (next: Conversation) => void }) {
  const toast = useToast();
  const [title, setTitle] = useState(conversation.title);
  const [kind, setKind] = useState(conversation.kind);
  const [links, setLinks] = useState<Record<LinkKey, LinkValue | undefined>>({
    account: conversation.account_id ? { id: conversation.account_id, title: conversation.account_name ?? "Account" } : null,
    opportunity: conversation.opportunity_id ? { id: conversation.opportunity_id, title: conversation.opportunity_name ?? "Deal" } : null,
    contact: conversation.contact_id ? { id: conversation.contact_id, title: conversation.contact_name ?? "Contact" } : null,
    lead: undefined,
  });
  const [linking, setLinking] = useState<LinkKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const body: Record<string, string | null> = { title: title.trim(), kind };
    for (const key of ["account", "opportunity", "contact", "lead"] as LinkKey[]) {
      if (links[key] !== undefined) body[`${key}_id`] = links[key]?.id ?? null;
    }
    try {
      const next = await api.patch<Conversation>(`/conversations/${conversation.id}`, body);
      toast("Conversation saved.");
      onSaved(next);
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  const rows: { key: LinkKey; label: string }[] = [
    { key: "account", label: "Account" },
    { key: "opportunity", label: "Deal" },
    { key: "contact", label: "Contact" },
    { key: "lead", label: "Lead" },
  ];

  return (
    <Modal
      open
      title="Edit conversation"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="conversation-edit" className="ui-btn ui-btn-primary" disabled={saving || !title.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Save
          </button>
        </>
      }
    >
      <form id="conversation-edit" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Title" required>
          <input className="ui-input" value={title} maxLength={300} onChange={(event) => setTitle(event.target.value)} />
        </Field>
        <Field label="Type" hint="Team meetings follow your Team meetings settings.">
          <Tabs
            label="Type"
            value={kind}
            onChange={setKind}
            items={[
              { value: "external", label: "With customers" },
              { value: "internal", label: "Team meeting" },
            ]}
          />
        </Field>
        <div className="conv-edit-links">
          {rows.map((row) => {
            const value = links[row.key];
            return (
              <div key={row.key} className="conv-edit-link">
                <span className="ui-small ui-strong">{row.label}</span>
                <span className="conv-edit-link-value">
                  {value ? value.title : <span className="ui-muted">{value === undefined && row.key === "lead" ? "Unchanged" : "None"}</span>}
                </span>
                <span className="ui-row" style={{ gap: 6 }}>
                  <button type="button" className="ui-btn ui-btn-sm" onClick={() => setLinking(linking === row.key ? null : row.key)}>
                    {value ? "Change" : "Link"}
                  </button>
                  {value ? (
                    <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => setLinks((current) => ({ ...current, [row.key]: null }))}>
                      Unlink
                    </button>
                  ) : null}
                </span>
                {linking === row.key ? (
                  <div className="conv-edit-search">
                    <RecordSearch
                      types={[row.key]}
                      placeholder={`Search ${row.label.toLowerCase()}s`}
                      onPick={(record: FoundRecord) => {
                        setLinks((current) => ({ ...current, [row.key]: { id: record.id, title: record.title } }));
                        setLinking(null);
                      }}
                    />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </form>
    </Modal>
  );
}

