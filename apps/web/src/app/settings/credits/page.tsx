"use client";

import Link from "next/link";
import { Coins, Download, PackagePlus } from "lucide-react";
import { useState } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { UsageMeter } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import { periodLabel, shortPeriod } from "@/lib/usage";

type CreditUsage = {
  period: string;
  allowance: { credits_total: number; credits_plan: number; credits_addons: number; credits_per_seat: number; seats: number; plan: string };
  used: number;
  remaining: number | null;
  by_user: { user_id: string | null; name: string; used: number; limit: number | null }[];
  by_feature: { feature: string; label: string; used: number }[];
  transactions: {
    id: string;
    created_at: string;
    user_name: string;
    kind: string;
    amount: number;
    feature: string;
    description: string | null;
    record_type: string | null;
    record_id: string | null;
  }[];
  trend: { period: string; used: number }[];
  periods: string[];
  can_see_everyone: boolean;
};

const RECORD_PATHS: Record<string, string> = { lead: "leads", contact: "contacts", account: "accounts", opportunity: "opportunities" };

export default function CreditsPage() {
  return (
    <SettingsShell>
      <Credits />
    </SettingsShell>
  );
}

function Credits() {
  const [period, setPeriod] = useState<string>("");
  const [person, setPerson] = useState<string>("");
  const usage = useApi<CreditUsage>(`/credits/usage${period || person ? "?" : ""}${period ? `period=${period}` : ""}${period && person ? "&" : ""}${person ? `user_id=${person}` : ""}`);
  const data = usage.data;
  const current = data && data.period === data.periods[0];
  const maxTrend = Math.max(1, ...(data?.trend ?? []).map((t) => t.used));
  const maxFeature = Math.max(1, ...(data?.by_feature ?? []).map((f) => f.used));

  return (
    <>
      <PageHeader
        title="Credit usage"
        description="Credits pay for waterfall enrichment lookups and data requests. The pool refills on the 1st of each month."
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
            {data?.can_see_everyone ? (
              <a className="ui-btn" href={`/api/aiva/credits/usage.csv?period=${data.period}`}>
                <Download size={15} /> Export CSV
              </a>
            ) : null}
            <Link className="ui-btn ui-btn-primary" href="/settings/billing/add-ons">
              <PackagePlus size={15} /> Buy credits
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
              <UsageMeter label={current ? "Used this month" : `Used in ${periodLabel(data.period)}`} used={data.used} total={data.allowance.credits_total} />
              <p className="ui-muted ui-small" style={{ margin: "10px 0 0" }}>
                {data.allowance.credits_per_seat.toLocaleString()} per user × {data.allowance.seats} + {data.allowance.credits_addons.toLocaleString()} from add-ons
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">Remaining</div>
              <div className="ui-kpi-value">{data.remaining === null ? "—" : data.remaining.toLocaleString()}</div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                {current ? "Resets on the 1st." : "Past months don't carry over."}
              </p>
            </Card>
            <Card title="Last six months">
              <div className="settings-columns" style={{ height: 96 }}>
                {[...data.trend].map((t) => (
                  <div key={t.period} className="settings-column" title={`${periodLabel(t.period)}: ${t.used.toLocaleString()} credits`}>
                    <div className={`col${t.period === data.period ? " current" : ""}`} style={{ height: `${Math.max(2, (t.used / maxTrend) * 70)}px` }} />
                    <span>{shortPeriod(t.period)}</span>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div className="ui-grid ui-grid-2 admin-two">
            <Card title="By feature">
              {data.by_feature.some((f) => f.used > 0) ? (
                <div className="settings-bar-chart">
                  {data.by_feature
                    .filter((f) => f.used > 0)
                    .sort((a, b) => b.used - a.used)
                    .map((f) => (
                      <div key={f.feature} className="settings-bar-row">
                        <span className="ui-truncate">{f.label}</span>
                        <span className="bar">
                          <div style={{ width: `${(f.used / maxFeature) * 100}%` }} />
                        </span>
                        <span className="val">{f.used.toLocaleString()}</span>
                      </div>
                    ))}
                </div>
              ) : (
                <p className="ui-muted ui-small" style={{ margin: 0 }}>
                  No credits used yet this month.
                </p>
              )}
            </Card>
            <Card title={data.can_see_everyone ? "By person" : "Your usage"} flush>
              {data.by_user.length ? (
                <div className="ui-table-wrap">
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>Person</th>
                        <th className="ui-num">Used</th>
                        <th className="ui-num">Monthly limit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.by_user.map((u) => (
                        <tr key={u.user_id ?? "workspace"}>
                          <td>
                            {u.user_id && data.can_see_everyone ? (
                              <button type="button" className="link-button" onClick={() => setPerson(person === u.user_id ? "" : (u.user_id ?? ""))}>
                                {u.name}
                              </button>
                            ) : (
                              u.name
                            )}
                            {person && person === u.user_id ? <Badge tone="info">Filtered</Badge> : null}
                          </td>
                          <td className="ui-num">{u.used.toLocaleString()}</td>
                          <td className="ui-num">
                            {u.limit === null || u.limit === undefined ? (
                              <span className="ui-muted">No limit</span>
                            ) : (
                              <span className={u.used >= u.limit ? "text-danger" : undefined}>{u.limit.toLocaleString()}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState icon={<Coins size={20} />} title="Nobody has used credits yet" />
              )}
            </Card>
          </div>

          <Card
            title="Activity"
            flush
            actions={
              person ? (
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => setPerson("")}>
                  Show everyone
                </button>
              ) : null
            }
          >
            {data.transactions.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Who</th>
                      <th>Feature</th>
                      <th>What</th>
                      <th className="ui-num">Credits</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.transactions.map((t) => (
                      <tr key={t.id}>
                        <td className="ui-nowrap ui-small">{dateTime(t.created_at)}</td>
                        <td>{t.user_name}</td>
                        <td>{t.feature}</td>
                        <td className="ui-small" style={{ maxWidth: 360 }}>
                          {t.record_type && t.record_id && RECORD_PATHS[t.record_type] ? (
                            <Link href={`/${RECORD_PATHS[t.record_type]}/${t.record_id}`}>{t.description ?? "Open record"}</Link>
                          ) : (
                            t.description ?? "—"
                          )}
                        </td>
                        <td className={`ui-num ui-strong${t.amount > 0 ? " text-success" : ""}`}>
                          {t.amount > 0 ? `+${t.amount}` : t.amount}
                          {t.kind === "refund" ? <span className="ui-muted ui-small"> refund</span> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon={<Coins size={20} />} title="No credit activity" message="Enrichment lookups and data requests show up here." />
            )}
          </Card>
        </div>
      )}
    </>
  );
}
