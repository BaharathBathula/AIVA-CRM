"use client";

import Link from "next/link";
import { ArrowLeft, MailCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AuthLayout } from "@/components/auth-layout";
import { Alert, Field } from "@/components/ui";
import { api, errorMessage } from "@/lib/use-api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState<{ message: string; reset_url?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = await api.post<{ message: string; reset_url?: string }>("/auth/password/forgot", {
        email: email.trim().toLowerCase(),
      });
      setSent(result);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthLayout>
      {sent ? (
        <>
          <div className="ui-avatar" style={{ width: 44, height: 44, marginBottom: 18 }}>
            <MailCheck size={22} />
          </div>
          <h1>Check your email</h1>
          <p>{sent.message}</p>
          {sent.reset_url ? (
            <Alert tone="info">
              Development mode: no email service is configured, so here is the link that would be sent.{" "}
              <a href={new URL(sent.reset_url).pathname + new URL(sent.reset_url).search} style={{ color: "inherit", fontWeight: 700 }}>
                Open the reset link
              </a>
            </Alert>
          ) : null}
          <div className="auth-alt">
            <Link href="/login">
              <ArrowLeft size={14} style={{ verticalAlign: -2 }} /> Back to sign in
            </Link>
          </div>
        </>
      ) : (
        <>
          <h1>Reset your password</h1>
          <p>Enter the email you sign in with and we&apos;ll send you a link to choose a new password.</p>
          <form className="auth-form" onSubmit={handleSubmit}>
            {error ? <Alert>{error}</Alert> : null}
            <Field label="Work email">
              <input
                className="ui-input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                autoFocus
                required
              />
            </Field>
            <button className="ui-btn ui-btn-primary" type="submit" disabled={loading}>
              {loading ? <span className="ui-spinner" /> : null}
              Send reset link
            </button>
          </form>
          <div className="auth-alt">
            Remembered it? <Link href="/login">Sign in</Link>
          </div>
        </>
      )}
    </AuthLayout>
  );
}
