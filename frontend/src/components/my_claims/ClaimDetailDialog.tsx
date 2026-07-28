"use client";

import * as React from "react";
import { Undo2 } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/Button";
import { INITIAL_EMPLOYEES } from "@/data/initialClaims";
import type { ExpenseClaim } from "@/types";

import { formatCurrency, formatDate } from "./columns";
import { ClaimStatusStepper, getCurrentStepIndex } from "./ClaimStatusStepper";

function getCurrentStepCopy(claim: ExpenseClaim): { title: string; body: string } {
  const manager = INITIAL_EMPLOYEES.find((e) => e.id === claim.employeeId)?.managerName;

  switch (claim.status) {
    case "Submitted":
      return {
        title: "Currently: Submitted",
        body: "Your claim has been received and is queued for AI scanning.",
      };
    case "Processing_AI":
      return {
        title: "Currently: AI scanning",
        body: "Our system is extracting receipt data and validating policy compliance.",
      };
    case "Manager_Review":
      return {
        title: "Currently: Manager review",
        body: manager
          ? `${manager} is reviewing this now. You'll get a notification when it moves to the next step — usually within 2 business days.`
          : "Your manager is reviewing this now. You'll get a notification when it moves to the next step.",
      };
    case "Finance_Review":
      return {
        title: "Currently: Finance review",
        body: "Finance is verifying the claim ahead of disbursement. You'll be notified once it's processed.",
      };
    case "Flagged_Fraud":
      return {
        title: "Currently: Under investigation",
        body: "This claim was flagged during fraud screening and is being investigated. Reach out to Finance if you have questions.",
      };
    case "Approved":
    case "Auto_Approved":
      return {
        title: "Currently: Approved",
        body: "This claim has been approved and is queued for disbursement.",
      };
    case "Rejected":
      return {
        title: "Currently: Rejected",
        body: "This claim was rejected. Check comments below for details, or contact your manager.",
      };
    case "Disbursed":
      return {
        title: "Disbursed",
        body: "Payment has been issued for this claim. It should reflect in your account shortly.",
      };
    default:
      return {
        title: "Currently: Draft",
        body: "This claim hasn't been submitted yet.",
      };
  }
}

interface ClaimDetailDialogProps {
  claim: ExpenseClaim | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ClaimDetailDialog({ claim, open, onOpenChange }: ClaimDetailDialogProps) {
  if (!claim) return null;

  const currentIndex = getCurrentStepIndex(claim.status);
  const currentStepCopy = getCurrentStepCopy(claim);
  const manager = INITIAL_EMPLOYEES.find((e) => e.id === claim.employeeId)?.managerName;
  const canWithdraw = currentIndex <= 1;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader className="pr-8">
          <DialogTitle className="text-xl">{claim.employeeName}</DialogTitle>
          <p className="text-sm text-muted-foreground">
            {claim.merchantVendor} · {claim.claimNumber} · {formatCurrency(claim.amount, claim.currency)}
          </p>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-6 sm:grid-cols-[auto_1fr]">
          <ClaimStatusStepper claim={claim} className="sm:pr-6 sm:border-r" />

          <div className="flex flex-col gap-4">
            <div className="rounded-lg border bg-secondary/10 p-4">
              <p className="text-sm font-semibold text-secondary">{currentStepCopy.title}</p>
              <p className="mt-1 text-sm text-muted-foreground">{currentStepCopy.body}</p>
            </div>

            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Category
                </p>
                <p className="text-sm font-medium text-foreground">{claim.category}</p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Date
                </p>
                <p className="text-sm font-medium text-foreground">{formatDate(claim.expenseDate)}</p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Manager
                </p>
                <p className="text-sm font-medium text-foreground">{manager ?? "—"}</p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Amount
                </p>
                <p className="text-sm font-semibold text-foreground">
                  {formatCurrency(claim.amount, claim.currency)}
                </p>
              </div>
            </div>

            <div className="mt-2 flex items-center border-t pt-4">
              <Button
                variant="outline"
                size="sm"
                disabled={!canWithdraw}
                className="text-destructive hover:text-destructive"
              >
                <Undo2 />
                Withdraw claim
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
