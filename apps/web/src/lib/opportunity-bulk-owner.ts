import {
  aivaRequest,
} from "@/lib/api";

import {
  getAivaAccessToken,
  getAivaSession,
} from "@/lib/auth";

import {
  getOrganizationMembers,
} from "@/lib/organization-members";

import type {
  BulkOwnerOption,
} from "@/components/opportunities/opportunity-bulk-owner-dialog";


export interface BulkOwnerUpdateResult {
  updated_count: number;
  updated_ids: string[];
  requested_count: number;
  owner_user_id: string;
}


export async function getEligibleOpportunityOwners():
  Promise<BulkOwnerOption[]> {
  const members = await getOrganizationMembers();

  return members
    .filter((member) => member.is_active)
    .map((member) => ({
      id: member.user_id,
      name: member.full_name,
      email: member.email,
    }));
}


export async function bulkChangeOpportunityOwner(
  opportunityIds: string[],
  ownerUserId: string
): Promise<BulkOwnerUpdateResult> {
  const token = getAivaAccessToken();
  const session = getAivaSession();

  if (!token || !session) {
    throw new Error(
      "Your login session has expired. Please sign in again."
    );
  }

  if (!["owner", "admin"].includes(session.role)) {
    throw new Error(
      "Only organization owners and administrators can change opportunity owners."
    );
  }

  if (opportunityIds.length === 0) {
    throw new Error("Select at least one opportunity.");
  }

  if (opportunityIds.length > 100) {
    throw new Error(
      "You can update a maximum of 100 opportunities at once."
    );
  }

  if (new Set(opportunityIds).size !== opportunityIds.length) {
    throw new Error(
      "Duplicate opportunity selections are not allowed."
    );
  }

  if (!ownerUserId) {
    throw new Error("Select a new opportunity owner.");
  }

  return aivaRequest<BulkOwnerUpdateResult>(
    "/opportunities/bulk/owner",
    {
      method: "PATCH",
      body: JSON.stringify({
        opportunity_ids: opportunityIds,
        owner_user_id: ownerUserId,
      }),
    }
  );
}
