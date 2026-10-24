"use client";

import Link from "next/link";
import { ArrowRight, CheckCircle2, Circle, Clock, Send } from "lucide-react";

import { useSession } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { UsageMeter } from "@/components/settings-kit";
import { Alert, Badge, Card, Kpi, LoadingRows, PageHeader } from "@/components/ui";
import { dateTime, number, percent, shortDate } from "@/lib/format";
import { SETTINGS_MENU, allowed } from "@/lib/settings-menu";
import { useApi } from "@/lib/use-api";

type Overview = {
  last_30_days: { sent: number; opened: number; clicked: number; replied: number; bounced: number; failed: number; unsubscribed: number };
  rates: { opened: number; clicked: number; replied: number; bounced: number };
  daily: { date: string; sent: number; replied: number }[];
  sequences: Record<string, number>;
  enrollments: Record<string, number>;
  mailboxes: { connected: number; errors: number; total: number };
  domains: { verified: number; total: number };
  capacity_today: number;
  sent_today: number;
  in_window_now: boolean;
  next_window: string;
  my_sequence_tasks: number;
  checklist: { key: string; label: string; done: boolean; href: string }[];
};

export default function OutboundOverviewPage() {
  return (
    <SettingsShell>
      <OutboundOverview />
    </SettingsShell>
  );
}

/** The last 30 days, one entry per day, with zero for days nothing went out. */
function last30(daily: Overview["daily"]) {
  const byDay = new Map(daily.map((d) => [d.date.slice(0, 10), d]));
  const days: { date: string; sent: number; replied: number }[] = [];
  const today = new Date();
  for (let offset = 29; offset >= 0; offset -= 1) {
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - offset);
    const key = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    days.push(byDay.get(key) ?? { date: key, sent: 0, replied: 0 });
  }
  return days;
}

function OutboundOverview() {
  const { can } = useSession();
  const overview = useApi<Overview>("/outbound/overview");
  const data = overview.data;
  const pages = (SETTINGS_MENU.find((group) => group.key === "outbound")?.items ?? []).filter(
    (item) => item.href !== "/settings/outbound" && allowed(item, can),
  );

  return (
    <>
      <PageHeader
        title="Outbound"
        description="How your team's sequence emails are doing, whether mailboxes and domains are healthy, and what to fix first."
        actions={
          <Link href="/sequences" className="ui-btn ui-btn-primary">
            <Send size={15} /> Open sequences
          </Link>
        }
      />
      {overview.error ? <Alert>{overview.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows rows={8} />
        </div>
      ) : (
        <div className="ui-stack">
          <Checklist items={data.checklist} />

          <div className="settings-kpis">
            <Kpi label="Emails sent" value={number(data.last_30_days.sent)} note="Last 30 days" />
            <Kpi label="Open rate" value={percent(data.rates.opened, 1)} note={`${number(data.last_30_days.opened)} opened`} />
            <Kpi
              label="Reply rate"
              value={percent(data.rates.replied, 1)}
              note={`${number(data.last_30_days.replied)} ${data.last_30_days.replied === 1 ? "reply" : "replies"}`}
              tone={data.last_30_days.replied > 0 ? "success" : undefined}
            />
            <Kpi
              label="Bounce rate"
              value={percent(data.rates.bounced, 1)}
              note={`${number(data.last_30_days.bounced)} bounced · ${number(data.last_30_days.unsubscribed)} unsubscribed`}
              tone={data.rates.bounced >= 5 ? "danger" : undefined}
            />
          </div>

          <div className="ui-grid ui-grid-2 outbound-two">
            <Card title="Today">
              <div className="ui-stack" style={{ gap: 16 }}>
                <UsageMeter label="Emails sent of today's capacity" used={data.sent_today} total={Math.max(data.capacity_today, data.sent_today)} />
                <div className="outbound-facts">
                  <div>
                    <span className="ui-muted ui-small">Sending window</span>
                    {data.in_window_now ? (
                      <Badge tone="success" dot>
                        Sending now
                      </Badge>
                    ) : (
                      <span className="ui-row" style={{ gap: 6 }}>
                        <Clock size={14} aria-hidden /> Opens {dateTime(data.next_window)}
                      </span>
                    )}
                  </div>
                  <div>
                    <span className="ui-muted ui-small">Your sequence tasks</span>
                    <Link href="/tasks">{data.my_sequence_tasks ? `${data.my_sequence_tasks} to do` : "Nothing waiting"}</Link>
                  </div>
                  <div>
                    <span className="ui-muted ui-small">Sequences on</span>
                    <strong>{number(data.sequences.active ?? 0)}</strong>
                  </div>
                  <div>
                    <span className="ui-muted ui-small">People in sequences</span>
                    <strong>{number((data.enrollments.active ?? 0) + (data.enrollments.paused ?? 0))}</strong>
                  </div>
                </div>
                {data.capacity_today === 0 ? (
                  <p className="ui-muted ui-small" style={{ margin: 0 }}>
                    No mailbox is connected, so there&apos;s no sending capacity yet.{" "}
                    <Link href="/settings/outbound/mailboxes">Connect one</Link>.
                  </p>
                ) : null}
              </div>
            </Card>

            <Card title="Health">
              <div className="outbound-health">
                <HealthLine
                  href="/settings/outbound/mailboxes"
                  label="Mailboxes"
                  ok={data.mailboxes.connected > 0 && data.mailboxes.errors === 0}
                  text={
                    data.mailboxes.total
                      ? `${data.mailboxes.connected} of ${data.mailboxes.total} connected${data.mailboxes.errors ? ` · ${data.mailboxes.errors} with errors` : ""}`
                      : "None connected yet"
                  }
                />
                <HealthLine
                  href="/settings/outbound/domains"
                  label="Sending domains"
                  ok={data.domains.total > 0 && data.domains.verified === data.domains.total}
                  text={data.domains.total ? `${data.domains.verified} of ${data.domains.total} verified` : "No domain added yet"}
                />
                <HealthLine
                  href="/settings/outbound/diagnose"
                  label="Bounces"
                  ok={data.rates.bounced < 3}
                  text={data.last_30_days.sent ? `${percent(data.rates.bounced, 1)} of emails bounced` : "Nothing sent yet"}
                />
                <HealthLine
                  href="/settings/outbound/diagnose"
                  label="Failed sends"
                  ok={data.last_30_days.failed === 0}
                  text={data.last_30_days.failed ? `${data.last_30_days.failed} emails couldn't be sent` : "No failures"}
                />
              </div>
              <Link href="/settings/outbound/diagnose" className="ui-btn ui-btn-sm" style={{ marginTop: 14 }}>
                Run a full check <ArrowRight size={14} />
              </Link>
            </Card>
          </div>

          <Card title="Emails sent per day" actions={<span className="ui-muted ui-small">Last 30 days</span>}>
            <DailyChart days={last30(data.daily)} />
          </Card>

          <Card title="Outbound settings">
            <div className="settings-tiles">
              {pages.map((item) => {
                const Icon = item.icon;
                return (
                  <Link key={item.href} href={item.href} className="settings-tile">
                    <span className="tile-icon">
                      <Icon size={19} />
                    </span>
                    <span>
                      <strong>{item.label}</strong>
                      <span className="desc">{item.description}</span>
                    </span>
                  </Link>
                );
              })}
            </div>
          </Card>
        </div>
      )}
    </>
  );
}

function Checklist({ items }: { items: Overview["checklist"] }) {
  const done = items.filter((item) => item.done).length;
  if (done === items.length) return null;
  return (
    <Card title="Get set up" actions={<span className="ui-muted ui-small">{done} of {items.length} done</span>}>
      <ul className="outbound-checklist">
        {items.map((item) => (
          <li key={item.key} className={item.done ? "done" : undefined}>
            {item.done ? <CheckCircle2 size={18} aria-label="Done" /> : <Circle size={18} aria-label="To do" />}
            {item.done ? (
              <span>{item.label}</span>
            ) : (
              <Link href={item.href}>
                {item.label} <ArrowRight size={13} aria-hidden />
              </Link>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function HealthLine({ label, text, ok, href }: { label: string; text: string; ok: boolean; href: string }) {
  return (
    <Link href={href} className="outbound-health-line">
      <span className={`outbound-health-dot ${ok ? "ok" : "bad"}`} aria-hidden />
      <span className="outbound-health-text">
        <strong>{label}</strong>
        <span className="ui-muted ui-small">{text}</span>
      </span>
      <Badge tone={ok ? "success" : "warning"}>{ok ? "Good" : "Check"}</Badge>
    </Link>
  );
}

function DailyChart({ days }: { days: { date: string; sent: number; replied: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.sent));
  const total = days.reduce((sum, d) => sum + d.sent, 0);
  if (!total) {
    return <p className="ui-muted ui-small" style={{ margin: 0 }}>No sequence emails went out in the last 30 days.</p>;
  }
  return (
    <div className="outbound-daily" role="img" aria-label={`${total} emails sent over the last 30 days`}>
      {days.map((day, index) => (
        <div key={day.date} className="outbound-daily-col" title={`${shortDate(day.date)}: ${day.sent} sent, ${day.replied} replied`}>
          <div className="bar" style={{ height: `${Math.max(2, (day.sent / max) * 100)}%` }}>
            {day.replied ? <div className="replied" style={{ height: `${(day.replied / Math.max(1, day.sent)) * 100}%` }} /> : null}
          </div>
          <span>{index % 5 === 4 || index === days.length - 1 ? shortDate(day.date) : ""}</span>
        </div>
      ))}
    </div>
  );
}
