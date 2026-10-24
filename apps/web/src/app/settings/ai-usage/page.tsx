"use client";

import Link from "next/link";
import { Coins, PackagePlus, Sparkles } from "lucide-react";
import { useState } from "react";

import { ModelUsageTables, type ModelUsage } from "@/components/integrations/panels/ai";
import { SettingsShell } from "@/components/settings-shell";
import { UsageMeter } from "@/components/settings-kit";
import { Alert, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { AI_SOURCE_LABELS, isAiSource } from "@/lib/ai-source";
import { useApi } from "@/lib/use-api";
import { periodLabel } from "@/lib/usage";

type AiUsage = {
  period: string;
  allowance: number;
  allowance_detail: { ai_words_per_seat: number; seats: number; ai_words_addons: number; ai_words_plan: number };
  used: number;
  remaining: number | null;
  by_user: { user_id: string | null; name: string; words: number; requests: number; limit: number | null }[];
  /** Words by where they came from: a model (`ai` = all of them) or built-in rules. */
  by_feature: { feature: string; label: string; words: number; requests: number; ai: number; claude: number; openai: number; azure_openai: number; rules: number }[];
  daily: { date: string; words: number }[];
  periods: string[];
  model_source: string;
  model_label: string;
  model_name: string | null;
  /** Tokens and estimated cost on the workspace's own models (OpenAI / Azure OpenAI), when used. */
  provider_usage: ModelUsage | null;
  can_see_everyone: boolean;
};

function featureTitle(f: AiUsage["by_feature"][number]) {
  const parts = (["openai", "azure_openai", "claude"] as const).filter((source) => f[source]).map((source) => `${f[source].toLocaleString()} words from ${AI_SOURCE_LABELS[source]}`);
  if (f.rules) parts.push(`${f.rules.toLocaleString()} built-in`);
  return `${f.requests} requests · ${parts.join(", ") || "no words"}`;
}

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
                {data.model_label ?? data.model_source}
              </div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                {data.model_name ? (
                  <>
                    <span className="ui-mono">{data.model_name}</span> ·{" "}
                  </>
                ) : null}
                {isAiSource(data.model_source) && data.model_source !== "claude" ? (
                  <>
                    Your workspace&apos;s own model. <Link href={`/integrations/${data.model_source}`}>Change it</Link>
                  </>
                ) : (
                  <>
                    Use your own OpenAI or Azure OpenAI models in <Link href="/integrations?category=AI%20Models">Integrations</Link>
                  </>
                )}
                . Tune tone and facts in the <Link href="/settings/ai-context">AI Context Center</Link>.
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
                    <div key={f.feature} className="settings-bar-row" title={featureTitle(f)}>
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
          {data.provider_usage ? (
            <Card title="Your AI models: tokens and cost" icon={<Coins size={17} />}>
              <ModelUsageTables usage={data.provider_usage} />
              <p className="ui-small ui-muted" style={{ margin: "10px 0 0" }}>
                Estimated from the price table on the provider&apos;s integration page. Limits and fallbacks are set there too.
              </p>
            </Card>
          ) : null}
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
