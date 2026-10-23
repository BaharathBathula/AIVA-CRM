"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, ShieldCheck } from "lucide-react";
import { Suspense, useState, type FormEvent } from "react";

import { AuthLayout, PasswordInput } from "@/components/auth-layout";
import { Alert, Field } from "@/components/ui";
import { login, verifyMfa } from "@/lib/auth";

const SHOW_DEMO = process.env.NODE_ENV !== "production" || process.env.NEXT_PUBLIC_DEMO_MODE === "true";

function safeNext(value: string | null) {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const notice = params.get("reset") ? "Your password was updated. Sign in with the new one." : null;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [mfaName, setMfaName] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function finish() {
    router.replace(next);
    router.refresh();
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await login(email, password, remember);
      if (result.mfa_required) {
        setMfaToken(result.mfa_token);
        setMfaName(result.user.full_name);
        setPassword("");
        setLoading(false);
        return;
      }
      finish();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to sign in. Try again.");
      setLoading(false);
    }
  }

  async function handleMfa(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!mfaToken) return;
    setError(null);
    setLoading(true);
    try {
      await verifyMfa(mfaToken, code.replace(/\s/g, ""), remember);
      finish();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That code didn't work. Try again.");
      setLoading(false);
    }
  }

  if (mfaToken) {
    return (
      <>
        <div className="ui-avatar" style={{ width: 44, height: 44, marginBottom: 18 }}>
          <ShieldCheck size={22} />
        </div>
        <h1>Two-step verification</h1>
        <p>
          Hi {mfaName.split(" ")[0] || "there"} — enter the 6-digit code from your authenticator app to finish signing in.
        </p>
        <form className="auth-form" onSubmit={handleMfa}>
          {error ? <Alert>{error}</Alert> : null}
          <Field label="Verification code">
            <input
              className="ui-input auth-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={7}
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/[^\d ]/g, ""))}
              placeholder="123 456"
              autoFocus
              required
            />
          </Field>
          <button className="ui-btn ui-btn-primary" type="submit" disabled={loading || code.replace(/\s/g, "").length < 6}>
            {loading ? <span className="ui-spinner" /> : null}
            Verify and sign in
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-ghost"
            onClick={() => {
              setMfaToken(null);
              setCode("");
              setError(null);
            }}
          >
            <ArrowLeft size={16} /> Use a different account
          </button>
        </form>
      </>
    );
  }

  return (
    <>
      <h1>Sign in to AIVA</h1>
      <p>Welcome back. Use your work email to continue.</p>
      <form className="auth-form" onSubmit={handleLogin}>
        {notice && !error ? <Alert tone="success">{notice}</Alert> : null}
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Work email">
          <input
            id="email"
            className="ui-input"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@company.com"
            autoFocus
            required
          />
        </Field>
        <div className="ui-field">
          <div className="ui-row" style={{ justifyContent: "space-between" }}>
            <label htmlFor="password" className="ui-label">
              Password
            </label>
            <Link href="/forgot-password" className="ui-small auth-link">
              Forgot password?
            </Link>
          </div>
          <PasswordInput id="password" value={password} onChange={setPassword} autoComplete="current-password" />
        </div>
        <label className="ui-check">
          <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
          Keep me signed in for 30 days
        </label>
        <button className="ui-btn ui-btn-primary" type="submit" disabled={loading}>
          {loading ? <span className="ui-spinner" /> : null}
          Sign in <ArrowRight size={16} />
        </button>
      </form>
      <div className="auth-alt">
        New to AIVA? <Link href="/signup">Start a free trial</Link> · <Link href="/pricing">Pricing</Link>
      </div>
      {SHOW_DEMO ? (
        <div className="auth-demo">
          <strong>Demo workspace.</strong> Sign in as <code>admin@aiva.local</code>, or a sales manager{" "}
          <code>maya.chen@aiva.local</code>, rep <code>priya.raman@aiva.local</code> or support agent{" "}
          <code>tom.becker@aiva.local</code>. Password for all: <code>AivaDemo#2026</code>
        </div>
      ) : null}
    </>
  );
}

export default function LoginPage() {
  return (
    <AuthLayout>
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </AuthLayout>
  );
}
