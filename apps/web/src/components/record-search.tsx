"use client";

import { Search } from "lucide-react";
import { useEffect, useState } from "react";

import type { SearchGroup } from "@/lib/platform-types";
import { api, useDebounced } from "@/lib/use-api";

export interface FoundRecord {
  type: string;
  id: string;
  title: string;
  subtitle?: string | null;
  href: string;
}

const TYPES: Record<string, string> = {
  account: "Accounts",
  contact: "Contacts",
  lead: "Leads",
  opportunity: "Deals",
  case: "Cases",
  quote: "Quotes",
  sales_order: "Sales orders",
  purchase_order: "Purchase orders",
  vendor: "Vendors",
  project: "Projects",
};

/** Find any CRM record by name (uses the global search). */
export function RecordSearch({ onPick, types = Object.keys(TYPES), placeholder = "Search accounts, deals, contacts…" }: { onPick: (record: FoundRecord) => void; types?: string[]; placeholder?: string }) {
  const [term, setTerm] = useState("");
  const debounced = useDebounced(term);
  const [results, setResults] = useState<FoundRecord[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const q = debounced.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    let active = true;
    setLoading(true);
    api
      .get<{ groups: SearchGroup[] }>(`/search?q=${encodeURIComponent(q)}&limit=6`)
      .then((data) => {
        if (!active) return;
        setResults(
          data.groups
            .filter((g) => types.includes(g.type))
            .flatMap((g) => g.items.map((item) => ({ type: g.type, id: item.id, title: item.title, subtitle: item.subtitle, href: item.href })))
        );
      })
      .catch(() => active && setResults([]))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  return (
    <div className="ui-stack" style={{ gap: 8 }}>
      <div className="ui-search" style={{ flex: "1 1 auto" }}>
        <Search size={15} />
        <input className="ui-input" value={term} onChange={(e) => setTerm(e.target.value)} placeholder={placeholder} aria-label="Search records" autoFocus />
      </div>
      {loading ? <span className="ui-muted ui-small">Searching…</span> : null}
      {results.length ? (
        <ul className="mini-list" style={{ maxHeight: 260, overflowY: "auto" }}>
          {results.map((r) => (
            <li key={`${r.type}-${r.id}`}>
              <div className="mini-main">
                <span className="mini-title">{r.title}</span>
                <span className="mini-sub">
                  {TYPES[r.type] ?? r.type}
                  {r.subtitle ? ` · ${r.subtitle}` : ""}
                </span>
              </div>
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => onPick(r)}>
                Link
              </button>
            </li>
          ))}
        </ul>
      ) : debounced.trim().length >= 2 && !loading ? (
        <span className="ui-muted ui-small">No matching records.</span>
      ) : null}
    </div>
  );
}
