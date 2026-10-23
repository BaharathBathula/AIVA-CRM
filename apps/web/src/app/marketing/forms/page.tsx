"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ClipboardList, ExternalLink, Plus } from "lucide-react";
import { useState } from "react";

import { FeaturePage, PlanChip } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import type { WebFormDef } from "@/lib/marketing";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function FormsPage() {
  return (
    <FeaturePage feature="web_forms">
      <Forms />
    </FeaturePage>
  );
}

function Forms() {
  const { can } = useSession();
  const forms = useApi<WebFormDef[]>("/forms");
  const [creating, setCreating] = useState(false);
  return (
    <>
      <PageHeader
        title="Web forms"
        description="Hosted forms and website embeds that create leads — or support cases — the moment someone submits."
        actions={
          can("marketing.manage") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New form
            </button>
          ) : null
        }
      />
      {forms.error ? <Alert>{forms.error}</Alert> : null}
      <Card flush>
        {forms.loading && !forms.data ? (
          <LoadingRows />
        ) : forms.data?.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Form</th>
                  <th>Creates</th>
                  <th>Status</th>
                  <th className="ui-num">Submissions</th>
                  <th>Last submission</th>
                  <th aria-label="Open" />
                </tr>
              </thead>
              <tbody>
                {forms.data.map((form) => (
                  <tr key={form.id}>
                    <td className="cell-title">
                      <Link href={`/marketing/forms/${form.id}`}>{form.name}</Link>
                      <div className="ui-muted ui-small">{form.fields.length} fields{Math.max(...form.fields.map((f) => f.step || 1)) > 1 ? " · multi-step" : ""}</div>
                    </td>
                    <td>{form.target === "case" ? "Support cases" : "Leads"}</td>
                    <td>{form.is_active ? <Badge tone="success">Live</Badge> : <Badge>Off</Badge>}</td>
                    <td className="ui-num">{form.submission_count}</td>
                    <td className="ui-muted">{form.last_submission_at ? relativeTime(form.last_submission_at) : "—"}</td>
                    <td style={{ textAlign: "right" }}>
                      <a className="ui-btn ui-btn-ghost ui-btn-sm" href={form.url} target="_blank" rel="noopener noreferrer">
                        <ExternalLink size={14} /> Open
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<ClipboardList size={20} />} title="No forms yet" message="Create a form, share its link or embed it on your website, and new leads arrive here automatically." />
        )}
      </Card>
      <NewFormModal open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function NewFormModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const { has } = useSession();
  const [name, setName] = useState("");
  const [target, setTarget] = useState("lead");
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      const form = await api.post<WebFormDef>("/forms", { name: name.trim(), target, settings: { title: name.trim() } });
      router.push(`/marketing/forms/${form.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New web form"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim()} onClick={() => void create()}>
            Create form
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <Field label="Form name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Request a quote" />
        </Field>
        <Field label="Each submission creates">
          <div className="chip-list">
            <button type="button" className={`chip${target === "lead" ? " on" : ""}`} onClick={() => setTarget("lead")}>
              A lead
            </button>
            <button type="button" className={`chip${target === "case" ? " on" : ""}`} disabled={!has("web_to_case")} onClick={() => setTarget("case")}>
              A support case
            </button>
            <PlanChip feature="web_to_case" />
          </div>
        </Field>
      </div>
    </Modal>
  );
}
