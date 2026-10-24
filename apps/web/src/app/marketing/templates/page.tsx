"use client";

import { FileText, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { EmailEditor } from "@/components/email-editor";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Template {
  id: string;
  name: string;
  subject: string;
  body: string;
  category: string | null;
  is_shared: boolean;
  owner_user_id: string | null;
  owner_name: string | null;
  usage_count: number;
  updated_at: string;
}

export default function TemplatesPage() {
  return (
    <FeaturePage feature="email_templates">
      <Templates />
    </FeaturePage>
  );
}

function Templates() {
  const toast = useToast();
  const { session, can } = useSession();
  const templates = useApi<Template[]>("/email-templates");
  const [editing, setEditing] = useState<Template | "new" | null>(null);
  const [deleting, setDeleting] = useState<Template | null>(null);
  const [filter, setFilter] = useState("all");

  const categories = useMemo(() => Array.from(new Set((templates.data ?? []).map((t) => t.category).filter(Boolean))) as string[], [templates.data]);
  const shown = (templates.data ?? []).filter((t) => filter === "all" || t.category === filter);
  const canEdit = (t: Template) => t.owner_user_id === session?.user.id || can("marketing.manage");

  async function remove() {
    if (!deleting) return;
    try {
      await api.del(`/email-templates/${deleting.id}`);
      toast("Template deleted.");
      setDeleting(null);
      await templates.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Email templates"
        description="Reusable emails with merge fields. Use them when you email someone from a record, in campaigns and in journeys."
        actions={
          can("records.write") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New template
            </button>
          ) : null
        }
      />
      {templates.error ? <Alert>{templates.error}</Alert> : null}
      {categories.length ? (
        <div className="chip-list" style={{ marginBottom: 14 }}>
          {["all", ...categories].map((category) => (
            <button key={category} type="button" className={`chip${filter === category ? " on" : ""}`} onClick={() => setFilter(category)}>
              {category === "all" ? "All" : category}
            </button>
          ))}
        </div>
      ) : null}
      {templates.loading && !templates.data ? (
        <Card>
          <LoadingRows />
        </Card>
      ) : shown.length ? (
        <div className="ui-grid ui-grid-3">
          {shown.map((template) => (
            <section key={template.id} className="ui-card" style={{ padding: 20, display: "grid", gap: 10, alignContent: "start" }}>
              <div className="ui-row">
                <span className="type-icon">
                  <FileText size={15} />
                </span>
                <strong style={{ flex: 1, minWidth: 0 }} className="ui-truncate">
                  {template.name}
                </strong>
                {template.category ? <Badge>{template.category}</Badge> : null}
              </div>
              <div style={{ fontSize: 13.5, fontWeight: 650 }}>{template.subject}</div>
              <p className="ui-muted ui-small" style={{ margin: 0, whiteSpace: "pre-wrap", lineHeight: 1.5, display: "-webkit-box", WebkitLineClamp: 4, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                {template.body}
              </p>
              <div className="ui-row ui-muted ui-small">
                <span>
                  {template.owner_name ?? "—"} · used {template.usage_count}× · {relativeTime(template.updated_at)}
                </span>
                <span className="ui-spacer" />
                {canEdit(template) ? (
                  <>
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Edit template" onClick={() => setEditing(template)}>
                      <Pencil size={14} />
                    </button>
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Delete template" onClick={() => setDeleting(template)}>
                      <Trash2 size={14} />
                    </button>
                  </>
                ) : null}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <Card>
          <EmptyState icon={<FileText size={20} />} title="No templates yet" message="Write an email once and reuse it with merge fields like {{first_name}}." />
        </Card>
      )}

      <TemplateModal
        template={editing}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await templates.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete template?"
        message="Campaigns and journeys that already used it keep their copy."
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function TemplateModal({ template, onClose, onSaved }: { template: Template | "new" | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [shared, setShared] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sharing = useApi<{ data: { templates_shared: boolean } }>(template === "new" ? "/workspace-settings/sharing" : null);
  const sharedByDefault = sharing.data?.data.templates_shared ?? true;

  useEffect(() => {
    if (!template) return;
    const t = template === "new" ? null : template;
    setName(t?.name ?? "");
    setCategory(t?.category ?? "");
    setSubject(t?.subject ?? "Hi {{first_name}}");
    setBody(t?.body ?? "Hi {{first_name|there}},\n\n\n\nBest,\n{{sender_name}}");
    setShared(t?.is_shared ?? sharedByDefault);
    setError(null);
  }, [template, sharedByDefault]);

  async function save() {
    setBusy(true);
    setError(null);
    const payload = { name: name.trim(), category: category.trim() || null, subject, body, is_shared: shared };
    try {
      if (template === "new") await api.post("/email-templates", payload);
      else if (template) await api.patch(`/email-templates/${template.id}`, payload);
      toast("Template saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(template)}
      title={template === "new" ? "New email template" : "Edit template"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim() || !subject.trim()} onClick={() => void save()}>
            Save template
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Template name" required>
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Renewal check-in" />
          </Field>
          <Field label="Category">
            <input className="ui-input" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Renewals" />
          </Field>
        </div>
        <EmailEditor subject={subject} body={body} onSubject={setSubject} onBody={setBody} />
        <label className="ui-check">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> Share with the whole workspace
        </label>
      </div>
    </Modal>
  );
}
