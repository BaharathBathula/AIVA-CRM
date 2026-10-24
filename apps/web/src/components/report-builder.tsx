"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Download, LayoutGrid, Plus, Save, Trash2, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";

import { ReportVisual, formatValue, reportFormat } from "@/components/charts";
import { ValueInput, operatorLabel, operatorsFor } from "@/components/field-inputs";
import { useSession, useToast } from "@/components/providers";
import { Alert, Card, ConfirmDialog, Field, LoadingRows, Modal, Skeleton } from "@/components/ui";
import { dateTime, date as formatDate, moneyExact, number, titleCase } from "@/lib/format";
import type { Catalog, DashboardDef, FieldDef, ReportDef, ReportResult, ReportSpec } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const DEFAULT_SPEC: ReportSpec = {
  object_type: "opportunity",
  columns: ["name", "account_id", "stage_id", "amount", "expected_close_date", "owner_user_id"],
  filters: [],
  group_by: "stage_id",
  date_granularity: null,
  aggregate: "sum",
  aggregate_field: "amount",
  sort_field: null,
  sort_direction: "desc",
  row_limit: 200,
  chart_type: "bar",
};

const CHARTS: { value: ReportSpec["chart_type"]; label: string }[] = [
  { value: "bar", label: "Bar" },
  { value: "line", label: "Line" },
  { value: "donut", label: "Donut" },
  { value: "metric", label: "Single number" },
  { value: "table", label: "Table only" },
];

function cell(value: unknown, field: FieldDef) {
  if (value === null || value === undefined || value === "") return <span className="ui-muted">—</span>;
  if (field.type === "currency") return moneyExact(Number(value));
  if (field.type === "number") return number(Number(value), 2);
  if (field.type === "date") return formatDate(String(value));
  if (field.type === "datetime") return dateTime(String(value));
  if (field.type === "bool") return value ? "Yes" : "No";
  if (field.type === "enum") return titleCase(String(value));
  return String(value);
}

function csvEscape(value: unknown) {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function ReportBuilder({ reportId }: { reportId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const { can, session } = useSession();
  const catalog = useApi<Catalog>("/meta/catalog");
  const saved = useApi<ReportDef>(reportId ? `/reports/${reportId}` : null);
  const [spec, setSpec] = useState<ReportSpec | null>(reportId ? null : DEFAULT_SPEC);
  const [meta, setMeta] = useState({ name: "", description: "", folder: "", is_shared: true });
  // New reports start shared or private as set in Settings → Team sharing & defaults.
  const sharing = useApi<{ data: { reports_shared: boolean } }>(reportId ? null : "/workspace-settings/sharing");
  useEffect(() => {
    if (!reportId && sharing.data) setMeta((current) => ({ ...current, is_shared: sharing.data!.data.reports_shared }));
  }, [reportId, sharing.data]);
  const [result, setResult] = useState<ReportResult | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [dashOpen, setDashOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);

  const canBuild = can("analytics.reports");
  // Shared reports are team property; private ones only their owner (or an admin) can change.
  const isOwner = !saved.data || saved.data.is_shared || saved.data.owner_user_id === session?.user.id || can("admin.settings");

  useEffect(() => {
    if (!saved.data) return;
    const { name, description, folder, is_shared, ...rest } = saved.data;
    setSpec({
      object_type: rest.object_type,
      columns: rest.columns,
      filters: rest.filters,
      group_by: rest.group_by,
      date_granularity: rest.date_granularity,
      aggregate: rest.aggregate,
      aggregate_field: rest.aggregate_field,
      sort_field: rest.sort_field,
      sort_direction: rest.sort_direction,
      row_limit: rest.row_limit,
      chart_type: rest.chart_type,
    });
    setMeta({ name, description: description ?? "", folder: folder ?? "", is_shared });
  }, [saved.data]);

  const object = catalog.data?.objects.find((item) => item.key === spec?.object_type);
  const fields = useMemo(() => object?.fields ?? [], [object]);
  const fieldMap = useMemo(() => new Map(fields.map((field) => [field.key, field])), [fields]);
  const groupField = spec?.group_by ? fieldMap.get(spec.group_by) : undefined;

  // Run the report whenever the definition changes (debounced).
  useEffect(() => {
    if (!spec || !catalog.data) return;
    const ready = spec.filters.every(
      (filter) => ["is_empty", "is_not_empty"].includes(filter.operator) || (filter.value !== "" && filter.value !== null && filter.value !== undefined)
    );
    if (!ready) return;
    const timer = window.setTimeout(async () => {
      setRunning(true);
      try {
        const body = { ...spec, chart_type: spec.group_by ? spec.chart_type : spec.chart_type === "metric" ? "metric" : "table" };
        const next = reportId && !canBuild ? (await api.get<{ result: ReportResult }>(`/reports/${reportId}/run`)).result : await api.post<ReportResult>("/reports/run", body);
        setResult(next);
        setRunError(null);
      } catch (caught) {
        setRunError(errorMessage(caught));
      } finally {
        setRunning(false);
      }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [canBuild, catalog.data, reportId, spec]);

  function update(changes: Partial<ReportSpec>) {
    setSpec((current) => (current ? { ...current, ...changes } : current));
  }

  function changeObject(objectType: string) {
    const definition = catalog.data?.objects.find((item) => item.key === objectType);
    const first = definition?.fields.slice(0, 5).map((field) => field.key) ?? [];
    setSpec({ ...DEFAULT_SPEC, object_type: objectType, columns: first, group_by: null, aggregate: "count", aggregate_field: null, chart_type: "table" });
  }

  async function save(asNew: boolean) {
    if (!spec) return;
    setBusy(true);
    try {
      const payload = {
        ...spec,
        chart_type: spec.group_by || spec.chart_type === "metric" ? spec.chart_type : "table",
        name: meta.name.trim(),
        description: meta.description.trim() || null,
        folder: meta.folder.trim() || null,
        is_shared: meta.is_shared,
      };
      if (reportId && !asNew) {
        await api.patch(`/reports/${reportId}`, payload);
        await saved.reload();
        toast("Report saved.");
      } else {
        const created = await api.post<ReportDef>("/reports", payload);
        toast("Report saved.");
        router.replace(`/reports/${created.id}`);
      }
      setSaveOpen(false);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!reportId) return;
    setBusy(true);
    try {
      await api.del(`/reports/${reportId}`);
      toast("Report deleted.");
      router.replace("/reports");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  function exportCsv() {
    if (!result) return;
    const lines: string[] = [];
    if (result.group_by && result.groups.length) {
      lines.push([result.group_by.label, result.aggregate === "count" ? "Count" : `${titleCase(result.aggregate)} of ${result.aggregate_field?.label}`, "Records"].map(csvEscape).join(","));
      for (const group of result.groups) lines.push([group.label, group.value, group.count].map(csvEscape).join(","));
      lines.push("");
    }
    lines.push(result.columns.map((column) => csvEscape(column.label)).join(","));
    for (const row of result.rows) lines.push(result.columns.map((column) => csvEscape(row.values[column.key])).join(","));
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${(meta.name || "aiva-report").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (saved.error) return <Alert>{saved.error}</Alert>;
  if (!spec || !catalog.data) return <div className="ui-card"><LoadingRows rows={10} /></div>;

  const aggregatable = fields.filter((field) => field.aggregatable && (field.type === "number" || field.type === "currency"));
  const groupable = fields.filter((field) => field.groupable);
  const editable = canBuild && isOwner;

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/reports" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> Reports
        </Link>
      </div>
      <div className="ui-header">
        <div style={{ minWidth: 0 }}>
          <div className="ui-eyebrow">{reportId ? meta.folder || "Report" : "New report"}</div>
          <h1>{reportId ? meta.name : `New ${object?.singular.toLowerCase() ?? ""} report`}</h1>
          <p>
            {result
              ? `${number(result.total_count)} ${result.object_label.toLowerCase()} match${result.total_count === 1 ? "es" : ""}`
              : "Pick what to report on, filter it and group it."}
            {saved.data?.owner_name ? ` · Owner ${saved.data.owner_name}` : ""}
          </p>
        </div>
        <div className="ui-actions">
          <button type="button" className="ui-btn" onClick={exportCsv} disabled={!result}>
            <Download size={15} /> Export CSV
          </button>
          {reportId && can("analytics.dashboards") ? (
            <button type="button" className="ui-btn" onClick={() => setDashOpen(true)}>
              <LayoutGrid size={15} /> Add to dashboard
            </button>
          ) : null}
          {reportId && editable ? (
            <button type="button" className="ui-btn ui-btn-danger ui-btn-icon" aria-label="Delete report" onClick={() => setDeleting(true)}>
              <Trash2 size={15} />
            </button>
          ) : null}
          {canBuild ? (
            <button
              type="button"
              className="ui-btn ui-btn-primary"
              onClick={() => {
                if (!reportId) setMeta((m) => ({ ...m, name: m.name || `${object?.label ?? "Records"} report` }));
                setSaveOpen(true);
              }}
            >
              <Save size={15} /> {reportId ? "Save" : "Save report"}
            </button>
          ) : null}
        </div>
      </div>

      <div className={canBuild ? "ui-builder" : "ui-stack"}>
        {canBuild ? (
          <div className="ui-stack report-config">
            <Card title="Data">
              <div className="ui-stack" style={{ gap: 12 }}>
                <Field label="Report on">
                  <select className="ui-select sm" value={spec.object_type} onChange={(e) => changeObject(e.target.value)}>
                    {catalog.data.objects.map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="ui-field">
                  <span className="ui-label">Filters</span>
                  {spec.filters.map((filter, index) => {
                    const field = fieldMap.get(filter.field);
                    const ops = operatorsFor(field, catalog.data!.operators);
                    return (
                      <div key={index} style={{ display: "grid", gap: 6, padding: 10, border: "1px solid var(--border)", borderRadius: 10 }}>
                        <div className="ui-row">
                          <select
                            className="ui-select sm"
                            value={filter.field}
                            onChange={(e) => {
                              const nextField = fieldMap.get(e.target.value);
                              const nextOps = operatorsFor(nextField, catalog.data!.operators);
                              const filters = [...spec.filters];
                              filters[index] = { field: e.target.value, operator: nextOps[0], value: "" };
                              update({ filters });
                            }}
                            aria-label="Filter field"
                          >
                            {fields.map((item) => (
                              <option key={item.key} value={item.key}>
                                {item.label}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                            aria-label="Remove filter"
                            onClick={() => update({ filters: spec.filters.filter((_, i) => i !== index) })}
                          >
                            <X size={14} />
                          </button>
                        </div>
                        <select
                          className="ui-select sm"
                          value={filter.operator}
                          onChange={(e) => {
                            const filters = [...spec.filters];
                            filters[index] = { ...filter, operator: e.target.value, value: "" };
                            update({ filters });
                          }}
                          aria-label="Condition"
                        >
                          {ops.map((op) => (
                            <option key={op} value={op}>
                              {operatorLabel(op, field)}
                            </option>
                          ))}
                        </select>
                        <ValueInput
                          field={field}
                          operator={filter.operator}
                          value={String(filter.value ?? "")}
                          onChange={(value) => {
                            const filters = [...spec.filters];
                            filters[index] = { ...filter, value };
                            update({ filters });
                          }}
                        />
                      </div>
                    );
                  })}
                  <button
                    type="button"
                    className="ui-btn ui-btn-sm"
                    onClick={() => {
                      const field = fields[0];
                      update({ filters: [...spec.filters, { field: field.key, operator: operatorsFor(field, catalog.data!.operators)[0], value: "" }] });
                    }}
                  >
                    <Plus size={14} /> Add filter
                  </button>
                </div>
              </div>
            </Card>

            <Card title="Summarize">
              <div className="ui-stack" style={{ gap: 12 }}>
                <Field label="Group by">
                  <select
                    className="ui-select sm"
                    value={spec.group_by ?? ""}
                    onChange={(e) => {
                      const next = fieldMap.get(e.target.value);
                      update({
                        group_by: e.target.value || null,
                        date_granularity: next && (next.type === "date" || next.type === "datetime") ? spec.date_granularity ?? "month" : null,
                        chart_type: e.target.value ? (spec.chart_type === "table" || spec.chart_type === "metric" ? "bar" : spec.chart_type) : "table",
                      });
                    }}
                  >
                    <option value="">No grouping</option>
                    {groupable.map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </Field>
                {groupField && (groupField.type === "date" || groupField.type === "datetime") ? (
                  <Field label="By">
                    <select className="ui-select sm" value={spec.date_granularity ?? "month"} onChange={(e) => update({ date_granularity: e.target.value })}>
                      {["day", "week", "month", "quarter", "year"].map((item) => (
                        <option key={item} value={item}>
                          {titleCase(item)}
                        </option>
                      ))}
                    </select>
                  </Field>
                ) : null}
                <div className="ui-form-grid" style={{ gap: 10 }}>
                  <Field label="Measure">
                    <select
                      className="ui-select sm"
                      value={spec.aggregate}
                      onChange={(e) => {
                        const aggregate = e.target.value as ReportSpec["aggregate"];
                        update({ aggregate, aggregate_field: aggregate === "count" ? null : spec.aggregate_field ?? aggregatable[0]?.key ?? null });
                      }}
                    >
                      <option value="count">Count</option>
                      {aggregatable.length ? (
                        <>
                          <option value="sum">Sum</option>
                          <option value="avg">Average</option>
                          <option value="min">Minimum</option>
                          <option value="max">Maximum</option>
                        </>
                      ) : null}
                    </select>
                  </Field>
                  {spec.aggregate !== "count" ? (
                    <Field label="Of">
                      <select className="ui-select sm" value={spec.aggregate_field ?? ""} onChange={(e) => update({ aggregate_field: e.target.value })}>
                        {aggregatable.map((item) => (
                          <option key={item.key} value={item.key}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ) : null}
                </div>
                <Field label="Chart">
                  <select className="ui-select sm" value={spec.chart_type} onChange={(e) => update({ chart_type: e.target.value as ReportSpec["chart_type"] })}>
                    {CHARTS.filter((chart) => spec.group_by || chart.value === "table" || chart.value === "metric").map((chart) => (
                      <option key={chart.value} value={chart.value}>
                        {chart.label}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
            </Card>

            <Card title="Columns">
              <div style={{ display: "grid", gap: 6, maxHeight: 260, overflowY: "auto", paddingBottom: 6, borderBottom: "1px solid var(--border)" }}>
                {fields.map((field) => (
                  <label key={field.key} className="ui-check">
                    <input
                      type="checkbox"
                      checked={spec.columns.includes(field.key)}
                      onChange={(e) =>
                        update({
                          columns: e.target.checked ? [...spec.columns, field.key] : spec.columns.filter((key) => key !== field.key),
                        })
                      }
                    />
                    {field.label}
                  </label>
                ))}
              </div>
              <div className="ui-form-grid" style={{ gap: 10, marginTop: 12 }}>
                <Field label="Sort by">
                  <select className="ui-select sm" value={spec.sort_field ?? ""} onChange={(e) => update({ sort_field: e.target.value || null })}>
                    <option value="">Newest first</option>
                    {fields.map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Order">
                  <select className="ui-select sm" value={spec.sort_direction} onChange={(e) => update({ sort_direction: e.target.value as "asc" | "desc" })}>
                    <option value="desc">Descending</option>
                    <option value="asc">Ascending</option>
                  </select>
                </Field>
              </div>
            </Card>
          </div>
        ) : null}

        <div className="ui-stack" style={{ minWidth: 0 }}>
          {runError ? <Alert>{runError}</Alert> : null}
          <Card
            title={
              result?.group_by
                ? `${result.aggregate === "count" ? "Count" : `${titleCase(result.aggregate)} of ${result.aggregate_field?.label ?? ""}`} by ${result.group_by.label.toLowerCase()}`
                : "Summary"
            }
            actions={running ? <span className="ui-spinner" style={{ color: "var(--muted)" }} /> : null}
          >
            {result ? <ReportVisual result={result} chart={spec.group_by ? spec.chart_type : "metric"} /> : <Skeleton height={220} />}
          </Card>

          {result?.group_by && result.groups.length ? (
            <Card title="Groups" flush>
              <div className="ui-table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
                <table className="ui-table">
                  <thead>
                    <tr>
                      <th>{result.group_by.label}</th>
                      <th className="ui-num">{result.aggregate === "count" ? "Records" : `${titleCase(result.aggregate)} of ${result.aggregate_field?.label}`}</th>
                      {result.aggregate !== "count" ? <th className="ui-num">Records</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {result.groups.map((group, index) => (
                      <tr key={`${group.key}-${index}`}>
                        <td>{group.label}</td>
                        <td className="ui-num ui-strong">{formatValue(group.value, reportFormat(result), false)}</td>
                        {result.aggregate !== "count" ? <td className="ui-num">{number(group.count)}</td> : null}
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td>Total</td>
                      <td className="ui-num">{formatValue(result.summary.value, reportFormat(result), false)}</td>
                      {result.aggregate !== "count" ? <td className="ui-num">{number(result.summary.count)}</td> : null}
                    </tr>
                  </tfoot>
                </table>
              </div>
            </Card>
          ) : null}

          <Card title={`Records${result ? ` (${number(Math.min(result.rows.length, result.total_count))} of ${number(result.total_count)})` : ""}`} flush>
            {result ? (
              result.rows.length ? (
                <div className="ui-table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
                  <table className="ui-table">
                    <thead>
                      <tr>
                        {result.columns.map((column) => (
                          <th key={column.key} className={column.type === "currency" || column.type === "number" ? "ui-num" : undefined}>
                            {column.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.rows.map((row) => (
                        <tr key={row.id}>
                          {result.columns.map((column, index) => (
                            <td key={column.key} className={column.type === "currency" || column.type === "number" ? "ui-num" : undefined}>
                              {index === 0 && row.link ? (
                                <Link href={row.link} className="cell-title">
                                  {cell(row.values[column.key], column)}
                                </Link>
                              ) : (
                                <Fragment>{cell(row.values[column.key], column)}</Fragment>
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="ui-muted ui-small" style={{ padding: 18 }}>
                  No records match these filters.
                </div>
              )
            ) : (
              <LoadingRows rows={5} />
            )}
          </Card>
        </div>
      </div>

      <Modal
        open={saveOpen}
        title={reportId ? "Save report" : "Save new report"}
        onClose={() => setSaveOpen(false)}
        footer={
          <>
            {reportId ? (
              <button type="button" className="ui-btn" style={{ marginRight: "auto" }} onClick={() => void save(true)} disabled={busy || !meta.name.trim()}>
                Save as copy
              </button>
            ) : null}
            <button type="button" className="ui-btn" onClick={() => setSaveOpen(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save(false)} disabled={busy || !meta.name.trim() || (Boolean(reportId) && !isOwner)}>
              Save
            </button>
          </>
        }
      >
        <div className="ui-stack">
          {reportId && !isOwner ? <Alert tone="info">This report belongs to someone else. Save a copy to keep your changes.</Alert> : null}
          <Field label="Name" required>
            <input className="ui-input" value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} />
          </Field>
          <Field label="Description">
            <input className="ui-input" value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} />
          </Field>
          <Field label="Folder" hint="Reports are grouped by folder.">
            <input className="ui-input" value={meta.folder} onChange={(e) => setMeta({ ...meta, folder: e.target.value })} placeholder="Sales" />
          </Field>
          <label className="ui-check">
            <input type="checkbox" checked={meta.is_shared} onChange={(e) => setMeta({ ...meta, is_shared: e.target.checked })} />
            Share with the whole workspace
          </label>
        </div>
      </Modal>

      {reportId ? <AddToDashboard open={dashOpen} reportId={reportId} onClose={() => setDashOpen(false)} /> : null}

      <ConfirmDialog
        open={deleting}
        title="Delete this report?"
        message="It's also removed from any dashboards that show it."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}

function AddToDashboard({ open, reportId, onClose }: { open: boolean; reportId: string; onClose: () => void }) {
  const toast = useToast();
  const router = useRouter();
  const dashboards = useApi<DashboardDef[]>(open ? "/dashboards" : null);
  const [target, setTarget] = useState("");
  const [size, setSize] = useState<"small" | "medium" | "large">("medium");
  const [busy, setBusy] = useState(false);

  async function add() {
    const dashboard = dashboards.data?.find((item) => item.id === target);
    if (!dashboard) return;
    setBusy(true);
    try {
      await api.put(`/dashboards/${dashboard.id}`, {
        name: dashboard.name,
        description: dashboard.description,
        is_shared: dashboard.is_shared,
        widgets: [...dashboard.widgets.map(({ id, report_id, title, size: s }) => ({ id, report_id, title, size: s })), { report_id: reportId, size }],
      });
      toast(`Added to ${dashboard.name}.`);
      onClose();
      router.push(`/dashboards/${dashboard.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      size="sm"
      title="Add to dashboard"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void add()} disabled={!target || busy}>
            Add
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <Field label="Dashboard">
          <select className="ui-select" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose…</option>
            {(dashboards.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Size">
          <select className="ui-select" value={size} onChange={(e) => setSize(e.target.value as typeof size)}>
            <option value="small">Small (one third)</option>
            <option value="medium">Medium (half)</option>
            <option value="large">Large (full width)</option>
          </select>
        </Field>
      </div>
    </Modal>
  );
}
