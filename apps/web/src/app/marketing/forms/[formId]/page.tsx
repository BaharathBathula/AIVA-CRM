"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowDown, ArrowLeft, ArrowUp, ExternalLink, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { CopyField } from "@/components/copy-field";
import type { CustomFieldDef } from "@/components/custom-fields";
import { FeaturePage, PlanChip } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, PageHeader, Tabs } from "@/components/ui";
import { dateTime } from "@/lib/format";
import type { FormFieldDef, WebFormDef } from "@/lib/marketing";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Options {
  input_types: string[];
  lead_fields: { key: string; label: string }[];
  case_fields: { key: string; label: string }[];
  can_use_steps: boolean;
}

interface Submission {
  id: string;
  data: Record<string, unknown>;
  attribution: Record<string, string>;
  link: string | null;
  created_at: string;
}

const INPUT_LABELS: Record<string, string> = {
  text: "Short text",
  email: "Email",
  tel: "Phone",
  textarea: "Paragraph",
  select: "Dropdown",
  checkbox: "Checkbox",
  number: "Number",
  date: "Date",
};

export default function FormBuilderPage() {
  return (
    <FeaturePage feature="web_forms">
      <FormBuilder />
    </FeaturePage>
  );
}

function FormBuilder() {
  const { formId } = useParams<{ formId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const form = useApi<WebFormDef>(`/forms/${formId}`);
  const options = useApi<Options>("/forms/options");
  const members = useApi<{ id: string; full_name: string }[]>("/meta/members");
  const [tab, setTab] = useState<"build" | "share" | "submissions">("build");
  const [name, setName] = useState("");
  const [fields, setFields] = useState<FormFieldDef[]>([]);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const editable = can("marketing.manage");

  const target = form.data?.target ?? "lead";
  const custom = useApi<CustomFieldDef[]>(form.data ? `/custom-fields?object_type=${target}` : null);
  const submissions = useApi<Submission[]>(tab === "submissions" ? `/forms/${formId}/submissions` : null);

  useEffect(() => {
    if (!form.data) return;
    setName(form.data.name);
    setFields(form.data.fields);
    setSettings(form.data.settings ?? {});
    setActive(form.data.is_active);
  }, [form.data]);

  if (form.error) return <Alert>{form.error}</Alert>;
  if (!form.data || !options.data) return <Card><LoadingRows rows={10} /></Card>;

  const targets = [
    ...(target === "case" ? options.data.case_fields : options.data.lead_fields),
    ...(custom.data ?? []).filter((f) => f.is_active && !["formula", "auto_number", "lookup"].includes(f.field_type)).map((f) => ({ key: `cf.${f.key}`, label: `${f.label} (custom)` })),
  ];
  const steps = Math.max(1, ...fields.map((f) => f.step || 1));
  const set = (index: number, patch: Partial<FormFieldDef>) => setFields(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  const move = (index: number, delta: number) => {
    const next = [...fields];
    const [item] = next.splice(index, 1);
    next.splice(index + delta, 0, item);
    setFields(next);
  };

  async function save() {
    setBusy(true);
    try {
      const saved = await api.patch<WebFormDef>(`/forms/${formId}`, { name: name.trim(), target, fields, settings, is_active: active });
      form.setData(saved);
      toast("Form saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    try {
      await api.del(`/forms/${formId}`);
      router.push("/marketing/forms");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const setting = (key: string) => settings[key] ?? "";
  const put = (key: string, value: string) => setSettings({ ...settings, [key]: value });

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href="/marketing/forms" className="ui-row" style={{ gap: 6, color: "inherit", textDecoration: "none" }}>
            <ArrowLeft size={14} /> Web forms
          </Link>
        }
        title={form.data.name}
        description={`${form.data.target === "case" ? "Creates a support case" : "Creates a lead"} for every submission · ${form.data.submission_count} so far`}
        actions={
          <>
            <a className="ui-btn" href={form.data.url} target="_blank" rel="noopener noreferrer">
              <ExternalLink size={15} /> Preview
            </a>
            {editable ? (
              <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
                Save form
              </button>
            ) : null}
          </>
        }
      />
      <div style={{ marginBottom: 14 }}>
        <Tabs
          label="Form sections"
          value={tab}
          onChange={setTab}
          items={[
            { value: "build", label: "Build" },
            { value: "share", label: "Share and embed" },
            { value: "submissions", label: "Submissions", count: form.data.submission_count },
          ]}
        />
      </div>

      {tab === "build" ? (
        <div className="ui-builder">
          <Card title="Fields">
            <div className="ui-stack">
              {fields.map((field, index) => (
                <div key={index}>
                  {index > 0 && options.data!.can_use_steps && field.step !== fields[index - 1].step ? (
                    <div className="ui-muted ui-small ui-strong" style={{ margin: "6px 0 10px" }}>
                      Step {field.step}
                    </div>
                  ) : null}
                  <div className="builder-step">
                    <div className="builder-step-head">
                      <span className="num">{index + 1}</span>
                      <input className="ui-input sm" style={{ flex: 1 }} value={field.label} disabled={!editable} onChange={(e) => set(index, { label: e.target.value })} aria-label="Field label" />
                      {editable ? (
                        <>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move up" disabled={index === 0} onClick={() => move(index, -1)}>
                            <ArrowUp size={14} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Move down" disabled={index === fields.length - 1} onClick={() => move(index, 1)}>
                            <ArrowDown size={14} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove field" onClick={() => setFields(fields.filter((_, i) => i !== index))}>
                            <Trash2 size={14} />
                          </button>
                        </>
                      ) : null}
                    </div>
                    <div className="ui-row" style={{ flexWrap: "wrap", gap: 8 }}>
                      <select className="ui-select sm" style={{ flex: "1 1 140px" }} value={field.type} disabled={!editable} onChange={(e) => set(index, { type: e.target.value as FormFieldDef["type"] })} aria-label="Input type">
                        {options.data!.input_types.map((type) => (
                          <option key={type} value={type}>
                            {INPUT_LABELS[type] ?? type}
                          </option>
                        ))}
                      </select>
                      <select className="ui-select sm" style={{ flex: "1 1 180px" }} value={field.maps_to} disabled={!editable} onChange={(e) => set(index, { maps_to: e.target.value })} aria-label="Saves to">
                        <option value="">Saves to: notes only</option>
                        {targets.map((t) => (
                          <option key={t.key} value={t.key}>
                            Saves to: {t.label}
                          </option>
                        ))}
                      </select>
                      {options.data!.can_use_steps ? (
                        <select className="ui-select sm" style={{ flex: "0 0 110px" }} value={field.step || 1} disabled={!editable} onChange={(e) => set(index, { step: Number(e.target.value) })} aria-label="Step">
                          {[1, 2, 3, 4, 5].map((n) => (
                            <option key={n} value={n}>
                              Step {n}
                            </option>
                          ))}
                        </select>
                      ) : null}
                      <label className="ui-check">
                        <input type="checkbox" checked={field.required} disabled={!editable} onChange={(e) => set(index, { required: e.target.checked })} /> Required
                      </label>
                    </div>
                    {field.type === "select" ? (
                      <input
                        className="ui-input sm"
                        value={field.options.join(", ")}
                        disabled={!editable}
                        placeholder="Options, separated by commas"
                        onChange={(e) => set(index, { options: e.target.value.split(",").map((o) => o.trimStart()) })}
                        onBlur={(e) => set(index, { options: e.target.value.split(",").map((o) => o.trim()).filter(Boolean) })}
                        aria-label="Options"
                      />
                    ) : null}
                  </div>
                </div>
              ))}
              {editable ? (
                <button
                  type="button"
                  className="ui-btn ui-btn-sm"
                  style={{ width: "fit-content" }}
                  onClick={() => setFields([...fields, { key: `field_${fields.length + 1}`, label: "New question", type: "text", required: false, maps_to: "", options: [], step: steps }])}
                >
                  <Plus size={14} /> Add field
                </button>
              ) : null}
              {!options.data.can_use_steps ? (
                <div className="ui-muted ui-small">
                  Want a step-by-step form? <PlanChip feature="sequential_forms" />
                </div>
              ) : null}
            </div>
          </Card>
          <Card title="Settings">
            <div className="ui-stack">
              <Field label="Form name">
                <input className="ui-input" value={name} disabled={!editable} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Heading on the form">
                <input className="ui-input" value={setting("title")} disabled={!editable} onChange={(e) => put("title", e.target.value)} />
              </Field>
              <Field label="Intro text">
                <textarea className="ui-textarea" rows={2} value={setting("description")} disabled={!editable} onChange={(e) => put("description", e.target.value)} />
              </Field>
              <Field label="Button label">
                <input className="ui-input" value={setting("submit_label")} placeholder="Submit" disabled={!editable} onChange={(e) => put("submit_label", e.target.value)} />
              </Field>
              <Field label="Thank-you message">
                <input className="ui-input" value={setting("success_message")} placeholder="Thanks! We'll be in touch soon." disabled={!editable} onChange={(e) => put("success_message", e.target.value)} />
              </Field>
              <Field label="Then send people to" hint="Optional page on your website.">
                <input className="ui-input" type="url" value={setting("redirect_url")} placeholder="https://" disabled={!editable} onChange={(e) => put("redirect_url", e.target.value)} />
              </Field>
              {target === "lead" ? (
                <>
                  <Field label="Lead owner">
                    <select className="ui-select" value={setting("owner_user_id")} disabled={!editable} onChange={(e) => put("owner_user_id", e.target.value)}>
                      <option value="">Whoever created the form</option>
                      {(members.data ?? []).map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.full_name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Lead source" hint="Google Ads clicks are tagged automatically.">
                    <input className="ui-input" value={setting("lead_source")} placeholder="web_form" disabled={!editable} onChange={(e) => put("lead_source", e.target.value)} />
                  </Field>
                </>
              ) : null}
              <Field label="Notify on each submission">
                <select className="ui-select" value={setting("notify_user_id")} disabled={!editable} onChange={(e) => put("notify_user_id", e.target.value)}>
                  <option value="">{target === "lead" ? "The lead owner" : "Nobody"}</option>
                  {(members.data ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.full_name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Accent color">
                <input className="ui-input" type="color" value={setting("accent") || "#18171c"} disabled={!editable} onChange={(e) => put("accent", e.target.value)} style={{ padding: 4, width: 70 }} />
              </Field>
              <label className="ui-check">
                <input type="checkbox" checked={active} disabled={!editable} onChange={(e) => setActive(e.target.checked)} /> Accepting responses
              </label>
              {editable ? (
                <button type="button" className="ui-btn ui-btn-ghost" style={{ width: "fit-content" }} onClick={() => setDeleting(true)}>
                  <Trash2 size={14} /> Delete form
                </button>
              ) : null}
            </div>
          </Card>
        </div>
      ) : null}

      {tab === "share" ? (
        <div className="ui-stack" style={{ gap: 16 }}>
          <Card title="Link to your form">
            <div className="ui-stack">
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Share this link in emails, ads or social posts. Add UTM parameters (?utm_source=…) or let Google Ads add gclid — AIVA saves them on the lead.
              </p>
              <CopyField value={form.data.url} label="Copy form link" />
            </div>
          </Card>
          <Card title="Embed on your website">
            <div className="ui-stack">
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Paste this where the form should appear on your site.
              </p>
              <CopyField value={form.data.embed_code} label="Copy embed code" multiline />
            </div>
          </Card>
        </div>
      ) : null}

      {tab === "submissions" ? (
        <Card flush>
          {submissions.loading && !submissions.data ? (
            <LoadingRows />
          ) : submissions.data?.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Answers</th>
                    <th>Source</th>
                    <th aria-label="Record" />
                  </tr>
                </thead>
                <tbody>
                  {submissions.data.map((s) => (
                    <tr key={s.id}>
                      <td className="ui-muted ui-nowrap">{dateTime(s.created_at)}</td>
                      <td style={{ maxWidth: 520 }}>
                        {Object.entries(s.data)
                          .filter(([, v]) => v !== "" && v !== null && v !== false)
                          .map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`)
                          .join(" · ")}
                      </td>
                      <td>{s.attribution.gclid ? <Badge tone="info">Google Ads</Badge> : s.attribution.utm_source ? <Badge>{s.attribution.utm_source}</Badge> : <span className="ui-muted">Direct</span>}</td>
                      <td style={{ textAlign: "right" }}>
                        {s.link ? (
                          <Link className="ui-btn ui-btn-ghost ui-btn-sm" href={s.link}>
                            Open {form.data!.target}
                          </Link>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty-hint">No submissions yet. Share the link to start collecting responses.</p>
          )}
        </Card>
      ) : null}

      <ConfirmDialog open={deleting} title="Delete this form?" message="The link and embed stop working. Leads and cases it created stay." onConfirm={() => void remove()} onClose={() => setDeleting(false)} />
    </>
  );
}
