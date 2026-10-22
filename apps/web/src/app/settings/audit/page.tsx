"use client";

import { ChevronDown, ChevronRight, Download, Search } from "lucide-react";
import { Fragment, useState } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { Alert, Avatar, Badge, LoadingRows, PageHeader, type Tone } from "@/components/ui";
import { dateTime, number, relativeTime, titleCase } from "@/lib/format";
import type { AuditLog, Member, Paged } from "@/lib/platform-types";
import { query, useApi, useDebounced } from "@/lib/use-api";

const ACTION_LABELS: Record<string, { label: string; tone: Tone }> = {
  create: { label: "Created", tone: "success" },
  update: { label: "Updated", tone: "info" },
  delete: { label: "Deleted", tone: "danger" },
  login: { label: "Signed in", tone: "neutral" },
  logout: { label: "Signed out", tone: "neutral" },
  login_failed: { label: "Failed sign-in", tone: "warning" },
  account_locked: { label: "Account locked", tone: "danger" },
  signup: { label: "Created workspace", tone: "primary" },
  invite: { label: "Invited user", tone: "primary" },
  invite_accepted: { label: "Accepted invite", tone: "primary" },
  member_update: { label: "Changed access", tone: "warning" },
  member_removed: { label: "Removed user", tone: "danger" },
  password_changed: { label: "Changed password", tone: "neutral" },
  password_reset: { label: "Reset password", tone: "warning" },
  password_reset_sent: { label: "Sent reset link", tone: "neutral" },
  mfa_enabled: { label: "Turned on 2-step", tone: "success" },
  mfa_disabled: { label: "Turned off 2-step", tone: "danger" },
  settings_update: { label: "Changed settings", tone: "warning" },
  recalculate: { label: "Recalculated", tone: "neutral" },
};

const ENTITY_TYPES = [
  "account",
  "contact",
  "lead",
  "opportunity",
  "task",
  "activity",
  "case",
  "quote",
  "product",
  "kb_article",
  "report",
  "dashboard",
  "workflow",
  "scoring_rule",
  "sla_policy",
  "role",
  "team",
  "user",
  "organization",
];

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Changes({ changes }: { changes: Record<string, unknown> | null }) {
  if (!changes || !Object.keys(changes).length) return <span className="ui-muted ui-small">No field details recorded.</span>;
  return (
    <div className="ui-diff">
      {Object.entries(changes).map(([field, value]) => {
        const pair = value && typeof value === "object" && ("from" in value || "to" in value) ? (value as { from?: unknown; to?: unknown }) : null;
        return (
          <Fragment key={field}>
            <span className="field">{titleCase(field)}</span>
            <span style={{ minWidth: 0, wordBreak: "break-word" }}>
              {pair ? (
                <>
                  <del>{show(pair.from)}</del> → <ins>{show(pair.to)}</ins>
                </>
              ) : (
                show(value)
              )}
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}

export default function AuditPage() {
  return (
    <SettingsShell permission="admin.audit" what="the audit log">
      <AuditLogView />
    </SettingsShell>
  );
}

function AuditLogView() {
  const [search, setSearch] = useState("");
  const [action, setAction] = useState("");
  const [entityType, setEntityType] = useState("");
  const [actor, setActor] = useState("");
  const [since, setSince] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const term = useDebounced(search.trim(), 300);
  const members = useApi<Member[]>("/meta/members");
  const logs = useApi<Paged<AuditLog>>(
    `/admin/audit-logs${query({
      search: term,
      action,
      entity_type: entityType,
      actor_user_id: actor,
      since: since ? new Date(`${since}T00:00:00`).toISOString() : null,
      page,
      page_size: 50,
    })}`
  );
  const pages = logs.data ? Math.max(1, Math.ceil(logs.data.total / logs.data.page_size)) : 1;

  function reset<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value);
      setPage(1);
    };
  }

  function exportCsv() {
    const rows = logs.data?.items ?? [];
    const header = ["Time", "User", "Action", "Record type", "Record", "IP address", "Changes"];
    const lines = rows.map((row) =>
      [row.created_at, row.actor_name ?? "System", row.action, row.entity_type, row.entity_label ?? "", row.ip_address ?? "", JSON.stringify(row.changes ?? {})]
        .map((cell) => `"${String(cell).replace(/"/g, '""')}"`)
        .join(",")
    );
    const blob = new Blob([[header.join(","), ...lines].join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `aiva-audit-log-page-${page}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every sign-in, permission change and record edit, with who did it and from where."
        actions={
          <button type="button" className="ui-btn" onClick={exportCsv} disabled={!logs.data?.items.length}>
            <Download size={15} /> Export page
          </button>
        }
      />
      <div className="ui-toolbar">
        <div className="ui-search">
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search records" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        </div>
        <select className="ui-select sm" style={{ width: 170 }} value={action} onChange={(e) => reset(setAction)(e.target.value)} aria-label="Action">
          <option value="">All actions</option>
          {Object.entries(ACTION_LABELS).map(([key, value]) => (
            <option key={key} value={key}>
              {value.label}
            </option>
          ))}
        </select>
        <select className="ui-select sm" style={{ width: 160 }} value={entityType} onChange={(e) => reset(setEntityType)(e.target.value)} aria-label="Record type">
          <option value="">All record types</option>
          {ENTITY_TYPES.map((type) => (
            <option key={type} value={type}>
              {titleCase(type === "kb_article" ? "knowledge article" : type)}
            </option>
          ))}
        </select>
        <select className="ui-select sm" style={{ width: 170 }} value={actor} onChange={(e) => reset(setActor)(e.target.value)} aria-label="User">
          <option value="">Everyone</option>
          {(members.data ?? []).map((member) => (
            <option key={member.id} value={member.id}>
              {member.full_name}
            </option>
          ))}
        </select>
        <input className="ui-input sm" type="date" style={{ width: 150 }} value={since} onChange={(e) => reset(setSince)(e.target.value)} aria-label="Since" />
      </div>

      {logs.error ? <Alert>{logs.error}</Alert> : null}

      <div className="ui-card">
        {logs.loading && !logs.data ? (
          <LoadingRows rows={10} />
        ) : (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th style={{ width: 28 }} />
                  <th>When</th>
                  <th>User</th>
                  <th>Action</th>
                  <th>Record</th>
                  <th>IP address</th>
                </tr>
              </thead>
              <tbody>
                {(logs.data?.items ?? []).map((row) => {
                  const meta = ACTION_LABELS[row.action] ?? { label: titleCase(row.action), tone: "neutral" as Tone };
                  const expanded = open.has(row.id);
                  return (
                    <Fragment key={row.id}>
                      <tr
                        className="clickable"
                        onClick={() => {
                          const next = new Set(open);
                          if (expanded) next.delete(row.id);
                          else next.add(row.id);
                          setOpen(next);
                        }}
                      >
                        <td className="ui-muted">{expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</td>
                        <td className="ui-nowrap" title={dateTime(row.created_at)}>
                          {relativeTime(row.created_at)}
                        </td>
                        <td>
                          <div className="ui-row">
                            <Avatar name={row.actor_name ?? "System"} size={24} />
                            <span className="ui-nowrap">{row.actor_name ?? "System"}</span>
                          </div>
                        </td>
                        <td>
                          <Badge tone={meta.tone}>{meta.label}</Badge>
                        </td>
                        <td>
                          <div className="cell-title">{row.entity_label ?? "—"}</div>
                          <div className="cell-sub">{titleCase(row.entity_type === "kb_article" ? "knowledge article" : row.entity_type)}</div>
                        </td>
                        <td className="ui-mono ui-muted">{row.ip_address ?? "—"}</td>
                      </tr>
                      {expanded ? (
                        <tr>
                          <td />
                          <td colSpan={5} style={{ background: "var(--surface-muted)" }}>
                            <Changes changes={row.changes} />
                            <div className="ui-muted ui-small" style={{ marginTop: 8 }}>
                              {dateTime(row.created_at)}
                              {row.user_agent ? ` · ${row.user_agent}` : ""}
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
                {!logs.data?.items.length ? (
                  <tr>
                    <td colSpan={6} className="ui-muted" style={{ textAlign: "center", padding: 30 }}>
                      No events match these filters.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}
        {logs.data && logs.data.total > logs.data.page_size ? (
          <div className="ui-card-footer" style={{ justifyContent: "space-between" }}>
            <span className="ui-muted ui-small">
              {number(logs.data.total)} events · page {page} of {pages}
            </span>
            <div className="ui-actions">
              <button type="button" className="ui-btn ui-btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Previous
              </button>
              <button type="button" className="ui-btn ui-btn-sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </>
  );
}
