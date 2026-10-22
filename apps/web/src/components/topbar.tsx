"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Bell,
  BookOpen,
  Box,
  BriefcaseBusiness,
  CheckCheck,
  ContactRound,
  FilePenLine,
  Handshake,
  Headset,
  Menu,
  Plus,
  Search,
  Target,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSession, useShell } from "@/components/providers";
import { relativeTime } from "@/lib/format";
import type { NotificationItem, SearchGroup } from "@/lib/platform-types";
import { api, useDebounced } from "@/lib/use-api";

const SEARCH_ICONS: Record<string, LucideIcon> = {
  account: BriefcaseBusiness,
  contact: ContactRound,
  lead: Target,
  opportunity: Handshake,
  case: Headset,
  quote: FilePenLine,
  article: BookOpen,
  product: Box,
};

function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) close();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

export function Topbar() {
  const { setNavOpen } = useShell();
  return (
    <header className="topbar">
      <button type="button" className="iconButton topbarMenu" aria-label="Open navigation" onClick={() => setNavOpen(true)}>
        <Menu size={19} />
      </button>
      <GlobalSearch />
      <div className="topbarActions">
        <QuickCreate />
        <Notifications />
      </div>
    </header>
  );
}

// ---------------------------------------------------------------- Global search

function GlobalSearch() {
  const router = useRouter();
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const [groups, setGroups] = useState<SearchGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounced = useDebounced(term.trim(), 200);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);

  const flat = groups.flatMap((group) => group.items.map((item) => ({ ...item, type: group.type })));

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (debounced.length < 2) {
      setGroups([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .get<{ groups: SearchGroup[] }>(`/search?q=${encodeURIComponent(debounced)}`)
      .then((result) => {
        if (!cancelled) {
          setGroups(result.groups);
          setCursor(0);
        }
      })
      .catch(() => {
        if (!cancelled) setGroups([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  function go(href: string) {
    setOpen(false);
    setTerm("");
    router.push(href);
  }

  let index = -1;

  return (
    <div className="topbarSearch searchBox" ref={ref}>
      <Search size={18} />
      <input
        ref={inputRef}
        value={term}
        placeholder="Search records, cases, quotes, articles…"
        aria-label="Global search"
        role="combobox"
        aria-expanded={open && debounced.length >= 2}
        aria-controls="global-search-results"
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setTerm(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setCursor((value) => Math.min(flat.length - 1, value + 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setCursor((value) => Math.max(0, value - 1));
          } else if (event.key === "Enter" && flat[cursor]) {
            event.preventDefault();
            go(flat[cursor].href);
          }
        }}
      />
      <div className="shortcut">⌘K</div>

      {open && debounced.length >= 2 ? (
        <div className="ui-popover" id="global-search-results" role="listbox">
          {loading && !groups.length ? (
            <div className="ui-muted ui-small" style={{ padding: 12 }}>
              Searching…
            </div>
          ) : !groups.length ? (
            <div className="ui-muted ui-small" style={{ padding: 12 }}>
              No matches for “{debounced}”.
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.type}>
                <div className="ui-menu-heading">{group.label}</div>
                {group.items.map((item) => {
                  index += 1;
                  const Icon = SEARCH_ICONS[group.type] ?? Search;
                  const selected = index === cursor;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      className={`ui-menu-item${selected ? " selected" : ""}`}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => go(item.href)}
                    >
                      <Icon size={15} />
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span className="ui-truncate" style={{ display: "block", fontWeight: 600 }}>
                          {item.title}
                        </span>
                        {item.subtitle ? (
                          <span className="ui-truncate ui-muted" style={{ display: "block", fontSize: 12 }}>
                            {item.subtitle}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- Quick create

function QuickCreate() {
  const { can } = useSession();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);

  const items: { label: string; href: string; icon: LucideIcon; show: boolean }[] = [
    { label: "Lead", href: "/leads?new=1", icon: Target, show: can("records.write") },
    { label: "Account", href: "/accounts?new=1", icon: BriefcaseBusiness, show: can("records.write") },
    { label: "Contact", href: "/contacts?new=1", icon: ContactRound, show: can("records.write") },
    { label: "Opportunity", href: "/opportunities?new=1", icon: Handshake, show: can("records.write") },
    { label: "Quote", href: "/quotes/new", icon: FilePenLine, show: can("sales.quotes") },
    { label: "Case", href: "/cases?new=1", icon: Headset, show: can("service.cases") && can("records.write") },
    { label: "Knowledge article", href: "/knowledge/new", icon: BookOpen, show: can("service.knowledge_write") },
  ];
  const visible = items.filter((item) => item.show);
  if (!visible.length) return null;

  return (
    <div className="topbarCreate" ref={ref}>
      <button
        type="button"
        className="aiButton"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Plus size={17} />
        <span>Create</span>
      </button>
      {open ? (
        <div className="ui-popover" role="menu">
          {visible.map((item) => {
            const Icon = item.icon;
            return (
              <Link key={item.href} href={item.href} className="ui-menu-item" role="menuitem" onClick={() => setOpen(false)}>
                <Icon size={15} /> {item.label}
              </Link>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- Notifications

function Notifications() {
  const router = useRouter();
  const { session } = useSession();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);

  const load = useCallback(async () => {
    try {
      const result = await api.get<{ unread: number; items: NotificationItem[] }>("/notifications?limit=20");
      setItems(result.items);
      setUnread(result.unread);
    } catch {
      // The bell is best-effort; pages report their own errors.
    }
  }, []);

  useEffect(() => {
    if (!session) return;
    void load();
    const timer = window.setInterval(() => void load(), 60000);
    return () => window.clearInterval(timer);
  }, [load, session]);

  async function openItem(item: NotificationItem) {
    if (!item.is_read) {
      setItems((current) => current.map((entry) => (entry.id === item.id ? { ...entry, is_read: true } : entry)));
      setUnread((value) => Math.max(0, value - 1));
      void api.post(`/notifications/${item.id}/read`).catch(() => undefined);
    }
    setOpen(false);
    if (item.link) router.push(item.link);
  }

  async function markAll() {
    setItems((current) => current.map((entry) => ({ ...entry, is_read: true })));
    setUnread(0);
    await api.post("/notifications/read-all").catch(() => undefined);
  }

  return (
    <div className="topbarNotifications" ref={ref}>
      <button
        className="iconButton"
        type="button"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
          if (!open) void load();
        }}
      >
        <Bell size={19} />
        {unread ? <span className="topbarBadge">{unread > 9 ? "9+" : unread}</span> : null}
      </button>
      {open ? (
        <div className="ui-popover" role="dialog" aria-label="Notifications">
          <div className="ui-row" style={{ padding: "12px 14px" }}>
            <strong style={{ fontSize: 14 }}>Notifications</strong>
            <span className="ui-spacer" />
            {unread ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void markAll()}>
                <CheckCheck size={14} /> Mark all read
              </button>
            ) : null}
          </div>
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            {items.length ? (
              items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`notificationItem ${item.is_read ? "read" : "unread"}`}
                  onClick={() => void openItem(item)}
                >
                  <span className="dot" />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: "block", fontWeight: 600, fontSize: 13 }}>{item.title}</span>
                    {item.body ? (
                      <span className="ui-muted" style={{ display: "block", fontSize: 12.5, marginTop: 2 }}>
                        {item.body}
                      </span>
                    ) : null}
                    <span className="ui-muted" style={{ display: "block", fontSize: 11.5, marginTop: 4 }}>
                      {relativeTime(item.created_at)}
                    </span>
                  </span>
                </button>
              ))
            ) : (
              <div className="ui-muted ui-small" style={{ padding: "18px 14px 22px", borderTop: "1px solid var(--border)" }}>
                You&apos;re all caught up. Workflow alerts, assignments and mentions show up here.
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
