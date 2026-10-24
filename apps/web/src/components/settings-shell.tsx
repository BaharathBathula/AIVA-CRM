"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown, ChevronRight, Lock, Search, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { UpgradeGate } from "@/components/plan";
import { useSession } from "@/components/providers";
import { SETTINGS_MENU, activeSettingsItem, allowed, type SettingsGroup } from "@/lib/settings-menu";

const OPEN_KEY = "aiva.settings.open";

function readOpen(): string[] | null {
  try {
    const raw = window.localStorage.getItem(OPEN_KEY);
    return raw ? (JSON.parse(raw) as string[]) : null;
  } catch {
    return null;
  }
}

function writeOpen(keys: string[]) {
  try {
    window.localStorage.setItem(OPEN_KEY, JSON.stringify(keys));
  } catch {
    // Storage can be unavailable (private windows); the menu still works.
  }
}

/** Frame for settings pages: the grouped settings menu on the left, the page on the right. */
export function SettingsShell({
  children,
  permission,
  what,
  feature,
}: {
  children: ReactNode;
  permission?: string | string[];
  what?: string;
  /** Plan feature the page belongs to; shows the upgrade card when missing. */
  feature?: string;
}) {
  return (
    <AppShell>
      <div className="settings-layout">
        <SettingsMenu />
        <div className="settings-page">
          <PermissionGate permission={permission} what={what}>
            {feature ? <UpgradeGate feature={feature}>{children}</UpgradeGate> : children}
          </PermissionGate>
        </div>
      </div>
    </AppShell>
  );
}

function SettingsMenu() {
  const pathname = usePathname();
  const { can, has } = useSession();
  const active = activeSettingsItem(pathname);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<string[]>(() => (active ? [active.group.key] : ["personal"]));
  const [mobileOpen, setMobileOpen] = useState(false);

  // Restore the groups the person had open, and always open the current one.
  useEffect(() => {
    const saved = readOpen();
    setOpen((current) => Array.from(new Set([...(saved ?? current), ...(active ? [active.group.key] : [])])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.group.key]);

  useEffect(() => setMobileOpen(false), [pathname]);

  const groups = useMemo(() => {
    const term = filter.trim().toLowerCase();
    return SETTINGS_MENU.map((group): SettingsGroup => ({
      ...group,
      items: group.items.filter(
        (item) =>
          allowed(item, can) &&
          (!term || item.label.toLowerCase().includes(term) || item.description.toLowerCase().includes(term) || group.label.toLowerCase().includes(term)),
      ),
    })).filter((group) => group.items.length);
  }, [can, filter]);

  function toggle(key: string) {
    setOpen((current) => {
      const next = current.includes(key) ? current.filter((k) => k !== key) : [...current, key];
      writeOpen(next);
      return next;
    });
  }

  const searching = filter.trim().length > 0;

  return (
    <aside className={`settings-nav${mobileOpen ? " open" : ""}`} aria-label="Settings menu">
      <button type="button" className="settings-nav-mobile" aria-expanded={mobileOpen} onClick={() => setMobileOpen((value) => !value)}>
        <span>
          <span className="ui-muted ui-small">{active?.group.label ?? "Settings"}</span>
          <strong>{active?.item.label ?? "All settings"}</strong>
        </span>
        <ChevronDown size={18} />
      </button>
      <div className="settings-nav-body">
        <div className="settings-nav-head">
          <Link href="/settings" className={`settings-nav-all${pathname === "/settings" ? " active" : ""}`}>
            All settings
          </Link>
          <div className="settings-nav-search">
            <Search size={15} aria-hidden />
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Find a setting" aria-label="Find a setting" />
            {filter ? (
              <button type="button" aria-label="Clear" onClick={() => setFilter("")}>
                <X size={14} />
              </button>
            ) : null}
          </div>
        </div>
        <nav className="settings-nav-groups">
          {groups.map((group) => {
            const expanded = searching || open.includes(group.key);
            const holdsActive = active?.group.key === group.key;
            return (
              <div key={group.key} className={`settings-nav-group${holdsActive ? " current" : ""}`}>
                <button type="button" className="settings-nav-group-btn" aria-expanded={expanded} onClick={() => toggle(group.key)}>
                  {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  <span>{group.label}</span>
                </button>
                {expanded ? (
                  <ul>
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const current = active?.item.href === item.href;
                      const locked = Boolean(item.feature && !has(item.feature));
                      return (
                        <li key={item.href}>
                          <Link href={item.href} className={`settings-nav-link${current ? " active" : ""}`} aria-current={current ? "page" : undefined}>
                            <Icon size={15} aria-hidden />
                            <span>{item.label}</span>
                            {locked ? <Lock size={11} className="settings-nav-lock" aria-label="Not in your plan" /> : null}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </div>
            );
          })}
          {!groups.length ? <p className="ui-muted ui-small" style={{ padding: "8px 12px" }}>No settings match “{filter}”.</p> : null}
        </nav>
      </div>
    </aside>
  );
}

function PermissionGate({ permission, what, children }: { permission?: string | string[]; what?: string; children: ReactNode }) {
  const { can } = useSession();
  const required = Array.isArray(permission) ? permission : permission ? [permission] : [];
  if (required.every((key) => can(key))) return <>{children}</>;
  return (
    <div className="ui-card">
      <div className="ui-empty">
        <div className="icon">
          <ShieldCheck size={20} />
        </div>
        <h3>You don&apos;t have access</h3>
        <p>Your role doesn&apos;t include permission to manage {what ?? "this setting"}. Ask a workspace admin if you need it.</p>
      </div>
    </div>
  );
}
