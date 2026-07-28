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

import { PolicyRuleFormFields } from "./PolicyRuleFormFields";
import {
  policyRuleSchema,
  POLICY_RULE_FORM_DEFAULTS,
  type PolicyRuleFormValues,
} from "./policyRuleSchema";

interface AddPolicyRuleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (values: PolicyRuleFormValues) => void;
}

export function AddPolicyRuleDialog({ open, onOpenChange, onSave }: AddPolicyRuleDialogProps) {
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
    if (open) reset(POLICY_RULE_FORM_DEFAULTS);
  }, [open, reset]);

  const onSubmit = (values: PolicyRuleFormValues) => {
    onSave(values);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader className="pr-8">
          <DialogTitle>Add policy rule manually</DialogTitle>
          <DialogDescription>
            Fill in the rule details below, then save to add it to the policy table.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)}>
          <PolicyRuleFormFields register={register} errors={errors} idPrefix="manual" />

          <Separator className="my-6" />

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={isSubmitting}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
