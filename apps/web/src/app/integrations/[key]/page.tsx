"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, BookOpen, Check, ListChecks, Users } from "lucide-react";
import { Suspense, useEffect } from "react";

import { AppShell } from "@/components/app-shell";
import {
  ConnectButton,
  ConnectionSummary,
  CredentialsForm,
  DisconnectButton,
  IntegrationSwitch,
  IntegrationTile,
  LogList,
  OAuthAppCard,
  StatusBadge,
  TestButton,
  UpgradeNote,
} from "@/components/integrations/common";
import { INTEGRATION_PANELS } from "@/components/integrations/registry";
import type { IntegrationDetail } from "@/components/integrations/types";
import { useToast } from "@/components/providers";
import { Alert, Card, LoadingRows } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export default function IntegrationPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <Integration />
      </Suspense>
    </AppShell>
  );
}

function Integration() {
  const { key } = useParams<{ key: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const detail = useApi<IntegrationDetail>(`/integration-hub/${key}`);

  useEffect(() => {
    const error = params.get("error");
    const connected = params.get("connected");
    if (error) toast(error, "danger");
    if (connected) toast("Connected.");
    if (error || connected) router.replace(`/integrations/${key}`);
  }, [params, router, toast, key]);

  if (detail.error && !detail.data) {
    return (
      <>
        <BackLink />
        <Alert>{detail.error}</Alert>
      </>
    );
  }
  if (!detail.data) {
    return (
      <>
        <BackLink />
        <div className="ui-card">
          <LoadingRows rows={8} />
        </div>
      </>
    );
  }

  const data = detail.data;
  const item = data.integration;
  const Panel = INTEGRATION_PANELS[item.key];
  const setDetail = (next: IntegrationDetail) => detail.setData(next);
  const reload = () => detail.reload();

  return (
    <>
      <BackLink />
      <header className="int-hero">
        <IntegrationTile item={item} size={56} />
        <div className="int-hero-text">
          <div className="ui-muted ui-small">
            {item.vendor} · {item.category}
            {item.personal ? " · each person connects their own account" : ""}
          </div>
          <h1>{item.name}</h1>
          <p>{item.summary}</p>
        </div>
        <div className="int-hero-side">
          <StatusBadge status={data.status} />
          {data.available ? (
            <span className="ui-row" style={{ gap: 8 }}>
              <span className="ui-small ui-muted">{data.switched_on ? "On" : "Off"}</span>
              <IntegrationSwitch item={{ key: item.key, name: item.name, switched_on: data.switched_on, available: data.available }} canManage={data.can_manage} onChanged={setDetail} />
            </span>
          ) : null}
        </div>
      </header>

      {!data.available ? <UpgradeNote item={item} /> : null}
      {data.available && !data.switched_on ? (
        <Alert tone="info">
          {item.name} is off for this workspace.{" "}
          {data.can_manage ? "Turn it on with the switch above to set it up." : "Ask a workspace admin to turn it on."}
        </Alert>
      ) : null}
      {data.switched_on && !data.dependency_enabled && item.depends_on ? (
        <Alert tone="warning">
          {item.name} needs <Link href={`/integrations/${item.depends_on}`}>{item.depends_on.replace(/_/g, " ")}</Link> to be on.
        </Alert>
      ) : null}

      <div className="int-layout">
        <div className="ui-stack">
          {data.available ? <ConnectionCard detail={data} setDetail={setDetail} /> : null}
          {Panel && data.available ? <Panel detail={data} setDetail={setDetail} reload={reload} /> : null}
          {item.personal && data.can_manage ? <PeopleCard detail={data} setDetail={setDetail} /> : null}
          <Card title="Activity" flush={false}>
            <LogList logs={data.logs} />
            {data.logs.length >= 40 ? (
              <p className="ui-small" style={{ margin: "10px 0 0" }}>
                <Link href={`/integrations/activity?provider=${item.key}`}>See all activity</Link>
              </p>
            ) : null}
          </Card>
        </div>
        <aside className="ui-stack">
          <Card title="What it does" icon={<Check size={17} />}>
            <ul className="int-list">
              {item.capabilities.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </Card>
          <Card title="Set up" icon={<ListChecks size={17} />}>
            <ol className="int-steps">
              {item.setup.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
            {item.docs_url ? (
              <a className="ui-small" href={item.docs_url} target="_blank" rel="noopener noreferrer">
                <BookOpen size={12} style={{ verticalAlign: -1 }} /> {item.vendor} developer docs
              </a>
            ) : null}
          </Card>
          {data.available && data.oauth ? <OAuthAppCard detail={data} onSaved={() => void reload()} /> : null}
        </aside>
      </div>
    </>
  );
}

function BackLink() {
  return (
    <Link href="/integrations" className="int-back">
      <ArrowLeft size={15} /> All integrations
    </Link>
  );
}

function ConnectionCard({ detail, setDetail }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void }) {
  const item = detail.integration;
  const connection = detail.connection;
  const title = item.personal ? "Your connection" : "Connection";

  if (item.auth === "depends") {
    return (
      <Card title={title}>
        {connection ? (
          <ConnectionSummary connection={connection}>
            <Link className="ui-btn" href={`/integrations/${item.depends_on}`}>
              Manage the {item.depends_on?.replace(/_/g, " ")} connection
            </Link>
          </ConnectionSummary>
        ) : (
          <p className="ui-small" style={{ margin: 0 }}>
            {item.name} uses your <Link href={`/integrations/${item.depends_on}`}>{item.depends_on?.replace(/_/g, " ")}</Link> connection. Connect that first.
          </p>
        )}
      </Card>
    );
  }

  if (item.auth === "webhook" || item.auth === "api_key") {
    // These set up in their own panel (URLs, keys, subscriptions).
    return null;
  }

  if (item.auth === "credentials") {
    return (
      <Card title={title}>
        <div className="ui-stack" style={{ gap: 14 }}>
          {connection ? (
            <ConnectionSummary connection={connection}>
              <TestButton detail={detail} />
              {detail.can_manage ? <DisconnectButton detail={detail} onDone={setDetail} /> : null}
            </ConnectionSummary>
          ) : null}
          <CredentialsForm detail={detail} onSaved={setDetail} submitLabel={connection ? "Save changes" : "Save and connect"} />
        </div>
      </Card>
    );
  }

  // OAuth
  const canConnect = item.personal || detail.can_manage;
  return (
    <Card title={title}>
      {connection ? (
        <ConnectionSummary connection={connection}>
          {canConnect && (connection.status !== "connected" || connection.last_error) ? <ConnectButton detail={detail} /> : null}
          <TestButton detail={detail} />
          {canConnect ? <DisconnectButton detail={detail} onDone={setDetail} /> : null}
        </ConnectionSummary>
      ) : !detail.enabled ? (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          Turn {item.name} on to connect.
        </p>
      ) : canConnect ? (
        <div className="ui-stack" style={{ gap: 10 }}>
          <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
            {item.personal
              ? `Sign in with ${item.vendor} to connect your own account. Only you can use it; admins can see that it's connected.`
              : `Sign in with ${item.vendor} once for the whole workspace.`}
          </p>
          {detail.oauth && !detail.oauth.configured ? (
            <Alert tone="warning">
              {detail.oauth.label} sign-in isn&apos;t set up yet.{" "}
              {detail.can_manage ? "Add your app's client ID and secret in the panel on the right." : "Ask an admin to set it up."}
            </Alert>
          ) : null}
          <div>
            <ConnectButton detail={detail} />
          </div>
        </div>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0 }}>
          An admin connects {item.name} for the workspace.
        </p>
      )}
    </Card>
  );
}

function PeopleCard({ detail, setDetail }: { detail: IntegrationDetail; setDetail: (d: IntegrationDetail) => void }) {
  return (
    <Card title="People connected" icon={<Users size={17} />} flush>
      {detail.people.length ? (
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Account</th>
                <th>Status</th>
                <th>Since</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {detail.people.map((person) => (
                <tr key={person.id}>
                  <td className="cell-title">{person.user_name ?? "Former member"}</td>
                  <td className="ui-small">{person.account_label ?? "—"}</td>
                  <td>
                    <StatusBadge status={person.status} />
                    {person.last_error ? <div className="ui-muted ui-small ui-truncate" style={{ maxWidth: 240 }}>{person.last_error}</div> : null}
                  </td>
                  <td className="ui-small ui-muted ui-nowrap">{relativeTime(person.connected_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    {person.user_id ? <DisconnectButton detail={detail} userId={person.user_id} onDone={setDetail} /> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="ui-muted ui-small" style={{ margin: 0, padding: "16px 22px" }}>
          Nobody has connected their account yet.
        </p>
      )}
    </Card>
  );
}
