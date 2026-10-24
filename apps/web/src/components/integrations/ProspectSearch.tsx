"use client";

import Link from "next/link";
import { Building2, ChevronLeft, ChevronRight, Download, Search, ShieldOff, UserRound } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { TagsInput, useMembers } from "@/components/settings-kit";
import { Alert, Badge, EmptyState, Field, LoadingRows, Tabs } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

/** Prospect sources (apps/api/app/integrations/providers/sales_intel.py). */
export type ProspectProviderKey = "apollo" | "zoominfo";
type Kind = "people" | "companies";
type ImportAs = "lead" | "contact" | "account";

type Providers = {
  items: { key: ProspectProviderKey; name: string; enabled: boolean; connected: boolean }[];
  can_import: boolean;
  can_assign_owner: boolean;
  max_import: number;
  seniorities: string[];
  employee_ranges: string[];
};

export type Prospect = {
  id: string;
  name: string;
  first_name?: string;
  last_name?: string;
  title?: string | null;
  seniority?: string | null;
  company?: string | null;
  domain?: string | null;
  website?: string | null;
  industry?: string | null;
  employees?: number | null;
  linkedin?: string | null;
  location?: string | null;
  email?: string | null;
  has_email?: boolean;
  in_aiva: boolean;
  record: { type: "lead" | "contact" | "account"; id: string } | null;
  suppressed: boolean;
};

type Results = { items: Prospect[]; page: number; per_page: number; total: number };

export type ProspectImportResult = {
  created: { id: string; type: ImportAs; name: string; href: string | null }[];
  skipped: { name: string; reason: string }[];
  credits: number;
};

type Filters = {
  keywords: string;
  titles: string[];
  seniorities: string[];
  locations: string[];
  company_locations: string[];
  industries: string[];
  employee_ranges: string[];
  company_name: string;
  domains: string[];
};

const EMPTY: Filters = { keywords: "", titles: [], seniorities: [], locations: [], company_locations: [], industries: [], employee_ranges: [], company_name: "", domains: [] };

const SENIORITY_LABEL: Record<string, string> = {
  owner: "Owner",
  founder: "Founder",
  c_suite: "C-suite",
  partner: "Partner",
  vp: "VP",
  head: "Head of",
  director: "Director",
  manager: "Manager",
  senior: "Senior",
  entry: "Entry level",
  intern: "Intern",
};

const VENDOR_NOTE: Record<ProspectProviderKey, string> = {
  apollo: "Searching Apollo is free. Importing looks each person up in Apollo for their email and phone, which uses Apollo credits.",
  zoominfo: "Searching ZoomInfo is free. Importing enriches each person with ZoomInfo, which uses ZoomInfo credits. Locations filter by country.",
};

function rangeLabel(range: string) {
  const [low, high] = range.split(",").map((n) => Number(n));
  if (high >= 1_000_000) return `${low.toLocaleString()}+`;
  return `${low.toLocaleString()}–${high.toLocaleString()}`;
}

const RECORD_PATH: Record<ImportAs, string> = { lead: "/leads", contact: "/contacts", account: "/accounts" };

/**
 * Search Apollo.io or ZoomInfo for people and companies and import the ones
 * you pick as leads, contacts or accounts. Skips anyone already in AIVA or on
 * the do-not-contact list; one AIVA credit per record imported.
 *
 *   <ProspectSearch />                       picks among the connected sources
 *   <ProspectSearch provider="apollo" />     locked to one source (its integration page)
 *
 * Needs the waterfall_enrichment plan feature; renders a short note when no
 * source is connected.
 */
export function ProspectSearch({ provider: locked, onImported }: { provider?: ProspectProviderKey; onImported?: (result: ProspectImportResult) => void }) {
  const toast = useToast();
  const meta = useApi<Providers>("/prospects/providers");
  const { members } = useMembers();
  const connected = useMemo(() => (meta.data?.items ?? []).filter((p) => p.enabled && p.connected), [meta.data]);
  const [chosen, setChosen] = useState<ProspectProviderKey | null>(locked ?? null);
  const provider = locked ?? chosen ?? connected[0]?.key ?? null;
  const [kind, setKind] = useState<Kind>("people");
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [results, setResults] = useState<Results | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, Prospect>>({});
  const [importAs, setImportAs] = useState<ImportAs>("lead");
  const [owner, setOwner] = useState("");
  const [source, setSource] = useState("");
  const [importing, setImporting] = useState(false);
  const [outcome, setOutcome] = useState<ProspectImportResult | null>(null);

  // A new search kind or source starts over.
  useEffect(() => {
    setResults(null);
    setSelected({});
    setOutcome(null);
    setSearchError(null);
    setImportAs(kind === "companies" ? "account" : "lead");
  }, [kind, provider]);

  if (meta.error) return <Alert tone="warning">{meta.error}</Alert>;
  if (!meta.data) return <LoadingRows rows={4} />;
  const info = meta.data;
  const sourceInfo = info.items.find((p) => p.key === provider);
  if (!provider || !sourceInfo?.connected || !sourceInfo.enabled) {
    return (
      <EmptyState
        icon={<Search size={20} />}
        title={locked ? `Connect ${sourceInfo?.name ?? "this source"} to search it` : "No prospect source connected"}
        message={
          locked ? (
            "Save working credentials above, then search here."
          ) : (
            <>
              Connect <Link href="/integrations/apollo">Apollo.io</Link> or <Link href="/integrations/zoominfo">ZoomInfo</Link> in Integrations to search for people and companies.
            </>
          )
        }
      />
    );
  }

  const set = <K extends keyof Filters>(name: K, value: Filters[K]) => setFilters((current) => ({ ...current, [name]: value }));
  const toggleIn = (name: "seniorities" | "employee_ranges", value: string) =>
    set(name, filters[name].includes(value) ? filters[name].filter((v) => v !== value) : [...filters[name], value]);
  const selectedList = Object.values(selected);
  const selectable = (results?.items ?? []).filter((p) => !p.in_aiva && !p.suppressed);
  const allOnPage = selectable.length > 0 && selectable.every((p) => selected[p.id]);

  async function search(page = 1, keepOutcome = false) {
    setSearching(true);
    setSearchError(null);
    if (!keepOutcome) setOutcome(null);
    try {
      const body = {
        kind,
        page,
        keywords: filters.keywords.trim() || null,
        titles: kind === "people" ? filters.titles : [],
        seniorities: kind === "people" ? filters.seniorities : [],
        locations: filters.locations,
        company_locations: kind === "people" ? filters.company_locations : [],
        industries: kind === "companies" ? filters.industries : [],
        employee_ranges: filters.employee_ranges,
        company_name: kind === "companies" ? filters.company_name.trim() || null : null,
        domains: kind === "people" ? filters.domains : [],
      };
      setResults(await api.post<Results>(`/prospects/${provider}/search`, body));
    } catch (caught) {
      setSearchError(errorMessage(caught));
    } finally {
      setSearching(false);
    }
  }

  function toggle(person: Prospect) {
    setSelected((current) => {
      const next = { ...current };
      if (next[person.id]) delete next[person.id];
      else if (Object.keys(next).length < info.max_import) next[person.id] = person;
      return next;
    });
  }

  function toggleAll() {
    setSelected((current) => {
      const next = { ...current };
      if (allOnPage) selectable.forEach((p) => delete next[p.id]);
      else for (const p of selectable) if (Object.keys(next).length < info.max_import) next[p.id] = p;
      return next;
    });
  }

  async function importSelected() {
    setImporting(true);
    try {
      const result = await api.post<ProspectImportResult>("/prospects/import", {
        provider,
        kind,
        as: importAs,
        owner_user_id: owner || null,
        lead_source: source.trim() || null,
        items: selectedList.map((p) => ({
          id: p.id,
          name: p.name?.slice(0, 300) || null,
          first_name: p.first_name?.slice(0, 120) || null,
          last_name: p.last_name?.slice(0, 120) || null,
          title: p.title?.slice(0, 200) || null,
          company: p.company?.slice(0, 250) || null,
          domain: p.domain?.slice(0, 250) || null,
          website: p.website?.slice(0, 500) || null,
          industry: p.industry?.slice(0, 150) || null,
          employees: typeof p.employees === "number" ? p.employees : null,
          linkedin: p.linkedin?.slice(0, 500) || null,
          location: p.location?.slice(0, 300) || null,
        })),
      });
      setOutcome(result);
      setSelected({});
      // Read the page again so what was just imported shows as in AIVA (searching is free).
      if (results && result.created.length) void search(results.page, true);
      toast(result.created.length ? `Imported ${result.created.length} ${result.created.length === 1 ? importAs : `${importAs}s`}.` : "Nothing new to import.");
      onImported?.(result);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="isal-prospects">
      <div className="isal-prospects-top">
        <Tabs
          label="What to search for"
          value={kind}
          onChange={setKind}
          items={[
            { value: "people", label: "People" },
            { value: "companies", label: "Companies" },
          ]}
        />
        {!locked && connected.length > 1 ? (
          <label className="isal-source-pick">
            <span className="ui-small ui-muted">Search</span>
            <select className="ui-select sm" value={provider} onChange={(event) => setChosen(event.target.value as ProspectProviderKey)}>
              {connected.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      <form
        className="isal-filters"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          void search(1);
        }}
      >
        <div className="ui-form-grid">
          {kind === "people" ? (
            <>
              <Field label="Job titles">
                <TagsInput value={filters.titles} onChange={(v) => set("titles", v.slice(0, 10))} placeholder="Head of sales, CFO" label="Job titles" />
              </Field>
              <Field label="Where they are">
                <TagsInput value={filters.locations} onChange={(v) => set("locations", v.slice(0, 10))} placeholder="United States, Berlin" label="Person locations" />
              </Field>
              <Field label="Company websites" hint="Only people at these companies.">
                <TagsInput value={filters.domains} onChange={(v) => set("domains", v.slice(0, 20))} placeholder="acme.com" label="Company domains" transform={(v) => v.toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0]} />
              </Field>
              <Field label="Company headquarters">
                <TagsInput value={filters.company_locations} onChange={(v) => set("company_locations", v.slice(0, 10))} placeholder="Canada" label="Company locations" />
              </Field>
            </>
          ) : (
            <>
              <Field label="Company name">
                <input className="ui-input" value={filters.company_name} maxLength={200} onChange={(event) => set("company_name", event.target.value)} placeholder="Northwind" />
              </Field>
              <Field label="Industries">
                <TagsInput value={filters.industries} onChange={(v) => set("industries", v.slice(0, 10))} placeholder="software, logistics" label="Industries" />
              </Field>
              <Field label="Locations">
                <TagsInput value={filters.locations} onChange={(v) => set("locations", v.slice(0, 10))} placeholder="United Kingdom" label="Company locations" />
              </Field>
            </>
          )}
          <Field label="Keywords">
            <input className="ui-input" value={filters.keywords} maxLength={200} onChange={(event) => set("keywords", event.target.value)} placeholder={kind === "people" ? "revops, salesforce" : "fleet management"} />
          </Field>
        </div>
        {kind === "people" ? (
          <div className="isal-chips" role="group" aria-label="Seniority">
            <span className="isal-chips-label">Seniority</span>
            {info.seniorities.map((s) => (
              <button key={s} type="button" className={`isal-chip${filters.seniorities.includes(s) ? " on" : ""}`} aria-pressed={filters.seniorities.includes(s)} onClick={() => toggleIn("seniorities", s)}>
                {SENIORITY_LABEL[s] ?? s}
              </button>
            ))}
          </div>
        ) : null}
        <div className="isal-chips" role="group" aria-label="Company size">
          <span className="isal-chips-label">Employees</span>
          {info.employee_ranges.map((r) => (
            <button key={r} type="button" className={`isal-chip${filters.employee_ranges.includes(r) ? " on" : ""}`} aria-pressed={filters.employee_ranges.includes(r)} onClick={() => toggleIn("employee_ranges", r)}>
              {rangeLabel(r)}
            </button>
          ))}
        </div>
        <div className="isal-filter-actions">
          <span className="ui-muted ui-small">{VENDOR_NOTE[provider]}</span>
          <div className="ui-row" style={{ gap: 8 }}>
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setFilters(EMPTY)} disabled={searching}>
              Clear
            </button>
            <button type="submit" className="ui-btn ui-btn-primary ui-btn-sm" disabled={searching}>
              {searching ? <span className="ui-spinner" /> : <Search size={14} />} Search {sourceInfo.name}
            </button>
          </div>
        </div>
      </form>

      {searchError ? <Alert>{searchError}</Alert> : null}

      {outcome ? (
        <div className="isal-outcome" role="status">
          <strong>
            {outcome.created.length ? `Imported ${outcome.created.length}` : "Nothing imported"}
            {outcome.credits ? ` · ${outcome.credits} credit${outcome.credits === 1 ? "" : "s"} used` : ""}
          </strong>
          {outcome.created.length ? (
            <ul className="isal-outcome-list">
              {outcome.created.slice(0, 12).map((c) => (
                <li key={c.id}>
                  <Link href={c.href ?? `${RECORD_PATH[c.type]}/${c.id}`}>{c.name}</Link>
                </li>
              ))}
              {outcome.created.length > 12 ? <li className="ui-muted">and {outcome.created.length - 12} more</li> : null}
            </ul>
          ) : null}
          {outcome.skipped.length ? (
            <details className="isal-skipped">
              <summary>
                {outcome.skipped.length} skipped
              </summary>
              <ul>
                {outcome.skipped.map((s, index) => (
                  <li key={`${s.name}-${index}`}>
                    <strong>{s.name}</strong> <span className="ui-muted">— {s.reason}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}

      {results ? (
        results.items.length ? (
          <>
            <div className="ui-table-wrap isal-results">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input type="checkbox" aria-label="Select everyone on this page" checked={allOnPage} disabled={!selectable.length || !info.can_import} onChange={toggleAll} />
                    </th>
                    <th>{kind === "people" ? "Person" : "Company"}</th>
                    <th>{kind === "people" ? "Company" : "Industry"}</th>
                    <th>Location</th>
                    <th aria-label="Status" />
                  </tr>
                </thead>
                <tbody>
                  {results.items.map((p) => {
                    const blocked = p.in_aiva || p.suppressed;
                    return (
                      <tr key={p.id} className={selected[p.id] ? "isal-row-on" : undefined}>
                        <td>
                          <input type="checkbox" aria-label={`Select ${p.name}`} checked={Boolean(selected[p.id])} disabled={blocked || !info.can_import} onChange={() => toggle(p)} />
                        </td>
                        <td>
                          <div className="isal-who">
                            {kind === "people" ? <UserRound size={15} aria-hidden /> : <Building2 size={15} aria-hidden />}
                            <span>
                              <strong>{p.name || "Unnamed"}</strong>
                              <span className="ui-muted ui-small">{kind === "people" ? p.title ?? "" : p.domain ?? ""}</span>
                            </span>
                          </div>
                        </td>
                        <td className="ui-small">
                          {kind === "people" ? (
                            <>
                              {p.company ?? "—"}
                              {p.domain ? <span className="ui-muted"> · {p.domain}</span> : null}
                            </>
                          ) : (
                            <>
                              {p.industry ?? "—"}
                              {typeof p.employees === "number" ? <span className="ui-muted"> · {p.employees.toLocaleString()} people</span> : null}
                            </>
                          )}
                        </td>
                        <td className="ui-small ui-muted">{p.location || "—"}</td>
                        <td style={{ textAlign: "right" }}>
                          {p.suppressed ? (
                            <Badge tone="danger">
                              <ShieldOff size={11} aria-hidden /> Do not contact
                            </Badge>
                          ) : p.record ? (
                            <Link className="ui-small" href={`${RECORD_PATH[p.record.type]}/${p.record.id}`}>
                              In AIVA
                            </Link>
                          ) : p.in_aiva ? (
                            <Badge>In AIVA</Badge>
                          ) : kind === "people" && p.has_email ? (
                            <Badge tone="success">Email available</Badge>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="isal-pager">
              <span className="ui-muted ui-small">
                {(results.page - 1) * results.per_page + 1}–{(results.page - 1) * results.per_page + results.items.length} of {results.total.toLocaleString()}
              </span>
              <div className="ui-row" style={{ gap: 6 }}>
                <button type="button" className="ui-btn ui-btn-sm" disabled={searching || results.page <= 1} onClick={() => void search(results.page - 1)} aria-label="Previous page">
                  <ChevronLeft size={14} />
                </button>
                <button
                  type="button"
                  className="ui-btn ui-btn-sm"
                  disabled={searching || results.page >= 100 || results.page * results.per_page >= results.total}
                  onClick={() => void search(results.page + 1)}
                  aria-label="Next page"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          </>
        ) : (
          <EmptyState icon={<Search size={20} />} title="No matches" message="Try fewer filters or broader titles." />
        )
      ) : null}

      {info.can_import && selectedList.length ? (
        <div className="isal-import-bar">
          <strong>{selectedList.length} selected</strong>
          {selectedList.length >= info.max_import ? <span className="ui-muted ui-small">(up to {info.max_import} at a time)</span> : null}
          {kind === "people" ? (
            <select className="ui-select sm" value={importAs} onChange={(event) => setImportAs(event.target.value as ImportAs)} aria-label="Import as">
              <option value="lead">as leads</option>
              <option value="contact">as contacts (with their account)</option>
            </select>
          ) : (
            <span className="ui-small">as accounts</span>
          )}
          {info.can_assign_owner ? (
            <select className="ui-select sm" value={owner} onChange={(event) => setOwner(event.target.value)} aria-label="Owner">
              <option value="">Default owner</option>
              {members
                .filter((m) => m.is_active)
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.full_name}
                  </option>
                ))}
            </select>
          ) : null}
          {importAs === "lead" ? (
            <input className="ui-input sm" value={source} maxLength={80} onChange={(event) => setSource(event.target.value)} placeholder={`Lead source: ${provider}`} aria-label="Lead source" style={{ width: 170 }} />
          ) : null}
          <span className="isal-import-spacer" />
          <span className="ui-muted ui-small">1 credit each</span>
          <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" disabled={importing} onClick={() => void importSelected()}>
            {importing ? <span className="ui-spinner" /> : <Download size={14} />} Import
          </button>
        </div>
      ) : null}
    </div>
  );
}
