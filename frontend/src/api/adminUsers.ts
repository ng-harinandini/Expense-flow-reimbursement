import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Employee, EmployeeGrade, UserRole } from "@/types";

import { apiRequest } from "./client";

export const ADMIN_USERS_QUERY_KEY = ["admin-users"] as const;

export interface AdminUserSummary {
  username: string | null;
  sub: string | null;
  email: string | null;
  role: string | null;
  employeeCode: string | null;
  enabled: boolean | null;
  status: string | null;
  employeeId: string | null;
  fullName: string | null;
  grade: string | null;
  managerId: string | null;
  managerName: string | null;
  roleId: number | null;
  isActive: boolean | null;
}

export interface AdminUserList {
  users: AdminUserSummary[];
  nextToken: string | null;
}

export interface CreateAdminUserPayload {
  email: string;
  name: string;
  grade: EmployeeGrade;
  role: UserRole;
  managerId?: string;
}

export interface UpdateAdminUserPayload {
  name?: string;
  grade?: EmployeeGrade;
  role?: UserRole;
  managerId?: string | null;
  isActive?: boolean;
}

export function fetchAdminUsers(limit = 60): Promise<AdminUserList> {
  return apiRequest<AdminUserList>(`/admin/users?limit=${limit}`);
}

export function createAdminUser(payload: CreateAdminUserPayload): Promise<AdminUserSummary> {
  return apiRequest<AdminUserSummary>("/admin/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAdminUser(
  employeeId: string,
  payload: UpdateAdminUserPayload
): Promise<AdminUserSummary> {
  return apiRequest<AdminUserSummary>(`/admin/users/${employeeId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/** Deactivates the employee (soft delete — claim history keeps the row alive). */
export function deleteAdminUser(employeeId: string): Promise<void> {
  return apiRequest<void>(`/admin/users/${employeeId}`, { method: "DELETE" });
}

/** Maps one Cognito+Employee summary row to the shape the Organisation grid renders. */
export function toEmployee(user: AdminUserSummary): Employee {
  return {
    id: user.employeeCode ?? user.sub ?? user.username ?? "",
    name: user.fullName ?? user.email ?? "Unknown",
    email: user.email ?? "",
    grade: (user.grade ?? "") as EmployeeGrade,
    role: (user.role ?? "employee") as UserRole,
    status: (user.isActive ?? user.enabled ?? true) ? "active" : "inactive",
    managerId: user.managerId ?? undefined,
    managerName: user.managerName ?? undefined,
    monthlySpendUSD: 0,
    employeeRecordId: user.employeeId ?? undefined,
    roleId: user.roleId ?? undefined,
  };
}

export function useAdminUsersQuery() {
  return useQuery({
    queryKey: ADMIN_USERS_QUERY_KEY,
    queryFn: () => fetchAdminUsers(),
  });
}

export function useCreateAdminUserMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createAdminUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_USERS_QUERY_KEY });
    },
  });
}

export function useUpdateAdminUserMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ employeeId, payload }: { employeeId: string; payload: UpdateAdminUserPayload }) =>
      updateAdminUser(employeeId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_USERS_QUERY_KEY });
    },
  });
}

export function useDeleteAdminUserMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteAdminUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_USERS_QUERY_KEY });
    },
  });
}
