"use client";

/**
 * "Billing" on an account: Chargebee subscriptions (plan, status, MRR, next
 * renewal) and every payment and refund taken through Stripe, PayPal, Square or
 * Chargebee for the account.
 *
 *   <RecordBilling accountId={account.id} />
 *
 * Renders nothing without the online-payments plan feature, or when Chargebee is
 * off and the account has no subscriptions or online payments.
 */

import { ArrowUpRight, Repeat, WalletCards } from "lucide-react";

import { useSession } from "@/components/providers";
import { Badge, Card } from "@/components/ui";
import { date } from "@/lib/format";
import { useApi } from "@/lib/use-api";

import { amountText, PaymentsTable, SUBSCRIPTION_TONE, type PaymentRow, type Processor } from "./payments-kit";

type BillingData = {
  chargebee: {
    enabled: boolean;
    customer_id: string | null;
    customer_url: string | null;
    counts: Record<string, number>;
    mrr: { currency: string; amount: string }[];
    next_renewal: string | null;
    items?: PaymentRow[];
  };
  payments: PaymentRow[];
  processors: Processor[];
};

export function RecordBilling({ accountId }: { accountId: string }) {
  const { has } = useSession();
  const enabled = has("online_payments");
  const billing = useApi<BillingData>(enabled ? `/payments/accounts/${accountId}` : null);
  if (!enabled || !billing.data) return null;
  const data = billing.data;
  const subscriptions = data.chargebee.items ?? [];
  if (!subscriptions.length && !data.payments.length && !(data.chargebee.enabled && data.chargebee.customer_id)) return null;

  return (
    <Card
      title="Billing"
      icon={<WalletCards size={16} />}
      actions={
        data.chargebee.customer_url ? (
          <a className="ui-btn ui-btn-ghost ui-btn-sm" href={data.chargebee.customer_url} target="_blank" rel="noopener noreferrer">
            Chargebee <ArrowUpRight size={13} />
          </a>
        ) : null
      }
    >
      <div className="ui-stack" style={{ gap: 14 }}>
        {subscriptions.length ? (
          <>
            <div className="ipay-facts">
              <div>
                <span className="ui-muted ui-small">MRR</span>
                <strong>{data.chargebee.mrr.length ? data.chargebee.mrr.map((m) => amountText(m.amount, m.currency)).join(" + ") : amountText("0", "USD")}</strong>
              </div>
              <div>
                <span className="ui-muted ui-small">Next renewal</span>
                <strong>{data.chargebee.next_renewal ? date(data.chargebee.next_renewal) : "—"}</strong>
              </div>
            </div>
            <ul className="ipay-subs">
              {subscriptions.map((sub) => (
                <li key={sub.id}>
                  <Repeat size={14} aria-hidden className="ipay-sub-icon" />
                  <div className="ipay-row-main">
                    <strong>{sub.data.plan ?? sub.title ?? "Subscription"}</strong>
                    <span className="ui-muted ui-small">
                      {amountText(sub.amount, sub.currency)}/mo
                      {sub.data.billing_period ? ` · billed every ${sub.data.billing_period}` : ""}
                      {sub.status === "cancelled" && sub.data.cancelled_at
                        ? ` · cancelled ${date(sub.data.cancelled_at)}${sub.data.cancel_reason ? ` (${sub.data.cancel_reason})` : ""}`
                        : sub.data.next_billing_at
                          ? ` · next bill ${date(sub.data.next_billing_at)}`
                          : sub.data.current_term_end
                            ? ` · term ends ${date(sub.data.current_term_end)}`
                            : ""}
                      {sub.status === "in_trial" && sub.data.trial_end ? ` · trial ends ${date(sub.data.trial_end)}` : ""}
                    </span>
                  </div>
                  <div className="ipay-row-side">
                    <Badge tone={SUBSCRIPTION_TONE[sub.status ?? ""] ?? "neutral"} dot>
                      {sub.data.status_label ?? sub.status}
                    </Badge>
                    {sub.url ? (
                      <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={sub.url} target="_blank" rel="noopener noreferrer" aria-label="Open in Chargebee">
                        <ArrowUpRight size={14} />
                      </a>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {data.payments.length ? (
          <div>
            {subscriptions.length ? <h3 className="ipay-subhead">Payments</h3> : null}
            <PaymentsTable rows={data.payments.slice(0, 12)} showInvoice />
          </div>
        ) : !subscriptions.length ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>Linked to a Chargebee customer with no subscriptions yet.</p>
        ) : null}
      </div>
    </Card>
  );
}

export default RecordBilling;
