"use client";

import { useEffect, useMemo } from "react";

import { Field } from "@/components/ui";
import { useApi } from "@/lib/use-api";
import type { Account } from "@/types/account";
import type { Contact } from "@/types/contact";
import type { Opportunity } from "@/types/opportunity";

export type QuoteDetails = {
  name: string;
  opportunity_id: string;
  account_id: string;
  contact_id: string;
  valid_until: string;
  terms: string;
  notes: string;
};

export function defaultValidUntil(days = 30) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

export const DEFAULT_TERMS = "Net 30. Prices exclude applicable taxes unless shown. Subscription fees are billed in advance.";

/** Shared fields for creating a quote and editing its header. */
export function QuoteDetailsFields({ value, onChange }: { value: QuoteDetails; onChange: (next: QuoteDetails) => void }) {
  const opportunities = useApi<Opportunity[]>("/opportunities?limit=100");
  const accounts = useApi<Account[]>("/accounts?limit=100");
  const contacts = useApi<Contact[]>(value.account_id ? `/contacts?account_id=${value.account_id}&limit=100` : null);

  const opportunity = useMemo(
    () => opportunities.data?.find((item) => item.id === value.opportunity_id) ?? null,
    [opportunities.data, value.opportunity_id]
  );

  // Picking an opportunity fills in its account and primary contact.
  useEffect(() => {
    if (!opportunity) return;
    if (opportunity.account_id !== value.account_id || (!value.contact_id && opportunity.primary_contact_id)) {
      onChange({
        ...value,
        account_id: opportunity.account_id,
        contact_id: value.contact_id || opportunity.primary_contact_id || "",
        name: value.name || `${opportunity.name} proposal`,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity]);

  const set = (key: keyof QuoteDetails) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    onChange({ ...value, [key]: event.target.value });

  return (
    <div className="ui-form-grid">
      <Field label="Opportunity" hint="Accepting the quote updates this deal's amount.">
        <select className="ui-select" value={value.opportunity_id} onChange={set("opportunity_id")}>
          <option value="">No opportunity</option>
          {(opportunities.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Quote name" required>
        <input className="ui-input" value={value.name} onChange={set("name")} required placeholder="2027 platform proposal" />
      </Field>
      <Field label="Account">
        <select
          className="ui-select"
          value={value.account_id}
          onChange={(e) => onChange({ ...value, account_id: e.target.value, contact_id: "" })}
          disabled={Boolean(opportunity)}
        >
          <option value="">No account</option>
          {(accounts.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Prepared for">
        <select className="ui-select" value={value.contact_id} onChange={set("contact_id")} disabled={!value.account_id}>
          <option value="">{value.account_id ? "No contact" : "Choose an account first"}</option>
          {(contacts.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.first_name} {item.last_name}
              {item.job_title ? ` — ${item.job_title}` : ""}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Valid until">
        <input className="ui-input" type="date" value={value.valid_until} onChange={set("valid_until")} />
      </Field>
      <span />
      <Field label="Terms" className="span-2">
        <textarea className="ui-textarea" rows={2} value={value.terms} onChange={set("terms")} />
      </Field>
      <Field label="Internal notes" hint="Not shown on the customer copy." className="span-2">
        <textarea className="ui-textarea" rows={2} value={value.notes} onChange={set("notes")} />
      </Field>
    </div>
  );
}

export function detailsPayload(value: QuoteDetails) {
  return {
    name: value.name.trim(),
    opportunity_id: value.opportunity_id || null,
    account_id: value.account_id || null,
    contact_id: value.contact_id || null,
    valid_until: value.valid_until || null,
    terms: value.terms.trim() || null,
    notes: value.notes.trim() || null,
  };
}
