"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Download, Plus, Search, Settings2 } from "lucide-react";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { FieldValue, type CustomFieldDef } from "@/components/custom-fields";
import { useSession, useToast } from "@/components/providers";
import { ModuleIcon, RecordWizard } from "@/components/record-wizard";
import { Alert, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { CustomModuleSummary } from "@/lib/modules";
import { api, query, useApi, useDebounced } from "@/lib/use-api";

interface RecordRow {
  id: string;
  name: string;
  owner_name: string | null;
  values: Record<string, unknown>;
  labels: Record<string, string>;
  created_at: string;
  updated_at: string;
}

interface RecordList {
  module: CustomModuleSummary;
  fields: CustomFieldDef[];
  items: RecordRow[];
  total: number;
  page: number;
  page_size: number;
}

export default function ModuleRecordsPage() {
  return (
    <AppShell>
      <ModuleRecords />
    </AppShell>
  );
}

function ModuleRecords() {
  const { moduleKey } = useParams<{ moduleKey: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can, has } = useSession();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  const list = useApi<RecordList>(`/modules/${moduleKey}/records${query({ q: q || undefined, page, page_size: 50 })}`);
  const [creating, setCreating] = useState(false);
  const d = list.data;
  const columns = (d?.fields ?? []).filter((f) => f.is_active && f.field_type !== "textarea").slice(0, 5);
  const pages = d ? Math.max(1, Math.ceil(d.total / d.page_size)) : 1;

  if (list.error && !d) return <Alert>{list.error}</Alert>;

  return (
    <>
      <PageHeader
        eyebrow={d ? <ModuleIcon icon={d.module.icon} size={14} /> : undefined}
        title={d?.module.name ?? "Loading…"}
        description={d?.module.description ?? undefined}
        actions={
          <>
            {can("admin.settings") ? (
              <Link className="ui-btn" href={`/settings/fields?object=${moduleKey}`}>
                <Settings2 size={15} /> Fields
              </Link>
            ) : null}
            {has("data_import_export") && can("records.export") ? (
              <a className="ui-btn" href={`/api/aiva/data/export/${moduleKey}.csv`}>
                <Download size={15} /> Export
              </a>
            ) : null}
            {can("records.write") && d?.module.is_active ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
                <Plus size={16} /> New {d.module.singular.toLowerCase()}
              </button>
            ) : null}
          </>
        }
      />
      {d && !d.module.is_active ? (
        <div style={{ marginBottom: 14 }}>
          <Alert tone="warning">This module is turned off. Records are read-only until an admin turns it back on.</Alert>
        </div>
      ) : null}
      <Card
        flush
        title={d ? `${d.total} ${d.total === 1 ? d.module.singular.toLowerCase() : d.module.name.toLowerCase()}` : undefined}
        actions={
          <div className="search-input">
            <Search size={15} />
            <input
              className="ui-input sm"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              placeholder="Search by name"
              aria-label="Search"
            />
          </div>
        }
        footer={
          pages > 1 ? (
            <>
              <span className="ui-muted ui-small" style={{ marginRight: "auto" }}>
                Page {page} of {pages}
              </span>
              <button type="button" className="ui-btn ui-btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Previous
              </button>
              <button type="button" className="ui-btn ui-btn-sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>
                Next
              </button>
            </>
          ) : undefined
        }
      >
        {list.loading && !d ? (
          <LoadingRows />
        ) : d?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Name</th>
                  {columns.map((field) => (
                    <th key={field.key}>{field.label}</th>
                  ))}
                  <th>Owner</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((row) => (
                  <tr key={row.id} className="clickable-row" onClick={() => router.push(`/modules/${moduleKey}/${row.id}`)}>
                    <td className="cell-title">
                      <Link href={`/modules/${moduleKey}/${row.id}`} onClick={(e) => e.stopPropagation()}>
                        {row.name}
                      </Link>
                    </td>
                    {columns.map((field) => (
                      <td key={field.key}>
                        <FieldValue field={field} value={row.values[field.key]} label={row.labels[field.key]} />
                      </td>
                    ))}
                    <td>{row.owner_name ?? "—"}</td>
                    <td className="ui-muted">{relativeTime(row.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={d ? <ModuleIcon icon={d.module.icon} size={20} /> : undefined}
            title={q ? "No matches" : `No ${d?.module.name.toLowerCase() ?? "records"} yet`}
            message={q ? "Try a different name." : undefined}
          />
        )}
      </Card>
      {d ? (
        <RecordWizard
          open={creating}
          title={`New ${d.module.singular.toLowerCase()}`}
          singular={d.module.singular}
          fields={d.fields}
          onClose={() => setCreating(false)}
          onSave={async (name, values) => {
            const record = await api.post<{ id: string }>(`/modules/${moduleKey}/records`, { name, values });
            toast(`${d.module.singular} created.`);
            setCreating(false);
            router.push(`/modules/${moduleKey}/${record.id}`);
          }}
        />
      ) : null}
    </>
  );
}
