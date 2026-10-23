"use client";

import Link from "next/link";
import { Globe, Laptop, Plus, Search, Smartphone, Tablet, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";

import { CopyField } from "@/components/copy-field";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { RecordSearch } from "@/components/record-search";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";
import { seconds, type TrackingSite, type VisitSession, type Visitor } from "@/lib/workspace";

export default function VisitsPage() {
  return (
    <FeaturePage feature="website_visits">
      <Visits />
    </FeaturePage>
  );
}

interface VisitorList {
  items: Visitor[];
  summary: {
    visitors: number;
    identified: number;
    sessions: number;
    page_views: number;
    pages_per_visit: number;
    avg_seconds: number;
    top_pages: { path: string; views: number; visitors: number }[];
    top_sources: { source: string; sessions: number }[];
  };
  has_sites: boolean;
}

function snippet(site: TrackingSite) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `<script async src="${origin}/aiva-track.js" data-site="${site.site_key}"></script>`;
}

function DeviceIcon({ device }: { device: string | null }) {
  if (device === "mobile") return <Smartphone size={14} aria-label="Phone" />;
  if (device === "tablet") return <Tablet size={14} aria-label="Tablet" />;
  return <Laptop size={14} aria-label="Computer" />;
}

function Visits() {
  const toast = useToast();
  const { can } = useSession();
  const sites = useApi<{ items: TrackingSite[]; can_manage: boolean }>("/visits/sites");
  const [days, setDays] = useState("30");
  const [who, setWho] = useState<"all" | "identified" | "anonymous">("all");
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const query = new URLSearchParams({ days });
  if (who !== "all") query.set("identified", String(who === "identified"));
  if (debounced.trim()) query.set("search", debounced.trim());
  const visitors = useApi<VisitorList>(`/visits/visitors?${query}`);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<TrackingSite | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const s = visitors.data?.summary;
  const manage = Boolean(sites.data?.can_manage);
  const maxViews = Math.max(1, ...(s?.top_pages.map((p) => p.views) ?? [1]));
  const maxSessions = Math.max(1, ...(s?.top_sources.map((p) => p.sessions) ?? [1]));

  async function toggle(site: TrackingSite) {
    try {
      await api.patch(`/visits/sites/${site.id}`, { is_active: !site.is_active });
      await sites.reload();
      toast(site.is_active ? "Tracking paused." : "Tracking resumed.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const list = sites.data?.items ?? [];
  return (
    <>
      <PageHeader
        title="Website visits"
        description="See who's on your website, which pages they read and where they came from. Visitors who fill in a form are matched to their lead or contact."
        actions={
          manage ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setAdding(true)}>
              <Plus size={16} /> Add website
            </button>
          ) : null
        }
      />
      {sites.data && !list.length ? (
        <Card title="Start tracking your website" icon={<Globe size={16} />}>
          <p style={{ marginTop: 0 }}>Add your website, then paste one line of code into its pages (before &lt;/head&gt;). Visits start showing here within seconds.</p>
          {manage ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setAdding(true)}>
              <Plus size={16} /> Add website
            </button>
          ) : (
            <p className="ui-muted">Ask a marketing admin to add your website.</p>
          )}
        </Card>
      ) : null}
      {list.length ? (
        <>
          <div className="ui-toolbar">
            <Tabs
              label="Visitors"
              value={who}
              onChange={setWho}
              items={[
                { value: "all", label: "All visitors" },
                { value: "identified", label: "Known" },
                { value: "anonymous", label: "Anonymous" },
              ]}
            />
            <select className="ui-select" value={days} onChange={(e) => setDays(e.target.value)} aria-label="Period">
              <option value="1">Last 24 hours</option>
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
            </select>
            <span className="ui-spacer" />
            <div className="ui-search">
              <Search size={15} />
              <input className="ui-input sm" placeholder="Search email, name or page" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search visitors" />
            </div>
          </div>
          <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
            <Kpi label="Visitors" value={s?.visitors ?? "—"} note={s ? `${s.identified} known` : undefined} />
            <Kpi label="Visits" value={s?.sessions ?? "—"} />
            <Kpi label="Page views" value={s?.page_views ?? "—"} note={s ? `${s.pages_per_visit} per visit` : undefined} />
            <Kpi label="Average visit" value={s ? seconds(s.avg_seconds) : "—"} />
          </div>
          <div className="ui-split" style={{ marginBottom: 16 }}>
            <Card title="Visitors" flush>
              {visitors.loading && !visitors.data ? (
                <LoadingRows />
              ) : visitors.data?.items.length ? (
                <div className="ui-table-wrap">
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>Visitor</th>
                        <th className="ui-num">Visits</th>
                        <th className="ui-num">Pages</th>
                        <th className="ui-num">Time</th>
                        <th>Last page</th>
                        <th>Last seen</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visitors.data.items.map((v) => (
                        <tr key={v.id} className="clickable" onClick={() => setOpenId(v.id)}>
                          <td>
                            <div className="cell-title">
                              <DeviceIcon device={v.device} /> {v.label}
                            </div>
                            <div className="cell-sub">
                              {v.contact_id ? "Contact" : v.lead_id ? "Lead" : v.email ? "Not in AIVA yet" : "Anonymous"}
                              {v.browser ? ` · ${v.browser}` : ""}
                              {v.language ? ` · ${v.language}` : ""}
                            </div>
                          </td>
                          <td className="ui-num">{v.visit_count}</td>
                          <td className="ui-num">{v.page_view_count}</td>
                          <td className="ui-num">{seconds(v.total_seconds)}</td>
                          <td className="ui-truncate" style={{ maxWidth: 200 }}>
                            {v.last_url ? new URL(v.last_url).pathname : "—"}
                          </td>
                          <td>{relativeTime(v.last_seen_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState icon={<Globe size={20} />} title="No visits in this period" message="Make sure the tracking code is on your website's pages." />
              )}
            </Card>
            <div className="ui-stack">
              <Card title="Top pages">
                {s?.top_pages.length ? (
                  <ul className="bar-list">
                    {s.top_pages.map((p) => (
                      <li key={p.path}>
                        <span className="ui-truncate">{p.path}</span>
                        <span className="ui-muted">{p.views}</span>
                        <span className="bar">
                          <span style={{ width: `${(p.views / maxViews) * 100}%` }} />
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="ui-muted ui-small" style={{ margin: 0 }}>No page views yet.</p>
                )}
              </Card>
              <Card title="Where visits come from">
                {s?.top_sources.length ? (
                  <ul className="bar-list">
                    {s.top_sources.map((p) => (
                      <li key={p.source}>
                        <span className="ui-truncate">{p.source}</span>
                        <span className="ui-muted">{p.sessions}</span>
                        <span className="bar">
                          <span style={{ width: `${(p.sessions / maxSessions) * 100}%` }} />
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="ui-muted ui-small" style={{ margin: 0 }}>No visits yet.</p>
                )}
              </Card>
            </div>
          </div>
          <Card title="Tracked websites" icon={<Globe size={16} />}>
            <div className="ui-stack">
              {list.map((site) => (
                <div key={site.id} className="ui-stack" style={{ gap: 8 }}>
                  <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <strong>{site.name}</strong>
                    <span className="ui-muted">{site.domain}</span>
                    <Badge tone={site.is_active ? "success" : "neutral"}>{site.is_active ? "Tracking" : "Paused"}</Badge>
                    <span className="ui-muted ui-small">{site.last_hit_at ? `Last visit ${relativeTime(site.last_hit_at)}` : "No visits yet"} · {site.visitors_30d} visitors in 30 days</span>
                    <span className="ui-spacer" />
                    {manage ? (
                      <>
                        <button type="button" className="ui-btn ui-btn-sm" onClick={() => void toggle(site)}>
                          {site.is_active ? "Pause" : "Resume"}
                        </button>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Remove ${site.name}`} onClick={() => setRemoving(site)}>
                          <Trash2 size={14} />
                        </button>
                      </>
                    ) : null}
                  </div>
                  <CopyField value={snippet(site)} label="Copy tracking code" />
                </div>
              ))}
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Paste the code before &lt;/head&gt; on every page of the site (or in your tag manager). Forms with an email field identify the visitor automatically; you can also call <code>aiva.identify(email, name)</code>. Browsers with Do Not Track on aren&apos;t tracked.
              </p>
            </div>
          </Card>
        </>
      ) : sites.loading ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : null}
      <AddSite open={adding} onClose={() => setAdding(false)} onAdded={() => (setAdding(false), void sites.reload(), toast("Website added. Paste the code into your site."))} />
      <ConfirmDialog
        open={removing !== null}
        title={`Remove ${removing?.name ?? "website"}?`}
        message="Tracking stops and every visit recorded for this website is deleted."
        confirmLabel="Remove"
        onConfirm={() =>
          removing &&
          void api
            .del(`/visits/sites/${removing.id}`)
            .then(() => (setRemoving(null), sites.reload(), visitors.reload(), toast("Website removed.")))
            .catch((caught) => toast(errorMessage(caught), "danger"))
        }
        onClose={() => setRemoving(null)}
      />
      <VisitorDetail id={openId} canWrite={can("records.write")} onClose={() => setOpenId(null)} onChanged={() => void visitors.reload()} />
    </>
  );
}

function AddSite({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: () => void }) {
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function add() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/visits/sites", { name: name.trim() || domain.trim(), domain: domain.trim() });
      setName("");
      setDomain("");
      onAdded();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open={open}
      size="sm"
      title="Add a website"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void add()} disabled={busy || !domain.trim()}>
            Add
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-stack">
        <Field label="Domain" required hint="Visits from this domain and its subdomains are recorded.">
          <input className="ui-input" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="example.com" />
        </Field>
        <Field label="Name">
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Marketing site" />
        </Field>
      </div>
    </Modal>
  );
}

function VisitorDetail({ id, canWrite, onClose, onChanged }: { id: string | null; canWrite: boolean; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const detail = useApi<Visitor & { sessions: VisitSession[] }>(id ? `/visits/visitors/${id}` : null);
  const [linking, setLinking] = useState(false);
  const v = id ? detail.data : null;

  async function link(type: string, recordId: string) {
    try {
      await api.post(`/visits/visitors/${id}/link`, type === "contact" ? { contact_id: recordId } : { lead_id: recordId });
      setLinking(false);
      await detail.reload();
      onChanged();
      toast("Linked.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function createLead() {
    try {
      const result = await api.post<{ lead_id: string }>(`/visits/visitors/${id}/create-lead`);
      await detail.reload();
      onChanged();
      toast("Lead created.");
      window.location.assign(`/leads/${result.lead_id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <Modal open={Boolean(id)} size="lg" title={v?.label ?? "Visitor"} description={v ? `First seen ${dateTime(v.first_seen_at)} · last seen ${relativeTime(v.last_seen_at)}` : undefined} onClose={onClose}>
      {!v ? (
        <LoadingRows rows={5} />
      ) : (
        <div className="ui-stack">
          <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
            {v.contact_id ? (
              <Link className="ui-btn ui-btn-sm" href={`/contacts/${v.contact_id}`}>
                Contact: {v.contact_name}
              </Link>
            ) : v.lead_id ? (
              <Link className="ui-btn ui-btn-sm" href={`/leads/${v.lead_id}`}>
                Lead: {v.lead_name}
              </Link>
            ) : canWrite ? (
              <>
                {v.email ? (
                  <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => void createLead()}>
                    <UserPlus size={14} /> Create lead
                  </button>
                ) : null}
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => setLinking(!linking)}>
                  Link to a lead or contact
                </button>
              </>
            ) : null}
            {v.email ? <span className="ui-muted ui-small">{v.email}</span> : null}
          </div>
          {linking ? <RecordSearch types={["contact", "lead"]} placeholder="Search contacts and leads" onPick={(r) => void link(r.type, r.id)} /> : null}
          <div className="visit-stats">
            <span>
              <strong>{v.visit_count}</strong> visits
            </span>
            <span>
              <strong>{v.page_view_count}</strong> pages
            </span>
            <span>
              <strong>{seconds(v.total_seconds)}</strong> on site
            </span>
          </div>
          <dl className="ui-dl">
            <dt>Device</dt>
            <dd>
              {v.device ?? "—"}
              {v.browser ? ` · ${v.browser}` : ""}
            </dd>
            <dt>Language</dt>
            <dd>{v.language ?? "—"}</dd>
            <dt>Time zone</dt>
            <dd>{v.timezone ?? "—"}</dd>
            <dt>First came from</dt>
            <dd className="ui-truncate">{v.first_referrer ?? "Direct"}</dd>
          </dl>
          <div>
            <div className="ui-label" style={{ marginBottom: 8 }}>
              Visits
            </div>
            <div className="ui-stack" style={{ gap: 12 }}>
              {v.sessions.map((session) => (
                <div key={session.id}>
                  <div className="ui-row" style={{ gap: 8 }}>
                    <strong style={{ fontSize: 13.5 }}>{dateTime(session.started_at)}</strong>
                    <span className="ui-muted ui-small">
                      from {session.source}
                      {session.utm_campaign ? ` · ${session.utm_campaign}` : ""} · {seconds(session.duration_seconds)}
                    </span>
                  </div>
                  <ol className="timeline-pages">
                    {session.pages.map((p, index) => (
                      <li key={index}>
                        <span className="ui-truncate">{p.title || p.path}</span>
                        <span className="ui-muted">{seconds(p.seconds)}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
