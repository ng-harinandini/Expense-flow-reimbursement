import { useQuery } from "@tanstack/react-query";

import type { UserRole } from "@/types";

import { apiRequest } from "./client";

export interface RoleOption {
  id: number;
  name: UserRole;
  description: string | null;
}

export function fetchRoles(): Promise<RoleOption[]> {
  return apiRequest<RoleOption[]>("/directory/roles");
}

export function useRolesQuery() {
  return useQuery({
    queryKey: ["directory", "roles"],
    queryFn: fetchRoles,
  });
}
