"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Bot, Check, Coins, Mic, PackagePlus, Sparkles, Waypoints, type LucideIcon } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, LoadingRows, PageHeader } from "@/components/ui";
import { date, moneyExact } from "@/lib/format";
import { FEATURE_PLAN, PLAN_NAMES, type PlanKey } from "@/lib/plans";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Addon = {
  key: string;
  name: string;
  description: string;
  price: number;
  per_seat: boolean;
  credits: number;
  ai_words: number;
  features: string[];
  max_quantity: number;
  active: boolean;
  status: string | null;
  quantity: number;
  monthly_cost: number;
  renews_at: string | null;
  cancelled_at: string | null;
  started_at: string | null;
  included_in_plan: boolean;
};

type Allowances = {
  plan: string;
  seats: number;
  credits_per_seat: number;
  credits_plan: number;
  credits_addons: number;
  credits_total: number;
  ai_words_per_seat: number;
  ai_words_plan: number;
  ai_words_addons: number;
  ai_words_total: number;
};

type AddonList = { items: Addon[]; seats: number; plan: PlanKey; allowances: Allowances };

const ICONS: Record<string, LucideIcon> = {
  credit_pack: Coins,
  ai_words: Sparkles,
  conversation_intelligence: Mic,
  advanced_data: Waypoints,
};

const LINKS: Record<string, { href: string; label: string }> = {
  credit_pack: { href: "/settings/credits", label: "See credit usage" },
  ai_words: { href: "/settings/ai-usage", label: "See AI word usage" },
  conversation_intelligence: { href: "/conversations", label: "Open conversations" },
  advanced_data: { href: "/settings/enrichment", label: "Set up enrichment" },
};

export default function AddonsPage() {
  return (
    <SettingsShell>
      <Suspense fallback={null}>
        <Addons />
      </Suspense>
    </SettingsShell>
  );
}

function Addons() {
  const toast = useToast();
  const params = useSearchParams();
  const { can, refresh } = useSession();
  const manage = can("admin.settings");
  const addons = useApi<AddonList>("/billing/addons");
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<Addon | null>(null);

  useEffect(() => {
    const result = params.get("checkout");
    if (result === "success") toast("Add-on purchased. It can take a moment to show up.");
    if (result === "canceled") toast("Checkout canceled. Nothing was charged.", "danger");
  }, [params, toast]);

  async function buy(addon: Addon) {
    setBusy(addon.key);
    try {
      const result = await api.post<{ redirect_url: string | null }>(`/billing/addons/${addon.key}`, { quantity: quantities[addon.key] ?? Math.max(1, addon.quantity || 1) });
      if (result.redirect_url) {
        window.location.href = result.redirect_url;
        return;
      }
      toast(addon.status === "active" ? `${addon.name} updated.` : `${addon.name} added.`);
      await Promise.all([addons.reload(), refresh()]);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    if (!cancelling) return;
    setBusy(cancelling.key);
    try {
      await api.del(`/billing/addons/${cancelling.key}`);
      toast(`${cancelling.name} canceled.`);
      setCancelling(null);
      await Promise.all([addons.reload(), refresh()]);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const data = addons.data;
  const monthly = (data?.items ?? []).reduce((sum, item) => sum + (item.active ? item.monthly_cost : 0), 0);

  return (
    <>
      <PageHeader
        title="Product add-ons"
        description="Top up credits and AI words, or add features without changing your whole plan. Add-ons renew monthly and you can cancel any time."
      />
      {addons.error ? <Alert>{addons.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <div className="ui-stack">
          <div className="settings-kpis three">
            <Card>
              <div className="ui-kpi-label">Credits each month</div>
              <div className="ui-kpi-value">{data.allowances.credits_total.toLocaleString()}</div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                {data.allowances.credits_plan.toLocaleString()} from your plan ({data.allowances.credits_per_seat.toLocaleString()} per user) +{" "}
                {data.allowances.credits_addons.toLocaleString()} from add-ons
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">AI words each month</div>
              <div className="ui-kpi-value">{data.allowances.ai_words_total.toLocaleString()}</div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                {data.allowances.ai_words_plan.toLocaleString()} from your plan + {data.allowances.ai_words_addons.toLocaleString()} from add-ons
              </p>
            </Card>
            <Card>
              <div className="ui-kpi-label">Add-ons per month</div>
              <div className="ui-kpi-value">{moneyExact(monthly)}</div>
              <p className="ui-muted ui-small" style={{ margin: "6px 0 0" }}>
                On top of your {PLAN_NAMES[data.plan] ?? data.plan} plan · {data.seats} active user{data.seats === 1 ? "" : "s"}
              </p>
            </Card>
          </div>

          {data.plan === "trial" ? (
            <Alert tone="info">
              Your trial already includes every feature. Credit and AI word packs you add now apply right away; feature add-ons matter once you pick a plan.
            </Alert>
          ) : null}

          <div className="addon-grid">
            {data.items.map((addon) => {
              const Icon = ICONS[addon.key] ?? PackagePlus;
              const quantity = quantities[addon.key] ?? Math.max(1, addon.quantity || 1);
              const units = addon.per_seat ? data.seats : quantity;
              const link = LINKS[addon.key];
              const on = addon.status === "active";
              const ending = addon.status === "cancelled" && addon.active;
              return (
                <section key={addon.key} className={`ui-card addon-card${addon.active ? " active" : ""}`}>
                  <div className="addon-head">
                    <span className="addon-icon">
                      <Icon size={20} />
                    </span>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <h2>{addon.name}</h2>
                      <div className="addon-price">
                        <strong>{moneyExact(addon.price)}</strong>
                        <span className="ui-muted ui-small">{addon.per_seat ? " per user / month" : " per pack / month"}</span>
                      </div>
                    </div>
                    {addon.included_in_plan ? (
                      <Badge tone="success">Included in your plan</Badge>
                    ) : on ? (
                      <Badge tone="success" dot>
                        Active
                      </Badge>
                    ) : ending ? (
                      <Badge tone="warning">Ends {date(addon.renews_at)}</Badge>
                    ) : null}
                  </div>
                  <p className="addon-desc">{addon.description}</p>
                  {addon.features.length ? (
                    <ul className="addon-features">
                      {addon.features.map((feature) => (
                        <li key={feature}>
                          <Check size={14} /> {FEATURE_PLAN[feature]?.label ?? feature}
                          {FEATURE_PLAN[feature] ? <span className="ui-muted ui-small"> · normally {PLAN_NAMES[FEATURE_PLAN[feature].plan]}</span> : null}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <div className="addon-foot">
                    {addon.included_in_plan ? (
                      link ? (
                        <Link href={link.href} className="ui-btn ui-btn-sm">
                          {link.label}
                        </Link>
                      ) : null
                    ) : manage ? (
                      <>
                        {!addon.per_seat ? (
                          <label className="ui-row" style={{ gap: 8 }}>
                            <span className="ui-small ui-muted">Packs</span>
                            <select
                              className="ui-select sm"
                              style={{ width: 84 }}
                              value={quantity}
                              aria-label={`${addon.name} packs`}
                              onChange={(event) => setQuantities((current) => ({ ...current, [addon.key]: Number(event.target.value) }))}
                            >
                              {Array.from({ length: addon.max_quantity }, (_, i) => i + 1).map((n) => (
                                <option key={n} value={n}>
                                  {n}
                                </option>
                              ))}
                            </select>
                          </label>
                        ) : (
                          <span className="ui-small ui-muted">
                            {data.seats} user{data.seats === 1 ? "" : "s"} · {moneyExact(addon.price * units)} / month
                          </span>
                        )}
                        <span className="ui-spacer" />
                        {on ? (
                          <>
                            {!addon.per_seat && quantity !== addon.quantity ? (
                              <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" disabled={busy === addon.key} onClick={() => void buy(addon)}>
                                Change to {quantity}
                              </button>
                            ) : link ? (
                              <Link href={link.href} className="ui-btn ui-btn-sm">
                                {link.label}
                              </Link>
                            ) : null}
                            <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={busy === addon.key} onClick={() => setCancelling(addon)}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" disabled={busy === addon.key} onClick={() => void buy(addon)}>
                            {busy === addon.key ? <span className="ui-spinner" /> : <PackagePlus size={14} />}{" "}
                            {ending ? "Keep it" : `Add for ${moneyExact(addon.price * units)}/mo`}
                          </button>
                        )}
                      </>
                    ) : (
                      <span className="ui-muted ui-small">{on ? `Active · renews ${date(addon.renews_at)}` : "Ask a workspace admin to add this."}</span>
                    )}
                  </div>
                  {on && addon.renews_at ? (
                    <div className="ui-muted ui-small">
                      {addon.per_seat ? "" : `${addon.quantity} pack${addon.quantity === 1 ? "" : "s"} · `}
                      {moneyExact(addon.monthly_cost)} a month · renews {date(addon.renews_at)}
                    </div>
                  ) : null}
                </section>
              );
            })}
          </div>

          <Card title="How credits and AI words work" icon={<Bot size={17} />}>
            <ul className="ui-small" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7, color: "var(--text-2)" }}>
              <li>Each paid user adds credits and AI words to a shared monthly pool that resets on the 1st.</li>
              <li>Credits pay for waterfall enrichment lookups and data requests. Built-in enrichment is free.</li>
              <li>AI words count the text AIVA writes for you: email drafts, record summaries, call notes and agent suggestions.</li>
              <li>Admins can cap what one person can use in License settings.</li>
            </ul>
          </Card>
        </div>
      )}
      <ConfirmDialog
        open={Boolean(cancelling)}
        title={`Cancel ${cancelling?.name ?? "add-on"}?`}
        message={
          cancelling?.features.length
            ? "The features it adds stop working at the end of the billing month. Your data stays."
            : "Your extra allowance stops at the end of the billing month."
        }
        confirmLabel="Cancel add-on"
        busy={busy === cancelling?.key}
        onConfirm={() => void cancel()}
        onClose={() => setCancelling(null)}
      />
    </>
  );
}
