"use client";

import Image from "next/image";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  Eye,
  EyeOff,
  KeyRound,
  Mail,
  Phone,
  Route,
  Sparkles,
  SquareArrowOutUpRight,
  UserRound,
} from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { Alert, Field } from "@/components/ui";
import { discoverSso, fetchProviders, providerUrl, type ProviderAvailability } from "@/lib/auth";

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="signin-shell">
      <main className="signin-side">
        <header>
          <Link href="/login" className="auth-mark" aria-label="AIVA CRM">
            <Image src="/aiva-mark.png" alt="" width={32} height={32} priority />
            AIVA
          </Link>
        </header>
        <div className="signin-center">
          <div className="auth-card">{children}</div>
        </div>
        <footer className="signin-legal">
          © {new Date().getFullYear()} AIVA CRM. All rights reserved. ·{" "}
          <Link href="/pricing">Plans and pricing</Link>
        </footer>
      </main>
      <Showcase />
    </div>
  );
}

/** The product preview beside the sign-in form (hidden on phones). */
function Showcase() {
  return (
    <aside className="signin-showcase" aria-label="About AIVA">
      <div className="signin-stage">
        <div className="signin-window" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <div className="signin-preview">
          <div className="signin-preview-head">
            <span className="auth-mark">
              <Image src="/aiva-mark.png" alt="" width={20} height={20} />
              AIVA
            </span>
            <span className="signin-tabs" aria-hidden>
              <b>
                <UserRound size={12} /> Person
              </b>
              <i>
                <Building2 size={12} /> Account
              </i>
            </span>
          </div>
          <div className="signin-person">
            <strong>Olivia Johnson</strong>
            <span>Operations Director at Northwind Logistics</span>
            <em>
              <b>A</b> Lead score 92 · Hot
            </em>
          </div>
          <div className="signin-actions" aria-hidden>
            <span>
              <Phone size={15} />
              Call
            </span>
            <span>
              <Route size={15} />
              Add to journey
            </span>
            <span>
              <Mail size={15} />
              Compose email
            </span>
          </div>
          <dl className="signin-contact">
            <div>
              <Mail size={15} />
              <dt>olivia.johnson@northwind.example</dt>
              <dd>Work</dd>
            </div>
            <div>
              <Phone size={15} />
              <dt>+1 (214) 555-0147</dt>
              <dd>Mobile</dd>
            </div>
          </dl>
          <div className="signin-ai">
            <Sparkles size={14} />
            <span>
              <strong>Deal coach</strong> No reply in 6 days. A follow-up email is drafted.
            </span>
          </div>
        </div>
      </div>
      <div className="signin-pitch">
        <strong>Every Enterprise feature, free for 7 days</strong>
        <p>Sales, service, marketing and AI agents in one workspace. No card needed to start.</p>
        <Link href="/pricing" className="signin-cta">
          See plans and pricing
        </Link>
      </div>
    </aside>
  );
}

/** Log In / Sign Up switch at the top of both pages. */
export function AuthTabs({ active }: { active: "login" | "signup" }) {
  return (
    <nav className="signin-switch" aria-label="Sign in or sign up">
      <Link href="/login" aria-current={active === "login" ? "page" : undefined}>
        Log In
      </Link>
      <Link href="/signup" aria-current={active === "signup" ? "page" : undefined}>
        <SquareArrowOutUpRight size={16} /> Sign Up
      </Link>
    </nav>
  );
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" width="20" height="20" aria-hidden>
      <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.6-.4-3.9z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2c-2 1.5-4.5 2.4-7.2 2.4-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.9z" />
    </svg>
  );
}

function MicrosoftIcon() {
  return (
    <svg viewBox="0 0 22 22" width="18" height="18" aria-hidden>
      <rect x="1" y="1" width="9" height="9" fill="#F25022" />
      <rect x="12" y="1" width="9" height="9" fill="#7FBA00" />
      <rect x="1" y="12" width="9" height="9" fill="#00A4EF" />
      <rect x="12" y="12" width="9" height="9" fill="#FFB900" />
    </svg>
  );
}

function AppleIcon() {
  return (
    <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden fill="currentColor">
      <path d="M12.15 6.9c-.95 0-2.42-1.08-3.96-1.04-2.04.03-3.91 1.18-4.96 3.01-2.12 3.68-.55 9.1 1.52 12.09 1.01 1.45 2.2 3.09 3.79 3.04 1.52-.07 2.09-.99 3.93-.99 1.83 0 2.35.99 3.96.95 1.64-.03 2.68-1.48 3.68-2.95 1.16-1.69 1.64-3.33 1.66-3.42-.04-.01-3.18-1.22-3.22-4.86-.03-3.04 2.48-4.49 2.6-4.56-1.43-2.09-3.62-2.32-4.39-2.38-2-.16-3.68 1.09-4.61 1.09zm3.38-3.07c.84-1.01 1.4-2.43 1.25-3.83-1.21.05-2.66.8-3.53 1.82-.78.9-1.45 2.34-1.27 3.71 1.34.1 2.71-.69 3.55-1.7" />
    </svg>
  );
}

const PROVIDER_LABEL = { google: "Google", microsoft: "Microsoft", apple: "Apple" } as const;

/**
 * "Log In with Google / Microsoft / Apple / your Organization".
 * Buttons for providers the server isn't set up for still show, and explain
 * themselves when pressed.
 */
export function ProviderButtons({
  intent,
  next = "/",
  onOrganization,
}: {
  intent: "login" | "signup";
  next?: string;
  onOrganization: () => void;
}) {
  const [available, setAvailable] = useState<ProviderAvailability | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const verb = intent === "signup" ? "Sign up" : "Log In";

  useEffect(() => {
    let active = true;
    fetchProviders()
      .then((result) => active && setAvailable(result))
      .catch(() => active && setAvailable({ google: false, microsoft: false, apple: false, sso: true }));
    return () => {
      active = false;
    };
  }, []);

  function go(provider: keyof typeof PROVIDER_LABEL) {
    if (available && !available[provider]) {
      setNotice(
        `${PROVIDER_LABEL[provider]} sign-in isn't switched on for this AIVA server yet. Use your email and password for now — an administrator can turn it on.`
      );
      return;
    }
    window.location.assign(providerUrl(provider, intent, next));
  }

  return (
    <div className="signin-providers">
      <button type="button" className="signin-provider" onClick={() => go("google")}>
        <GoogleIcon /> {verb} with Google
      </button>
      <button type="button" className="signin-provider" onClick={() => go("microsoft")}>
        <MicrosoftIcon /> {verb} with Microsoft
      </button>
      <button type="button" className="signin-provider" onClick={() => go("apple")}>
        <AppleIcon /> {intent === "signup" ? "Sign up" : "Sign in"} with Apple
      </button>
      {intent === "login" ? (
        <button type="button" className="signin-provider" onClick={onOrganization}>
          <KeyRound size={18} /> Log In with your Organization
        </button>
      ) : null}
      {notice ? (
        <p className="signin-notice" role="status">
          {notice}
        </p>
      ) : null}
    </div>
  );
}

/** "Log In with your Organization": find the workspace's single sign-on by email. */
export function OrganizationSignIn({ next = "/", onBack }: { next?: string; onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [found, setFound] = useState<{ organization_name: string; provider_host: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function find(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await discoverSso(email);
      setFound(result);
      setBusy(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "We couldn't find your organization's sign-in.");
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Log in with your organization</h1>
      <p>Enter your work email. If your company uses single sign-on with AIVA, we&apos;ll send you to its sign-in page.</p>
      <form className="auth-form" onSubmit={find}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Work email">
          <input
            className="ui-input"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              setFound(null);
            }}
            placeholder="you@company.com"
            autoFocus
            required
          />
        </Field>
        {found ? (
          <div className="signin-found">
            <Building2 size={18} />
            <span>
              <strong>{found.organization_name}</strong>
              Signs in at {found.provider_host}
            </span>
          </div>
        ) : null}
        {found ? (
          <a className="ui-btn ui-btn-primary signin-submit" href={providerUrl("sso", "login", next, email)}>
            Continue to {found.organization_name} <ArrowRight size={16} />
          </a>
        ) : (
          <button className="ui-btn ui-btn-primary signin-submit" type="submit" disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null}
            Continue <ArrowRight size={16} />
          </button>
        )}
        <button type="button" className="ui-btn ui-btn-ghost" onClick={onBack}>
          <ArrowLeft size={16} /> Other ways to log in
        </button>
      </form>
    </>
  );
}

export function OrDivider() {
  return (
    <div className="signin-or" role="separator">
      <span>Or</span>
    </div>
  );
}

export function PasswordInput({
  value,
  onChange,
  autoComplete,
  id,
  placeholder,
  invalid,
}: {
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  id: string;
  placeholder?: string;
  invalid?: boolean;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="password-wrap">
      <input
        id={id}
        className="ui-input"
        type={visible ? "text" : "password"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        required
      />
      <button
        type="button"
        className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
        onClick={() => setVisible((current) => !current)}
        aria-label={visible ? "Hide password" : "Show password"}
      >
        {visible ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

/** Mirrors the server policy: 10+ characters, 3 of 4 character types, not the email name. */
export function passwordChecks(password: string, email = "") {
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((pattern) => pattern.test(password)).length;
  const local = email.split("@")[0]?.toLowerCase() ?? "";
  return [
    { label: "At least 10 characters", ok: password.length >= 10 },
    { label: "Three of: lowercase, uppercase, number, symbol", ok: classes >= 3 },
    {
      label: "Doesn't contain your email name",
      ok: password.length > 0 && (!local || local.length < 3 || !password.toLowerCase().includes(local)),
    },
  ];
}

export function PasswordStrength({ password, email }: { password: string; email?: string }) {
  const checks = passwordChecks(password, email);
  const passed = checks.filter((check) => check.ok).length + (password.length >= 14 ? 1 : 0);
  const level = passed <= 1 ? "weak" : passed === 2 ? "fair" : "strong";
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div className={`strength ${password ? level : ""}`} aria-hidden>
        {[0, 1, 2, 3].map((index) => (
          <i key={index} className={password && index < passed ? "on" : ""} />
        ))}
      </div>
      <ul className="policy">
        {checks.map((check) => (
          <li key={check.label} className={check.ok ? "ok" : ""}>
            <Check size={13} style={{ opacity: check.ok ? 1 : 0.35 }} />
            {check.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
