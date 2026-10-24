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
  /** False for people who only sign in with Google, Microsoft, Apple or single sign-on. */
  has_password?: boolean;
  last_login_at?: string | null;
}

export interface SubscriptionState {
  plan: "trial" | "starter" | "pro" | "enterprise";
  plan_name: string;
  status: "trialing" | "active" | "past_due" | "canceled" | "expired";
  read_only: boolean;
  trial_ends_at: string | null;
  days_left: number | null;
  features: string[];
  /** Active product add-ons (credit_pack, ai_words, conversation_intelligence, advanced_data). */
  addons?: string[];
  limits: Record<string, number | null>;
  provider: string;
}

export interface CurrentSession {
  subscription: SubscriptionState;
  is_sandbox: boolean;
  hipaa_mode: boolean;
  /** The workspace requires two-step verification (Settings → Security or HIPAA). */
  mfa_enforced?: boolean;
  /** Signed in as AIVA support through a support-access grant. */
  support_access?: boolean;
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

export type SignInProvider = "google" | "microsoft" | "apple" | "sso";

export interface ProviderAvailability {
  google: boolean;
  microsoft: boolean;
  apple: boolean;
  sso: boolean;
}

/** Which sign-in buttons work on this server. */
export function fetchProviders() {
  return sessionRequest<ProviderAvailability>("providers");
}

/** The message from a failed Google / Microsoft / Apple / SSO sign-in, once. */
export async function fetchAuthError() {
  return (await sessionRequest<{ message: string | null }>("auth-error")).message;
}

/** Finds the workspace whose single sign-on covers this email. */
export function discoverSso(email: string) {
  return sessionRequest<{ organization_name: string; provider_host: string }>("sso-discover", { email: email.trim().toLowerCase() });
}

/** Full-page navigation into the provider's sign-in. */
export function providerUrl(provider: SignInProvider, intent: "login" | "signup" | "link", next = "/", email?: string) {
  const params = new URLSearchParams({ intent, next });
  if (email) params.set("email", email.trim().toLowerCase());
  return `/api/session/oauth/${provider}/start?${params.toString()}`;
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
