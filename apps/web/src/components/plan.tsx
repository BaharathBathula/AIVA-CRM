"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FlaskConical, Lock, Sparkles, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession } from "@/components/providers";
import { FEATURE_PLAN, PLAN_NAMES, PLAN_PRICES, featurePlan } from "@/lib/plans";

/** Small "Pro" / "Enterprise" chip next to features a plan doesn't include. */
export function PlanChip({ feature }: { feature: string }) {
  const { has } = useSession();
  if (has(feature)) return null;
  const plan = featurePlan(feature).plan;
  return (
    <span className={`plan-chip plan-${plan}`} title={`Part of the ${PLAN_NAMES[plan]} plan`}>
      <Lock size={11} /> {PLAN_NAMES[plan]}
    </span>
  );
}

/** Shown in place of a page or card when the plan doesn't include a feature. */
export function UpgradeGate({ feature, children, compact }: { feature: string; children?: ReactNode; compact?: boolean }) {
  const { has, can, session } = useSession();
  if (has(feature)) return <>{children}</>;
  const definition = featurePlan(feature);
  const plan = definition.plan;
  const manage = can("admin.settings");
  const included = Object.entries(FEATURE_PLAN)
    .filter(([, value]) => value.plan === plan)
    .map(([, value]) => value.label)
    .slice(0, 6);
  if (compact) {
    return (
      <div className="upgrade-inline">
        <Lock size={15} />
        <span>
          {definition.label} is part of {PLAN_NAMES[plan]}.{" "}
          {manage ? <Link href={`/settings/billing?plan=${plan}`}>Upgrade</Link> : "Ask an admin to upgrade."}
        </span>
      </div>
    );
  }
  return (
    <section className="ui-card upgrade-card">
      <div className="upgrade-icon">
        <Sparkles size={22} />
      </div>
      <div className="upgrade-body">
        <span className={`plan-chip plan-${plan}`}>
          <Lock size={11} /> {PLAN_NAMES[plan]} plan
        </span>
        <h2>{definition.label}</h2>
        <p>
          You&apos;re on {session?.subscription.plan_name ?? "your current plan"}. {definition.label} comes with{" "}
          {PLAN_NAMES[plan]} at ${PLAN_PRICES[plan]} per user per month, along with:
        </p>
        <ul>
          {included.map((label) => (
            <li key={label}>{label}</li>
          ))}
        </ul>
        <div className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
          {manage ? (
            <Link className="ui-btn ui-btn-primary" href={`/settings/billing?plan=${plan}`}>
              Upgrade to {PLAN_NAMES[plan]}
            </Link>
          ) : (
            <span className="ui-muted ui-small">Ask a workspace admin to upgrade the plan.</span>
          )}
          <Link className="ui-btn" href="/pricing">
            Compare plans
          </Link>
        </div>
      </div>
    </section>
  );
}

/** App shell for a page that belongs to a plan feature. */
export function FeaturePage({
  feature,
  permission,
  what,
  children,
}: {
  feature: string | string[];
  permission?: string | string[];
  what?: string;
  children: ReactNode;
}) {
  const { has } = useSession();
  const features = Array.isArray(feature) ? feature : [feature];
  const missing = features.find((key) => !has(key));
  return (
    <AppShell permission={permission} what={what}>
      {missing ? <UpgradeGate feature={missing} /> : children}
    </AppShell>
  );
}

/** Trial countdown, read-only notice or sandbox banner above every page. */
export function PlanBanner() {
  const { session, can } = useSession();
  const pathname = usePathname();
  if (!session) return null;
  const subscription = session.subscription;
  const onBilling = pathname.startsWith("/settings/billing");

  if (session.is_sandbox) {
    return (
      <div className="plan-banner tone-sandbox" role="status">
        <FlaskConical size={16} />
        <span>
          <strong>Sandbox.</strong> Changes here don&apos;t affect production. Deploy what works from Settings → Sandbox.
        </span>
      </div>
    );
  }
  if (subscription?.read_only) {
    return (
      <div className="plan-banner tone-danger" role="alert">
        <TriangleAlert size={16} />
        <span>
          <strong>{subscription.status === "expired" ? "Your free trial has ended." : "Your plan was canceled."}</strong> Everything is
          still here, but the workspace is read-only until you choose a plan.
        </span>
        {!onBilling && can("admin.settings") ? (
          <Link href="/settings/billing" className="ui-btn ui-btn-sm ui-btn-primary">
            Choose a plan
          </Link>
        ) : null}
      </div>
    );
  }
  if (subscription?.plan === "trial" && subscription.days_left !== null) {
    const left = subscription.days_left;
    return (
      <div className={`plan-banner ${left <= 2 ? "tone-warning" : "tone-info"}`} role="status">
        <Sparkles size={16} />
        <span>
          <strong>
            Free trial: {left} day{left === 1 ? "" : "s"} left.
          </strong>{" "}
          Every Enterprise feature is on. Pick a plan any time; no card needed until then.
        </span>
        {!onBilling && can("admin.settings") ? (
          <Link href="/settings/billing" className="ui-btn ui-btn-sm">
            See plans
          </Link>
        ) : null}
      </div>
    );
  }
  if (subscription?.status === "past_due") {
    return (
      <div className="plan-banner tone-warning" role="alert">
        <TriangleAlert size={16} />
        <span>
          <strong>Payment failed.</strong> Update your payment method to keep your plan.
        </span>
        {!onBilling && can("admin.settings") ? (
          <Link href="/settings/billing" className="ui-btn ui-btn-sm">
            Fix billing
          </Link>
        ) : null}
      </div>
    );
  }
  return null;
}
