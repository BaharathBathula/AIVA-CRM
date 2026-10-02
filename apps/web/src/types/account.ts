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
}
