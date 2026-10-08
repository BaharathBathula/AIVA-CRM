export type OpportunityType =
  | "new_business"
  | "renewal"
  | "upsell"
  | "cross_sell"
  | "expansion";

export type OpportunityPriority =
  | "low"
  | "medium"
  | "high"
  | "critical";

export type ForecastCategory =
  | "pipeline"
  | "best_case"
  | "commit"
  | "closed"
  | "omitted";


export interface Opportunity {
  id: string;
  organization_id: string;

  name: string;
  description: string | null;

  // CRM relationships
  account_id: string;
  primary_contact_id: string | null;
  owner_user_id: string | null;

  // Pipeline
  pipeline_id: string;
  stage_id: string;
  stage_entered_at: string;

  // Commercial value
  amount: string | null;
  currency: string;
  probability: number;
  weighted_amount: string | number | null;

  // Classification
  opportunity_type: OpportunityType;
  lead_source: string | null;
  priority: OpportunityPriority;
  forecast_category: ForecastCategory;

  // Sales execution
  next_step: string | null;
  expected_close_date: string | null;
  closed_at: string | null;

  // Win / loss
  loss_reason: string | null;
  competitor: string | null;

  // Audit
  created_at: string;
  updated_at: string;
}


export interface OpportunityCreatePayload {
  name: string;
  account_id: string;

  primary_contact_id?: string | null;
  owner_user_id?: string | null;

  pipeline_id?: string | null;
  stage_id?: string | null;

  amount?: number | null;
  currency?: string;

  probability?: number | null;

  opportunity_type?: OpportunityType;
  lead_source?: string | null;
  priority?: OpportunityPriority;
  forecast_category?: ForecastCategory;

  next_step?: string | null;
  expected_close_date?: string | null;

  loss_reason?: string | null;
  competitor?: string | null;

  description?: string | null;
}


export interface OpportunityUpdatePayload {
  name?: string;

  account_id?: string;
  primary_contact_id?: string | null;
  owner_user_id?: string | null;

  pipeline_id?: string;
  stage_id?: string;

  amount?: number | null;
  currency?: string;

  probability?: number | null;

  opportunity_type?: OpportunityType | null;
  lead_source?: string | null;
  priority?: OpportunityPriority | null;
  forecast_category?: ForecastCategory | null;

  next_step?: string | null;
  expected_close_date?: string | null;

  loss_reason?: string | null;
  competitor?: string | null;

  description?: string | null;
}