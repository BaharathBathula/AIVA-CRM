import {
  aivaRequest,
} from "@/lib/api";

import type {
  Activity,
  ActivityCreatePayload,
  ActivityDirection,
  ActivityType,
} from "@/types/activity";


export interface ActivityFilters {
  activityType?: ActivityType;
  direction?: ActivityDirection;

  accountId?: string;
  contactId?: string;
  leadId?: string;
  opportunityId?: string;

  occurredFrom?: string;
  occurredTo?: string;

  search?: string;

  skip?: number;
  limit?: number;
}


export function getActivities(
  filters: ActivityFilters = {}
): Promise<Activity[]> {
  const params =
    new URLSearchParams();

  if (filters.activityType) {
    params.set(
      "activity_type",
      filters.activityType
    );
  }

  if (filters.direction) {
    params.set(
      "direction",
      filters.direction
    );
  }

  if (filters.accountId) {
    params.set(
      "account_id",
      filters.accountId
    );
  }

  if (filters.contactId) {
    params.set(
      "contact_id",
      filters.contactId
    );
  }

  if (filters.leadId) {
    params.set(
      "lead_id",
      filters.leadId
    );
  }

  if (filters.opportunityId) {
    params.set(
      "opportunity_id",
      filters.opportunityId
    );
  }

  if (filters.occurredFrom) {
    params.set(
      "occurred_from",
      filters.occurredFrom
    );
  }

  if (filters.occurredTo) {
    params.set(
      "occurred_to",
      filters.occurredTo
    );
  }

  if (filters.search) {
    params.set(
      "search",
      filters.search
    );
  }

  if (filters.skip !== undefined) {
    params.set(
      "skip",
      String(filters.skip)
    );
  }

  if (filters.limit !== undefined) {
    params.set(
      "limit",
      String(filters.limit)
    );
  }

  const query =
    params.toString();

  return aivaRequest<Activity[]>(
    `/activities${
      query
        ? `?${query}`
        : ""
    }`
  );
}


export function getAccountActivities(
  accountId: string,
  skip = 0,
  limit = 50
): Promise<Activity[]> {
  return aivaRequest<Activity[]>(
    `/accounts/${accountId}/activities?skip=${skip}&limit=${limit}`
  );
}


export function getContactActivities(
  contactId: string,
  skip = 0,
  limit = 50
): Promise<Activity[]> {
  return aivaRequest<Activity[]>(
    `/contacts/${contactId}/activities?skip=${skip}&limit=${limit}`
  );
}


export function getLeadActivities(
  leadId: string,
  skip = 0,
  limit = 50
): Promise<Activity[]> {
  return aivaRequest<Activity[]>(
    `/leads/${leadId}/activities?skip=${skip}&limit=${limit}`
  );
}


export function getOpportunityActivities(
  opportunityId: string,
  skip = 0,
  limit = 50
): Promise<Activity[]> {
  return aivaRequest<Activity[]>(
    `/opportunities/${opportunityId}/activities?skip=${skip}&limit=${limit}`
  );
}


export function createActivity(
  payload: ActivityCreatePayload
): Promise<Activity> {
  return aivaRequest<Activity>(
    "/activities",
    {
      method: "POST",
      body: JSON.stringify(
        payload
      ),
    }
  );
}
