import { NextResponse, type NextRequest } from "next/server";

/** Cookies for the Google / Microsoft / Apple / single sign-on round trip. */
export const OAUTH_COOKIE = {
  state: "aiva_oauth",
  mfa: "aiva_mfa",
  error: "aiva_auth_error",
} as const;

export const PROVIDERS = new Set(["google", "microsoft", "apple", "sso"]);

const secure = process.env.NODE_ENV === "production";

export function authCookie(sameSite: "lax" | "none" = "lax") {
  // SameSite=None needs Secure; plain-http local development falls back to Lax.
  const site = sameSite === "none" && secure ? "none" : "lax";
  return { httpOnly: true, secure: secure || site === "none", sameSite: site as "lax" | "none", path: "/" };
}

/** Where the browser goes back to, with the message kept out of the URL. */
export function redirectWithError(request: NextRequest, path: string, message: string) {
  const url = new URL(path, request.nextUrl.origin);
  url.searchParams.set("error", "signin");
  const response = NextResponse.redirect(url, 303);
  response.cookies.set(OAUTH_COOKIE.error, message.slice(0, 400), { ...authCookie(), maxAge: 120 });
  return response;
}

export function intentPage(intent: string | null | undefined) {
  if (intent === "signup") return "/signup";
  if (intent === "link") return "/settings/security";
  return "/login";
}
