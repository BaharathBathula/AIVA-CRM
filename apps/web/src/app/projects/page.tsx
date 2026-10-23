"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FolderKanban, Plus, Search } from "lucide-react";
import { Suspense, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { ProjectModal } from "@/components/project-modal";
import { useSession } from "@/components/providers";
import { Alert, Badge, EmptyState, Kpi, LoadingRows, PageHeader, Progress, Tabs } from "@/components/ui";
import { date } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/use-api";
import { PROJECT_STATUS, type Project } from "@/lib/workspace";

type Filter = "open" | "planning" | "active" | "on_hold" | "completed" | "all";

export default function ProjectsPage() {
  return (
    <FeaturePage feature="projects">
      <Suspense>
        <Projects />
      </Suspense>
    </FeaturePage>
  );
}

function Projects() {
  const router = useRouter();
  const params = useSearchParams();
  const { can } = useSession();
  const [filter, setFilter] = useState<Filter>("open");
  const [mine, setMine] = useState(false);
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const [creating, setCreating] = useState(params.get("new") === "1");
  const query = new URLSearchParams();
  if (filter !== "all") query.set("status", filter);
  if (mine) query.set("mine", "true");
  if (debounced.trim()) query.set("search", debounced.trim());
  const projects = useApi<{ items: Project[]; summary: { active: number; planning: number; late: number; completed: number } }>(`/projects?${query}`);
  const s = projects.data?.summary;

  return (
    <>
      <PageHeader
        title="Projects"
        description="Deliver what you sold: onboarding, installs and rollouts with milestones, tasks and progress. Start one from any won deal."
        actions={
          can("projects.manage") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New project
            </button>
          ) : null
        }
      />
      <div className="ui-stack">
        <div className="ui-grid ui-grid-4">
          <Kpi label="Active" value={s?.active ?? "—"} />
          <Kpi label="Planning" value={s?.planning ?? "—"} />
          <Kpi label="Past due" value={s?.late ?? "—"} tone={s?.late ? "danger" : undefined} />
          <Kpi label="Completed" value={s?.completed ?? "—"} tone="success" />
        </div>
        <div className="ui-toolbar" style={{ marginBottom: 0 }}>
          <Tabs
            label="Project status"
            value={filter}
            onChange={setFilter}
            items={[
              { value: "open", label: "Open" },
              { value: "planning", label: "Planning" },
              { value: "active", label: "Active" },
              { value: "on_hold", label: "On hold" },
              { value: "completed", label: "Completed" },
              { value: "all", label: "All" },
            ]}
          />
          <label className="ui-check">
            <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine
          </label>
          <span className="ui-spacer" />
          <div className="ui-search">
            <Search size={15} />
            <input className="ui-input sm" placeholder="Search projects" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search projects" />
          </div>
        </div>
        {projects.error ? <Alert>{projects.error}</Alert> : null}
        <div className="ui-card">
          {projects.loading && !projects.data ? (
            <LoadingRows />
          ) : projects.data?.items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Account</th>
                    <th>Status</th>
                    <th style={{ width: 180 }}>Progress</th>
                    <th>Due</th>
                    <th>Owner</th>
                  </tr>
                </thead>
                <tbody>
                  {projects.data.items.map((p) => (
                    <tr key={p.id} className="clickable" onClick={() => router.push(`/projects/${p.id}`)}>
                      <td>
                        <Link href={`/projects/${p.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                          {p.name}
                        </Link>
                        <div className="cell-sub">
                          {p.display_number} · {p.milestones_done}/{p.milestones_total} milestones
                          {p.tasks_overdue ? <span style={{ color: "var(--danger)" }}> · {p.tasks_overdue} overdue</span> : null}
                        </div>
                      </td>
                      <td>{p.account_name ?? "—"}</td>
                      <td>
                        <Badge tone={p.is_late ? "danger" : PROJECT_STATUS[p.status].tone}>{p.is_late ? "Past due" : PROJECT_STATUS[p.status].label}</Badge>
                      </td>
                      <td>
                        <div className="ui-row" style={{ gap: 8 }}>
                          <Progress value={p.progress} tone={p.progress >= 100 ? "success" : undefined} />
                          <span className="ui-small ui-muted">
                            {p.tasks_done}/{p.tasks_total}
                          </span>
                        </div>
                      </td>
                      <td style={{ color: p.is_late ? "var(--danger)" : undefined }}>{date(p.due_date)}</td>
                      <td>{p.owner_name ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState icon={<FolderKanban size={20} />} title="No projects here" message="Open a won deal and choose Start project, or create one here." />
          )}
        </div>
      </div>
      <ProjectModal open={creating} onClose={() => setCreating(false)} onSaved={(p) => router.push(`/projects/${p.id}`)} />
    </>
  );
}
