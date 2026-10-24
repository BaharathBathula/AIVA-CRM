"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, KeyRound } from "lucide-react";
import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { IntegrationTile } from "@/components/integrations/common";
import { DeliveriesCard, SignatureCard, SubscriptionsCard } from "@/components/integrations/webhooks";
import { useSession } from "@/components/providers";
import { Alert, Card, PageHeader, Tabs } from "@/components/ui";

type Tab = "subscriptions" | "deliveries";

const TOOLS = [
  { key: "zapier", name: "Zapier", color: "#ff4f00", monogram: "Z" },
  { key: "make", name: "Make", color: "#6d00cc", monogram: "M" },
  { key: "n8n", name: "n8n", color: "#ea4b71", monogram: "n8n" },
];

export default function WebhooksPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <Webhooks />
      </Suspense>
    </AppShell>
  );
}

function Webhooks() {
  const params = useSearchParams();
  const router = useRouter();
  const { has } = useSession();
  const tab: Tab = params.get("tab") === "deliveries" ? "deliveries" : "subscriptions";

  return (
    <>
      <Link href="/integrations" className="int-back">
        <ArrowLeft size={15} /> All integrations
      </Link>
      <PageHeader
        title="Webhooks"
        description="Send AIVA events (new leads, deals won, tasks completed and more) to Zapier, Make, n8n or any URL. Events are queued with the change, signed, and retried for about a day if the receiver is down."
        actions={
          <Link href="/settings/api-keys" className="ui-btn">
            <KeyRound size={15} /> API keys
          </Link>
        }
      />
      {!has("api_access") ? (
        <Alert tone="info">
          Webhooks need API access, which isn&apos;t included in your plan. <Link href="/settings/billing">See plans</Link>.
        </Alert>
      ) : (
        <div className="ila-page">
          <div className="ui-stack">
            <Tabs<Tab>
              label="Webhooks"
              value={tab}
              onChange={(next) => router.replace(next === "deliveries" ? "/integrations/webhooks?tab=deliveries" : "/integrations/webhooks")}
              items={[
                { value: "subscriptions", label: "Subscriptions" },
                { value: "deliveries", label: "Deliveries" },
              ]}
            />
            {tab === "subscriptions" ? <SubscriptionsCard title="Subscriptions" /> : <DeliveriesCard limit={50} />}
          </div>
          <aside className="ui-stack">
            <Card title="Set up by tool">
              <ul className="ila-tools">
                {TOOLS.map((tool) => (
                  <li key={tool.key}>
                    <Link href={`/integrations/${tool.key}`}>
                      <IntegrationTile item={tool} size={30} />
                      <span>{tool.name}</span>
                    </Link>
                  </li>
                ))}
              </ul>
              <p className="ui-muted ui-small" style={{ margin: "10px 0 0" }}>
                Each tool has its own on/off switch. While one is off, its webhooks wait instead of sending.
              </p>
            </Card>
            <SignatureCard />
          </aside>
        </div>
      )}
    </>
  );
}
