"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { AuthLayout, AuthTabs, OrDivider, OrganizationSignIn, PasswordInput, ProviderButtons } from "@/components/auth-layout";
import { Alert, Field } from "@/components/ui";
import { fetchAuthError, login, verifyMfa } from "@/lib/auth";

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
  // After Google / Microsoft / Apple / SSO the challenge is held in an httpOnly cookie ("cookie").
  const [mfaToken, setMfaToken] = useState<string | null>(params.get("mfa") === "1" ? "cookie" : null);
  const [mfaName, setMfaName] = useState(params.get("name") ?? "");
  const [code, setCode] = useState("");
  const [organization, setOrganization] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (params.get("error") !== "signin") return;
    let active = true;
    fetchAuthError()
      .then((message) => active && setError(message ?? "Sign-in didn't finish. Try again."))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [params]);

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
          <button className="ui-btn ui-btn-primary signin-submit" type="submit" disabled={loading || code.replace(/\s/g, "").length < 6}>
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
              router.replace("/login");
            }}
          >
            <ArrowLeft size={16} /> Use a different account
          </button>
        </form>
      </>
    );
  }

  if (organization) {
    return <OrganizationSignIn next={next} onBack={() => setOrganization(false)} />;
  }

  return (
    <>
      <AuthTabs active="login" />
      {notice && !error ? <Alert tone="success">{notice}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      <ProviderButtons intent="login" next={next} onOrganization={() => setOrganization(true)} />
      <OrDivider />
      <form className="auth-form" onSubmit={handleLogin}>
        <Field label="Email">
          <input
            id="email"
            className="ui-input"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Work email"
            required
          />
        </Field>
        <div className="ui-field">
          <label htmlFor="password" className="ui-label">
            Password
          </label>
          <PasswordInput id="password" value={password} onChange={setPassword} autoComplete="current-password" placeholder="Enter your password" />
        </div>
        <button className="ui-btn ui-btn-primary signin-submit" type="submit" disabled={loading}>
          {loading ? <span className="ui-spinner" /> : null}
          Log In
        </button>
        <div className="signin-row">
          <label className="ui-check">
            <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
            Keep me signed in
          </label>
          <Link href="/forgot-password" className="auth-link">
            Forgot password?
          </Link>
        </div>
      </form>
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
