import {
  aivaRequest,
} from "@/lib/api";

import type {
  OrganizationMember,
} from "@/types/organization-member";


export function getOrganizationMembers():
  Promise<OrganizationMember[]> {
  return aivaRequest<
    OrganizationMember[]
  >(
    "/organization-members"
  );
}