import {
  aivaRequest,
} from "@/lib/api";

import type {
  Activity,
  ActivityCreatePayload,
} from "@/types/activity";


export function getAccountActivities(
  accountId: string
): Promise<Activity[]> {
  return aivaRequest<Activity[]>(
    `/accounts/${accountId}/activities`
  );
}


export function getContactActivities(
  contactId: string
): Promise<Activity[]> {
  return aivaRequest<Activity[]>(
    `/contacts/${contactId}/activities`
  );
}


export function createActivity(
  payload: ActivityCreatePayload
): Promise<Activity> {
  return aivaRequest<Activity>(
    "/activities",
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}
