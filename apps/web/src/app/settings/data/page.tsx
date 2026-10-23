"use client";

import { Download, FileUp, History, Upload } from "lucide-react";
import { Fragment, useRef, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, Field, LoadingRows, PageHeader, Tabs, type Tone } from "@/components/ui";
import { dateTime, titleCase } from "@/lib/format";
import type { CustomModuleSummary } from "@/lib/modules";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Preview {
  headers: string[];
  sample: string[][];
  total_rows: number;
  fields: { key: string; label: string; required: boolean }[];
  mapping: Record<string, string | null>;
}

interface ImportResult {
  id: string;
  object_type: string;
  filename: string | null;
  status: "completed" | "partial" | "failed";
  total_rows: number;
  created: number;
  updated: number;
  skipped: number;
  errors: number;
  error_rows: { row: number; message: string }[];
  created_by?: string | null;
  created_at: string;
}

const EXPORTS = [
  { key: "lead", label: "Leads" },
  { key: "contact", label: "Contacts" },
  { key: "account", label: "Accounts" },
  { key: "opportunity", label: "Opportunities" },
  { key: "case", label: "Cases" },
  { key: "task", label: "Tasks" },
  { key: "activity", label: "Activities" },
  { key: "product", label: "Products", feature: "cpq" },
  { key: "quote", label: "Quotes", feature: "cpq" },
];

const STATUS_TONE: Record<ImportResult["status"], Tone> = { completed: "success", partial: "warning", failed: "danger" };

export default function DataPage() {
  return (
    <SettingsShell feature="data_import_export">
      <DataSettings />
    </SettingsShell>
  );
}

function DataSettings() {
  const { can } = useSession();
  const [view, setView] = useState<"import" | "export" | "history">(can("data.import") ? "import" : "export");
  return (
    <>
      <PageHeader
        title="Import and export"
        description="Bring leads, contacts and accounts in from a spreadsheet, or download any record type as CSV."
      />
      <div style={{ marginBottom: 14 }}>
        <Tabs
          label="Data"
          value={view}
          onChange={setView}
          items={[
            ...(can("data.import") ? [{ value: "import" as const, label: "Import" }] : []),
            { value: "export", label: "Export" },
            { value: "history", label: "Import history" },
          ]}
        />
      </div>
      {view === "import" ? <ImportWizard onDone={() => setView("history")} /> : view === "export" ? <Exports /> : <ImportHistory />}
    </>
  );
}

function ImportWizard({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [objectType, setObjectType] = useState<"lead" | "contact" | "account">("lead");
  const [csv, setCsv] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [duplicates, setDuplicates] = useState<"skip" | "update" | "create">("skip");
  const [source, setSource] = useState("import");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  async function load(file: File) {
    setError(null);
    setResult(null);
    if (file.size > 5 * 1024 * 1024) {
      setError("Files can be up to 5 MB. Split larger files.");
      return;
    }
    const text = await file.text();
    setCsv(text);
    setFilename(file.name);
    await runPreview(text, file.name, objectType);
  }

  async function runPreview(text: string, name: string | null, type: string) {
    setBusy(true);
    try {
      const next = await api.post<Preview>("/data/import/preview", { object_type: type, csv: text, filename: name });
      setPreview(next);
      setMapping(next.mapping);
    } catch (caught) {
      setError(errorMessage(caught));
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const next = await api.post<ImportResult>("/data/import", {
        object_type: objectType,
        csv,
        filename,
        mapping,
        duplicate_action: duplicates,
        default_source: objectType === "lead" ? source || null : null,
      });
      setResult(next);
      toast(`Imported ${next.created} new and updated ${next.updated} ${objectType}s.`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setCsv("");
    setFilename(null);
    setPreview(null);
    setResult(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  if (result) {
    return (
      <Card title="Import finished" icon={<Upload size={16} />}>
        <div className="ui-stack">
          <div className="stat-strip">
            <div className="billing-stat">
              <span>Created</span>
              <strong>{result.created}</strong>
            </div>
            <div className="billing-stat">
              <span>Updated</span>
              <strong>{result.updated}</strong>
            </div>
            <div className="billing-stat">
              <span>Skipped duplicates</span>
              <strong>{result.skipped}</strong>
            </div>
            <div className="billing-stat">
              <span>Rows with problems</span>
              <strong>{result.errors}</strong>
            </div>
          </div>
          <ErrorRows rows={result.error_rows} />
          <div className="ui-row" style={{ gap: 8 }}>
            <button type="button" className="ui-btn ui-btn-primary" onClick={reset}>
              Import another file
            </button>
            <button type="button" className="ui-btn" onClick={onDone}>
              See import history
            </button>
          </div>
        </div>
      </Card>
    );
  }

  const mapped = new Set(Object.values(mapping).filter(Boolean));

  return (
    <div className="ui-stack">
      <Card title="1. Choose a file" icon={<FileUp size={16} />}>
        <div className="ui-form-grid">
          <Field label="Import as">
            <select
              className="ui-select"
              value={objectType}
              onChange={(e) => {
                const next = e.target.value as "lead" | "contact" | "account";
                setObjectType(next);
                if (csv) void runPreview(csv, filename, next);
              }}
            >
              <option value="lead">Leads</option>
              <option value="contact">Contacts</option>
              <option value="account">Accounts</option>
            </select>
          </Field>
          <Field label="CSV file" hint="Up to 5 MB and 10,000 rows. The first row must be column names.">
            <input
              ref={fileInput}
              className="ui-input"
              type="file"
              accept=".csv,text/csv,.tsv,text/tab-separated-values,.txt"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void load(file);
              }}
            />
          </Field>
        </div>
        {objectType === "contact" ? <p className="ui-muted ui-small">A column named Company or Account links each contact to a matching account, creating it when needed.</p> : null}
      </Card>
      {error ? <Alert>{error}</Alert> : null}
      {busy && !preview ? (
        <Card>
          <LoadingRows rows={4} />
        </Card>
      ) : null}
      {preview ? (
        <Card title={`2. Match columns · ${preview.total_rows.toLocaleString()} rows`} flush>
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Column in your file</th>
                  <th>Sample values</th>
                  <th style={{ width: 260 }}>AIVA field</th>
                </tr>
              </thead>
              <tbody>
                {preview.headers.map((header, index) => (
                  <tr key={header}>
                    <td className="cell-title">{header}</td>
                    <td className="ui-muted ui-small">
                      {preview.sample
                        .map((row) => row[index])
                        .filter(Boolean)
                        .slice(0, 3)
                        .join(" · ") || "—"}
                    </td>
                    <td>
                      <select
                        className="ui-select sm"
                        value={mapping[header] ?? ""}
                        onChange={(e) => setMapping({ ...mapping, [header]: e.target.value || null })}
                        aria-label={`Field for ${header}`}
                      >
                        <option value="">Don&apos;t import</option>
                        {preview.fields.map((f) => (
                          <option key={f.key} value={f.key} disabled={mapped.has(f.key) && mapping[header] !== f.key}>
                            {f.label}
                            {f.required ? " *" : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
      {preview ? (
        <Card title="3. Import">
          <div className="ui-stack">
            <div className="ui-form-grid">
              <Field label={objectType === "account" ? "When an account with the same name exists" : "When the email already exists"}>
                <select className="ui-select" value={duplicates} onChange={(e) => setDuplicates(e.target.value as "skip" | "update" | "create")}>
                  <option value="skip">Skip the row</option>
                  <option value="update">Update the existing record</option>
                  <option value="create">Create a duplicate anyway</option>
                </select>
              </Field>
              {objectType === "lead" ? (
                <Field label="Lead source for rows without one">
                  <input className="ui-input" value={source} onChange={(e) => setSource(e.target.value)} />
                </Field>
              ) : null}
            </div>
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Workflows, lead assignment and scoring run on every imported record. Records without an Owner column are assigned to you.
            </p>
            <div className="ui-row" style={{ gap: 8 }}>
              <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void run()}>
                <Upload size={15} /> Import {preview.total_rows.toLocaleString()} rows
              </button>
              <button type="button" className="ui-btn" onClick={reset}>
                Start over
              </button>
            </div>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function ErrorRows({ rows }: { rows: { row: number; message: string }[] }) {
  if (!rows.length) return null;
  return (
    <div className="import-errors">
      <strong className="ui-small">Rows that weren&apos;t imported</strong>
      <ul>
        {rows.slice(0, 50).map((row) => (
          <li key={`${row.row}-${row.message}`}>
            Row {row.row}: {row.message}
          </li>
        ))}
      </ul>
      {rows.length > 50 ? <span className="ui-muted ui-small">…and {rows.length - 50} more.</span> : null}
    </div>
  );
}

function Exports() {
  const { has, can } = useSession();
  const modules = useApi<CustomModuleSummary[]>("/custom-modules");
  if (!can("records.export")) return <Alert tone="info">Your role doesn&apos;t include exporting records.</Alert>;
  const items = [...EXPORTS.filter((e) => !e.feature || has(e.feature)), ...(modules.data ?? []).map((m) => ({ key: m.key, label: m.name }))];
  return (
    <Card title="Download as CSV" icon={<Download size={16} />}>
      <div className="export-grid">
        {items.map((item) => (
          <a key={item.key} className="export-tile" href={`/api/aiva/data/export/${item.key}.csv`}>
            <Download size={15} />
            <span>{item.label}</span>
          </a>
        ))}
      </div>
      <p className="ui-muted ui-small" style={{ marginBottom: 0 }}>
        Exports include custom fields. Encrypted values are exported only for people allowed to view them. Every export is written to the audit log.
      </p>
    </Card>
  );
}

function ImportHistory() {
  const jobs = useApi<ImportResult[]>("/data/imports");
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card title="Recent imports" icon={<History size={16} />} flush>
      {jobs.error ? <Alert>{jobs.error}</Alert> : null}
      {!jobs.data ? (
        <LoadingRows />
      ) : jobs.data.length ? (
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>File</th>
                <th>Type</th>
                <th>Result</th>
                <th>By</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((job) => (
                <Fragment key={job.id}>
                  <tr className={job.error_rows.length ? "clickable-row" : undefined} onClick={() => setOpen(open === job.id ? null : job.id)}>
                    <td className="cell-title">{job.filename ?? "Pasted data"}</td>
                    <td>{titleCase(job.object_type)}s</td>
                    <td>
                      <Badge tone={STATUS_TONE[job.status] ?? "neutral"}>{job.status}</Badge>{" "}
                      <span className="ui-small">
                        {job.created} new · {job.updated} updated · {job.skipped} skipped{job.errors ? ` · ${job.errors} problems` : ""}
                      </span>
                    </td>
                    <td>{job.created_by ?? "—"}</td>
                    <td className="ui-muted">{dateTime(job.created_at)}</td>
                  </tr>
                  {open === job.id && job.error_rows.length ? (
                    <tr>
                      <td colSpan={5}>
                        <ErrorRows rows={job.error_rows} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty-hint">No imports yet.</p>
      )}
    </Card>
  );
}
