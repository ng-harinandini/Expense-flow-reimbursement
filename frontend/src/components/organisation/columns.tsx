import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { Pencil, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Employee, EmployeeStatus, UserRole } from "@/types";

import {
  formatEmployeeId,
  formatUsd,
  ROLE_LABELS,
  STATUS_LABELS,
} from "./helpers";

const ROLE_BADGE_CLASSES: Record<UserRole, string> = {
  employee: "bg-muted text-muted-foreground border-transparent",
  manager: "bg-secondary/15 text-secondary border-transparent",
  finance: "bg-emerald-500/15 text-emerald-500 border-transparent",
  admin: "bg-primary/15 text-primary border-transparent",
  auditor: "bg-amber-500/15 text-amber-500 border-transparent",
};

const STATUS_BADGE_CLASSES: Record<EmployeeStatus, string> = {
  active: "bg-emerald-500/15 text-emerald-500 border-transparent",
  inactive: "bg-muted text-muted-foreground border-transparent",
};

export function RoleBadge({ role }: { role: UserRole }) {
  return (
    <Badge className={cn("font-medium", ROLE_BADGE_CLASSES[role])}>
      {ROLE_LABELS[role]}
    </Badge>
  );
}

export function StatusBadge({ status }: { status: EmployeeStatus }) {
  return (
    <Badge className={cn("font-medium", STATUS_BADGE_CLASSES[status])}>
      <span
        className={cn(
          "size-1.5 rounded-full",
          status === "active" ? "bg-emerald-500" : "bg-muted-foreground"
        )}
      />
      {STATUS_LABELS[status]}
    </Badge>
  );
}

export function buildColumnDefs(
  onEdit: (employee: Employee) => void,
  onDelete: (employee: Employee) => void
): ColDef<Employee>[] {
  return [
    {
      headerName: "Emp ID",
      field: "id",
      flex: 0.9,
      minWidth: 110,
      cellClass: "font-medium",
      valueFormatter: (params) =>
        params.value ? formatEmployeeId(params.value as string) : "",
    },
    {
      headerName: "Employee",
      field: "name",
      flex: 1.7,
      minWidth: 200,
      cellRenderer: (params: ICellRendererParams<Employee>) => (
        <div className="flex flex-col py-1 leading-tight">
          <span className="font-medium text-foreground">{params.data?.name}</span>
          <span className="text-xs text-muted-foreground">{params.data?.email}</span>
        </div>
      ),
      autoHeight: true,
    },
    {
      headerName: "Grade",
      field: "grade",
      flex: 0.7,
      minWidth: 90,
    },
    {
      headerName: "Manager",
      field: "managerName",
      flex: 1.3,
      minWidth: 150,
      valueFormatter: (params) => (params.value as string) || "—",
    },
    {
      headerName: "Role",
      field: "role",
      flex: 1,
      minWidth: 120,
      cellRenderer: (params: ICellRendererParams<Employee>) =>
        params.data ? <RoleBadge role={params.data.role} /> : null,
    },
    {
      headerName: "Status",
      field: "status",
      flex: 1,
      minWidth: 120,
      cellRenderer: (params: ICellRendererParams<Employee>) =>
        params.data ? <StatusBadge status={params.data.status} /> : null,
    },
    {
      headerName: "Monthly Spend",
      field: "monthlySpendUSD",
      flex: 1,
      minWidth: 130,
      valueFormatter: (params) =>
        params.value === undefined ? "" : formatUsd(params.value as number),
    },
    {
      headerName: "Actions",
      colId: "actions",
      flex: 0.8,
      minWidth: 100,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<Employee>) => (
        <div className="flex h-full items-center gap-1">
          <button
            type="button"
            aria-label={`Edit ${params.data?.name ?? "employee"}`}
            title="Edit employee"
            className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-secondary"
            onClick={() => params.data && onEdit(params.data)}
          >
            <Pencil className="size-4" />
          </button>
          <button
            type="button"
            aria-label={`Delete ${params.data?.name ?? "employee"}`}
            title="Delete employee"
            className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive"
            onClick={() => params.data && onDelete(params.data)}
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      ),
    },
  ];
}
