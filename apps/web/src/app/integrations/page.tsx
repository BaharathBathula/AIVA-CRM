"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Search, Users } from "lucide-react";
import { Suspense, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { IntegrationSwitch, IntegrationTile, StatusBadge } from "@/components/integrations/common";
import type { CatalogItem, IntegrationDetail } from "@/components/integrations/types";
import { PlanChip } from "@/components/plan";
import { useToast } from "@/components/providers";
import { Alert, Kpi, LoadingRows, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/use-api";

type Catalog = { items: CatalogItem[]; categories: string[]; can_manage: boolean };
type Filter = "all" | "on" | "connected" | "attention";

export default function IntegrationsPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <Integrations />
      </Suspense>
    </AppShell>
  );
}

function Integrations() {
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const catalog = useApi<Catalog>("/integration-hub/catalog");
  const [term, setTerm] = useState("");
  const [category, setCategory] = useState(params.get("category") ?? "All");
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    const error = params.get("error");
    if (error) {
      toast(error, "danger");
      router.replace("/integrations");
    }
  }, [params, router, toast]);

  const items = catalog.data?.items ?? [];
  const shown = useMemo(() => {
    const q = term.trim().toLowerCase();
    return items.filter((item) => {
      if (category !== "All" && item.category !== category) return false;
      if (filter === "on" && !item.switched_on) return false;
      if (filter === "connected" && !["connected", "ready"].includes(item.status)) return false;
      if (filter === "attention" && !["error", "needs_reauth", "not_connected"].includes(item.status)) return false;
      return !q || `${item.name} ${item.vendor} ${item.category} ${item.summary}`.toLowerCase().includes(q);
    });
  }, [items, term, category, filter]);

  const onCount = items.filter((i) => i.switched_on).length;
  const connected = items.filter((i) => i.status === "connected" || i.status === "ready").length;
  const attention = items.filter((i) => i.status === "error" || i.status === "needs_reauth").length;

  function updated(detail: IntegrationDetail) {
    catalog.setData((current) =>
      current
        ? {
            ...current,
            items: current.items.map((item) =>
              item.key === detail.integration.key
                ? { ...item, switched_on: detail.switched_on, enabled: detail.enabled, status: detail.status }
                : item.depends_on === detail.integration.key && !detail.switched_on
                  ? { ...item, switched_on: false, enabled: false, status: "off" }
                  : item,
            ),
          }
        : current,
    );
  }

  const categories = ["All", ...(catalog.data?.categories ?? [])];

  return (
    <>
      <PageHeader
        title="Integrations"
        description="Connect the tools your team already uses. Turn each integration on or off for the whole workspace; personal ones, like your mailbox or calendar, each person connects for themselves."
        actions={
          <label className="settings-nav-search" style={{ width: 260, background: "var(--surface)" }}>
            <Search size={15} aria-hidden />
            <input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search integrations" aria-label="Search integrations" />
          </label>
        }
      />
      {catalog.error ? <Alert>{catalog.error}</Alert> : null}
      {!catalog.data ? (
        <div className="ui-card">
          <LoadingRows rows={8} />
        </div>
      ) : (
        <div className="ui-stack">
          <div className="settings-kpis int-kpis">
            <Kpi label="Available" value={items.length} note={`${items.filter((i) => i.available).length} in your plan`} />
            <Kpi label="Turned on" value={onCount} />
            <Kpi label="Connected for you" value={connected} />
            <Kpi label="Need attention" value={attention} tone={attention ? "danger" : undefined} />
          </div>

          <div className="int-filters">
            <div className="int-chips" role="tablist" aria-label="Category">
              {categories.map((name) => (
                <button key={name} type="button" role="tab" aria-selected={category === name} className={`int-chip${category === name ? " on" : ""}`} onClick={() => setCategory(name)}>
                  {name}
                  <span className="count">{name === "All" ? items.length : items.filter((i) => i.category === name).length}</span>
                </button>
              ))}
            </div>
            <select className="ui-select sm" aria-label="Show" value={filter} onChange={(event) => setFilter(event.target.value as Filter)} style={{ width: 190 }}>
              <option value="all">All integrations</option>
              <option value="on">Turned on</option>
              <option value="connected">Connected</option>
              <option value="attention">Needs attention</option>
            </select>
          </div>

          {category === "All" && !term && filter === "all" ? (
            (catalog.data.categories ?? []).map((name) => {
              const group = shown.filter((i) => i.category === name);
              if (!group.length) return null;
              return (
                <section key={name} className="int-group">
                  <h2>{name}</h2>
                  <div className="int-grid">
                    {group.map((item) => (
                      <IntegrationCard key={item.key} item={item} canManage={catalog.data!.can_manage} onChanged={updated} />
                    ))}
                  </div>
                </section>
              );
            })
          ) : shown.length ? (
            <div className="int-grid">
              {shown.map((item) => (
                <IntegrationCard key={item.key} item={item} canManage={catalog.data!.can_manage} onChanged={updated} />
              ))}
            </div>
          ) : (
            <div className="ui-card">
              <div className="ui-empty">
                <h3>No integrations match</h3>
                <p>Try another search or category.</p>
              </div>
            </div>
          )}
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Looking for app passwords, the calendar feed, Google Ads or QuickBooks? They&apos;re in <Link href="/settings/integrations">Other connections</Link>.
          </p>
        </div>
      )}
    </>
  );
}

function IntegrationCard({ item, canManage, onChanged }: { item: CatalogItem; canManage: boolean; onChanged: (detail: IntegrationDetail) => void }) {
  return (
    <section className={`ui-card int-card${item.switched_on ? " on" : ""}`}>
      <div className="int-card-head">
        <IntegrationTile item={item} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <Link href={`/integrations/${item.key}`} className="int-card-title">
            {item.name}
          </Link>
          <div className="ui-muted ui-small">
            {item.category}
            {item.personal ? " · per person" : ""}
          </div>
        </div>
        {item.available ? <IntegrationSwitch item={item} canManage={canManage} onChanged={onChanged} /> : <PlanChip feature={item.feature} />}
      </div>
      <p className="int-card-summary">{item.summary}</p>
      <div className="int-card-foot">
        <StatusBadge status={item.status} />
        {item.personal && item.people_connected ? (
          <span className="ui-muted ui-small" title="People who connected their account">
            <Users size={12} style={{ verticalAlign: -1 }} /> {item.people_connected}
          </span>
        ) : null}
        {item.account_label && item.status === "connected" ? <span className="ui-muted ui-small ui-truncate">{item.account_label}</span> : null}
        <span className="ui-spacer" />
        <Link href={`/integrations/${item.key}`} className="ui-btn ui-btn-sm">
          {item.switched_on ? "Manage" : "Details"} <ArrowRight size={13} />
        </Link>
      </div>
    </section>
  );
}
