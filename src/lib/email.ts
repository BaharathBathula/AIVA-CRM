cat > src/lib/email.ts <<'TS'
import {
  aivaRequest,
} from "@/lib/api";

import type {
  EmailMessage,
  EmailMessageCreatePayload,
  EmailThread,
  EmailThreadCreatePayload,
  EmailThreadFilters,
} from "@/types/email";


export function getEmailThreads(
  filters: EmailThreadFilters = {}
): Promise<EmailThread[]> {
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

  if (filters.search) {
    params.set(
      "search",
      filters.search
    );
  }

  const query =
    params.toString();

  return aivaRequest<
    EmailThread[]
  >(
    `/email/threads${
      query
        ? `?${query}`
        : ""
    }`
  );
}


export function getEmailThread(
  threadId: string
): Promise<EmailThread> {
  return aivaRequest<
    EmailThread
  >(
    `/email/threads/${threadId}`
  );
}


export function createEmailThread(
  payload: EmailThreadCreatePayload
): Promise<EmailThread> {
  return aivaRequest<
    EmailThread
  >(
    "/email/threads",
    {
      method: "POST",
      body: JSON.stringify(
        payload
      ),
    }
  );
}


export function getThreadMessages(
  threadId: string
): Promise<EmailMessage[]> {
  return aivaRequest<
    EmailMessage[]
  >(
    `/email/threads/${threadId}/messages`
  );
}


export function createEmailMessage(
  payload: EmailMessageCreatePayload
): Promise<EmailMessage> {
  return aivaRequest<
    EmailMessage
  >(
    "/email/messages",
    {
      method: "POST",
      body: JSON.stringify(
        payload
      ),
    }
  );
}
TS
