"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { FieldValue, type CustomFieldDef } from "@/components/custom-fields";
import { useSession, useToast } from "@/components/providers";
import { ModuleIcon, RecordWizard } from "@/components/record-wizard";
import { Alert, Card, ConfirmDialog, LoadingRows, PageHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";
import type { CustomModuleSummary } from "@/lib/modules";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface ModuleRecord {
  id: string;
  module: CustomModuleSummary;
  module_key: string;
  name: string;
  owner_name: string | null;
  created_at: string;
  updated_at: string;
  fields: CustomFieldDef[];
  values: Record<string, unknown>;
  labels: Record<string, string>;
}

export default function ModuleRecordPage() {
  return (
    <AppShell>
      <ModuleRecordView />
    </AppShell>
  );
}

function ModuleRecordView() {
  const { moduleKey, recordId } = useParams<{ moduleKey: string; recordId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const record = useApi<ModuleRecord>(`/modules/${moduleKey}/records/${recordId}`);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const r = record.data;
  const fields = useMemo(() => (r?.fields ?? []).filter((f) => f.is_active), [r]);
  const steps = useMemo(() => Array.from(new Set(fields.map((f) => f.step))).sort((a, b) => a - b), [fields]);

  async function remove() {
    setBusy(true);
    try {
      await api.del(`/modules/${moduleKey}/records/${recordId}`);
      toast("Deleted.");
      router.replace(`/modules/${moduleKey}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  if (record.error) return <Alert>{record.error}</Alert>;
  if (!r) {
    return (
      <Card>
        <LoadingRows rows={8} />
      </Card>
    );
  }

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href={`/modules/${moduleKey}`} className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> {r.module.name}
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            <ModuleIcon icon={r.module.icon} size={14} /> {r.module.singular}
          </>
        }
        title={r.name}
        description={`Owner ${r.owner_name ?? "—"} · created ${dateTime(r.created_at)} · updated ${dateTime(r.updated_at)}`}
        actions={
          <>
            {can("records.delete") ? (
              <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDeleting(true)}>
                <Trash2 size={15} /> Delete
              </button>
            ) : null}
            {can("records.write") && r.module.is_active ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing(true)}>
                <Pencil size={15} /> Edit
              </button>
            ) : null}
          </>
        }
      />
      {fields.length ? (
        <div className="ui-stack">
          {steps.map((step, index) => (
            <Card key={step} title={steps.length > 1 ? `Step ${index + 1}` : "Details"}>
              <dl className="kv">
                {fields
                  .filter((f) => f.step === step)
                  .map((field) => (
                    <div key={field.key} style={{ display: "contents" }}>
                      <dt>{field.label}</dt>
                      <dd>
                        <FieldValue field={field} value={r.values[field.key]} label={r.labels[field.key]} />
                      </dd>
                    </div>
                  ))}
              </dl>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <p className="ui-muted" style={{ margin: 0 }}>
            This module has no fields yet.{" "}
            {can("admin.settings") ? <Link href={`/settings/fields?object=${moduleKey}`}>Add fields</Link> : "Ask an admin to add some."}
          </p>
        </Card>
      )}
      <RecordWizard
        open={editing}
        title={`Edit ${r.module.singular.toLowerCase()}`}
        singular={r.module.singular}
        fields={r.fields}
        initialName={r.name}
        initialValues={r.values}
        onClose={() => setEditing(false)}
        onSave={async (name, values) => {
          const next = await api.patch<Omit<ModuleRecord, "module">>(`/modules/${moduleKey}/records/${recordId}`, { name, values });
          record.setData({ ...r, ...next });
          setEditing(false);
          toast("Saved.");
        }}
      />
      <ConfirmDialog
        open={deleting}
        title={`Delete ${r.name}?`}
        message="This can't be undone."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}
