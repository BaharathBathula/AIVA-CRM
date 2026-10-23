"use client";

import Image from "next/image";
import Link from "next/link";
import { Check, Minus } from "lucide-react";
import { Fragment } from "react";

import { LoadingRows } from "@/components/ui";
import { useApi } from "@/lib/use-api";

interface PlanInfo {
  key: "trial" | "starter" | "pro" | "enterprise";
  name: string;
  price: number;
  tagline: string;
  features: string[];
  limits: Record<string, number | null>;
  highlight: boolean;
}

interface Catalog {
  trial_days: number;
  plans: PlanInfo[];
  feature_groups: { plan: "starter" | "pro" | "enterprise"; features: { key: string; label: string; description: string }[] }[];
  limit_labels: Record<string, string>;
}

const GROUP_TITLE = { starter: "Starter features", pro: "Pro features", enterprise: "Enterprise features" } as const;
const SHOWN = 11;

const FAQ = [
  {
    q: "How does the free trial work?",
    a: "Every new workspace gets 7 days with every Enterprise feature switched on. No credit card is needed. Pick a plan any time during the trial; if you don't, the workspace stays readable but you can't change records until you choose one.",
  },
  {
    q: "Is pricing per user?",
    a: "Yes. Every plan is billed per active user per month. Add or remove people whenever you like; the next invoice reflects your active users.",
  },
  {
    q: "Can I change plans later?",
    a: "Yes. Upgrade or downgrade from Settings → Billing. Upgrades take effect immediately, and features from your old plan stay readable if you move down.",
  },
  {
    q: "What happens to my data if I cancel?",
    a: "Nothing is deleted. Your workspace becomes read-only at the end of the paid period, and you can export everything as CSV at any time.",
  },
  {
    q: "Do you offer annual billing or nonprofit discounts?",
    a: "Talk to us. We offer discounts for annual commitments, nonprofits and education.",
  },
];

function priceLabel(plan: PlanInfo) {
  return plan.key === "trial" ? "0" : String(plan.price);
}

export default function PricingPage() {
  const catalog = useApi<Catalog>("/public/plans");
  const data = catalog.data;
  const paid = data?.plans.filter((plan) => plan.key !== "trial") ?? [];

  return (
    <div className="pricing-page">
      <nav className="pricing-nav" aria-label="AIVA">
        <Link href="/" className="brand">
          <Image src="/aiva-mark.png" alt="" width={30} height={30} priority />
          AIVA
        </Link>
        <span className="ui-spacer" />
        <Link href="/login" className="ui-btn">
          Sign in
        </Link>
        <Link href="/signup" className="ui-btn ui-btn-primary">
          Start free trial
        </Link>
      </nav>

      <header className="pricing-hero">
        <h1>One CRM for sales, service and marketing</h1>
        <p>
          Start with a free {data?.trial_days ?? 7}-day trial of everything. Then choose the plan that fits your team. Every plan is priced per
          user, per month.
        </p>
      </header>

      {!data ? (
        <div className="ui-card" style={{ maxWidth: 1240, margin: "0 auto" }}>
          {catalog.error ? <p className="empty-hint">{catalog.error}</p> : <LoadingRows rows={8} />}
        </div>
      ) : (
        <>
          <section className="pricing-grid" aria-label="Plans">
            {data.plans.map((plan) => {
              const group = data.feature_groups.find((g) => g.plan === plan.key);
              const previous = plan.key === "pro" ? "Starter" : plan.key === "enterprise" ? "Pro" : null;
              const items =
                plan.key === "trial"
                  ? [
                      `Every Enterprise feature for ${data.trial_days} days`,
                      "No credit card needed",
                      "Invite your whole team",
                      "Demo-ready pipeline, SLAs and dashboards",
                      "Keep all your data when you pick a plan",
                    ]
                  : (group?.features ?? []).slice(0, SHOWN).map((f) => f.label);
              const more = plan.key !== "trial" ? Math.max(0, (group?.features.length ?? 0) - SHOWN) : 0;
              return (
                <article key={plan.key} className={`price-card${plan.highlight ? " highlight" : ""}`}>
                  {plan.highlight ? <span className="tag">Most popular</span> : null}
                  <h2>{plan.name}</h2>
                  <p className="tagline">{plan.tagline}</p>
                  <div className="price-amount">
                    <strong>${priceLabel(plan)}</strong>
                    <span>{plan.key === "trial" ? `for ${data.trial_days} days` : "per user / month"}</span>
                  </div>
                  <Link href={plan.key === "trial" ? "/signup" : `/signup?plan=${plan.key}`} className={`ui-btn${plan.highlight ? " ui-btn-primary" : ""}`}>
                    {plan.key === "trial" ? "Start free trial" : `Try ${plan.name} free`}
                  </Link>
                  <p className="includes">{plan.key === "trial" ? "Includes" : previous ? `Everything in ${previous}, plus` : "Includes"}</p>
                  <ul>
                    {items.map((item) => (
                      <li key={item}>
                        <Check size={15} strokeWidth={2.6} /> {item}
                      </li>
                    ))}
                    {more ? (
                      <li>
                        <a href="#compare" style={{ color: "var(--text)", fontWeight: 700 }}>
                          + {more} more in the comparison
                        </a>
                      </li>
                    ) : null}
                  </ul>
                </article>
              );
            })}
          </section>

          <section className="pricing-compare" id="compare">
            <h2>Compare plans</h2>
            <div className="ui-card" style={{ padding: 0 }}>
              <div className="ui-table-wrap">
                <table className="ui-table compare-table">
                  <thead>
                    <tr>
                      <th>Feature</th>
                      {data.plans.map((plan) => (
                        <th key={plan.key}>
                          {plan.name}
                          <div className="ui-muted ui-small" style={{ fontWeight: 600 }}>
                            {plan.key === "trial" ? "free" : `$${plan.price}/user/mo`}
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.feature_groups.map((group) => (
                      <Fragment key={group.plan}>
                        <tr className="group-row">
                          <td colSpan={data.plans.length + 1}>{GROUP_TITLE[group.plan]}</td>
                        </tr>
                        {group.features.map((feature) => (
                          <tr key={feature.key}>
                            <td>
                              {feature.label}
                              <div className="ui-muted ui-small" style={{ fontWeight: 500 }}>
                                {feature.description}
                              </div>
                            </td>
                            {data.plans.map((plan) => (
                              <td key={plan.key}>
                                {plan.features.includes(feature.key) ? (
                                  <Check className="compare-yes" size={18} strokeWidth={2.6} aria-label="Included" />
                                ) : (
                                  <Minus className="compare-no" size={18} aria-label="Not included" />
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </Fragment>
                    ))}
                    <tr className="group-row">
                      <td colSpan={data.plans.length + 1}>Limits</td>
                    </tr>
                    {Object.entries(data.limit_labels).map(([key, label]) => (
                      <tr key={key}>
                        <td style={{ textTransform: "capitalize" }}>{label}</td>
                        {data.plans.map((plan) => (
                          <td key={plan.key}>{plan.limits[key] ? plan.limits[key] : "Unlimited"}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <p className="ui-muted ui-small" style={{ textAlign: "center", marginTop: 14 }}>
              Prices in US dollars. Taxes may apply. {paid.length ? `Starter $${paid[0].price}, Pro $${paid[1]?.price}, Enterprise $${paid[2]?.price} per user per month.` : null}
            </p>
          </section>

          <section className="pricing-faq">
            <h2>Questions</h2>
            {FAQ.map((item) => (
              <details key={item.q}>
                <summary>{item.q}</summary>
                <p>{item.a}</p>
              </details>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
