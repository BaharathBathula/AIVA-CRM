"use client";

import Link from "next/link";
import { BrainCircuit, TrendingDown, TrendingUp } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Kpi, LoadingRows, PageHeader, Progress, Tabs, type Tone } from "@/components/ui";
import { compactMoney, date, moneyExact } from "@/lib/format";
import { useApi } from "@/lib/use-api";

interface Factor {
  label: string;
  impact: "positive" | "negative" | "neutral";
}

interface PredictionRow {
  id: string;
  name: string;
  account_name: string | null;
  owner_name: string | null;
  stage_name: string | null;
  amount: number;
  expected_close_date: string | null;
  stage_probability: number | null;
  weighted: number;
  probability: number;
  label: "likely" | "possible" | "at_risk";
  factors: Factor[];
  predicted_close_date: string | null;
}

interface Predictions {
  model: { closed_deals: number; win_rate: number; median_cycle_days: number; median_won_amount: number; confidence: "high" | "medium" | "low" };
  summary: {
    open_deals: number;
    pipeline: number;
    predicted_revenue: number;
    predicted_this_quarter: number;
    stage_weighted: number;
    likely: number;
    possible: number;
    at_risk: number;
    quarter_end: string;
  };
  items: PredictionRow[];
}

const LABEL: Record<PredictionRow["label"], { text: string; tone: Tone }> = {
  likely: { text: "Likely to win", tone: "success" },
  possible: { text: "Could go either way", tone: "warning" },
  at_risk: { text: "At risk", tone: "danger" },
};

export default function PredictionsPage() {
  return (
    <FeaturePage feature="predictive_intelligence">
      <PredictionsView />
    </FeaturePage>
  );
}

function PredictionsView() {
  const { can } = useSession();
  const [mine, setMine] = useState(false);
  const [filter, setFilter] = useState<"all" | PredictionRow["label"]>("all");
  const [open, setOpen] = useState<string | null>(null);
  const data = useApi<Predictions>(`/predictions${mine ? "?mine=true" : ""}`);
  const d = data.data;
  const rows = (d?.items ?? []).filter((row) => filter === "all" || row.label === filter);

  return (
    <>
      <PageHeader
        title="Predictions"
        description="AIVA learns from your closed deals — stage history, deal size, rep track record, activity and timing — to estimate each open deal's chance of winning and when it will close."
        actions={
          can("records.view_all") ? (
            <label className="ui-check ui-small">
              <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only my deals
            </label>
          ) : null
        }
      />
      {data.error ? <Alert>{data.error}</Alert> : null}
      {d ? (
        <div className="prediction-model ui-muted ui-small">
          <BrainCircuit size={15} />
          Trained on {d.model.closed_deals} closed deals · {d.model.win_rate}% win rate · typical cycle {d.model.median_cycle_days} day{d.model.median_cycle_days === 1 ? "" : "s"} ·{" "}
          <Badge tone={d.model.confidence === "high" ? "success" : d.model.confidence === "medium" ? "info" : "warning"}>{d.model.confidence} confidence</Badge>
          {d.model.confidence === "low" ? <span>Predictions sharpen as more deals close.</span> : null}
        </div>
      ) : null}
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Predicted revenue" value={d ? compactMoney(d.summary.predicted_revenue) : "—"} note={d ? `vs ${compactMoney(d.summary.stage_weighted)} by stage probability` : undefined} />
        <Kpi label="Predicted this quarter" value={d ? compactMoney(d.summary.predicted_this_quarter) : "—"} note={d ? `Closing by ${date(d.summary.quarter_end)}` : undefined} />
        <Kpi label="Open pipeline" value={d ? compactMoney(d.summary.pipeline) : "—"} note={d ? `${d.summary.open_deals} deals` : undefined} />
        <Kpi label="Deals at risk" value={d?.summary.at_risk ?? "—"} note={d ? `${d.summary.likely} likely · ${d.summary.possible} could go either way` : undefined} />
      </div>
      <Card
        flush
        title="Open deals"
        actions={
          <Tabs
            label="Filter"
            value={filter}
            onChange={setFilter}
            items={[
              { value: "all", label: "All" },
              { value: "likely", label: "Likely", count: d?.summary.likely },
              { value: "possible", label: "Either way", count: d?.summary.possible },
              { value: "at_risk", label: "At risk", count: d?.summary.at_risk },
            ]}
          />
        }
      >
        {data.loading && !d ? (
          <LoadingRows />
        ) : rows.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Deal</th>
                  <th style={{ minWidth: 170 }}>Win chance</th>
                  <th>Predicted close</th>
                  <th className="ui-num">Amount</th>
                  <th className="ui-num">Predicted value</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <PredictionTableRow key={row.id} row={row} open={open === row.id} onToggle={() => setOpen(open === row.id ? null : row.id)} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<BrainCircuit size={20} />} title="No open deals to score" message="Predictions appear for every open opportunity." />
        )}
      </Card>
    </>
  );
}

function PredictionTableRow({ row, open, onToggle }: { row: PredictionRow; open: boolean; onToggle: () => void }) {
  const label = LABEL[row.label];
  const drift = row.stage_probability !== null ? row.probability - row.stage_probability : 0;
  return (
    <>
      <tr className="clickable-row" onClick={onToggle}>
        <td className="cell-title">
          <Link href={`/opportunities/${row.id}`} onClick={(e) => e.stopPropagation()}>
            {row.name}
          </Link>
          <div className="ui-muted ui-small">{[row.account_name, row.stage_name, row.owner_name].filter(Boolean).join(" · ")}</div>
        </td>
        <td>
          <div className="ui-row" style={{ gap: 8 }}>
            <strong className="ui-num">{row.probability}%</strong>
            <Badge tone={label.tone}>{label.text}</Badge>
          </div>
          <div style={{ marginTop: 6 }}>
            <Progress value={row.probability} />
          </div>
          {row.stage_probability !== null && Math.abs(drift) >= 5 ? (
            <div className="ui-small" style={{ marginTop: 4, color: drift > 0 ? "var(--success)" : "var(--danger)" }}>
              {drift > 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />} {Math.abs(drift)} pts {drift > 0 ? "above" : "below"} the stage&apos;s {row.stage_probability}%
            </div>
          ) : null}
        </td>
        <td>
          {date(row.predicted_close_date)}
          {row.expected_close_date && row.predicted_close_date && row.predicted_close_date > row.expected_close_date ? (
            <div className="ui-small" style={{ color: "var(--warning)" }}>
              Rep says {date(row.expected_close_date)}
            </div>
          ) : null}
        </td>
        <td className="ui-num">{moneyExact(row.amount)}</td>
        <td className="ui-num ui-strong">{moneyExact(row.weighted)}</td>
      </tr>
      {open ? (
        <tr className="prediction-factors">
          <td colSpan={5}>
            <strong className="ui-small">Why AIVA thinks so</strong>
            <ul>
              {row.factors.map((factor) => (
                <li key={factor.label} className={`impact-${factor.impact}`}>
                  {factor.impact === "positive" ? <TrendingUp size={13} /> : factor.impact === "negative" ? <TrendingDown size={13} /> : <BrainCircuit size={13} />}
                  {factor.label}
                </li>
              ))}
            </ul>
          </td>
        </tr>
      ) : null}
    </>
  );
}
