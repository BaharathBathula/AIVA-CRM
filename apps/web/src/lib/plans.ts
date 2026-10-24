/**
 * Plan names, prices and which plan each feature belongs to. Mirrors
 * apps/api/app/core/plans.py; the API is the source of truth and enforces it.
 */

export type PlanKey = "trial" | "starter" | "pro" | "enterprise";
export type PaidPlan = Exclude<PlanKey, "trial">;

export const PLAN_NAMES: Record<PlanKey, string> = {
  trial: "Free trial",
  starter: "Starter",
  pro: "Pro",
  enterprise: "Enterprise",
};

export const PLAN_PRICES: Record<PaidPlan, number> = { starter: 19, pro: 29, enterprise: 59 };

export const TRIAL_DAYS = 7;

export const FEATURE_PLAN: Record<string, { label: string; plan: PaidPlan }> = {
  contact_management: { label: "Contact management", plan: "starter" },
  follow_up_reminders: { label: "Follow-up reminders", plan: "starter" },
  workflow_automation: { label: "Workflow automation", plan: "starter" },
  email_templates: { label: "Custom email templates", plan: "starter" },
  tasks_meetings_calls: { label: "Tasks, meetings & calls", plan: "starter" },
  data_import_export: { label: "Data import and export", plan: "starter" },
  standard_reports: { label: "Standard reports", plan: "starter" },
  api_access: { label: "APIs", plan: "starter" },
  mobile_app: { label: "Mobile apps", plan: "starter" },
  mass_email: { label: "Email integration & mass emails", plan: "starter" },
  calling: { label: "Built-in calling", plan: "starter" },
  multiple_pipelines: { label: "Multiple sales pipelines", plan: "starter" },
  calendar_sync: { label: "Calendar integration", plan: "starter" },
  web_forms: { label: "Data capture via forms", plan: "starter" },
  forecasting: { label: "Sales forecasting", plan: "starter" },
  data_enrichment: { label: "Data enrichment", plan: "starter" },
  custom_modules: { label: "Custom modules", plan: "starter" },
  hipaa: { label: "HIPAA compliance", plan: "starter" },
  mailbox_sync: { label: "Gmail and Outlook integration", plan: "starter" },
  chat_integrations: { label: "Slack, Zoom, Teams integration", plan: "starter" },
  documents: { label: "Documents", plan: "starter" },
  team_chat: { label: "Team chat", plan: "starter" },
  service_desk: { label: "Cases, SLAs and knowledge base", plan: "starter" },
  lead_scoring: { label: "Lead scoring", plan: "starter" },
  sequences: { label: "Sales sequences", plan: "starter" },
  goals: { label: "Goals", plan: "starter" },
  ai_agents: { label: "AI agents", plan: "pro" },
  territories: { label: "Territories", plan: "pro" },
  buying_intent: { label: "Personas, intent and signals", plan: "pro" },
  waterfall_enrichment: { label: "Waterfall enrichment", plan: "pro" },
  process_management: { label: "Process management", plan: "pro" },
  inventory: { label: "Inventory management", plan: "pro" },
  price_books: { label: "Price books", plan: "pro" },
  sales_orders: { label: "Sales orders", plan: "pro" },
  purchase_orders: { label: "Vendors and purchase orders", plan: "pro" },
  services: { label: "Services and appointments", plan: "pro" },
  projects: { label: "Projects", plan: "pro" },
  social: { label: "Social publishing", plan: "pro" },
  website_visits: { label: "Website visits", plan: "pro" },
  predictive_intelligence: { label: "Predictive intelligence", plan: "pro" },
  unlimited_reports: { label: "Unlimited reports", plan: "pro" },
  cpq: { label: "Configure, Price, Quote (CPQ)", plan: "pro" },
  custom_portals: { label: "Custom portals", plan: "pro" },
  customer_journeys: { label: "Customer journeys", plan: "pro" },
  web_to_case: { label: "Web-to-case forms", plan: "pro" },
  customer_portal: { label: "Customer portals", plan: "pro" },
  google_ads: { label: "Google Ads integration", plan: "pro" },
  multi_team: { label: "Multi-team management", plan: "enterprise" },
  reporting_hierarchy: { label: "Reporting hierarchy", plan: "enterprise" },
  custom_buttons: { label: "Custom buttons and functions", plan: "enterprise" },
  extended_fields: { label: "Extended field types", plan: "enterprise" },
  sequential_forms: { label: "Sequential data collection", plan: "enterprise" },
  approvals: { label: "Approval process", plan: "enterprise" },
  abm: { label: "Account Based Marketing", plan: "enterprise" },
  sandbox: { label: "Developer sandbox", plan: "enterprise" },
  field_encryption: { label: "Field-level encryption", plan: "enterprise" },
  extended_ai: { label: "Extended AI capabilities", plan: "enterprise" },
  quickbooks: { label: "QuickBooks integration", plan: "enterprise" },
  sso: { label: "Single sign-on", plan: "enterprise" },
  conversation_intelligence: { label: "Conversation intelligence", plan: "enterprise" },
};

export function featurePlan(feature: string) {
  return FEATURE_PLAN[feature] ?? { label: feature, plan: "enterprise" as PaidPlan };
}

/** Features a workspace can also get as a product add-on (Settings → Product add-ons). */
export const ADDON_FOR_FEATURE: Record<string, string> = {
  conversation_intelligence: "Conversation intelligence",
  waterfall_enrichment: "Advanced data",
  buying_intent: "Advanced data",
};
