"use client";

import Link from "next/link";
import { Building2, Flame, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { useRefOptions } from "@/components/field-inputs";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Progress, Tabs } from "@/components/ui";
import { compactMoney, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Target {
  id: string;
  account_id: string;
  account_name: string;
  industry: string | null;
  tier: "tier_1" | "tier_2" | "tier_3";
  goal: string | null;
  owner_name: string | null;
  engagement_score: number;
  engagement: "hot" | "warm" | "cold";
  activities_30d: number;
  last_activity_at: string | null;
  contacts: number;
  engaged_contacts: number;
  email_opens_90d: number;
  open_pipeline: number;
  won_revenue: number;
  committee_covered: string[];
  committee_missing: string[];
}

interface Abm {
  items: Target[];
  summary: { targets: number; by_tier: Record<string, number>; hot: number; open_pipeline: number; won_revenue: number; average_engagement: number };
}

const TIER_LABEL = { tier_1: "Tier 1", tier_2: "Tier 2", tier_3: "Tier 3" };
const HEAT_TONE = { hot: "danger", warm: "warning", cold: "neutral" } as const;

export default function AbmPage() {
  return (
    <FeaturePage feature="abm">
      <Abm />
    </FeaturePage>
  );
}

function Abm() {
  const toast = useToast();
  const { can } = useSession();
  const abm = useApi<Abm>("/abm/accounts");
  const [tier, setTier] = useState<"all" | "tier_1" | "tier_2" | "tier_3">("all");
  const [adding, setAdding] = useState(false);
  const rows = (abm.data?.items ?? []).filter((r) => tier === "all" || r.tier === tier);
  const manage = can("marketing.manage");

  async function update(row: Target, nextTier: Target["tier"]) {
    try {
      await api.patch(`/abm/accounts/${row.id}`, { tier: nextTier, goal: row.goal, notes: null });
      await abm.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove(row: Target) {
    try {
      await api.del(`/abm/accounts/${row.id}`);
      toast(`${row.account_name} removed from targets.`);
      await abm.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const s = abm.data?.summary;
  return (
    <>
      <PageHeader
        title="Account based marketing"
        description="Your target accounts by tier, how engaged each one is, and which roles in the buying committee you still need to reach."
        actions={
          manage ? (
            <>
              <Link href="/marketing/campaigns?new=1" className="ui-btn">
                Email target accounts
              </Link>
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setAdding(true)}>
                <Plus size={16} /> Add target accounts
              </button>
            </>
          ) : null
        }
      />
      {abm.error ? <Alert>{abm.error}</Alert> : null}
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Target accounts" value={s?.targets ?? "—"} note={s ? `${s.by_tier.tier_1} tier 1 · ${s.by_tier.tier_2} tier 2 · ${s.by_tier.tier_3} tier 3` : undefined} />
        <Kpi label="Hot accounts" value={s?.hot ?? "—"} note={s ? `Average engagement ${s.average_engagement}/100` : undefined} />
        <Kpi label="Open pipeline" value={s ? compactMoney(s.open_pipeline) : "—"} />
        <Kpi label="Won revenue" value={s ? compactMoney(s.won_revenue) : "—"} />
      </div>
      <Card
        title="Target accounts"
        actions={
          <Tabs
            label="Tier"
            value={tier}
            onChange={setTier}
            items={[
              { value: "all", label: "All" },
              { value: "tier_1", label: "Tier 1", count: s?.by_tier.tier_1 },
              { value: "tier_2", label: "Tier 2", count: s?.by_tier.tier_2 },
              { value: "tier_3", label: "Tier 3", count: s?.by_tier.tier_3 },
            ]}
          />
        }
        flush
      >
        {abm.loading && !abm.data ? (
          <LoadingRows />
        ) : rows.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Tier</th>
                  <th>Engagement</th>
                  <th>Buying committee</th>
                  <th className="ui-num">Pipeline</th>
                  <th>Last touch</th>
                  {manage ? <th aria-label="Actions" /> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td className="cell-title">
                      <Link href={`/accounts/${row.account_id}`}>{row.account_name}</Link>
                      <div className="ui-muted ui-small">{row.goal || row.industry || "—"}</div>
                    </td>
                    <td>
                      {manage ? (
                        <select className="ui-select sm" value={row.tier} onChange={(e) => void update(row, e.target.value as Target["tier"])} aria-label="Tier">
                          {Object.entries(TIER_LABEL).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        TIER_LABEL[row.tier]
                      )}
                    </td>
                    <td style={{ minWidth: 170 }}>
                      <div className="ui-row" style={{ gap: 8 }}>
                        <Badge tone={HEAT_TONE[row.engagement]}>
                          {row.engagement === "hot" ? <Flame size={12} /> : null} {row.engagement}
                        </Badge>
                        <span className="ui-num ui-small">{row.engagement_score}</span>
                      </div>
                      <div style={{ marginTop: 6 }}>
                        <Progress value={row.engagement_score} />
                      </div>
                      <div className="ui-muted ui-small" style={{ marginTop: 4 }}>
                        {row.activities_30d} touches in 30d · {row.engaged_contacts}/{row.contacts} people engaged · {row.email_opens_90d} opens
                      </div>
                    </td>
                    <td style={{ minWidth: 220 }}>
                      <div className="chip-list">
                        {row.committee_covered.map((role) => (
                          <Badge key={role} tone="success">
                            {role}
                          </Badge>
                        ))}
                        {row.committee_missing.map((role) => (
                          <Badge key={role}>{role}?</Badge>
                        ))}
                      </div>
                    </td>
                    <td className="ui-num">{compactMoney(row.open_pipeline)}</td>
                    <td className="ui-muted">{row.last_activity_at ? relativeTime(row.last_activity_at) : "Never"}</td>
                    {manage ? (
                      <td style={{ textAlign: "right" }}>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Remove ${row.account_name}`} onClick={() => void remove(row)}>
                          <Trash2 size={14} />
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Building2 size={20} />} title="No target accounts" message="Pick the accounts you want to win and AIVA tracks their engagement and buying committee." />
        )}
      </Card>
      <p className="ui-muted ui-small">
        Green roles have at least one contact with a matching title. Grey roles with a “?” are gaps: nobody there yet.
      </p>
      <AddTargets open={adding} onClose={() => setAdding(false)} onAdded={() => void abm.reload()} existing={useMemo(() => new Set((abm.data?.items ?? []).map((r) => r.account_id)), [abm.data])} />
    </>
  );
}

function AddTargets({ open, onClose, onAdded, existing }: { open: boolean; onClose: () => void; onAdded: () => void; existing: Set<string> }) {
  const toast = useToast();
  const accounts = useRefOptions(open ? "account" : null);
  const [selected, setSelected] = useState<string[]>([]);
  const [tier, setTier] = useState("tier_2");
  const [goal, setGoal] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const shown = accounts.filter((a) => !existing.has(a.value) && a.label.toLowerCase().includes(search.toLowerCase()));

  async function add() {
    setBusy(true);
    try {
      const result = await api.post<{ added: number }>("/abm/accounts", { account_ids: selected, tier, goal: goal || null });
      toast(`Added ${result.added} target account${result.added === 1 ? "" : "s"}.`);
      setSelected([]);
      setGoal("");
      onAdded();
      onClose();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Add target accounts"
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !selected.length} onClick={() => void add()}>
            Add {selected.length || ""} account{selected.length === 1 ? "" : "s"}
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <div className="ui-form-grid">
          <Field label="Tier">
            <select className="ui-select" value={tier} onChange={(e) => setTier(e.target.value)}>
              <option value="tier_1">Tier 1 — one-to-one</option>
              <option value="tier_2">Tier 2 — one-to-few</option>
              <option value="tier_3">Tier 3 — one-to-many</option>
            </select>
          </Field>
          <Field label="Goal">
            <input className="ui-input" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Land a pilot this quarter" />
          </Field>
        </div>
        <input className="ui-input" placeholder="Search accounts" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search accounts" />
        <div style={{ maxHeight: 300, overflowY: "auto", display: "grid", gap: 2 }}>
          {shown.map((account) => (
            <label key={account.value} className="ui-check" style={{ padding: "8px 6px" }}>
              <input
                type="checkbox"
                checked={selected.includes(account.value)}
                onChange={(e) => setSelected(e.target.checked ? [...selected, account.value] : selected.filter((id) => id !== account.value))}
              />
              {account.label}
            </label>
          ))}
          {!shown.length ? <p className="ui-muted ui-small">No more accounts to add.</p> : null}
        </div>
      </div>
    </Modal>
  );
}
