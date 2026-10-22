"use client";

import QRCode from "qrcode";
import { Laptop, ShieldCheck, ShieldOff, Smartphone } from "lucide-react";
import { useState, type FormEvent } from "react";

import { PasswordInput, PasswordStrength, passwordChecks } from "@/components/auth-layout";
import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, ConfirmDialog, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { SessionRow } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function SecurityPage() {
  return (
    <SettingsShell>
      <PageHeader
        eyebrow="Settings"
        title="Password & security"
        description="Protect your account with a strong password and two-step verification, and review where you're signed in."
      />
      <div className="ui-stack">
        <ChangePassword />
        <TwoStep />
        <Sessions />
      </div>
    </SettingsShell>
  );
}

function ChangePassword() {
  const { session } = useSession();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!passwordChecks(next, session?.user.email).every((check) => check.ok)) {
      setError("Choose a stronger password that meets all three rules.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.post("/auth/password/change", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      toast("Password changed. Other devices were signed out.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <Card
        title="Password"
        footer={
          <button className="ui-btn ui-btn-primary" type="submit" disabled={saving || !current || !next}>
            {saving ? <span className="ui-spinner" /> : null}
            Change password
          </button>
        }
      >
        {error ? (
          <div style={{ marginBottom: 14 }}>
            <Alert>{error}</Alert>
          </div>
        ) : null}
        <div className="ui-form-grid">
          <div className="ui-field">
            <label className="ui-label" htmlFor="current-password">
              Current password
            </label>
            <PasswordInput id="current-password" value={current} onChange={setCurrent} autoComplete="current-password" />
          </div>
          <div className="ui-field">
            <label className="ui-label" htmlFor="new-password">
              New password
            </label>
            <PasswordInput id="new-password" value={next} onChange={setNext} autoComplete="new-password" />
            <PasswordStrength password={next} email={session?.user.email} />
          </div>
        </div>
      </Card>
    </form>
  );
}

function TwoStep() {
  const { session, refresh } = useSession();
  const toast = useToast();
  const enabled = Boolean(session?.user.mfa_enabled);
  const [setup, setSetup] = useState<{ secret: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [disableOpen, setDisableOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ secret: string; otpauth_uri: string }>("/auth/mfa/setup");
      const qr = await QRCode.toDataURL(result.otpauth_uri, { margin: 1, width: 200 });
      setSetup({ secret: result.secret, qr });
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function enable(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/mfa/enable", { code: code.replace(/\s/g, "") });
      setSetup(null);
      setCode("");
      await refresh();
      toast("Two-step verification is on.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function disable(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/mfa/disable", { password });
      setDisableOpen(false);
      setPassword("");
      await refresh();
      toast("Two-step verification is off.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Two-step verification"
      actions={enabled ? <Badge tone="success" dot>On</Badge> : <Badge tone="warning" dot>Off</Badge>}
    >
      <div className="ui-row" style={{ alignItems: "flex-start", gap: 14 }}>
        <span className="ui-avatar" style={{ width: 40, height: 40, borderRadius: 10 }}>
          {enabled ? <ShieldCheck size={19} /> : <ShieldOff size={19} />}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ margin: "0 0 12px", fontSize: 13.5, color: "var(--text-2)", maxWidth: "62ch" }}>
            {enabled
              ? "Signing in requires your password and a 6-digit code from your authenticator app."
              : "Add a second step at sign-in using an authenticator app such as Google Authenticator, Microsoft Authenticator or 1Password."}
          </p>
          {enabled ? (
            <button type="button" className="ui-btn ui-btn-danger" onClick={() => setDisableOpen(true)}>
              Turn off
            </button>
          ) : (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => void start()} disabled={busy}>
              <Smartphone size={15} /> Set up authenticator app
            </button>
          )}
        </div>
      </div>

      <Modal
        open={Boolean(setup)}
        title="Set up your authenticator"
        description="Scan the QR code, then enter the 6-digit code the app shows."
        onClose={() => {
          setSetup(null);
          setError(null);
        }}
      >
        {setup ? (
          <form onSubmit={enable} className="ui-stack">
            {error ? <Alert>{error}</Alert> : null}
            <div className="ui-row" style={{ gap: 18, flexWrap: "wrap" }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={setup.qr} alt="QR code for your authenticator app" width={168} height={168} style={{ border: "1px solid var(--border)", borderRadius: 12 }} />
              <div style={{ minWidth: 0, flex: "1 1 200px" }}>
                <div className="ui-label">Can&apos;t scan? Enter this key</div>
                <code className="ui-mono" style={{ display: "block", marginTop: 6, wordBreak: "break-all", fontSize: 14, letterSpacing: "0.08em" }}>
                  {setup.secret.match(/.{1,4}/g)?.join(" ")}
                </code>
              </div>
            </div>
            <Field label="Verification code">
              <input
                className="ui-input auth-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={7}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ""))}
                placeholder="123 456"
                required
              />
            </Field>
            <button className="ui-btn ui-btn-primary" type="submit" disabled={busy || code.replace(/\s/g, "").length < 6}>
              {busy ? <span className="ui-spinner" /> : null}
              Verify and turn on
            </button>
          </form>
        ) : null}
      </Modal>

      <Modal
        open={disableOpen}
        size="sm"
        title="Turn off two-step verification?"
        description="Your account will be protected by your password only. Confirm your password to continue."
        onClose={() => {
          setDisableOpen(false);
          setError(null);
        }}
      >
        <form onSubmit={disable} className="ui-stack">
          {error ? <Alert>{error}</Alert> : null}
          <div className="ui-field">
            <label className="ui-label" htmlFor="mfa-password">
              Password
            </label>
            <PasswordInput id="mfa-password" value={password} onChange={setPassword} autoComplete="current-password" />
          </div>
          <button className="ui-btn ui-btn-danger solid" type="submit" disabled={busy || !password}>
            Turn off
          </button>
        </form>
      </Modal>
    </Card>
  );
}

function describeAgent(agent: string | null) {
  if (!agent) return "Unknown device";
  const browser = /Edg\//.test(agent)
    ? "Edge"
    : /Chrome\//.test(agent)
      ? "Chrome"
      : /Firefox\//.test(agent)
        ? "Firefox"
        : /Safari\//.test(agent)
          ? "Safari"
          : agent.split(" ")[0];
  const os = /Windows/.test(agent)
    ? "Windows"
    : /Mac OS X/.test(agent)
      ? "macOS"
      : /Android/.test(agent)
        ? "Android"
        : /iPhone|iPad/.test(agent)
          ? "iOS"
          : /Linux/.test(agent)
            ? "Linux"
            : "";
  return os ? `${browser} on ${os}` : browser;
}

function Sessions() {
  const toast = useToast();
  const { data, loading, reload } = useApi<SessionRow[]>("/auth/sessions");
  const [revoking, setRevoking] = useState<SessionRow | "others" | null>(null);
  const [busy, setBusy] = useState(false);
  const others = (data ?? []).filter((row) => !row.current).length;

  async function confirm() {
    if (!revoking) return;
    setBusy(true);
    try {
      if (revoking === "others") {
        await api.post("/auth/sessions/revoke-others");
        toast("Signed out of all other sessions.");
      } else {
        await api.del(`/auth/sessions/${revoking.id}`);
        toast("Session signed out.");
      }
      setRevoking(null);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Where you're signed in"
      flush
      actions={
        others ? (
          <button type="button" className="ui-btn ui-btn-sm" onClick={() => setRevoking("others")}>
            Sign out other sessions
          </button>
        ) : null
      }
    >
      {loading && !data ? (
        <LoadingRows rows={3} />
      ) : (
        <div className="ui-list">
          {(data ?? []).map((row) => (
            <div key={row.id} className="ui-list-item">
              <span className="ui-avatar" style={{ borderRadius: 9, width: 34, height: 34 }}>
                <Laptop size={16} />
              </span>
              <div className="main">
                <div className="title">
                  {describeAgent(row.user_agent)} {row.current ? <Badge tone="success">This device</Badge> : null}
                </div>
                <div className="sub">
                  {row.ip_address ?? "Unknown IP"} · signed in {relativeTime(row.created_at)} · last active{" "}
                  {relativeTime(row.last_used_at ?? row.created_at)}
                </div>
              </div>
              {row.current ? null : (
                <button type="button" className="ui-btn ui-btn-sm ui-btn-danger" onClick={() => setRevoking(row)}>
                  Sign out
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={Boolean(revoking)}
        title={revoking === "others" ? "Sign out other sessions?" : "Sign out this session?"}
        message={
          revoking === "others"
            ? `Every other browser and device (${others}) will need to sign in again.`
            : "That browser or device will need to sign in again."
        }
        confirmLabel="Sign out"
        busy={busy}
        onConfirm={() => void confirm()}
        onClose={() => setRevoking(null)}
      />
    </Card>
  );
}
