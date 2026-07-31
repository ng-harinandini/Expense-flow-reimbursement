"use client";

import * as React from "react";
import { Undo2 } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/Button";
import { INITIAL_EMPLOYEES } from "@/data/initialClaims";
import type { Claim } from "@/types";

import { formatCurrency, formatDate, getClaimTotal } from "./columns";
import { ClaimStatusStepper, getCurrentStepIndex } from "./ClaimStatusStepper";

function getCurrentStepCopy(status: Claim["status"], manager?: string): { title: string; body: string } {
  switch (status) {
    case "submitted":
      return {
        title: "Currently: Submitted",
        body: "Your claim has been received and is queued for automated checks.",
      };
    case "manager_review":
      return {
        title: "Currently: Manager review",
        body: manager
          ? `${manager} is reviewing the items on policy hold. You'll get a notification when it moves to the next step — usually within 2 business days.`
          : "Your manager is reviewing the items on policy hold. You'll get a notification when it moves to the next step.",
      };
    case "fraud_review":
      return {
        title: "Currently: Fraud review",
        body: "At least one item was flagged during fraud screening and is being investigated. Reach out to Finance if you have questions.",
      };
    case "finance_review":
      return {
        title: "Currently: Finance review",
        body: "Finance is verifying the claim ahead of disbursement. You'll be notified once it's processed.",
      };
    case "auto_approved":
      return {
        title: "Currently: Auto approved",
        body: "Every item cleared automatically, so no manual review was needed.",
      };
    case "approved":
      return {
        title: "Currently: Approved",
        body: "This claim has been approved and is queued for disbursement.",
      };
    case "rejected":
      return {
        title: "Currently: Rejected",
        body: "This claim was rejected. Check comments below for details, or contact your manager.",
      };
    case "disbursed":
      return {
        title: "Disbursed",
        body: "Payment has been issued for this claim. It should reflect in your account shortly.",
      };
    case "draft":
      return {
        title: "Currently: Draft",
        body: "This claim hasn't been submitted yet.",
      };
  }
}

interface ClaimDetailDialogProps {
  claim: Claim | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ClaimDetailDialog({ claim, open, onOpenChange }: ClaimDetailDialogProps) {
  const [selectedItemId, setSelectedItemId] = React.useState<string>("");

  // Default to the first expense item whenever a (new) claim is opened.
  React.useEffect(() => {
    if (open && claim && claim.items.length > 0) {
      setSelectedItemId(claim.items[0].id);
    }
  }, [open, claim]);

  if (!claim) return null;

  const currentIndex = getCurrentStepIndex(claim.status);
  const manager = INITIAL_EMPLOYEES.find((e) => e.id === claim.employeeId)?.managerName;
  const currentStepCopy = getCurrentStepCopy(claim.status, manager);
  const canWithdraw = currentIndex <= 1;
  const totalAmount = getClaimTotal(claim);
  const selectedItem =
    claim.items.find((item) => item.id === selectedItemId) ?? claim.items[0];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader className="pr-8">
          <DialogTitle className="text-xl">{claim.employeeName}</DialogTitle>
          <p className="text-sm text-muted-foreground">
            {claim.claimTitle} · {claim.claimNumber} · {formatCurrency(totalAmount)}
          </p>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-6 sm:grid-cols-[auto_1fr]">
          <ClaimStatusStepper
            claim={claim}
            manager={manager}
            className="sm:pr-6 sm:border-r"
          />

          <div className="flex flex-col gap-4">
            <div className="rounded-lg border bg-secondary/10 p-4">
              <p className="text-sm font-semibold text-secondary">{currentStepCopy.title}</p>
              <p className="mt-1 text-sm text-muted-foreground">{currentStepCopy.body}</p>
            </div>

            {selectedItem && (
              <>
                <div className="space-y-1.5">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Expense item
                  </p>
                  <Select value={selectedItem.id} onValueChange={setSelectedItemId}>
                    <SelectTrigger className="h-9 text-foreground">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {claim.items.map((item, index) => (
                        <SelectItem key={item.id} value={item.id}>
                          Expense Item {index + 1} — {item.merchantVendor}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Category
                    </p>
                    <p className="text-sm font-medium text-foreground">{selectedItem.category}</p>
                  </div>
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Date
                    </p>
                    <p className="text-sm font-medium text-foreground">
                      {formatDate(selectedItem.expenseDate)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Vendor
                    </p>
                    <p className="text-sm font-medium text-foreground">
                      {selectedItem.merchantVendor}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Amount
                    </p>
                    <p className="text-sm font-semibold text-foreground">
                      {formatCurrency(selectedItem.amount, selectedItem.currency)}
                    </p>
                  </div>
                  <div className="col-span-2">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Description
                    </p>
                    <p className="text-sm text-foreground">{selectedItem.description}</p>
                  </div>
                </div>

                <img
                  src={selectedItem.receiptUrl}
                  alt={`Receipt for ${selectedItem.merchantVendor}`}
                  className="max-h-48 w-full rounded-lg border object-cover"
                />
              </>
            )}

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
