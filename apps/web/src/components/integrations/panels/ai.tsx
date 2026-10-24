"use client";

import Link from "next/link";
import { Bot, Coins, FlaskConical, Gauge, Plus, RefreshCcw, Route, Save, Send, ShieldCheck, SlidersHorizontal, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";

import { useToast } from "@/components/providers";
import { NumberInput, SettingRow, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, Kpi } from "@/components/ui";
import { AI_SOURCE_LABELS } from "@/lib/ai-source";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage } from "@/lib/use-api";

import type { IntegrationDetail, PanelProps } from "../types";

// ===========================================================================
// Shapes from the ai_models provider (apps/api/app/integrations/providers/ai_models.py)
// ===========================================================================

type ProviderKey = "openai" | "azure_openai";
type Policy = "aiva_default" | "rules_only";

type AiSettings = {
  default_model: string;
  feature_models: Record<string, string>;
  temperature: number | null;
  max_output_tokens: number;
  reasoning_effort: string;
  timeout_seconds: number;
  limit_type: "none" | "tokens" | "spend";
  monthly_token_limit: number | null;
  monthly_spend_limit: string | null;
  at_limit: Policy;
  on_error: Policy;
  redact_emails: boolean;
  redact_phones: boolean;
  hipaa_acknowledged: boolean;
  hipaa_acknowledged_at: string | null;
  hipaa_acknowledged_by: string | null;
  prices: Record<string, { input: string; output: string }>;
  region: "default" | "eu";
  api_mode: "v1" | "deployments";
  api_version: string;
};

type UsageRow = { requests: number; errors: number; input_tokens: number; output_tokens: number; tokens: number; cost: number; priced?: boolean };

export type ModelUsage = {
  period: string;
  totals: UsageRow & { unpriced_calls: number };
  by_model: (UsageRow & { provider: ProviderKey; provider_label: string; model: string; price: [number, number] | null })[];
  by_feature: (UsageRow & { feature: string; label: string })[];
  by_user: (UsageRow & { user_id: string | null; name: string })[];
  limit: LimitState | null;
  only_mine: boolean;
};

type LimitState = {
  type: "none" | "tokens" | "spend";
  limit: number | null;
  used: number;
  spent?: number;
  remaining: number | null;
  reached: boolean;
  unpriced_calls: number;
};

type AiExtra = {
  settings: AiSettings;
  features: { key: string; label: string; description: string; model: string; effective: string | null }[];
  models: string[];
  models_listed_at: string | null;
  auth: "key" | "entra" | null;
  endpoint: string | null;
  active: boolean;
  state: { provider: ProviderKey | null; reason: string; message: string };
  other: { key: ProviderKey; name: string; enabled: boolean };
  default_source: "claude" | "rules";
  hipaa: { workspace: boolean; acknowledged: boolean; acknowledged_at: string | null };
  usage: ModelUsage;
  prices: Record<string, [number, number]>;
  default_prices: Record<string, [number, number]>;
  efforts: string[];
  azure: { default_api_version: string; hosts: string[] } | null;
  can_manage: boolean;
  error?: string;
};

type TestResult = {
  text: string;
  provider_label: string;
  model: string;
  served_by: string;
  redacted: boolean;
  usage: { input_tokens: number; output_tokens: number; reasoning_tokens: number; latency_ms: number; cost: number | null };
};

const USE_AIVA = "aiva";
const EFFORT_LABELS: Record<string, string> = { "": "Model default", none: "None", minimal: "Minimal", low: "Low", medium: "Medium", high: "High" };
const POLICY_LABELS: Record<Policy, string> = { aiva_default: "AIVA's default model", rules_only: "Built-in rules only" };

// ===========================================================================
// Helpers
// ===========================================================================

/** US dollars with enough precision for small amounts. */
export function usd(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "$0.00";
  return value < 1 ? `$${value.toFixed(4)}` : `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function tokens(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  return value.toLocaleString();
}

function defaultLabel(source: "claude" | "rules") {
  return source === "claude" ? "AIVA's default model (Claude)" : "AIVA's built-in rules";
}

async function saveSettings(key: string, config: Partial<AiSettings>) {
  return api.put<IntegrationDetail>(`/integration-hub/${key}/settings`, { config });
}

/** Local edits to a slice of the settings, saved together. */
function useSlice<K extends keyof AiSettings>(detail: IntegrationDetail, setDetail: (d: IntegrationDetail) => void, keys: readonly K[], what: string) {
  const toast = useToast();
  const extra = detail.extra as unknown as AiExtra;
  const saved = useMemo(() => {
    const out = {} as Pick<AiSettings, K>;
    for (const key of keys) out[key] = extra.settings[key];
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extra.settings]);
  const [draft, setDraft] = useState(saved);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(saved), [saved]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  async function save(override?: Partial<Pick<AiSettings, K>>) {
    setBusy(true);
    try {
      setDetail(await saveSettings(detail.integration.key, { ...draft, ...override }));
      toast(`${what} saved.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const set = <F extends K>(field: F, value: AiSettings[F]) => setDraft((current) => ({ ...current, [field]: value }));
  return { draft, set, setDraft, dirty, busy, save, reset: () => setDraft(saved) };
}

function SaveRow({ dirty, busy, onSave, onReset, note }: { dirty: boolean; busy: boolean; onSave: () => void; onReset: () => void; note?: ReactNode }) {
  return (
    <div className="iai-save">
      <span className="ui-small ui-muted">{note}</span>
      <span className="ui-row" style={{ gap: 8 }}>
        <button type="button" className="ui-btn ui-btn-sm" disabled={!dirty || busy} onClick={onReset}>
          Undo
        </button>
        <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={!dirty || busy} onClick={onSave}>
          <Save size={14} /> {busy ? "Saving…" : "Save"}
        </button>
      </span>
    </div>
  );
}

// ===========================================================================
// Status: which provider runs AIVA's AI, and what happens when it can't
// ===========================================================================

function StatusCard({ detail, extra }: { detail: IntegrationDetail; extra: AiExtra }) {
  const name = detail.integration.name;
  const ready = extra.active;
  const fallback = defaultLabel(extra.default_source);
  return (
    <Card title="Running AIVA's AI" icon={<Bot size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <div className="iai-state">
          <Badge tone={ready ? "success" : extra.state.reason === "limit" ? "warning" : "neutral"} dot>
            {ready ? `On ${name}` : extra.state.reason === "limit" ? "Monthly limit reached" : "Not in use yet"}
          </Badge>
          <span className="ui-small">{ready ? `AI features use the models below. Anything left on the default uses ${fallback}.` : extra.state.message}</span>
        </div>
        {extra.other.enabled ? (
          <Alert tone="warning">
            {extra.other.name} is also switched on. Only one provider runs AIVA&apos;s AI at a time; switching this one off and on again makes it the active provider.
          </Alert>
        ) : (
          <p className="ui-small ui-muted iai-note">
            One provider at a time: turning {name} on turns <Link href={`/integrations/${extra.other.key}`}>{extra.other.name}</Link> off. When {name} can&apos;t answer, AIVA follows the
            error policy below; without a provider, AI features use {fallback}.
          </p>
        )}
        {detail.integration.key === "azure_openai" && detail.connection ? (
          <dl className="iai-facts">
            <dt>Endpoint</dt>
            <dd className="ui-mono">{extra.endpoint ?? "—"}</dd>
            <dt>Signs in with</dt>
            <dd>{extra.auth === "entra" ? "Microsoft Entra ID (client credentials)" : "API key"}</dd>
          </dl>
        ) : null}
      </div>
    </Card>
  );
}

// ===========================================================================
// Models: the default, and one per feature
// ===========================================================================

function ModelInput({ id, value, onChange, models, disabled, placeholder, label }: { id: string; value: string; onChange: (v: string) => void; models: string[]; disabled: boolean; placeholder: string; label: string }) {
  return (
    <>
      <input
        className="ui-input sm iai-model-input"
        list={`${id}-models`}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        aria-label={label}
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => onChange(event.target.value.trim())}
      />
      <datalist id={`${id}-models`}>
        {models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
    </>
  );
}

function ModelsCard({ detail, setDetail, extra }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void; extra: AiExtra }) {
  const toast = useToast();
  const key = detail.integration.key as ProviderKey;
  const azure = key === "azure_openai";
  const noun = azure ? "deployment" : "model";
  const slice = useSlice(detail, setDetail, ["default_model", "feature_models"] as const, "Models");
  const [refreshing, setRefreshing] = useState(false);
  const [models, setModels] = useState(extra.models);
  useEffect(() => setModels(extra.models), [extra.models]);
  const locked = !detail.can_manage;

  async function refresh() {
    setRefreshing(true);
    try {
      const result = await api.get<{ models: string[] }>(`/ai-models/${key}/models?refresh=true`);
      setModels(result.models);
      toast(`${result.models.length} ${azure ? "models on the resource" : "text models available"}.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setRefreshing(false);
    }
  }

  function mode(feature: string) {
    const chosen = slice.draft.feature_models;
    if (!(feature in chosen)) return "default";
    return chosen[feature] === USE_AIVA ? "aiva" : "model";
  }

  /** null: use the default model. A blank name while typing counts as the default when saved. */
  function setFeature(feature: string, value: string | null) {
    const next = { ...slice.draft.feature_models };
    if (value === null) delete next[feature];
    else next[feature] = value;
    slice.set("feature_models", next);
  }

  const fallback = defaultLabel(extra.default_source);
  return (
    <Card
      title={azure ? "Deployments" : "Models"}
      icon={<Route size={17} />}
      actions={
        detail.connection && detail.can_manage ? (
          <button type="button" className="ui-btn ui-btn-sm" disabled={refreshing} onClick={() => void refresh()}>
            <RefreshCcw size={14} /> {refreshing ? "Checking…" : azure ? "Check resource" : "Refresh models"}
          </button>
        ) : null
      }
    >
      <div className="ui-stack" style={{ gap: 14 }}>
        <p className="ui-small ui-muted iai-note">
          {azure
            ? "Enter the deployment names from Azure AI Foundry (Deployments). Each feature can use its own deployment; the live test checks every one."
            : models.length
              ? `Pick from the ${models.length} text models this key can use${extra.models_listed_at ? ` (checked ${relativeTime(extra.models_listed_at)})` : ""}, or type a model ID such as a fine-tuned model.`
              : "Connect a key to see the models it can use, or type a model ID."}
        </p>
        <SettingRow label={`Default ${noun}`} hint={`Used by every feature that doesn't have its own ${noun}.`}>
          <ModelInput id={`${key}-default`} value={slice.draft.default_model} onChange={(v) => slice.set("default_model", v)} models={models} disabled={locked} placeholder={azure ? "e.g. crm-gpt-4o-mini" : "e.g. gpt-5-mini"} label={`Default ${noun}`} />
        </SettingRow>
        <div className="iai-features" role="table" aria-label="AI features">
          {extra.features.map((feature) => {
            const current = mode(feature.key);
            return (
              <div key={feature.key} className="iai-feature" role="row">
                <div className="iai-feature-text" role="cell">
                  <strong>{feature.label}</strong>
                  <span className="ui-small ui-muted">{feature.description}</span>
                </div>
                <div className="iai-feature-control" role="cell">
                  <select
                    className="ui-select sm"
                    aria-label={`${feature.label}: which model`}
                    value={current}
                    disabled={locked}
                    onChange={(event) => {
                      const next = event.target.value;
                      if (next === "aiva") setFeature(feature.key, USE_AIVA);
                      else if (next === "model") setFeature(feature.key, slice.draft.default_model || models[0] || "");
                      else setFeature(feature.key, null);
                    }}
                  >
                    <option value="default">Default {noun}{slice.draft.default_model ? ` (${slice.draft.default_model})` : ""}</option>
                    <option value="model">Its own {noun}</option>
                    <option value="aiva">{fallback}</option>
                  </select>
                  {current === "model" ? (
                    <ModelInput
                      id={`${key}-${feature.key}`}
                      value={slice.draft.feature_models[feature.key] ?? ""}
                      onChange={(v) => setFeature(feature.key, v)}
                      models={models}
                      disabled={locked}
                      placeholder={noun}
                      label={`${feature.label} ${noun}`}
                    />
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
        {detail.can_manage ? <SaveRow dirty={slice.dirty} busy={slice.busy} onSave={() => void slice.save()} onReset={slice.reset} /> : null}
      </div>
    </Card>
  );
}

// ===========================================================================
// Behaviour: temperature, output cap, reasoning, timeout, endpoint options
// ===========================================================================

function BehaviourCard({ detail, setDetail, extra }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void; extra: AiExtra }) {
  const azure = detail.integration.key === "azure_openai";
  const keys = azure
    ? (["temperature", "max_output_tokens", "reasoning_effort", "timeout_seconds", "api_mode", "api_version"] as const)
    : (["temperature", "max_output_tokens", "reasoning_effort", "timeout_seconds", "region"] as const);
  const slice = useSlice(detail, setDetail, keys as readonly (keyof AiSettings)[], "Model settings");
  const draft = slice.draft as Partial<AiSettings>;
  const locked = !detail.can_manage;
  return (
    <Card title="How the model writes" icon={<SlidersHorizontal size={17} />}>
      <div className="settings-rows">
        <SettingRow label="Temperature" hint="Blank uses the model's default. Reasoning models (o-series, GPT-5 and later) ignore it.">
          <NumberInput label="Temperature" value={draft.temperature ?? null} allowEmpty min={0} max={2} step={0.1} width={110} disabled={locked} onChange={(v) => slice.set("temperature", v === null ? null : Math.round(v * 100) / 100)} />
        </SettingRow>
        <SettingRow label="Output limit" hint="The most a model may write for one request, reasoning included. Features ask for less (summaries ~350, drafts ~450).">
          <NumberInput label="Output limit" value={draft.max_output_tokens ?? 2000} min={256} max={32000} step={100} width={120} suffix="tokens" disabled={locked} onChange={(v) => slice.set("max_output_tokens", v ?? 2000)} />
        </SettingRow>
        <SettingRow label="Reasoning effort" hint={azure ? "Only for reasoning deployments (o-series, GPT-5 and later); leave on the model default otherwise." : "For reasoning models. Lower is faster and cheaper; AIVA's CRM tasks rarely need more than low."}>
          <select className="ui-select sm" aria-label="Reasoning effort" value={draft.reasoning_effort ?? ""} disabled={locked} onChange={(e) => slice.set("reasoning_effort", e.target.value)} style={{ width: 160 }}>
            {["", ...extra.efforts].map((effort) => (
              <option key={effort} value={effort}>
                {EFFORT_LABELS[effort] ?? effort}
              </option>
            ))}
          </select>
        </SettingRow>
        <SettingRow label="Timeout" hint="AIVA never waits longer than this, retries included; then the error policy applies.">
          <NumberInput label="Timeout" value={draft.timeout_seconds ?? 30} min={5} max={120} width={90} suffix="seconds" disabled={locked} onChange={(v) => slice.set("timeout_seconds", v ?? 30)} />
        </SettingRow>
        {azure ? (
          <>
            <SettingRow label="API" hint="The v1 API needs no version. Use the deployments route for resources that haven't moved to v1.">
              <select className="ui-select sm" aria-label="Azure API" value={draft.api_mode ?? "v1"} disabled={locked} onChange={(e) => slice.set("api_mode", e.target.value as AiSettings["api_mode"])} style={{ width: 200 }}>
                <option value="v1">v1 (recommended)</option>
                <option value="deployments">Deployments + api-version</option>
              </select>
            </SettingRow>
            {draft.api_mode === "deployments" ? (
              <SettingRow label="API version" hint={`Latest GA: ${extra.azure?.default_api_version ?? "2024-10-21"}.`}>
                <input className="ui-input sm" aria-label="API version" value={draft.api_version ?? ""} disabled={locked} onChange={(e) => slice.set("api_version", e.target.value.trim())} style={{ width: 200 }} />
              </SettingRow>
            ) : null}
          </>
        ) : (
          <SettingRow label="Data residency" hint="EU projects are served from eu.api.openai.com. The key must belong to an EU project.">
            <select className="ui-select sm" aria-label="Data residency" value={draft.region ?? "default"} disabled={locked} onChange={(e) => slice.set("region", e.target.value as AiSettings["region"])} style={{ width: 200 }}>
              <option value="default">Default (api.openai.com)</option>
              <option value="eu">Europe (eu.api.openai.com)</option>
            </select>
          </SettingRow>
        )}
      </div>
      {detail.can_manage ? <SaveRow dirty={slice.dirty} busy={slice.busy} onSave={() => void slice.save()} onReset={slice.reset} /> : null}
    </Card>
  );
}

// ===========================================================================
// Spending: monthly limit, what happens at the limit and on errors
// ===========================================================================

function LimitsCard({ detail, setDetail, extra }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void; extra: AiExtra }) {
  const slice = useSlice(detail, setDetail, ["limit_type", "monthly_token_limit", "monthly_spend_limit", "at_limit", "on_error"] as const, "Limits");
  const { draft } = slice;
  const locked = !detail.can_manage;
  const state = extra.usage.limit;
  const percent = state && state.limit ? Math.min(100, (Number(state.used) / state.limit) * 100) : 0;
  return (
    <Card title="Limits and fallbacks" icon={<Gauge size={17} />}>
      <div className="settings-rows">
        <SettingRow label="Monthly limit" hint="Covers both AI model providers together and resets on the 1st (UTC). Spend uses the price table below.">
          <span className="ui-row iai-limit" style={{ gap: 8 }}>
            <select className="ui-select sm" aria-label="Limit type" value={draft.limit_type} disabled={locked} onChange={(e) => slice.set("limit_type", e.target.value as AiSettings["limit_type"])} style={{ width: 130 }}>
              <option value="none">No limit</option>
              <option value="tokens">Tokens</option>
              <option value="spend">Spend (USD)</option>
            </select>
            {draft.limit_type === "tokens" ? (
              <NumberInput label="Monthly token limit" value={draft.monthly_token_limit} min={1000} step={100000} width={140} suffix="tokens" disabled={locked} onChange={(v) => slice.set("monthly_token_limit", v)} />
            ) : null}
            {draft.limit_type === "spend" ? (
              <span className="iai-money">
                <span aria-hidden>$</span>
                <input className="ui-input sm" inputMode="decimal" aria-label="Monthly spend limit in US dollars" value={draft.monthly_spend_limit ?? ""} disabled={locked} onChange={(e) => slice.set("monthly_spend_limit", e.target.value)} style={{ width: 110 }} />
              </span>
            ) : null}
          </span>
        </SettingRow>
        <SettingRow label="At the limit" hint="Until next month.">
          <select className="ui-select sm" aria-label="At the limit" value={draft.at_limit} disabled={locked} onChange={(e) => slice.set("at_limit", e.target.value as Policy)} style={{ width: 220 }}>
            {(Object.keys(POLICY_LABELS) as Policy[]).map((p) => (
              <option key={p} value={p}>
                {POLICY_LABELS[p]}
              </option>
            ))}
          </select>
        </SettingRow>
        <SettingRow label="When a request fails" hint="After retries (rate limits and outages), a timeout, or a blocked answer.">
          <select className="ui-select sm" aria-label="When a request fails" value={draft.on_error} disabled={locked} onChange={(e) => slice.set("on_error", e.target.value as Policy)} style={{ width: 220 }}>
            {(Object.keys(POLICY_LABELS) as Policy[]).map((p) => (
              <option key={p} value={p}>
                {POLICY_LABELS[p]}
              </option>
            ))}
          </select>
        </SettingRow>
      </div>
      {state && state.type !== "none" && state.limit ? (
        <div className={`iai-meter${state.reached ? " full" : percent >= 80 ? " high" : ""}`}>
          <div className="iai-meter-top">
            <span>This month</span>
            <strong>
              {state.type === "spend" ? `${usd(Number(state.used))} of ${usd(state.limit)}` : `${tokens(Number(state.used))} of ${tokens(state.limit)} tokens`}
            </strong>
          </div>
          <div className="iai-meter-bar" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100} aria-label="Monthly limit used">
            <div style={{ width: `${percent}%` }} />
          </div>
          {state.type === "spend" && state.unpriced_calls ? <span className="ui-small ui-muted">{state.unpriced_calls} calls have no price yet and aren&apos;t counted.</span> : null}
        </div>
      ) : null}
      {detail.can_manage ? <SaveRow dirty={slice.dirty} busy={slice.busy} onSave={() => void slice.save()} onReset={slice.reset} note="Limits are checked before each request, so a burst can run slightly over." /> : null}
    </Card>
  );
}

// ===========================================================================
// Data controls
// ===========================================================================

function DataCard({ detail, setDetail, extra }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void; extra: AiExtra }) {
  const azure = detail.integration.key === "azure_openai";
  const slice = useSlice(detail, setDetail, ["redact_emails", "redact_phones", "hipaa_acknowledged"] as const, "Data controls");
  const locked = !detail.can_manage;
  return (
    <Card title="Data controls" icon={<ShieldCheck size={17} />}>
      <div className="settings-rows">
        <SettingRow label="Redact email addresses" hint="Replaced with [email] before anything is sent. Drafts don't need them.">
          <Toggle label="Redact email addresses" checked={slice.draft.redact_emails} disabled={locked} onChange={(v) => slice.set("redact_emails", v)} />
        </SettingRow>
        <SettingRow label="Redact phone numbers" hint="Replaced with [phone]. Field prompts can't pick out a phone number while this is on.">
          <Toggle label="Redact phone numbers" checked={slice.draft.redact_phones} disabled={locked} onChange={(v) => slice.set("redact_phones", v)} />
        </SettingRow>
        {extra.hipaa.workspace || slice.draft.hipaa_acknowledged ? (
          <SettingRow
            label="HIPAA: a BAA covers this provider"
            hint={
              extra.hipaa.workspace
                ? `This workspace is in HIPAA mode, so CRM data only goes to ${detail.integration.name} after an admin confirms a Business Associate Agreement covers it${azure ? " (Microsoft's BAA covers Azure OpenAI)" : " (OpenAI offers BAAs with zero data retention)"}. Until then AIVA's default is used.`
                : "Kept for when HIPAA mode is on."
            }
          >
            <Toggle label="A BAA covers this provider" checked={slice.draft.hipaa_acknowledged} disabled={locked} onChange={(v) => slice.set("hipaa_acknowledged", v)} />
          </SettingRow>
        ) : null}
      </div>
      <ul className="iai-promises">
        <li>Encrypted fields are never sent; they&apos;re removed before a request leaves AIVA.</li>
        <li>AIVA doesn&apos;t store or log prompts or answers, only tokens, model, feature and person for usage.</li>
        {azure ? (
          <li>
            Azure OpenAI doesn&apos;t use your data to train models. Microsoft may keep prompts up to 30 days for abuse monitoring unless your subscription is approved for modified abuse
            monitoring.
          </li>
        ) : (
          <li>
            Requests are sent with <code>store: false</code>, so OpenAI doesn&apos;t keep responses for later retrieval. For zero data retention, ask OpenAI to enable it for your organization.
          </li>
        )}
      </ul>
      {extra.hipaa.acknowledged && extra.hipaa.acknowledged_at ? <p className="ui-small ui-muted iai-note">BAA confirmed {dateTime(extra.hipaa.acknowledged_at)}.</p> : null}
      {detail.can_manage ? <SaveRow dirty={slice.dirty} busy={slice.busy} onSave={() => void slice.save()} onReset={slice.reset} /> : null}
    </Card>
  );
}

// ===========================================================================
// Price table
// ===========================================================================

function PricesCard({ detail, setDetail, extra }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void; extra: AiExtra }) {
  const toast = useToast();
  const azure = detail.integration.key === "azure_openai";
  const settings = extra.settings;
  const inUse = useMemo(() => {
    const names = new Set<string>();
    if (settings.default_model) names.add(settings.default_model);
    Object.values(settings.feature_models).forEach((m) => m && m !== USE_AIVA && names.add(m));
    extra.usage.by_model.filter((m) => m.provider === detail.integration.key).forEach((m) => m.model && names.add(m.model));
    Object.keys(settings.prices).forEach((m) => names.add(m));
    return Array.from(names).sort();
  }, [settings, extra.usage.by_model, detail.integration.key]);
  const [rows, setRows] = useState<Record<string, { input: string; output: string }>>({});
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const next: Record<string, { input: string; output: string }> = {};
    for (const name of inUse) {
      const own = settings.prices[name];
      next[name] = own ? { input: String(Number(own.input)), output: String(Number(own.output)) } : { input: "", output: "" };
    }
    setRows(next);
  }, [inUse, settings.prices]);

  function listPrice(name: string) {
    const found = extra.prices[name] ?? extra.default_prices[name];
    return found ? found : null;
  }

  async function save() {
    const prices: Record<string, { input: string; output: string }> = {};
    for (const [name, value] of Object.entries(rows)) {
      if (value.input !== "" || value.output !== "") {
        const base = extra.default_prices[name];
        prices[name] = { input: value.input || String(base?.[0] ?? 0), output: value.output || String(base?.[1] ?? 0) };
      }
    }
    setBusy(true);
    try {
      setDetail(await saveSettings(detail.integration.key, { prices }));
      toast("Prices saved. Estimates and spend limits use them now.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Prices" icon={<Coins size={17} />} flush>
      <p className="ui-small ui-muted iai-pad">
        USD per million tokens, for cost estimates and spend limits. {azure ? "Azure prices depend on the deployment type and region: enter yours, or leave blank to use the list price of the model a deployment runs." : "Blank uses OpenAI's list price; enter yours if you have a negotiated rate."}
      </p>
      <div className="ui-table-wrap">
        <table className="ui-table iai-prices">
          <thead>
            <tr>
              <th>{azure ? "Deployment or model" : "Model"}</th>
              <th className="ui-num">Input</th>
              <th className="ui-num">Output</th>
              <th aria-label="Remove" />
            </tr>
          </thead>
          <tbody>
            {Object.keys(rows).length ? (
              Object.entries(rows).map(([name, value]) => {
                const list = listPrice(name);
                return (
                  <tr key={name}>
                    <td className="ui-mono">{name}</td>
                    {(["input", "output"] as const).map((side, index) => (
                      <td key={side} className="ui-num">
                        <input
                          className="ui-input sm iai-price"
                          inputMode="decimal"
                          aria-label={`${name} ${side} price`}
                          placeholder={list ? String(list[index]) : "—"}
                          value={value[side]}
                          disabled={!detail.can_manage}
                          onChange={(e) => setRows((current) => ({ ...current, [name]: { ...current[name], [side]: e.target.value } }))}
                        />
                      </td>
                    ))}
                    <td>
                      {settings.prices[name] && detail.can_manage ? (
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Use the list price for ${name}`} title="Use the list price" onClick={() => setRows((current) => ({ ...current, [name]: { input: "", output: "" } }))}>
                          <Trash2 size={14} />
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={4} className="ui-muted ui-small">
                  Choose a {azure ? "deployment" : "model"} first.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {detail.can_manage ? (
        <div className="iai-save iai-pad">
          <span className="ui-row" style={{ gap: 6 }}>
            <input className="ui-input sm" aria-label="Add a model or deployment" placeholder={azure ? "Deployment name" : "Model ID"} value={adding} onChange={(e) => setAdding(e.target.value.trim())} style={{ width: 180 }} />
            <button
              type="button"
              className="ui-btn ui-btn-sm"
              disabled={!adding || adding in rows}
              onClick={() => {
                setRows((current) => ({ ...current, [adding]: { input: "", output: "" } }));
                setAdding("");
              }}
            >
              <Plus size={14} /> Add
            </button>
          </span>
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy} onClick={() => void save()}>
            <Save size={14} /> {busy ? "Saving…" : "Save prices"}
          </button>
        </div>
      ) : null}
    </Card>
  );
}

// ===========================================================================
// Test prompt
// ===========================================================================

function TestPromptCard({ detail, extra }: { detail: IntegrationDetail; extra: AiExtra }) {
  const key = detail.integration.key;
  const [prompt, setPrompt] = useState("In two sentences, what should a sales rep do before a renewal call?");
  const [feature, setFeature] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.post<TestResult>(`/ai-models/${key}/test-prompt`, { prompt, feature: feature || null }));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Test prompt" icon={<FlaskConical size={17} />}>
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small ui-muted iai-note">Goes through exactly what a feature uses — model, limits, redaction — without falling back. Not stored; counted in usage as a test.</p>
        <textarea className="ui-textarea" rows={3} maxLength={4000} aria-label="Test prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <div className="iai-save">
          <select className="ui-select sm" aria-label="Use the model of" value={feature} onChange={(e) => setFeature(e.target.value)} style={{ width: 230 }}>
            <option value="">Default model</option>
            {extra.features.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
                {f.effective ? ` (${f.effective})` : ""}
              </option>
            ))}
          </select>
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={busy || !prompt.trim() || !detail.connection} onClick={() => void send()}>
            <Send size={14} /> {busy ? "Waiting for the model…" : "Send"}
          </button>
        </div>
        {error ? <Alert>{error}</Alert> : null}
        {result ? (
          <div className="iai-answer" aria-live="polite">
            <p>{result.text}</p>
            <div className="iai-answer-meta ui-small ui-muted">
              <span>
                {result.provider_label} · {result.model}
                {result.served_by && result.served_by !== result.model ? ` (${result.served_by})` : ""}
              </span>
              <span>
                {result.usage.input_tokens.toLocaleString()} in · {result.usage.output_tokens.toLocaleString()} out
                {result.usage.reasoning_tokens ? ` (${result.usage.reasoning_tokens.toLocaleString()} reasoning)` : ""}
              </span>
              <span>{usd(result.usage.cost)}</span>
              <span>{(result.usage.latency_ms / 1000).toFixed(1)}s</span>
              {result.redacted ? <span>Emails or phone numbers were redacted before sending.</span> : null}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

// ===========================================================================
// Usage (also used on Settings → AI word usage)
// ===========================================================================

export function ModelUsageTables({ usage }: { usage: ModelUsage }) {
  const [view, setView] = useState<"model" | "feature" | "person">("model");
  const rows: (UsageRow & { label: ReactNode; key: string })[] =
    view === "model"
      ? usage.by_model.map((m) => ({ ...m, key: `${m.provider}:${m.model}`, label: <><span className="ui-mono">{m.model}</span> <span className="ui-muted ui-small">{m.provider_label}</span></> }))
      : view === "feature"
        ? usage.by_feature.map((f) => ({ ...f, key: f.feature, label: f.label }))
        : usage.by_user.map((u) => ({ ...u, key: u.user_id ?? "workspace", label: u.name }));
  return (
    <div className="iai-usage">
      <div className="iai-kpis">
        <Kpi label="Requests" value={usage.totals.requests.toLocaleString()} note={usage.totals.errors ? `${usage.totals.errors} failed` : undefined} />
        <Kpi label="Tokens" value={tokens(usage.totals.tokens)} note={`${tokens(usage.totals.input_tokens)} in · ${tokens(usage.totals.output_tokens)} out`} />
        <Kpi label="Estimated cost" value={usd(usage.totals.cost)} note={usage.totals.unpriced_calls ? `${usage.totals.unpriced_calls} calls without a price` : "At the price table"} />
      </div>
      {usage.totals.requests ? (
        <>
          <div className="iai-tabs" role="tablist" aria-label="Group usage by">
            {(["model", "feature", "person"] as const).map((v) => (
              <button key={v} type="button" role="tab" aria-selected={view === v} className={`iai-tab${view === v ? " on" : ""}`} onClick={() => setView(v)}>
                By {v}
              </button>
            ))}
          </div>
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>{view === "model" ? "Model" : view === "feature" ? "Feature" : "Person"}</th>
                  <th className="ui-num">Requests</th>
                  <th className="ui-num">Tokens in</th>
                  <th className="ui-num">Tokens out</th>
                  <th className="ui-num">Est. cost</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    <td className="ui-num">
                      {row.requests.toLocaleString()}
                      {row.errors ? <span className="ui-muted ui-small"> ({row.errors} failed)</span> : null}
                    </td>
                    <td className="ui-num">{row.input_tokens.toLocaleString()}</td>
                    <td className="ui-num">{row.output_tokens.toLocaleString()}</td>
                    <td className="ui-num">{row.priced === false && !row.cost ? <span className="ui-muted">No price</span> : usd(row.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <EmptyState icon={<Coins size={20} />} title="No requests yet this month" message="Drafts, summaries, answers, agent suggestions and call notes show up here." />
      )}
      {usage.only_mine ? <p className="ui-small ui-muted" style={{ margin: 0 }}>Showing your own requests. Admins see everyone&apos;s.</p> : null}
    </div>
  );
}

function UsageCard({ detail, extra }: { detail: IntegrationDetail; extra: AiExtra }) {
  return (
    <Card
      title="Usage this month"
      icon={<Coins size={17} />}
      actions={
        <Link className="ui-btn ui-btn-sm" href="/settings/ai-usage">
          AI usage
        </Link>
      }
    >
      <ModelUsageTables usage={extra.usage} />
      <p className="ui-small ui-muted iai-note" style={{ marginTop: 10 }}>
        Both providers together, counted when {detail.integration.name} answers (or fails). AI word allowances still apply to everything AIVA writes.
      </p>
    </Card>
  );
}

// ===========================================================================
// The panel
// ===========================================================================

function AiModelsPanel({ detail, setDetail }: PanelProps) {
  const extra = detail.extra as unknown as AiExtra;
  if (!detail.switched_on) return null;
  if (extra.error) return <Alert tone="warning">{extra.error}</Alert>;
  if (!extra.settings) return null;
  return (
    <div className="ui-stack iai-panel">
      <StatusCard detail={detail} extra={extra} />
      <ModelsCard detail={detail} setDetail={setDetail} extra={extra} />
      {detail.connection && detail.can_manage ? <TestPromptCard detail={detail} extra={extra} /> : null}
      <LimitsCard detail={detail} setDetail={setDetail} extra={extra} />
      <DataCard detail={detail} setDetail={setDetail} extra={extra} />
      <BehaviourCard detail={detail} setDetail={setDetail} extra={extra} />
      <PricesCard detail={detail} setDetail={setDetail} extra={extra} />
      <UsageCard detail={detail} extra={extra} />
      {!detail.can_manage ? <p className="ui-small ui-muted">Only a workspace admin can change these settings.</p> : null}
      <p className="ui-small ui-muted iai-note">
        Without a provider, AI features use {extra.default_source === "claude" ? `${AI_SOURCE_LABELS.claude}, AIVA's default model` : "built-in rules"}.
      </p>
    </div>
  );
}

/** Provider panels for this group, by integration key. */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  openai: AiModelsPanel,
  azure_openai: AiModelsPanel,
};
