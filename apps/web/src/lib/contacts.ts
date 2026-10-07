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
  includeArchived?: boolean;
  segment?: string;
  tag?: string;
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
    options.includeArchived
    !== undefined
  ) {
    params.set(
      "include_archived",
      String(
        options.includeArchived
      )
    );
  }


  if (options.segment) {
    params.set(
      "segment",
      options.segment
    );
  }

  if (options.tag) {
    params.set(
      "tag",
      options.tag
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

export interface ContactDuplicateCheckParams {
  firstName?: string;
  lastName?: string;
  email?: string;
  phone?: string;
  mobile?: string;
  accountId?: string;
  excludeContactId?: string;
}


export async function checkContactDuplicates(
  values: ContactDuplicateCheckParams
): Promise<
  import("@/types/contact")
    .ContactDuplicateCheckResponse
> {
  const params =
    new URLSearchParams();

  if (values.firstName?.trim()) {
    params.set(
      "first_name",
      values.firstName.trim()
    );
  }

  if (values.lastName?.trim()) {
    params.set(
      "last_name",
      values.lastName.trim()
    );
  }

  if (values.email?.trim()) {
    params.set(
      "email",
      values.email.trim()
    );
  }

  if (values.phone?.trim()) {
    params.set(
      "phone",
      values.phone.trim()
    );
  }

  if (values.mobile?.trim()) {
    params.set(
      "mobile",
      values.mobile.trim()
    );
  }

  if (values.accountId) {
    params.set(
      "account_id",
      values.accountId
    );
  }

  if (values.excludeContactId) {
    params.set(
      "exclude_contact_id",
      values.excludeContactId
    );
  }

  return aivaRequest(
    `/contacts/duplicates/check?${params.toString()}`
  );
}



export function archiveContact(
  contactId: string
): Promise<Contact> {
  return aivaRequest<Contact>(
    `/contacts/${contactId}/archive`,
    {
      method: "POST",
    }
  );
}


export function reactivateContact(
  contactId: string
): Promise<Contact> {
  return aivaRequest<Contact>(
    `/contacts/${contactId}/reactivate`,
    {
      method: "POST",
    }
  );
}
