import {
  aivaRequest,
} from "@/lib/api";

import type {
  Account,
  AccountCreatePayload,
} from "@/types/account";


export function getAccounts(): Promise<Account[]> {
  return aivaRequest<Account[]>(
    "/accounts"
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
      body: JSON.stringify(payload),
    }
  );
}


export function updateAccount(
  accountId: string,
  payload: Partial<AccountCreatePayload>
): Promise<Account> {
  return aivaRequest<Account>(
    `/accounts/${accountId}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    }
  );
}
