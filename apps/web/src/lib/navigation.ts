import {
  Boxes,
  ChartPie,
  Handshake,
  Headset,
  LayoutGrid,
  Megaphone,
  Settings,
  type LucideIcon,
} from "lucide-react";

/**
 * App navigation. Sections appear as icons in the left rail; the pages of the
 * current section appear as tabs in the top bar. Pages tied to a plan feature
 * show a lock when the workspace's plan doesn't include it.
 */

export type NavPage = {
  label: string;
  href: string;
  permission?: string;
  /** Plan feature the page belongs to (lib/plans.ts). */
  feature?: string;
  /** Extra path prefixes that belong to this page (e.g. detail routes). */
  match?: string[];
  /** Only the exact href (plus `match`) counts as this page. */
  exact?: boolean;
};

export type NavSection = {
  key: string;
  label: string;
  icon: LucideIcon;
  pages: NavPage[];
};

/** Settings pages reached from the "All settings" hub. */
export const SETTINGS_PAGES = [
  "/settings/organization",
  "/settings/roles",
  "/settings/teams",
  "/settings/hierarchy",
  "/settings/audit",
  "/settings/lead-scoring",
  "/settings/sla",
  "/settings/fields",
  "/settings/processes",
  "/settings/approvals",
  "/settings/buttons",
  "/settings/api-keys",
  "/settings/data",
  "/settings/compliance",
  "/settings/sandbox",
  "/settings/mobile",
];

export const SECTIONS: NavSection[] = [
  {
    key: "home",
    label: "Home",
    icon: LayoutGrid,
    pages: [
      { label: "Overview", href: "/" },
      { label: "Activity", href: "/activities" },
      { label: "Tasks", href: "/tasks" },
      { label: "Calls", href: "/calls", feature: "calling" },
      { label: "Email", href: "/email" },
      { label: "Approvals", href: "/approvals", feature: "approvals" },
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
      { label: "Quotes", href: "/quotes", feature: "cpq" },
      { label: "Products", href: "/products", feature: "cpq" },
      { label: "Inventory", href: "/inventory", feature: "inventory" },
      { label: "Invoices", href: "/invoices", feature: "cpq" },
      { label: "Forecast", href: "/forecast", permission: "sales.forecast_view" },
      { label: "Predictions", href: "/predictions", feature: "predictive_intelligence" },
    ],
  },
  {
    key: "marketing",
    label: "Marketing",
    icon: Megaphone,
    pages: [
      { label: "Campaigns", href: "/marketing/campaigns", feature: "mass_email" },
      { label: "Templates", href: "/marketing/templates", feature: "email_templates" },
      { label: "Web forms", href: "/marketing/forms", feature: "web_forms" },
      { label: "Journeys", href: "/marketing/journeys", feature: "customer_journeys" },
      { label: "Target accounts", href: "/marketing/abm", feature: "abm" },
      { label: "Google Ads", href: "/marketing/google-ads", feature: "google_ads" },
    ],
  },
  {
    key: "service",
    label: "Service",
    icon: Headset,
    pages: [
      { label: "Cases", href: "/cases", permission: "service.cases" },
      { label: "Knowledge base", href: "/knowledge" },
      { label: "Portals", href: "/portals", feature: "customer_portal" },
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
      { label: "AI agents", href: "/ai", feature: "ai_agents" },
    ],
  },
  {
    key: "apps",
    label: "Apps",
    icon: Boxes,
    pages: [{ label: "All modules", href: "/modules", exact: true }],
  },
  {
    key: "settings",
    label: "Settings",
    icon: Settings,
    pages: [
      { label: "All settings", href: "/settings", exact: true, match: SETTINGS_PAGES },
      { label: "Profile", href: "/settings/profile" },
      { label: "Security", href: "/settings/security" },
      { label: "Billing", href: "/settings/billing" },
      { label: "Users", href: "/settings/users", permission: "admin.users" },
      { label: "Integrations", href: "/settings/integrations" },
    ],
  },
];

function pageMatches(page: NavPage, pathname: string) {
  if (page.href === "/") return pathname === "/";
  const under = (prefix: string) => pathname === prefix || pathname.startsWith(`${prefix}/`);
  if (page.exact) return pathname === page.href || (page.match ?? []).some(under);
  return [page.href, ...(page.match ?? [])].some(under);
}

export function currentLocation(pathname: string) {
  for (const section of SECTIONS) {
    const page = section.pages.find((item) => pageMatches(item, pathname));
    if (page) return { section, page };
  }
  if (pathname.startsWith("/modules")) return { section: SECTIONS.find((s) => s.key === "apps")!, page: null };
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
