"use client";

import Link from "next/link";
import {
  Activity,
  Briefcase,
  Check,
  FileText,
  Flame,
  MailOpen,
  MessageSquareReply,
  MousePointerClick,
  Radio,
  RotateCcw,
  Settings2,
  Target,
  Timer,
  X,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, LoadingRows, PageHeader, Tabs } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

type EventStatus = "new" | "actioned" | "dismissed";

type SignalEvent = {
  id: string;
  rule_id: string;
  rule_name: string;
  kind: string;
  record_type: string;
  record_id: string;
  record_label: string | null;
  href: string;
  account_id: string | null;
  account_name: string | null;
  owner_name: string | null;
  detail: string | null;
  fired_at: string;
  status: EventStatus;
};

const KIND_ICONS: Record<string, LucideIcon> = {
  page_visit: MousePointerClick,
  intent_surge: Flame,
  email_engagement: MailOpen,
  form_submitted: FileText,
  deal_stalled: Timer,
  job_change: Briefcase,
  sequence_reply: MessageSquareReply,
  persona_match: Target,
};

const RECORD_LABELS: Record<string, string> = { lead: "Lead", contact: "Contact", account: "Account", opportunity: "Deal" };

export default function SignalsPage() {
  return (
    <FeaturePage feature="buying_intent">
      <Signals />
    </FeaturePage>
  );
}

function Signals() {
  const toast = useToast();
  const { can } = useSession();
  const [status, setStatus] = useState<EventStatus | "all">("new");
  const [ruleId, setRuleId] = useState("");
  const [mine, setMine] = useState(false);
  const events = useApi<{ items: SignalEvent[]; counts: Partial<Record<EventStatus, number>> }>(
    `/signals/events${query({ status, rule_id: ruleId || null, mine: mine || null })}`,
  );
  const rules = useApi<{ items: { id: string; name: string; is_active: boolean }[]; can_edit: boolean }>("/signals/rules");
  const [busy, setBusy] = useState<string | null>(null);
  const counts = events.data?.counts ?? {};
  const items = events.data?.items ?? [];
  const viewAll = can("records.view_all");

  async function act(event: SignalEvent, next: EventStatus) {
    setBusy(event.id);
    try {
      await api.post(`/signals/events/${event.id}`, { status: next });
      toast(next === "actioned" ? "Marked as done." : next === "dismissed" ? "Dismissed." : "Moved back to new.");
      await events.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const noRules = rules.data && !rules.data.items.length;

  return (
    <>
      <PageHeader
        title="Signals"
        description="Moments worth acting on, newest first: pricing-page visits, buying-intent surges, replies, stalled deals and more."
        actions={
          rules.data?.can_edit ? (
            <Link className="ui-btn" href="/settings/signals">
              <Settings2 size={15} /> Set up signals
            </Link>
          ) : null
        }
      />
      <div className="ui-toolbar" style={{ marginBottom: 14 }}>
        <Tabs
          label="Signal status"
          value={status}
          onChange={setStatus}
          items={[
            { value: "new", label: "New", count: counts.new ?? 0, hot: true },
            { value: "actioned", label: "Done", count: counts.actioned ?? 0 },
            { value: "dismissed", label: "Dismissed", count: counts.dismissed ?? 0 },
            { value: "all", label: "All" },
          ]}
        />
        <span className="ui-spacer" />
        <select className="ui-select sm" value={ruleId} onChange={(event) => setRuleId(event.target.value)} aria-label="Signal" style={{ maxWidth: 220 }}>
          <option value="">All signals</option>
          {(rules.data?.items ?? []).map((rule) => (
            <option key={rule.id} value={rule.id}>
              {rule.name}
            </option>
          ))}
        </select>
        {viewAll ? (
          <label className="ui-check">
            <input type="checkbox" checked={mine} onChange={(event) => setMine(event.target.checked)} />
            <span>Only mine</span>
          </label>
        ) : null}
      </div>
      {events.error ? <Alert>{events.error}</Alert> : null}
      <Card flush>
        {events.loading && !events.data ? (
          <LoadingRows />
        ) : items.length ? (
          <ul className="dm-feed">
            {items.map((event) => {
              const Icon = KIND_ICONS[event.kind] ?? Activity;
              return (
                <li key={event.id} className={`dm-feed-item${event.status !== "new" ? " done" : ""}`}>
                  <span className={`dm-feed-icon kind-${event.kind}`} aria-hidden>
                    <Icon size={16} />
                  </span>
                  <div className="dm-feed-body">
                    <div className="dm-feed-top">
                      <Link href={event.href} className="cell-title">
                        {event.record_label ?? RECORD_LABELS[event.record_type] ?? "Record"}
                      </Link>
                      <Badge>{RECORD_LABELS[event.record_type] ?? event.record_type}</Badge>
                      {event.account_name && event.record_type !== "account" && event.account_id ? (
                        <Link href={`/accounts/${event.account_id}`} className="ui-muted ui-small">
                          {event.account_name}
                        </Link>
                      ) : null}
                    </div>
                    <div className="ui-small">{event.detail ?? event.rule_name}</div>
                    <div className="ui-muted ui-small">
                      {event.rule_name} · <span title={dateTime(event.fired_at)}>{relativeTime(event.fired_at)}</span>
                      {event.owner_name ? ` · ${event.owner_name}` : ""}
                      {event.status === "actioned" ? " · Done" : event.status === "dismissed" ? " · Dismissed" : ""}
                    </div>
                  </div>
                  <div className="dm-feed-actions">
                    {event.status === "new" ? (
                      <>
                        <button type="button" className="ui-btn ui-btn-sm" onClick={() => void act(event, "actioned")} disabled={busy === event.id}>
                          <Check size={14} /> Done
                        </button>
                        <button
                          type="button"
                          className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                          aria-label="Dismiss"
                          title="Dismiss"
                          onClick={() => void act(event, "dismissed")}
                          disabled={busy === event.id}
                        >
                          <X size={14} />
                        </button>
                      </>
                    ) : (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void act(event, "new")} disabled={busy === event.id}>
                        <RotateCcw size={14} /> Reopen
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <EmptyState
            icon={<Radio size={20} />}
            title={status === "new" ? "You're all caught up" : "Nothing here"}
            message={
              noRules ? (
                <>
                  No signals are set up yet. <Link href="/settings/signals">Create your first one</Link> — for example, a visit to your pricing page.
                </>
              ) : (
                "New signals appear here as AIVA spots them (it checks every few minutes)."
              )
            }
          />
        )}
      </Card>
    </>
  );
}
