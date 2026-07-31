"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";
import { Layers, Mail, User, UserCog } from "lucide-react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/Label";
import { useRolesQuery } from "@/api/roles";
import type { Employee, UserRole } from "@/types";

import {
  EMPLOYEE_FORM_DEFAULTS,
  employeeSchema,
  type EmployeeFormValues,
} from "./employeeSchema";
import {
  EMPLOYEE_GRADES,
  EMPLOYEE_STATUSES,
  ROLE_LABELS,
  STATUS_LABELS,
  USER_ROLES,
  formatEmployeeId,
} from "./helpers";

const SELECT_CLASSES =
  "flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent py-1 pr-3 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive dark:bg-input/30";

function toFormValues(employee: Employee): EmployeeFormValues {
  return {
    name: employee.name,
    email: employee.email,
    grade: employee.grade,
    role: employee.role,
    isActive: employee.status === "active",
    managerId: employee.managerId ?? "",
  };
}

interface EmployeeFormDialogProps {
  employee: Employee | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  managers: Employee[];
  takenEmails: string[];
  onSave: (values: EmployeeFormValues) => void | Promise<void>;
}

export function EmployeeFormDialog({
  employee,
  open,
  onOpenChange,
  managers,
  takenEmails,
  onSave,
}: EmployeeFormDialogProps) {
  const isEditing = Boolean(employee);
  const { data: fetchedRoles } = useRolesQuery();
  const roleOptions: {
    key: number | UserRole;
    value: UserRole;
    label: string;
  }[] = fetchedRoles?.length
    ? fetchedRoles.map((role) => ({
        key: role.id,
        value: role.name,
        label: ROLE_LABELS[role.name] ?? role.name,
      }))
    : USER_ROLES.map((role) => ({
        key: role,
        value: role,
        label: ROLE_LABELS[role],
      }));

  const {
    register,
    handleSubmit,
    reset,
    setError,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<EmployeeFormValues>({
    resolver: yupResolver(employeeSchema),
    defaultValues: EMPLOYEE_FORM_DEFAULTS,
  });

  const isActive = watch("isActive");

  const [submitError, setSubmitError] = React.useState<string | null>(null);

  // Reload the form each time the dialog opens so add and edit never leak state.
  React.useEffect(() => {
    if (!open) return;
    setSubmitError(null);
    reset(employee ? toFormValues(employee) : EMPLOYEE_FORM_DEFAULTS);
  }, [open, employee, reset]);

  const onSubmit = async (values: EmployeeFormValues) => {
    const email = values.email.trim().toLowerCase();

    if (takenEmails.some((taken) => taken.toLowerCase() === email)) {
      setError("email", {
        type: "manual",
        message: "Another employee already uses this email",
      });
      return;
    }

    setSubmitError(null);
    try {
      await onSave({ ...values, email });
      onOpenChange(false);
    } catch (error) {
      setSubmitError(
        error instanceof Error
          ? error.message
          : "Something went wrong. Try again.",
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader className="pr-8">
          <DialogTitle>
            {isEditing ? "Edit employee" : "Add new user"}
          </DialogTitle>
          <DialogDescription>
            {isEditing && employee
              ? `Update the directory record for ${employee.name} (${formatEmployeeId(employee.id)}).`
              : "Create a directory record. An employee ID is assigned automatically."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-5">
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="name">Full name</Label>
              <div className="relative">
                <User className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="name"
                  placeholder="e.g. Sarah Chen"
                  aria-invalid={!!errors.name}
                  className="pl-9"
                  {...register("name")}
                />
              </div>
              {errors.name && (
                <p className="text-xs text-destructive">
                  {errors.name.message}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="email">Work email</Label>
              <div className="relative">
                <Mail className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="email"
                  type="email"
                  placeholder="e.g. sarah.chen@acme.com"
                  aria-invalid={!!errors.email}
                  className="pl-9"
                  {...register("email")}
                />
              </div>
              {errors.email && (
                <p className="text-xs text-destructive">
                  {errors.email.message}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="grade">Grade</Label>
              <div className="relative">
                <Layers className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <select
                  id="grade"
                  aria-invalid={!!errors.grade}
                  className={`${SELECT_CLASSES} pl-9`}
                  {...register("grade")}
                >
                  <option value="">Select a grade</option>
                  {EMPLOYEE_GRADES.map((grade) => (
                    <option key={grade} value={grade}>
                      {grade}
                    </option>
                  ))}
                </select>
              </div>
              {errors.grade && (
                <p className="text-xs text-destructive">
                  {errors.grade.message}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="role">Role</Label>
              <select
                id="role"
                aria-invalid={!!errors.role}
                className={`${SELECT_CLASSES} pl-3`}
                {...register("role")}
              >
                <option value="">Select a role</option>
                {roleOptions.map((role) => (
                  <option key={role.key} value={role.value}>
                    {role.label}
                  </option>
                ))}
              </select>
              {errors.role && (
                <p className="text-xs text-destructive">
                  {errors.role.message}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="managerId">Reporting manager</Label>
              <div className="relative">
                <UserCog className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <select
                  id="managerId"
                  className={`${SELECT_CLASSES} pl-9`}
                  {...register("managerId")}
                >
                  <option value="">No manager</option>
                  {managers
                    .filter((manager) => manager.employeeRecordId)
                    .map((manager) => (
                      <option
                        key={manager.employeeRecordId}
                        value={manager.employeeRecordId}
                      >
                        {manager.name} · {manager.grade}
                      </option>
                    ))}
                </select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="isActive">Status</Label>
              <select
                id="isActive"
                aria-invalid={!!errors.isActive}
                className={`${SELECT_CLASSES} pl-3`}
                value={isActive ? "active" : "inactive"}
                onChange={(e) =>
                  setValue("isActive", e.target.value === "active", {
                    shouldDirty: true,
                  })
                }
              >
                {EMPLOYEE_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {STATUS_LABELS[status]}
                  </option>
                ))}
              </select>
              {errors.isActive && (
                <p className="text-xs text-destructive">
                  {errors.isActive.message}
                </p>
              )}
            </div>
          </div>

          {submitError && (
            <p className="text-sm text-destructive" role="alert">
              {submitError}
            </p>
          )}

          <DialogFooter className="border-t pt-4">
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={isSubmitting}>
              {isEditing ? "Save changes" : "Add user"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
