export type LeadStatus =
  | "new"
  | "contacted"
  | "qualified"
  | "unqualified"
  | "nurture"
  | "converted";


export type EditableLeadStatus =
  Exclude<
    LeadStatus,
    "converted"
  >;


export interface Lead {
  id: string;
  organization_id: string;

  first_name: string;
  last_name: string;

  email: string | null;
  phone: string | null;

  company_name: string | null;
  job_title: string | null;

  source: string;
  status: LeadStatus;
  score: number;

  notes: string | null;

  owner_user_id: string | null;

  converted_at: string | null;
  converted_account_id: string | null;
  converted_contact_id: string | null;
  converted_opportunity_id: string | null;

  created_at: string;
  updated_at: string;
}


export interface LeadCreatePayload {
  first_name: string;
  last_name: string;

  email?: string | null;
  phone?: string | null;

  company_name?: string | null;
  job_title?: string | null;

  source?: string;
  status?: EditableLeadStatus;
  score?: number;

  notes?: string | null;

  owner_user_id?: string | null;
}
