"use client";

import * as React from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from "ag-grid-community";
import { Search, UserPlus, Users } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { INITIAL_EMPLOYEES_DIRECTORY } from "@/data/employees";
import type { Employee } from "@/types";

import { buildColumnDefs } from "./columns";
import { DeleteEmployeeDialog } from "./DeleteEmployeeDialog";
import { EmployeeFormDialog } from "./EmployeeFormDialog";
import type { EmployeeFormValues } from "./employeeSchema";
import {
  ROLE_LABELS,
  USER_ROLES,
  managerOptions,
  nextEmployeeId,
  orgSubheader,
} from "./helpers";

ModuleRegistry.registerModules([AllCommunityModule]);

const ALL_ROLES = "all-roles";

function Organisation() {
  // TODO: replace with GET /api/employees once the endpoint exists.
  const [employees, setEmployees] = React.useState<Employee[]>(
    INITIAL_EMPLOYEES_DIRECTORY
  );
  const [search, setSearch] = React.useState("");
  const [role, setRole] = React.useState<string>(ALL_ROLES);

  const [editingEmployee, setEditingEmployee] = React.useState<Employee | null>(null);
  const [isFormOpen, setIsFormOpen] = React.useState(false);
  const [deletingEmployee, setDeletingEmployee] = React.useState<Employee | null>(null);
  const [isDeleteOpen, setIsDeleteOpen] = React.useState(false);

  const handleAdd = React.useCallback(() => {
    setEditingEmployee(null);
    setIsFormOpen(true);
  }, []);

  const handleEdit = React.useCallback((employee: Employee) => {
    setEditingEmployee(employee);
    setIsFormOpen(true);
  }, []);

  const handleDelete = React.useCallback((employee: Employee) => {
    setDeletingEmployee(employee);
    setIsDeleteOpen(true);
  }, []);

  const handleSave = React.useCallback(
    (values: EmployeeFormValues) => {
      setEmployees((current) => {
        const manager = current.find((e) => e.id === values.managerId);
        const managerFields = {
          managerId: manager?.id,
          managerName: manager?.name,
        };

        // TODO: replace with PUT /api/employees/:id
        if (editingEmployee) {
          const updated: Employee = {
            ...editingEmployee,
            ...values,
            ...managerFields,
          };

          return current.map((employee) => {
            if (employee.id === updated.id) return updated;
            // Keep denormalised manager names in sync after a rename.
            if (employee.managerId === updated.id) {
              return { ...employee, managerName: updated.name };
            }
            return employee;
          });
        }

        // TODO: replace with POST /api/employees
        const created: Employee = {
          ...values,
          ...managerFields,
          id: nextEmployeeId(current),
          monthlySpendUSD: 0,
        };

        return [...current, created];
      });
    },
    [editingEmployee]
  );

  const handleConfirmDelete = React.useCallback((employee: Employee) => {
    // TODO: replace with DELETE /api/employees/:id
    setEmployees((current) =>
      current
        .filter((e) => e.id !== employee.id)
        .map((e) =>
          e.managerId === employee.id
            ? { ...e, managerId: undefined, managerName: undefined }
            : e
        )
    );
  }, []);

  const columnDefs = React.useMemo<ColDef<Employee>[]>(
    () => buildColumnDefs(handleEdit, handleDelete),
    [handleEdit, handleDelete]
  );

  const rowData = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return employees.filter((employee) => {
      const matchesSearch =
        !query ||
        employee.id.toLowerCase().includes(query) ||
        employee.name.toLowerCase().includes(query);

      const matchesRole = role === ALL_ROLES || employee.role === role;

      return matchesSearch && matchesRole;
    });
  }, [employees, search, role]);

  const defaultColDef = React.useMemo<ColDef>(
    () => ({
      resizable: true,
      sortable: true,
      filter: false,
    }),
    []
  );

  const managers = React.useMemo(
    () => managerOptions(employees, editingEmployee?.id),
    [employees, editingEmployee]
  );

  const takenEmails = React.useMemo(
    () =>
      employees
        .filter((employee) => employee.id !== editingEmployee?.id)
        .map((employee) => employee.email),
    [employees, editingEmployee]
  );

  const directReportCount = React.useMemo(
    () =>
      deletingEmployee
        ? employees.filter((e) => e.managerId === deletingEmployee.id).length
        : 0,
    [employees, deletingEmployee]
  );

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <Users className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">Organisation Directory</h2>
            <p className="text-sm text-muted-foreground">{orgSubheader(role)}</p>
          </div>
        </div>
        <Button className="w-full sm:w-auto" onClick={handleAdd}>
          <UserPlus />
          Add New User
        </Button>
      </div>

      <div className="flex flex-col gap-3 px-6 pb-4 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by employee ID or name..."
            className="pl-9 text-foreground"
          />
        </div>

        <Select value={role} onValueChange={setRole}>
          <SelectTrigger className="h-9 text-foreground sm:w-48">
            <SelectValue placeholder="All Roles" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_ROLES}>All Roles</SelectItem>
            {USER_ROLES.map((option) => (
              <SelectItem key={option} value={option}>
                {ROLE_LABELS[option]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="h-[560px] px-6 pb-6">
        <AgGridReact<Employee>
          theme={themeQuartz}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          getRowId={(params) => params.data.id}
          overlayNoRowsTemplate="No employees found matching your filter criteria."
          domLayout="normal"
          rowHeight={56}
          headerHeight={44}
          suppressCellFocus
        />
      </div>

      <EmployeeFormDialog
        employee={editingEmployee}
        open={isFormOpen}
        onOpenChange={setIsFormOpen}
        managers={managers}
        takenEmails={takenEmails}
        onSave={handleSave}
      />

      <DeleteEmployeeDialog
        employee={deletingEmployee}
        open={isDeleteOpen}
        onOpenChange={setIsDeleteOpen}
        directReportCount={directReportCount}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}

export default Organisation;
