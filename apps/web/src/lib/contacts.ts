import {
  aivaRequest,
} from "@/lib/api";

import type {
  Contact,
  ContactCreatePayload,
  ContactUpdatePayload,
} from "@/types/contact";


export interface ContactListOptions {
  search?: string;
  isActive?: boolean;
  isPrimary?: boolean;
  skip?: number;
  limit?: number;
}


export function getContacts(
  accountId?: string,
  options: ContactListOptions = {}
): Promise<Contact[]> {
  const params =
    new URLSearchParams();

  if (accountId) {
    params.set(
      "account_id",
      accountId
    );
  }

  if (options.search) {
    params.set(
      "search",
      options.search
    );
  }

  if (
    options.isActive
    !== undefined
  ) {
    params.set(
      "is_active",
      String(
        options.isActive
      )
    );
  }

  if (
    options.isPrimary
    !== undefined
  ) {
    params.set(
      "is_primary",
      String(
        options.isPrimary
      )
    );
  }

  if (
    options.skip
    !== undefined
  ) {
    params.set(
      "skip",
      String(
        options.skip
      )
    );
  }

  params.set(
    "limit",
    String(
      options.limit ?? 100
    )
  );

  const query =
    params.toString();

  return aivaRequest<Contact[]>(
    `/contacts${
      query
        ? `?${query}`
        : ""
    }`
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
      body: JSON.stringify(
        payload
      ),
    }
  );
}


export function updateContact(
  contactId: string,
  payload: ContactUpdatePayload
): Promise<Contact> {
  return aivaRequest<Contact>(
    `/contacts/${contactId}`,
    {
      method: "PATCH",
      body: JSON.stringify(
        payload
      ),
    }
  );
}