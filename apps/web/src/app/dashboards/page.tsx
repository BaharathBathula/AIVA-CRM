"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { LayoutGrid, Plus } from "lucide-react";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { DashboardDef } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function DashboardsPage() {
  return (
    <AppShell>
      <Dashboards />
    </AppShell>
  );
}

function Dashboards() {
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const dashboards = useApi<DashboardDef[]>("/dashboards");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(true);
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      const created = await api.post<DashboardDef>("/dashboards", { name: name.trim(), description: description.trim() || null, is_shared: shared, widgets: [] });
      toast("Dashboard created. Add reports to it.");
      router.push(`/dashboards/${created.id}?edit=1`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Dashboards"
        description="Live boards of saved reports. Every widget re-runs when you open the dashboard."
        actions={
          can("analytics.dashboards") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreateOpen(true)}>
              <Plus size={16} /> New dashboard
            </button>
          ) : null
        }
      />
      {dashboards.error ? <Alert>{dashboards.error}</Alert> : null}
      {dashboards.loading && !dashboards.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : dashboards.data?.length ? (
        <div className="ui-grid ui-grid-3">
          {dashboards.data.map((dashboard) => (
            <Link key={dashboard.id} href={`/dashboards/${dashboard.id}`} className="ui-insight" style={{ minHeight: 132 }}>
              <span className="ui-row" style={{ justifyContent: "space-between" }}>
                <span className="ui-avatar" style={{ borderRadius: 9, width: 34, height: 34 }}>
                  <LayoutGrid size={16} />
                </span>
                {!dashboard.is_shared ? <Badge>Private</Badge> : null}
              </span>
              <strong style={{ fontSize: 15 }}>{dashboard.name}</strong>
              <span>{dashboard.description ?? "No description"}</span>
              <span style={{ marginTop: "auto", fontSize: 12 }}>
                {dashboard.widgets.length} widget{dashboard.widgets.length === 1 ? "" : "s"} · updated {relativeTime(dashboard.updated_at)}
              </span>
            </Link>
          ))}
        </div>
      ) : (
        <div className="ui-card">
          <EmptyState icon={<LayoutGrid size={20} />} title="No dashboards yet" message="Create one and pin your most important reports to it." />
        </div>
      )}

      <Modal
        open={createOpen}
        title="New dashboard"
        onClose={() => setCreateOpen(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setCreateOpen(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void create()} disabled={busy || !name.trim()}>
              Create
            </button>
          </>
        }
      >
        <div className="ui-stack">
          <Field label="Name" required>
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Weekly sales review" />
          </Field>
          <Field label="Description">
            <input className="ui-input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <label className="ui-check">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            Share with the whole workspace
          </label>
        </div>
      </Modal>
    </>
  );
}
