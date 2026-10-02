cat > src/lib/meetings.ts <<'TS'
import {
  aivaRequest,
} from "@/lib/api";

import type {
  Meeting,
  MeetingCreatePayload,
  MeetingFilters,
  MeetingUpdatePayload,
} from "@/types/meeting";


export function getMeetings(
  filters: MeetingFilters = {}
): Promise<Meeting[]> {
  const params =
    new URLSearchParams();

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

  if (filters.opportunityId) {
    params.set(
      "opportunity_id",
      filters.opportunityId
    );
  }

  if (filters.provider) {
    params.set(
      "provider",
      filters.provider
    );
  }

  if (filters.status) {
    params.set(
      "status",
      filters.status
    );
  }

  if (filters.startFrom) {
    params.set(
      "start_from",
      filters.startFrom
    );
  }

  if (filters.startTo) {
    params.set(
      "start_to",
      filters.startTo
    );
  }

  const query =
    params.toString();

  return aivaRequest<
    Meeting[]
  >(
    `/meetings${
      query
        ? `?${query}`
        : ""
    }`
  );
}


export function getMeeting(
  meetingId: string
): Promise<Meeting> {
  return aivaRequest<
    Meeting
  >(
    `/meetings/${meetingId}`
  );
}


export function createMeeting(
  payload: MeetingCreatePayload
): Promise<Meeting> {
  return aivaRequest<
    Meeting
  >(
    "/meetings",
    {
      method: "POST",
      body: JSON.stringify(
        payload
      ),
    }
  );
}


export function updateMeeting(
  meetingId: string,
  payload: MeetingUpdatePayload
): Promise<Meeting> {
  return aivaRequest<
    Meeting
  >(
    `/meetings/${meetingId}`,
    {
      method: "PATCH",
      body: JSON.stringify(
        payload
      ),
    }
  );
}
TS
