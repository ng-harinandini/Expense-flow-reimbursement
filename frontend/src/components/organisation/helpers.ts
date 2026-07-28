import type { Employee, EmployeeGrade, EmployeeStatus, UserRole } from "@/types";

export const EMPLOYEE_GRADES: EmployeeGrade[] = [
  "L1",
  "L2",
  "L3",
  "L4",
  "L5",
  "Director",
  "VP",
];

export const USER_ROLES: UserRole[] = [
  "employee",
  "manager",
  "finance",
  "admin",
  "auditor",
];

export const EMPLOYEE_STATUSES: EmployeeStatus[] = ["active", "inactive"];

export const ROLE_LABELS: Record<UserRole, string> = {
  employee: "Employee",
  manager: "Manager",
  finance: "Finance",
  admin: "Admin",
  auditor: "Auditor",
};

export const STATUS_LABELS: Record<EmployeeStatus, string> = {
  active: "Active",
  inactive: "Inactive",
};

/** 'emp-101' -> 'EMP-101' */
export function formatEmployeeId(id: string) {
  return id.toUpperCase();
}

export function formatUsd(amount: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(amount);
}

/** Next free 'emp-###' id, based on the highest numeric suffix in use. */
export function nextEmployeeId(employees: Employee[]) {
  const highest = employees.reduce((max, employee) => {
    const suffix = Number.parseInt(employee.id.replace(/\D/g, ""), 10);
    return Number.isNaN(suffix) ? max : Math.max(max, suffix);
  }, 100);

  return `emp-${highest + 1}`;
}

/** Employees eligible to be picked as someone's manager. */
export function managerOptions(employees: Employee[], excludeId?: string) {
  return employees
    .filter(
      (employee) =>
        employee.id !== excludeId &&
        employee.status === "active" &&
        employee.role !== "employee"
    )
    .sort((a, b) => a.name.localeCompare(b.name));
}
