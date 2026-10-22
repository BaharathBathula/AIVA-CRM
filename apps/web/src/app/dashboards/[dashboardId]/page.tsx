"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowDown, ArrowLeft, ArrowUp, ExternalLink, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { ReportVisual } from "@/components/charts";
import { AppShell } from "@/components/app-shell";
import { useSession, useToast } from "@/components/providers";
import { Alert, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { number, relativeTime } from "@/lib/format";
import type { DashboardDef, DashboardWidget, ReportDef } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const SPAN = { small: 4, medium: 6, large: 12 } as const;

export default function DashboardPage() {
  return (
    <AppShell>
      <DashboardView />
    </AppShell>
  );
}

function DashboardView() {
  const { dashboardId } = useParams<{ dashboardId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const dashboard = useApi<DashboardDef>(`/dashboards/${dashboardId}`);
  const reports = useApi<ReportDef[]>("/reports");
  const [editing, setEditing] = useState(false);
  const [widgets, setWidgets] = useState<DashboardWidget[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [pick, setPick] = useState("");
  const [size, setSize] = useState<DashboardWidget["size"]>("medium");
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const d = dashboard.data;
  const canEdit = can("analytics.dashboards");

  useEffect(() => {
    if (d) setWidgets(d.widgets);
  }, [d]);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("edit") === "1") setEditing(true);
  }, []);

  function move(index: number, by: number) {
    setWidgets((current) => {
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(Math.max(0, Math.min(next.length, index + by)), 0, item);
      return next;
    });
  }

  async function save() {
    if (!d) return;
    setBusy(true);
    try {
      await api.put(`/dashboards/${dashboardId}`, {
        name: d.name,
        description: d.description,
        is_shared: d.is_shared,
        widgets: widgets.map(({ id, report_id, title, size: s }) => ({ id, report_id, title, size: s })),
      });
      await dashboard.reload();
      setEditing(false);
      toast("Dashboard saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/dashboards/${dashboardId}`);
      toast("Dashboard deleted.");
      router.replace("/dashboards");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  if (dashboard.error) return <Alert>{dashboard.error}</Alert>;
  if (!d) return <div className="ui-card"><LoadingRows rows={10} /></div>;

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/dashboards" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> Dashboards
        </Link>
      </div>
      <PageHeader
        title={d.name}
        description={`${d.description ? `${d.description} · ` : ""}Updated ${relativeTime(d.updated_at)}`}
        actions={
          editing ? (
            <>
              <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDeleting(true)}>
                <Trash2 size={15} /> Delete
              </button>
              <button type="button" className="ui-btn" onClick={() => setAddOpen(true)}>
                <Plus size={15} /> Add report
              </button>
              <button
                type="button"
                className="ui-btn"
                onClick={() => {
                  setWidgets(d.widgets);
                  setEditing(false);
                }}
              >
                Cancel
              </button>
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
                Save layout
              </button>
            </>
          ) : (
            <>
              <button type="button" className="ui-btn" onClick={() => void dashboard.reload()} disabled={dashboard.loading}>
                <RefreshCw size={15} /> Refresh
              </button>
              {canEdit ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing(true)}>
                  <Pencil size={15} /> Edit
                </button>
              ) : null}
            </>
          )
        }
      />

      {widgets.length ? (
        <div className="dash-grid">
          {widgets.map((widget, index) => {
            const report = widget.report ?? reports.data?.find((item) => item.id === widget.report_id);
            return (
              <section key={widget.id ?? `${widget.report_id}-${index}`} className="ui-card" style={{ gridColumn: `span ${SPAN[widget.size]}` }}>
                <div className="ui-card-header">
                  <h2 className="ui-truncate" style={{ display: "block" }}>
                    {widget.title || report?.name || "Report"}
                  </h2>
                  {editing ? (
                    <div className="ui-actions">
                      <select
                        className="ui-select sm"
                        style={{ width: 110 }}
                        value={widget.size}
                        onChange={(e) => setWidgets(widgets.map((w, i) => (i === index ? { ...w, size: e.target.value as DashboardWidget["size"] } : w)))}
                        aria-label="Widget size"
                      >
                        <option value="small">Small</option>
                        <option value="medium">Medium</option>
                        <option value="large">Large</option>
                      </select>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move earlier" onClick={() => move(index, -1)} disabled={index === 0}>
                        <ArrowUp size={14} />
                      </button>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move later" onClick={() => move(index, 1)} disabled={index === widgets.length - 1}>
                        <ArrowDown size={14} />
                      </button>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove widget" onClick={() => setWidgets(widgets.filter((_, i) => i !== index))}>
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <Link href={`/reports/${widget.report_id}`} className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Open report">
                      <ExternalLink size={14} />
                    </Link>
                  )}
                </div>
                <div className="ui-card-body">
                  {widget.error ? (
                    <div className="ui-muted ui-small">{widget.error}</div>
                  ) : widget.result && report ? (
                    <>
                      <ReportVisual result={widget.result} chart={report.chart_type} compact />
                      <div className="ui-muted ui-small" style={{ marginTop: 8 }}>
                        {number(widget.result.total_count)} {widget.result.object_label.toLowerCase()}
                      </div>
                    </>
                  ) : (
                    <div className="ui-muted ui-small">Save the layout to load this report.</div>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        <div className="ui-card">
          <EmptyState
            icon={<Plus size={20} />}
            title="This dashboard is empty"
            message="Add saved reports as widgets. Build new reports from the Reports page."
            action={
              canEdit ? (
                <button
                  type="button"
                  className="ui-btn ui-btn-primary"
                  onClick={() => {
                    setEditing(true);
                    setAddOpen(true);
                  }}
                >
                  <Plus size={15} /> Add report
                </button>
              ) : null
            }
          />
        </div>
      )}

      <Modal
        open={addOpen}
        size="sm"
        title="Add a report"
        onClose={() => setAddOpen(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setAddOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="ui-btn ui-btn-primary"
              disabled={!pick}
              onClick={() => {
                setWidgets([...widgets, { id: "", report_id: pick, size }]);
                setPick("");
                setAddOpen(false);
                setEditing(true);
              }}
            >
              Add
            </button>
          </>
        }
      >
        <div className="ui-stack">
          <Field label="Report">
            <select className="ui-select" value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Choose…</option>
              {(reports.data ?? []).map((report) => (
                <option key={report.id} value={report.id}>
                  {report.folder ? `${report.folder} / ` : ""}
                  {report.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Size">
            <select className="ui-select" value={size} onChange={(e) => setSize(e.target.value as DashboardWidget["size"])}>
              <option value="small">Small (one third)</option>
              <option value="medium">Medium (half)</option>
              <option value="large">Large (full width)</option>
            </select>
          </Field>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleting}
        title={`Delete ${d.name}?`}
        message="The dashboard is removed. Its reports are kept."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}
