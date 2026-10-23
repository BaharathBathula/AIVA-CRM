"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CreditCard, ExternalLink, Receipt, Users } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, LoadingRows, PageHeader } from "@/components/ui";
import { date, moneyExact } from "@/lib/format";
import { PLAN_NAMES, type PaidPlan } from "@/lib/plans";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Invoice {
  id: string;
  number: string;
  plan_name: string;
  seats: number;
  amount: number;
  status: string;
  description: string;
  issued_at: string;
  provider: string;
}

interface CatalogPlan {
  key: string;
  name: string;
  price: number;
  tagline: string;
}

interface Billing {
  plan: string;
  plan_name: string;
  status: string;
  read_only: boolean;
  trial_ends_at: string | null;
  days_left: number | null;
  provider: string;
  stripe_enabled: boolean;
  billing_email: string | null;
  payment_method: string | null;
  seats_billed: number;
  seats_active: number;
  price_per_seat: number;
  monthly_total: number;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  can_manage: boolean;
  invoices: Invoice[];
  catalog: {
    plans: CatalogPlan[];
    feature_groups: { plan: PaidPlan; features: { key: string; label: string }[] }[];
  };
}

const ORDER: PaidPlan[] = ["starter", "pro", "enterprise"];
const HIGHLIGHTS: Record<PaidPlan, string[]> = {
  starter: ["Contacts, deals and pipelines", "Mass email and web forms", "Built-in calling and calendar sync", "Custom modules and APIs"],
  pro: ["AI agents and predictions", "Quotes (CPQ) and inventory", "Customer and partner portals", "Journeys and stage playbooks"],
  enterprise: ["Approvals and custom buttons", "Teams, hierarchy and visibility", "Sandbox and field encryption", "Ask AIVA and QuickBooks"],
};

const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  trialing: "info",
  active: "success",
  past_due: "warning",
  canceled: "danger",
  expired: "danger",
};

function BillingPage() {
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const { refresh } = useSession();
  const billing = useApi<Billing>("/billing");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmPlan, setConfirmPlan] = useState<PaidPlan | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const suggested = params.get("plan") as PaidPlan | null;

  useEffect(() => {
    const checkout = params.get("checkout");
    if (checkout === "success") toast("Thanks! Your subscription is active. It can take a moment to show here.");
    if (checkout === "canceled") toast("Checkout was canceled. Nothing was charged.", "danger");
    if (checkout) {
      void refresh();
      router.replace("/settings/billing");
    }
  }, [params, refresh, router, toast]);

  const data = billing.data;

  async function changePlan(plan: PaidPlan) {
    setBusy(plan);
    try {
      const result = await api.post<{ redirect_url: string | null; billing?: Billing }>("/billing/change-plan", { plan });
      if (result.redirect_url) {
        window.location.assign(result.redirect_url);
        return;
      }
      if (result.billing) billing.setData(result.billing);
      await refresh();
      toast(`You're on ${PLAN_NAMES[plan]}.`);
      setConfirmPlan(null);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function post(path: string, message: string) {
    setBusy(path);
    try {
      const result = await api.post<Billing | { redirect_url: string }>(path);
      if ("redirect_url" in result) {
        window.location.assign(result.redirect_url);
        return;
      }
      billing.setData(result);
      await refresh();
      toast(message);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
      setConfirmCancel(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Billing and plan"
        description="Your plan, users, invoices and payment details. Plans are billed per active user per month."
        actions={
          <Link href="/pricing" className="ui-btn">
            Compare plans <ExternalLink size={14} />
          </Link>
        }
      />
      {billing.error ? <Alert>{billing.error}</Alert> : null}
      {!data ? (
        <Card>
          <LoadingRows rows={8} />
        </Card>
      ) : (
        <div className="ui-stack" style={{ gap: 16 }}>
          {data.provider === "demo" && data.plan !== "trial" ? (
            <Alert tone="info">
              Demo billing: plan changes apply straight away and nothing is charged. Add Stripe keys on the server to take real card payments.
            </Alert>
          ) : null}
          <div className="billing-hero">
            <section className="ui-card billing-plan">
              <div className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
                <span className={`plan-chip plan-${data.plan}`}>{data.plan_name}</span>
                <Badge tone={STATUS_TONE[data.status] ?? "neutral"}>{data.status.replace("_", " ")}</Badge>
                {data.cancel_at_period_end ? <Badge tone="warning">Cancels {date(data.current_period_end)}</Badge> : null}
              </div>
              <h2>
                {data.plan === "trial"
                  ? data.read_only
                    ? "Your free trial has ended"
                    : `${data.days_left} day${data.days_left === 1 ? "" : "s"} left in your free trial`
                  : `${data.plan_name} · ${moneyExact(data.monthly_total)} / month`}
              </h2>
              <p className="ui-muted" style={{ margin: 0 }}>
                {data.plan === "trial"
                  ? `Every Enterprise feature is on until ${date(data.trial_ends_at)}. Choose a plan below to keep going.`
                  : `${moneyExact(data.price_per_seat)} per user × ${data.seats_active} active user${data.seats_active === 1 ? "" : "s"}. ${
                      data.current_period_end ? `Next renewal ${date(data.current_period_end)}.` : ""
                    }`}
              </p>
              <div className="stat-strip">
                <div className="billing-stat">
                  <span>Active users</span>
                  <strong>{data.seats_active}</strong>
                </div>
                <div className="billing-stat">
                  <span>Users billed</span>
                  <strong>{data.seats_billed}</strong>
                </div>
                <div className="billing-stat">
                  <span>Payment</span>
                  <strong style={{ fontSize: 14 }}>{data.payment_method ?? (data.provider === "stripe" ? "Card on file" : data.plan === "trial" ? "Not needed yet" : "Demo — no charge")}</strong>
                </div>
              </div>
              {data.can_manage && data.plan !== "trial" ? (
                <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                  {data.seats_billed !== data.seats_active ? (
                    <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy)} onClick={() => void post("/billing/sync-seats", "Users updated.")}>
                      <Users size={14} /> Bill for {data.seats_active} users
                    </button>
                  ) : null}
                  {data.provider === "stripe" ? (
                    <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy)} onClick={() => void post("/billing/portal", "")}>
                      <CreditCard size={14} /> Payment details
                    </button>
                  ) : null}
                  {data.cancel_at_period_end ? (
                    <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy)} onClick={() => void post("/billing/resume", "Your plan will renew.")}>
                      Keep my plan
                    </button>
                  ) : (
                    <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" disabled={Boolean(busy)} onClick={() => setConfirmCancel(true)}>
                      Cancel plan
                    </button>
                  )}
                </div>
              ) : null}
            </section>
            <Card title="Invoices" icon={<Receipt size={17} />} flush>
              {data.invoices.length ? (
                <div className="ui-list">
                  {data.invoices.slice(0, 6).map((invoice) => (
                    <Link key={invoice.id} href={`/settings/billing/invoices/${invoice.id}`} className="ui-list-item">
                      <span className="main">
                        <span className="title">{invoice.number}</span>
                        <span className="sub">
                          {date(invoice.issued_at)} · {invoice.plan_name} · {invoice.seats} user{invoice.seats === 1 ? "" : "s"}
                        </span>
                      </span>
                      <span className="ui-num ui-strong">{moneyExact(invoice.amount)}</span>
                    </Link>
                  ))}
                </div>
              ) : (
                <p className="empty-hint">Invoices appear here once you're on a paid plan.</p>
              )}
            </Card>
          </div>

          <Card title={data.plan === "trial" ? "Choose your plan" : "Change plan"}>
            <div className="billing-plans">
              {ORDER.map((key) => {
                const plan = data.catalog.plans.find((p) => p.key === key)!;
                const current = data.plan === key;
                const recommended = !current && (suggested ? suggested === key : key === "pro");
                return (
                  <div key={key} className={`billing-option${current ? " current" : ""}${recommended ? " recommended" : ""}`}>
                    <div className="ui-row">
                      <h3>{plan.name}</h3>
                      <span className="ui-spacer" />
                      {current ? <Badge tone="success">Current</Badge> : recommended ? <Badge tone="primary">Recommended</Badge> : null}
                    </div>
                    <div className="price">
                      ${plan.price} <small>per user / month</small>
                    </div>
                    <span className="ui-muted ui-small">{plan.tagline}</span>
                    <ul>
                      {HIGHLIGHTS[key].map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                    <div className="ui-muted ui-small">
                      {moneyExact(plan.price * data.seats_active)} / month for {data.seats_active} user{data.seats_active === 1 ? "" : "s"}
                    </div>
                    {data.can_manage ? (
                      <button
                        type="button"
                        className={`ui-btn${recommended ? " ui-btn-primary" : ""}`}
                        disabled={current || Boolean(busy)}
                        onClick={() => setConfirmPlan(key)}
                      >
                        {current ? "Your plan" : busy === key ? "Switching…" : data.plan === "trial" ? `Choose ${plan.name}` : `Switch to ${plan.name}`}
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
            {!data.can_manage ? <p className="ui-muted ui-small" style={{ marginBottom: 0 }}>Only workspace owners and administrators can change the plan.</p> : null}
          </Card>
        </div>
      )}

      <ConfirmDialog
        open={Boolean(confirmPlan)}
        title={confirmPlan ? `Switch to ${PLAN_NAMES[confirmPlan]}?` : ""}
        message={
          confirmPlan && data ? (
            data.stripe_enabled ? (
              <>You&apos;ll go to Stripe to enter payment details. {PLAN_NAMES[confirmPlan]} is ${data.catalog.plans.find((p) => p.key === confirmPlan)?.price} per user per month.</>
            ) : (
              <>
                {PLAN_NAMES[confirmPlan]} costs {moneyExact((data.catalog.plans.find((p) => p.key === confirmPlan)?.price ?? 0) * data.seats_active)} per month for your{" "}
                {data.seats_active} active user{data.seats_active === 1 ? "" : "s"}. The change applies right away.
              </>
            )
          ) : null
        }
        confirmLabel={data?.stripe_enabled ? "Continue to payment" : "Switch plan"}
        danger={false}
        busy={Boolean(busy)}
        onConfirm={() => confirmPlan && void changePlan(confirmPlan)}
        onClose={() => setConfirmPlan(null)}
      />
      <ConfirmDialog
        open={confirmCancel}
        title="Cancel your plan?"
        message="Your plan stays active until the end of this billing period. After that the workspace becomes read-only; nothing is deleted and you can choose a plan again any time."
        confirmLabel="Cancel plan"
        busy={Boolean(busy)}
        onConfirm={() => void post("/billing/cancel", "Your plan will end at the close of this period.")}
        onClose={() => setConfirmCancel(false)}
      />
    </>
  );
}

export default function BillingSettings() {
  return (
    <SettingsShell>
      <Suspense fallback={<LoadingRows rows={6} />}>
        <BillingPage />
      </Suspense>
    </SettingsShell>
  );
}
