export interface Contact {
  id: string;
  organization_id: string;
  account_id: string | null;

  first_name: string;
  last_name: string;

  email: string | null;
  phone: string | null;
  mobile: string | null;

  job_title: string | null;
  department: string | null;
  linkedin_url: string | null;

  is_primary: boolean;
  is_active: boolean;

  created_at: string;
  updated_at: string;
}


export interface ContactCreatePayload {
  account_id?: string | null;

  first_name: string;
  last_name: string;

  email?: string | null;
  phone?: string | null;
  mobile?: string | null;

  job_title?: string | null;
  department?: string | null;
  linkedin_url?: string | null;

  is_primary?: boolean;
}
