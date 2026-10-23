"use client";

import Link from "next/link";
import { CalendarClock, Download, FileText, FolderKanban, Globe, Paperclip, Plus, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { MeetingModal, type MeetingDefaults } from "@/components/meeting-modal";
import { useSession, useToast } from "@/components/providers";
import { Badge, Card } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import {
  MEETING_STATUS,
  fileKind,
  fileSize,
  seconds,
  uploadFile,
  type DocumentItem,
  type Meeting,
  type VisitSession,
} from "@/lib/workspace";

/** Files linked to a record, with upload. */
export function RecordDocuments({ recordType, recordId }: { recordType: string; recordId: string }) {
  const { has, can } = useSession();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const docs = useApi<{ items: DocumentItem[]; max_mb: number }>(has("documents") ? `/documents?record_type=${recordType}&record_id=${recordId}` : null);
  if (!has("documents")) return null;

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        form.append("record_type", recordType);
        form.append("record_id", recordId);
        await uploadFile("/documents", form);
      }
      toast(files.length === 1 ? `Uploaded ${files[0].name}.` : `Uploaded ${files.length} files.`);
      await docs.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  const items = docs.data?.items ?? [];
  return (
    <Card
      title="Documents"
      icon={<Paperclip size={16} />}
      actions={
        can("records.write") ? (
          <>
            <input ref={input} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} aria-label="Upload files" />
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => input.current?.click()} disabled={busy}>
              {busy ? <span className="ui-spinner" /> : <Upload size={14} />} Upload
            </button>
          </>
        ) : null
      }
    >
      {items.length ? (
        <ul className="mini-list">
          {items.map((doc) => (
            <li key={doc.id}>
              <FileText size={16} className="mini-icon" />
              <div className="mini-main">
                <Link href={`/documents/${doc.id}`} className="mini-title">
                  {doc.name}
                </Link>
                <span className="mini-sub">
                  {fileKind(doc.content_type, doc.name)} · {fileSize(doc.size_bytes)}
                  {doc.version > 1 ? ` · v${doc.version}` : ""} · {relativeTime(doc.updated_at)}
                </span>
              </div>
              <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={`/api/aiva/documents/${doc.id}/download`} aria-label={`Download ${doc.name}`}>
                <Download size={14} />
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          {docs.loading ? "Loading…" : `No files yet. Upload contracts, proposals or specs (up to ${docs.data?.max_mb ?? 25} MB each).`}
        </p>
      )}
    </Card>
  );
}

/** Upcoming and past meetings for a record, with "Schedule". */
export function RecordMeetings({
  recordType,
  recordId,
  defaults,
}: {
  recordType: "account" | "contact" | "lead" | "opportunity";
  recordId: string;
  defaults: MeetingDefaults;
}) {
  const { has, can } = useSession();
  const [open, setOpen] = useState(false);
  const meetings = useApi<{ items: Meeting[] }>(has("tasks_meetings_calls") ? `/meetings?record_type=${recordType}&record_id=${recordId}` : null);
  if (!has("tasks_meetings_calls")) return null;
  const now = Date.now();
  const items = meetings.data?.items ?? [];
  const upcoming = items.filter((m) => m.status === "scheduled" && new Date(m.end_at).getTime() >= now);
  const past = items.filter((m) => !upcoming.includes(m)).reverse().slice(0, 5);

  return (
    <Card
      title="Meetings"
      icon={<CalendarClock size={16} />}
      actions={
        can("records.write") ? (
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setOpen(true)}>
            <Plus size={14} /> Schedule
          </button>
        ) : null
      }
    >
      {items.length ? (
        <ul className="mini-list">
          {[...upcoming, ...past].map((meeting) => (
            <li key={meeting.id}>
              <CalendarClock size={16} className="mini-icon" />
              <div className="mini-main">
                <Link href={`/meetings?open=${meeting.id}`} className="mini-title">
                  {meeting.title}
                </Link>
                <span className="mini-sub">
                  {dateTime(meeting.start_at)} · {meeting.organizer_name ?? "—"}
                </span>
              </div>
              <Badge tone={MEETING_STATUS[meeting.status].tone}>{upcoming.includes(meeting) ? "Upcoming" : MEETING_STATUS[meeting.status].label}</Badge>
            </li>
          ))}
        </ul>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          {meetings.loading ? "Loading…" : "No meetings yet."}
        </p>
      )}
      <MeetingModal open={open} onClose={() => setOpen(false)} onSaved={() => void meetings.reload()} defaults={defaults} />
    </Card>
  );
}

interface RecordVisitsData {
  visitor_count: number;
  visit_count: number;
  page_view_count: number;
  total_seconds: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  sessions: VisitSession[];
}

/** Website visits by a lead or contact. */
export function RecordVisits({ recordType, recordId }: { recordType: "lead" | "contact"; recordId: string }) {
  const { has } = useSession();
  const data = useApi<RecordVisitsData>(has("website_visits") ? `/visits/record/${recordType}/${recordId}` : null);
  if (!has("website_visits") || !data.data || !data.data.visitor_count) return null;
  const d = data.data;
  return (
    <Card title="Website visits" icon={<Globe size={16} />}>
      <div className="visit-stats">
        <span>
          <strong>{d.visit_count}</strong> visits
        </span>
        <span>
          <strong>{d.page_view_count}</strong> pages
        </span>
        <span>
          <strong>{seconds(d.total_seconds)}</strong> on site
        </span>
      </div>
      <p className="ui-muted ui-small" style={{ margin: "6px 0 10px" }}>
        Last seen {relativeTime(d.last_seen_at)}
      </p>
      <ul className="mini-list">
        {d.sessions.slice(0, 4).map((session) => (
          <li key={session.id} style={{ alignItems: "flex-start" }}>
            <Globe size={16} className="mini-icon" />
            <div className="mini-main">
              <span className="mini-title">
                {dateTime(session.started_at)} · from {session.source}
              </span>
              <span className="mini-sub">{session.pages.map((p) => p.title || p.path).slice(0, 4).join(" → ")}</span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** "Start project" on a won deal, or a link to its project. */
export function DealProjectButton({ opportunityId }: { opportunityId: string }) {
  const { has, can } = useSession();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const projects = useApi<{ items: { id: string; display_number: string; status: string }[] }>(has("projects") ? `/projects?opportunity_id=${opportunityId}` : null);
  if (!has("projects")) return null;
  const existing = projects.data?.items.find((p) => p.status !== "cancelled");
  if (existing) {
    return (
      <Link className="ui-btn" href={`/projects/${existing.id}`}>
        <FolderKanban size={15} /> {existing.display_number}
      </Link>
    );
  }
  if (!can("projects.manage") || !projects.data) return null;
  async function start() {
    setBusy(true);
    try {
      const project = await api.post<{ id: string; display_number: string }>(`/projects/from-opportunity/${opportunityId}`);
      toast(`Started ${project.display_number}.`);
      window.location.assign(`/projects/${project.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }
  return (
    <button type="button" className="ui-btn" onClick={() => void start()} disabled={busy}>
      <FolderKanban size={15} /> Start project
    </button>
  );
}
