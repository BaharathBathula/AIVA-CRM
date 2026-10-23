import { NextResponse, type NextRequest } from "next/server";

/**
 * Route protection: pages need a signed-in session (refresh cookie);
 * the sign-in pages redirect to the app when a session already exists.
 */

const PUBLIC_PAGES = ["/login", "/signup", "/forgot-password", "/reset-password", "/accept-invite", "/pricing", "/f", "/u", "/portal"];
const GUEST_ONLY = ["/login", "/signup", "/forgot-password"];

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = Boolean(request.cookies.get("aiva_rt")?.value);
  const isPublic = PUBLIC_PAGES.some((page) => pathname === page || pathname.startsWith(`${page}/`));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }

  if (signedIn && GUEST_ONLY.includes(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Pages only: skip API routes, Next internals and static files.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|manifest.webmanifest|sw.js|.*\\.(?:png|jpg|jpeg|svg|gif|webp|ico|txt|js|html|webmanifest)$).*)"],
};
