import { NextRequest, NextResponse } from "next/server";

import {
  COOKIE,
  backendFetch,
  clearSessionCookies,
  clientContext,
  refreshSession,
  rememberChoice,
  sessionPayload,
  setSessionCookies,
  type BackendSession,
} from "@/lib/session-server";
import { OAUTH_COOKIE, authCookie } from "@/lib/oauth-server";

type Params = { params: Promise<{ action: string }> };

async function relay(response: Response) {
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text || "Request failed." };
  }
  return { ok: response.ok, status: response.status, body };
}

function error(status: number, body: unknown) {
  return NextResponse.json(body ?? { detail: "Request failed." }, { status });
}

async function startSession(request: NextRequest, path: string, payload: Record<string, unknown>, remember: boolean) {
  const result = await relay(
    await backendFetch(path, { method: "POST", body: JSON.stringify(payload), ...clientContext(request) })
  );
  if (!result.ok) return error(result.status, result.body);

  const data = result.body as BackendSession & { mfa_required?: boolean; mfa_token?: string };
  if (data.mfa_required) {
    return NextResponse.json({ mfa_required: true, mfa_token: data.mfa_token, user: data.user });
  }
  const response = NextResponse.json(sessionPayload(data));
  setSessionCookies(response, data, remember);
  return response;
}

export async function POST(request: NextRequest, { params }: Params) {
  const { action } = await params;
  const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
  const remember = body.remember !== false;

  switch (action) {
    case "login":
      return startSession(
        request,
        "/auth/login",
        { email: body.email, password: body.password, organization_id: body.organization_id ?? null },
        remember
      );
    case "mfa": {
      // After Google / Microsoft / Apple / SSO, the challenge waits in an httpOnly cookie.
      const fromCookie = request.cookies.get(OAUTH_COOKIE.mfa)?.value;
      const response = await startSession(
        request,
        "/auth/mfa/verify",
        { mfa_token: body.mfa_token === "cookie" ? fromCookie : body.mfa_token, code: body.code },
        remember
      );
      if (response.ok && body.mfa_token === "cookie") response.cookies.set(OAUTH_COOKIE.mfa, "", { ...authCookie(), maxAge: 0 });
      return response;
    }
    case "signup":
      return startSession(
        request,
        "/auth/signup",
        { full_name: body.full_name, email: body.email, password: body.password, company_name: body.company_name },
        true
      );
    case "accept-invite":
      return startSession(
        request,
        "/auth/invitations/accept",
        { token: body.token, password: body.password, full_name: body.full_name ?? null },
        true
      );
    case "switch-organization": {
      const refreshed = await refreshSession(request, String(body.organization_id ?? ""));
      if (!refreshed) return error(401, { detail: "Your session ended. Sign in again." });
      if (refreshed.organization_id !== body.organization_id) {
        const response = error(403, { detail: "You don't have access to that workspace." });
        setSessionCookies(response, refreshed, rememberChoice(request));
        return response;
      }
      const response = NextResponse.json(sessionPayload(refreshed));
      setSessionCookies(response, refreshed, rememberChoice(request));
      return response;
    }
    case "sso-discover": {
      const result = await relay(
        await backendFetch("/auth/sso/discover", { method: "POST", body: JSON.stringify({ email: body.email }), ...clientContext(request) })
      );
      return result.ok ? NextResponse.json(result.body) : error(result.status, result.body);
    }
    case "logout": {
      const refreshToken = request.cookies.get(COOKIE.refresh)?.value;
      if (refreshToken) {
        await backendFetch("/auth/logout", {
          method: "POST",
          body: JSON.stringify({ refresh_token: refreshToken }),
          ...clientContext(request),
        }).catch(() => undefined);
      }
      const response = NextResponse.json({ ok: true });
      clearSessionCookies(response);
      return response;
    }
    default:
      return error(404, { detail: "Unknown session action." });
  }
}

export async function GET(request: NextRequest, { params }: Params) {
  const { action } = await params;
  if (action === "providers") {
    const response = await backendFetch("/auth/providers").catch(() => null);
    const data = response?.ok ? await response.json() : { google: false, microsoft: false, apple: false, sso: true };
    return NextResponse.json(data);
  }
  if (action === "auth-error") {
    // The message from a failed provider sign-in, shown once.
    const message = request.cookies.get(OAUTH_COOKIE.error)?.value ?? null;
    const response = NextResponse.json({ message });
    response.cookies.set(OAUTH_COOKIE.error, "", { ...authCookie(), maxAge: 0 });
    return response;
  }
  if (action !== "me") return error(404, { detail: "Unknown session action." });

  const organizationId = request.cookies.get(COOKIE.organization)?.value;
  const sessionId = request.cookies.get(COOKIE.session)?.value;
  let token = request.cookies.get(COOKIE.access)?.value;
  let refreshed: BackendSession | null = null;

  if (!token) {
    refreshed = await refreshSession(request);
    if (!refreshed) {
      const response = error(401, { detail: "Sign in to continue." });
      clearSessionCookies(response);
      return response;
    }
    token = refreshed.access_token;
  }

  let result = await relay(
    await backendFetch("/auth/me", {
      token,
      organizationId: refreshed?.organization_id ?? organizationId,
      sessionId,
      ...clientContext(request),
    })
  );

  if (result.status === 401 && !refreshed) {
    refreshed = await refreshSession(request);
    if (refreshed) {
      result = await relay(
        await backendFetch("/auth/me", {
          token: refreshed.access_token,
          organizationId: refreshed.organization_id,
          sessionId,
          ...clientContext(request),
        })
      );
    }
  }

  const response = result.ok ? NextResponse.json(result.body) : error(result.status, result.body);
  if (refreshed) setSessionCookies(response, refreshed, rememberChoice(request));
  if (result.status === 401) clearSessionCookies(response);
  return response;
}
