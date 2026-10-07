export interface OrganizationMember {
  user_id: string;

  full_name: string;
  email: string;

  role: string;

  is_active: boolean;
}