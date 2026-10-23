"use client";

import Link from "next/link";
import {
  Boxes,
  CircleUserRound,
  ClipboardCheck,
  CreditCard,
  Database,
  FileClock,
  FlaskConical,
  Gauge,
  GitBranch,
  Globe,
  KeyRound,
  ListChecks,
  MousePointerClick,
  Network,
  Plug,
  ShieldCheck,
  ShieldPlus,
  Smartphone,
  Target,
  Timer,
  UserCog,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import { PlanChip } from "@/components/plan";
import { useSession } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { PageHeader } from "@/components/ui";

type Tile = { href: string; label: string; description: string; icon: LucideIcon; permission?: string; feature?: string };

const GROUPS: { title: string; tiles: Tile[] }[] = [
  {
    title: "Your account",
    tiles: [
      { href: "/settings/profile", label: "Profile", description: "Your name, title, phone and time zone.", icon: CircleUserRound },
      { href: "/settings/security", label: "Password and security", description: "Two-step verification and signed-in devices.", icon: ShieldCheck },
      { href: "/settings/integrations", label: "Integrations", description: "Gmail or Outlook, calendar feed, Zoom, Slack and Teams.", icon: Plug },
      { href: "/settings/mobile", label: "Mobile app", description: "Install AIVA on your phone's home screen.", icon: Smartphone, feature: "mobile_app" },
    ],
  },
  {
    title: "Workspace",
    tiles: [
      { href: "/settings/organization", label: "Organization", description: "Name, currency, time zone, fiscal year, session length.", icon: Globe },
      { href: "/settings/billing", label: "Billing and plan", description: "Your plan, users, invoices and payment.", icon: CreditCard },
      { href: "/settings/users", label: "Users", description: "Invite people, change roles, deactivate access.", icon: Users, permission: "admin.users" },
      { href: "/settings/roles", label: "Roles and permissions", description: "Built-in and custom roles.", icon: UserCog, permission: "admin.users" },
      { href: "/settings/teams", label: "Teams", description: "Teams and their managers.", icon: Users },
      { href: "/settings/hierarchy", label: "Hierarchy and visibility", description: "Who reports to whom, nested teams and who sees which records.", icon: Network, feature: "reporting_hierarchy" },
      { href: "/settings/audit", label: "Audit log", description: "Every change and sign-in, with before and after.", icon: FileClock, permission: "admin.audit" },
    ],
  },
  {
    title: "Customization",
    tiles: [
      { href: "/settings/fields", label: "Fields and modules", description: "Custom fields on any record and your own record types.", icon: Boxes, feature: "custom_modules" },
      { href: "/settings/processes", label: "Stage playbooks", description: "Required fields and checklists for each pipeline stage.", icon: ListChecks, feature: "process_management" },
      { href: "/settings/approvals", label: "Approval rules", description: "Sign-off for big deals and discounted quotes.", icon: ClipboardCheck, feature: "approvals" },
      { href: "/settings/buttons", label: "Custom buttons", description: "Buttons on records that open links, call webhooks or run functions.", icon: MousePointerClick, feature: "custom_buttons" },
      { href: "/workflows", label: "Workflows", description: "When-then automation rules.", icon: Workflow },
      { href: "/settings/lead-scoring", label: "Lead scoring", description: "Points rules behind every lead's grade.", icon: Gauge },
      { href: "/settings/sla", label: "SLA policies", description: "Response and resolution targets for cases.", icon: Timer, permission: "service.cases" },
    ],
  },
  {
    title: "Data and security",
    tiles: [
      { href: "/settings/data", label: "Import and export", description: "Bring in CSV files and download any list.", icon: Database, feature: "data_import_export" },
      { href: "/settings/api-keys", label: "API keys", description: "Connect other systems to the AIVA API.", icon: KeyRound, feature: "api_access" },
      { href: "/settings/compliance", label: "HIPAA compliance", description: "Enforced two-step sign-in, short sessions, view auditing.", icon: ShieldPlus, feature: "hipaa", permission: "admin.settings" },
      { href: "/settings/sandbox", label: "Developer sandbox", description: "Test changes in a copy of your setup, then deploy.", icon: FlaskConical, feature: "sandbox", permission: "admin.settings" },
      { href: "/portals", label: "Portals", description: "Customer help center and partner portal.", icon: Target, feature: "customer_portal" },
      { href: "/pipeline", label: "Pipelines", description: "Pipelines and their stages.", icon: GitBranch },
    ],
  },
];

export default function SettingsHub() {
  const { can } = useSession();
  return (
    <SettingsShell>
      <PageHeader title="Settings" description="Everything you can set up in AIVA, in one place." />
      <div className="settings-hub">
        {GROUPS.map((group) => {
          const tiles = group.tiles.filter((tile) => !tile.permission || can(tile.permission));
          if (!tiles.length) return null;
          return (
            <section key={group.title} className="settings-group">
              <h2>{group.title}</h2>
              <div className="settings-tiles">
                {tiles.map((tile) => {
                  const Icon = tile.icon;
                  return (
                    <Link key={tile.href} href={tile.href} className="settings-tile">
                      <span className="tile-icon">
                        <Icon size={19} />
                      </span>
                      <span>
                        <strong>
                          {tile.label} {tile.feature ? <PlanChip feature={tile.feature} /> : null}
                        </strong>
                        <span className="desc">{tile.description}</span>
                      </span>
                    </Link>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </SettingsShell>
  );
}
