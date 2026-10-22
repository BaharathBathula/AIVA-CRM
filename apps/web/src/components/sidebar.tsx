"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  BarChart3,
  BookOpen,
  Box,
  BriefcaseBusiness,
  Check,
  ChevronsUpDown,
  CircleGauge,
  ContactRound,
  FilePenLine,
  Gauge,
  Handshake,
  Headset,
  LayoutDashboard,
  LayoutGrid,
  LogOut,
  Mail,
  PieChart,
  Settings,
  ShieldCheck,
  Target,
  UserRound,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useSession, useShell, useToast } from "@/components/providers";
import { Avatar } from "@/components/ui";
import { logout, switchOrganization } from "@/lib/auth";

type SidebarProps = {
  /** Kept for older pages; the active item is derived from the URL. */
  active?: string;
};

type MenuItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  permission?: string;
  match?: string[];
};

type MenuSection = {
  title: string;
  items: MenuItem[];
};

const sections: MenuSection[] = [
  {
    title: "Workspace",
    items: [{ label: "Home", href: "/", icon: LayoutDashboard }],
  },
  {
    title: "Sales",
    items: [
      { label: "Leads", href: "/leads", icon: Target },
      { label: "Accounts", href: "/accounts", icon: BriefcaseBusiness },
      { label: "Contacts", href: "/contacts", icon: ContactRound },
      { label: "Opportunities", href: "/opportunities", icon: Handshake },
      { label: "Pipeline", href: "/pipeline", icon: BarChart3 },
      { label: "Quotes", href: "/quotes", icon: FilePenLine },
      { label: "Products", href: "/products", icon: Box },
      { label: "Forecast", href: "/forecast", icon: Gauge, permission: "sales.forecast_view" },
    ],
  },
  {
    title: "Service",
    items: [
      { label: "Cases", href: "/cases", icon: Headset, permission: "service.cases" },
      { label: "Knowledge base", href: "/knowledge", icon: BookOpen },
    ],
  },
  {
    title: "Engagement",
    items: [
      { label: "Activities", href: "/activities", icon: Activity },
      { label: "Tasks", href: "/tasks", icon: CircleGauge },
      { label: "Email", href: "/email", icon: Mail },
    ],
  },
  {
    title: "Insights",
    items: [
      { label: "Reports", href: "/reports", icon: PieChart },
      { label: "Dashboards", href: "/dashboards", icon: LayoutGrid },
      { label: "Workflows", href: "/workflows", icon: Workflow },
    ],
  },
];

export function Sidebar(_props: SidebarProps) {
  const pathname = usePathname();
  const { can } = useSession();
  const { navOpen, setNavOpen } = useShell();

  function isActive(item: MenuItem) {
    if (item.href === "/") return pathname === "/";
    return pathname === item.href || pathname.startsWith(`${item.href}/`);
  }

  return (
    <>
      {navOpen ? <div className="sidebarScrim" onClick={() => setNavOpen(false)} aria-hidden /> : null}
      <aside className={`sidebar${navOpen ? " open" : ""}`} aria-label="Main navigation">
        <div className="sidebarInner">
          <Link href="/" className="sidebarBrand" aria-label="AIVA CRM home">
            <Image src="/aiva-logo.png" alt="AIVA CRM" width={172} height={60} priority className="sidebarBrandImage" style={{ height: "auto" }} />
          </Link>

          <nav className="sidebarNav">
            {sections.map((section) => {
              const items = section.items.filter((item) => !item.permission || can(item.permission));
              if (!items.length) return null;
              return (
                <div className="sidebarSection" key={section.title}>
                  <div className="sidebarSectionTitle">{section.title}</div>
                  <div className="sidebarSectionItems">
                    {items.map((item) => {
                      const Icon = item.icon;
                      const selected = isActive(item);
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          className={`sidebarItem${selected ? " active" : ""}`}
                          aria-current={selected ? "page" : undefined}
                        >
                          <span className="sidebarItemIcon">
                            <Icon size={18} strokeWidth={1.8} />
                          </span>
                          <span className="sidebarItemLabel">{item.label}</span>
                        </Link>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </nav>

          <div className="sidebarFooter">
            <Link href="/settings" className={`sidebarItem${pathname.startsWith("/settings") ? " active" : ""}`}>
              <span className="sidebarItemIcon">
                <Settings size={18} strokeWidth={1.8} />
              </span>
              <span className="sidebarItemLabel">Settings</span>
            </Link>
            <UserMenu />
          </div>
        </div>
      </aside>
    </>
  );
}

function UserMenu() {
  const { session } = useSession();
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!session) {
    return (
      <div className="sidebarUser">
        <div className="sidebarUserButton" style={{ opacity: 0.6 }}>
          <span className="ui-avatar">…</span>
          <div style={{ minWidth: 0 }}>
            <div className="sidebarUserName">Loading…</div>
          </div>
        </div>
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
    <div className="sidebarUser" ref={ref}>
      <button
        type="button"
        className="sidebarUserButton"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Avatar name={session.user.full_name} size={32} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="sidebarUserName">{session.user.full_name}</div>
          <div className="sidebarUserMeta">
            {session.role_name} · {session.organization.name}
          </div>
        </div>
        <ChevronsUpDown size={15} color="#8c94a7" />
      </button>

      {open ? (
        <div className="ui-popover" role="menu">
          <div style={{ padding: "8px 10px 6px" }}>
            <div style={{ fontWeight: 650, fontSize: 13 }}>{session.user.full_name}</div>
            <div className="ui-muted ui-small ui-truncate">{session.user.email}</div>
          </div>
          <div className="ui-menu-sep" />
          <Link className="ui-menu-item" href="/settings/profile" role="menuitem" onClick={() => setOpen(false)}>
            <UserRound size={15} /> Your profile
          </Link>
          <Link className="ui-menu-item" href="/settings/security" role="menuitem" onClick={() => setOpen(false)}>
            <ShieldCheck size={15} /> Password & security
          </Link>
          {session.organizations.length > 1 ? (
            <>
              <div className="ui-menu-sep" />
              <div className="ui-menu-heading">Workspaces</div>
              {session.organizations.map((organization) => (
                <button
                  key={organization.id}
                  type="button"
                  className="ui-menu-item"
                  role="menuitem"
                  disabled={busy}
                  onClick={() => void switchTo(organization.id)}
                >
                  <span style={{ width: 15, display: "inline-flex" }}>
                    {organization.id === session.organization.id ? <Check size={15} /> : null}
                  </span>
                  <span className="ui-truncate">{organization.name}</span>
                </button>
              ))}
            </>
          ) : null}
          <div className="ui-menu-sep" />
          <button type="button" className="ui-menu-item" role="menuitem" onClick={() => void signOut()} disabled={busy}>
            <LogOut size={15} /> Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
