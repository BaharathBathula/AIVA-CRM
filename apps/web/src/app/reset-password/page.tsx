"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { AuthLayout, PasswordInput, PasswordStrength, passwordChecks } from "@/components/auth-layout";
import { Alert } from "@/components/ui";
import { api, errorMessage } from "@/lib/use-api";

function ResetForm() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token) {
    return (
      <>
        <h1>Link not valid</h1>
        <p>This reset link is missing its code. Request a new one and use the latest email.</p>
        <Link className="ui-btn ui-btn-primary" href="/forgot-password" style={{ height: 42 }}>
          Request a new link
        </Link>
      </>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!passwordChecks(password).every((check) => check.ok)) {
      setError("Choose a stronger password that meets all the rules.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.post("/auth/password/reset", { token, password });
      router.replace("/login?reset=1");
    } catch (caught) {
      setError(errorMessage(caught));
      setLoading(false);
    }
  }

  return (
    <>
      <h1>Choose a new password</h1>
      <p>For your security, every other signed-in device will be signed out.</p>
      <form className="auth-form" onSubmit={handleSubmit}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-field">
          <label htmlFor="new-password" className="ui-label">
            New password
          </label>
          <PasswordInput id="new-password" value={password} onChange={setPassword} autoComplete="new-password" />
          <PasswordStrength password={password} />
        </div>
        <div className="ui-field">
          <label htmlFor="confirm-password" className="ui-label">
            Confirm new password
          </label>
          <PasswordInput
            id="confirm-password"
            value={confirm}
            onChange={setConfirm}
            autoComplete="new-password"
            invalid={Boolean(confirm) && confirm !== password}
          />
        </div>
        <button className="ui-btn ui-btn-primary" type="submit" disabled={loading}>
          {loading ? <span className="ui-spinner" /> : null}
          Update password
        </button>
      </form>
      <div className="auth-alt">
        <Link href="/login">Back to sign in</Link>
      </div>
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <AuthLayout>
      <Suspense fallback={null}>
        <ResetForm />
      </Suspense>
    </AuthLayout>
  );
}
