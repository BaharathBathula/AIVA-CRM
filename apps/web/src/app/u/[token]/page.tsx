"use client";

import { useParams } from "next/navigation";
import { CheckCircle2, MailX } from "lucide-react";
import { useState } from "react";

import { Alert, LoadingRows } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function UnsubscribePage() {
  const { token } = useParams<{ token: string }>();
  const info = useApi<{ email: string; organization_name: string; unsubscribed: boolean }>(`/public/unsubscribe/${token}`);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function unsubscribe() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/public/unsubscribe/${token}`);
      setDone(true);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="public-shell">
      <div className="public-card" style={{ textAlign: "center" }}>
        {info.error ? (
          <>
            <h1>This link isn&apos;t valid</h1>
            <p>{info.error}</p>
          </>
        ) : !info.data ? (
          <LoadingRows rows={4} />
        ) : done || info.data.unsubscribed ? (
          <div className="ui-stack" style={{ justifyItems: "center" }}>
            <CheckCircle2 size={44} color="var(--success)" />
            <h1>You&apos;re unsubscribed</h1>
            <p>
              {info.data.email} won&apos;t get marketing email from {info.data.organization_name} anymore. You may still hear from us about things you asked
              for.
            </p>
          </div>
        ) : (
          <div className="ui-stack" style={{ justifyItems: "center" }}>
            <MailX size={44} color="var(--muted)" />
            <h1>Unsubscribe?</h1>
            <p>
              Stop marketing email from {info.data.organization_name} to <strong>{info.data.email}</strong>.
            </p>
            {error ? <Alert>{error}</Alert> : null}
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void unsubscribe()}>
              Unsubscribe
            </button>
          </div>
        )}
        <div className="public-foot">Powered by AIVA CRM</div>
      </div>
    </div>
  );
}
