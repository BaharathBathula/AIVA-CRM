import { NextRequest, NextResponse } from "next/server";

import { OAUTH_COOKIE, PROVIDERS, authCookie, intentPage, redirectWithError } from "@/lib/oauth-server";
import { backendFetch, clientContext, setSessionCookies, type BackendSession } from "@/lib/session-server";

type Params = { params: Promise<{ provider: string }> };

type Result =
  | { status: "session"; next: string; session: BackendSession }
  | { status: "mfa"; next: string; mfa_token: string; user: { full_name: string } }
  | { status: "linked"; provider: string }
  | { status: "error"; message: string; intent: string };

function safeNext(value: string | undefined) {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

/** The provider sends the browser back here (GET, or POST for Apple). */
async function finish(request: NextRequest, provider: string, fields: Record<string, string | null>) {
  if (!PROVIDERS.has(provider)) return redirectWithError(request, "/login", "Unknown sign-in method.");
  const stateToken = request.cookies.get(OAUTH_COOKIE.state)?.value ?? null;

  const response = await backendFetch("/auth/oauth/callback", {
    method: "POST",
    body: JSON.stringify({
      provider,
      code: fields.code,
      state: fields.state,
      state_token: stateToken,
      user: fields.user,
      error: fields.error,
    }),
    ...clientContext(request),
  });
  const result = (await response.json().catch(() => null)) as Result | null;

  let redirect: NextResponse;
  if (!response.ok || !result) {
    redirect = redirectWithError(request, "/login", "Sign-in didn't finish. Try again.");
  } else if (result.status === "error") {
    redirect = redirectWithError(request, intentPage(result.intent), result.message);
  } else if (result.status === "linked") {
    redirect = NextResponse.redirect(new URL(`/settings/security?connected=${encodeURIComponent(result.provider)}`, request.nextUrl.origin), 303);
  } else if (result.status === "mfa") {
    const url = new URL("/login", request.nextUrl.origin);
    url.searchParams.set("mfa", "1");
    url.searchParams.set("name", result.user.full_name.split(" ")[0] ?? "");
    url.searchParams.set("next", safeNext(result.next));
    redirect = NextResponse.redirect(url, 303);
    redirect.cookies.set(OAUTH_COOKIE.mfa, result.mfa_token, { ...authCookie(), maxAge: 300 });
  } else {
    redirect = NextResponse.redirect(new URL(safeNext(result.next), request.nextUrl.origin), 303);
    setSessionCookies(redirect, result.session, true);
  }
  redirect.cookies.set(OAUTH_COOKIE.state, "", { ...authCookie(), maxAge: 0 });
  return redirect;
}

export async function GET(request: NextRequest, { params }: Params) {
  const { provider } = await params;
  const query = request.nextUrl.searchParams;
  return finish(request, provider, {
    code: query.get("code"),
    state: query.get("state"),
    error: query.get("error"),
    user: null,
  });
}

export async function POST(request: NextRequest, { params }: Params) {
  const { provider } = await params;
  const form = await request.formData().catch(() => null);
  const value = (key: string) => {
    const entry = form?.get(key);
    return typeof entry === "string" ? entry : null;
  };
  return finish(request, provider, { code: value("code"), state: value("state"), error: value("error"), user: value("user") });
}
