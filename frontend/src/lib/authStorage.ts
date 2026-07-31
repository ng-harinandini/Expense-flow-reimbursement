import { deleteCookie, getCookie, setCookie } from "cookies-next/client";

import type { AuthenticatedUser } from "@/types";

export const CHALLENGE_STORAGE_KEY = "pendingPasswordChallenge";

const TOKEN_MAX_AGE_SECONDS = 60 * 60;

export const ACCESS_TOKEN_COOKIE = "accessToken";

/** Holds the JSON-serialised AuthenticatedUser. */
export const USER_COOKIE = "authUser";

const COOKIE_OPTIONS = {
  path: "/",
  sameSite: "lax",
  secure: process.env.NODE_ENV === "production",
} as const;

export interface StoredSession {
  accessToken: string;
  user: AuthenticatedUser;
  expiresIn?: number | null;
}

export function getAccessToken(): string | null {
  return (getCookie(ACCESS_TOKEN_COOKIE) as string | undefined) ?? null;
}

export function getStoredUser(): AuthenticatedUser | null {
  const raw = getCookie(USER_COOKIE) as string | undefined;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthenticatedUser;
  } catch {
    // Corrupt entry — drop it rather than wedging every page load.
    deleteCookie(USER_COOKIE, COOKIE_OPTIONS);
    return null;
  }
}

export function saveSession({
  accessToken,
  user,
  expiresIn,
}: StoredSession): void {
  const options = {
    ...COOKIE_OPTIONS,
    maxAge: expiresIn && expiresIn > 0 ? expiresIn : TOKEN_MAX_AGE_SECONDS,
  };

  setCookie(ACCESS_TOKEN_COOKIE, accessToken, options);
  setCookie(USER_COOKIE, JSON.stringify(user), options);
}

export function saveUser(user: AuthenticatedUser): void {
  setCookie(USER_COOKIE, JSON.stringify(user), {
    ...COOKIE_OPTIONS,
    maxAge: TOKEN_MAX_AGE_SECONDS,
  });
}

export function clearSession(): void {
  deleteCookie(ACCESS_TOKEN_COOKIE, COOKIE_OPTIONS);
  deleteCookie(USER_COOKIE, COOKIE_OPTIONS);
}
