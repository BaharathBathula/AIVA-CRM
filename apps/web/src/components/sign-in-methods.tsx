"use client";

import { useSearchParams } from "next/navigation";
import { KeyRound, Link2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, LoadingRows } from "@/components/ui";
import { fetchAuthError, providerUrl } from "@/lib/auth";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface Identity {
  id: string;
  provider: "google" | "microsoft" | "apple" | "sso";
  label: string;
  email: string | null;
  created_at: string;
  last_login_at: string | null;
}

interface Identities {
  has_password: boolean;
  available: { google: boolean; microsoft: boolean; apple: boolean };
  items: Identity[];
}

const NAMES = { google: "Google", microsoft: "Microsoft", apple: "Apple" } as const;

/** Google, Microsoft, Apple and single sign-on accounts connected to this login. */
export function SignInMethods() {
  const toast = useToast();
  const params = useSearchParams();
  const data = useApi<Identities>("/auth/identities");
  const [removing, setRemoving] = useState<Identity | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const connected = params.get("connected");
    if (connected && connected in NAMES) toast(`${NAMES[connected as keyof typeof NAMES]} is connected. You can use it to sign in.`);
    if (params.get("error") === "signin") void fetchAuthError().then((message) => message && setError(message));
  }, [params, toast]);

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await api.del(`/auth/identities/${removing.id}`);
      toast(`${removing.label} disconnected.`);
      setRemoving(null);
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  const linked = new Set((data.data?.items ?? []).map((item) => item.provider));
  const connectable = (Object.keys(NAMES) as (keyof typeof NAMES)[]).filter((p) => data.data?.available[p] && !linked.has(p));

  return (
    <Card title="Sign-in methods" icon={<KeyRound size={16} />}>
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      {!data.data ? (
        <LoadingRows rows={2} />
      ) : (
        <div className="ui-stack">
          <ul className="method-list">
            <li>
              <span className="type-icon">
                <KeyRound size={15} />
              </span>
              <span className="method-body">
                <strong>Email and password</strong>
                <span className="ui-muted ui-small">{data.data.has_password ? "Set" : "Not set — use Forgot password on the sign-in page to add one."}</span>
              </span>
              {data.data.has_password ? <Badge tone="success">On</Badge> : <Badge>Off</Badge>}
            </li>
            {data.data.items.map((item) => (
              <li key={item.id}>
                <span className="type-icon">
                  <Link2 size={15} />
                </span>
                <span className="method-body">
                  <strong>{item.label}</strong>
                  <span className="ui-muted ui-small">
                    {item.email ?? "Connected"} · {item.last_login_at ? `last used ${relativeTime(item.last_login_at)}` : `connected ${relativeTime(item.created_at)}`}
                  </span>
                </span>
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => setRemoving(item)}>
                  Disconnect
                </button>
              </li>
            ))}
          </ul>
          {connectable.length ? (
            <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
              {connectable.map((provider) => (
                <a key={provider} className="ui-btn ui-btn-sm" href={providerUrl(provider, "link")}>
                  Connect {NAMES[provider]}
                </a>
              ))}
            </div>
          ) : null}
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Signing in with Google, Microsoft or Apple uses the same AIVA account when the email matches. Two-step verification still applies.
          </p>
        </div>
      )}
      <ConfirmDialog
        open={Boolean(removing)}
        title={`Disconnect ${removing?.label ?? ""}?`}
        message="You won't be able to sign in with it until you connect it again."
        confirmLabel="Disconnect"
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setRemoving(null)}
      />
    </Card>
  );
}
