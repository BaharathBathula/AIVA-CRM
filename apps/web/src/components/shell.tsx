"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Bell,
  BookOpen,
  Box,
  BriefcaseBusiness,
  Check,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  ContactRound,
  FilePenLine,
  Handshake,
  Headset,
  LogOut,
  Moon,
  Plus,
  Search,
  ShieldCheck,
  Sun,
  Target,
  UserRound,
  X,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSession, useTheme, useToast } from "@/components/providers";
import { Avatar } from "@/components/ui";
import { logout, switchOrganization } from "@/lib/auth";
import { relativeTime } from "@/lib/format";
import { SECTIONS, currentLocation, sectionHref, visiblePages } from "@/lib/navigation";
import type { NotificationItem, SearchGroup } from "@/lib/platform-types";
import { api, useDebounced } from "@/lib/use-api";

/** Closes a popover on outside click or Escape. */
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

// ======================================================================= Top bar

export function TopBar() {
  const pathname = usePathname();
  const { can } = useSession();
  const { section, page } = currentLocation(pathname);
  const pages = visiblePages(section, can);
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <header className="shell-top">
      <Link href="/" className="shell-pill shell-brand" aria-label="AIVA home">
        <Image src="/aiva-mark.png" alt="" width={30} height={30} priority />
        <span>AIVA</span>
      </Link>

      <nav className="shell-pill shell-tabs" aria-label={`${section.label} pages`}>
        {pages.map((item) => {
          const active = page?.href === item.href;
          return (
            <Link key={item.href} href={item.href} className={`shell-tab${active ? " active" : ""}`} aria-current={active ? "page" : undefined}>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="shell-spacer" />

      <div className="shell-pill shell-actions">
        <button type="button" className="shell-icon" aria-label="Search (Ctrl+K)" title="Search (Ctrl+K)" onClick={() => setSearchOpen(true)}>
          <Search size={19} strokeWidth={1.8} />
        </button>
        <QuickCreate />
        <Notifications />
      </div>

      <UserMenu />

      {searchOpen ? <SearchDialog onClose={() => setSearchOpen(false)} /> : null}
    </header>
  );
}

// ======================================================================= Rail

export function Rail() {
  const pathname = usePathname();
  const { can } = useSession();
  const { theme, setTheme } = useTheme();
  const { section } = currentLocation(pathname);
  const [helpOpen, setHelpOpen] = useState(false);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const helpRef = useDismiss(helpOpen, closeHelp);
  const router = useRouter();

  async function signOut() {
    await logout();
    router.replace("/login");
    router.refresh();
  }

  return (
    <>
      <aside className="shell-rail" aria-label="Main navigation">
        <div className="shell-capsule" role="group" aria-label="Theme">
          <button
            type="button"
            className={`shell-railbtn${theme === "light" ? " selected" : ""}`}
            aria-pressed={theme === "light"}
            data-tip="Light"
            aria-label="Light theme"
            onClick={() => setTheme("light")}
          >
            <Sun size={19} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            className={`shell-railbtn${theme === "dark" ? " selected" : ""}`}
            aria-pressed={theme === "dark"}
            data-tip="Dark"
            aria-label="Dark theme"
            onClick={() => setTheme("dark")}
          >
            <Moon size={19} strokeWidth={1.8} />
          </button>
        </div>

        <nav className="shell-capsule">
          {SECTIONS.map((item) => {
            const Icon = item.icon;
            const active = item.key === section.key;
            return (
              <Link
                key={item.key}
                href={sectionHref(item, can)}
                className={`shell-railbtn${active ? " active" : ""}`}
                aria-current={active ? "page" : undefined}
                aria-label={item.label}
                data-tip={item.label}
              >
                <Icon size={19} strokeWidth={1.8} />
              </Link>
            );
          })}
        </nav>

        <div className="shell-capsule shell-rail-end" ref={helpRef}>
          <button
            type="button"
            className="shell-railbtn"
            aria-label="Help"
            data-tip="Help"
            aria-expanded={helpOpen}
            onClick={() => setHelpOpen((open) => !open)}
          >
            <CircleHelp size={19} strokeWidth={1.8} />
          </button>
          <button type="button" className="shell-railbtn" aria-label="Sign out" data-tip="Sign out" onClick={() => void signOut()}>
            <LogOut size={19} strokeWidth={1.8} />
          </button>
          {helpOpen ? (
            <div className="ui-popover shell-help" role="dialog" aria-label="Help">
              <strong>Getting around</strong>
              <ul>
                <li>
                  <kbd>Ctrl</kbd> <kbd>K</kbd> searches records, cases, quotes and articles.
                </li>
                <li>The icons on the left switch between Home, Sales, Service, Insights and Settings.</li>
                <li>Pages for the current area are in the bar at the top.</li>
                <li>
                  Use <Plus size={12} style={{ verticalAlign: -1 }} /> to create a lead, deal, quote or case from anywhere.
                </li>
              </ul>
              <a href="https://github.com/BaharathBathula/AIVA-CRM#readme" target="_blank" rel="noopener noreferrer">
                Read the full guide
              </a>
            </div>
          ) : null}
        </div>
      </aside>

      <nav className="shell-mobilebar" aria-label="Sections">
        {SECTIONS.map((item) => {
          const Icon = item.icon;
          const active = item.key === section.key;
          return (
            <Link
              key={item.key}
              href={sectionHref(item, can)}
              className={`shell-railbtn${active ? " active" : ""}`}
              aria-current={active ? "page" : undefined}
              aria-label={item.label}
            >
              <Icon size={20} strokeWidth={1.8} />
            </Link>
          );
        })}
      </nav>
    </>
  );
}

// ======================================================================= Search

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

function SearchDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [term, setTerm] = useState("");
  const [groups, setGroups] = useState<SearchGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const debounced = useDebounced(term.trim(), 200);
  const flat = groups.flatMap((group) => group.items.map((item) => ({ ...item, type: group.type })));

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

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
    onClose();
    router.push(href);
  }

  let index = -1;

  return (
    <div className="ui-modal-backdrop shell-search-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="shell-search" role="dialog" aria-modal="true" aria-label="Search">
        <div className="shell-search-field">
          <Search size={19} strokeWidth={1.8} />
          <input
            autoFocus
            value={term}
            placeholder="Search leads, accounts, deals, cases, quotes, articles…"
            aria-label="Search AIVA"
            role="combobox"
            aria-expanded={debounced.length >= 2}
            aria-controls="shell-search-results"
            onChange={(event) => setTerm(event.target.value)}
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
          <button type="button" className="shell-icon" aria-label="Close search" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="shell-search-results" id="shell-search-results" role="listbox">
          {debounced.length < 2 ? (
            <p className="ui-muted ui-small" style={{ padding: "6px 14px 14px", margin: 0 }}>
              Type at least two letters. Use the arrow keys and Enter to open a result.
            </p>
          ) : loading && !groups.length ? (
            <p className="ui-muted ui-small" style={{ padding: "6px 14px 14px", margin: 0 }}>
              Searching…
            </p>
          ) : !groups.length ? (
            <p className="ui-muted ui-small" style={{ padding: "6px 14px 14px", margin: 0 }}>
              Nothing matches “{debounced}”.
            </p>
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
                      onClick={() => go(item.href)}
                    >
                      <Icon size={16} />
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span className="ui-truncate" style={{ display: "block", fontWeight: 650 }}>
                          {item.title}
                        </span>
                        {item.subtitle ? (
                          <span className="ui-truncate ui-muted" style={{ display: "block", fontSize: 12.5 }}>
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
      </div>
    </div>
  );
}

// ======================================================================= Create

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
    <div className="shell-pop" ref={ref}>
      <button type="button" className="shell-icon" aria-label="Create" title="Create" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <Plus size={20} strokeWidth={1.8} />
      </button>
      {open ? (
        <div className="ui-popover shell-menu" role="menu">
          <div className="ui-menu-heading">Create</div>
          {visible.map((item) => {
            const Icon = item.icon;
            return (
              <Link key={item.href} href={item.href} className="ui-menu-item" role="menuitem" onClick={() => setOpen(false)}>
                <Icon size={16} /> {item.label}
              </Link>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

// ======================================================================= Notifications

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
      // Best effort; pages report their own errors.
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
    <div className="shell-pop" ref={ref}>
      <button
        className="shell-icon"
        type="button"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
          if (!open) void load();
        }}
      >
        <Bell size={19} strokeWidth={1.8} />
        {unread ? <span className="shell-dot" aria-hidden /> : null}
      </button>
      {open ? (
        <div className="ui-popover shell-menu shell-notifications" role="dialog" aria-label="Notifications">
          <div className="ui-row" style={{ padding: "8px 10px 10px" }}>
            <strong style={{ fontSize: 15 }}>Notifications</strong>
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
                <button key={item.id} type="button" className={`notificationItem ${item.is_read ? "read" : "unread"}`} onClick={() => void openItem(item)}>
                  <span className="dot" />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: "block", fontWeight: 650, fontSize: 13.5 }}>{item.title}</span>
                    {item.body ? (
                      <span className="ui-muted" style={{ display: "block", fontSize: 12.5, marginTop: 2 }}>
                        {item.body}
                      </span>
                    ) : null}
                    <span className="ui-muted" style={{ display: "block", fontSize: 12, marginTop: 4 }}>
                      {relativeTime(item.created_at)}
                    </span>
                  </span>
                </button>
              ))
            ) : (
              <p className="ui-muted ui-small" style={{ padding: "4px 12px 14px", margin: 0 }}>
                You&apos;re all caught up. Workflow alerts and case assignments show up here.
              </p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ======================================================================= Account

function UserMenu() {
  const { session } = useSession();
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);

  if (!session) {
    return (
      <div className="shell-pill shell-user" aria-hidden>
        <span className="ui-avatar" style={{ width: 40, height: 40 }} />
        <span className="shell-user-text">
          <span className="ui-skeleton" style={{ display: "block", width: 90, height: 12 }} />
        </span>
      </div>
    );
  }

  async function signOut() {
    setBusy(true);
    await logout();
    router.replace("/login");
    router.refresh();
  }

  async function switchTo(organizationId: string) {
    if (organizationId === session?.organization.id) return;
    setBusy(true);
    try {
      await switchOrganization(organizationId);
      window.location.assign("/");
    } catch (error) {
      toast(error instanceof Error ? error.message : "Couldn't switch workspace.", "danger");
      setBusy(false);
    }
  }

  return (
    <div className="shell-pop" ref={ref}>
      <button type="button" className="shell-pill shell-user" aria-haspopup="menu" aria-expanded={open} aria-label="Account menu" onClick={() => setOpen((value) => !value)}>
        <Avatar name={session.user.full_name} size={40} />
        <span className="shell-user-text">
          <strong>{session.user.full_name}</strong>
          <span>{session.user.email}</span>
        </span>
        <ChevronDown size={16} className="shell-user-chevron" />
      </button>
      {open ? (
        <div className="ui-popover shell-menu shell-account" role="menu">
          <div style={{ padding: "8px 10px 10px" }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>{session.user.full_name}</div>
            <div className="ui-muted ui-small">
              {session.role_name} in {session.organization.name}
            </div>
          </div>
          <div className="ui-menu-sep" />
          <Link className="ui-menu-item" href="/settings/profile" role="menuitem" onClick={() => setOpen(false)}>
            <UserRound size={16} /> Your profile
          </Link>
          <Link className="ui-menu-item" href="/settings/security" role="menuitem" onClick={() => setOpen(false)}>
            <ShieldCheck size={16} /> Password and security
          </Link>
          <button type="button" className="ui-menu-item" role="menuitem" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />} {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
          {session.organizations.length > 1 ? (
            <>
              <div className="ui-menu-sep" />
              <div className="ui-menu-heading">Workspaces</div>
              {session.organizations.map((organization) => (
                <button key={organization.id} type="button" className="ui-menu-item" role="menuitem" disabled={busy} onClick={() => void switchTo(organization.id)}>
                  <span style={{ width: 16, display: "inline-flex" }}>{organization.id === session.organization.id ? <Check size={16} /> : null}</span>
                  <span className="ui-truncate">{organization.name}</span>
                </button>
              ))}
            </>
          ) : null}
          <div className="ui-menu-sep" />
          <button type="button" className="ui-menu-item" role="menuitem" onClick={() => void signOut()} disabled={busy}>
            <LogOut size={16} /> Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
