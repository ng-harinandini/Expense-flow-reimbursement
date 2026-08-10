"use client";

import * as React from "react";
import type { ColDef } from "ag-grid-community";
import { Search, UserPlus, Users } from "lucide-react";

import { DataGrid } from "@/components/shared/DataGrid";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  toEmployee,
  useAdminUsersQuery,
  useCreateAdminUserMutation,
  useDeleteAdminUserMutation,
  useUpdateAdminUserMutation,
} from "@/api/adminUsers";
import { useRolesQuery } from "@/api/roles";
import type { Employee } from "@/types";

import { buildColumnDefs } from "./columns";
import { DeleteEmployeeDialog } from "./DeleteEmployeeDialog";
import { EmployeeFormDialog } from "./EmployeeFormDialog";
import type { EmployeeFormValues } from "./employeeSchema";
import { ROLE_LABELS, managerOptions } from "./helpers";

const ALL_ROLES = "all-roles";

function Organisation() {
  const { data: usersData, isLoading } = useAdminUsersQuery();
  const { data: roles } = useRolesQuery();
  const createAdminUser = useCreateAdminUserMutation();
  const updateAdminUser = useUpdateAdminUserMutation();
  const deleteAdminUser = useDeleteAdminUserMutation();

  const [employees, setEmployees] = React.useState<Employee[]>([]);
  React.useEffect(() => {
    if (usersData) {
      setEmployees(usersData.users.map(toEmployee));
    }
  }, [usersData]);

  const [search, setSearch] = React.useState("");
  // Filter value is the stringified roles.id (or ALL_ROLES); Radix Select values are always strings.
  const [role, setRole] = React.useState<string>(ALL_ROLES);
  const activeRoleName = roles?.find((r) => String(r.id) === role)?.name;

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
    async (values: EmployeeFormValues) => {
      // ``values.managerId`` is the manager's employeeRecordId (UUID) — the select is keyed by it.
      if (editingEmployee) {
        if (!editingEmployee.employeeRecordId) return;
        await updateAdminUser.mutateAsync({
          employeeId: editingEmployee.employeeRecordId,
          payload: {
            name: values.name,
            grade: values.grade,
            role: values.role,
            managerId: values.managerId || null,
            isActive: values.isActive,
          },
        });
        return;
      }

      await createAdminUser.mutateAsync({
        email: values.email,
        name: values.name,
        grade: values.grade,
        role: values.role,
        managerId: values.managerId || undefined,
      });
    },
    [editingEmployee, createAdminUser, updateAdminUser]
  );

  const handleConfirmDelete = React.useCallback(
    async (employee: Employee) => {
      if (!employee.employeeRecordId) return;
      await deleteAdminUser.mutateAsync(employee.employeeRecordId);
    },
    [deleteAdminUser]
  );

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

      const matchesRole = role === ALL_ROLES || String(employee.roleId) === role;

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
    () => managerOptions(employees, editingEmployee?.employeeRecordId),
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
        ? employees.filter((e) => e.managerId === deletingEmployee.employeeRecordId).length
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
            <p className="text-sm text-muted-foreground">
              Manage employees, grades, reporting lines, and platform roles
            </p>
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
            {roles?.map((option) => (
              <SelectItem key={option.id} value={String(option.id)}>
                {ROLE_LABELS[option.name] ?? option.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="h-[560px] px-6 pb-6">
        <DataGrid<Employee>
          rowData={rowData}
          loading={isLoading}
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
