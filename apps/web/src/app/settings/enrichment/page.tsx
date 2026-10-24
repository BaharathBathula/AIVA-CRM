"use client";

import Link from "next/link";
import { ArrowDown, ArrowUp, CheckCircle2, Coins, PlugZap, XCircle } from "lucide-react";
import { useState } from "react";

import { PlanChip } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { SecretInput, SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Badge, Card, PageHeader } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

type ProviderKey = "builtin" | "website" | "hunter" | "pdl" | "apollo" | "zoominfo";

type Enrichment = {
  providers: { key: ProviderKey; enabled: boolean }[];
  overwrite: boolean;
  auto_enrich_new: boolean;
  fields: string[];
};

type ProviderInfo = {
  key: ProviderKey;
  label: string;
  cost: number;
  needs_key: string | null;
  /** Set for providers connected in Integrations (Apollo.io, ZoomInfo) instead of with a key here. */
  integration?: string | null;
  description: string;
  enabled: boolean;
  key_set: boolean;
  plan_ok: boolean;
};

type Catalog = { providers: ProviderInfo[]; fields: { key: string; label: string }[]; can_edit: boolean };

export default function EnrichmentPage() {
  return (
    <SettingsShell feature="data_enrichment">
      <WaterfallEnrichment />
    </SettingsShell>
  );
}

function WaterfallEnrichment() {
  const toast = useToast();
  const { has } = useSession();
  const state = useWorkspaceSettings<Enrichment>("enrichment");
  const catalog = useApi<Catalog>("/enrichment/providers");
  const [testing, setTesting] = useState<ProviderKey | null>(null);
  const [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  const info = Object.fromEntries((catalog.data?.providers ?? []).map((p) => [p.key, p])) as Record<string, ProviderInfo>;
  const paidAllowed = has("waterfall_enrichment");

  async function test(key: ProviderKey) {
    setTesting(key);
    try {
      const result = await api.post<{ ok: boolean; message: string }>(`/enrichment/providers/${key}/test`);
      setResults((current) => ({ ...current, [key]: result }));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setTesting(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Waterfall enrichment"
        description="When a lead, contact or account is missing details, AIVA asks each provider in turn — top to bottom — and stops once the gaps are filled. Free sources go first so you only spend credits when you need to."
        actions={
          <Link className="ui-btn" href="/settings/credits">
            <Coins size={15} /> Credit usage
          </Link>
        }
      />
      <SettingsBody state={state}>
        {(draft) => {
          const off = !state.canEdit;
          const providers = draft.providers;
          return (
            <div className="ui-stack">
              <Card title="Providers, in order">
                {!paidAllowed ? (
                  <div className="upgrade-inline" style={{ marginBottom: 12 }}>
                    <span>
                      Paid providers (Hunter.io, People Data Labs, Apollo.io, ZoomInfo) come with Pro or the Advanced data add-on. <PlanChip feature="waterfall_enrichment" />{" "}
                      <Link href="/settings/billing/add-ons">See add-ons</Link>
                    </span>
                  </div>
                ) : null}
                <div className="settings-list">
                  {providers.map((provider, index) => {
                    const meta = info[provider.key];
                    const paid = Boolean(meta?.cost);
                    const locked = paid && !paidAllowed;
                    const needsKey = meta?.needs_key ?? null;
                    const integration = meta?.integration ?? null;
                    const result = results[provider.key];
                    return (
                      <div key={provider.key} className="settings-list-row dm-provider">
                        <div className="settings-list-handle">
                          <button
                            type="button"
                            aria-label={`Move ${meta?.label ?? provider.key} up`}
                            disabled={off || index === 0}
                            onClick={() => state.set("providers", reorder(providers, index, -1))}
                          >
                            <ArrowUp size={13} />
                          </button>
                          <button
                            type="button"
                            aria-label={`Move ${meta?.label ?? provider.key} down`}
                            disabled={off || index === providers.length - 1}
                            onClick={() => state.set("providers", reorder(providers, index, 1))}
                          >
                            <ArrowDown size={13} />
                          </button>
                        </div>
                        <div className="dm-provider-body">
                          <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                            <span className="dm-step">{index + 1}</span>
                            <strong>{meta?.label ?? provider.key}</strong>
                            {paid ? <Badge tone="warning">{meta?.cost} credit per record</Badge> : <Badge tone="success">Free</Badge>}
                            {locked ? <PlanChip feature="waterfall_enrichment" /> : null}
                          </div>
                          <p className="ui-muted ui-small" style={{ margin: "4px 0 0" }}>
                            {meta?.description}
                          </p>
                          {integration ? (
                            <div className="ui-row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                              {meta?.key_set ? <Badge tone="success" dot>Connected</Badge> : <Badge tone="warning" dot>Not connected</Badge>}
                              <Link className="ui-small" href={`/integrations/${integration}`}>
                                {meta?.key_set ? `Manage in Integrations` : `Connect ${meta?.label ?? integration} in Integrations`}
                              </Link>
                            </div>
                          ) : null}
                          {needsKey ? (
                            <div className="ui-row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                              <span className="ui-small" style={{ fontWeight: 650 }}>
                                API key
                              </span>
                              <SecretInput
                                name={needsKey}
                                label={`${meta?.label} API key`}
                                isSet={Boolean(state.secretsSet[needsKey])}
                                pending={state.pendingSecrets[needsKey]}
                                onChange={state.setSecret}
                                disabled={off}
                              />
                            </div>
                          ) : null}
                          {result ? (
                            <p className={`dm-test ${result.ok ? "ok" : "bad"}`}>
                              {result.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />} {result.message}
                            </p>
                          ) : null}
                        </div>
                        <div className="ui-row" style={{ gap: 8 }}>
                          {!off ? (
                            <button type="button" className="ui-btn ui-btn-sm" onClick={() => void test(provider.key)} disabled={testing === provider.key}>
                              {testing === provider.key ? <span className="ui-spinner" /> : <PlugZap size={14} />} Test
                            </button>
                          ) : null}
                          <Toggle
                            label={`Use ${meta?.label ?? provider.key}`}
                            checked={provider.enabled && !locked}
                            disabled={off || locked}
                            onChange={(value) => state.set("providers", providers.map((p, i) => (i === index ? { ...p, enabled: value } : p)))}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
                <p className="ui-muted ui-small" style={{ marginBottom: 0 }}>
                  Keys are stored encrypted and never shown again. Save after pasting a key, then use Test to check it. Apollo.io and ZoomInfo connect in Integrations and use your own Apollo or ZoomInfo credits as well.
                </p>
              </Card>

              <Card title="What to fill in">
                <div className="settings-rows">
                  <SettingRow label="Fields" hint="AIVA only asks providers for the fields you pick here." stacked>
                    <div className="dm-checks">
                      {(catalog.data?.fields ?? []).map((field) => (
                        <label key={field.key} className="ui-check">
                          <input
                            type="checkbox"
                            disabled={off}
                            checked={draft.fields.includes(field.key)}
                            onChange={(event) =>
                              state.set("fields", event.target.checked ? [...draft.fields, field.key] : draft.fields.filter((f) => f !== field.key))
                            }
                          />
                          <span>{field.label}</span>
                        </label>
                      ))}
                    </div>
                  </SettingRow>
                  <SettingRow label="Replace existing values" hint="Off: only empty fields are filled, so nothing your team typed is overwritten.">
                    <Toggle label="Replace existing values" checked={draft.overwrite} disabled={off} onChange={(value) => state.set("overwrite", value)} />
                  </SettingRow>
                  <SettingRow label="Enrich new records automatically" hint="Runs the free AIVA rules on every new lead, contact and account as it's created. Paid providers only run when someone clicks Enrich.">
                    <Toggle label="Enrich new records automatically" checked={draft.auto_enrich_new} disabled={off} onChange={(value) => state.set("auto_enrich_new", value)} />
                  </SettingRow>
                </div>
              </Card>
            </div>
          );
        }}
      </SettingsBody>
    </>
  );
}

function reorder<T>(list: T[], index: number, delta: number) {
  const next = [...list];
  const [item] = next.splice(index, 1);
  next.splice(index + delta, 0, item);
  return next;
}
