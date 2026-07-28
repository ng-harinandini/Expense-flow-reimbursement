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
import type { Employee } from "@/types";

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
    status: employee.status,
    managerId: employee.managerId ?? "",
  };
}

interface EmployeeFormDialogProps {
  /** `null` puts the dialog in "add new user" mode. */
  employee: Employee | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Employees selectable as the reporting manager. */
  managers: Employee[];
  /** Emails already in use by other employees, for the duplicate check. */
  takenEmails: string[];
  onSave: (values: EmployeeFormValues) => void;
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

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<EmployeeFormValues>({
    resolver: yupResolver(employeeSchema),
    defaultValues: EMPLOYEE_FORM_DEFAULTS,
  });

  // Reload the form each time the dialog opens so add and edit never leak state.
  React.useEffect(() => {
    if (!open) return;
    reset(employee ? toFormValues(employee) : EMPLOYEE_FORM_DEFAULTS);
  }, [open, employee, reset]);

  const onSubmit = (values: EmployeeFormValues) => {
    const email = values.email.trim().toLowerCase();

    if (takenEmails.some((taken) => taken.toLowerCase() === email)) {
      setError("email", {
        type: "manual",
        message: "Another employee already uses this email",
      });
      return;
    }

    onSave({ ...values, email });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader className="pr-8">
          <DialogTitle>{isEditing ? "Edit employee" : "Add new user"}</DialogTitle>
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
                <p className="text-xs text-destructive">{errors.name.message}</p>
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
                <p className="text-xs text-destructive">{errors.email.message}</p>
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
                <p className="text-xs text-destructive">{errors.grade.message}</p>
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
                {USER_ROLES.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </select>
              {errors.role && (
                <p className="text-xs text-destructive">{errors.role.message}</p>
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
                  {managers.map((manager) => (
                    <option key={manager.id} value={manager.id}>
                      {manager.name} · {manager.grade}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="status">Status</Label>
              <select
                id="status"
                aria-invalid={!!errors.status}
                className={`${SELECT_CLASSES} pl-3`}
                {...register("status")}
              >
                {EMPLOYEE_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {STATUS_LABELS[status]}
                  </option>
                ))}
              </select>
              {errors.status && (
                <p className="text-xs text-destructive">{errors.status.message}</p>
              )}
            </div>
          </div>

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
