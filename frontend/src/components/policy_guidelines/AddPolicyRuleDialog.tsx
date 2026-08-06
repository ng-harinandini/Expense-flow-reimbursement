"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/Button";
import { getErrorMessage } from "@/lib/apiError";
import type { AdminPolicyRule } from "@/types";

import { PolicyRuleFormFields } from "./PolicyRuleFormFields";
import { policyRuleToFormValues } from "./helpers";
import {
  policyRuleSchema,
  POLICY_RULE_FORM_DEFAULTS,
  type PolicyRuleFormValues,
} from "./policyRuleSchema";

interface AddPolicyRuleDialogProps {
  /** Non-null puts the dialog in edit mode, pre-filled from this rule. */
  rule?: AdminPolicyRule | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (values: PolicyRuleFormValues) => void | Promise<void>;
}

export function AddPolicyRuleDialog({
  rule,
  open,
  onOpenChange,
  onSave,
}: AddPolicyRuleDialogProps) {
  const isEditing = Boolean(rule);
  const [submitError, setSubmitError] = React.useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<PolicyRuleFormValues>({
    resolver: yupResolver(policyRuleSchema),
    defaultValues: POLICY_RULE_FORM_DEFAULTS,
  });

  React.useEffect(() => {
    if (!open) return;
    setSubmitError(null);
    reset(rule ? policyRuleToFormValues(rule) : POLICY_RULE_FORM_DEFAULTS);
  }, [open, rule, reset]);

  const onSubmit = async (values: PolicyRuleFormValues) => {
    setSubmitError(null);
    try {
      await onSave(values);
      onOpenChange(false);
    } catch (error) {
      setSubmitError(getErrorMessage(error, "Something went wrong. Try again."));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader className="pr-8">
          <DialogTitle>{isEditing ? "Edit policy rule" : "Add policy rule manually"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? "Update the rule details below, then save to publish the change."
              : "Fill in the rule details below, then save to add it to the policy table."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)}>
          <PolicyRuleFormFields register={register} errors={errors} idPrefix="manual" />

          {submitError && (
            <p className="mt-4 text-sm text-destructive" role="alert">
              {submitError}
            </p>
          )}

          <Separator className="my-6" />

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={isSubmitting}>
              {isEditing ? "Save changes" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
