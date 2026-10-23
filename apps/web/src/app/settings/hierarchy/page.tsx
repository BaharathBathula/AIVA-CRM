"use client";

import Link from "next/link";
import { Eye, Network, Plus, UsersRound, X } from "lucide-react";
import { useMemo, useState } from "react";

import { PlanChip, UpgradeGate } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Avatar, Badge, Card, LoadingRows, PageHeader } from "@/components/ui";
import { titleCase } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Member {
  user_id: string;
  name: string;
  email: string;
  job_title: string | null;
  role: string;
  team_id: string | null;
  reports_to_user_id: string | null;
  reports_to_name: string | null;
}

interface TeamRow {
  id: string;
  name: string;
  description: string | null;
  parent_team_id: string | null;
  manager_user_id: string | null;
  manager_name: string | null;
  members: { user_id: string; name: string; role: string; primary: boolean }[];
}

interface Hierarchy {
  visibility: "all" | "team" | "hierarchy";
  features: { multi_team: boolean; reporting_hierarchy: boolean };
  members: Member[];
  teams: TeamRow[];
}

const MODES: { key: Hierarchy["visibility"]; title: string; text: string; feature?: string }[] = [
  { key: "all", title: "Everyone sees everything", text: "Every user can see every lead, contact, account and deal." },
  {
    key: "team",
    title: "Team based",
    text: "People see records owned by anyone on their teams. Team managers and leads also see their sub-teams.",
    feature: "multi_team",
  },
  {
    key: "hierarchy",
    title: "Reporting hierarchy",
    text: "People see their own records and everything owned by the people who report to them, all the way down.",
    feature: "reporting_hierarchy",
  },
];

export default function HierarchyPage() {
  return (
    <SettingsShell permission="admin.users" what="the hierarchy">
      <HierarchySettings />
    </SettingsShell>
  );
}

function HierarchySettings() {
  const toast = useToast();
  const { has, can } = useSession();
  const data = useApi<Hierarchy>("/org/hierarchy");
  const d = data.data;

  async function setMode(mode: Hierarchy["visibility"]) {
    try {
      await api.put("/org/visibility", { mode });
      toast("Record visibility updated. It applies on everyone's next page load.");
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Hierarchy and visibility"
        description="Who reports to whom, how teams nest, and which records each person can see. Admins and owners always see everything."
      />
      {data.error ? <Alert>{data.error}</Alert> : null}
      {!d ? (
        <Card>
          <LoadingRows />
        </Card>
      ) : (
        <div className="ui-stack">
          <Card title="Who can see which records" icon={<Eye size={16} />}>
            <div className="visibility-options">
              {MODES.map((mode) => {
                const locked = mode.feature ? !has(mode.feature) : false;
                const on = d.visibility === mode.key;
                return (
                  <button
                    key={mode.key}
                    type="button"
                    className={`visibility-option${on ? " on" : ""}`}
                    disabled={locked || !can("admin.settings")}
                    aria-pressed={on}
                    onClick={() => void setMode(mode.key)}
                  >
                    <span className="ui-row" style={{ gap: 8 }}>
                      <span className={`radio-dot${on ? " on" : ""}`} />
                      <strong>{mode.title}</strong>
                      {mode.feature ? <PlanChip feature={mode.feature} /> : null}
                    </span>
                    <span className="ui-muted ui-small">{mode.text}</span>
                  </button>
                );
              })}
            </div>
          </Card>
          <UpgradeGate feature="reporting_hierarchy">
            <ReportingLines members={d.members} onChanged={() => void data.reload()} />
            <TeamTree teams={d.teams} members={d.members} onChanged={() => void data.reload()} />
          </UpgradeGate>
        </div>
      )}
    </>
  );
}

function ReportingLines({ members, onChanged }: { members: Member[]; onChanged: () => void }) {
  const toast = useToast();
  const byManager = useMemo(() => {
    const map = new Map<string | null, Member[]>();
    for (const m of members) {
      const key = m.reports_to_user_id && members.some((x) => x.user_id === m.reports_to_user_id) ? m.reports_to_user_id : null;
      map.set(key, [...(map.get(key) ?? []), m]);
    }
    return map;
  }, [members]);

  async function setManager(member: Member, managerId: string) {
    try {
      await api.put(`/org/members/${member.user_id}/manager`, { reports_to_user_id: managerId || null });
      toast(managerId ? `${member.name} now reports to ${members.find((m) => m.user_id === managerId)?.name}.` : `${member.name} has no manager.`);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  function branch(managerId: string | null, depth: number): React.ReactNode {
    return (byManager.get(managerId) ?? []).map((member) => (
      <div key={member.user_id}>
        <div className="org-node" style={{ paddingLeft: 12 + depth * 26 }}>
          {depth ? <span className="org-elbow" aria-hidden /> : null}
          <Avatar name={member.name} size={30} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <strong className="ui-truncate" style={{ display: "block" }}>
              {member.name}
            </strong>
            <span className="ui-muted ui-small">{member.job_title || titleCase(member.role)}</span>
          </div>
          <select className="ui-select sm" style={{ width: 200 }} value={member.reports_to_user_id ?? ""} onChange={(e) => void setManager(member, e.target.value)} aria-label={`${member.name} reports to`}>
            <option value="">No manager</option>
            {members
              .filter((m) => m.user_id !== member.user_id)
              .map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  Reports to {m.name}
                </option>
              ))}
          </select>
        </div>
        {branch(member.user_id, depth + 1)}
      </div>
    ));
  }

  return (
    <Card title="Reporting lines" icon={<Network size={16} />} flush>
      <div className="org-tree">{branch(null, 0)}</div>
    </Card>
  );
}

function TeamTree({ teams, members, onChanged }: { teams: TeamRow[]; members: Member[]; onChanged: () => void }) {
  const toast = useToast();
  const { has } = useSession();
  const [adding, setAdding] = useState<Record<string, string>>({});
  const multi = has("multi_team");

  async function setParent(team: TeamRow, parentId: string) {
    try {
      await api.put(`/org/teams/${team.id}/parent`, { parent_team_id: parentId || null });
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function addMember(team: TeamRow) {
    const userId = adding[team.id];
    if (!userId) return;
    try {
      await api.post(`/org/teams/${team.id}/members`, { user_id: userId, role: "member" });
      setAdding({ ...adding, [team.id]: "" });
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function removeMember(team: TeamRow, userId: string) {
    try {
      await api.del(`/org/teams/${team.id}/members/${userId}`);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const children = (parentId: string | null): TeamRow[] =>
    teams.filter((t) => (t.parent_team_id && teams.some((x) => x.id === t.parent_team_id) ? t.parent_team_id : null) === parentId);

  function render(parentId: string | null, depth: number): React.ReactNode {
    return children(parentId).map((team) => (
      <div key={team.id}>
        <div className="team-node" style={{ marginLeft: depth * 26 }}>
          <div className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
            <span className="type-icon">
              <UsersRound size={15} />
            </span>
            <strong style={{ flex: 1 }}>{team.name}</strong>
            {team.manager_name ? <span className="ui-muted ui-small">Manager {team.manager_name}</span> : null}
            {multi ? (
              <select className="ui-select sm" style={{ width: 190 }} value={team.parent_team_id ?? ""} onChange={(e) => void setParent(team, e.target.value)} aria-label={`${team.name} sits under`}>
                <option value="">Top level</option>
                {teams
                  .filter((t) => t.id !== team.id)
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      Under {t.name}
                    </option>
                  ))}
              </select>
            ) : null}
          </div>
          <div className="chip-list" style={{ marginTop: 10 }}>
            {team.members.map((m) => (
              <span key={`${m.user_id}-${m.primary}`} className="member-chip">
                {m.name}
                {m.role === "lead" ? <Badge tone="info">Lead</Badge> : null}
                {!m.primary && multi ? (
                  <button type="button" aria-label={`Remove ${m.name} from ${team.name}`} onClick={() => void removeMember(team, m.user_id)}>
                    <X size={12} />
                  </button>
                ) : null}
              </span>
            ))}
            {!team.members.length ? <span className="ui-muted ui-small">No members</span> : null}
          </div>
          {multi ? (
            <div className="ui-row" style={{ gap: 6, marginTop: 10 }}>
              <select
                className="ui-select sm"
                style={{ width: 200 }}
                value={adding[team.id] ?? ""}
                onChange={(e) => setAdding({ ...adding, [team.id]: e.target.value })}
                aria-label={`Add someone to ${team.name}`}
              >
                <option value="">Add someone to this team…</option>
                {members
                  .filter((m) => !team.members.some((x) => x.user_id === m.user_id))
                  .map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.name}
                    </option>
                  ))}
              </select>
              <button type="button" className="ui-btn ui-btn-sm" disabled={!adding[team.id]} onClick={() => void addMember(team)}>
                <Plus size={13} /> Add
              </button>
            </div>
          ) : null}
        </div>
        {render(team.id, depth + 1)}
      </div>
    ));
  }

  return (
    <Card
      title="Teams"
      icon={<UsersRound size={16} />}
      actions={
        <Link href="/settings/teams" className="ui-btn ui-btn-sm">
          Manage teams
        </Link>
      }
    >
      {!multi ? (
        <div style={{ marginBottom: 12 }}>
          <UpgradeGate feature="multi_team" compact />
        </div>
      ) : (
        <p className="ui-muted ui-small" style={{ marginTop: 0 }}>
          Nest teams under each other and put people on more than one team. Each person&apos;s main team is set on the Users page.
        </p>
      )}
      {teams.length ? <div className="ui-stack" style={{ gap: 10 }}>{render(null, 0)}</div> : <p className="empty-hint">No teams yet.</p>}
    </Card>
  );
}
