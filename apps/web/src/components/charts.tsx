"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { compactMoney, money, number } from "@/lib/format";
import type { ReportResult } from "@/lib/platform-types";

/**
 * Lightweight SVG charts. One measure per chart (no dual axes), a single
 * brand hue for one series, the fixed categorical order for part-to-whole,
 * and a hover tooltip on every mark.
 */

// Theme-aware colours (defined in theme.css for light and dark).
export const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);
const PRIMARY = "var(--ink)";
const OTHER = "var(--series-other)";

export type Datum = { label: string; value: number; href?: string | null };
export type ValueFormat = "number" | "currency" | "percent";

export function formatValue(value: number, format: ValueFormat = "number", compact = true) {
  if (format === "currency") return compact ? compactMoney(value) : money(value);
  if (format === "percent") return `${number(value, 1)}%`;
  return number(value, Number.isInteger(value) ? 0 : 1);
}

/** Track the rendered width so SVG text stays at its real size at any card width. */
function useWidth(fallback = 640) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = () => setWidth(Math.max(240, Math.round(node.getBoundingClientRect().width)));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceMax(value: number) {
  if (value <= 0) return 1;
  const exponent = Math.pow(10, Math.floor(Math.log10(value)));
  const fraction = value / exponent;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return nice * exponent;
}

/** Positioned in percentages of the chart box so it tracks the responsive SVG. */
function Tooltip({ x, y, label, value }: { x: number; y: number; label: string; value: string }) {
  return (
    <div
      style={{
        position: "absolute",
        left: `${x}%`,
        top: `${y}%`,
        transform: "translate(-50%, calc(-100% - 10px))",
        padding: "6px 10px",
        borderRadius: 8,
        background: "var(--ink)",
        color: "var(--on-ink)",
        fontSize: 12,
        whiteSpace: "nowrap",
        pointerEvents: "none",
        boxShadow: "0 6px 20px rgba(16,19,26,0.18)",
        zIndex: 5,
      }}
    >
      <div style={{ opacity: 0.7 }}>{label}</div>
      <div style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------- Column chart

export function BarChart({
  data,
  format = "number",
  height = 220,
  color = PRIMARY,
}: {
  data: Datum[];
  format?: ValueFormat;
  height?: number;
  color?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [ref, width] = useWidth();
  const pad = { top: 12, right: 8, bottom: 34, left: 52 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(0, ...data.map((d) => d.value)));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const slot = innerW / Math.max(1, data.length);
  const barW = Math.min(46, slot * 0.62);
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(2, Math.floor(innerW / 80))));
  const maxChars = Math.max(6, Math.floor((slot * labelEvery) / 7));

  if (!data.length) return <ChartEmpty height={height} />;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg className="ui-chart" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Column chart">
        {ticks.map((tick) => {
          const y = pad.top + innerH - (tick / max) * innerH;
          return (
            <g key={tick}>
              <line className="grid" x1={pad.left} x2={width - pad.right} y1={y} y2={y} />
              <text x={pad.left - 8} y={y + 4} textAnchor="end">
                {formatValue(tick, format)}
              </text>
            </g>
          );
        })}
        {data.map((d, index) => {
          const h = Math.max(d.value > 0 ? 2 : 0, (d.value / max) * innerH);
          const x = pad.left + slot * index + (slot - barW) / 2;
          const y = pad.top + innerH - h;
          const r = Math.min(4, barW / 2, h);
          return (
            <g key={`${d.label}-${index}`}>
              <path
                d={`M${x},${pad.top + innerH} V${y + r} Q${x},${y} ${x + r},${y} H${x + barW - r} Q${x + barW},${y} ${x + barW},${y + r} V${pad.top + innerH} Z`}
                style={{ fill: color }}
                opacity={hover === null || hover === index ? 1 : 0.4}
              />
              <rect
                x={pad.left + slot * index}
                y={pad.top}
                width={slot}
                height={innerH}
                fill="transparent"
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
              />
              {index % labelEvery === 0 ? (
                <text x={x + barW / 2} y={height - 12} textAnchor="middle">
                  {d.label.length > maxChars ? `${d.label.slice(0, maxChars - 1)}…` : d.label}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {hover !== null ? (
        <Tooltip
          x={((pad.left + slot * hover + slot / 2) / width) * 100}
          y={((pad.top + innerH - (data[hover].value / max) * innerH) / height) * 100}
          label={data[hover].label}
          value={formatValue(data[hover].value, format, false)}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- Line chart

export function LineChart({
  data,
  format = "number",
  height = 220,
  color = PRIMARY,
}: {
  data: Datum[];
  format?: ValueFormat;
  height?: number;
  color?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [ref, width] = useWidth();
  const pad = { top: 14, right: 18, bottom: 34, left: 52 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(0, ...data.map((d) => d.value)));
  const step = data.length > 1 ? innerW / (data.length - 1) : 0;
  const points = data.map((d, index) => ({
    x: pad.left + (data.length > 1 ? step * index : innerW / 2),
    y: pad.top + innerH - (d.value / max) * innerH,
  }));
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(2, Math.floor(innerW / 90))));

  if (!data.length) return <ChartEmpty height={height} />;

  const path = points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
  const area = `${path} L${points[points.length - 1].x},${pad.top + innerH} L${points[0].x},${pad.top + innerH} Z`;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg
        className="ui-chart"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Line chart"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const x = ((event.clientX - box.left) / box.width) * width;
          const index = data.length > 1 ? Math.round((x - pad.left) / step) : 0;
          setHover(Math.max(0, Math.min(data.length - 1, index)));
        }}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const y = pad.top + innerH - t * innerH;
          return (
            <g key={t}>
              <line className="grid" x1={pad.left} x2={width - pad.right} y1={y} y2={y} />
              <text x={pad.left - 8} y={y + 4} textAnchor="end">
                {formatValue(t * max, format)}
              </text>
            </g>
          );
        })}
        <path d={area} style={{ fill: color }} opacity={0.07} />
        <path d={path} fill="none" style={{ stroke: color }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null ? (
          <line x1={points[hover].x} x2={points[hover].x} y1={pad.top} y2={pad.top + innerH} style={{ stroke: "var(--border-strong)" }} strokeDasharray="3 3" />
        ) : null}
        {points.map((p, index) => (
          <circle
            key={index}
            cx={p.x}
            cy={p.y}
            r={hover === index ? 5 : data.length <= 16 ? 3.5 : 0}
            style={{ fill: color, stroke: "var(--surface)" }}
            strokeWidth={2}
          />
        ))}
        {data.map((d, index) =>
          index % labelEvery === 0 ? (
            <text
              key={index}
              x={points[index].x}
              y={height - 12}
              textAnchor={index === 0 && data.length > 1 ? "start" : index === data.length - 1 && data.length > 1 ? "end" : "middle"}
            >
              {d.label.length > 14 ? `${d.label.slice(0, 13)}…` : d.label}
            </text>
          ) : null
        )}
      </svg>
      {hover !== null ? (
        <Tooltip
          x={(points[hover].x / width) * 100}
          y={(points[hover].y / height) * 100}
          label={data[hover].label}
          value={formatValue(data[hover].value, format, false)}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- Donut

export function DonutChart({
  data,
  format = "number",
  size = 170,
  sort = true,
}: {
  data: Datum[];
  format?: ValueFormat;
  size?: number;
  /** Keep the given order (e.g. grades) instead of largest-first. */
  sort?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const slices = useMemo(() => {
    const positive = data.filter((d) => d.value > 0);
    const sorted = sort ? [...positive].sort((a, b) => b.value - a.value) : positive;
    if (sorted.length <= 6) return sorted.map((d, i) => ({ ...d, color: SERIES[i] }));
    const head = sorted.slice(0, 5).map((d, i) => ({ ...d, color: SERIES[i] }));
    const rest = sorted.slice(5).reduce((sum, d) => sum + d.value, 0);
    return [...head, { label: "Other", value: rest, color: OTHER }];
  }, [data, sort]);
  const total = slices.reduce((sum, d) => sum + d.value, 0);

  if (!total) return <ChartEmpty height={size} />;

  const radius = 70;
  const thickness = 22;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const gap = slices.length > 1 ? 2 : 0;
  const active = hover !== null ? slices[hover] : null;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 20 }}>
      <svg width={size} height={size} viewBox="0 0 180 180" role="img" aria-label="Donut chart" style={{ flex: "none" }}>
        <g transform="rotate(-90 90 90)">
          {slices.map((slice, index) => {
            const length = (slice.value / total) * circumference;
            const dash = Math.max(0.5, length - gap);
            const element = (
              <circle
                key={slice.label}
                cx={90}
                cy={90}
                r={radius}
                fill="none"
                style={{ stroke: slice.color, transition: "stroke-width 0.12s" }}
                strokeWidth={hover === index ? thickness + 4 : thickness}
                strokeDasharray={`${dash} ${circumference - dash}`}
                strokeDashoffset={-offset}
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
              />
            );
            offset += length;
            return element;
          })}
        </g>
        <text x={90} y={86} textAnchor="middle" style={{ fontSize: 19, fontWeight: 760, fill: "var(--text)" }}>
          {formatValue(active ? active.value : total, format)}
        </text>
        <text x={90} y={105} textAnchor="middle" style={{ fontSize: 12, fill: "var(--muted)" }}>
          {active ? (active.label.length > 16 ? `${active.label.slice(0, 15)}…` : active.label) : "Total"}
        </text>
      </svg>
      <div style={{ display: "grid", gap: 7, minWidth: 0, flex: "1 1 160px" }}>
        {slices.map((slice, index) => (
          <div
            key={slice.label}
            onMouseEnter={() => setHover(index)}
            onMouseLeave={() => setHover(null)}
            style={{ display: "grid", gridTemplateColumns: "10px minmax(0,1fr) auto auto", gap: 8, alignItems: "center", fontSize: 12.5 }}
          >
            <i style={{ width: 10, height: 10, borderRadius: 3, background: slice.color }} />
            <span className="ui-truncate" style={{ color: "var(--text-2)" }}>
              {slice.label}
            </span>
            <span style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{formatValue(slice.value, format)}</span>
            <span className="ui-muted" style={{ width: 38, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
              {Math.round((slice.value / total) * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Horizontal bars

export function HBarList({ data, format = "number", color = PRIMARY }: { data: Datum[]; format?: ValueFormat; color?: string }) {
  const max = Math.max(0, ...data.map((d) => d.value)) || 1;
  if (!data.length) return <ChartEmpty height={120} />;
  return (
    <div className="ui-hbars">
      {data.map((d) => (
        <div className="ui-hbar" key={d.label} title={`${d.label}: ${formatValue(d.value, format, false)}`}>
          <span className="label">{d.label}</span>
          <span className="track">
            <span className="fill" style={{ display: "block", width: `${(d.value / max) * 100}%`, background: color }} />
          </span>
          <span className="value">{formatValue(d.value, format)}</span>
        </div>
      ))}
    </div>
  );
}

function ChartEmpty({ height }: { height: number }) {
  return (
    <div style={{ height, display: "grid", placeItems: "center", color: "var(--muted)", fontSize: 13 }}>
      No data for this period yet.
    </div>
  );
}

// ---------------------------------------------------------------- Report visual

export function reportFormat(result: ReportResult): ValueFormat {
  if (result.aggregate !== "count" && result.aggregate_field?.type === "currency") return "currency";
  return "number";
}

export function ReportVisual({
  result,
  chart,
  compact,
}: {
  result: ReportResult;
  chart: string;
  compact?: boolean;
}) {
  const format = reportFormat(result);
  const data: Datum[] = result.groups.map((group) => ({ label: group.label ?? "—", value: Number(group.value) || 0 }));
  const height = compact ? 190 : 260;

  if (chart === "metric" || !result.group_by) {
    return (
      <div className="ui-metric">
        <strong>{formatValue(result.summary.value, format, false)}</strong>
        <span className="ui-muted ui-small">
          {result.aggregate === "count"
            ? `${result.object_label.toLowerCase()}`
            : `${result.aggregate} of ${result.aggregate_field?.label.toLowerCase() ?? "value"} across ${number(result.summary.count)} records`}
        </span>
      </div>
    );
  }
  if (chart === "line") return <LineChart data={data} format={format} height={height} />;
  if (chart === "donut") return <DonutChart data={data} format={format} size={compact ? 150 : 180} />;
  if (chart === "table") {
    return <HBarList data={data.slice(0, compact ? 8 : 50)} format={format} />;
  }
  return data.length > 12 && !compact ? (
    <HBarList data={data} format={format} />
  ) : (
    <BarChart data={data} format={format} height={height} />
  );
}
