"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Workflow as WorkflowIcon } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { number, relativeTime } from "@/lib/format";
import type { Catalog, Workflow } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const TRIGGER: Record<string, string> = {
  created: "created",
  updated: "updated",
  created_or_updated: "created or updated",
  field_changed: "field changes",
};

export default function WorkflowsPage() {
  return (
    <AppShell>
      <Workflows />
    </AppShell>
  );
}

function Workflows() {
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const workflows = useApi<Workflow[]>("/workflows");
  const catalog = useApi<Catalog>("/meta/catalog");
  const editable = can("automation.workflows");
  const singular = (key: string) => catalog.data?.objects.find((object) => object.key === key)?.singular ?? key;

  async function toggle(workflow: Workflow) {
    try {
      await api.post(`/workflows/${workflow.id}/toggle`);
      toast(workflow.is_active ? `${workflow.name} paused.` : `${workflow.name} is live.`);
      await workflows.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Workflows"
        description="Rules that create tasks, update fields, reassign owners and send alerts the moment records change."
        actions={
          editable ? (
            <Link href="/workflows/new" className="ui-btn ui-btn-primary">
              <Plus size={16} /> New workflow
            </Link>
          ) : null
        }
      />
      {workflows.error ? <Alert>{workflows.error}</Alert> : null}
      <div className="ui-card">
        {workflows.loading && !workflows.data ? (
          <LoadingRows />
        ) : workflows.data?.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Trigger</th>
                  <th>Actions</th>
                  <th className="ui-num">Runs</th>
                  <th>Last run</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {workflows.data.map((workflow) => (
                  <tr key={workflow.id} className="clickable" onClick={() => router.push(`/workflows/${workflow.id}`)}>
                    <td style={{ maxWidth: 380 }}>
                      <Link href={`/workflows/${workflow.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                        {workflow.name}
                      </Link>
                      {workflow.description ? <div className="cell-sub ui-truncate">{workflow.description}</div> : null}
                    </td>
                    <td className="ui-nowrap">
                      {singular(workflow.object_type)} {TRIGGER[workflow.trigger_event] ?? workflow.trigger_event}
                      {workflow.conditions.length ? <div className="cell-sub">{workflow.conditions.length} condition(s)</div> : null}
                    </td>
                    <td>{workflow.actions.length}</td>
                    <td className="ui-num">
                      {number(workflow.run_count)}
                      {workflow.error_count ? <div className="cell-sub" style={{ color: "var(--danger)" }}>{workflow.error_count} failed</div> : null}
                    </td>
                    <td className="ui-muted ui-nowrap">{workflow.last_run_at ? relativeTime(workflow.last_run_at) : "Never"}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {editable ? (
                        <label className="ui-check">
                          <input type="checkbox" checked={workflow.is_active} onChange={() => void toggle(workflow)} aria-label={`Turn ${workflow.name} ${workflow.is_active ? "off" : "on"}`} />
                          {workflow.is_active ? <Badge tone="success">Live</Badge> : <Badge>Paused</Badge>}
                        </label>
                      ) : workflow.is_active ? (
                        <Badge tone="success">Live</Badge>
                      ) : (
                        <Badge>Paused</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<WorkflowIcon size={20} />} title="No workflows yet" message="Automate follow-ups: e.g. when a deal over $50K is created, book an executive sponsor call." />
        )}
      </div>
    </>
  );
}
