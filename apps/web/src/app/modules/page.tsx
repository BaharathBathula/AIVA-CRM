"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Boxes, Plus } from "lucide-react";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { UpgradeGate } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { MODULE_ICONS, ModuleIcon } from "@/components/record-wizard";
import { Alert, Badge, Card, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { plural } from "@/lib/format";
import { refreshCustomModules, type CustomModuleSummary } from "@/lib/modules";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function ModulesPage() {
  return (
    <AppShell>
      <Modules />
    </AppShell>
  );
}

function Modules() {
  const { can, has } = useSession();
  const modules = useApi<CustomModuleSummary[]>("/custom-modules");
  const [creating, setCreating] = useState(false);
  const admin = can("admin.settings");

  return (
    <>
      <PageHeader
        title="Modules"
        description="Your own record types — properties, vehicles, policies, projects — with their own fields, list, forms and reports."
        actions={
          admin && has("custom_modules") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New module
            </button>
          ) : null
        }
      />
      {modules.error ? <Alert>{modules.error}</Alert> : null}
      {!has("custom_modules") ? (
        <div style={{ marginBottom: 16 }}>
          <UpgradeGate feature="custom_modules" compact />
        </div>
      ) : null}
      {modules.loading && !modules.data ? (
        <Card>
          <LoadingRows />
        </Card>
      ) : modules.data?.length ? (
        <div className="ui-grid ui-grid-3">
          {modules.data.map((module) => (
            <Link key={module.id} href={`/modules/${module.key}`} className="ui-card module-tile">
              <div className="ui-row" style={{ gap: 10 }}>
                <span className="type-icon">
                  <ModuleIcon icon={module.icon} />
                </span>
                <strong style={{ flex: 1 }}>{module.name}</strong>
                {!module.is_active ? <Badge>Off</Badge> : null}
              </div>
              <span className="ui-muted ui-small">{module.description || `Custom record type with ${plural(module.field_count, "field")}.`}</span>
              <span className="ui-small">
                <strong>{module.record_count}</strong> <span className="ui-muted">{module.record_count === 1 ? module.singular.toLowerCase() : module.name.toLowerCase()}</span>
                {module.steps > 1 ? <span className="ui-muted"> · {module.steps}-step form</span> : null}
              </span>
            </Link>
          ))}
        </div>
      ) : (
        <Card>
          <EmptyState
            icon={<Boxes size={20} />}
            title="No custom modules yet"
            message={admin ? "Create a module for anything your business tracks that isn't a lead, deal or account." : "Ask a workspace admin to create one."}
          />
        </Card>
      )}
      <NewModule open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function NewModule({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const [name, setName] = useState("");
  const [singular, setSingular] = useState("");
  const [description, setDescription] = useState("");
  const [icon, setIcon] = useState("box");
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      const module = await api.post<CustomModuleSummary>("/custom-modules", {
        name: name.trim(),
        singular: singular.trim() || name.trim().replace(/s$/i, ""),
        description: description.trim() || null,
        icon,
      });
      await refreshCustomModules();
      toast(`${module.name} created. Add its fields next.`);
      router.push(`/settings/fields?object=${module.key}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New module"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim()} onClick={() => void create()}>
            Create module
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <div className="ui-form-grid">
          <Field label="Name (plural)" required>
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Properties" />
          </Field>
          <Field label="One record is a…">
            <input className="ui-input" value={singular} onChange={(e) => setSingular(e.target.value)} placeholder={name.trim().replace(/s$/i, "") || "Property"} />
          </Field>
        </div>
        <Field label="Description">
          <input className="ui-input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Homes we list and manage" />
        </Field>
        <Field label="Icon">
          <div className="chip-list">
            {Object.keys(MODULE_ICONS).map((key) => (
              <button key={key} type="button" className={`chip${icon === key ? " on" : ""}`} aria-pressed={icon === key} aria-label={key} onClick={() => setIcon(key)}>
                <ModuleIcon icon={key} size={14} />
              </button>
            ))}
          </div>
        </Field>
      </div>
    </Modal>
  );
}
