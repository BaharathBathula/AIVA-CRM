"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { LogList } from "@/components/integrations/common";
import type { IntegrationLog } from "@/components/integrations/types";
import { Alert, Card, LoadingRows, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/use-api";

type Activity = { items: IntegrationLog[]; providers: { key: string; name: string }[] };

export default function IntegrationActivityPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <IntegrationActivity />
      </Suspense>
    </AppShell>
  );
}

function IntegrationActivity() {
  const params = useSearchParams();
  const [provider, setProvider] = useState(params.get("provider") ?? "");
  const [level, setLevel] = useState("");
  const activity = useApi<Activity>(`/integration-hub?limit=500${provider ? `&provider=${provider}` : ""}${level ? `&level=${level}` : ""}`);
  return (
    <>
      <PageHeader
        title="Integration activity"
        description="Connections, syncs, messages and webhooks across every integration. Errors show up here first."
        actions={
          <>
            <select className="ui-select sm" aria-label="Integration" value={provider} onChange={(event) => setProvider(event.target.value)} style={{ width: 220 }}>
              <option value="">All integrations</option>
              {(activity.data?.providers ?? []).map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
            <select className="ui-select sm" aria-label="Kind" value={level} onChange={(event) => setLevel(event.target.value)} style={{ width: 150 }}>
              <option value="">Everything</option>
              <option value="error">Errors</option>
              <option value="warning">Warnings</option>
              <option value="success">Successes</option>
              <option value="info">Info</option>
            </select>
          </>
        }
      />
      {activity.error ? <Alert>{activity.error}</Alert> : null}
      <Card>{activity.data ? <LogList logs={activity.data.items} empty="No integration activity yet." /> : <LoadingRows />}</Card>
    </>
  );
}
