"use client";

import Link from "next/link";
import { BarChart3, Folder, Gauge, LineChart, PieChart, Plus, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession } from "@/components/providers";
import { Alert, Badge, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { Catalog, ReportDef } from "@/lib/platform-types";
import { useApi } from "@/lib/use-api";

const CHART_ICON = { bar: BarChart3, line: LineChart, donut: PieChart, metric: Gauge, table: Table2 } as const;

export default function ReportsPage() {
  return (
    <AppShell>
      <Reports />
    </AppShell>
  );
}

function Reports() {
  const { can } = useSession();
  const reports = useApi<ReportDef[]>("/reports");
  const catalog = useApi<Catalog>("/meta/catalog");
  const [search, setSearch] = useState("");

  const objectLabel = (key: string) => catalog.data?.objects.find((object) => object.key === key)?.label ?? key;

  const folders = useMemo(() => {
    const term = search.trim().toLowerCase();
    const map = new Map<string, ReportDef[]>();
    for (const report of reports.data ?? []) {
      if (term && !report.name.toLowerCase().includes(term) && !(report.description ?? "").toLowerCase().includes(term)) continue;
      const folder = report.folder || "Unfiled";
      map.set(folder, [...(map.get(folder) ?? []), report]);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [reports.data, search]);

  return (
    <>
      <PageHeader
        title="Reports"
        description="Ask any question of your CRM data: filter, group and chart it, then pin it to a dashboard."
        actions={
          can("analytics.reports") ? (
            <Link href="/reports/new" className="ui-btn ui-btn-primary">
              <Plus size={16} /> New report
            </Link>
          ) : null
        }
      />
      <div className="ui-toolbar">
        <div className="ui-search" style={{ flexBasis: 360 }}>
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search reports" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>
      {reports.error ? <Alert>{reports.error}</Alert> : null}
      {reports.loading && !reports.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : folders.length ? (
        <div className="ui-stack">
          {folders.map(([folder, items]) => (
            <section key={folder} className="ui-card">
              <div className="ui-card-header">
                <h2>
                  <Folder size={16} /> {folder}
                </h2>
                <span className="ui-muted ui-small">{items.length}</span>
              </div>
              <div className="ui-list">
                {items.map((report) => {
                  const Icon = CHART_ICON[report.chart_type] ?? Table2;
                  return (
                    <Link key={report.id} href={`/reports/${report.id}`} className="ui-list-item">
                      <span className="ui-avatar" style={{ borderRadius: 9, width: 34, height: 34 }}>
                        <Icon size={16} />
                      </span>
                      <div className="main">
                        <div className="title">{report.name}</div>
                        <div className="sub">
                          {objectLabel(report.object_type)}
                          {report.description ? ` · ${report.description}` : ""}
                        </div>
                      </div>
                      {!report.is_shared ? <Badge>Private</Badge> : null}
                      <span className="ui-muted ui-small ui-nowrap">
                        {report.owner_name ?? ""} · {relativeTime(report.updated_at)}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="ui-card">
          <EmptyState icon={<BarChart3 size={20} />} title={search ? "No reports match" : "No reports yet"} message="Build one to answer a question about your pipeline, cases or activity." />
        </div>
      )}
    </>
  );
}
