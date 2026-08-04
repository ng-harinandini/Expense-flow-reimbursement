import { NextResponse, type NextRequest } from "next/server";


/** Routes under (auth) — reachable signed-out, and redirected away from once signed in. */
const AUTH_ROUTES = [
  "/login",
  "/forgot-password",
  "/reset-password",
  "/set-new-password",
];

/**
 * Route gate. Because the tokens live in cookies, this runs before the page renders:
 * signed-out users never reach the app shell, and signed-in users skip the login page.
 *
 * Presence of the cookie is all that is checked — it is not verified here. Every
 * protected API call is still authenticated server-side (backend/app/core/deps.py),
 * so a forged cookie buys nothing but an empty screen.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = Boolean(request.cookies.get('accessToken')?.value);
  const isAuthRoute = AUTH_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );

  if (!hasSession && !isAuthRoute) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  // /set-new-password is reached mid-challenge, before any session exists; leaving it
  // out of this check would bounce a signed-in user who is completing a forced reset.
  if (hasSession && isAuthRoute && pathname !== "/set-new-password") {
    return NextResponse.redirect(new URL("/my-claims", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Everything except Next internals, the favicon and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|assets|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
