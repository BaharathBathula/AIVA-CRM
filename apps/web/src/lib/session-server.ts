import type { NextRequest, NextResponse } from "next/server";

/**
 * Server-only session helpers.
 *
 * Tokens never reach browser JavaScript: the Next.js server keeps the access
 * token, rotating refresh token, workspace and session id in httpOnly
 * cookies, and the /api/aiva proxy attaches them to backend requests.
 */

export const BACKEND_URL =
  process.env.AIVA_INTERNAL_API_URL ?? "http://127.0.0.1:8000/api/v1";

export const COOKIE = {
  access: "aiva_at",
  refresh: "aiva_rt",
  organization: "aiva_org",
  session: "aiva_sid",
  remember: "aiva_remember",
} as const;

const THIRTY_DAYS = 60 * 60 * 24 * 30;

export interface BackendSession {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  session_id: string;
  organization_id: string;
  user_id: string;
  role: string;
  user: { id: string; email: string; full_name: string };
  organizations: { id: string; name: string; slug: string; role: string }[];
}

function baseCookie() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
  };
}

export function setSessionCookies(
  response: NextResponse,
  session: BackendSession,
  remember: boolean
) {
  const persistent = remember ? { maxAge: THIRTY_DAYS } : {};

  response.cookies.set(COOKIE.access, session.access_token, {
    ...baseCookie(),
    maxAge: Math.max(60, session.expires_in - 30),
  });
  response.cookies.set(COOKIE.refresh, session.refresh_token, { ...baseCookie(), ...persistent });
  response.cookies.set(COOKIE.organization, session.organization_id, { ...baseCookie(), ...persistent });
  response.cookies.set(COOKIE.session, session.session_id, { ...baseCookie(), ...persistent });
  response.cookies.set(COOKIE.remember, remember ? "1" : "0", { ...baseCookie(), ...persistent });
}

export function clearSessionCookies(response: NextResponse) {
  for (const name of Object.values(COOKIE)) {
    response.cookies.set(name, "", { ...baseCookie(), maxAge: 0 });
  }
}

export function sessionPayload(session: BackendSession) {
  return {
    user: session.user,
    organization_id: session.organization_id,
    role: session.role,
    organizations: session.organizations,
  };
}

export async function backendFetch(
  path: string,
  init: RequestInit & { token?: string; organizationId?: string; sessionId?: string; forwardedFor?: string; userAgent?: string } = {}
) {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (init.token) headers.set("Authorization", `Bearer ${init.token}`);
  if (init.organizationId) headers.set("X-Organization-ID", init.organizationId);
  if (init.sessionId) headers.set("X-Session-ID", init.sessionId);
  if (init.forwardedFor) headers.set("X-Forwarded-For", init.forwardedFor);
  if (init.userAgent) headers.set("User-Agent", init.userAgent);

  return fetch(`${BACKEND_URL}${path}`, { ...init, headers, cache: "no-store" });
}

export function clientContext(request: NextRequest) {
  return {
    forwardedFor:
      request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
      request.headers.get("x-real-ip") ??
      undefined,
    userAgent: request.headers.get("user-agent") ?? undefined,
  };
}

/** Exchange the refresh cookie for a new access token. */
export async function refreshSession(request: NextRequest, organizationId?: string) {
  const refreshToken = request.cookies.get(COOKIE.refresh)?.value;
  if (!refreshToken) return null;

  const response = await backendFetch("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({
      refresh_token: refreshToken,
      organization_id: organizationId ?? request.cookies.get(COOKIE.organization)?.value ?? null,
    }),
    ...clientContext(request),
  });
  if (!response.ok) return null;
  return (await response.json()) as BackendSession;
}

export function rememberChoice(request: NextRequest) {
  return request.cookies.get(COOKIE.remember)?.value !== "0";
}
