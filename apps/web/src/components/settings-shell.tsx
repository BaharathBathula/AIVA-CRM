"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Building2,
  Gauge,
  KeyRound,
  ScrollText,
  ShieldCheck,
  Timer,
  UserRound,
  Users,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession } from "@/components/providers";

type Item = { href: string; label: string; icon: LucideIcon; permission?: string };

const GROUPS: { title: string; items: Item[] }[] = [
  {
    title: "Personal",
    items: [
      { href: "/settings/profile", label: "Profile", icon: UserRound },
      { href: "/settings/security", label: "Password & security", icon: ShieldCheck },
    ],
  },
  {
    title: "Workspace",
    items: [
      { href: "/settings/organization", label: "Organization", icon: Building2 },
      { href: "/settings/users", label: "Users", icon: Users, permission: "admin.users" },
      { href: "/settings/roles", label: "Roles & permissions", icon: KeyRound, permission: "admin.users" },
      { href: "/settings/teams", label: "Teams", icon: UsersRound },
      { href: "/settings/audit", label: "Audit log", icon: ScrollText, permission: "admin.audit" },
    ],
  },
  {
    title: "Sales & service",
    items: [
      { href: "/settings/lead-scoring", label: "Lead scoring", icon: Gauge },
      { href: "/settings/sla", label: "SLA policies", icon: Timer, permission: "service.cases" },
    ],
  },
];

export function SettingsShell({
  children,
  permission,
  what,
}: {
  children: ReactNode;
  permission?: string | string[];
  what?: string;
}) {
  const pathname = usePathname();
  const { can } = useSession();

  return (
    <AppShell>
      <div className="settings-shell">
        <nav className="settings-nav" aria-label="Settings">
          {GROUPS.map((group) => {
            const items = group.items.filter((item) => !item.permission || can(item.permission));
            if (!items.length) return null;
            return (
              <div key={group.title} style={{ display: "contents" }}>
                <div className="group">{group.title}</div>
                {items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <Link key={item.href} href={item.href} className={pathname === item.href ? "active" : undefined}>
                      <Icon size={16} />
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>
        <div style={{ minWidth: 0 }}>
          <PermissionGate permission={permission} what={what}>
            {children}
          </PermissionGate>
        </div>
      </div>
    </AppShell>
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
