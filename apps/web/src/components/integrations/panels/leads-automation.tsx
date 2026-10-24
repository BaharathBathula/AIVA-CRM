"use client";

import Link from "next/link";
import { ChevronDown, Download, ExternalLink, FileText, KeyRound, RefreshCcw, RotateCw, Save, Send, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { SettingRow, TagsInput, Toggle, useMembers } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, Field, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

import { UrlRow } from "../common";
import type { IntegrationDetail, PanelProps } from "../types";
import { DeliveriesCard, SecretOnce, SignatureCard, SubscriptionsCard, type HookProvider } from "../webhooks";

/* -------------------------------------------------------------------------- Shared types */

type Target = { key: string; label: string };

type LeadDefaultsValue = { lead_source: string; owner_user_id: string | null; on_duplicate: "update" | "skip" | "create"; tags: string[] };

type FormSettings = { enabled?: boolean; lead_source?: string | null; owner_user_id?: string | null; tags?: string[]; mapping?: Record<string, string> };

type FormField = { key: string; label: string; type?: string | null };

type CaptureExtra = {
  defaults: LeadDefaultsValue;
  forms: Record<string, FormSettings>;
  targets: Target[];
  custom_fields: Target[];
  error?: string;
};

async function saveSettings(detail: IntegrationDetail, config: Record<string, unknown>) {
  return api.put<IntegrationDetail>(`/integration-hub/${detail.integration.key}/settings`, { config });
}

const DUPLICATES: { value: LeadDefaultsValue["on_duplicate"]; label: string; hint: string }[] = [
  { value: "update", label: "Update the existing lead", hint: "Fill in what's missing and add the new message to its notes." },
  { value: "skip", label: "Skip it", hint: "Keep the existing lead as it is." },
  { value: "create", label: "Create another lead", hint: "Every submission becomes its own lead." },
];

/* -------------------------------------------------------------------------- Lead defaults */

function LeadDefaults({ detail, setDetail, value, sourceHint }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void; value: LeadDefaultsValue; sourceHint: string }) {
  const toast = useToast();
  const { members } = useMembers();
  const [draft, setDraft] = useState<LeadDefaultsValue>(value);
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(value);
  const locked = !detail.can_manage;

  useEffect(() => setDraft(value), [value]);

  async function save() {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail, { defaults: draft }));
      toast("Lead settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="New leads"
      footer={
        detail.can_manage ? (
          <div className="ila-save-row">
            <span className="ui-muted ui-small">{dirty ? "Unsaved changes" : "Applies to every form unless a form says otherwise."}</span>
            <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy} onClick={() => void save()}>
              {busy ? <span className="ui-spinner" /> : <Save size={14} />} Save
            </button>
          </div>
        ) : undefined
      }
    >
      <div className="settings-rows">
        <SettingRow label="Lead source" hint={sourceHint}>
          <input className="ui-input sm" value={draft.lead_source} disabled={locked} maxLength={80} onChange={(e) => setDraft({ ...draft, lead_source: e.target.value })} style={{ width: 220 }} />
        </SettingRow>
        <SettingRow label="Lead owner" hint="Who new leads are assigned to. Assignment rules and territories still apply.">
          <select className="ui-select sm" value={draft.owner_user_id ?? ""} disabled={locked} onChange={(e) => setDraft({ ...draft, owner_user_id: e.target.value || null })} style={{ width: 220 }}>
            <option value="">Whoever connected it</option>
            {members
              .filter((m) => m.is_active)
              .map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.full_name}
                </option>
              ))}
          </select>
        </SettingRow>
        <SettingRow label="Same email again" hint={DUPLICATES.find((d) => d.value === draft.on_duplicate)?.hint}>
          <select className="ui-select sm" value={draft.on_duplicate} disabled={locked} onChange={(e) => setDraft({ ...draft, on_duplicate: e.target.value as LeadDefaultsValue["on_duplicate"] })} style={{ width: 220 }}>
            {DUPLICATES.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </SettingRow>
        <SettingRow label="Tags" hint="Kept on the lead's attribution, for reports and workflow conditions." stacked>
          <TagsInput value={draft.tags} disabled={locked} onChange={(tags) => setDraft({ ...draft, tags })} placeholder="inbound, website" label="Tags" />
        </SettingRow>
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- Field mapping */

function MappingEditor({
  fields,
  mapping,
  onChange,
  targets,
  customFields,
  disabled,
}: {
  fields: FormField[];
  mapping: Record<string, string>;
  onChange: (mapping: Record<string, string>) => void;
  targets: Target[];
  customFields: Target[];
  disabled?: boolean;
}) {
  if (!fields.length) return <p className="ui-muted ui-small" style={{ margin: 0 }}>No fields seen yet. They appear after the first submission.</p>;
  const standard = targets.filter((t) => t.key !== "ignore");
  return (
    <div className="ila-map" role="table" aria-label="Field mapping">
      <div className="ila-map-row head" role="row">
        <span role="columnheader">Form field</span>
        <span role="columnheader">Goes to</span>
      </div>
      {fields.map((field) => (
        <div key={field.key} className="ila-map-row" role="row">
          <span role="cell" className="ila-map-field">
            <strong>{field.label}</strong>
            {field.label !== field.key ? <span className="ui-muted ui-small ui-mono">{field.key}</span> : null}
          </span>
          <span role="cell">
            <select
              className="ui-select sm"
              aria-label={`Where ${field.label} goes`}
              value={mapping[field.key] ?? ""}
              disabled={disabled}
              onChange={(e) => {
                const next = { ...mapping };
                if (e.target.value) next[field.key] = e.target.value;
                else delete next[field.key];
                onChange(next);
              }}
            >
              <option value="">Detect automatically</option>
              <optgroup label="Lead fields">
                {standard.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </optgroup>
              {customFields.length ? (
                <optgroup label="Custom fields">
                  {customFields.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.label}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              <option value="ignore">Don&apos;t import</option>
            </select>
          </span>
        </div>
      ))}
    </div>
  );
}

function FormEditor({
  title,
  subtitle,
  fields,
  value,
  onChange,
  extra,
  disabled,
  actions,
  defaultOpen,
}: {
  title: string;
  subtitle?: ReactNode;
  fields: FormField[];
  value: FormSettings;
  onChange: (value: FormSettings) => void;
  extra: CaptureExtra;
  disabled?: boolean;
  actions?: ReactNode;
  defaultOpen?: boolean;
}) {
  const { members } = useMembers();
  const on = value.enabled !== false;
  return (
    <details className="ila-form" open={defaultOpen}>
      <summary className="ila-form-head">
        <span className="ila-form-icon" aria-hidden>
          <FileText size={16} />
        </span>
        <span className="ila-form-title">
          <strong>{title}</strong>
          {subtitle ? <span className="ui-muted ui-small">{subtitle}</span> : null}
        </span>
        {!on ? <Badge tone="neutral">Not imported</Badge> : Object.keys(value.mapping ?? {}).length ? <Badge tone="info">{Object.keys(value.mapping ?? {}).length} mapped</Badge> : null}
        <ChevronDown size={16} className="ila-chevron" aria-hidden />
      </summary>
      <div className="ila-form-body">
        <div className="settings-rows">
          <SettingRow label="Import this form" hint="Off: submissions are received but don't create leads.">
            <Toggle checked={on} disabled={disabled} onChange={(next) => onChange({ ...value, enabled: next })} label={`Import ${title}`} />
          </SettingRow>
          <SettingRow label="Lead source" hint="Leave blank to use the default.">
            <input className="ui-input sm" value={value.lead_source ?? ""} disabled={disabled} maxLength={80} placeholder={extra.defaults.lead_source} onChange={(e) => onChange({ ...value, lead_source: e.target.value || null })} style={{ width: 200 }} />
          </SettingRow>
          <SettingRow label="Lead owner">
            <select className="ui-select sm" value={value.owner_user_id ?? ""} disabled={disabled} onChange={(e) => onChange({ ...value, owner_user_id: e.target.value || null })} style={{ width: 200 }}>
              <option value="">Default owner</option>
              {members
                .filter((m) => m.is_active)
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.full_name}
                  </option>
                ))}
            </select>
          </SettingRow>
          <SettingRow label="Extra tags" stacked>
            <TagsInput value={value.tags ?? []} disabled={disabled} onChange={(tags) => onChange({ ...value, tags })} placeholder="spring-campaign" label={`Tags for ${title}`} />
          </SettingRow>
        </div>
        <MappingEditor fields={fields} mapping={value.mapping ?? {}} onChange={(mapping) => onChange({ ...value, mapping })} targets={extra.targets} customFields={extra.custom_fields} disabled={disabled} />
        {actions ? <div className="ui-actions">{actions}</div> : null}
      </div>
    </details>
  );
}

function useFormsDraft(saved: Record<string, FormSettings>) {
  const [draft, setDraft] = useState<Record<string, FormSettings>>(saved);
  useEffect(() => setDraft(saved), [saved]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  return { draft, setDraft, dirty };
}

/* -------------------------------------------------------------------------- WordPress */

type SeenForm = { key: string; name: string; plugin?: string | null; site?: string | null; fields: FormField[]; count: number; first_at?: string; last_at?: string };

type Submission = { id: string; at: string; action: string; level: string; message: string; form_name?: string | null; result?: string | null; lead_id?: string | null };

type WordPressExtra = CaptureExtra & {
  webhook_url: string | null;
  has_secret: boolean;
  require_signature: boolean;
  seen_forms: SeenForm[];
  plugins: { key: string; name: string }[];
  recent: Submission[];
  stats_7d: { created: number; updated: number; rejected: number };
  plugin_version: string;
};

const RESULT_TONE: Record<string, Tone> = {
  lead_created: "success",
  lead_updated: "info",
  submission_skipped: "neutral",
  submission_duplicate: "neutral",
  submission_ignored: "neutral",
  submission_rejected: "danger",
  test_received: "success",
};
const RESULT_LABEL: Record<string, string> = {
  lead_created: "New lead",
  lead_updated: "Updated",
  submission_skipped: "Skipped",
  submission_duplicate: "Already imported",
  submission_ignored: "Ignored",
  submission_rejected: "Refused",
  test_received: "Test",
};

function WordPressPanel({ detail, setDetail }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as unknown as WordPressExtra;
  const savedForms = useMemo(() => extra.forms ?? {}, [extra.forms]);
  const forms = useFormsDraft(savedForms);
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ form: string; mapped: Record<string, string> } | null>(null);
  if (!detail.enabled || !("webhook_url" in detail.extra)) return detail.extra.error ? <Alert>{detail.extra.error}</Alert> : null;
  const manage = detail.can_manage;
  const labels = Object.fromEntries([...extra.targets, ...extra.custom_fields].map((t) => [t.key, t.label]));

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name);
    try {
      await action();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const rotate = () =>
    run("rotate", async () => {
      const result = await api.post<{ secret: string }>("/lead-capture/wordpress/secret");
      setSecret(result.secret);
    });

  const requireSignature = (next: boolean) =>
    run("require", async () => {
      setDetail(await saveSettings(detail, { require_signature: next }));
      toast(next ? "Unsigned submissions will be refused." : "Unsigned submissions are accepted again.");
    });

  const saveForms = () =>
    run("forms", async () => {
      setDetail(await saveSettings(detail, { forms: forms.draft }));
      toast("Form settings saved.");
    });

  const test = (formKey: string | null, create: boolean) =>
    run(`test:${formKey ?? ""}:${create}`, async () => {
      const result = await api.post<{ result: string; mapped?: Record<string, string>; link?: string; lead_id?: string | null }>("/lead-capture/wordpress/test", { form_key: formKey, create });
      if (!create) {
        setPreview({ form: formKey ?? "", mapped: result.mapped ?? {} });
        return;
      }
      toast(result.result === "created" ? "Test lead created." : result.result === "updated" ? "The test updated an existing lead." : `Test: ${result.result}`);
      setDetail(await api.get<IntegrationDetail>(`/integration-hub/${detail.integration.key}`));
    });

  return (
    <>
      <Card title="Connect your WordPress site">
        <div className="ila-two">
          <section className="ila-option">
            <div className="ila-option-head">
              <Badge tone="success">Recommended</Badge>
              <h3>Install the AIVA plugin</h3>
            </div>
            <ol className="ila-steps">
              <li>Download the plugin. It&apos;s already set up for this workspace.</li>
              <li>In WordPress: Plugins → Add New → Upload Plugin, then activate it.</li>
              <li>Submit any form on your site. The lead appears here within seconds.</li>
            </ol>
            <div className="ila-chips">
              {extra.plugins.map((p) => (
                <span key={p.key} className="ila-chip">
                  {p.name}
                </span>
              ))}
            </div>
            {manage ? (
              <a className="ui-btn ui-btn-primary" href="/api/aiva/lead-capture/wordpress/plugin.zip" download>
                <Download size={15} /> Download plugin (v{extra.plugin_version})
              </a>
            ) : (
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                An admin can download the plugin.
              </p>
            )}
          </section>
          <section className="ila-option">
            <div className="ila-option-head">
              <h3>Or use a form plugin&apos;s webhook</h3>
            </div>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              Point any form&apos;s webhook (WPForms, Gravity Forms, Elementor, Fluent Forms…) at this address. JSON and regular form posts both work.
            </p>
            <UrlRow label="Webhook URL" value={extra.webhook_url} />
            {manage ? (
              <div className="ila-secret">
                <div className="ui-small" style={{ color: "var(--text-2)" }}>
                  Optional: add the header <code>X-AIVA-Secret</code> with your workspace secret.
                </div>
                <button type="button" className="ui-btn ui-btn-sm" disabled={busy === "rotate"} onClick={() => void rotate()}>
                  {busy === "rotate" ? <span className="ui-spinner" /> : <RotateCw size={14} />} {extra.has_secret ? "Create a new secret" : "Create a secret"}
                </button>
              </div>
            ) : null}
          </section>
        </div>
        <div className="settings-rows" style={{ marginTop: 14 }}>
          <SettingRow
            label="Only accept signed submissions"
            hint="Refuse anything without the plugin's signature or the X-AIVA-Secret header. The address alone is already hard to guess; this adds a second check."
          >
            <Toggle checked={extra.require_signature} disabled={!manage || busy === "require"} onChange={(next) => void requireSignature(next)} label="Only accept signed submissions" />
          </SettingRow>
        </div>
      </Card>

      <LeadDefaults detail={detail} setDetail={setDetail} value={extra.defaults} sourceHint="Shown on the lead and in reports. Default: wordpress." />

      <Card
        title="Forms"
        actions={
          manage ? (
            <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy)} onClick={() => void test(null, true)}>
              {busy === "test::true" ? <span className="ui-spinner" /> : <Send size={14} />} Send a test lead
            </button>
          ) : null
        }
        footer={
          manage && extra.seen_forms.length ? (
            <div className="ila-save-row">
              <span className="ui-muted ui-small">{forms.dirty ? "Unsaved changes" : "Fields are detected automatically; map only what AIVA gets wrong."}</span>
              <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!forms.dirty || busy === "forms"} onClick={() => void saveForms()}>
                {busy === "forms" ? <span className="ui-spinner" /> : <Save size={14} />} Save forms
              </button>
            </div>
          ) : undefined
        }
      >
        {extra.seen_forms.length ? (
          <div className="ui-stack" style={{ gap: 10 }}>
            {extra.seen_forms.map((form, index) => (
              <FormEditor
                key={form.key}
                title={form.name}
                subtitle={
                  <>
                    {extra.plugins.find((p) => p.key === form.plugin)?.name ?? form.plugin ?? "Webhook"} · {form.count} submission{form.count === 1 ? "" : "s"}
                    {form.last_at ? ` · last ${relativeTime(form.last_at)}` : ""}
                  </>
                }
                fields={form.fields}
                value={forms.draft[form.key] ?? {}}
                onChange={(value) => forms.setDraft({ ...forms.draft, [form.key]: value })}
                extra={extra}
                disabled={!manage}
                defaultOpen={index === 0 && extra.seen_forms.length === 1}
                actions={
                  manage ? (
                    <>
                      <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy) || forms.dirty} title={forms.dirty ? "Save first" : undefined} onClick={() => void test(form.key, false)}>
                        <Wand2 size={14} /> Preview mapping
                      </button>
                      <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={Boolean(busy) || forms.dirty} onClick={() => void test(form.key, true)}>
                        <Send size={14} /> Create a test lead
                      </button>
                    </>
                  ) : null
                }
              />
            ))}
            {preview ? (
              <Alert tone="info">
                <strong>Preview</strong>:{" "}
                {Object.keys(preview.mapped).length
                  ? Object.entries(preview.mapped)
                      .map(([field, target]) => `${extra.seen_forms.find((f) => f.key === preview.form)?.fields.find((x) => x.key === field)?.label ?? field} → ${labels[target] ?? target}`)
                      .join(" · ")
                  : "nothing would be imported"}
                . Everything else goes to the lead&apos;s notes.
              </Alert>
            ) : null}
          </div>
        ) : (
          <EmptyState icon={<FileText size={20} />} title="No submissions yet" message="Forms show up here after their first submission, with their fields ready to map." />
        )}
      </Card>

      <Card
        title="Recent submissions"
        actions={
          <span className="ila-stats">
            <span>
              <strong>{extra.stats_7d.created}</strong> new
            </span>
            <span>
              <strong>{extra.stats_7d.updated}</strong> updated
            </span>
            <span className={extra.stats_7d.rejected ? "bad" : undefined}>
              <strong>{extra.stats_7d.rejected}</strong> refused
            </span>
            <span className="ui-muted">7 days</span>
          </span>
        }
      >
        {extra.recent.length ? (
          <ul className="ila-recent">
            {extra.recent.map((item) => (
              <li key={item.id}>
                <Badge tone={RESULT_TONE[item.action] ?? "neutral"}>{RESULT_LABEL[item.action] ?? item.action}</Badge>
                <span className="ila-recent-text">{item.message}</span>
                <span className="ui-muted ui-small ui-nowrap">{relativeTime(item.at)}</span>
                {item.lead_id ? (
                  <Link className="ui-small" href={`/leads/${item.lead_id}`}>
                    Open lead
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Nothing received yet.
          </p>
        )}
      </Card>
      <SecretOnce secret={secret} title="New WordPress secret" onClose={() => { setSecret(null); void api.get<IntegrationDetail>(`/integration-hub/${detail.integration.key}`).then(setDetail); }} />
    </>
  );
}

/* -------------------------------------------------------------------------- Facebook */

type FbPage = { id: string; name: string; category?: string | null; tasks?: string[]; can_leads: boolean; subscribed: boolean; subscribed_at?: string | null; error?: string | null };
type FbForm = { id: string; name: string; status?: string | null; questions: FormField[] };

type FacebookExtra = CaptureExtra & {
  callback_url: string;
  app_source: "server" | "workspace" | null;
  verify_token: string | null;
  pages: FbPage[];
  lead_forms: Record<string, FbForm[]>;
  last_backfill_at: string | null;
  pages_synced_at: string | null;
  testing_tool_url: string;
};

function FacebookPanel({ detail, setDetail }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as unknown as FacebookExtra;
  const savedForms = useMemo(() => extra.forms ?? {}, [extra.forms]);
  const forms = useFormsDraft(savedForms);
  const [busy, setBusy] = useState<string | null>(null);
  if (!detail.enabled || !("callback_url" in detail.extra)) return detail.extra.error ? <Alert>{detail.extra.error}</Alert> : null;
  const manage = detail.can_manage;
  const connected = Boolean(detail.connection);
  const subscribed = extra.pages.filter((p) => p.subscribed);

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name);
    try {
      await action();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const refresh = () => run("refresh", async () => setDetail(await api.post<IntegrationDetail>("/lead-capture/facebook/pages/refresh")));
  const sync = () =>
    run("sync", async () => {
      const result = await api.post<{ message: string }>("/lead-capture/facebook/sync");
      toast(result.message);
      setDetail(await api.get<IntegrationDetail>(`/integration-hub/${detail.integration.key}`));
    });
  const toggle = (page: FbPage, next: boolean) =>
    run(`page:${page.id}`, async () => {
      setDetail(await api.put<IntegrationDetail>(`/lead-capture/facebook/pages/${page.id}`, { subscribed: next }));
      toast(next ? `Leads from ${page.name} now come to AIVA.` : `Stopped syncing ${page.name}.`);
    });
  const loadForms = (page: FbPage) =>
    run(`forms:${page.id}`, async () => {
      await api.get(`/lead-capture/facebook/pages/${page.id}/forms`);
      setDetail(await api.get<IntegrationDetail>(`/integration-hub/${detail.integration.key}`));
    });
  const saveForms = () =>
    run("forms", async () => {
      setDetail(await saveSettings(detail, { forms: forms.draft }));
      toast("Form settings saved.");
    });

  return (
    <>
      {manage && extra.app_source === "workspace" ? (
        <Card title="Lead notifications from your Meta app">
          <div className="ui-stack" style={{ gap: 12 }}>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              You&apos;re using your own Meta app, so tell it where to send new leads: in the Meta developer dashboard open <strong>Webhooks</strong>, choose <strong>Page</strong>, click <strong>Subscribe to this object</strong>, paste these values, then subscribe to the <code>leadgen</code> field.
            </p>
            <UrlRow label="Callback URL" value={extra.callback_url} />
            <UrlRow label="Verify token" value={extra.verify_token} />
          </div>
        </Card>
      ) : extra.app_source === "server" ? (
        <Alert tone="info">AIVA&apos;s Meta app delivers lead notifications for you. Connect, pick your pages, and leads arrive within seconds.</Alert>
      ) : null}

      {connected ? (
        <Card
          title="Pages"
          flush
          actions={
            manage ? (
              <div className="ui-row" style={{ gap: 6 }}>
                <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={Boolean(busy)} onClick={() => void refresh()}>
                  {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCcw size={14} />} Refresh pages
                </button>
                <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy) || !subscribed.length} onClick={() => void sync()} title="Ask Facebook for leads AIVA hasn't received yet">
                  {busy === "sync" ? <span className="ui-spinner" /> : <RotateCw size={14} />} Check for missed leads
                </button>
              </div>
            ) : null
          }
        >
          {extra.pages.length ? (
            <ul className="ila-pages">
              {extra.pages.map((page) => (
                <li key={page.id}>
                  <div className="ila-page-main">
                    <strong>{page.name}</strong>
                    <span className="ui-muted ui-small">
                      {page.category ?? "Page"}
                      {page.subscribed && page.subscribed_at ? ` · syncing since ${relativeTime(page.subscribed_at)}` : ""}
                    </span>
                    {page.error ? <span className="ila-page-error ui-small">{page.error}</span> : !page.can_leads ? <span className="ila-page-error ui-small">Your role on this page may not allow reading leads (needs Advertise or Manage).</span> : null}
                  </div>
                  {busy === `page:${page.id}` ? <span className="ui-spinner" /> : null}
                  <Toggle checked={page.subscribed} disabled={!manage || Boolean(busy)} onChange={(next) => void toggle(page, next)} label={`Send leads from ${page.name}`} />
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No pages" message="This Facebook account doesn't manage any pages. Reconnect with an account that does." />
          )}
          <div className="ila-card-foot ui-muted ui-small">
            {extra.last_backfill_at ? `Last checked for missed leads ${relativeTime(extra.last_backfill_at)}. ` : ""}AIVA checks every 15 minutes, so a lead is never lost if a notification goes missing.
          </div>
        </Card>
      ) : null}

      {connected ? <LeadDefaults detail={detail} setDetail={setDetail} value={extra.defaults} sourceHint="Shown on the lead and in reports. Default: facebook_ads." /> : null}

      {connected && subscribed.length ? (
        <Card
          title="Lead forms"
          actions={
            <a className="ui-btn ui-btn-sm ui-btn-ghost" href={extra.testing_tool_url} target="_blank" rel="noopener noreferrer">
              <ExternalLink size={14} /> Send a test lead
            </a>
          }
          footer={
            manage ? (
              <div className="ila-save-row">
                <span className="ui-muted ui-small">{forms.dirty ? "Unsaved changes" : "Standard questions (name, email, phone, company, job title) are mapped automatically."}</span>
                <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!forms.dirty || busy === "forms"} onClick={() => void saveForms()}>
                  {busy === "forms" ? <span className="ui-spinner" /> : <Save size={14} />} Save forms
                </button>
              </div>
            ) : undefined
          }
        >
          <div className="ui-stack" style={{ gap: 16 }}>
            {subscribed.map((page) => {
              const pageForms = extra.lead_forms?.[page.id] ?? [];
              return (
                <section key={page.id} className="ui-stack" style={{ gap: 8 }}>
                  <div className="ila-subhead">
                    <strong>{page.name}</strong>
                    {manage ? (
                      <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={Boolean(busy)} onClick={() => void loadForms(page)}>
                        {busy === `forms:${page.id}` ? <span className="ui-spinner" /> : <RefreshCcw size={13} />} {pageForms.length ? "Reload forms" : "Load forms"}
                      </button>
                    ) : null}
                  </div>
                  {pageForms.length ? (
                    pageForms.map((form) => (
                      <FormEditor
                        key={form.id}
                        title={form.name}
                        subtitle={`${form.status === "ACTIVE" ? "Active" : (form.status ?? "").toLowerCase() || "Form"} · ${form.questions.length} question${form.questions.length === 1 ? "" : "s"}`}
                        fields={form.questions}
                        value={forms.draft[form.id] ?? {}}
                        onChange={(value) => forms.setDraft({ ...forms.draft, [form.id]: value })}
                        extra={extra}
                        disabled={!manage}
                      />
                    ))
                  ) : (
                    <p className="ui-muted ui-small" style={{ margin: 0 }}>
                      No lead forms loaded for this page yet.
                    </p>
                  )}
                </section>
              );
            })}
          </div>
        </Card>
      ) : null}
    </>
  );
}

/* -------------------------------------------------------------------------- Zapier, Make, n8n */

type AutomationExtra = {
  api_base_url: string;
  rest_hooks: { subscribe: string; unsubscribe: string; events: string; sample: string; auth_test: string };
  subscriptions: { active: number; paused: number };
  deliveries_24h: { delivered: number; failed: number; pending: number };
  signature_header: string;
  error?: string;
};

const GUIDES: Record<"zapier" | "make" | "n8n", { title: string; steps: ReactNode[]; actions: ReactNode }> = {
  zapier: {
    title: "Trigger a Zap from AIVA",
    steps: [
      <>In Zapier, create a Zap whose trigger is <strong>Webhooks by Zapier → Catch Hook</strong> and copy its URL.</>,
      <>Here, click <strong>Add webhook</strong>, paste the URL and pick the events (for example New lead).</>,
      <>Click <strong>Test</strong> on the webhook so Zapier sees a sample, then finish and publish the Zap.</>,
    ],
    actions: (
      <>
        To create or update AIVA records from a Zap, add a <strong>Webhooks by Zapier → Custom Request</strong> step that calls the AIVA API with an API key.
      </>
    ),
  },
  make: {
    title: "Start a Make scenario from AIVA",
    steps: [
      <>In Make, add the <strong>Webhooks → Custom webhook</strong> module, create a webhook and copy its address.</>,
      <>Here, click <strong>Add webhook</strong>, paste the address and pick the events.</>,
      <>In Make, click <strong>Redetermine data structure</strong>, then press <strong>Test</strong> here so Make learns the fields.</>,
    ],
    actions: (
      <>
        To change AIVA from a scenario, use the <strong>HTTP → Make a request</strong> module with the header <code>Authorization: Bearer &lt;API key&gt;</code>.
      </>
    ),
  },
  n8n: {
    title: "Start an n8n workflow from AIVA",
    steps: [
      <>Add a <strong>Webhook</strong> node (method POST) and copy its <strong>Production URL</strong>. Activate the workflow.</>,
      <>Here, click <strong>Add webhook</strong>, paste the URL and pick the events.</>,
      <>Optional: check the <code>X-AIVA-Signature</code> header in a Code node (see below).</>,
    ],
    actions: (
      <>
        To change AIVA from a workflow, use an <strong>HTTP Request</strong> node with <strong>Header Auth</strong>: <code>Authorization: Bearer &lt;API key&gt;</code>.
      </>
    ),
  },
};

function AutomationPanel({ detail, reload }: PanelProps) {
  const extra = detail.extra as unknown as AutomationExtra;
  const key = detail.integration.key as "zapier" | "make" | "n8n";
  if (!detail.enabled) {
    return (
      <SubscriptionsCard provider={key as HookProvider} title={`${detail.integration.name} webhooks`} enabled={false} onChanged={() => void reload()} />
    );
  }
  if (!("api_base_url" in detail.extra)) return detail.extra.error ? <Alert>{detail.extra.error}</Alert> : null;
  const guide = GUIDES[key];
  const example = `curl -X POST ${extra.api_base_url}/leads \\\n  -H "Authorization: Bearer aiva_sk_…" \\\n  -H "Content-Type: application/json" \\\n  -d '{"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"}'`;
  return (
    <>
      <div className="ila-kpis">
        <div className="ila-kpi">
          <span>Active webhooks</span>
          <strong>{extra.subscriptions.active}</strong>
        </div>
        <div className="ila-kpi">
          <span>Paused</span>
          <strong>{extra.subscriptions.paused}</strong>
        </div>
        <div className="ila-kpi">
          <span>Delivered (24 h)</span>
          <strong>{extra.deliveries_24h.delivered}</strong>
        </div>
        <div className={`ila-kpi${extra.deliveries_24h.failed ? " bad" : ""}`}>
          <span>Failed (24 h)</span>
          <strong>{extra.deliveries_24h.failed}</strong>
        </div>
      </div>

      <Card title={guide.title}>
        <ol className="ila-steps">
          {guide.steps.map((step, index) => (
            <li key={index}>{step}</li>
          ))}
        </ol>
      </Card>

      <SubscriptionsCard provider={key} title={`${detail.integration.name} webhooks`} enabled={detail.enabled} onChanged={() => void reload()} />
      <DeliveriesCard provider={key} limit={10} compact title="Recent deliveries" />

      <Card title={`Update AIVA from ${detail.integration.name}`} icon={<KeyRound size={17} />}>
        <div className="ui-stack" style={{ gap: 12 }}>
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            {guide.actions} Keys act as the person who created them. <Link href="/settings/api-keys">Create an API key</Link>.
          </p>
          <Field label="API base URL">
            <CopyField value={extra.api_base_url} label="Copy the API base URL" />
          </Field>
          <Field label="Example: create a lead">
            <pre className="code-block">{example}</pre>
          </Field>
        </div>
      </Card>

      {key === "zapier" ? (
        <Card title="Building your own Zapier app (REST hooks)">
          <div className="ui-stack" style={{ gap: 10 }}>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              For a private Zapier integration, use API key authentication (<code>Authorization: Bearer &lt;API key&gt;</code>) and REST hook triggers. Subscriptions made this way show up above, marked &ldquo;Subscribed through the API&rdquo;.
            </p>
            <div className="ila-endpoints">
              <Endpoint method="GET" label="Test authentication" url={extra.rest_hooks.auth_test} />
              <Endpoint method="GET" label="List events" url={extra.rest_hooks.events} />
              <Endpoint method="POST" label="Subscribe" url={extra.rest_hooks.subscribe} note='Body: {"event": "lead.created", "target_url": "{{bundle.targetUrl}}"}' />
              <Endpoint method="DELETE" label="Unsubscribe" url={extra.rest_hooks.unsubscribe} note="Use the id returned by Subscribe." />
              <Endpoint method="GET" label="Sample data (perform list)" url={extra.rest_hooks.sample} />
            </div>
          </div>
        </Card>
      ) : null}
      <SignatureCard />
    </>
  );
}

function Endpoint({ method, label, url, note }: { method: string; label: string; url: string; note?: string }) {
  return (
    <div className="ila-endpoint">
      <div className="ila-endpoint-head">
        <span className={`ila-method m-${method.toLowerCase()}`}>{method}</span>
        <span className="ui-small">{label}</span>
      </div>
      <CopyField value={url} label={`Copy the ${label} URL`} />
      {note ? <span className="ui-muted ui-small ui-mono">{note}</span> : null}
    </div>
  );
}

/**
 * Provider panels for the leads-automation group. Each panel renders the integration's own
 * settings and tools below the shared connection card on /integrations/[key].
 */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  wordpress_forms: WordPressPanel,
  facebook_lead_ads: FacebookPanel,
  zapier: AutomationPanel,
  make: AutomationPanel,
  n8n: AutomationPanel,
};
