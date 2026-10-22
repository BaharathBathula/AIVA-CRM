"use client";

import { Copy, Lock, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import type { PermissionDef, Role } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function RolesPage() {
  return (
    <SettingsShell permission="admin.users" what="roles">
      <Roles />
    </SettingsShell>
  );
}

function Roles() {
  const { can } = useSession();
  const toast = useToast();
  const editable = can("admin.roles");
  const roles = useApi<Role[]>("/admin/roles");
  const permissions = useApi<PermissionDef[]>(editable ? "/admin/permissions" : null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ name: string; description: string; permissions: Set<string> } | null>(null);
  const [createOpen, setCreateOpen] = useState<{ copyFrom: string | null } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);

  const selected = roles.data?.find((role) => role.key === selectedKey) ?? roles.data?.[0] ?? null;

  useEffect(() => {
    if (!selected) return;
    setDraft({ name: selected.name, description: selected.description ?? "", permissions: new Set(selected.permissions) });
  }, [selected]);

  const groups = useMemo(() => {
    const map = new Map<string, PermissionDef[]>();
    for (const permission of permissions.data ?? []) {
      map.set(permission.group, [...(map.get(permission.group) ?? []), permission]);
    }
    return [...map.entries()];
  }, [permissions.data]);

  const dirty =
    selected &&
    draft &&
    !selected.is_system &&
    (draft.name !== selected.name ||
      draft.description !== (selected.description ?? "") ||
      draft.permissions.size !== selected.permissions.length ||
      selected.permissions.some((key) => !draft.permissions.has(key)));

  async function save() {
    if (!selected || !draft) return;
    setBusy(true);
    try {
      await api.patch(`/admin/roles/${selected.key}`, {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        permissions: [...draft.permissions],
      });
      toast(`${draft.name} saved. Members get the new permissions on their next request.`);
      await roles.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.del(`/admin/roles/${selected.key}`);
      toast(`${selected.name} deleted.`);
      setDeleting(false);
      setSelectedKey(null);
      await roles.reload();
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
        title="Roles & permissions"
        description="Roles decide what people can see and do. Built-in roles cover most teams; duplicate one to tailor it."
        actions={
          editable ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreateOpen({ copyFrom: null })}>
              <Plus size={16} /> New role
            </button>
          ) : null
        }
      />
      {roles.error ? <Alert>{roles.error}</Alert> : null}
      {roles.loading && !roles.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <div className="ui-master-detail">
          <div className="ui-card" style={{ overflow: "hidden" }}>
            <div className="ui-list">
              {(roles.data ?? []).map((role) => (
                <button
                  key={role.key}
                  type="button"
                  className="ui-list-item"
                  onClick={() => setSelectedKey(role.key)}
                  style={{
                    border: 0,
                    borderTop: "1px solid var(--border)",
                    width: "100%",
                    textAlign: "left",
                    cursor: "pointer",
                    background: selected?.key === role.key ? "var(--primary-soft)" : "transparent",
                    font: "inherit",
                  }}
                >
                  <div className="main">
                    <div className="title">{role.name}</div>
                    <div className="sub">
                      {role.member_count} member{role.member_count === 1 ? "" : "s"} · {role.permissions.length} permissions
                    </div>
                  </div>
                  {role.is_system ? <Lock size={14} color="var(--muted)" aria-label="Built-in role" /> : <Badge tone="primary">Custom</Badge>}
                </button>
              ))}
            </div>
          </div>

          {selected && draft ? (
            <Card
              title={selected.is_system ? selected.name : "Edit role"}
              actions={
                editable ? (
                  <>
                    <button type="button" className="ui-btn ui-btn-sm" onClick={() => setCreateOpen({ copyFrom: selected.key })}>
                      <Copy size={14} /> Duplicate
                    </button>
                    {!selected.is_system ? (
                      <button type="button" className="ui-btn ui-btn-sm ui-btn-danger" onClick={() => setDeleting(true)}>
                        <Trash2 size={14} /> Delete
                      </button>
                    ) : null}
                  </>
                ) : null
              }
              footer={
                !selected.is_system && editable ? (
                  <>
                    <button
                      type="button"
                      className="ui-btn"
                      disabled={!dirty || busy}
                      onClick={() =>
                        setDraft({ name: selected.name, description: selected.description ?? "", permissions: new Set(selected.permissions) })
                      }
                    >
                      Discard
                    </button>
                    <button type="button" className="ui-btn ui-btn-primary" disabled={!dirty || busy} onClick={() => void save()}>
                      {busy ? <span className="ui-spinner" /> : null}
                      Save role
                    </button>
                  </>
                ) : null
              }
            >
              <div className="ui-stack">
                {selected.is_system ? (
                  <Alert tone="info">
                    {selected.description} Built-in roles can&apos;t be edited — duplicate it to make a custom version.
                  </Alert>
                ) : (
                  <div className="ui-form-grid">
                    <Field label="Role name">
                      <input
                        className="ui-input"
                        value={draft.name}
                        disabled={!editable}
                        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                      />
                    </Field>
                    <Field label="Description">
                      <input
                        className="ui-input"
                        value={draft.description}
                        disabled={!editable}
                        onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                      />
                    </Field>
                  </div>
                )}

                {editable ? (
                  groups.map(([group, items]) => (
                    <div className="ui-perm-group" key={group}>
                      <h4>{group}</h4>
                      {items.map((permission) => (
                        <label className="ui-perm" key={permission.key}>
                          <input
                            type="checkbox"
                            checked={draft.permissions.has(permission.key)}
                            disabled={selected.is_system}
                            onChange={(e) => {
                              const next = new Set(draft.permissions);
                              if (e.target.checked) next.add(permission.key);
                              else next.delete(permission.key);
                              setDraft({ ...draft, permissions: next });
                            }}
                          />
                          <span>
                            <strong>{permission.label}</strong>
                            <span>{permission.description}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                  ))
                ) : (
                  <div className="ui-row" style={{ flexWrap: "wrap", gap: 6 }}>
                    {selected.permissions.map((key) => (
                      <Badge key={key}>{key}</Badge>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          ) : null}
        </div>
      )}

      <CreateRoleModal
        state={createOpen}
        roles={roles.data ?? []}
        onClose={() => setCreateOpen(null)}
        onCreated={async (key) => {
          setCreateOpen(null);
          await roles.reload();
          setSelectedKey(key);
        }}
      />

      <ConfirmDialog
        open={deleting}
        title={`Delete ${selected?.name ?? "role"}?`}
        message={
          selected?.member_count
            ? `${selected.member_count} member(s) still have this role. Move them to another role first.`
            : "This custom role will be removed. This can't be undone."
        }
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}

function CreateRoleModal({
  state,
  roles,
  onClose,
  onCreated,
}: {
  state: { copyFrom: string | null } | null;
  roles: Role[];
  onClose: () => void;
  onCreated: (key: string) => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [copyFrom, setCopyFrom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!state) return;
    const source = roles.find((role) => role.key === state.copyFrom);
    setName(source ? `${source.name} (custom)` : "");
    setDescription(source?.description ?? "");
    setCopyFrom(state.copyFrom ?? "member");
    setError(null);
  }, [roles, state]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const role = await api.post<{ key: string }>("/admin/roles", {
        name: name.trim(),
        description: description.trim() || null,
        copy_from: copyFrom || null,
        permissions: [],
      });
      toast(`${name.trim()} created.`);
      onCreated(role.key);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(state)}
      title="New role"
      description="Start from an existing role's permissions, then adjust."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="role-form" className="ui-btn ui-btn-primary" disabled={busy}>
            Create role
          </button>
        </>
      }
    >
      <form id="role-form" className="ui-stack" onSubmit={submit}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Role name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} required placeholder="Renewals specialist" />
        </Field>
        <Field label="Description">
          <input className="ui-input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Start with permissions from">
          <select className="ui-select" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
            <option value="">No permissions</option>
            {roles.map((role) => (
              <option key={role.key} value={role.key}>
                {role.name}
              </option>
            ))}
          </select>
        </Field>
      </form>
    </Modal>
  );
}
