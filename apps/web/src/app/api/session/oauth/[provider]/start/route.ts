import { NextRequest, NextResponse } from "next/server";

import { OAUTH_COOKIE, PROVIDERS, authCookie, intentPage, redirectWithError } from "@/lib/oauth-server";
import { COOKIE, backendFetch, clientContext, refreshSession } from "@/lib/session-server";

type Params = { params: Promise<{ provider: string }> };

/**
 * Starts "Sign in with Google / Microsoft / Apple / your organization":
 * asks the API for the provider's sign-in URL, keeps the signed state in an
 * httpOnly cookie for the return trip, and sends the browser to the provider.
 */
export async function GET(request: NextRequest, { params }: Params) {
  const { provider } = await params;
  const intent = request.nextUrl.searchParams.get("intent") ?? "login";
  const back = intentPage(intent);
  if (!PROVIDERS.has(provider) || !["login", "signup", "link"].includes(intent)) {
    return redirectWithError(request, back, "Unknown sign-in method.");
  }

  let response: Response;
  if (intent === "link") {
    let token = request.cookies.get(COOKIE.access)?.value;
    let organizationId = request.cookies.get(COOKIE.organization)?.value;
    if (!token) {
      const refreshed = await refreshSession(request);
      token = refreshed?.access_token;
      organizationId = refreshed?.organization_id;
    }
    if (!token) return redirectWithError(request, "/login", "Sign in first, then connect the account.");
    response = await backendFetch("/auth/oauth/link", {
      method: "POST",
      body: JSON.stringify({ provider }),
      token,
      organizationId,
      ...clientContext(request),
    });
  } else {
    response = await backendFetch("/auth/oauth/start", {
      method: "POST",
      body: JSON.stringify({
        provider,
        intent,
        next: request.nextUrl.searchParams.get("next") ?? "/",
        email: request.nextUrl.searchParams.get("email"),
      }),
      ...clientContext(request),
    });
  }

  const data = (await response.json().catch(() => ({}))) as {
    authorization_url?: string;
    state_token?: string;
    cookie_same_site?: "lax" | "none";
    detail?: string;
  };
  if (!response.ok || !data.authorization_url || !data.state_token) {
    return redirectWithError(request, back, typeof data.detail === "string" ? data.detail : "That sign-in method isn't available right now.");
  }

  const redirect = NextResponse.redirect(data.authorization_url, 303);
  redirect.cookies.set(OAUTH_COOKIE.state, data.state_token, { ...authCookie(data.cookie_same_site ?? "lax"), maxAge: 600 });
  return redirect;
}
