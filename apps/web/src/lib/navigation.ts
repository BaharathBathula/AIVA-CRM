import { SETTINGS_PATHS } from "@/lib/settings-menu";
import {
  Boxes,
  ChartPie,
  Handshake,
  Headset,
  LayoutGrid,
  Megaphone,
  Package,
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

/** Every settings page lives under the "Settings" tab; the settings menu on the left moves between them. */
export const SETTINGS_PAGES = [...SETTINGS_PATHS];

export const SECTIONS: NavSection[] = [
  {
    key: "home",
    label: "Home",
    icon: LayoutGrid,
    pages: [
      { label: "Overview", href: "/" },
      { label: "Activity", href: "/activities" },
      { label: "Tasks", href: "/tasks" },
      { label: "Meetings", href: "/meetings", feature: "tasks_meetings_calls" },
      { label: "Calls", href: "/calls", feature: "calling" },
      { label: "Conversations", href: "/conversations", feature: "conversation_intelligence" },
      { label: "SalesInbox", href: "/salesinbox" },
      { label: "Email", href: "/email" },
      { label: "Chat", href: "/chat", feature: "team_chat" },
      { label: "People", href: "/people" },
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
      { label: "Sequences", href: "/sequences", feature: "sequences" },
      { label: "Forecast", href: "/forecast", permission: "sales.forecast_view" },
      { label: "Predictions", href: "/predictions", feature: "predictive_intelligence" },
      { label: "Documents", href: "/documents", feature: "documents" },
    ],
  },
  {
    key: "inventory",
    label: "Inventory",
    icon: Package,
    pages: [
      { label: "Products", href: "/products", feature: "cpq" },
      { label: "Price books", href: "/price-books", feature: "price_books" },
      { label: "Quotes", href: "/quotes", feature: "cpq" },
      { label: "Sales orders", href: "/sales-orders", feature: "sales_orders" },
      { label: "Purchase orders", href: "/purchase-orders", feature: "purchase_orders" },
      { label: "Invoices", href: "/invoices", feature: "cpq" },
      { label: "Vendors", href: "/vendors", feature: "purchase_orders" },
      { label: "Stock", href: "/inventory", feature: "inventory" },
    ],
  },
  {
    key: "marketing",
    label: "Marketing",
    icon: Megaphone,
    pages: [
      { label: "Campaigns", href: "/marketing/campaigns", feature: "mass_email" },
      { label: "Social", href: "/social", feature: "social" },
      { label: "Visits", href: "/visits", feature: "website_visits" },
      { label: "Signals", href: "/signals", feature: "buying_intent" },
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
      { label: "Services", href: "/services", feature: "services" },
      { label: "Appointments", href: "/appointments", feature: "services" },
      { label: "Projects", href: "/projects", feature: "projects" },
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
      { label: "Settings", href: "/settings", exact: true, match: SETTINGS_PAGES },
      { label: "Profile", href: "/settings/profile" },
      { label: "Users", href: "/settings/users", permission: "admin.users" },
      { label: "Billing", href: "/settings/billing", match: ["/settings/billing/add-ons", "/settings/billing/invoices"] },
      { label: "Outbound", href: "/settings/outbound" },
      { label: "Integrations", href: "/settings/integrations" },
    ],
  },
];

/** How specifically a page claims a path: the length of its longest matching prefix, or -1. */
function matchLength(page: NavPage, pathname: string) {
  if (page.href === "/") return pathname === "/" ? 1 : -1;
  const under = (prefix: string) => pathname === prefix || pathname.startsWith(`${prefix}/`);
  const prefixes = page.exact ? (page.match ?? []) : [page.href, ...(page.match ?? [])];
  if (page.exact && pathname === page.href) return page.href.length;
  return Math.max(-1, ...prefixes.filter(under).map((prefix) => prefix.length));
}

export function currentLocation(pathname: string) {
  for (const section of SECTIONS) {
    let best: { page: NavPage; length: number } | null = null;
    for (const item of section.pages) {
      const length = matchLength(item, pathname);
      if (length >= 0 && (!best || length > best.length)) best = { page: item, length };
    }
    if (best) return { section, page: best.page };
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
