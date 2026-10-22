"use client";

import Image from "next/image";
import { Check, Eye, EyeOff, Gauge, Headset, ShieldCheck, Workflow } from "lucide-react";
import { useState, type ReactNode } from "react";

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <aside className="auth-brand">
        <Image src="/aiva-logo.png" alt="AIVA CRM" width={150} height={52} priority style={{ height: "auto", position: "relative", zIndex: 1 }} />
        <div style={{ position: "relative", zIndex: 1 }}>
          <h2>Sales, service and insight in one workspace.</h2>
          <p>Work every lead and deal, resolve customer cases on time, and see exactly where the quarter will land.</p>
        </div>
        <ul className="auth-points">
          <li>
            <Gauge size={18} />
            <span>
              <strong>Pipeline to forecast</strong>
              Lead scoring, quotes, quotas and a live commit number.
            </span>
          </li>
          <li>
            <Headset size={18} />
            <span>
              <strong>Service desk with SLAs</strong>
              Cases, queues and a knowledge base your team writes.
            </span>
          </li>
          <li>
            <Workflow size={18} />
            <span>
              <strong>Reports and automation</strong>
              Build reports, pin them to dashboards, automate the busywork.
            </span>
          </li>
          <li>
            <ShieldCheck size={18} />
            <span>
              <strong>Enterprise controls</strong>
              Roles and permissions, two-step verification, full audit log.
            </span>
          </li>
        </ul>
        <div className="auth-foot">© {new Date().getFullYear()} AIVA CRM</div>
      </aside>
      <main className="auth-panel">
        <div className="auth-card">
          <Image src="/aiva-logo.png" alt="AIVA CRM" width={110} height={38} className="auth-mobile-logo" style={{ height: "auto" }} />
          {children}
        </div>
      </main>
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
