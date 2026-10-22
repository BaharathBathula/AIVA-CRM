"use client";

import { Copy, KeyRound, MailPlus, MoreHorizontal, Search, ShieldCheck, UserCheck, UserX, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Avatar, Badge, ConfirmDialog, Field, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { AdminUser, Role, Team } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Filter = "all" | "active" | "invited" | "deactivated";

export default function UsersPage() {
  return (
    <SettingsShell permission="admin.users" what="users">
      <Users />
    </SettingsShell>
  );
}

function Users() {
  const toast = useToast();
  const users = useApi<AdminUser[]>("/admin/users");
  const roles = useApi<Role[]>("/admin/roles");
  const teams = useApi<Team[]>("/admin/teams");
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [link, setLink] = useState<{ title: string; url: string } | null>(null);
  const [removing, setRemoving] = useState<AdminUser | null>(null);
  const [busy, setBusy] = useState(false);

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (users.data ?? []).filter(
      (user) =>
        (filter === "all" || user.status === filter) &&
        (!term || user.full_name.toLowerCase().includes(term) || user.email.toLowerCase().includes(term))
    );
  }, [filter, search, users.data]);

  const counts = useMemo(() => {
    const list = users.data ?? [];
    return {
      all: list.length,
      active: list.filter((u) => u.status === "active").length,
      invited: list.filter((u) => u.status === "invited").length,
      deactivated: list.filter((u) => u.status === "deactivated").length,
    };
  }, [users.data]);

  const roleName = (key: string) => roles.data?.find((role) => role.key === key)?.name ?? key;

  async function update(user: AdminUser, changes: Record<string, unknown>, message: string) {
    try {
      await api.patch(`/admin/users/${user.id}`, changes);
      toast(message);
      await users.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function resetPassword(user: AdminUser) {
    try {
      const result = await api.post<{ message?: string; reset_url?: string }>(`/admin/users/${user.id}/reset-password`);
      if (result.reset_url) setLink({ title: `Password reset link for ${user.full_name}`, url: result.reset_url });
      else toast(`Reset link emailed to ${user.email}.`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await api.del(`/admin/users/${removing.id}`);
      toast(`${removing.full_name} was removed from the workspace.`);
      setRemoving(null);
      await users.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="Users"
        description="Invite teammates, set their role and team, and control who can sign in."
        actions={
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => setInviteOpen(true)}>
            <MailPlus size={16} /> Invite user
          </button>
        }
      />

      <div className="ui-toolbar">
        <Tabs
          label="Filter users"
          value={filter}
          onChange={setFilter}
          items={[
            { value: "all", label: "All", count: counts.all },
            { value: "active", label: "Active", count: counts.active },
            { value: "invited", label: "Invited", count: counts.invited },
            { value: "deactivated", label: "Deactivated", count: counts.deactivated },
          ]}
        />
        <span className="ui-spacer" />
        <div className="ui-search">
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search name or email" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>

      {users.error ? <Alert>{users.error}</Alert> : null}

      <div className="ui-card">
        {users.loading && !users.data ? (
          <LoadingRows />
        ) : (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>Team</th>
                  <th>Status</th>
                  <th>Security</th>
                  <th>Last sign-in</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <div className="ui-row">
                        <Avatar name={user.full_name} size={32} />
                        <div style={{ minWidth: 0 }}>
                          <div className="cell-title">
                            {user.full_name} {user.is_you ? <span className="ui-muted">(you)</span> : null}
                          </div>
                          <div className="cell-sub">{user.email}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <select
                        className="ui-select sm"
                        style={{ width: 180 }}
                        value={user.role}
                        disabled={user.is_you || user.role === "owner"}
                        aria-label={`Role for ${user.full_name}`}
                        onChange={(e) => void update(user, { role: e.target.value }, `${user.full_name} is now ${roleName(e.target.value)}.`)}
                      >
                        {(roles.data ?? [])
                          .filter((role) => role.key !== "owner" || user.role === "owner")
                          .map((role) => (
                            <option key={role.key} value={role.key}>
                              {role.name}
                            </option>
                          ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="ui-select sm"
                        style={{ width: 160 }}
                        value={user.team_id ?? ""}
                        aria-label={`Team for ${user.full_name}`}
                        onChange={(e) =>
                          void update(
                            user,
                            e.target.value ? { team_id: e.target.value } : { clear_team: true },
                            e.target.value ? "Team updated." : "Removed from team."
                          )
                        }
                      >
                        <option value="">No team</option>
                        {(teams.data ?? []).map((team) => (
                          <option key={team.id} value={team.id}>
                            {team.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      {user.status === "active" ? (
                        user.locked ? (
                          <Badge tone="danger" dot>
                            Locked
                          </Badge>
                        ) : (
                          <Badge tone="success" dot>
                            Active
                          </Badge>
                        )
                      ) : user.status === "invited" ? (
                        <Badge tone="info" dot>
                          Invited
                        </Badge>
                      ) : (
                        <Badge dot>Deactivated</Badge>
                      )}
                    </td>
                    <td>
                      {user.mfa_enabled ? (
                        <Badge tone="success">
                          <ShieldCheck size={12} /> 2-step on
                        </Badge>
                      ) : (
                        <span className="ui-muted ui-small">Password</span>
                      )}
                    </td>
                    <td className="ui-muted ui-nowrap">{user.last_login_at ? relativeTime(user.last_login_at) : "Never"}</td>
                    <td style={{ textAlign: "right" }}>
                      {user.is_you ? null : (
                        <RowMenu
                          user={user}
                          onDeactivate={() => void update(user, { status: "deactivated" }, `${user.full_name} can no longer sign in.`)}
                          onActivate={() => void update(user, { status: "active" }, `${user.full_name} can sign in again.`)}
                          onReset={() => void resetPassword(user)}
                          onRemove={() => setRemoving(user)}
                        />
                      )}
                    </td>
                  </tr>
                ))}
                {!rows.length ? (
                  <tr>
                    <td colSpan={7} className="ui-muted" style={{ textAlign: "center", padding: 30 }}>
                      No users match.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <InviteModal
        open={inviteOpen}
        roles={roles.data ?? []}
        teams={teams.data ?? []}
        onClose={() => setInviteOpen(false)}
        onInvited={(result) => {
          setInviteOpen(false);
          void users.reload();
          if (result.invite_url) setLink({ title: `Invitation link for ${result.full_name}`, url: result.invite_url });
          else toast(`Invitation sent to ${result.email}.`);
        }}
      />

      <Modal open={Boolean(link)} title={link?.title ?? ""} onClose={() => setLink(null)} description="No email service is configured in development, so share this link directly. It expires automatically.">
        {link ? (
          <div className="ui-stack">
            <input className="ui-input ui-mono" readOnly value={link.url} onFocus={(e) => e.currentTarget.select()} />
            <button
              type="button"
              className="ui-btn ui-btn-primary"
              onClick={() => {
                void navigator.clipboard
                  .writeText(link.url)
                  .then(() => toast("Link copied."))
                  .catch(() => toast("Select the link and copy it.", "danger"));
              }}
            >
              <Copy size={15} /> Copy link
            </button>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={Boolean(removing)}
        title="Remove user?"
        message={
          <>
            <strong>{removing?.full_name}</strong> loses access to this workspace immediately. Their records stay and can be
            reassigned.
          </>
        }
        confirmLabel="Remove user"
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setRemoving(null)}
      />
    </>
  );
}

function RowMenu({
  user,
  onDeactivate,
  onActivate,
  onReset,
  onRemove,
}: {
  user: AdminUser;
  onDeactivate: () => void;
  onActivate: () => void;
  onReset: () => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function run(action: () => void) {
    setOpen(false);
    action();
  }

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Actions for ${user.full_name}`} onClick={() => setOpen(!open)}>
        <MoreHorizontal size={16} />
      </button>
      {open ? (
        <div className="ui-popover" style={{ right: 0, top: "calc(100% + 4px)" }} role="menu">
          {user.status !== "invited" ? (
            <button type="button" className="ui-menu-item" onClick={() => run(onReset)}>
              <KeyRound size={15} /> Send password reset
            </button>
          ) : null}
          {user.status === "deactivated" ? (
            <button type="button" className="ui-menu-item" onClick={() => run(onActivate)}>
              <UserCheck size={15} /> Reactivate
            </button>
          ) : user.locked ? (
            <button type="button" className="ui-menu-item" onClick={() => run(onActivate)}>
              <UserCheck size={15} /> Unlock sign-in
            </button>
          ) : null}
          {user.status !== "deactivated" && user.role !== "owner" ? (
            <button type="button" className="ui-menu-item" onClick={() => run(onDeactivate)}>
              <UserX size={15} /> Deactivate
            </button>
          ) : null}
          {user.role !== "owner" ? (
            <>
              <div className="ui-menu-sep" />
              <button type="button" className="ui-menu-item" style={{ color: "var(--danger)" }} onClick={() => run(onRemove)}>
                <Trash2 size={15} /> Remove from workspace
              </button>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function InviteModal({
  open,
  roles,
  teams,
  onClose,
  onInvited,
}: {
  open: boolean;
  roles: Role[];
  teams: Team[];
  onClose: () => void;
  onInvited: (result: { email: string; full_name: string; invite_url?: string }) => void;
}) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState("member");
  const [teamId, setTeamId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setEmail("");
      setFullName("");
      setRole("member");
      setTeamId("");
      setError(null);
    }
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ invite_url?: string }>("/admin/users/invite", {
        email: email.trim().toLowerCase(),
        full_name: fullName.trim(),
        role,
        team_id: teamId || null,
      });
      onInvited({ email, full_name: fullName, invite_url: result.invite_url });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const selectedRole = roles.find((item) => item.key === role);

  return (
    <Modal
      open={open}
      title="Invite a teammate"
      description="They'll get a link to set a password and join this workspace."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="invite-form" className="ui-btn ui-btn-primary" disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null}
            Send invitation
          </button>
        </>
      }
    >
      <form id="invite-form" onSubmit={submit} className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Full name" required>
            <input className="ui-input" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
          </Field>
          <Field label="Work email" required>
            <input className="ui-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          <Field label="Role" hint={selectedRole?.description ?? undefined}>
            <select className="ui-select" value={role} onChange={(e) => setRole(e.target.value)}>
              {roles
                .filter((item) => item.key !== "owner")
                .map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Team">
            <select className="ui-select" value={teamId} onChange={(e) => setTeamId(e.target.value)}>
              <option value="">No team</option>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </form>
    </Modal>
  );
}
