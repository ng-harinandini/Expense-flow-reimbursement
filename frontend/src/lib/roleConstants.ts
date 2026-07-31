/**
 * Role-driven routing.
 *
 * Roles are the five strings the backend accepts in the Cognito `custom:role_id`
 * claim (see VALID_ROLES in backend/app/core/deps.py). Keep this in sync with
 * the nav map in components/shared/Sidebar.tsx.
 */

import type { UserRole } from "@/types";

export const VALID_ROLES: readonly UserRole[] = [
  "employee",
  "manager",
  "finance",
  "admin",
  "auditor",
] as const;

export function isUserRole(value: unknown): value is UserRole {
  return typeof value === "string" && (VALID_ROLES as readonly string[]).includes(value);
}

/** The first page each role should land on after signing in. */
const LANDING_PATH_BY_ROLE: Record<UserRole, string> = {
  employee: "/my-claims",
  manager: "/approvals",
  finance: "/finance-approvals",
  admin: "/organisation",
  auditor: "/audit-logs",
};

/** Where to send a user straight after login. Falls back to /my-claims. */
export function getRedirectPathForRole(role: UserRole | null | undefined): string {
  if (!role || !isUserRole(role)) return "/my-claims";
  return LANDING_PATH_BY_ROLE[role];
}
