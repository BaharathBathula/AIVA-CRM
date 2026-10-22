"use client";

import { Pencil, Plus, Trash2, UsersRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Avatar, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import type { Member, Team } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function TeamsPage() {
  return (
    <SettingsShell>
      <Teams />
    </SettingsShell>
  );
}

function Teams() {
  const { can } = useSession();
  const toast = useToast();
  const editable = can("admin.teams");
  const teams = useApi<Team[]>("/admin/teams");
  const members = useApi<Member[]>("/meta/members");
  const [editing, setEditing] = useState<Team | "new" | null>(null);
  const [deleting, setDeleting] = useState<Team | null>(null);
  const [busy, setBusy] = useState(false);

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/admin/teams/${deleting.id}`);
      toast(`${deleting.name} deleted. Its members now have no team.`);
      setDeleting(null);
      await teams.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Teams"
        description="Group people for case queues, forecasting roll-ups and reporting. Assign members from the Users page."
        actions={
          editable ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New team
            </button>
          ) : null
        }
      />
      {teams.error ? <Alert>{teams.error}</Alert> : null}
      {teams.loading && !teams.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : teams.data?.length ? (
        <div className="ui-grid ui-grid-2">
          {teams.data.map((team) => (
            <Card
              key={team.id}
              title={team.name}
              icon={<UsersRound size={16} />}
              actions={
                editable ? (
                  <>
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${team.name}`} onClick={() => setEditing(team)}>
                      <Pencil size={14} />
                    </button>
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${team.name}`} onClick={() => setDeleting(team)}>
                      <Trash2 size={14} />
                    </button>
                  </>
                ) : null
              }
            >
              <p className="ui-muted ui-small" style={{ margin: "0 0 12px" }}>
                {team.description || "No description."}
              </p>
              <dl className="ui-dl">
                <dt>Manager</dt>
                <dd>{team.manager_name ?? <span className="ui-muted">Not set</span>}</dd>
                <dt>Members</dt>
                <dd>
                  {team.members.length ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {team.members.map((member) => (
                        <span key={member.id} className="ui-row" style={{ gap: 6 }}>
                          <Avatar name={member.full_name} size={22} />
                          <span className="ui-small">{member.full_name}</span>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="ui-muted">No members yet</span>
                  )}
                </dd>
              </dl>
            </Card>
          ))}
        </div>
      ) : (
        <div className="ui-card">
          <EmptyState icon={<UsersRound size={20} />} title="No teams yet" message="Create teams to route cases and roll up forecasts by group." />
        </div>
      )}

      <TeamModal
        team={editing}
        members={members.data ?? []}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await teams.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.name ?? "team"}?`}
        message="Members stay in the workspace without a team. Cases assigned to the team keep their history."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function TeamModal({
  team,
  members,
  onClose,
  onSaved,
}: {
  team: Team | "new" | null;
  members: Member[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [manager, setManager] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!team) return;
    setName(team === "new" ? "" : team.name);
    setDescription(team === "new" ? "" : team.description ?? "");
    setManager(team === "new" ? "" : team.manager_user_id ?? "");
    setError(null);
  }, [team]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const payload = { name: name.trim(), description: description.trim() || null, manager_user_id: manager || null };
    try {
      if (team === "new") await api.post("/admin/teams", payload);
      else if (team) await api.patch(`/admin/teams/${team.id}`, payload);
      toast(team === "new" ? "Team created." : "Team saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(team)}
      title={team === "new" ? "New team" : "Edit team"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="team-form" className="ui-btn ui-btn-primary" disabled={busy}>
            Save team
          </button>
        </>
      }
    >
      <form id="team-form" className="ui-stack" onSubmit={submit}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Team name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} required />
        </Field>
        <Field label="Description">
          <textarea className="ui-textarea" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
        </Field>
        <Field label="Manager">
          <select className="ui-select" value={manager} onChange={(e) => setManager(e.target.value)}>
            <option value="">No manager</option>
            {members.map((member) => (
              <option key={member.id} value={member.id}>
                {member.full_name}
              </option>
            ))}
          </select>
        </Field>
      </form>
    </Modal>
  );
}
