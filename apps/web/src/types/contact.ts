export interface Contact {
  id: string;
  organization_id: string;
  account_id: string | null;
  owner_user_id: string | null;

  first_name: string;
  last_name: string;

  email: string | null;
  phone: string | null;
  mobile: string | null;

  job_title: string | null;
  department: string | null;

  segment: string | null;
  tags: string[];
  linkedin_url: string | null;

  is_primary: boolean;
  is_active: boolean;

  is_archived: boolean;
  archived_at: string | null;

  created_at: string;
  updated_at: string;
}


export interface ContactCreatePayload {
  account_id?: string | null;
  owner_user_id?: string | null;

  first_name: string;
  last_name: string;

  email?: string | null;
  phone?: string | null;
  mobile?: string | null;

  job_title?: string | null;
  department?: string | null;

  segment?: string | null;
  tags?: string[];
  linkedin_url?: string | null;

  is_primary?: boolean;
}


export interface ContactUpdatePayload {
  account_id?: string | null;
  owner_user_id?: string | null;

  first_name?: string;
  last_name?: string;

  email?: string | null;
  phone?: string | null;
  mobile?: string | null;

  job_title?: string | null;
  department?: string | null;

  segment?: string | null;
  tags?: string[];
  linkedin_url?: string | null;

  is_primary?: boolean;
  is_active?: boolean;
}

export interface ContactDuplicateMatch {
  contact: Contact;
  confidence: string;
  reasons: string[];
}


export interface ContactDuplicateCheckResponse {
  has_duplicates: boolean;
  matches: ContactDuplicateMatch[];
}
