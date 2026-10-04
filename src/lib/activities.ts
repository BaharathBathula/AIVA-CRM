cat > src/lib/activities.ts <<'TS'
import {
  aivaRequest,
} from "@/lib/api";

import type {
  Activity,
  ActivityCreatePayload,
} from "@/types/activity";


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


export function getAccountActivities(
  accountId: string
): Promise<Activity[]> {
  return aivaRequest<
    Activity[]
  >(
    `/accounts/${accountId}/activities`
  );
}


export function getContactActivities(
  contactId: string
): Promise<Activity[]> {
  return aivaRequest<
    Activity[]
  >(
    `/contacts/${contactId}/activities`
  );
}


export function getLeadActivities(
  leadId: string
): Promise<Activity[]> {
  return aivaRequest<
    Activity[]
  >(
    `/leads/${leadId}/activities`
  );
}


export function getOpportunityActivities(
  opportunityId: string
): Promise<Activity[]> {
  return aivaRequest<
    Activity[]
  >(
    `/opportunities/${opportunityId}/activities`
  );
}
TS
