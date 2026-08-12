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
import { getErrorMessage } from "@/lib/apiError";
import type { AdminPolicyRule } from "@/types";

interface DeletePolicyRuleDialogProps {
  rule: AdminPolicyRule | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (rule: AdminPolicyRule) => void | Promise<void>;
}

export function DeletePolicyRuleDialog({
  rule,
  open,
  onOpenChange,
  onConfirm,
}: DeletePolicyRuleDialogProps) {
  const [isDeleting, setIsDeleting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setIsDeleting(false);
      setError(null);
    }
  }, [open]);

  if (!rule) return null;

  const handleConfirm = async () => {
    setError(null);
    setIsDeleting(true);
    try {
      await onConfirm(rule);
      onOpenChange(false);
    } catch (err) {
      setError(getErrorMessage(err, "Something went wrong. Try again."));
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader className="pr-8">
          <DialogTitle>Delete policy rule</DialogTitle>
          <DialogDescription>
            The rule for {rule.category} ({rule.gradeApplicable}) will be retired and no longer
            enforced on new claims. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>

        <div className="flex gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
          <p className="text-sm text-muted-foreground">
            Past claims decided under this rule keep referencing it — only future evaluations stop
            applying it.
          </p>
        </div>

        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}

        <DialogFooter className="border-t pt-4">
          <DialogClose asChild>
            <Button type="button" variant="outline">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="destructive"
            onClick={handleConfirm}
            disabled={isDeleting}
          >
            <Trash2 />
            Delete rule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
