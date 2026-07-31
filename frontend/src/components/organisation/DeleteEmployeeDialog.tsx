"use client";

import * as React from "react";
import { AlertTriangle, Trash2 } from "lucide-react";

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
import type { Employee } from "@/types";

import { formatEmployeeId } from "./helpers";

interface DeleteEmployeeDialogProps {
  employee: Employee | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** How many employees currently report to this person. */
  directReportCount: number;
  onConfirm: (employee: Employee) => void;
}

export function DeleteEmployeeDialog({
  employee,
  open,
  onOpenChange,
  directReportCount,
  onConfirm,
}: DeleteEmployeeDialogProps) {
  if (!employee) return null;

  const handleConfirm = () => {
    onConfirm(employee);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader className="pr-8">
          <DialogTitle>Delete employee</DialogTitle>
          <DialogDescription>
            {employee.name} ({formatEmployeeId(employee.id)}) will be removed from the
            organisation directory. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>

        {directReportCount > 0 && (
          <div className="flex gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
            <p className="text-sm text-muted-foreground">
              {directReportCount === 1
                ? "1 employee reports"
                : `${directReportCount} employees report`}{" "}
              to {employee.name}. They will be left without a reporting manager.
            </p>
          </div>
        )}

        <DialogFooter className="border-t pt-4">
          <DialogClose asChild>
            <Button type="button" variant="outline">
              Cancel
            </Button>
          </DialogClose>
          <Button type="button" variant="destructive" onClick={handleConfirm}>
            <Trash2 />
            Delete employee
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
