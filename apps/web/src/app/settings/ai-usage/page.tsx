"use client";

import Link from "next/link";
import { PackagePlus, Sparkles } from "lucide-react";
import { useState } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { UsageMeter } from "@/components/settings-kit";
import { Alert, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/use-api";
import { periodLabel } from "@/lib/usage";

type AiUsage = {
  period: string;
  allowance: number;
  allowance_detail: { ai_words_per_seat: number; seats: number; ai_words_addons: number; ai_words_plan: number };
  used: number;
  remaining: number | null;
  by_user: { user_id: string | null; name: string; words: number; requests: number; limit: number | null }[];
  by_feature: { feature: string; label: string; words: number; requests: number; claude: number; rules: number }[];
  daily: { date: string; words: number }[];
  periods: string[];
  model_source: string;
  can_see_everyone: boolean;
};

export default function AiUsagePage() {
  return (
    <SettingsShell>
      <AiUsageView />
    </SettingsShell>
  );
}

function AiUsageView() {
  const [period, setPeriod] = useState("");
  const usage = useApi<AiUsage>(`/ai-usage${period ? `?period=${period}` : ""}`);
  const data = usage.data;
  const maxDay = Math.max(1, ...(data?.daily ?? []).map((d) => d.words));
  const maxFeature = Math.max(1, ...(data?.by_feature ?? []).map((f) => f.words));
  const current = data && data.period === data.periods[0];

  return (
    <>
      <PageHeader
        title="AI word usage"
        description="Words AIVA writes for you — email drafts, record summaries, call notes and agent suggestions — count toward a monthly pool that refills on the 1st."
        actions={
          <>
            {data ? (
              <select className="ui-select sm" aria-label="Month" value={period || data.period} onChange={(event) => setPeriod(event.target.value)} style={{ width: 170 }}>
                {data.periods.map((p) => (
                  <option key={p} value={p}>
                    {periodLabel(p)}
                  </option>
                ))}
              </select>
            ) : null}
            <Link className="ui-btn ui-btn-primary" href="/settings/billing/add-ons">
              <PackagePlus size={15} /> Buy AI words
            </Link>
          </>
        }
      />
      {usage.error ? <Alert>{usage.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <div className="ui-stack">
          <div className="settings-kpis three">
            <Card>
              <UsageMeter label={current ? "Used this month" : `Used in ${periodLabel(data.period)}`} used={data.used} total={data.allowance} />
              <p className="ui-muted ui-small" style={{ margin: "10px 0 0" }}>
                {data.allowance_detail.ai_words_per_seat.toLocaleString()} per user × {data.allowance_detail.seats} +{" "}
                {data.allowance_detail.ai_words_addons.toLocaleString()} from add-ons
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">Remaining</div>
              <div className="ui-kpi-value">{data.remaining === null ? "—" : data.remaining.toLocaleString()}</div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                When it runs out, AIVA falls back to simple built-in text until the 1st.
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">Writing with</div>
              <div className="ui-kpi-value" style={{ fontSize: 20 }}>
                {data.model_source}
              </div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                Tune tone and facts in the <Link href="/settings/ai-context">AI Context Center</Link>.
              </p>
            </Card>
          </div>

          <Card title="Words per day">
            {data.daily.length ? (
              <div className="settings-columns">
                {data.daily.map((d) => (
                  <div key={d.date} className="settings-column" title={`${d.date}: ${d.words.toLocaleString()} words`}>
                    <div className="col" style={{ height: `${Math.max(2, (d.words / maxDay) * 110)}px` }} />
                    <span>{new Date(`${d.date}T12:00:00`).getDate()}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                No AI writing yet this month.
              </p>
            )}
          </Card>

          <div className="ui-grid ui-grid-2 admin-two">
            <Card title="By feature">
              {data.by_feature.length ? (
                <div className="settings-bar-chart">
                  {data.by_feature.map((f) => (
                    <div key={f.feature} className="settings-bar-row" title={`${f.requests} requests · ${f.claude.toLocaleString()} words from Claude, ${f.rules.toLocaleString()} built-in`}>
                      <span className="ui-truncate">{f.label}</span>
                      <span className="bar">
                        <div style={{ width: `${(f.words / maxFeature) * 100}%` }} />
                      </span>
                      <span className="val">{f.words.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState icon={<Sparkles size={20} />} title="Nothing yet" message="Drafts, summaries and call notes show up here." />
              )}
            </Card>
            <Card title={data.can_see_everyone ? "By person" : "Your usage"} flush>
              {data.by_user.length ? (
                <div className="ui-table-wrap">
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>Person</th>
                        <th className="ui-num">Requests</th>
                        <th className="ui-num">Words</th>
                        <th className="ui-num">Limit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.by_user.map((u) => (
                        <tr key={u.user_id ?? "agents"}>
                          <td>{u.name}</td>
                          <td className="ui-num">{u.requests.toLocaleString()}</td>
                          <td className="ui-num">{u.words.toLocaleString()}</td>
                          <td className="ui-num">{u.limit === null || u.limit === undefined ? <span className="ui-muted">No limit</span> : u.limit.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState icon={<Sparkles size={20} />} title="No usage yet" />
              )}
            </Card>
          </div>
          {data.can_see_everyone ? (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Set a monthly cap for one person in <Link href="/settings/licenses">License settings</Link>.
            </p>
          ) : null}
        </div>
      )}
    </>
  );
}
