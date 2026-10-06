export type LifecycleStage =
  | "prospect"
  | "lead"
  | "customer"
  | "partner"
  | "inactive"
  | "churned";


export interface Account {
  id: string;
  organization_id: string;

  name: string;
  domain: string | null;
  website: string | null;
  industry: string | null;
  phone: string | null;

  lifecycle_stage: LifecycleStage;

  employee_count: number | null;
  annual_revenue: string | null;

  description: string | null;

  billing_address_line1: string | null;
  billing_address_line2: string | null;
  billing_city: string | null;
  billing_state: string | null;
  billing_postal_code: string | null;
  billing_country: string | null;

  owner_user_id: string | null;

  parent_account_id: string | null;

  is_archived: boolean;
  archived_at: string | null;

  created_at: string;
  updated_at: string;
}


export interface AccountCreatePayload {
  name: string;

  domain?: string | null;
  website?: string | null;
  industry?: string | null;
  phone?: string | null;

  lifecycle_stage?: LifecycleStage;

  employee_count?: number | null;
  annual_revenue?: number | null;

  description?: string | null;

  billing_address_line1?: string | null;
  billing_address_line2?: string | null;
  billing_city?: string | null;
  billing_state?: string | null;
  billing_postal_code?: string | null;
  billing_country?: string | null;

  owner_user_id?: string | null;

  parent_account_id?: string | null;
}


export interface AccountListOptions {
  skip?: number;
  limit?: number;
  includeArchived?: boolean;
  parentAccountId?: string | null;
}


export interface AccountDuplicateMatch {
  account: Account;

  match_reasons: string[];

  confidence:
    | "high"
    | "medium";
}


export interface AccountDuplicateCheckResponse {
  has_duplicates: boolean;

  matches: AccountDuplicateMatch[];
}


export interface AccountDuplicateCheckPayload {
  name: string;
  domain?: string | null;
  excludeAccountId?: string | null;
}