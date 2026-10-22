"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { AppShell } from "@/components/app-shell";
import { useToast } from "@/components/providers";
import {
  DEFAULT_TERMS,
  QuoteDetailsFields,
  defaultValidUntil,
  detailsPayload,
  type QuoteDetails,
} from "@/components/quote-details-form";
import { Alert, Card, PageHeader } from "@/components/ui";
import type { Quote } from "@/lib/platform-types";
import { api, errorMessage } from "@/lib/use-api";

export default function NewQuotePage() {
  return (
    <AppShell permission="sales.quotes" what="quotes">
      <NewQuote />
    </AppShell>
  );
}

function NewQuote() {
  const router = useRouter();
  const toast = useToast();
  const [details, setDetails] = useState<QuoteDetails>({
    name: "",
    opportunity_id: "",
    account_id: "",
    contact_id: "",
    valid_until: defaultValidUntil(),
    terms: DEFAULT_TERMS,
    notes: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const opportunityId = new URLSearchParams(window.location.search).get("opportunity_id");
    if (opportunityId) setDetails((current) => ({ ...current, opportunity_id: opportunityId }));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const quote = await api.post<Quote>("/quotes", { ...detailsPayload(details), line_items: [] });
      toast(`${quote.display_number} created. Add products next.`);
      router.replace(`/quotes/${quote.id}`);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="New quote" description="Set who it's for, then add products and pricing on the next screen." />
      <form onSubmit={submit} style={{ maxWidth: 860 }}>
        <Card
          footer={
            <>
              <Link href="/quotes" className="ui-btn">
                Cancel
              </Link>
              <button type="submit" className="ui-btn ui-btn-primary" disabled={busy}>
                {busy ? <span className="ui-spinner" /> : null}
                Create quote
              </button>
            </>
          }
        >
          {error ? (
            <div style={{ marginBottom: 14 }}>
              <Alert>{error}</Alert>
            </div>
          ) : null}
          <QuoteDetailsFields value={details} onChange={setDetails} />
        </Card>
      </form>
    </>
  );
}
