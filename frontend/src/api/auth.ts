/**
 * Auth API bindings — one function per endpoint in backend/app/api/auth.py.
 * Field names are camelCase to match the backend schemas exactly.
 */

import type { AuthenticatedUser } from "@/types";

import { apiRequest } from "./client";

export interface LoginResponse {
  // Present on success:
  idToken?: string | null;
  accessToken?: string | null;
  refreshToken?: string | null;
  expiresIn?: number | null;
  tokenType?: string | null;
  user?: AuthenticatedUser | null;
  // Present instead when Cognito returns a challenge (e.g. first login):
  challenge?: string | null;
  session?: string | null;
}

export interface MessageResponse {
  detail: string;
}

export const NEW_PASSWORD_REQUIRED = "NEW_PASSWORD_REQUIRED";

export function login(email: string, password: string): Promise<LoginResponse> {
  return apiRequest<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

/** Completes the first-login NEW_PASSWORD_REQUIRED challenge using the session from login(). */
export function respondChallenge(params: {
  email: string;
  session: string;
  newPassword: string;
  challenge?: string;
  name?: string;
}): Promise<LoginResponse> {
  return apiRequest<LoginResponse>("/auth/respond-challenge", {
    method: "POST",
    body: JSON.stringify({
      email: params.email,
      session: params.session,
      newPassword: params.newPassword,
      challenge: params.challenge ?? NEW_PASSWORD_REQUIRED,
      ...(params.name ? { name: params.name } : {}),
    }),
  });
}

export function forgotPassword(email: string): Promise<MessageResponse> {
  return apiRequest<MessageResponse>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function confirmForgotPassword(params: {
  email: string;
  code: string;
  newPassword: string;
}): Promise<MessageResponse> {
  return apiRequest<MessageResponse>("/auth/confirm-forgot-password", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function logout(): Promise<MessageResponse> {
  return apiRequest<MessageResponse>("/auth/logout", {
    method: "POST",
  });
}

export function fetchMe(): Promise<AuthenticatedUser> {
  return apiRequest<AuthenticatedUser>("/auth/me");
}
