"use client";

import { AlertTriangle, Check, Globe, Pencil, Plus, RefreshCw, Star, Trash2, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type DnsCheck = { status: "pass" | "warn" | "fail"; found: string[] | string | null; detail: string; fix: string | null };

type Domain = {
  id: string;
  domain: string;
  status: "verified" | "issues" | "pending";
  dkim_selector: string | null;
  tracking_host: string | null;
  checks: Partial<Record<"spf" | "dkim" | "dmarc" | "mx" | "tracking", DnsCheck>> & { error?: string };
  last_checked_at: string | null;
  is_default: boolean;
};

type DomainList = { items: Domain[]; suggested: string[]; app_host: string | null; can_edit: boolean };

const STATUS: Record<Domain["status"], { label: string; tone: Tone }> = {
  verified: { label: "Verified", tone: "success" },
  issues: { label: "Needs attention", tone: "warning" },
  pending: { label: "Not checked yet", tone: "neutral" },
};

const CHECKS: { key: "spf" | "dkim" | "dmarc" | "mx" | "tracking"; label: string; about: string }[] = [
  { key: "spf", label: "SPF", about: "Lists the servers allowed to send email for this domain." },
  { key: "dkim", label: "DKIM", about: "Signs each email so receivers know it wasn't changed." },
  { key: "dmarc", label: "DMARC", about: "Tells receivers what to do with email that fails SPF or DKIM." },
  { key: "mx", label: "MX", about: "Where replies to this domain are delivered." },
  { key: "tracking", label: "Tracking domain", about: "Your own address for open and click tracking." },
];

export default function DomainsPage() {
  return (
    <SettingsShell>
      <Domains />
    </SettingsShell>
  );
}

function Domains() {
  const toast = useToast();
  const domains = useApi<DomainList>("/outbound/domains");
  const [editing, setEditing] = useState<Domain | "new" | null>(null);
  const [prefill, setPrefill] = useState("");
  const [deleting, setDeleting] = useState<Domain | null>(null);
  const [checking, setChecking] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = domains.data;
  const canEdit = data?.can_edit ?? false;

  async function recheck(domain: Domain) {
    setChecking(domain.id);
    try {
      const result = await api.post<Domain>(`/outbound/domains/${domain.id}/check`);
      toast(result.status === "verified" ? `${result.domain} looks good.` : `Checked ${result.domain}.`);
      await domains.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setChecking(null);
    }
  }

  async function makeDefault(domain: Domain) {
    try {
      await api.patch(`/outbound/domains/${domain.id}`, {
        domain: domain.domain,
        dkim_selector: domain.dkim_selector,
        tracking_host: domain.tracking_host,
        is_default: true,
      });
      toast(`${domain.domain} is now the default.`);
      await domains.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/outbound/domains/${deleting.id}`);
      toast("Domain removed.");
      setDeleting(null);
      await domains.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Domains"
        description="AIVA checks the DNS records that decide whether your emails reach the inbox: SPF, DKIM, DMARC and MX. Fix anything flagged with your domain provider, then check again."
        actions={
          canEdit ? (
            <button
              type="button"
              className="ui-btn ui-btn-primary"
              onClick={() => {
                setPrefill("");
                setEditing("new");
              }}
            >
              <Plus size={16} /> Add domain
            </button>
          ) : null
        }
      />
      {domains.error ? <Alert>{domains.error}</Alert> : null}
      {data?.suggested.length && canEdit ? (
        <Alert tone="info">
          Your team sends from{" "}
          {data.suggested.map((name, index) => (
            <span key={name}>
              {index ? ", " : ""}
              <button
                type="button"
                className="outbound-linkbtn"
                onClick={() => {
                  setPrefill(name);
                  setEditing("new");
                }}
              >
                {name}
              </button>
            </span>
          ))}
          , which {data.suggested.length === 1 ? "isn't" : "aren't"} checked yet. Add {data.suggested.length === 1 ? "it" : "them"} so AIVA can watch the DNS
          records.
        </Alert>
      ) : null}

      {!data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : !data.items.length ? (
        <div className="ui-card">
          <EmptyState
            icon={<Globe size={20} />}
            title="No sending domains yet"
            message="Add the domain in your team's email addresses (the part after @). AIVA will check it straight away and every day after."
            action={
              canEdit ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
                  <Plus size={16} /> Add domain
                </button>
              ) : null
            }
          />
        </div>
      ) : (
        <div className="ui-stack">
          {data.items.map((domain) => (
            <Card
              key={domain.id}
              title={
                <span className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
                  <span>{domain.domain}</span>
                  <Badge tone={STATUS[domain.status]?.tone ?? "neutral"} dot>
                    {STATUS[domain.status]?.label ?? domain.status}
                  </Badge>
                  {domain.is_default ? <Badge tone="info">Default</Badge> : null}
                </span>
              }
              actions={
                <>
                  <button type="button" className="ui-btn ui-btn-sm" disabled={checking === domain.id} onClick={() => void recheck(domain)}>
                    {checking === domain.id ? <span className="ui-spinner" /> : <RefreshCw size={14} />} Check again
                  </button>
                  {canEdit ? (
                    <>
                      {!domain.is_default ? (
                        <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => void makeDefault(domain)}>
                          <Star size={14} /> Make default
                        </button>
                      ) : null}
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${domain.domain}`} onClick={() => setEditing(domain)}>
                        <Pencil size={14} />
                      </button>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Remove ${domain.domain}`} onClick={() => setDeleting(domain)}>
                        <Trash2 size={14} />
                      </button>
                    </>
                  ) : null}
                </>
              }
              footer={
                <span className="ui-muted ui-small">
                  {domain.last_checked_at ? `Checked ${relativeTime(domain.last_checked_at)}` : "Not checked yet"}
                  {domain.dkim_selector ? ` · DKIM selector “${domain.dkim_selector}”` : ""}
                </span>
              }
            >
              {domain.checks.error ? (
                <Alert tone="warning">{domain.checks.error} Try again in a minute.</Alert>
              ) : (
                <div className="settings-checks">
                  {CHECKS.filter((item) => domain.checks[item.key]).map((item) => (
                    <CheckLine key={item.key} label={item.label} about={item.about} check={domain.checks[item.key] as DnsCheck} />
                  ))}
                  {!domain.tracking_host ? (
                    <div className="settings-check">
                      <span className="settings-check-icon info" aria-hidden>
                        <Globe size={13} />
                      </span>
                      <div>
                        <strong>Tracking domain</strong>
                        <p>
                          Optional. Tracked links use AIVA&apos;s address unless you add your own, like track.{domain.domain}
                          {canEdit ? (
                            <>
                              {" "}
                              (
                              <button type="button" className="outbound-linkbtn" onClick={() => setEditing(domain)}>
                                add one
                              </button>
                              )
                            </>
                          ) : null}
                          .
                        </p>
                      </div>
                    </div>
                  ) : null}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      <DomainModal
        domain={editing}
        prefill={prefill}
        appHost={data?.app_host ?? null}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await domains.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Remove domain?"
        message={`AIVA will stop checking ${deleting?.domain ?? "this domain"}. Emails keep sending; nothing changes in your DNS.`}
        confirmLabel="Remove"
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function CheckLine({ label, about, check }: { label: string; about: string; check: DnsCheck }) {
  const found = Array.isArray(check.found) ? check.found : check.found ? [check.found] : [];
  const Icon = check.status === "pass" ? Check : check.status === "warn" ? AlertTriangle : X;
  return (
    <div className="settings-check">
      <span className={`settings-check-icon ${check.status}`} aria-label={check.status === "pass" ? "Passing" : check.status === "warn" ? "Warning" : "Failing"}>
        <Icon size={13} />
      </span>
      <div style={{ minWidth: 0 }}>
        <strong>{label}</strong> <span className="ui-muted ui-small">· {about}</span>
        <p>{check.detail}</p>
        {found.length ? <code>{found.join("\n")}</code> : null}
        {check.fix ? (
          <p>
            <strong>How to fix:</strong> {check.fix}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function DomainModal({
  domain,
  prefill,
  appHost,
  onClose,
  onSaved,
}: {
  domain: Domain | "new" | null;
  prefill: string;
  appHost: string | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [selector, setSelector] = useState("");
  const [tracking, setTracking] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const existing = domain && domain !== "new" ? domain : null;

  useEffect(() => {
    if (!domain) return;
    setName(existing?.domain ?? prefill);
    setSelector(existing?.dkim_selector ?? "");
    setTracking(existing?.tracking_host ?? "");
    setIsDefault(existing?.is_default ?? false);
    setError(null);
  }, [domain, existing, prefill]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { domain: existing?.domain ?? name.trim(), dkim_selector: selector.trim() || null, tracking_host: tracking.trim() || null, is_default: isDefault };
    try {
      const result = existing ? await api.patch<Domain>(`/outbound/domains/${existing.id}`, payload) : await api.post<Domain>("/outbound/domains", payload);
      toast(result.status === "verified" ? `${result.domain} is set up correctly.` : `${result.domain} saved. Some records need attention.`);
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  const base = (existing?.domain ?? name.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "")) || "yourcompany.com";

  return (
    <Modal
      open={Boolean(domain)}
      title={existing ? `Edit ${existing.domain}` : "Add a sending domain"}
      description={existing ? undefined : "AIVA looks up the domain's DNS records as soon as you save."}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="domain-form" className="ui-btn ui-btn-primary" disabled={saving || (!existing && name.trim().length < 3)}>
            {saving ? <span className="ui-spinner" /> : null} {existing ? "Save and check" : "Add and check"}
          </button>
        </>
      }
    >
      <form id="domain-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        {!existing ? (
          <Field label="Domain" required hint="The part after @ in your team's email addresses.">
            <input className="ui-input" value={name} autoFocus onChange={(event) => setName(event.target.value)} placeholder="yourcompany.com" autoCapitalize="none" />
          </Field>
        ) : null}
        <Field label="DKIM selector" hint="Leave blank and AIVA tries the usual ones (google, selector1, selector2 and more). Your email provider shows the selector in its DKIM settings.">
          <input className="ui-input" value={selector} maxLength={63} onChange={(event) => setSelector(event.target.value.replace(/[^A-Za-z0-9._-]/g, ""))} placeholder="google" autoCapitalize="none" />
        </Field>
        <Field
          label="Tracking domain"
          hint={
            <>
              Optional. A subdomain such as track.{base}. Add a CNAME record pointing it to <span className="ui-mono">{appHost ?? "your AIVA address"}</span>.
            </>
          }
        >
          <input className="ui-input" value={tracking} onChange={(event) => setTracking(event.target.value)} placeholder={`track.${base}`} autoCapitalize="none" />
        </Field>
        <label className="ui-check">
          <input type="checkbox" checked={isDefault} onChange={(event) => setIsDefault(event.target.checked)} /> Use as the default sending domain
        </label>
      </form>
    </Modal>
  );
}
