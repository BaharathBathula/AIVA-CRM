"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { AuthLayout, AuthTabs, OrDivider, PasswordInput, PasswordStrength, ProviderButtons, passwordChecks } from "@/components/auth-layout";
import { Alert, Field } from "@/components/ui";
import { fetchAuthError, signup } from "@/lib/auth";

export default function SignupPage() {
  return (
    <AuthLayout>
      <Suspense fallback={null}>
        <SignupForm />
      </Suspense>
    </AuthLayout>
  );
}

function SignupForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const policyOk = passwordChecks(password, email).every((check) => check.ok);

  useEffect(() => {
    if (params.get("error") !== "signin") return;
    let active = true;
    fetchAuthError()
      .then((message) => active && setError(message ?? "Sign-up didn't finish. Try again."))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [params]);

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
    <>
      <AuthTabs active="signup" />
      <h1>Start your free trial</h1>
      <p>
        Seven days with every Enterprise feature, no credit card needed.{" "}
        <Link href="/pricing" className="auth-link">
          See plans and pricing
        </Link>
      </p>
      {error ? <Alert>{error}</Alert> : null}
      <ProviderButtons intent="signup" onOrganization={() => router.push("/login")} />
      <OrDivider />
      <form className="auth-form" onSubmit={handleSubmit}>
        <Field label="Full name">
          <input className="ui-input" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" required />
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
        <button className="ui-btn ui-btn-primary signin-submit" type="submit" disabled={loading || !agreed}>
          {loading ? <span className="ui-spinner" /> : null}
          Create workspace <ArrowRight size={16} />
        </button>
      </form>
    </>
  );
}
