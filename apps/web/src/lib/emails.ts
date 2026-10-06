import {
  aivaRequest,
} from "@/lib/api";

import type {
  EmailComposePayload,
  EmailMessage,
  EmailReplyPayload,
  EmailSendResult,
  EmailThread,
} from "@/types/email";


export interface EmailThreadFilters {
  provider?: string;

  accountId?: string;
  contactId?: string;
  leadId?: string;
  opportunityId?: string;

  search?: string;

  skip?: number;
  limit?: number;
}


/* ============================================================
   Threads
   ============================================================ */


export function getEmailThreads(
  filters: EmailThreadFilters = {}
): Promise<EmailThread[]> {
  const params =
    new URLSearchParams();

  if (filters.provider) {
    params.set(
      "provider",
      filters.provider
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

  if (filters.search) {
    params.set(
      "search",
      filters.search
    );
  }

  if (
    filters.skip !== undefined
  ) {
    params.set(
      "skip",
      String(filters.skip)
    );
  }

  if (
    filters.limit !== undefined
  ) {
    params.set(
      "limit",
      String(filters.limit)
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


/* ============================================================
   Messages
   ============================================================ */


export function getThreadMessages(
  threadId: string
): Promise<EmailMessage[]> {
  return aivaRequest<
    EmailMessage[]
  >(
    `/email/threads/${threadId}/messages`
  );
}


export function getEmailMessage(
  messageId: string
): Promise<EmailMessage> {
  return aivaRequest<
    EmailMessage
  >(
    `/email/messages/${messageId}`
  );
}


/* ============================================================
   Compose
   ============================================================ */


export function composeEmail(
  payload: EmailComposePayload
): Promise<EmailSendResult> {
  return aivaRequest<
    EmailSendResult
  >(
    "/email/compose",
    {
      method: "POST",

      body: JSON.stringify(
        payload
      ),
    }
  );
}


/* ============================================================
   Reply
   ============================================================ */


export function replyToEmailThread(
  threadId: string,
  payload: EmailReplyPayload
): Promise<EmailSendResult> {
  return aivaRequest<
    EmailSendResult
  >(
    `/email/threads/${threadId}/reply`,
    {
      method: "POST",

      body: JSON.stringify(
        payload
      ),
    }
  );
}