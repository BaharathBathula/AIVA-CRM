"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AudioLines, FileUp, MessagesSquare, Plus, Search, Settings2, Sparkles, X } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { RecordSearch, type FoundRecord } from "@/components/record-search";
import { ColorDot } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { date, dateTime } from "@/lib/format";
import { AivaApiError } from "@/lib/api";
import { api, errorMessage, query, useApi, useDebounced } from "@/lib/use-api";

export default function ConversationsPage() {
  return (
    <FeaturePage feature="conversation_intelligence">
      <Conversations />
    </FeaturePage>
  );
}

type Speaker = { speaker: string; amount: number; percent: number };

type ConversationRow = {
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
  summary: string | null;
  tracker_counts: { id: string; name: string; count: number; color: string | null }[];
  pending_suggestions: number;
  talk_time: { unit?: "seconds" | "words"; speakers?: Speaker[] };
  has_recording: boolean;
};

type ConversationList = {
  items: ConversationRow[];
  trackers: { id: string; name: string; color: string; mentions: number; conversations: number }[];
  stats: { count: number; hours: number; pending_suggestions: number };
};

const SPEAKER_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-7)"];

function minutesLabel(seconds: number | null | undefined) {
  if (!seconds) return null;
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function TalkBar({ speakers }: { speakers: Speaker[] }) {
  if (!speakers.length) return null;
  const label = speakers.map((s) => `${s.speaker} ${Math.round(s.percent)}%`).join(", ");
  return (
    <span className="conv-talkbar" role="img" aria-label={`Talk time: ${label}`} title={label}>
      {speakers.slice(0, 6).map((s, index) => (
        <span key={s.speaker} style={{ width: `${s.percent}%`, background: SPEAKER_COLORS[index % SPEAKER_COLORS.length] }} />
      ))}
    </span>
  );
}

function Conversations() {
  const { can } = useSession();
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search.trim(), 300);
  const [kind, setKind] = useState<"all" | "external" | "internal">("all");
  const [days, setDays] = useState("90");
  const [trackerId, setTrackerId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const list = useApi<ConversationList>(
    `/conversations${query({ search: debounced, kind: kind === "all" ? null : kind, days, tracker_id: trackerId })}`,
  );
  const stats = list.data?.stats;
  const trackers = list.data?.trackers ?? [];
  const topTracker = useMemo(() => [...trackers].sort((a, b) => b.mentions - a.mentions)[0], [trackers]);
  const items = list.data?.items ?? [];
  const filtered = Boolean(debounced || kind !== "all" || trackerId);

  return (
    <>
      <PageHeader
        title="Conversations"
        description="Calls and meetings with transcripts, summaries and the moments that matter. Add a transcript from Zoom, Teams or Meet, or upload a recording."
        actions={
          <>
            {can("admin.settings") ? (
              <Link href="/settings/recording" className="ui-btn">
                <Settings2 size={16} /> Settings
              </Link>
            ) : null}
            {can("records.write") ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setAdding(true)}>
                <Plus size={16} /> Add conversation
              </button>
            ) : null}
          </>
        }
      />

      <div className="ui-grid ui-grid-4 conv-kpis">
        <Kpi label="Conversations" value={stats ? stats.count : "—"} note={`Last ${days} days`} />
        <Kpi label="Hours captured" value={stats ? stats.hours : "—"} />
        <Kpi label="Field updates to review" value={stats ? stats.pending_suggestions : "—"} note={stats?.pending_suggestions ? "Open a conversation to apply them" : undefined} />
        <Kpi label="Most mentioned" value={topTracker && topTracker.mentions ? topTracker.name : "—"} note={topTracker && topTracker.mentions ? `${topTracker.mentions} mentions` : "Set up trackers to spot topics"} />
      </div>

      {trackers.length ? (
        <div className="conv-chips" role="group" aria-label="Filter by tracker">
          <button type="button" className={`conv-chip${trackerId === null ? " on" : ""}`} aria-pressed={trackerId === null} onClick={() => setTrackerId(null)}>
            All topics
          </button>
          {trackers.map((tracker) => (
            <button
              key={tracker.id}
              type="button"
              className={`conv-chip${trackerId === tracker.id ? " on" : ""}`}
              aria-pressed={trackerId === tracker.id}
              onClick={() => setTrackerId(trackerId === tracker.id ? null : tracker.id)}
            >
              <ColorDot color={tracker.color} />
              {tracker.name}
              <span className="conv-chip-count">{tracker.mentions}</span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="ui-toolbar">
        <Tabs
          label="Conversation type"
          value={kind}
          onChange={setKind}
          items={[
            { value: "all", label: "All" },
            { value: "external", label: "With customers" },
            { value: "internal", label: "Team meetings" },
          ]}
        />
        <select className="ui-select" value={days} onChange={(event) => setDays(event.target.value)} aria-label="Period">
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
          <option value="365">Last 12 months</option>
          <option value="3650">All time</option>
        </select>
        <span className="ui-spacer" />
        <div className="ui-search">
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search titles, summaries and transcripts" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Search conversations" />
        </div>
      </div>

      {list.error ? <Alert>{list.error}</Alert> : null}

      <Card flush>
        {list.loading && !list.data ? (
          <LoadingRows rows={6} />
        ) : items.length ? (
          <ul className="conv-list">
            {items.map((item) => (
              <li key={item.id}>
                <Link href={`/conversations/${item.id}`} className="conv-row">
                  <span className="conv-row-icon" aria-hidden>
                    {item.source === "audio_upload" ? <AudioLines size={17} /> : <MessagesSquare size={17} />}
                  </span>
                  <span className="conv-row-main">
                    <span className="conv-row-title">
                      {item.title}
                      {item.kind === "internal" ? <Badge>Team</Badge> : null}
                      {item.status === "processing" ? <Badge tone="info">Transcribing…</Badge> : null}
                      {item.status === "failed" ? <Badge tone="danger">Failed</Badge> : null}
                    </span>
                    <span className="conv-row-meta">
                      <span title={dateTime(item.occurred_at)}>{date(item.occurred_at)}</span>
                      {minutesLabel(item.duration_seconds) ? <span>{minutesLabel(item.duration_seconds)}</span> : null}
                      {item.owner_name ? <span>{item.owner_name}</span> : null}
                      {item.account_name ? <span className="conv-link-chip">{item.account_name}</span> : null}
                      {item.opportunity_name ? <span className="conv-link-chip">{item.opportunity_name}</span> : null}
                      {item.contact_name ? <span className="conv-link-chip">{item.contact_name}</span> : null}
                    </span>
                    {item.status === "failed" && item.error ? <span className="conv-row-summary danger">{item.error}</span> : null}
                    {item.summary ? <span className="conv-row-summary">{item.summary}</span> : null}
                  </span>
                  <span className="conv-row-side">
                    <span className="conv-row-badges">
                      {item.pending_suggestions ? (
                        <Badge tone="warning">
                          <Sparkles size={12} /> {item.pending_suggestions} to review
                        </Badge>
                      ) : null}
                      {item.tracker_counts.slice(0, 3).map((tracker) => (
                        <span key={tracker.id} className="conv-mini-chip">
                          <ColorDot color={tracker.color} />
                          {tracker.name} · {tracker.count}
                        </span>
                      ))}
                    </span>
                    <TalkBar speakers={item.talk_time?.speakers ?? []} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={<MessagesSquare size={20} />}
            title={filtered ? "No conversations match" : "No conversations yet"}
            message={
              filtered
                ? "Try a different search, topic or period."
                : "Paste a meeting transcript or upload a .vtt file from Zoom, Teams or Google Meet. AIVA writes the summary, spots topics and suggests field updates."
            }
            action={
              !filtered && can("records.write") ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => setAdding(true)}>
                  <Plus size={16} /> Add conversation
                </button>
              ) : undefined
            }
          />
        )}
      </Card>

      <AddConversationModal open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

// ---------------------------------------------------------------- Add

type Meeting = { id: string; title: string; start_at: string };

type LinkKey = "account" | "contact" | "opportunity" | "lead";
const LINK_LABELS: Record<LinkKey, string> = { account: "Account", contact: "Contact", opportunity: "Deal", lead: "Lead" };

async function uploadConversation(form: FormData) {
  const response = await fetch("/api/aiva/conversations/upload", { method: "POST", body: form, credentials: "same-origin" });
  const text = await response.text();
  let data: Record<string, unknown> | null = null;
  try {
    data = text ? (JSON.parse(text) as Record<string, unknown>) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : response.status === 413 ? "That file is too large." : "Upload failed.";
    throw new AivaApiError(message, response.status, data ?? {});
  }
  return data as { id: string };
}

function AddConversationModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const [mode, setMode] = useState<"paste" | "upload">("paste");
  const [title, setTitle] = useState("");
  const [when, setWhen] = useState("");
  const [meetingId, setMeetingId] = useState("");
  const [transcript, setTranscript] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [links, setLinks] = useState<Partial<Record<LinkKey, FoundRecord>>>({});
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const since = useMemo(() => new Date(Date.now() - 30 * 86400000).toISOString(), []);
  const until = useMemo(() => new Date(Date.now() + 86400000).toISOString(), []);
  const meetings = useApi<{ items: Meeting[] }>(open ? `/meetings${query({ start: since, end: until, limit: 200 })}` : null);
  const recentMeetings = [...(meetings.data?.items ?? [])].reverse();

  function reset() {
    setTitle("");
    setWhen("");
    setMeetingId("");
    setTranscript("");
    setFile(null);
    setLinks({});
    setLinking(false);
    setError(null);
    setConfirm(null);
  }

  function close() {
    reset();
    onClose();
  }

  async function save(force: boolean) {
    setSaving(true);
    setError(null);
    const ids = {
      account_id: links.account?.id ?? null,
      contact_id: links.contact?.id ?? null,
      opportunity_id: links.opportunity?.id ?? null,
      lead_id: links.lead?.id ?? null,
      meeting_id: meetingId || null,
    };
    const occurred = when ? new Date(when).toISOString() : null;
    try {
      let created: { id: string };
      if (mode === "paste") {
        created = await api.post<{ id: string }>("/conversations", { title: title.trim(), transcript_text: transcript, occurred_at: occurred, ...ids, force });
      } else {
        if (!file) throw new Error("Choose a file to upload.");
        const form = new FormData();
        form.append("file", file);
        if (title.trim()) form.append("title", title.trim());
        if (occurred) form.append("occurred_at", occurred);
        for (const [key, value] of Object.entries(ids)) if (value) form.append(key, value);
        if (force) form.append("force", "true");
        created = await uploadConversation(form);
      }
      toast("Conversation added.");
      reset();
      onClose();
      router.push(`/conversations/${created.id}`);
    } catch (caught) {
      const message = errorMessage(caught);
      if (caught instanceof AivaApiError && caught.status === 409 && /anyway\?$/.test(message)) {
        setConfirm(message.replace(/\s*Save it anyway\?$/, ""));
      } else {
        setError(message);
      }
    } finally {
      setSaving(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void save(false);
  }

  const ready = mode === "paste" ? Boolean(title.trim() && transcript.trim()) : Boolean(file);

  return (
    <Modal
      open={open}
      title="Add a conversation"
      description="AIVA reads the transcript, writes a summary, finds tracker topics and suggests CRM updates."
      onClose={close}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={close} disabled={saving}>
            Cancel
          </button>
          <button type="submit" form="conversation-form" className="ui-btn ui-btn-primary" disabled={saving || !ready}>
            {saving ? <span className="ui-spinner" /> : null}
            {mode === "upload" && file && !/\.(vtt|srt|txt)$/i.test(file.name) ? "Upload and transcribe" : "Add conversation"}
          </button>
        </>
      }
    >
      <form id="conversation-form" className="ui-stack" onSubmit={submit}>
        <Tabs
          label="How to add it"
          value={mode}
          onChange={(value) => {
            setMode(value);
            setError(null);
            setConfirm(null);
          }}
          items={[
            { value: "paste", label: "Paste a transcript" },
            { value: "upload", label: "Upload a file" },
          ]}
        />
        {error ? <Alert>{error}</Alert> : null}
        {confirm ? (
          <Alert tone="warning">
            <div className="ui-stack" style={{ gap: 8 }}>
              <span>{confirm}</span>
              <span className="ui-row" style={{ gap: 8 }}>
                <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" disabled={saving} onClick={() => void save(true)}>
                  Save it anyway
                </button>
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => setConfirm(null)}>
                  Don&apos;t save
                </button>
              </span>
            </div>
          </Alert>
        ) : null}

        <div className="ui-form-grid">
          <Field label="Title" required={mode === "paste"} hint={mode === "upload" ? "Leave blank to use the file name." : undefined}>
            <input className="ui-input" value={title} maxLength={300} onChange={(event) => setTitle(event.target.value)} placeholder="Discovery call with Brightline" />
          </Field>
          <Field label="When it happened" hint="Leave blank for now, or the meeting's start time.">
            <input className="ui-input" type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} />
          </Field>
          <Field label="Meeting" hint="Linking a meeting brings in its attendees and records." className="span-2">
            <select className="ui-select" value={meetingId} onChange={(event) => setMeetingId(event.target.value)}>
              <option value="">No meeting</option>
              {recentMeetings.map((meeting) => (
                <option key={meeting.id} value={meeting.id}>
                  {meeting.title} · {dateTime(meeting.start_at)}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="ui-stack" style={{ gap: 8 }}>
          <div className="ui-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <span className="ui-small ui-strong">Linked records</span>
            {!linking ? (
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => setLinking(true)}>
                <Plus size={14} /> Link a record
              </button>
            ) : (
              <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => setLinking(false)}>
                Done
              </button>
            )}
          </div>
          {Object.keys(links).length ? (
            <div className="conv-chips">
              {(Object.entries(links) as [LinkKey, FoundRecord][]).map(([key, record]) => (
                <span key={key} className="conv-chip on">
                  <span className="ui-muted">{LINK_LABELS[key]}:</span> {record.title}
                  <button
                    type="button"
                    className="conv-chip-x"
                    aria-label={`Unlink ${record.title}`}
                    onClick={() =>
                      setLinks((current) => {
                        const next = { ...current };
                        delete next[key];
                        return next;
                      })
                    }
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          ) : !linking ? (
            <span className="ui-muted ui-small">Link the account, deal, contact or lead so the summary shows up on their timeline.</span>
          ) : null}
          {linking ? (
            <RecordSearch
              types={["account", "contact", "opportunity", "lead"]}
              placeholder="Search accounts, deals, contacts and leads"
              onPick={(record) => {
                setLinks((current) => ({ ...current, [record.type as LinkKey]: record }));
                setLinking(false);
              }}
            />
          ) : null}
        </div>

        {mode === "paste" ? (
          <Field
            label="Transcript"
            required
            hint="One line per speaker like “Jordan: Thanks for joining”, or paste a WebVTT/SRT transcript from Zoom, Teams or Meet."
          >
            <textarea
              className="ui-textarea conv-transcript-input"
              rows={10}
              value={transcript}
              onChange={(event) => setTranscript(event.target.value)}
              placeholder={"Jordan (AIVA): Thanks for joining. What made you look at a new CRM?\nPriya: Our team keeps losing track of follow-ups…"}
            />
          </Field>
        ) : (
          <Field
            label="File"
            required
            hint="Transcripts: .vtt, .srt or .txt up to 3 MB. Recordings: .mp3, .m4a, .wav, .mp4 or .webm up to 25 MB (needs a transcription provider in Recording configuration)."
          >
            <label className="conv-drop">
              <FileUp size={20} aria-hidden />
              <span>{file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(file.size > 1024 * 1024 ? 1 : 2)} MB` : "Choose a transcript or recording"}</span>
              <input
                type="file"
                accept=".vtt,.srt,.txt,.mp3,.m4a,.wav,.mp4,.webm,.ogg,audio/*"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setError(null);
                  setConfirm(null);
                }}
              />
            </label>
          </Field>
        )}
      </form>
    </Modal>
  );
}

