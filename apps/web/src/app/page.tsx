"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  FilePenLine,
  Flame,
  Handshake,
  History,
  ListChecks,
  Mail,
  MessageSquare,
  Phone,
  Users,
} from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { HBarList } from "@/components/charts";
import { useSession } from "@/components/providers";
import { Badge, Card, EmptyState, Kpi, LoadingRows, PageHeader, PRIORITY_TONE, Alert } from "@/components/ui";
import { compactMoney, greeting, number, percent, relativeTime, shortDate, todayLabel } from "@/lib/format";
import type { HomeSummary } from "@/lib/platform-types";
import { useApi } from "@/lib/use-api";

const ACTIVITY_ICONS: Record<string, typeof Mail> = {
  email: Mail,
  call: Phone,
  meeting: Users,
  note: MessageSquare,
};

function scoreTone(score: number) {
  if (score >= 70) return "success" as const;
  if (score >= 40) return "warning" as const;
  return "neutral" as const;
}

export default function HomePage() {
  return (
    <AppShell>
      <HomeContent />
    </AppShell>
  );
}

function HomeContent() {
  const { session, can } = useSession();
  const { data, error, loading } = useApi<HomeSummary>("/home/summary");
  const firstName = session?.user.full_name.split(" ")[0] ?? "";
  const k = data?.kpis;

  const attention = k
    ? [
        k.sla_breaches && can("service.cases")
          ? {
              tone: "danger",
              title: `${number(k.sla_breaches)} case${k.sla_breaches === 1 ? "" : "s"} past SLA`,
              body: "Customers are waiting longer than promised. Work these first.",
              href: "/cases?view=breached",
            }
          : null,
        k.my_overdue_tasks
          ? {
              tone: "warning",
              title: `${number(k.my_overdue_tasks)} of your tasks are overdue`,
              body: `${number(k.overdue_tasks)} overdue across the team.`,
              href: "/tasks",
            }
          : null,
        data?.closing_soon.length
          ? {
              tone: "primary",
              title: `${data.closing_soon.length} deals close in the next 30 days`,
              body: `${compactMoney(data.closing_soon.reduce((sum, deal) => sum + deal.amount, 0))} expected — confirm next steps.`,
              href: "/opportunities",
            }
          : null,
        k.quotes_out
          ? {
              tone: "success",
              title: `${number(k.quotes_out)} quote${k.quotes_out === 1 ? "" : "s"} awaiting a decision`,
              body: `${compactMoney(k.quotes_out_value)} out with customers.`,
              href: "/quotes",
            }
          : null,
      ].filter(Boolean)
    : [];

  return (
    <>
      <PageHeader
        eyebrow={todayLabel()}
        title={`${greeting()}${firstName ? `, ${firstName}` : ""}.`}
        description={`Here's where ${session?.organization.name ?? "your workspace"} stands today.`}
        actions={
          <>
            {can("sales.forecast_view") ? (
              <Link className="ui-btn" href="/forecast">
                View forecast
              </Link>
            ) : null}
            <Link className="ui-btn ui-btn-primary" href="/dashboards">
              Dashboards <ArrowRight size={15} />
            </Link>
          </>
        }
      />

      {error ? <Alert>{error}</Alert> : null}

      {loading && !data ? (
        <div className="ui-card">
          <LoadingRows rows={8} />
        </div>
      ) : data && k ? (
        <div className="ui-stack">
          <div className="ui-grid ui-grid-6">
            <Kpi label="Open pipeline" value={compactMoney(k.open_pipeline)} note={`${compactMoney(k.weighted_pipeline)} weighted`} href="/pipeline" />
            <Kpi
              label="Won this quarter"
              value={compactMoney(k.won_this_quarter)}
              note={`${number(k.won_count_quarter)} deal${k.won_count_quarter === 1 ? "" : "s"} closed`}
              tone={k.won_this_quarter > 0 ? "success" : undefined}
            />
            <Kpi label="Win rate" value={percent(k.win_rate)} note="Closed deals, last 12 months" />
            <Kpi label="New leads" value={number(k.new_leads_30d)} note={`Last 30 days · avg score ${number(k.avg_lead_score ?? 0)}`} href="/leads" />
            <Kpi
              label="Open cases"
              value={number(k.open_cases)}
              note={k.sla_breaches ? `${number(k.sla_breaches)} past SLA` : "All within SLA"}
              tone={k.sla_breaches ? "danger" : undefined}
              href={can("service.cases") ? "/cases" : undefined}
            />
            <Kpi label="Quotes out" value={number(k.quotes_out)} note={`${compactMoney(k.quotes_out_value)} pending`} href="/quotes" />
          </div>

          {attention.length ? (
            <div className="ui-grid ui-grid-4">
              {attention.map((item) =>
                item ? (
                  <Link key={item.title} href={item.href} className={`ui-insight tone-${item.tone}`}>
                    <strong>{item.title}</strong>
                    <span>{item.body}</span>
                  </Link>
                ) : null
              )}
            </div>
          ) : (
            <Alert tone="success">Nothing urgent — no breached cases, overdue tasks or stalled quotes.</Alert>
          )}

          <div className="ui-split">
            <Card
              title="Open pipeline by stage"
              icon={<Handshake size={16} />}
              actions={
                <Link className="ui-btn ui-btn-ghost ui-btn-sm" href="/pipeline">
                  Board <ArrowRight size={14} />
                </Link>
              }
            >
              {data.pipeline_by_stage.length ? (
                <HBarList
                  data={data.pipeline_by_stage.map((row) => ({ label: `${row.stage} (${row.count})`, value: row.amount }))}
                  format="currency"
                />
              ) : (
                <EmptyState title="No open deals" message="Create an opportunity to start building pipeline." />
              )}
            </Card>

            <Card title="Closing in 30 days" icon={<CalendarClock size={16} />} flush>
              {data.closing_soon.length ? (
                <div className="ui-list">
                  {data.closing_soon.map((deal) => (
                    <Link key={deal.id} className="ui-list-item" href={`/opportunities/${deal.id}`}>
                      <div className="main">
                        <div className="title">{deal.name}</div>
                        <div className="sub">
                          {deal.account_name ?? "No account"} · {shortDate(deal.expected_close_date)}
                          {deal.probability !== null ? ` · ${deal.probability}%` : ""}
                        </div>
                      </div>
                      <strong className="ui-num">{compactMoney(deal.amount)}</strong>
                    </Link>
                  ))}
                </div>
              ) : (
                <EmptyState icon={<CheckCircle2 size={20} />} title="Nothing closing soon" message="No open deals are due in the next 30 days." />
              )}
            </Card>
          </div>

          <div className="ui-grid ui-grid-3">
            <Card
              title="My tasks"
              icon={<ListChecks size={16} />}
              flush
              actions={
                <Link className="ui-btn ui-btn-ghost ui-btn-sm" href="/tasks">
                  All tasks
                </Link>
              }
            >
              {data.my_tasks.length ? (
                <div className="ui-list">
                  {data.my_tasks.map((task) => (
                    <Link key={task.id} className="ui-list-item" href="/tasks">
                      <div className="main">
                        <div className="title">{task.title}</div>
                        <div className="sub" style={{ color: task.overdue ? "var(--danger)" : undefined }}>
                          {task.overdue ? <AlertTriangle size={12} style={{ verticalAlign: -1 }} /> : null}{" "}
                          {task.due_at ? `Due ${relativeTime(task.due_at)}` : "No due date"}
                          {task.account_name ? ` · ${task.account_name}` : ""}
                        </div>
                      </div>
                      <Badge tone={PRIORITY_TONE[task.priority] ?? "neutral"}>{task.priority}</Badge>
                    </Link>
                  ))}
                </div>
              ) : (
                <EmptyState icon={<CheckCircle2 size={20} />} title="You're clear" message="No open tasks assigned to you." />
              )}
            </Card>

            <Card
              title="Hottest leads"
              icon={<Flame size={16} />}
              flush
              actions={
                <Link className="ui-btn ui-btn-ghost ui-btn-sm" href="/leads">
                  All leads
                </Link>
              }
            >
              {data.hot_leads.length ? (
                <div className="ui-list">
                  {data.hot_leads.map((lead) => (
                    <Link key={lead.id} className="ui-list-item" href={`/leads/${lead.id}`}>
                      <div className="main">
                        <div className="title">{lead.name}</div>
                        <div className="sub">
                          {lead.company_name ?? "No company"} · {lead.status}
                        </div>
                      </div>
                      <Badge tone={scoreTone(lead.score)}>{lead.score}</Badge>
                    </Link>
                  ))}
                </div>
              ) : (
                <EmptyState title="No open leads" message="New leads are scored automatically as they arrive." />
              )}
            </Card>

            <Card
              title="Recent activity"
              icon={<History size={16} />}
              flush
              actions={
                <Link className="ui-btn ui-btn-ghost ui-btn-sm" href="/activities">
                  Timeline
                </Link>
              }
            >
              {data.recent_activity.length ? (
                <div className="ui-list">
                  {data.recent_activity.map((activity) => {
                    const Icon = ACTIVITY_ICONS[activity.activity_type] ?? FilePenLine;
                    return (
                      <div key={activity.id} className="ui-list-item">
                        <span className="ui-avatar" style={{ borderRadius: 9 }}>
                          <Icon size={14} />
                        </span>
                        <div className="main">
                          <div className="title">{activity.subject}</div>
                          <div className="sub">
                            {activity.author_name ?? "Someone"}
                            {activity.account_name ? ` · ${activity.account_name}` : ""} · {relativeTime(activity.occurred_at)}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <EmptyState title="No activity yet" message="Calls, emails and meetings you log show up here." />
              )}
            </Card>
          </div>
        </div>
      ) : null}
    </>
  );
}
