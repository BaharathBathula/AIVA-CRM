import { NextRequest, NextResponse } from "next/server";

import {
  BACKEND_URL,
  COOKIE,
  clearSessionCookies,
  clientContext,
  refreshSession,
  rememberChoice,
  setSessionCookies,
  type BackendSession,
} from "@/lib/session-server";

/**
 * Forwards browser requests to the FastAPI backend.
 *
 * Credentials come from httpOnly cookies set at sign-in. When the access
 * token has expired, the proxy refreshes it once with the refresh cookie,
 * retries the request and stores the rotated tokens.
 */

// Only used before a user has signed in (public endpoints).
const FALLBACK_ORGANIZATION_ID = process.env.AIVA_ORGANIZATION_ID;

async function forward(request: NextRequest, body: string | undefined, token?: string, organizationId?: string) {
  const path = request.nextUrl.pathname.replace(/^\/api\/aiva\/?/, "");
  const headers = new Headers();

  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  if (token) headers.set("Authorization", `Bearer ${token}`);
  const org = organizationId ?? FALLBACK_ORGANIZATION_ID;
  if (org) headers.set("X-Organization-ID", org);

  const sessionId = request.cookies.get(COOKIE.session)?.value;
  if (sessionId) headers.set("X-Session-ID", sessionId);

  const context = clientContext(request);
  if (context.forwardedFor) headers.set("X-Forwarded-For", context.forwardedFor);
  if (context.userAgent) headers.set("User-Agent", context.userAgent);

  return fetch(`${BACKEND_URL}/${path}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
  });
}

async function proxy(request: NextRequest) {
  const body = request.method !== "GET" && request.method !== "HEAD" ? await request.text() : undefined;

  let token = request.cookies.get(COOKIE.access)?.value;
  let organizationId = request.cookies.get(COOKIE.organization)?.value;
  let refreshed: BackendSession | null = null;

  if (!token && request.cookies.get(COOKIE.refresh)) {
    refreshed = await refreshSession(request);
    if (refreshed) {
      token = refreshed.access_token;
      organizationId = refreshed.organization_id;
    }
  }

  let upstream = await forward(request, body, token, organizationId);

  if (upstream.status === 401 && !refreshed && request.cookies.get(COOKIE.refresh)) {
    refreshed = await refreshSession(request);
    if (refreshed) {
      upstream = await forward(request, body, refreshed.access_token, refreshed.organization_id);
    }
  }

  const response = new NextResponse(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
  });

  if (refreshed) setSessionCookies(response, refreshed, rememberChoice(request));
  else if (upstream.status === 401 && request.cookies.get(COOKIE.refresh)) clearSessionCookies(response);

  return response;
}

export async function GET(request: NextRequest) {
  return proxy(request);
}

export async function POST(request: NextRequest) {
  return proxy(request);
}

export async function PATCH(request: NextRequest) {
  return proxy(request);
}

export async function PUT(request: NextRequest) {
  return proxy(request);
}

export async function DELETE(request: NextRequest) {
  return proxy(request);
}
