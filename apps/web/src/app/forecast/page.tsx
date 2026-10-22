"use client";

import Link from "next/link";
import { ChevronLeft, ChevronRight, Target } from "lucide-react";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useToast } from "@/components/providers";
import { Alert, Avatar, Badge, Card, EmptyState, Kpi, LoadingRows, PageHeader, Progress, Tabs } from "@/components/ui";
import { compactMoney, date, moneyExact, percent } from "@/lib/format";
import type { Forecast } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const CATEGORY_TONE = { commit: "success", best_case: "info", pipeline: "neutral", omitted: "warning", closed: "primary" } as const;

function periodLabel(period: string) {
  const [year, quarter] = period.split("-");
  return `${quarter} ${year}`;
}

function shift(period: string, by: number) {
  const [yearText, quarterText] = period.split("-Q");
  let index = Number(yearText) * 4 + Number(quarterText) - 1 + by;
  const year = Math.floor(index / 4);
  index = (index % 4) + 1;
  return `${year}-Q${index}`;
}

export default function ForecastPage() {
  return (
    <AppShell permission="sales.forecast_view" what="the forecast">
      <ForecastView />
    </AppShell>
  );
}

function ForecastView() {
  const toast = useToast();
  const [period, setPeriod] = useState<string | null>(null);
  const forecast = useApi<Forecast>(`/forecast${period ? `?period=${period}` : ""}`);
  const [category, setCategory] = useState<"all" | "commit" | "best_case" | "pipeline" | "omitted">("all");
  const [quotaDraft, setQuotaDraft] = useState<Record<string, string>>({});
  const f = forecast.data;
  const current = period ?? f?.period ?? null;

  const deals = useMemo(
    () => (f?.deals ?? []).filter((deal) => category === "all" || deal.forecast_category === category),
    [category, f?.deals]
  );

  async function saveQuota(userId: string) {
    if (!f || quotaDraft[userId] === undefined) return;
    const amount = Number(quotaDraft[userId]);
    if (!Number.isFinite(amount) || amount < 0) {
      toast("Enter a quota of zero or more.", "danger");
      return;
    }
    try {
      await api.put("/forecast/quotas", { user_id: userId, period: f.period, amount });
      setQuotaDraft((draft) => {
        const next = { ...draft };
        delete next[userId];
        return next;
      });
      toast("Quota saved.");
      await forecast.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const t = f?.totals;

  return (
    <>
      <PageHeader
        eyebrow="Sales"
        title="Forecast"
        description="Closed won plus what each rep has committed, against quota, for the fiscal quarter."
        actions={
          current ? (
            <div className="ui-row">
              <button type="button" className="ui-btn ui-btn-icon" aria-label="Previous quarter" onClick={() => setPeriod(shift(current, -1))}>
                <ChevronLeft size={16} />
              </button>
              <select className="ui-select" style={{ width: 170 }} value={current} onChange={(e) => setPeriod(e.target.value)} aria-label="Quarter">
                {[...new Set([...(f?.periods ?? []), current])].sort().map((item) => (
                  <option key={item} value={item}>
                    {periodLabel(item)}
                    {item === f?.current_period ? " (current)" : ""}
                  </option>
                ))}
              </select>
              <button type="button" className="ui-btn ui-btn-icon" aria-label="Next quarter" onClick={() => setPeriod(shift(current, 1))}>
                <ChevronRight size={16} />
              </button>
            </div>
          ) : null
        }
      />

      {forecast.error ? <Alert>{forecast.error}</Alert> : null}

      {!f || !t ? (
        <div className="ui-card">
          <LoadingRows rows={8} />
        </div>
      ) : (
        <div className="ui-stack">
          <div className="ui-card" style={{ padding: 20 }}>
            <div className="ui-row" style={{ alignItems: "flex-end", flexWrap: "wrap", gap: 18, marginBottom: 14 }}>
              <div>
                <div className="ui-kpi-label">Projected ({periodLabel(f.period)})</div>
                <div style={{ fontSize: 34, fontWeight: 780, letterSpacing: "-0.03em", fontVariantNumeric: "tabular-nums" }}>
                  {moneyExact(t.projected)}
                </div>
                <div className="ui-muted ui-small">
                  Closed won + commit · {date(f.start)} – {date(f.end)}
                </div>
              </div>
              <span className="ui-spacer" />
              <div style={{ textAlign: "right" }}>
                <div className="ui-kpi-label">Projected vs quota</div>
                <div style={{ fontSize: 24, fontWeight: 760 }}>{t.quota ? percent((t.projected / t.quota) * 100) : "—"}</div>
                <div className="ui-muted ui-small">
                  {percent(t.attainment)} closed so far · {t.gap > 0 ? `${compactMoney(t.gap)} gap` : "quota covered"} to{" "}
                  {compactMoney(t.quota)}
                </div>
              </div>
            </div>
            <Progress value={t.quota ? (t.projected / t.quota) * 100 : 0} tone={t.projected >= t.quota && t.quota > 0 ? "success" : undefined} />
          </div>

          <div className="ui-grid ui-grid-4">
            <Kpi label="Closed won" value={compactMoney(t.closed_won)} tone={t.closed_won ? "success" : undefined} />
            <Kpi label="Commit" value={compactMoney(t.commit)} note="Reps are confident these close" />
            <Kpi label="Best case" value={compactMoney(t.best_case)} note="Upside if things go well" />
            <Kpi label="Pipeline" value={compactMoney(t.pipeline)} note="Open, not yet called" />
          </div>

          <Card title="By rep" flush>
            {f.rows.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Rep</th>
                      <th className="ui-num">Quota</th>
                      <th className="ui-num">Closed won</th>
                      <th className="ui-num">Commit</th>
                      <th className="ui-num">Best case</th>
                      <th className="ui-num">Pipeline</th>
                      <th style={{ width: 180 }}>Projected vs quota</th>
                    </tr>
                  </thead>
                  <tbody>
                    {f.rows.map((row) => (
                      <tr key={row.user_id}>
                        <td>
                          <div className="ui-row">
                            <Avatar name={row.name} />
                            <div>
                              <div className="cell-title">{row.name}</div>
                              <div className="cell-sub">{row.open_count} open deals</div>
                            </div>
                          </div>
                        </td>
                        <td className="ui-num">
                          {f.can_manage_quotas ? (
                            <input
                              className="ui-input sm"
                              type="number"
                              min={0}
                              step={1000}
                              style={{ width: 130, textAlign: "right" }}
                              value={quotaDraft[row.user_id] ?? String(row.quota)}
                              onChange={(e) => setQuotaDraft({ ...quotaDraft, [row.user_id]: e.target.value })}
                              onBlur={() => void saveQuota(row.user_id)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                              }}
                              aria-label={`Quota for ${row.name}`}
                            />
                          ) : (
                            compactMoney(row.quota)
                          )}
                        </td>
                        <td className="ui-num">{compactMoney(row.closed_won)}</td>
                        <td className="ui-num">{compactMoney(row.commit)}</td>
                        <td className="ui-num">{compactMoney(row.best_case)}</td>
                        <td className="ui-num">{compactMoney(row.pipeline)}</td>
                        <td>
                          <div className="ui-row">
                            <div style={{ flex: 1 }}>
                              <Progress value={row.quota ? (row.projected / row.quota) * 100 : 0} tone={row.projected >= row.quota && row.quota > 0 ? "success" : undefined} />
                            </div>
                            <span className="ui-num ui-small" style={{ width: 44 }}>
                              {row.quota ? `${Math.round((row.projected / row.quota) * 100)}%` : "—"}
                            </span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td>Total</td>
                      <td className="ui-num">{compactMoney(t.quota)}</td>
                      <td className="ui-num">{compactMoney(t.closed_won)}</td>
                      <td className="ui-num">{compactMoney(t.commit)}</td>
                      <td className="ui-num">{compactMoney(t.best_case)}</td>
                      <td className="ui-num">{compactMoney(t.pipeline)}</td>
                      <td className="ui-num">{t.quota ? percent((t.projected / t.quota) * 100) : "—"}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            ) : (
              <EmptyState icon={<Target size={20} />} title="No reps with deals or quotas" message="Set quotas or assign opportunities to see the roll-up." />
            )}
          </Card>

          <Card
            title="Deals closing this quarter"
            flush
            actions={
              <Tabs
                label="Forecast category"
                value={category}
                onChange={setCategory}
                items={[
                  { value: "all", label: "All" },
                  { value: "commit", label: "Commit" },
                  { value: "best_case", label: "Best case" },
                  { value: "pipeline", label: "Pipeline" },
                  { value: "omitted", label: "Omitted" },
                ]}
              />
            }
          >
            {deals.length ? (
              <div className="ui-table-wrap">
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>Opportunity</th>
                      <th>Owner</th>
                      <th>Category</th>
                      <th>Close date</th>
                      <th className="ui-num">Probability</th>
                      <th className="ui-num">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deals.map((deal) => (
                      <tr key={deal.id}>
                        <td>
                          <Link className="cell-title" href={`/opportunities/${deal.id}`}>
                            {deal.name}
                          </Link>
                        </td>
                        <td>{deal.owner_name ?? "—"}</td>
                        <td>
                          <Badge tone={CATEGORY_TONE[deal.forecast_category as keyof typeof CATEGORY_TONE] ?? "neutral"}>
                            {deal.forecast_category.replace("_", " ")}
                          </Badge>
                        </td>
                        <td>{date(deal.expected_close_date)}</td>
                        <td className="ui-num">{deal.probability}%</td>
                        <td className="ui-num ui-strong">{moneyExact(deal.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No deals in this view" message="Change a deal's forecast category on the opportunity to move it here." />
            )}
          </Card>
        </div>
      )}
    </>
  );
}
