"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { AuthLayout, PasswordInput, PasswordStrength, passwordChecks } from "@/components/auth-layout";
import { Alert, Field, LoadingRows } from "@/components/ui";
import { acceptInvitation } from "@/lib/auth";
import { api, errorMessage } from "@/lib/use-api";

type Invitation = {
  email: string;
  full_name: string;
  organization_name: string;
  invited_by: string | null;
  has_password: boolean;
};

function AcceptForm() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const [invite, setInvite] = useState<Invitation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(token ? null : "This invitation link is missing its code.");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .get<Invitation>(`/auth/invitations/${encodeURIComponent(token)}`)
      .then((result) => {
        setInvite(result);
        setFullName(result.full_name);
      })
      .catch((caught) => setLoadError(errorMessage(caught)));
  }, [token]);

  if (loadError) {
    return (
      <>
        <h1>Invitation unavailable</h1>
        <p>{loadError} Ask your workspace admin to send a new invitation.</p>
        <Link className="ui-btn" href="/login" style={{ height: 42 }}>
          Go to sign in
        </Link>
      </>
    );
  }

  if (!invite) return <LoadingRows rows={5} />;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!invite?.has_password && !passwordChecks(password, invite?.email).every((check) => check.ok)) {
      setError("Choose a stronger password that meets all three rules.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await acceptInvitation({ token, password, full_name: fullName.trim() || undefined });
      router.replace("/");
      router.refresh();
    } catch (caught) {
      setError(errorMessage(caught));
      setLoading(false);
    }
  }

  return (
    <>
      <h1>Join {invite.organization_name}</h1>
      <p>
        {invite.invited_by ? `${invite.invited_by} invited you` : "You've been invited"} to work in AIVA as{" "}
        <strong>{invite.email}</strong>.
      </p>
      <form className="auth-form" onSubmit={handleSubmit}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Your name">
          <input className="ui-input" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" required />
        </Field>
        <div className="ui-field">
          <label htmlFor="invite-password" className="ui-label">
            {invite.has_password ? "Your existing AIVA password" : "Choose a password"}
          </label>
          <PasswordInput
            id="invite-password"
            value={password}
            onChange={setPassword}
            autoComplete={invite.has_password ? "current-password" : "new-password"}
          />
          {invite.has_password ? null : <PasswordStrength password={password} email={invite.email} />}
        </div>
        <button className="ui-btn ui-btn-primary" type="submit" disabled={loading}>
          {loading ? <span className="ui-spinner" /> : null}
          Accept invitation
        </button>
      </form>
    </>
  );
}

export default function AcceptInvitePage() {
  return (
    <AuthLayout>
      <Suspense fallback={null}>
        <AcceptForm />
      </Suspense>
    </AuthLayout>
  );
}
