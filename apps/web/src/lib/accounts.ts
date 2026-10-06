import {
  aivaRequest,
} from "@/lib/api";

import type {
  Account,
  AccountCreatePayload,
  AccountDuplicateCheckPayload,
  AccountDuplicateCheckResponse,
  AccountListOptions,
} from "@/types/account";


export function getAccounts(
  options: AccountListOptions = {}
): Promise<Account[]> {
  const params =
    new URLSearchParams();

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

  if (
    options.limit
    !== undefined
  ) {
    params.set(
      "limit",
      String(
        options.limit
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

  if (
    options.parentAccountId
  ) {
    params.set(
      "parent_account_id",
      options.parentAccountId
    );
  }

  const query =
    params.toString();

  return aivaRequest<Account[]>(
    query
      ? `/accounts?${query}`
      : "/accounts"
  );
}


export function getAccount(
  accountId: string
): Promise<Account> {
  return aivaRequest<Account>(
    `/accounts/${accountId}`
  );
}


export function createAccount(
  payload: AccountCreatePayload
): Promise<Account> {
  return aivaRequest<Account>(
    "/accounts",
    {
      method: "POST",

      body:
        JSON.stringify(
          payload
        ),
    }
  );
}


export function updateAccount(
  accountId: string,
  payload:
    Partial<AccountCreatePayload>
): Promise<Account> {
  return aivaRequest<Account>(
    `/accounts/${accountId}`,
    {
      method: "PATCH",

      body:
        JSON.stringify(
          payload
        ),
    }
  );
}


export function getChildAccounts(
  accountId: string,
  includeArchived = false
): Promise<Account[]> {
  const params =
    new URLSearchParams();

  params.set(
    "include_archived",
    String(
      includeArchived
    )
  );

  return aivaRequest<
    Account[]
  >(
    `/accounts/${accountId}/children?${params.toString()}`
  );
}


export function archiveAccount(
  accountId: string
): Promise<Account> {
  return aivaRequest<Account>(
    `/accounts/${accountId}/archive`,
    {
      method: "POST",
    }
  );
}


export function reactivateAccount(
  accountId: string
): Promise<Account> {
  return aivaRequest<Account>(
    `/accounts/${accountId}/reactivate`,
    {
      method: "POST",
    }
  );
}


export function checkAccountDuplicates(
  payload:
    AccountDuplicateCheckPayload
): Promise<
  AccountDuplicateCheckResponse
> {
  const params =
    new URLSearchParams();

  params.set(
    "name",
    payload.name
  );

  if (
    payload.domain
  ) {
    params.set(
      "domain",
      payload.domain
    );
  }

  if (
    payload.excludeAccountId
  ) {
    params.set(
      "exclude_account_id",
      payload.excludeAccountId
    );
  }

  return aivaRequest<
    AccountDuplicateCheckResponse
  >(
    `/accounts/duplicates/check?${params.toString()}`
  );
}