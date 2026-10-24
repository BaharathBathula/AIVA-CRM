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

/** Signature headers that providers send to `/api/aiva/public/...` webhooks. */
const WEBHOOK_HEADERS = new Set([
  "x-hub-signature-256", // WhatsApp and Facebook (Meta)
  "x-hub-signature",
  "x-twilio-signature", // Twilio
  "calendly-webhook-signature", // Calendly
  "x-slack-signature", // Slack
  "x-slack-request-timestamp",
  "x-aiva-signature", // AIVA's WordPress plugin
  "x-aiva-secret",
  "x-aiva-timestamp",
]);

async function forward(request: NextRequest, body: ArrayBuffer | undefined, token?: string, organizationId?: string) {
  const path = request.nextUrl.pathname.replace(/^\/api\/aiva\/?/, "");
  const headers = new Headers();

  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  // API keys (REST API and MCP clients) pass straight through; the key itself
  // picks the workspace, so no organization header is added for them.
  const incoming = request.headers.get("authorization");
  const apiKey = !token && incoming && /^Bearer aiva_sk_[A-Za-z0-9_-]+$/.test(incoming.trim()) ? incoming.trim() : null;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  else if (apiKey) headers.set("Authorization", apiKey);
  const org = apiKey ? null : organizationId ?? FALLBACK_ORGANIZATION_ID;
  if (org) headers.set("X-Organization-ID", org);
  for (const name of ["accept", "mcp-protocol-version", "mcp-session-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const sessionId = request.cookies.get(COOKIE.session)?.value;
  if (sessionId) headers.set("X-Session-ID", sessionId);

  // Webhook signatures (Stripe, and the integrations' inbound webhooks) must
  // reach the backend untouched, or every signed delivery is rejected.
  const signature = request.headers.get("stripe-signature");
  if (signature) headers.set("Stripe-Signature", signature);
  if (path.startsWith("public/")) {
    request.headers.forEach((value, name) => {
      if (WEBHOOK_HEADERS.has(name) || name.startsWith("x-docusign-signature-")) headers.set(name, value);
    });
  }

  const context = clientContext(request);
  if (context.forwardedFor) headers.set("X-Forwarded-For", context.forwardedFor);
  if (context.userAgent) headers.set("User-Agent", context.userAgent);

  return fetch(`${BACKEND_URL}/${path}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
    // Redirects (tracked email links, sign-in flows) go back to the browser.
    redirect: "manual",
  });
}

/** Headers passed back to the browser (downloads keep their file name). */
function responseHeaders(upstream: Response) {
  const headers = new Headers({ "Content-Type": upstream.headers.get("content-type") ?? "application/json" });
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) headers.set("Content-Disposition", disposition);
  // Keep the download safety headers set by the backend (file previews).
  for (const name of ["content-security-policy", "x-content-type-options", "cache-control", "mcp-session-id", "www-authenticate", "location"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

const PORTAL_PATH = /^portal-api\/([a-z0-9-]{1,80})(?:\/(.*))?$/;

function portalCookie(slug: string) {
  return `aiva_portal_${slug}`;
}

/**
 * Customer and partner portals have their own sign-in, separate from AIVA
 * staff accounts. Their session token lives in an httpOnly cookie per portal
 * and is sent upstream as X-Portal-Token; staff credentials are never sent.
 */
async function portalProxy(request: NextRequest, slug: string, rest: string, body: ArrayBuffer | undefined) {
  const path = request.nextUrl.pathname.replace(/^\/api\/aiva\/?/, "");
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const token = request.cookies.get(portalCookie(slug))?.value;
  if (token) headers.set("X-Portal-Token", token);
  const context = clientContext(request);
  if (context.forwardedFor) headers.set("X-Forwarded-For", context.forwardedFor);
  if (context.userAgent) headers.set("User-Agent", context.userAgent);

  const upstream = await fetch(`${BACKEND_URL}/${path}${request.nextUrl.search}`, { method: request.method, headers, body, cache: "no-store" });
  const cookie = { httpOnly: true, sameSite: "lax" as const, secure: process.env.NODE_ENV === "production", path: "/" };

  if (rest === "verify" && upstream.ok) {
    const data = (await upstream.json()) as { session_token: string; expires_in: number; name: string };
    const response = NextResponse.json({ name: data.name });
    response.cookies.set(portalCookie(slug), data.session_token, { ...cookie, maxAge: data.expires_in });
    return response;
  }

  const response = new NextResponse(upstream.status === 204 ? null : upstream.body, { status: upstream.status, headers: responseHeaders(upstream) });
  if (rest === "logout" || (upstream.status === 401 && token)) response.cookies.set(portalCookie(slug), "", { ...cookie, maxAge: 0 });
  return response;
}

async function proxy(request: NextRequest) {
  // Raw bytes, so file uploads (multipart) reach the backend unchanged.
  const body = request.method !== "GET" && request.method !== "HEAD" ? await request.arrayBuffer() : undefined;

  const portal = PORTAL_PATH.exec(request.nextUrl.pathname.replace(/^\/api\/aiva\/?/, ""));
  if (portal) return portalProxy(request, portal[1], portal[2] ?? "", body);

  let token = request.cookies.get(COOKIE.access)?.value;
  let organizationId = request.cookies.get(COOKIE.organization)?.value;
  let refreshed: BackendSession | null = null;
  // Public endpoints (webhooks, sign-in callbacks, forms) never refresh or clear a
  // session: a 401 there is the endpoint's own answer, and retrying would deliver twice.
  const isPublic = request.nextUrl.pathname.replace(/^\/api\/aiva\/?/, "").startsWith("public/");

  if (!token && !isPublic && request.cookies.get(COOKIE.refresh)) {
    refreshed = await refreshSession(request);
    if (refreshed) {
      token = refreshed.access_token;
      organizationId = refreshed.organization_id;
    }
  }

  let upstream = await forward(request, body, token, organizationId);

  if (upstream.status === 401 && !refreshed && !isPublic && request.cookies.get(COOKIE.refresh)) {
    refreshed = await refreshSession(request);
    if (refreshed) {
      upstream = await forward(request, body, refreshed.access_token, refreshed.organization_id);
    }
  }

  const response = new NextResponse(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers: responseHeaders(upstream),
  });

  if (refreshed) setSessionCookies(response, refreshed, rememberChoice(request));
  else if (upstream.status === 401 && !isPublic && request.cookies.get(COOKIE.refresh)) clearSessionCookies(response);

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
