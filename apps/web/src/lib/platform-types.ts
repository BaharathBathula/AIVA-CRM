/** Response types for the platform APIs (admin, sales, service, analytics). */

export interface Member {
  id: string;
  full_name: string;
  email: string;
  role: string;
  team_id: string | null;
  team_name: string | null;
}

export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  job_title: string | null;
  role: string;
  team_id: string | null;
  team_name: string | null;
  status: "active" | "invited" | "deactivated";
  mfa_enabled: boolean;
  last_login_at: string | null;
  locked: boolean;
  active_sessions: number;
  joined_at: string;
  is_you: boolean;
}

export interface Role {
  key: string;
  name: string;
  description: string | null;
  permissions: string[];
  is_system: boolean;
  member_count: number;
}

export interface PermissionDef {
  key: string;
  label: string;
  group: string;
  description: string;
}

export interface Team {
  id: string;
  name: string;
  description: string | null;
  manager_user_id: string | null;
  manager_name: string | null;
  members: { id: string; full_name: string }[];
}

export interface AuditLog {
  id: string;
  created_at: string;
  actor_user_id: string | null;
  actor_name: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  entity_label: string | null;
  changes: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
}

export interface Paged<T> {
  total: number;
  page: number;
  page_size: number;
  items: T[];
}

export interface OrganizationSettings {
  currency?: string;
  timezone?: string;
  date_format?: string;
  fiscal_year_start_month?: number;
  session_timeout_minutes?: number;
  [key: string]: unknown;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  settings: OrganizationSettings;
  created_at: string;
  member_count: number;
}

export interface SessionRow {
  id: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  current: boolean;
}

// ---------------------------------------------------------------- Sales

export interface Product {
  id: string;
  name: string;
  sku: string | null;
  description: string | null;
  family: string | null;
  unit_price: number;
  currency: string;
  billing_frequency: "one_time" | "monthly" | "quarterly" | "annual";
  tax_rate: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type QuoteStatus = "draft" | "sent" | "accepted" | "declined" | "expired";

export interface QuoteSummary {
  id: string;
  number: number;
  display_number: string;
  name: string;
  status: QuoteStatus;
  opportunity_id: string | null;
  opportunity_name: string | null;
  account_id: string | null;
  account_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  valid_until: string | null;
  currency: string;
  subtotal: number;
  discount_total: number;
  tax_total: number;
  total: number;
  line_count: number;
  sent_at: string | null;
  accepted_at: string | null;
  declined_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface QuoteLine {
  id?: string;
  product_id: string | null;
  name: string;
  description: string | null;
  quantity: number;
  unit_price: number;
  discount_percent: number;
  tax_percent: number;
  line_total?: number;
}

export interface Quote extends QuoteSummary {
  terms: string | null;
  notes: string | null;
  line_items: QuoteLine[];
  price_book_id?: string | null;
}

export interface ScoringRule {
  id: string;
  name: string;
  field: string;
  operator: string;
  value: string | null;
  points: number;
  is_active: boolean;
}

export interface ScoringSummary {
  leads: number;
  average: number;
  distribution: Record<"A" | "B" | "C" | "D", number>;
}

export interface ForecastRow {
  user_id: string;
  name: string;
  quota: number;
  closed_won: number;
  commit: number;
  best_case: number;
  pipeline: number;
  omitted: number;
  open_count: number;
  projected: number;
  attainment: number;
}

export interface Forecast {
  period: string;
  current_period: string;
  start: string;
  end: string;
  periods: string[];
  can_manage_quotas: boolean;
  totals: Omit<ForecastRow, "user_id" | "name" | "open_count"> & { gap: number };
  rows: ForecastRow[];
  deals: {
    id: string;
    name: string;
    owner_name: string | null;
    amount: number;
    probability: number;
    forecast_category: string;
    expected_close_date: string | null;
  }[];
}

// ---------------------------------------------------------------- Service

export type CasePriority = "low" | "medium" | "high" | "urgent";
export type CaseStatus = "new" | "open" | "pending" | "on_hold" | "resolved" | "closed";

export interface SlaInfo {
  state: "on_track" | "at_risk" | "breached" | "met" | "missed" | "none";
  target: "first_response" | "resolution" | null;
  due_at: string | null;
  minutes_remaining: number | null;
}

export interface CaseSummary {
  id: string;
  number: number;
  display_number: string;
  subject: string;
  status: CaseStatus;
  priority: CasePriority;
  channel: string;
  category: string | null;
  account_id: string | null;
  account_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  assigned_to_user_id: string | null;
  assigned_to_name: string | null;
  team_id: string | null;
  team_name: string | null;
  tags: string[];
  sla: SlaInfo;
  first_response_due_at: string | null;
  resolution_due_at: string | null;
  first_responded_at: string | null;
  resolved_at: string | null;
  closed_at: string | null;
  satisfaction_rating: number | null;
  created_at: string;
  updated_at: string;
}

export interface CaseComment {
  id: string;
  body: string;
  is_internal: boolean;
  author_user_id: string | null;
  author_name: string | null;
  created_at: string;
}

export interface SlaPolicy {
  id: string;
  name: string;
  priority: CasePriority;
  first_response_minutes: number;
  resolution_minutes: number;
  is_active: boolean;
}

export interface ArticleSummary {
  id: string;
  title: string;
  slug: string;
  summary: string | null;
  category: string | null;
  status: "draft" | "published" | "archived";
  tags: string[];
  author_name: string | null;
  view_count: number;
  helpful_count: number;
  not_helpful_count: number;
  published_at: string | null;
  updated_at: string;
  created_at: string;
}

export interface Article extends ArticleSummary {
  body: string;
}

export interface CaseDetail extends CaseSummary {
  description: string | null;
  created_by_name: string | null;
  sla_policy: SlaPolicy | null;
  comments: CaseComment[];
  linked_articles: ArticleSummary[];
}

export interface CaseStats {
  open: number;
  mine: number;
  unassigned: number;
  breached: number;
  resolved_today: number;
  avg_first_response_hours: number | null;
  avg_resolution_hours: number | null;
  csat: number | null;
  sla_met_percent: number | null;
  open_by_priority: Record<CasePriority, number>;
}

// ---------------------------------------------------------------- Analytics

export interface FieldDef {
  key: string;
  label: string;
  type: "string" | "number" | "currency" | "date" | "datetime" | "bool" | "enum" | "ref" | "user";
  options: string[];
  ref: string | null;
  groupable: boolean;
  aggregatable: boolean;
  updatable: boolean;
  virtual: boolean;
}

export interface ObjectDef {
  key: string;
  label: string;
  singular: string;
  fields: FieldDef[];
}

export interface Catalog {
  objects: ObjectDef[];
  operators: string[];
  periods: string[];
  triggers: string[];
  actions: string[];
}

export interface ReportFilter {
  field: string;
  operator: string;
  value: unknown;
}

export interface ReportSpec {
  object_type: string;
  columns: string[];
  filters: ReportFilter[];
  group_by: string | null;
  date_granularity: string | null;
  aggregate: "count" | "sum" | "avg" | "min" | "max";
  aggregate_field: string | null;
  sort_field: string | null;
  sort_direction: "asc" | "desc";
  row_limit: number;
  chart_type: "table" | "bar" | "line" | "donut" | "metric";
}

export interface ReportDef extends ReportSpec {
  id: string;
  name: string;
  description: string | null;
  folder: string | null;
  is_shared: boolean;
  owner_user_id: string | null;
  owner_name: string | null;
  updated_at: string;
  created_at: string;
}

export interface ReportGroup {
  key: string | null;
  label: string;
  value: number;
  count: number;
}

export interface ReportResult {
  object_type: string;
  object_label: string;
  total_count: number;
  aggregate: string;
  aggregate_field: FieldDef | null;
  summary: { value: number; count: number };
  groups: ReportGroup[];
  group_by: (FieldDef & { granularity?: string | null }) | null;
  columns: FieldDef[];
  rows: { id: string; link: string | null; values: Record<string, unknown>; raw: Record<string, unknown> }[];
}

export interface DashboardWidget {
  id: string;
  report_id: string;
  title?: string | null;
  size: "small" | "medium" | "large";
  report?: ReportDef;
  result?: ReportResult;
  error?: string;
}

export interface DashboardDef {
  id: string;
  name: string;
  description: string | null;
  widgets: DashboardWidget[];
  is_shared: boolean;
  owner_user_id: string | null;
  updated_at: string;
}

export interface WorkflowCondition {
  field: string;
  operator: string;
  value: string | null;
}

export interface WorkflowAction {
  type: "create_task" | "update_field" | "notify" | "assign_owner";
  params: Record<string, unknown>;
}

export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  object_type: string;
  trigger_event: string;
  trigger_field: string | null;
  conditions: WorkflowCondition[];
  actions: WorkflowAction[];
  is_active: boolean;
  run_count: number;
  last_run_at: string | null;
  error_count: number;
  updated_at: string;
}

export interface WorkflowRun {
  id: string;
  entity_type: string;
  entity_id: string | null;
  entity_label: string | null;
  link: string | null;
  status: "success" | "skipped" | "error" | string;
  message: string | null;
  created_at: string;
}

export interface NotificationItem {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  is_read: boolean;
  created_at: string;
}

export interface SearchGroup {
  type: string;
  label: string;
  items: { id: string; title: string; subtitle: string | null; href: string }[];
}

export interface HomeSummary {
  kpis: {
    open_pipeline: number;
    weighted_pipeline: number;
    open_opportunities: number;
    won_this_quarter: number;
    won_count_quarter: number;
    win_rate: number | null;
    new_leads_30d: number;
    avg_lead_score: number | null;
    open_cases: number;
    sla_breaches: number;
    overdue_tasks: number;
    my_overdue_tasks: number;
    quotes_out: number;
    quotes_out_value: number;
  };
  pipeline_by_stage: { stage: string; position: number; count: number; amount: number }[];
  closing_soon: {
    id: string;
    name: string;
    account_name: string | null;
    amount: number;
    expected_close_date: string | null;
    probability: number | null;
  }[];
  my_tasks: {
    id: string;
    title: string;
    priority: string;
    status: string;
    due_at: string | null;
    overdue: boolean;
    account_name: string | null;
  }[];
  hot_leads: { id: string; name: string; company_name: string | null; score: number; status: string }[];
  recent_activity: {
    id: string;
    activity_type: string;
    subject: string;
    occurred_at: string;
    account_name: string | null;
    author_name: string | null;
  }[];
}
