"use client";

import Link from "next/link";
import { ChevronDown, Coins, FileText, LayoutTemplate, RefreshCcw, RotateCw, Save, Search, Settings2, Signature, Tags, UserPlus, Webhook } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";

import { useToast } from "@/components/providers";
import { NumberInput, SettingRow, TagsInput, Toggle, useMembers } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, Kpi } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

import { ProspectSearch, type ProspectProviderKey } from "../ProspectSearch";
import { SIGN_TAGS, type SignatureRequestItem } from "../record/documents-shared";
import { SignatureRow } from "../record/RecordSignatures";
import type { IntegrationDetail, PanelProps } from "../types";

/** Saves workspace options for an integration and hands back the refreshed page data. */
async function saveSettings(key: string, config: Record<string, unknown>) {
  return api.put<IntegrationDetail>(`/integration-hub/${key}/settings`, { config });
}

function activeMembers(members: ReturnType<typeof useMembers>["members"]) {
  return members.filter((m) => m.is_active);
}

// ===========================================================================
// E-signature: Adobe Acrobat Sign and PandaDoc
// ===========================================================================

type SignSettings = {
  save_signed_copy: boolean;
  include_certificate: boolean;
  mark_quote_accepted: boolean;
  notify_sender: boolean;
  reminder_days: number;
  expire_days: number;
};

type SignExtra = {
  counts: Record<string, number>;
  recent: SignatureRequestItem[];
  settings: SignSettings & { quote_template_id?: string | null; quote_template_name?: string | null };
  push: boolean;
  webhook: { registered: boolean; scope?: string | null; pending?: boolean };
  account: { email?: string | null; api_access_point?: string | null; workspace?: string | null };
  text_tags?: { signature: string; date: string };
  field_tags?: { signature: string; date: string };
  tokens?: string[];
};

function SignRequestsCard({ detail, extra, reload, footnote }: { detail: IntegrationDetail; extra: Partial<SignExtra>; reload: () => Promise<void>; footnote?: ReactNode }) {
  const counts = extra.counts ?? {};
  const waiting = (counts.created ?? 0) + (counts.sent ?? 0) + (counts.delivered ?? 0);
  return (
    <Card title="Signature requests" icon={<Signature size={17} />}>
      <div className="ui-stack" style={{ gap: 16 }}>
        <div className="settings-kpis three">
          <Kpi label="Waiting for signatures" value={waiting} />
          <Kpi label="Signed" value={counts.completed ?? 0} tone={counts.completed ? "success" : undefined} />
          <Kpi label="Declined or voided" value={(counts.declined ?? 0) + (counts.voided ?? 0)} tone={counts.declined ? "danger" : undefined} />
        </div>
        {extra.recent?.length ? (
          <ul className="idoc-requests">
            {extra.recent.map((item) => (
              <SignatureRow key={item.id} item={item} showRecord onChanged={() => void reload()} />
            ))}
          </ul>
        ) : (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Nothing sent with {detail.integration.name} yet. Use <strong>Send for signature</strong> on a deal, quote, account, contact or document and choose{" "}
            {detail.integration.name}.
          </p>
        )}
        {footnote ? <p className="ui-muted ui-small" style={{ margin: 0 }}>{footnote}</p> : null}
      </div>
    </Card>
  );
}

function SignUpdatesCard({
  detail,
  extra,
  setDetail,
  endpoint,
  vendor,
  pushNote,
}: {
  detail: IntegrationDetail;
  extra: Partial<SignExtra>;
  setDetail: (d: IntegrationDetail) => void;
  endpoint: string;
  vendor: string;
  pushNote: string;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const registered = Boolean(extra.webhook?.registered);

  async function register() {
    setBusy(true);
    try {
      setDetail(await api.post<IntegrationDetail>(endpoint));
      toast(`${vendor} now sends AIVA updates as they happen.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Status updates" icon={<Webhook size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        {extra.push ? (
          <>
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              {registered ? (
                <Badge tone="success" dot>
                  Webhook registered{extra.webhook?.scope ? ` (${extra.webhook.scope.toLowerCase()})` : ""}
                </Badge>
              ) : extra.webhook?.pending ? (
                <Badge tone="warning" dot>Waiting to register — AIVA retries every 30 minutes</Badge>
              ) : (
                <Badge tone="warning" dot>Not registered</Badge>
              )}
            </div>
            <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
              {pushNote} AIVA always reads the request back from {vendor} before changing anything, and also checks open requests every 15 minutes.
            </p>
            {detail.can_manage && !registered ? (
              <div>
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => void register()} disabled={busy}>
                  {busy ? <span className="ui-spinner" /> : <RotateCw size={14} />} Register the webhook now
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <Alert tone="info">
            AIVA checks open requests with {vendor} every 15 minutes. Run AIVA on a public https address and {vendor} will tell AIVA the moment something changes.
          </Alert>
        )}
      </div>
    </Card>
  );
}

type SettingField = keyof SignSettings;

const SIGN_SETTING_COPY: Record<SettingField, { label: string; hint: string }> = {
  save_signed_copy: { label: "Save the signed copy in Documents", hint: "The signed PDF is linked to the record (and the quote and deal)." },
  include_certificate: { label: "Include the audit report", hint: "The provider's page with who signed, when and from where." },
  mark_quote_accepted: { label: "Mark signed quotes as accepted", hint: "Signed quotes become Accepted, and tracked stock is taken out like any accepted quote." },
  notify_sender: { label: "Tell the sender", hint: "An AIVA notification when it's signed, declined or voided." },
  reminder_days: { label: "Remind signers every", hint: "0 uses your account's default." },
  expire_days: { label: "Expire unsigned requests after", hint: "0 uses your account's default." },
};

function SignSettingsCard({ detail, initial, setDetail, fields }: { detail: IntegrationDetail; initial: SignSettings; setDetail: (d: IntegrationDetail) => void; fields: SettingField[] }) {
  const toast = useToast();
  const pick = (source: SignSettings) => Object.fromEntries(fields.map((f) => [f, source[f]])) as Partial<SignSettings>;
  const saved = useMemo(() => pick(initial), [initial]); // eslint-disable-line react-hooks/exhaustive-deps
  const [values, setValues] = useState<Partial<SignSettings>>(saved);
  const [busy, setBusy] = useState(false);
  useEffect(() => setValues(saved), [saved]);
  const dirty = JSON.stringify(values) !== JSON.stringify(saved);
  const set = <K extends SettingField>(name: K, value: SignSettings[K]) => setValues((current) => ({ ...current, [name]: value }));

  async function save() {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail.integration.key, values));
      toast(`${detail.integration.name} settings saved.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="When something is signed"
      icon={<Settings2 size={17} />}
      footer={
        <div className="ui-actions" style={{ justifyContent: "flex-end" }}>
          {dirty ? (
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => setValues(saved)} disabled={busy}>
              Discard
            </button>
          ) : null}
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => void save()} disabled={busy || !dirty}>
            {busy ? <span className="ui-spinner" /> : null} Save
          </button>
        </div>
      }
    >
      {fields.map((name) => {
        const copy = SIGN_SETTING_COPY[name];
        if (name === "reminder_days" || name === "expire_days") {
          return (
            <SettingRow key={name} label={copy.label} hint={copy.hint}>
              <NumberInput
                value={Number(values[name] ?? 0)}
                onChange={(v) => set(name, v ?? 0)}
                min={0}
                max={name === "reminder_days" ? 30 : 365}
                suffix="days"
                label={copy.label}
                width={90}
              />
            </SettingRow>
          );
        }
        return (
          <SettingRow key={name} label={copy.label} hint={copy.hint}>
            <Toggle
              checked={Boolean(values[name])}
              onChange={(v) => set(name, v)}
              disabled={name === "include_certificate" && !values.save_signed_copy}
              label={copy.label}
            />
          </SettingRow>
        );
      })}
    </Card>
  );
}

function TagHelp({ title, tags, children }: { title: string; tags: { signature: string; date: string; next: string }; children?: ReactNode }) {
  return (
    <Card title={title} icon={<Tags size={17} />}>
      <div className="ui-stack" style={{ gap: 10 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          Quotes get their signature and date fields automatically. In a document you upload, type these where each signer signs:
        </p>
        <dl className="isal-tags">
          <dt>Signer 1 signs</dt>
          <dd>
            <code>{tags.signature}</code>
          </dd>
          <dt>Date signed</dt>
          <dd>
            <code>{tags.date}</code>
          </dd>
          <dt>Signer 2 signs</dt>
          <dd>
            <code>{tags.next}</code>
          </dd>
        </dl>
        {children}
      </div>
    </Card>
  );
}

function AdobeSignPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as Partial<SignExtra> & { error?: string };
  if (!detail.enabled) return null;
  const account = extra.account ?? {};
  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      <SignRequestsCard
        detail={detail}
        extra={extra}
        reload={reload}
        footnote={
          account.email ? (
            <>
              Sending as {account.email}
              {account.api_access_point ? ` · ${account.api_access_point.replace(/^https:\/\//, "").replace(/\/$/, "")}` : ""}
            </>
          ) : null
        }
      />
      {detail.connection ? (
        <SignUpdatesCard
          detail={detail}
          extra={extra}
          setDetail={setDetail}
          endpoint="/adobe-sign/webhook"
          vendor="Acrobat Sign"
          pushNote="AIVA registered a webhook with Acrobat Sign, so agreement changes arrive as they happen."
        />
      ) : null}
      <TagHelp title="Signature fields in your documents" tags={SIGN_TAGS.adobe_sign}>
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          These are Acrobat Sign text tags. Without them, Acrobat Sign adds a signature block at the end of the document.
        </p>
      </TagHelp>
      {detail.can_manage && extra.settings ? (
        <SignSettingsCard
          detail={detail}
          initial={extra.settings}
          setDetail={setDetail}
          fields={["save_signed_copy", "include_certificate", "mark_quote_accepted", "notify_sender", "reminder_days", "expire_days"]}
        />
      ) : null}
    </>
  );
}

type PandaTemplates = { items: { id: string; name: string; modified_at: string | null }[]; default_quote_template_id: string | null };

function PandaQuoteTemplateCard({ detail, extra, setDetail }: { detail: IntegrationDetail; extra: Partial<SignExtra>; setDetail: (d: IntegrationDetail) => void }) {
  const toast = useToast();
  const connected = detail.connection?.status === "connected";
  const templates = useApi<PandaTemplates>(connected ? "/pandadoc/templates" : null);
  const current = extra.settings?.quote_template_id ?? "";
  const [value, setValue] = useState(current);
  const [busy, setBusy] = useState(false);
  useEffect(() => setValue(current), [current]);

  async function save() {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail.integration.key, { quote_template_id: value || null }));
      toast(value ? "Quotes now go out in that template." : "Default quote template cleared.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Quotes in PandaDoc" icon={<LayoutTemplate size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          PandaDoc builds quotes from one of your templates: the quote&apos;s lines fill its pricing table and tokens like <code>[Quote.Total]</code> and{" "}
          <code>[Client.FirstName]</code> are filled in. People can still pick another template when they send.
        </p>
        {!connected ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Connect PandaDoc to choose a template.
          </p>
        ) : (
          <SettingRow label="Default quote template" hint={extra.settings?.quote_template_name ? `Now: ${extra.settings.quote_template_name}` : "None yet: senders must pick one."}>
            <div className="ui-row" style={{ gap: 8 }}>
              <select className="ui-select sm" value={value} disabled={!detail.can_manage || (templates.loading && !templates.data)} onChange={(event) => setValue(event.target.value)} style={{ width: 240 }} aria-label="Default quote template">
                <option value="">No default</option>
                {value && !templates.data?.items.some((t) => t.id === value) ? <option value={value}>{extra.settings?.quote_template_name ?? value}</option> : null}
                {(templates.data?.items ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              {detail.can_manage ? (
                <button type="button" className="ui-btn ui-btn-sm" disabled={busy || value === current} onClick={() => void save()}>
                  {busy ? <span className="ui-spinner" /> : <Save size={14} />} Save
                </button>
              ) : null}
            </div>
          </SettingRow>
        )}
        {templates.error ? <p className="ui-small" style={{ margin: 0, color: "var(--danger)" }}>{templates.error}</p> : null}
        {extra.tokens?.length ? (
          <details className="isal-tokens">
            <summary>Tokens AIVA fills in</summary>
            <div className="isal-token-list">
              {extra.tokens.map((token) => (
                <code key={token}>[{token}]</code>
              ))}
            </div>
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Add them to a template in PandaDoc; tokens a template doesn&apos;t use are left out. Signers take the template&apos;s roles in order.
            </p>
          </details>
        ) : null}
      </div>
    </Card>
  );
}

function PandaDocPanel({ detail, setDetail, reload }: PanelProps) {
  const extra = detail.extra as Partial<SignExtra> & { error?: string };
  if (!detail.enabled) return null;
  const account = extra.account ?? {};
  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      <SignRequestsCard
        detail={detail}
        extra={extra}
        reload={reload}
        footnote={account.email ? `Sending as ${account.email}${account.workspace ? ` · workspace ${account.workspace}` : ""}` : null}
      />
      <PandaQuoteTemplateCard detail={detail} extra={extra} setDetail={setDetail} />
      {detail.connection ? (
        <SignUpdatesCard
          detail={detail}
          extra={extra}
          setDetail={setDetail}
          endpoint="/pandadoc/webhook"
          vendor="PandaDoc"
          pushNote="AIVA subscribed to PandaDoc's document updates (signed with a shared key AIVA checks on every call)."
        />
      ) : null}
      <TagHelp title="Signature fields in uploaded documents" tags={SIGN_TAGS.pandadoc}>
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          These are PandaDoc field tags. Uploaded PDFs and Word files need them, or there&apos;s nowhere to sign.
        </p>
      </TagHelp>
      {detail.can_manage && extra.settings ? (
        <SignSettingsCard detail={detail} initial={extra.settings} setDetail={setDetail} fields={["save_signed_copy", "mark_quote_accepted", "notify_sender"]} />
      ) : null}
    </>
  );
}

// ===========================================================================
// Sales intelligence: Apollo.io and ZoomInfo
// ===========================================================================

type IntelDefaults = { lead_source: string | null; owner_user_id: string | null; import_as: "lead" | "contact" };

type IntelExtra = {
  defaults: IntelDefaults;
  waterfall: { enabled: boolean; position: number | null; providers: number };
  linked_records: number;
  credits_remaining: number | null;
  max_import: number;
  notes: string;
};

function IntelDefaultsCard({ detail, value, setDetail }: { detail: IntegrationDetail; value: IntelDefaults; setDetail: (d: IntegrationDetail) => void }) {
  const toast = useToast();
  const { members } = useMembers();
  const [draft, setDraft] = useState<IntelDefaults>(value);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(value), [value]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(value);
  const vendor = detail.integration.vendor;

  async function save() {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail.integration.key, { lead_source: draft.lead_source || null, owner_user_id: draft.owner_user_id || null, import_as: draft.import_as }));
      toast("Import settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Imported prospects"
      icon={<UserPlus size={17} />}
      footer={
        <div className="isal-save-row">
          <span className="ui-muted ui-small">{dirty ? "Unsaved changes" : "People importing can still change these for one import."}</span>
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy} onClick={() => void save()}>
            {busy ? <span className="ui-spinner" /> : <Save size={14} />} Save
          </button>
        </div>
      }
    >
      <div className="settings-rows">
        <SettingRow label="Import people as" hint="Contacts are filed under an account matched by website or name (created when missing).">
          <select className="ui-select sm" value={draft.import_as} onChange={(e) => setDraft({ ...draft, import_as: e.target.value as IntelDefaults["import_as"] })} style={{ width: 200 }}>
            <option value="lead">Leads</option>
            <option value="contact">Contacts</option>
          </select>
        </SettingRow>
        <SettingRow label="Lead source" hint={`Shown on the lead and in reports. Default: ${detail.integration.key}.`}>
          <input className="ui-input sm" value={draft.lead_source ?? ""} maxLength={80} placeholder={detail.integration.key} onChange={(e) => setDraft({ ...draft, lead_source: e.target.value || null })} style={{ width: 200 }} />
        </SettingRow>
        <SettingRow label="Owner" hint={`Who imported ${vendor} records belong to. Empty: whoever imports them.`}>
          <select className="ui-select sm" value={draft.owner_user_id ?? ""} onChange={(e) => setDraft({ ...draft, owner_user_id: e.target.value || null })} style={{ width: 200 }}>
            <option value="">Whoever imports them</option>
            {activeMembers(members).map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.full_name}
              </option>
            ))}
          </select>
        </SettingRow>
      </div>
    </Card>
  );
}

function SalesIntelPanel({ detail, setDetail }: PanelProps) {
  const extra = detail.extra as Partial<IntelExtra> & { error?: string };
  if (!detail.enabled) return null;
  const key = detail.integration.key as ProspectProviderKey;
  const connected = detail.connection?.status === "connected";
  const waterfall = extra.waterfall;
  return (
    <>
      {extra.error ? <Alert tone="warning">{extra.error}</Alert> : null}
      <Card
        title={`${detail.integration.vendor} in AIVA`}
        icon={<Coins size={17} />}
        actions={
          <Link className="ui-btn ui-btn-sm ui-btn-ghost" href="/settings/enrichment">
            Enrichment order
          </Link>
        }
      >
        <div className="ui-stack" style={{ gap: 14 }}>
          <div className="settings-kpis three">
            <Kpi
              label="Waterfall enrichment"
              value={waterfall?.enabled && waterfall.position ? `#${waterfall.position} of ${waterfall.providers}` : "Off"}
              note={waterfall?.enabled ? "Asked in this order when records are enriched" : "Turn it on in the enrichment order"}
            />
            <Kpi label="Records from here" value={(extra.linked_records ?? 0).toLocaleString()} note="Imported or enriched" />
            <Kpi label="AIVA credits left" value={extra.credits_remaining === null || extra.credits_remaining === undefined ? "—" : extra.credits_remaining.toLocaleString()} note="1 per record filled or imported" />
          </div>
          {extra.notes ? <p className="ui-muted ui-small" style={{ margin: 0 }}>{extra.notes}</p> : null}
        </div>
      </Card>
      {connected ? (
        <Card title="Find prospects" icon={<Search size={17} />}>
          <ProspectSearch provider={key} />
        </Card>
      ) : null}
      {detail.can_manage && extra.defaults ? <IntelDefaultsCard detail={detail} value={extra.defaults} setDetail={setDetail} /> : null}
    </>
  );
}

// ===========================================================================
// LinkedIn Lead Gen Forms
// ===========================================================================

type Target = { key: string; label: string };
type LeadDefaultsValue = { lead_source: string; owner_user_id: string | null; on_duplicate: "update" | "skip" | "create"; tags: string[] };
type FormSettings = { enabled?: boolean; lead_source?: string | null; owner_user_id?: string | null; tags?: string[]; mapping?: Record<string, string> };
type LiQuestion = { id: string; key: string; name: string; label: string; predefined: string | null; options: Record<string, string> };
type LiForm = { id: string; name: string; state: string | null; account_id: string; questions: LiQuestion[] };
type LiAccount = { id: string; name: string; status: string | null; currency: string | null; sync: boolean; forms?: number };

type LinkedInExtra = {
  accounts: LiAccount[];
  lead_forms: Record<string, LiForm>;
  defaults: LeadDefaultsValue;
  forms: Record<string, FormSettings>;
  targets: Target[];
  custom_fields: Target[];
  last_sync_at: string | null;
  accounts_synced_at: string | null;
  sync_every_minutes: number;
  api_version: string;
};

const DUPLICATES: { value: LeadDefaultsValue["on_duplicate"]; label: string; hint: string }[] = [
  { value: "update", label: "Update the existing lead", hint: "Fill in what's missing and add the answers to its notes." },
  { value: "skip", label: "Skip it", hint: "Keep the existing lead as it is." },
  { value: "create", label: "Create another lead", hint: "Every submission becomes its own lead." },
];

function LeadDefaultsCard({ detail, setDetail, value }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void; value: LeadDefaultsValue }) {
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
      setDetail(await saveSettings(detail.integration.key, { defaults: draft }));
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
          <div className="isal-save-row">
            <span className="ui-muted ui-small">{dirty ? "Unsaved changes" : "Applies to every form unless a form says otherwise."}</span>
            <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy} onClick={() => void save()}>
              {busy ? <span className="ui-spinner" /> : <Save size={14} />} Save
            </button>
          </div>
        ) : undefined
      }
    >
      <div className="settings-rows">
        <SettingRow label="Lead source" hint="Shown on the lead and in reports. Default: linkedin_ads.">
          <input className="ui-input sm" value={draft.lead_source} disabled={locked} maxLength={80} onChange={(e) => setDraft({ ...draft, lead_source: e.target.value })} style={{ width: 220 }} />
        </SettingRow>
        <SettingRow label="Lead owner" hint="Who new leads are assigned to. Assignment rules and territories still apply.">
          <select className="ui-select sm" value={draft.owner_user_id ?? ""} disabled={locked} onChange={(e) => setDraft({ ...draft, owner_user_id: e.target.value || null })} style={{ width: 220 }}>
            <option value="">Whoever connected LinkedIn</option>
            {activeMembers(members).map((m) => (
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
          <TagsInput value={draft.tags} disabled={locked} onChange={(tags) => setDraft({ ...draft, tags })} placeholder="linkedin, paid-social" label="Tags" />
        </SettingRow>
      </div>
    </Card>
  );
}

function MappingEditor({
  questions,
  mapping,
  onChange,
  targets,
  customFields,
  disabled,
}: {
  questions: LiQuestion[];
  mapping: Record<string, string>;
  onChange: (mapping: Record<string, string>) => void;
  targets: Target[];
  customFields: Target[];
  disabled?: boolean;
}) {
  if (!questions.length) return <p className="ui-muted ui-small" style={{ margin: 0 }}>This form has no questions AIVA can read.</p>;
  const standard = targets.filter((t) => t.key !== "ignore");
  return (
    <div className="isal-map" role="table" aria-label="Question mapping">
      <div className="isal-map-row head" role="row">
        <span role="columnheader">Question</span>
        <span role="columnheader">Goes to</span>
      </div>
      {questions.map((question) => (
        <div key={question.id} className="isal-map-row" role="row">
          <span role="cell" className="isal-map-field">
            <strong>{question.label || question.name}</strong>
            <span className="ui-muted ui-small">{question.predefined ? "Standard LinkedIn field" : Object.keys(question.options).length ? `${Object.keys(question.options).length} choices` : "Custom question"}</span>
          </span>
          <span role="cell">
            <select
              className="ui-select sm"
              aria-label={`Where ${question.label || question.name} goes`}
              value={mapping[question.key] ?? ""}
              disabled={disabled}
              onChange={(e) => {
                const next = { ...mapping };
                if (e.target.value) next[question.key] = e.target.value;
                else delete next[question.key];
                onChange(next);
              }}
            >
              <option value="">{question.predefined ? "Detect automatically" : "Add to the lead's notes"}</option>
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

function FormEditor({ form, value, onChange, extra, disabled }: { form: LiForm; value: FormSettings; onChange: (value: FormSettings) => void; extra: LinkedInExtra; disabled?: boolean }) {
  const { members } = useMembers();
  const on = value.enabled !== false;
  const mapped = Object.keys(value.mapping ?? {}).length;
  return (
    <details className="isal-form">
      <summary className="isal-form-head">
        <span className="isal-form-icon" aria-hidden>
          <FileText size={16} />
        </span>
        <span className="isal-form-title">
          <strong>{form.name}</strong>
          <span className="ui-muted ui-small">
            {(form.state ?? "form").toLowerCase()} · {form.questions.length} question{form.questions.length === 1 ? "" : "s"}
          </span>
        </span>
        {!on ? <Badge tone="neutral">Not imported</Badge> : mapped ? <Badge tone="info">{mapped} mapped</Badge> : null}
        <ChevronDown size={16} className="isal-chevron" aria-hidden />
      </summary>
      <div className="isal-form-body">
        <div className="settings-rows">
          <SettingRow label="Import this form" hint="Off: responses to this form don't create leads.">
            <Toggle checked={on} disabled={disabled} onChange={(next) => onChange({ ...value, enabled: next })} label={`Import ${form.name}`} />
          </SettingRow>
          <SettingRow label="Lead source" hint="Leave blank to use the default.">
            <input className="ui-input sm" value={value.lead_source ?? ""} disabled={disabled} maxLength={80} placeholder={extra.defaults.lead_source} onChange={(e) => onChange({ ...value, lead_source: e.target.value || null })} style={{ width: 200 }} />
          </SettingRow>
          <SettingRow label="Lead owner">
            <select className="ui-select sm" value={value.owner_user_id ?? ""} disabled={disabled} onChange={(e) => onChange({ ...value, owner_user_id: e.target.value || null })} style={{ width: 200 }}>
              <option value="">Default owner</option>
              {activeMembers(members).map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.full_name}
                </option>
              ))}
            </select>
          </SettingRow>
          <SettingRow label="Extra tags" stacked>
            <TagsInput value={value.tags ?? []} disabled={disabled} onChange={(tags) => onChange({ ...value, tags })} placeholder="webinar-q4" label={`Tags for ${form.name}`} />
          </SettingRow>
        </div>
        <MappingEditor
          questions={form.questions}
          mapping={value.mapping ?? {}}
          onChange={(mapping) => onChange({ ...value, mapping })}
          targets={extra.targets}
          customFields={extra.custom_fields}
          disabled={disabled}
        />
      </div>
    </details>
  );
}

function LinkedInLeadsPanel({ detail, setDetail }: PanelProps) {
  const toast = useToast();
  const extra = detail.extra as unknown as LinkedInExtra & { error?: string };
  const savedForms = useMemo(() => extra.forms ?? {}, [extra.forms]);
  const [draft, setDraft] = useState<Record<string, FormSettings>>(savedForms);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => setDraft(savedForms), [savedForms]);
  if (!detail.enabled) return null;
  if (!("accounts" in detail.extra)) return detail.extra.error ? <Alert>{detail.extra.error}</Alert> : null;
  const manage = detail.can_manage;
  const connected = detail.connection?.status === "connected";
  const synced = extra.accounts.filter((a) => a.sync);
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedForms);

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

  const refresh = () => run("refresh", async () => setDetail(await api.post<IntegrationDetail>("/linkedin-leads/accounts/refresh")));
  const sync = () =>
    run("sync", async () => {
      const result = await api.post<IntegrationDetail & { imported: number }>("/linkedin-leads/sync");
      toast(result.imported ? `${result.imported} new lead${result.imported === 1 ? "" : "s"} from LinkedIn.` : "No new leads since the last check.");
      const { imported: _imported, ...next } = result; // eslint-disable-line @typescript-eslint/no-unused-vars
      setDetail(next as IntegrationDetail);
    });
  const toggle = (account: LiAccount, next: boolean) =>
    run(`account:${account.id}`, async () => {
      setDetail(await api.put<IntegrationDetail>(`/linkedin-leads/accounts/${account.id}`, { sync: next }));
      toast(next ? `Leads from ${account.name} now come to AIVA.` : `Stopped syncing ${account.name}.`);
    });
  const reloadForms = (account: LiAccount) =>
    run(`forms:${account.id}`, async () => {
      // Turning sync on again re-reads the account's lead forms.
      setDetail(await api.put<IntegrationDetail>(`/linkedin-leads/accounts/${account.id}`, { sync: true }));
      toast(`Lead forms for ${account.name} reloaded.`);
    });
  const saveForms = () =>
    run("forms", async () => {
      setDetail(await saveSettings(detail.integration.key, { forms: draft }));
      toast("Form settings saved.");
    });

  if (!connected) return null;
  return (
    <>
      <Card
        title="Ad accounts"
        flush
        actions={
          manage ? (
            <div className="ui-row" style={{ gap: 6 }}>
              <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={Boolean(busy)} onClick={() => void refresh()}>
                {busy === "refresh" ? <span className="ui-spinner" /> : <RefreshCcw size={14} />} Refresh
              </button>
              <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy) || !synced.length} onClick={() => void sync()} title="Ask LinkedIn for new form responses now">
                {busy === "sync" ? <span className="ui-spinner" /> : <RotateCw size={14} />} Check for leads now
              </button>
            </div>
          ) : null
        }
      >
        {extra.accounts.length ? (
          <ul className="isal-accounts">
            {extra.accounts.map((account) => (
              <li key={account.id}>
                <div className="isal-account-main">
                  <strong>{account.name}</strong>
                  <span className="ui-muted ui-small">
                    {account.id}
                    {account.status ? ` · ${account.status.toLowerCase()}` : ""}
                    {account.sync && typeof account.forms === "number" ? ` · ${account.forms} lead form${account.forms === 1 ? "" : "s"}` : ""}
                  </span>
                </div>
                {busy === `account:${account.id}` ? <span className="ui-spinner" /> : null}
                <Toggle checked={account.sync} disabled={!manage || Boolean(busy)} onChange={(next) => void toggle(account, next)} label={`Sync leads from ${account.name}`} />
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No ad accounts" message="The connected LinkedIn member can't see any ad accounts. Reconnect with someone who has a role on your ad account." />
        )}
        <div className="isal-card-foot ui-muted ui-small">
          {extra.last_sync_at ? `Last checked ${relativeTime(extra.last_sync_at)}. ` : ""}
          AIVA checks LinkedIn for new form responses every {extra.sync_every_minutes ?? 10} minutes.
          {extra.accounts_synced_at ? ` Account list from ${dateTime(extra.accounts_synced_at)}.` : ""}
        </div>
      </Card>

      <LeadDefaultsCard detail={detail} setDetail={setDetail} value={extra.defaults} />

      {synced.length ? (
        <Card
          title="Lead forms"
          footer={
            manage ? (
              <div className="isal-save-row">
                <span className="ui-muted ui-small">{dirty ? "Unsaved changes" : "Name, email, phone, company and job title questions are mapped automatically."}</span>
                <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy === "forms"} onClick={() => void saveForms()}>
                  {busy === "forms" ? <span className="ui-spinner" /> : <Save size={14} />} Save forms
                </button>
              </div>
            ) : undefined
          }
        >
          <div className="ui-stack" style={{ gap: 16 }}>
            {synced.map((account) => {
              const forms = Object.values(extra.lead_forms ?? {}).filter((f) => f.account_id === account.id);
              return (
                <section key={account.id} className="ui-stack" style={{ gap: 8 }}>
                  <div className="isal-subhead">
                    <strong>{account.name}</strong>
                    {manage ? (
                      <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={Boolean(busy)} onClick={() => void reloadForms(account)} title="Load this account's lead forms again">
                        {busy === `forms:${account.id}` ? <span className="ui-spinner" /> : <RefreshCcw size={13} />} Reload forms
                      </button>
                    ) : null}
                  </div>
                  {forms.length ? (
                    forms.map((form) => (
                      <FormEditor key={form.id} form={form} value={draft[form.id] ?? {}} onChange={(value) => setDraft({ ...draft, [form.id]: value })} extra={extra} disabled={!manage} />
                    ))
                  ) : (
                    <p className="ui-muted ui-small" style={{ margin: 0 }}>
                      This ad account has no lead forms yet.
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

/** Provider panels for this group, by integration key. */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  adobe_sign: AdobeSignPanel,
  pandadoc: PandaDocPanel,
  apollo: SalesIntelPanel,
  zoominfo: SalesIntelPanel,
  linkedin_lead_gen: LinkedInLeadsPanel,
};
