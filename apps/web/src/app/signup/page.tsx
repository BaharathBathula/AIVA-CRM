"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AuthLayout, PasswordInput, PasswordStrength, passwordChecks } from "@/components/auth-layout";
import { Alert, Field } from "@/components/ui";
import { signup } from "@/lib/auth";

export default function SignupPage() {
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const policyOk = passwordChecks(password, email).every((check) => check.ok);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policyOk) {
      setError("Choose a stronger password that meets all three rules.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await signup({ full_name: fullName.trim(), email: email.trim().toLowerCase(), password, company_name: company.trim() });
      router.replace("/");
      router.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "We couldn't create your workspace. Try again.");
      setLoading(false);
    }
  }

  return (
    <AuthLayout>
      <h1>Start your free trial</h1>
      <p>
        Seven days with every Enterprise feature, no credit card needed. You&apos;ll own the workspace and can invite your team next.{" "}
        <Link href="/pricing" className="auth-link">
          See plans and pricing
        </Link>
      </p>
      <form className="auth-form" onSubmit={handleSubmit}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Full name">
          <input className="ui-input" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" required autoFocus />
        </Field>
        <Field label="Work email">
          <input
            className="ui-input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            placeholder="you@company.com"
            required
          />
        </Field>
        <Field label="Company name">
          <input className="ui-input" value={company} onChange={(e) => setCompany(e.target.value)} autoComplete="organization" required />
        </Field>
        <div className="ui-field">
          <label htmlFor="new-password" className="ui-label">
            Password
          </label>
          <PasswordInput id="new-password" value={password} onChange={setPassword} autoComplete="new-password" />
          <PasswordStrength password={password} email={email} />
        </div>
        <label className="ui-check" style={{ alignItems: "flex-start" }}>
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} required />
          <span>I agree to the terms of service and acknowledge the privacy policy.</span>
        </label>
        <button className="ui-btn ui-btn-primary" type="submit" disabled={loading || !agreed}>
          {loading ? <span className="ui-spinner" /> : null}
          Create workspace <ArrowRight size={16} />
        </button>
      </form>
      <div className="auth-alt">
        Already have an account? <Link href="/login">Sign in</Link>
      </div>
    </AuthLayout>
  );
}
