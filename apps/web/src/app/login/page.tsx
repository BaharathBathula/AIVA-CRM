"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole, ArrowRight, ShieldCheck } from "lucide-react";

import { loginToAiva } from "@/lib/auth";

export default function AivaLoginPage() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);

    try {
      await loginToAiva({
        email: email.trim().toLowerCase(),
        password,
        organization_id: organizationId.trim(),
      });

      router.push("/opportunities");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to sign in. Please try again."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "linear-gradient(135deg, #0f172a, #1e293b)",
        fontFamily: "Arial, sans-serif",
      }}
    >
      <section
        style={{
          width: "100%",
          maxWidth: 440,
          padding: 36,
          borderRadius: 20,
          background: "#ffffff",
          boxShadow: "0 25px 80px rgba(0,0,0,0.25)",
        }}
      >
        <div
          style={{
            width: 54,
            height: 54,
            borderRadius: 14,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#eef2ff",
            color: "#4338ca",
            marginBottom: 20,
          }}
        >
          <LockKeyhole size={26} />
        </div>

        <h1
          style={{
            fontSize: 28,
            fontWeight: 750,
            color: "#0f172a",
            margin: 0,
          }}
        >
          Welcome to AIVA
        </h1>

        <p
          style={{
            color: "#64748b",
            fontSize: 14,
            marginTop: 10,
            marginBottom: 28,
          }}
        >
          Sign in to your AI-powered CRM workspace.
        </p>

        <form onSubmit={handleLogin}>
          <label
            htmlFor="aiva-email"
            style={{
              display: "block",
              fontWeight: 600,
              color: "#334155",
              marginBottom: 8,
              fontSize: 13,
            }}
          >
            Email address
          </label>

          <input
            id="aiva-email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@company.com"
            style={inputStyle}
          />

          <label
            htmlFor="aiva-password"
            style={labelStyle}
          >
            Password
          </label>

          <input
            id="aiva-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Enter your password"
            style={inputStyle}
          />

          <label
            htmlFor="aiva-organization"
            style={labelStyle}
          >
            Organization ID
          </label>

          <input
            id="aiva-organization"
            type="text"
            required
            value={organizationId}
            onChange={(event) => setOrganizationId(event.target.value)}
            placeholder="Organization UUID"
            style={inputStyle}
          />

          {error && (
            <p
              role="alert"
              style={{
                padding: 12,
                borderRadius: 8,
                background: "#fef2f2",
                color: "#b91c1c",
                fontSize: 13,
                marginTop: 16,
              }}
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 10,
              marginTop: 24,
              padding: 14,
              border: 0,
              borderRadius: 10,
              background: "#4338ca",
              color: "#ffffff",
              fontWeight: 700,
              cursor: loading ? "wait" : "pointer",
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? "Signing in..." : "Sign In"}
            {!loading && <ArrowRight size={17} />}
          </button>
        </form>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginTop: 26,
            color: "#64748b",
            fontSize: 12,
          }}
        >
          <ShieldCheck size={16} />
          Secure AIVA CRM authentication
        </div>
      </section>
    </main>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "12px 14px",
  border: "1px solid #cbd5e1",
  borderRadius: 9,
  outlineColor: "#6366f1",
  fontSize: 14,
  color: "#0f172a",
  background: "#ffffff",
  boxSizing: "border-box",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontWeight: 600,
  color: "#334155",
  marginBottom: 8,
  marginTop: 18,
  fontSize: 13,
};
