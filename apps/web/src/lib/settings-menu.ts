import {
  Activity,
  BadgeCheck,
  Bot,
  Boxes,
  Building2,
  CircleUserRound,
  ClipboardCheck,
  Coins,
  Database,
  Eraser,
  FileClock,
  FileSearch,
  FlaskConical,
  Gauge,
  Globe,
  Goal,
  Handshake,
  Inbox,
  KeyRound,
  LayoutDashboard,
  LifeBuoy,
  ListChecks,
  ListTree,
  MailCheck,
  Map as MapIcon,
  MessageSquareQuote,
  Mic,
  MousePointerClick,
  Network,
  PackagePlus,
  PhoneCall,
  Plug,
  Radar,
  Radio,
  Receipt,
  ScanSearch,
  Send,
  Share2,
  ShieldAlert,
  ShieldCheck,
  ShieldPlus,
  Smartphone,
  Sparkles,
  SquareStack,
  Stethoscope,
  Tags,
  Target,
  TextQuote,
  Timer,
  UserCog,
  UserRoundSearch,
  Users,
  UsersRound,
  Video,
  Waypoints,
  Workflow,
  type LucideIcon,
} from "lucide-react";

/**
 * The settings menu: groups shown on the left of every settings page and as
 * tiles on the "All settings" hub. Items tied to a plan feature show a lock
 * when the plan doesn't include it; items with a permission are hidden from
 * roles without it.
 */

export type SettingsItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  description: string;
  permission?: string;
  /** Any one of these permissions is enough. */
  anyPermission?: string[];
  feature?: string;
  /** Extra paths that belong to this item (detail pages). */
  match?: string[];
};

export type SettingsGroup = {
  key: string;
  label: string;
  items: SettingsItem[];
};

export const SETTINGS_MENU: SettingsGroup[] = [
  {
    key: "personal",
    label: "Personal",
    items: [
      { label: "Profile", href: "/settings/profile", icon: CircleUserRound, description: "Your name, title, phone and time zone." },
      { label: "Password & security", href: "/settings/security", icon: ShieldCheck, description: "Your password, two-step verification and signed-in devices." },
      { label: "Mobile app", href: "/settings/mobile", icon: Smartphone, description: "Install AIVA on your phone's home screen.", feature: "mobile_app" },
    ],
  },
  {
    key: "billing",
    label: "Plan & billing",
    items: [
      { label: "Plan overview", href: "/settings/billing", icon: LayoutDashboard, description: "Your plan, users and what's included.", match: ["/settings/billing/checkout"] },
      { label: "Product add-ons", href: "/settings/billing/add-ons", icon: PackagePlus, description: "Credit packs, AI words, conversation intelligence and advanced data." },
      { label: "Billing", href: "/settings/billing/invoices", icon: Receipt, description: "Invoices, payment method and billing history." },
    ],
  },
  {
    key: "credits",
    label: "Credits & activity",
    items: [
      { label: "Credit usage", href: "/settings/credits", icon: Coins, description: "Credits used this month by person and feature." },
      { label: "Data requests", href: "/settings/data-requests", icon: UserRoundSearch, description: "Ask the data team for a missing email, phone or title." },
      { label: "AI word usage", href: "/settings/ai-usage", icon: Sparkles, description: "AI words used for drafts, summaries, agents and notes." },
    ],
  },
  {
    key: "users",
    label: "Users & teams",
    items: [
      { label: "Users", href: "/settings/users", icon: Users, description: "Invite people, change roles, deactivate access.", permission: "admin.users" },
      { label: "Teams", href: "/settings/teams", icon: UsersRound, description: "Teams and their managers." },
      { label: "Permission profiles", href: "/settings/roles", icon: UserCog, description: "Built-in and custom roles and what each can do.", permission: "admin.users" },
      { label: "Territories", href: "/settings/territories", icon: MapIcon, description: "Route accounts and leads to the right people by region, industry or size.", feature: "territories" },
      { label: "License settings", href: "/settings/licenses", icon: BadgeCheck, description: "Seats, default role and per-person credit and AI limits.", permission: "admin.users" },
      { label: "Workspace details", href: "/settings/organization", icon: Building2, description: "Name, currency, time zone, fiscal year, session length." },
      { label: "Team sharing & defaults", href: "/settings/sharing", icon: Share2, description: "What's shared by default and new-record defaults." },
      { label: "System activity log", href: "/settings/audit", icon: FileClock, description: "Every change and sign-in, with before and after.", permission: "admin.audit" },
      { label: "Security", href: "/settings/workspace-security", icon: ShieldAlert, description: "Two-step verification, IP allowlist, email domains and passwords.", permission: "admin.settings" },
      { label: "Single sign-on", href: "/settings/sso", icon: KeyRound, description: "Okta, Entra ID or Google Workspace sign-in for your team.", permission: "admin.settings", feature: "sso" },
      { label: "Hierarchy & visibility", href: "/settings/hierarchy", icon: Network, description: "Who reports to whom and who sees which records.", feature: "reporting_hierarchy" },
      { label: "HIPAA compliance", href: "/settings/compliance", icon: ShieldPlus, description: "Enforced two-step sign-in, short sessions, view auditing.", permission: "admin.settings", feature: "hipaa" },
      { label: "Support access", href: "/settings/support-access", icon: LifeBuoy, description: "Let AIVA support into your workspace for a limited time.", permission: "admin.settings" },
    ],
  },
  {
    key: "integrations",
    label: "Integrations",
    items: [
      { label: "All integrations", href: "/integrations", icon: Plug, description: "Gmail, Outlook, calendars, Slack, Teams, WhatsApp, Zoom, Drive, DocuSign, Zapier and more." },
      { label: "Other connections", href: "/settings/integrations", icon: Plug, description: "App-password mailboxes, the calendar feed, Google Ads and QuickBooks." },
      { label: "MCP", href: "/settings/mcp", icon: Bot, description: "Connect Claude and other AI assistants to your CRM.", feature: "api_access" },
      { label: "API keys", href: "/settings/api-keys", icon: KeyRound, description: "Connect other systems to the AIVA API.", feature: "api_access" },
      { label: "Developer sandbox", href: "/settings/sandbox", icon: FlaskConical, description: "Test changes in a copy of your setup, then deploy.", permission: "admin.settings", feature: "sandbox" },
    ],
  },
  {
    key: "outbound",
    label: "Outbound",
    items: [
      { label: "Overview", href: "/settings/outbound", icon: Send, description: "Sending health across domains, mailboxes and sequences." },
      { label: "Domains", href: "/settings/outbound/domains", icon: Globe, description: "SPF, DKIM, DMARC and tracking checks for sending domains." },
      { label: "Mailboxes", href: "/settings/outbound/mailboxes", icon: Inbox, description: "Connected mailboxes, daily limits and bounce rates." },
      { label: "Diagnose", href: "/settings/outbound/diagnose", icon: Stethoscope, description: "Find what's hurting deliverability and how to fix it." },
      { label: "Sending protection", href: "/settings/outbound/protection", icon: ShieldCheck, description: "Sending window, daily and hourly caps, bounce pause." },
      { label: "Sequences", href: "/settings/outbound/sequences", icon: SquareStack, description: "When sequences stop, threading and re-enrollment rules." },
      { label: "Tracking", href: "/settings/outbound/tracking", icon: MailCheck, description: "Open and click tracking, unsubscribe link, tracking domain." },
      { label: "Dialer", href: "/settings/outbound/dialer", icon: PhoneCall, description: "Call outcomes, notes, follow-ups and Twilio calling." },
      { label: "Prospecting configuration", href: "/settings/outbound/prospecting", icon: Radar, description: "Duplicates, stages people move through, do-not-contact checks." },
      { label: "Snippets", href: "/settings/outbound/snippets", icon: TextQuote, description: "Reusable text with merge fields for emails and steps." },
    ],
  },
  {
    key: "data",
    label: "Data management",
    items: [
      { label: "Waterfall enrichment", href: "/settings/enrichment", icon: Waypoints, description: "Fill missing data from several providers in your order.", feature: "data_enrichment" },
      { label: "Contact fields & stages", href: "/settings/contact-fields", icon: CircleUserRound, description: "Contact stages and custom contact fields." },
      { label: "Account fields & stages", href: "/settings/account-fields", icon: Building2, description: "Account stages and custom account fields." },
      { label: "Deal fields & stages", href: "/settings/deal-fields", icon: Handshake, description: "Pipelines, deal stages and custom deal fields." },
      { label: "Fields & modules", href: "/settings/fields", icon: Boxes, description: "Custom fields on any record and your own record types.", feature: "custom_modules" },
      { label: "Global picklists", href: "/settings/picklists", icon: Tags, description: "Shared value lists that several fields can use." },
      { label: "Goals", href: "/settings/goals", icon: Goal, description: "Targets for people and teams with live progress.", feature: "goals" },
      { label: "Imports & exports", href: "/settings/data", icon: Database, description: "Bring in CSV files and download any list.", feature: "data_import_export" },
      { label: "Removal requests", href: "/settings/removal-requests", icon: Eraser, description: "GDPR and CCPA erasure requests and the do-not-contact list.", anyPermission: ["data.privacy"] },
    ],
  },
  {
    key: "automation",
    label: "Automation",
    items: [
      { label: "Workflows", href: "/workflows", icon: Workflow, description: "When-then automation rules." },
      { label: "Stage playbooks", href: "/settings/processes", icon: ListChecks, description: "Required fields and checklists for each pipeline stage.", feature: "process_management" },
      { label: "Approval rules", href: "/settings/approvals", icon: ClipboardCheck, description: "Sign-off for big deals and discounted quotes.", feature: "approvals" },
      { label: "Custom buttons", href: "/settings/buttons", icon: MousePointerClick, description: "Buttons on records that open links, call webhooks or run functions.", feature: "custom_buttons" },
      { label: "SLA policies", href: "/settings/sla", icon: Timer, description: "Response and resolution targets for cases.", permission: "service.cases" },
    ],
  },
  {
    key: "icp",
    label: "Ideal customer profile",
    items: [
      { label: "Personas", href: "/settings/personas", icon: Target, description: "The buyers you sell to, matched by job title.", feature: "buying_intent" },
      { label: "Buying intent", href: "/settings/intent", icon: Activity, description: "Topics that show an account is in market.", feature: "buying_intent" },
      { label: "Website visitors", href: "/settings/website", icon: ScanSearch, description: "Tracking script, identification and exclusions.", feature: "website_visits" },
      { label: "Signals", href: "/settings/signals", icon: Radio, description: "Alerts when something happens that's worth acting on.", feature: "buying_intent" },
      { label: "Scoring", href: "/settings/lead-scoring", icon: Gauge, description: "Points rules for leads, contacts and accounts." },
      { label: "AI Context Center", href: "/settings/ai-context", icon: FileSearch, description: "What AIVA's AI knows about your company and how it writes." },
    ],
  },
  {
    key: "conversations",
    label: "Meetings & conversations",
    items: [
      { label: "Recording configuration", href: "/settings/recording", icon: Mic, description: "What gets captured, consent, transcription and retention.", feature: "conversation_intelligence" },
      { label: "Team permissions", href: "/settings/conversation-permissions", icon: Users, description: "Who can see, download and delete conversations.", feature: "conversation_intelligence" },
      { label: "Trackers", href: "/settings/trackers", icon: ListTree, description: "Words and phrases to spot in every call.", feature: "conversation_intelligence" },
      { label: "Scorecards", href: "/settings/scorecards", icon: ClipboardCheck, description: "Coaching scorecards for reviewing calls.", feature: "conversation_intelligence" },
      { label: "Custom field prompts", href: "/settings/field-prompts", icon: MessageSquareQuote, description: "Questions AIVA answers from calls to fill CRM fields.", feature: "conversation_intelligence" },
      { label: "Team meetings", href: "/settings/team-meetings", icon: Video, description: "Internal meetings: keep or skip, and who gets notes.", feature: "conversation_intelligence" },
    ],
  },
];

export function allowed(item: SettingsItem, can: (permission: string) => boolean) {
  if (item.permission && !can(item.permission)) return false;
  if (item.anyPermission && !item.anyPermission.some((p) => can(p))) return false;
  return true;
}

function under(pathname: string, prefix: string) {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** The menu item for a path: the longest matching href wins (so /settings/billing/add-ons beats /settings/billing). */
export function activeSettingsItem(pathname: string) {
  let best: { group: SettingsGroup; item: SettingsItem; length: number } | null = null;
  for (const group of SETTINGS_MENU) {
    for (const item of group.items) {
      for (const prefix of [item.href, ...(item.match ?? [])]) {
        if (under(pathname, prefix) && (!best || prefix.length > best.length)) best = { group, item, length: prefix.length };
      }
    }
  }
  return best;
}

/** Every settings path, for the top bar's "Settings" tab. */
export const SETTINGS_PATHS = SETTINGS_MENU.flatMap((group) => group.items.map((item) => item.href)).filter((href) => href.startsWith("/settings"));
