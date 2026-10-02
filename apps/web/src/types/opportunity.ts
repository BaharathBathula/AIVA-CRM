export interface Opportunity {
  id: string;
  organization_id: string;

  name: string;

  account_id: string;
  primary_contact_id: string | null;

  pipeline_id: string;
  stage_id: string;

  owner_user_id: string | null;

  amount: string | null;
  currency: string;

  probability: number;

  expected_close_date: string | null;
  closed_at: string | null;

  description: string | null;

  created_at: string;
  updated_at: string;
}


export interface OpportunityCreatePayload {
  name: string;

  account_id: string;

  primary_contact_id?: string | null;

  pipeline_id?: string | null;
  stage_id?: string | null;

  owner_user_id?: string | null;

  amount?: number | null;
  currency?: string;

  expected_close_date?: string | null;

  description?: string | null;
}


export interface OpportunityUpdatePayload {
  name?: string;

  account_id?: string;

  primary_contact_id?: string | null;

  pipeline_id?: string;
  stage_id?: string;

  owner_user_id?: string | null;

  amount?: number | null;
  currency?: string;

  probability?: number;

  expected_close_date?: string | null;

  description?: string | null;
}
