import {
  ChartPie,
  Handshake,
  Headset,
  LayoutGrid,
  Settings,
  type LucideIcon,
} from "lucide-react";

/**
 * App navigation. Sections appear as icons in the left rail; the pages of the
 * current section appear as tabs in the top bar.
 */

export type NavPage = {
  label: string;
  href: string;
  permission?: string;
  /** Extra path prefixes that belong to this page (e.g. detail routes). */
  match?: string[];
};

export type NavSection = {
  key: string;
  label: string;
  icon: LucideIcon;
  pages: NavPage[];
};

export const SECTIONS: NavSection[] = [
  {
    key: "home",
    label: "Home",
    icon: LayoutGrid,
    pages: [
      { label: "Overview", href: "/" },
      { label: "Activity", href: "/activities" },
      { label: "Tasks", href: "/tasks" },
      { label: "Email", href: "/email" },
    ],
  },
  {
    key: "sales",
    label: "Sales",
    icon: Handshake,
    pages: [
      { label: "Leads", href: "/leads" },
      { label: "Accounts", href: "/accounts" },
      { label: "Contacts", href: "/contacts" },
      { label: "Opportunities", href: "/opportunities" },
      { label: "Pipeline", href: "/pipeline" },
      { label: "Quotes", href: "/quotes" },
      { label: "Products", href: "/products" },
      { label: "Forecast", href: "/forecast", permission: "sales.forecast_view" },
    ],
  },
  {
    key: "service",
    label: "Service",
    icon: Headset,
    pages: [
      { label: "Cases", href: "/cases", permission: "service.cases" },
      { label: "Knowledge base", href: "/knowledge" },
    ],
  },
  {
    key: "insights",
    label: "Insights",
    icon: ChartPie,
    pages: [
      { label: "Reports", href: "/reports" },
      { label: "Dashboards", href: "/dashboards" },
      { label: "Workflows", href: "/workflows" },
    ],
  },
  {
    key: "settings",
    label: "Settings",
    icon: Settings,
    pages: [
      { label: "Profile", href: "/settings/profile" },
      { label: "Security", href: "/settings/security" },
      { label: "Organization", href: "/settings/organization" },
      { label: "Users", href: "/settings/users", permission: "admin.users" },
      { label: "Roles", href: "/settings/roles", permission: "admin.users" },
      { label: "Teams", href: "/settings/teams" },
      { label: "Audit log", href: "/settings/audit", permission: "admin.audit" },
      { label: "Lead scoring", href: "/settings/lead-scoring" },
      { label: "SLA policies", href: "/settings/sla", permission: "service.cases" },
    ],
  },
];

function pageMatches(page: NavPage, pathname: string) {
  if (page.href === "/") return pathname === "/";
  return [page.href, ...(page.match ?? [])].some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

export function currentLocation(pathname: string) {
  for (const section of SECTIONS) {
    const page = section.pages.find((item) => pageMatches(item, pathname));
    if (page) return { section, page };
  }
  if (pathname.startsWith("/settings")) return { section: SECTIONS[SECTIONS.length - 1], page: null };
  return { section: SECTIONS[0], page: null };
}

export function visiblePages(section: NavSection, can: (permission: string) => boolean) {
  return section.pages.filter((page) => !page.permission || can(page.permission));
}

/** Where a rail icon takes you: the first page of the section you can open. */
export function sectionHref(section: NavSection, can: (permission: string) => boolean) {
  return visiblePages(section, can)[0]?.href ?? "/";
}
