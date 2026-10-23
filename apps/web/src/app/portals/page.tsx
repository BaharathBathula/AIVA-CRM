"use client";

import { ExternalLink, Globe, Pencil, Plus, Trash2, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";

import { CopyField } from "@/components/copy-field";
import { useRefOptions } from "@/components/field-inputs";
import { FeaturePage, PlanChip } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime, titleCase } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface PortalRow {
  id: string;
  name: string;
  slug: string;
  audience: "customer" | "partner";
  settings: { modules: string[]; accent: string | null; welcome: string | null; logo_text: string | null; support_email: string | null };
  is_active: boolean;
  url: string;
  user_count: number;
  available_modules: string[];
}

interface PortalUserRow {
  id: string;
  full_name: string;
  email: string;
  status: "active" | "disabled";
  contact_id: string | null;
  account_name: string | null;
  last_login_at: string | null;
  created_at: string;
}

const MODULE_LABEL: Record<string, string> = {
  cases: "Support cases",
  knowledge: "Help articles",
  quotes: "Quotes (accept online)",
  invoices: "Invoices",
  deal_registration: "Deal registration",
};

export default function PortalsPage() {
  return (
    <FeaturePage feature="customer_portal">
      <Portals />
    </FeaturePage>
  );
}

function Portals() {
  const toast = useToast();
  const { can } = useSession();
  const portals = useApi<{ items: PortalRow[]; features: { customer_portal: boolean; custom_portals: boolean } }>("/portals");
  const [editing, setEditing] = useState<PortalRow | "new" | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<PortalRow | null>(null);
  const [busy, setBusy] = useState(false);
  const items = portals.data?.items ?? [];
  const current = items.find((p) => p.id === selected) ?? items[0] ?? null;
  const admin = can("admin.settings");

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/portals/${deleting.id}`);
      toast(`${deleting.name} deleted.`);
      setDeleting(null);
      setSelected(null);
      await portals.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Portals"
        description="Give customers a place to open and follow support requests, read help articles, accept quotes and see invoices — and give partners a place to register deals."
        actions={
          admin ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New portal
            </button>
          ) : null
        }
      />
      {portals.error ? <Alert>{portals.error}</Alert> : null}
      {!portals.data ? (
        <Card>
          <LoadingRows />
        </Card>
      ) : items.length ? (
        <div className="settings-split">
          <nav className="ui-card object-nav" aria-label="Portals">
            {items.map((portal) => (
              <button key={portal.id} type="button" className={current?.id === portal.id ? "active" : undefined} onClick={() => setSelected(portal.id)}>
                <Globe size={14} /> {portal.name}
                {!portal.is_active ? <Badge>Off</Badge> : null}
              </button>
            ))}
          </nav>
          {current ? (
            <div className="ui-stack">
              <Card
                title={current.name}
                actions={
                  <>
                    <a className="ui-btn ui-btn-sm" href={`/portal/${current.slug}`} target="_blank" rel="noopener noreferrer">
                      <ExternalLink size={13} /> Open portal
                    </a>
                    {admin ? (
                      <>
                        <button type="button" className="ui-btn ui-btn-sm" onClick={() => setEditing(current)}>
                          <Pencil size={13} /> Edit
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${current.name}`} onClick={() => setDeleting(current)}>
                          <Trash2 size={14} />
                        </button>
                      </>
                    ) : null}
                  </>
                }
              >
                <div className="ui-stack">
                  <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <Badge tone={current.audience === "partner" ? "primary" : "info"}>{current.audience === "partner" ? "Partner portal" : "Customer portal"}</Badge>
                    {current.is_active ? <Badge tone="success">Live</Badge> : <Badge>Off</Badge>}
                    {current.settings.modules.map((m) => (
                      <Badge key={m}>{MODULE_LABEL[m] ?? titleCase(m)}</Badge>
                    ))}
                  </div>
                  <Field label="Portal address">
                    <CopyField value={current.url} label="Copy portal link" />
                  </Field>
                  <p className="ui-muted ui-small" style={{ margin: 0 }}>
                    People sign in with a one-time link sent to their email — no passwords. They only see records linked to their own contact and account.
                  </p>
                </div>
              </Card>
              <PortalUsers portal={current} onChanged={() => void portals.reload()} />
            </div>
          ) : null}
        </div>
      ) : (
        <Card>
          <EmptyState icon={<Globe size={20} />} title="No portals yet" message="Create a customer portal and invite your contacts to it." />
        </Card>
      )}
      <PortalEditor
        portal={editing}
        partnerAllowed={portals.data?.features.custom_portals ?? false}
        onClose={() => setEditing(null)}
        onSaved={(saved) => {
          setEditing(null);
          setSelected(saved.id);
          void portals.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title={`Delete ${deleting?.name ?? "portal"}?`}
        message="The portal stops working and everyone invited to it loses access. Their cases, quotes and invoices stay in AIVA."
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function PortalUsers({ portal, onChanged }: { portal: PortalRow; onChanged: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const users = useApi<PortalUserRow[]>(`/portals/${portal.id}/users`);
  const [inviting, setInviting] = useState(false);
  const [link, setLink] = useState<string | null>(null);

  async function setStatus(user: PortalUserRow, status: "active" | "disabled") {
    try {
      await api.patch(`/portals/${portal.id}/users/${user.id}`, { status });
      toast(status === "active" ? `${user.full_name} can sign in again.` : `${user.full_name} can no longer sign in.`);
      await users.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <Card
      title={`People with access (${users.data?.length ?? portal.user_count})`}
      flush
      actions={
        can("records.write") ? (
          <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => setInviting(true)}>
            <UserPlus size={13} /> Invite a contact
          </button>
        ) : null
      }
    >
      {!users.data ? (
        <LoadingRows />
      ) : users.data.length ? (
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Account</th>
                <th>Last signed in</th>
                <th>Access</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((user) => (
                <tr key={user.id}>
                  <td className="cell-title">
                    {user.full_name}
                    <div className="ui-muted ui-small">{user.email}</div>
                  </td>
                  <td>{user.account_name ?? "—"}</td>
                  <td className="ui-muted">{user.last_login_at ? relativeTime(user.last_login_at) : "Never"}</td>
                  <td>
                    {can("records.write") ? (
                      <label className="ui-switch">
                        <input type="checkbox" checked={user.status === "active"} onChange={(e) => void setStatus(user, e.target.checked ? "active" : "disabled")} aria-label={`${user.full_name} access`} />
                        <span />
                      </label>
                    ) : (
                      <Badge tone={user.status === "active" ? "success" : "neutral"}>{user.status}</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty-hint">Nobody has access yet. Invite contacts and they&apos;ll get an email with a sign-in link.</p>
      )}
      <InviteModal
        portal={portal}
        open={inviting}
        onClose={() => setInviting(false)}
        onInvited={(result) => {
          setInviting(false);
          if (result.login_url) setLink(result.login_url);
          toast(result.delivery === "smtp" ? `Invitation emailed to ${result.email}.` : `${result.email} has access.`);
          void users.reload();
          onChanged();
        }}
      />
      <Modal open={Boolean(link)} title="Sign-in link" onClose={() => setLink(null)}>
        <div className="ui-stack">
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Email delivery isn&apos;t set up on this server, so here&apos;s the one-time sign-in link to share. It expires in 30 minutes; they can always request a new one from the portal.
          </p>
          {link ? <CopyField value={link} label="Copy link" /> : null}
        </div>
      </Modal>
    </Card>
  );
}

function InviteModal({
  portal,
  open,
  onClose,
  onInvited,
}: {
  portal: PortalRow;
  open: boolean;
  onClose: () => void;
  onInvited: (result: { email: string; delivery: string; login_url?: string }) => void;
}) {
  const contacts = useRefOptions(open ? "contact" : null);
  const [contactId, setContactId] = useState("");
  const [send, setSend] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function invite() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ email: string; delivery: string; login_url?: string }>(`/portals/${portal.id}/users`, { contact_id: contactId, send_email: send });
      setContactId("");
      onInvited(result);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={`Invite to ${portal.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !contactId} onClick={() => void invite()}>
            Invite
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Contact" hint="They'll see cases, quotes and invoices for their contact record and account.">
          <select className="ui-select" value={contactId} onChange={(e) => setContactId(e.target.value)}>
            <option value="">Choose a contact…</option>
            {contacts.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </Field>
        <label className="ui-check">
          <input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} /> Email them a sign-in link now
        </label>
      </div>
    </Modal>
  );
}

function PortalEditor({
  portal,
  partnerAllowed,
  onClose,
  onSaved,
}: {
  portal: PortalRow | "new" | null;
  partnerAllowed: boolean;
  onClose: () => void;
  onSaved: (saved: PortalRow) => void;
}) {
  const open = portal !== null;
  const existing = portal && portal !== "new" ? portal : null;
  const [name, setName] = useState("");
  const [audience, setAudience] = useState<"customer" | "partner">("customer");
  const [modules, setModules] = useState<string[]>([]);
  const [accent, setAccent] = useState("#2563eb");
  const [welcome, setWelcome] = useState("");
  const [logoText, setLogoText] = useState("");
  const [supportEmail, setSupportEmail] = useState("");
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(existing?.name ?? "Customer portal");
    setAudience(existing?.audience ?? "customer");
    setModules(existing?.settings.modules ?? ["cases", "knowledge", "quotes", "invoices"]);
    setAccent(existing?.settings.accent ?? "#2563eb");
    setWelcome(existing?.settings.welcome ?? "");
    setLogoText(existing?.settings.logo_text ?? "");
    setSupportEmail(existing?.settings.support_email ?? "");
    setActive(existing?.is_active ?? true);
    setError(null);
  }, [open, existing]);

  const available = audience === "partner" ? ["deal_registration", "knowledge"] : ["cases", "knowledge", "quotes", "invoices"];

  async function save() {
    setBusy(true);
    setError(null);
    const body = {
      name: name.trim(),
      audience,
      settings: { modules: modules.filter((m) => available.includes(m)), accent, welcome, logo_text: logoText, support_email: supportEmail },
      is_active: active,
    };
    try {
      const saved = existing ? await api.patch<PortalRow>(`/portals/${existing.id}`, body) : await api.post<PortalRow>("/portals", body);
      onSaved(saved);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={existing ? `Edit ${existing.name}` : "New portal"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <label className="ui-check" style={{ marginRight: "auto" }}>
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Live
          </label>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim()} onClick={() => void save()}>
            Save portal
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Portal name" required>
            <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="For">
            <select
              className="ui-select"
              value={audience}
              disabled={Boolean(existing)}
              onChange={(e) => {
                const next = e.target.value as "customer" | "partner";
                setAudience(next);
                setModules(next === "partner" ? ["deal_registration", "knowledge"] : ["cases", "knowledge", "quotes", "invoices"]);
                if (!existing) setName(next === "partner" ? "Partner portal" : "Customer portal");
              }}
            >
              <option value="customer">Customers</option>
              <option value="partner" disabled={!partnerAllowed}>
                Partners and resellers{partnerAllowed ? "" : " — Pro"}
              </option>
            </select>
            {!partnerAllowed ? <PlanChip feature="custom_portals" /> : null}
          </Field>
        </div>
        <Field label="What people can do">
          <div className="chip-list">
            {available.map((m) => {
              const on = modules.includes(m);
              return (
                <button key={m} type="button" className={`chip${on ? " on" : ""}`} aria-pressed={on} onClick={() => setModules(on ? modules.filter((x) => x !== m) : [...modules, m])}>
                  {MODULE_LABEL[m]}
                </button>
              );
            })}
          </div>
        </Field>
        <div className="ui-form-grid">
          <Field label="Brand name shown at the top">
            <input className="ui-input" value={logoText} onChange={(e) => setLogoText(e.target.value)} placeholder="Your company name" />
          </Field>
          <Field label="Accent color">
            <div className="ui-row" style={{ gap: 8 }}>
              <input type="color" value={accent} onChange={(e) => setAccent(e.target.value)} aria-label="Accent color" style={{ width: 44, height: 38, border: 0, background: "none" }} />
              <input className="ui-input ui-mono" value={accent} onChange={(e) => setAccent(e.target.value)} />
            </div>
          </Field>
          <Field label="Support email">
            <input className="ui-input" type="email" value={supportEmail} onChange={(e) => setSupportEmail(e.target.value)} placeholder="support@example.com" />
          </Field>
        </div>
        <Field label="Welcome message">
          <textarea className="ui-textarea" rows={2} value={welcome} onChange={(e) => setWelcome(e.target.value)} placeholder="Track your support requests, quotes and invoices in one place." />
        </Field>
      </div>
    </Modal>
  );
}
