"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, BookOpen, CheckCircle2, Link2, Lock, MessageSquare, Sparkles, Star, Trash2, Unlink } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AppShell } from "@/components/app-shell";
import { CASE_STATUS_LABEL, CHANNEL_LABEL, SlaBadge } from "@/components/case-bits";
import { useSession, useToast } from "@/components/providers";
import { RecordDocuments } from "@/components/record-panels";
import { RecordCloudFiles } from "@/components/integrations/record/RecordCloudFiles";
import { PinButton } from "@/components/pins";
import { RecordActions, RecordInsights } from "@/components/record-extras";
import { Alert, Avatar, Badge, Card, CASE_STATUS_TONE, ConfirmDialog, LoadingRows, PageHeader, PRIORITY_TONE } from "@/components/ui";
import { dateTime, duration, relativeTime } from "@/lib/format";
import type { CaseDetail, Member, Team } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Suggestion = { id: string; title: string; summary: string | null; category: string | null; linked: boolean };

export default function CaseDetailPage() {
  return (
    <AppShell permission="service.cases" what="cases">
      <CaseView />
    </AppShell>
  );
}

function CaseView() {
  const { caseId } = useParams<{ caseId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can, has, session } = useSession();
  const detail = useApi<CaseDetail>(`/cases/${caseId}`);
  const [extrasVersion, setExtrasVersion] = useState(0);
  const [drafting, setDrafting] = useState(false);
  const suggestions = useApi<Suggestion[]>(`/cases/${caseId}/suggested-articles`);
  const members = useApi<Member[]>("/meta/members");
  const teams = useApi<Team[]>("/admin/teams");
  const [reply, setReply] = useState("");
  const [internal, setInternal] = useState(false);
  const [sending, setSending] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const c = detail.data;
  const editable = can("records.write");

  async function update(changes: Record<string, unknown>, message?: string) {
    try {
      const result = await api.patch<CaseDetail>(`/cases/${caseId}`, changes);
      detail.setData(result);
      if (message) toast(message);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function send(event: FormEvent<HTMLFormElement>, resolve = false) {
    event.preventDefault();
    if (!reply.trim()) return;
    setSending(true);
    try {
      let result = await api.post<CaseDetail>(`/cases/${caseId}/comments`, { body: reply.trim(), is_internal: internal });
      if (resolve) result = await api.patch<CaseDetail>(`/cases/${caseId}`, { status: "resolved" });
      detail.setData(result);
      setReply("");
      toast(internal ? "Internal note added." : resolve ? "Reply sent and case resolved." : "Reply sent.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSending(false);
    }
  }

  async function draftReply() {
    setDrafting(true);
    try {
      const draft = await api.post<{ body: string; source: string }>("/ai/draft", {
        object_type: "case",
        record_id: caseId,
        intent: "reply to the customer about their support request",
        tone: "friendly",
      });
      setInternal(false);
      setReply(draft.body);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setDrafting(false);
    }
  }

  async function toggleArticle(article: Suggestion | { id: string; linked: true }) {
    try {
      if (article.linked) await api.del(`/cases/${caseId}/articles/${article.id}`);
      else await api.post(`/cases/${caseId}/articles`, { article_id: article.id });
      await Promise.all([detail.reload(), suggestions.reload()]);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    try {
      await api.del(`/cases/${caseId}`);
      toast("Case deleted.");
      router.replace("/cases");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  if (detail.error) return <Alert>{detail.error}</Alert>;
  if (!c) return <div className="ui-card"><LoadingRows rows={10} /></div>;

  const closed = c.status === "resolved" || c.status === "closed";

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/cases" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> Cases
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <span className="ui-row" style={{ gap: 8, display: "inline-flex" }}>
            {c.display_number} <Badge tone={PRIORITY_TONE[c.priority]}>{c.priority}</Badge>
            <Badge tone={CASE_STATUS_TONE[c.status]}>{CASE_STATUS_LABEL[c.status]}</Badge>
          </span>
        }
        title={c.subject}
        description={
          <>
            Opened {relativeTime(c.created_at)} by {c.created_by_name ?? "someone"} via {CHANNEL_LABEL[c.channel] ?? c.channel}
            {c.account_name ? ` · ${c.account_name}` : ""}
          </>
        }
        actions={
          <>
          <RecordActions
            objectType="case"
            recordId={c.id}
            onChanged={() => {
              setExtrasVersion((v) => v + 1);
              void detail.reload();
            }}
          />
          <PinButton type="case" id={c.id} />
          {editable ? (
            closed ? (
              <button type="button" className="ui-btn" onClick={() => void update({ status: "open" }, "Case reopened.")}>
                Reopen
              </button>
            ) : (
              <>
                {!c.assigned_to_user_id && session ? (
                  <button type="button" className="ui-btn" onClick={() => void update({ assigned_to_user_id: session.user.id }, "Assigned to you.")}>
                    Assign to me
                  </button>
                ) : null}
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => void update({ status: "resolved" }, "Case resolved.")}>
                  <CheckCircle2 size={15} /> Resolve
                </button>
              </>
            )
          ) : null}
          </>
        }
      />

      <div className="ui-split">
        <div className="ui-stack">
          <Card title="Conversation" icon={<MessageSquare size={16} />}>
            <div className="ui-thread">
              <div className="ui-message">
                <Avatar name={c.contact_name ?? c.account_name ?? "Customer"} size={32} />
                <div className="bubble">
                  <div className="meta">
                    <strong>{c.contact_name ?? "Customer"}</strong>
                    <span>{dateTime(c.created_at)}</span>
                  </div>
                  <div className="text">{c.description || "No description provided."}</div>
                </div>
              </div>
              {c.comments.map((comment) => (
                <div key={comment.id} className={`ui-message${comment.is_internal ? " internal" : ""}`}>
                  <Avatar name={comment.author_name} size={32} />
                  <div className="bubble">
                    <div className="meta">
                      <strong>{comment.author_name ?? "Teammate"}</strong>
                      {comment.is_internal ? (
                        <Badge tone="warning">
                          <Lock size={11} /> Internal note
                        </Badge>
                      ) : null}
                      <span>{dateTime(comment.created_at)}</span>
                    </div>
                    <div className="text">{comment.body}</div>
                  </div>
                </div>
              ))}
            </div>

            {editable ? (
              <form onSubmit={(event) => void send(event)} style={{ marginTop: 18, display: "grid", gap: 10 }}>
                <div className="ui-tabs" role="tablist" aria-label="Reply type">
                  <button type="button" role="tab" className="ui-tab" aria-selected={!internal} onClick={() => setInternal(false)}>
                    Reply to customer
                  </button>
                  <button type="button" role="tab" className="ui-tab" aria-selected={internal} onClick={() => setInternal(true)}>
                    <Lock size={12} /> Internal note
                  </button>
                </div>
                <textarea
                  className="ui-textarea"
                  rows={4}
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder={internal ? "Only teammates see internal notes." : "Write a reply… The first reply stops the response SLA clock."}
                  style={internal ? { background: "var(--warning-soft)", borderColor: "var(--border)" } : undefined}
                  aria-label="Message"
                />
                <div className="ui-actions" style={{ justifyContent: "flex-end" }}>
                  {has("extended_ai") && !internal ? (
                    <button type="button" className="ui-btn ui-btn-ghost" style={{ marginRight: "auto" }} disabled={drafting} onClick={() => void draftReply()}>
                      <Sparkles size={14} /> {drafting ? "Drafting…" : "Draft a reply"}
                    </button>
                  ) : null}
                  {!internal && !closed ? (
                    <button
                      type="button"
                      className="ui-btn"
                      disabled={sending || !reply.trim()}
                      onClick={(event) => void send(event as unknown as FormEvent<HTMLFormElement>, true)}
                    >
                      Send and resolve
                    </button>
                  ) : null}
                  <button type="submit" className="ui-btn ui-btn-primary" disabled={sending || !reply.trim()}>
                    {sending ? <span className="ui-spinner" /> : null}
                    {internal ? "Add note" : "Send reply"}
                  </button>
                </div>
              </form>
            ) : null}
          </Card>

          <Card title="Knowledge" icon={<BookOpen size={16} />} flush>
            <div className="ui-list">
              {c.linked_articles.map((article) => (
                <div key={article.id} className="ui-list-item">
                  <Link2 size={15} color="var(--success)" />
                  <div className="main">
                    <Link className="title" href={`/knowledge/${article.id}`} style={{ color: "inherit", textDecoration: "none", display: "block" }}>
                      {article.title}
                    </Link>
                    <div className="sub">Linked to this case</div>
                  </div>
                  {editable ? (
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void toggleArticle({ id: article.id, linked: true })}>
                      <Unlink size={14} /> Unlink
                    </button>
                  ) : null}
                </div>
              ))}
              {(suggestions.data ?? [])
                .filter((item) => !item.linked)
                .map((article) => (
                  <div key={article.id} className="ui-list-item">
                    <BookOpen size={15} color="var(--muted)" />
                    <div className="main">
                      <Link className="title" href={`/knowledge/${article.id}`} style={{ color: "inherit", textDecoration: "none", display: "block" }}>
                        {article.title}
                      </Link>
                      <div className="sub">Suggested{article.summary ? ` · ${article.summary}` : ""}</div>
                    </div>
                    {editable ? (
                      <button type="button" className="ui-btn ui-btn-sm" onClick={() => void toggleArticle(article)}>
                        <Link2 size={14} /> Link
                      </button>
                    ) : null}
                  </div>
                ))}
              {!c.linked_articles.length && !(suggestions.data ?? []).some((item) => !item.linked) ? (
                <div className="ui-list-item ui-muted ui-small">No matching articles. Write one once this is solved so the next agent can reuse it.</div>
              ) : null}
            </div>
          </Card>
        </div>

        <div className="ui-stack">
          <Card title="Service level">
            <div className="ui-stack" style={{ gap: 12 }}>
              <SlaBadge sla={c.sla} />
              <dl className="ui-dl">
                <dt>Policy</dt>
                <dd>{c.sla_policy ? `${c.sla_policy.name} (${duration(c.sla_policy.first_response_minutes)} / ${duration(c.sla_policy.resolution_minutes)})` : "None"}</dd>
                <dt>Respond by</dt>
                <dd>
                  {dateTime(c.first_response_due_at)}
                  {c.first_responded_at ? <div className="ui-muted ui-small">Responded {dateTime(c.first_responded_at)}</div> : null}
                </dd>
                <dt>Resolve by</dt>
                <dd>
                  {dateTime(c.resolution_due_at)}
                  {c.resolved_at ? <div className="ui-muted ui-small">Resolved {dateTime(c.resolved_at)}</div> : null}
                </dd>
              </dl>
            </div>
          </Card>

          <Card title="Properties">
            <div className="ui-stack" style={{ gap: 12 }}>
              <label className="ui-field">
                <span className="ui-label">Status</span>
                <select className="ui-select sm" value={c.status} disabled={!editable} onChange={(e) => void update({ status: e.target.value }, "Status updated.")}>
                  {Object.entries(CASE_STATUS_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="ui-field">
                <span className="ui-label">Priority</span>
                <select
                  className="ui-select sm"
                  value={c.priority}
                  disabled={!editable}
                  onChange={(e) => void update({ priority: e.target.value }, "Priority updated — SLA targets recalculated.")}
                >
                  {["urgent", "high", "medium", "low"].map((value) => (
                    <option key={value} value={value}>
                      {value[0].toUpperCase() + value.slice(1)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="ui-field">
                <span className="ui-label">Assigned to</span>
                <select
                  className="ui-select sm"
                  value={c.assigned_to_user_id ?? ""}
                  disabled={!editable}
                  onChange={(e) => void update({ assigned_to_user_id: e.target.value || null }, "Assignment updated.")}
                >
                  <option value="">Unassigned</option>
                  {(members.data ?? []).map((member) => (
                    <option key={member.id} value={member.id}>
                      {member.full_name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="ui-field">
                <span className="ui-label">Team queue</span>
                <select className="ui-select sm" value={c.team_id ?? ""} disabled={!editable} onChange={(e) => void update({ team_id: e.target.value || null }, "Queue updated.")}>
                  <option value="">No team</option>
                  {(teams.data ?? []).map((team) => (
                    <option key={team.id} value={team.id}>
                      {team.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </Card>

          <Card title="Customer">
            <dl className="ui-dl">
              <dt>Account</dt>
              <dd>{c.account_id ? <Link href={`/accounts/${c.account_id}`}>{c.account_name}</Link> : "—"}</dd>
              <dt>Contact</dt>
              <dd>{c.contact_id ? <Link href={`/contacts/${c.contact_id}`}>{c.contact_name}</Link> : "—"}</dd>
              <dt>Category</dt>
              <dd>{c.category ?? "—"}</dd>
              <dt>Channel</dt>
              <dd>{CHANNEL_LABEL[c.channel] ?? c.channel}</dd>
            </dl>
          </Card>

          {closed ? (
            <Card title="Customer satisfaction">
              <div className="ui-row" role="radiogroup" aria-label="Satisfaction rating">
                {[1, 2, 3, 4, 5].map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={c.satisfaction_rating === value}
                    aria-label={`${value} star${value > 1 ? "s" : ""}`}
                    className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                    disabled={!editable}
                    onClick={() => void update({ satisfaction_rating: value }, "Rating recorded.")}
                  >
                    <Star size={18} fill={(c.satisfaction_rating ?? 0) >= value ? "#eda100" : "none"} color="#eda100" />
                  </button>
                ))}
              </div>
              <div className="ui-muted ui-small" style={{ marginTop: 6 }}>
                Record the customer&apos;s rating from the follow-up survey.
              </div>
            </Card>
          ) : null}

          <RecordInsights objectType="case" recordId={c.id} version={extrasVersion} onChanged={() => setExtrasVersion((v) => v + 1)} />
          <RecordDocuments recordType="case" recordId={c.id} />
          <RecordCloudFiles recordType="case" recordId={c.id} />

          {can("records.delete") ? (
            <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDeleting(true)}>
              <Trash2 size={15} /> Delete case
            </button>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={deleting}
        title={`Delete ${c.display_number}?`}
        message="The case and its conversation are removed permanently. Resolving it keeps the history instead."
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}
