"use client";

/**
 * Browser-side session helpers.
 *
 * Tokens live in httpOnly cookies managed by the Next.js server
 * (see /api/session/*), so this module only holds the signed-in user's
 * profile and permissions for rendering decisions.
 */

export interface SessionOrganization {
  id: string;
  name: string;
  slug: string;
  role: string;
}

export interface SessionUser {
  id: string;
  email: string;
  full_name: string;
  job_title?: string | null;
  phone?: string | null;
  timezone?: string | null;
  mfa_enabled?: boolean;
  last_login_at?: string | null;
}

export interface CurrentSession {
  user: SessionUser;
  organization: { id: string; name: string; slug: string; settings: Record<string, unknown> };
  role: string;
  role_name: string;
  team_id: string | null;
  permissions: string[];
  organizations: SessionOrganization[];
}

export type LoginResult =
  | { mfa_required: true; mfa_token: string; user: { email: string; full_name: string } }
  | { mfa_required?: false; user: { id: string; email: string; full_name: string }; organization_id: string; role: string };

let cachedSession: CurrentSession | null = null;

export function setCachedSession(session: CurrentSession | null) {
  cachedSession = session;
}

async function sessionRequest<T>(action: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/session/${action}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (data as { detail?: unknown }).detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "Check the highlighted fields and try again."
          : "Something went wrong. Try again."
    );
  }
  return data as T;
}

export function login(email: string, password: string, remember: boolean) {
  return sessionRequest<LoginResult>("login", { email: email.trim().toLowerCase(), password, remember });
}

export function verifyMfa(mfaToken: string, code: string, remember: boolean) {
  return sessionRequest<LoginResult>("mfa", { mfa_token: mfaToken, code, remember });
}

export function signup(payload: { full_name: string; email: string; password: string; company_name: string }) {
  return sessionRequest<LoginResult>("signup", payload);
}

export function acceptInvitation(payload: { token: string; password: string; full_name?: string }) {
  return sessionRequest<LoginResult>("accept-invite", payload);
}

export function switchOrganization(organizationId: string) {
  return sessionRequest<LoginResult>("switch-organization", { organization_id: organizationId });
}

export function fetchCurrentSession() {
  return sessionRequest<CurrentSession>("me");
}

export async function logout() {
  await sessionRequest("logout", {}).catch(() => undefined);
  cachedSession = null;
}

// ---------------------------------------------------------------------------
// Compatibility with earlier modules
// ---------------------------------------------------------------------------

/** @deprecated Sessions are cookie based now; use `login`. */
export async function loginToAiva(credentials: { email: string; password: string }) {
  return login(credentials.email, credentials.password, true);
}

export function getAivaSession() {
  if (!cachedSession) return null;
  return {
    userId: cachedSession.user.id,
    organizationId: cachedSession.organization.id,
    role: cachedSession.role,
    permissions: cachedSession.permissions,
    expiresAt: Number.POSITIVE_INFINITY,
  };
}

export function logoutFromAiva() {
  void logout();
}
