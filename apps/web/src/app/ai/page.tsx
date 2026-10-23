"use client";

import Link from "next/link";
import { Bot, Check, MessageCircleQuestion, Play, Send, Sparkles, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { FeaturePage, UpgradeGate } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, LoadingRows, PageHeader, Tabs } from "@/components/ui";
import { relativeTime, titleCase } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

interface Agent {
  key: string;
  name: string;
  description: string;
  enabled: boolean;
  last_run_at: string | null;
  open_suggestions: number;
  accepted: number;
}

interface Suggestion {
  id: string;
  agent: string;
  agent_name: string;
  record_type: string;
  record_id: string;
  record_label: string;
  link: string;
  title: string;
  body: string | null;
  action: { type: string; [key: string]: unknown };
  confidence: number;
  status: "open" | "accepted" | "dismissed";
  source: string;
  owner_name: string | null;
  created_at: string;
  resolved_at: string | null;
}

const ACTION_LABEL: Record<string, string> = {
  update_field: "Apply",
  create_task: "Create task",
  enrich: "Fill in details",
  link_article: "Link article",
  open: "Open record",
};

export default function AiPage() {
  return (
    <FeaturePage feature="ai_agents" permission="automation.ai" what="AI agents">
      <AiView />
    </FeaturePage>
  );
}

function AiView() {
  const [view, setView] = useState<"inbox" | "agents" | "ask">("inbox");
  const agents = useApi<{ claude: boolean; agents: Agent[] }>("/ai/agents");
  const openCount = agents.data?.agents.reduce((sum, agent) => sum + agent.open_suggestions, 0);

  return (
    <>
      <PageHeader
        title="AI agents"
        description="Agents work through your CRM on a schedule and leave suggestions here — qualify a lead, move a slipped close date, triage a case. Nothing changes until you accept."
      />
      {agents.data && !agents.data.claude ? (
        <div style={{ marginBottom: 14 }}>
          <Alert tone="info">
            Agents are running on AIVA&apos;s built-in rules. Add an <code>ANTHROPIC_API_KEY</code> on the server to have Claude write summaries, drafts and answers.
          </Alert>
        </div>
      ) : null}
      <div style={{ marginBottom: 14 }}>
        <Tabs
          label="AI"
          value={view}
          onChange={setView}
          items={[
            { value: "inbox", label: "Suggestions", count: openCount },
            { value: "agents", label: "Agents" },
            { value: "ask", label: "Ask AIVA" },
          ]}
        />
      </div>
      {view === "inbox" ? <Inbox agents={agents.data?.agents ?? []} onChanged={() => void agents.reload()} /> : null}
      {view === "agents" ? <Agents agents={agents.data?.agents ?? null} error={agents.error} onChanged={() => void agents.reload()} /> : null}
      {view === "ask" ? (
        <UpgradeGate feature="extended_ai">
          <AskAiva />
        </UpgradeGate>
      ) : null}
    </>
  );
}

function Inbox({ agents, onChanged }: { agents: Agent[]; onChanged: () => void }) {
  const toast = useToast();
  const [status, setStatus] = useState<"open" | "accepted" | "dismissed">("open");
  const [agent, setAgent] = useState("");
  const [mine, setMine] = useState(false);
  const suggestions = useApi<Suggestion[]>(`/ai/suggestions${query({ status, agent: agent || undefined, mine: mine || undefined })}`);
  const [busy, setBusy] = useState<string | null>(null);

  async function resolve(row: Suggestion, accept: boolean) {
    setBusy(row.id);
    try {
      const result = await api.post<{ message: string }>(`/ai/suggestions/${row.id}/${accept ? "accept" : "dismiss"}`);
      toast(result.message);
      suggestions.setData((current) => (current ?? []).filter((item) => item.id !== row.id));
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      flush
      title={
        <Tabs
          label="Status"
          value={status}
          onChange={setStatus}
          items={[
            { value: "open", label: "Open" },
            { value: "accepted", label: "Accepted" },
            { value: "dismissed", label: "Dismissed" },
          ]}
        />
      }
      actions={
        <div className="ui-row" style={{ gap: 10 }}>
          <label className="ui-check ui-small">
            <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Mine
          </label>
          <select className="ui-select sm" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.key} value={a.key}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
      }
    >
      {suggestions.error ? <Alert>{suggestions.error}</Alert> : null}
      {suggestions.loading && !suggestions.data ? (
        <LoadingRows />
      ) : suggestions.data?.length ? (
        <ul className="suggestion-list">
          {suggestions.data.map((row) => (
            <li key={row.id} className="suggestion">
              <span className="suggestion-icon">
                <Sparkles size={15} />
              </span>
              <div className="suggestion-body">
                <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <strong>{row.title}</strong>
                  <Badge tone={row.confidence >= 75 ? "success" : row.confidence >= 55 ? "info" : "neutral"}>{row.confidence}% sure</Badge>
                </div>
                <div className="ui-small ui-muted">
                  {row.agent_name} · <Link href={row.link}>{row.record_label}</Link> ({titleCase(row.record_type)}) · {relativeTime(row.created_at)}
                  {row.owner_name ? ` · for ${row.owner_name}` : ""}
                  {row.source === "claude" ? " · written by Claude" : ""}
                </div>
                {row.body ? <p className="suggestion-text">{row.body}</p> : null}
              </div>
              {row.status === "open" ? (
                <div className="suggestion-actions">
                  <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy === row.id} onClick={() => void resolve(row, false)}>
                    <X size={14} /> Dismiss
                  </button>
                  {row.action.type === "open" ? (
                    <Link className="ui-btn ui-btn-sm" href={row.link}>
                      Review
                    </Link>
                  ) : (
                    <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy === row.id} onClick={() => void resolve(row, true)}>
                      <Check size={14} /> {ACTION_LABEL[row.action.type] ?? "Accept"}
                    </button>
                  )}
                </div>
              ) : (
                <Badge tone={row.status === "accepted" ? "success" : "neutral"}>{row.status}</Badge>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={<Bot size={20} />}
          title={status === "open" ? "No open suggestions" : `Nothing ${status} yet`}
          message={status === "open" ? "Agents run every few hours. Run one now from the Agents tab to see what it finds." : undefined}
        />
      )}
    </Card>
  );
}

function Agents({ agents, error, onChanged }: { agents: Agent[] | null; error: string | null; onChanged: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const [busy, setBusy] = useState<string | null>(null);
  const manage = can("automation.workflows");

  async function toggle(agent: Agent) {
    setBusy(agent.key);
    try {
      await api.put(`/ai/agents/${agent.key}`, { enabled: !agent.enabled });
      toast(`${agent.name} turned ${agent.enabled ? "off" : "on"}.`);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function run(agent: Agent) {
    setBusy(agent.key);
    try {
      const result = await api.post<{ message: string }>(`/ai/agents/${agent.key}/run`);
      toast(result.message);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!agents) {
    return (
      <Card>
        <LoadingRows />
      </Card>
    );
  }
  return (
    <div className="ui-grid ui-grid-3">
      {agents.map((agent) => (
        <section key={agent.key} className="ui-card agent-card">
          <div className="ui-row" style={{ gap: 10 }}>
            <span className="type-icon">
              <Bot size={16} />
            </span>
            <strong style={{ flex: 1 }}>{agent.name}</strong>
            <label className="ui-switch" title={agent.enabled ? "On" : "Off"}>
              <input type="checkbox" checked={agent.enabled} disabled={!manage || busy === agent.key} onChange={() => void toggle(agent)} aria-label={`${agent.name} on`} />
              <span />
            </label>
          </div>
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            {agent.description}
          </p>
          <div className="stat-strip" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
            <div className="billing-stat">
              <span>Open suggestions</span>
              <strong>{agent.open_suggestions}</strong>
            </div>
            <div className="billing-stat">
              <span>Accepted</span>
              <strong>{agent.accepted}</strong>
            </div>
          </div>
          <div className="ui-row" style={{ gap: 8 }}>
            <span className="ui-muted ui-small" style={{ flex: 1 }}>
              {agent.last_run_at ? `Last ran ${relativeTime(agent.last_run_at)}` : "Hasn't run yet"}
            </span>
            <button type="button" className="ui-btn ui-btn-sm" disabled={busy === agent.key} onClick={() => void run(agent)}>
              <Play size={13} /> Run now
            </button>
          </div>
        </section>
      ))}
    </div>
  );
}

const STARTERS = ["How is pipeline looking this quarter?", "Which deals are at risk?", "Who are the top reps by won revenue?", "How many overdue tasks do we have?", "What's happening with open cases?"];

function AskAiva() {
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState<{ q: string; a: string; source: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(text: string) {
    const q = text.trim();
    if (q.length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ answer: string; source: string }>("/ai/ask", { question: q });
      setThread((current) => [...current, { q, a: result.answer, source: result.source }]);
      setQuestion("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  return (
    <Card title="Ask AIVA" icon={<MessageCircleQuestion size={16} />}>
      <div className="ask-thread">
        {thread.length === 0 ? (
          <div className="ask-starters">
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Ask about your pipeline, revenue, reps, risky deals, tasks, cases or leads.
            </p>
            <div className="chip-list">
              {STARTERS.map((starter) => (
                <button key={starter} type="button" className="chip" onClick={() => void ask(starter)} disabled={busy}>
                  {starter}
                </button>
              ))}
            </div>
          </div>
        ) : (
          thread.map((item, index) => (
            <div key={index} className="ask-turn">
              <div className="ask-q">{item.q}</div>
              <div className="ask-a">
                <Sparkles size={14} />
                <div>
                  <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{item.a}</p>
                  <span className="ui-muted ui-small">{item.source === "claude" ? "Answered by Claude from your CRM data" : "Answered from your CRM data"}</span>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
      {error ? <Alert>{error}</Alert> : null}
      <form className="ask-form" onSubmit={submit}>
        <input className="ui-input" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask a question about your CRM" aria-label="Question" maxLength={500} />
        <button type="submit" className="ui-btn ui-btn-primary" disabled={busy || question.trim().length < 2}>
          <Send size={15} /> Ask
        </button>
      </form>
    </Card>
  );
}
