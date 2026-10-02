import {
  aivaRequest,
} from "@/lib/api";

import type {
  Opportunity,
  OpportunityCreatePayload,
  OpportunityUpdatePayload,
} from "@/types/opportunity";


export interface OpportunityFilters {
  accountId?: string;
  pipelineId?: string;
  stageId?: string;
  ownerUserId?: string;
  search?: string;
}


export function getOpportunities(
  filters: OpportunityFilters = {}
): Promise<Opportunity[]> {
  const params =
    new URLSearchParams();

  if (filters.accountId) {
    params.set(
      "account_id",
      filters.accountId
    );
  }

  if (filters.pipelineId) {
    params.set(
      "pipeline_id",
      filters.pipelineId
    );
  }

  if (filters.stageId) {
    params.set(
      "stage_id",
      filters.stageId
    );
  }

  if (filters.ownerUserId) {
    params.set(
      "owner_user_id",
      filters.ownerUserId
    );
  }

  if (filters.search) {
    params.set(
      "search",
      filters.search
    );
  }

  const query =
    params.toString();

  return aivaRequest<
    Opportunity[]
  >(
    `/opportunities${
      query ? `?${query}` : ""
    }`
  );
}


export function getOpportunity(
  opportunityId: string
): Promise<Opportunity> {
  return aivaRequest<Opportunity>(
    `/opportunities/${opportunityId}`
  );
}


export function createOpportunity(
  payload: OpportunityCreatePayload
): Promise<Opportunity> {
  return aivaRequest<Opportunity>(
    "/opportunities",
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}


export function updateOpportunity(
  opportunityId: string,
  payload: OpportunityUpdatePayload
): Promise<Opportunity> {
  return aivaRequest<Opportunity>(
    `/opportunities/${opportunityId}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    }
  );
}


export function moveOpportunity(
  opportunityId: string,
  stageId: string
): Promise<Opportunity> {
  return updateOpportunity(
    opportunityId,
    {
      stage_id: stageId,
    }
  );
}
