import {
  aivaRequest,
} from "@/lib/api";

import type {
  Contact,
  ContactCreatePayload,
} from "@/types/contact";


export function getContacts(
  accountId?: string
): Promise<Contact[]> {
  const query = accountId
    ? `?account_id=${encodeURIComponent(
        accountId
      )}`
    : "";

  return aivaRequest<Contact[]>(
    `/contacts${query}`
  );
}


export function getContact(
  contactId: string
): Promise<Contact> {
  return aivaRequest<Contact>(
    `/contacts/${contactId}`
  );
}


export function createContact(
  payload: ContactCreatePayload
): Promise<Contact> {
  return aivaRequest<Contact>(
    "/contacts",
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}


export function updateContact(
  contactId: string,
  payload: Partial<ContactCreatePayload>
): Promise<Contact> {
  return aivaRequest<Contact>(
    `/contacts/${contactId}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    }
  );
}
