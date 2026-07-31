"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { useUser } from "@/context/UserContext";

/**
 * Second line of defence for the authenticated area.
 *
 * `src/middleware.ts` already redirects cookie-less requests before the page renders,
 * so this mainly covers the in-session case the middleware cannot see: the cookie
 * expiring or being cleared while the SPA stays mounted. It also holds back the app
 * shell until the user is hydrated from the cookie, so no frame renders with a null user.
 *
 * Neither is a security boundary — every protected endpoint verifies the token
 * server-side (backend/app/core/deps.py).
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, isLoaded } = useUser();
  const router = useRouter();
  const redirected = React.useRef(false);

  React.useEffect(() => {
    if (isLoaded && !user && !redirected.current) {
      // Guard against redirecting twice for one sign-out: an explicit logout already
      // navigates to /login, and a second router.replace in the same tick cancels and
      // restarts the in-flight RSC request for that route.
      redirected.current = true;
      router.replace("/login");
    }
  }, [isLoaded, user, router]);

  if (!isLoaded || !user) {
    return null;
  }

  return <>{children}</>;
}
