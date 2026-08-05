"use client";

import React, { useEffect } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/Button";
import { ReceiptViewer } from "@/components/ReceiptViewer";
import { useWithdrawClaimMutation } from "@/api/claims";
import { getErrorMessage } from "@/lib/apiError";
import { INITIAL_EMPLOYEES } from "@/data/initialClaims";
import type { Claim } from "@/types";

import {
  StatusBadge,
  formatCurrency,
  formatDate,
  formatDateTime,
  getClaimTotal,
} from "./columns";
import { ClaimStatusStepper } from "./ClaimStatusStepper";

// Currently unused: the status card that rendered this copy is commented out in the layout below,
// pending a decision on whether it comes back alongside the header status badge.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function getCurrentStepCopy(
  status: Claim["status"],
  manager?: string,
): { title: string; body: string } {
  switch (status) {
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
    case "Withdrawn":
      return {
        title: "Currently: Withdrawn",
        body: "You withdrew this claim, so it's closed and no longer under review. Submit a new claim if you need to file these expenses again.",
      };
    default:
      return {
        title: "Currently: Draft",
        body: "This claim hasn't been submitted yet.",
      };
  }
}

// Mirrors the backend's WITHDRAWABLE_STATUSES (app/domain/claim_state_machine.py). Anything else —
// approved, disbursed, already closed, or under fraud investigation — is refused server-side, so
// the button is disabled rather than letting the user discover it through an error.
const WITHDRAWABLE_STATUSES = new Set<Claim["status"]>([
  "Submitted",
  "Processing_AI",
  "Manager_Review",
  "Finance_Review",
  "Auto_Approved",
]);

interface ClaimDetailDialogProps {
  claim: Claim | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ClaimDetailDialog({
  claim,
  open,
  onOpenChange,
}: ClaimDetailDialogProps) {
  const [selectedItemId, setSelectedItemId] = React.useState<string>("");
  const [withdrawReason, setWithdrawReason] = React.useState("");
  const [withdrawError, setWithdrawError] = React.useState<string | null>(null);

  // Default to the first expense item whenever a (new) claim is opened.
  React.useEffect(() => {
    if (open && claim && claim.items.length > 0) {
      setSelectedItemId(claim.items[0].id);
    }
  }, [open, claim]);

  useEffect(() => {
    setWithdrawError(null);
    setWithdrawReason("");
  }, [open, claim?.id]);

  const claimId = claim?.id;
  const withdrawMutation = useWithdrawClaimMutation();

  const handleWithdraw = React.useCallback(async () => {
    if (!claimId) return;
    if (!withdrawReason.trim()) {
      setWithdrawError("Reason is required to withdraw a claim.");
      return;
    }

    setWithdrawError(null);
    try {
      await withdrawMutation.mutateAsync({
        claimId,
        reason: withdrawReason.trim(),
      });
      onOpenChange(false);
    } catch (err) {
      setWithdrawError(getErrorMessage(err, "Failed to withdraw this claim."));
    }
  }, [claimId, onOpenChange, withdrawReason, withdrawMutation]);

  if (!claim) return null;

  const manager = INITIAL_EMPLOYEES.find(
    (e) => e.id === claim.employeeId,
  )?.managerName;
  const canWithdraw = WITHDRAWABLE_STATUSES.has(claim.status);
  const isWithdrawn = claim.status === "Withdrawn";
  const totalAmount = getClaimTotal(claim);
  const selectedItem =
    claim.items.find((item) => item.id === selectedItemId) ?? claim.items[0];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* The base DialogContent caps width at calc(100%-2rem); both that and the sm: breakpoint
          value have to be raised or the wider three-column layout has nowhere to go. */}
      <DialogContent className="max-h-[90vh] max-w-[calc(100%-10rem)] overflow-y-auto sm:max-w-[70rem]">
        <DialogHeader className="pr-8">
          <div className="flex flex-wrap items-center gap-2.5">
            <DialogTitle className="text-xl">{claim.employeeName}</DialogTitle>
            <StatusBadge status={claim.status} />
          </div>
          <p className="text-sm text-muted-foreground">
            {claim.claimTitle} · {claim.claimNumber} ·{" "}
            {formatCurrency(totalAmount)}
          </p>
        </DialogHeader>

        {/* Two narrow columns (stepper, item details) + one wide one for the receipt. The
            receipt column is the only `1fr` track, so it absorbs all the extra width. */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[auto_22rem_1fr]">
          {/* Column 1 — progress stepper */}
          <ClaimStatusStepper
            claim={claim}
            manager={manager}
            className="lg:border-r lg:pr-6"
          />

          {/* Column 2 — item picker, then that item's extracted details, stacked vertically */}
          <div className="flex flex-col gap-4 lg:border-r lg:pr-6">
            {/* Current-status card hidden for now — the status badge in the header covers it.
                `getCurrentStepCopy` is kept because restoring this block is the intended path. */}
            {/* <div className="rounded-lg border bg-secondary/10 p-4">
              <p className="text-sm font-semibold text-secondary">
                {currentStepCopy.title}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {currentStepCopy.body}
              </p>
            </div> */}

            {selectedItem && (
              <>
                <div className="space-y-1.5">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Expense item
                  </p>
                  <Select
                    value={selectedItem.id}
                    onValueChange={setSelectedItemId}
                  >
                    <SelectTrigger className="h-9 w-full text-foreground">
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

                <div className="flex flex-col gap-3">
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Category
                    </p>
                    <p className="text-sm font-medium text-foreground">
                      {selectedItem.category}
                    </p>
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
                      {formatCurrency(
                        selectedItem.amount,
                        selectedItem.currency,
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Description
                    </p>
                    <p className="text-sm text-foreground">
                      {selectedItem.description}
                    </p>
                  </div>
                </div>
              </>
            )}

            {isWithdrawn ? (
              <div className="mt-auto grid gap-3 border-t pt-4">
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Withdrawn on
                  </p>
                  <p className="mt-1 text-sm font-medium text-foreground">
                    {claim.withdrawnAt ? formatDateTime(claim.withdrawnAt) : "—"}
                  </p>

                  <p className="mt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Withdrawal reason
                  </p>
                  <p className="mt-1 text-sm text-foreground">
                    {claim.withdrawalReason?.trim() || "No reason was recorded."}
                  </p>
                </div>
              </div>
            ) : (
              <div className="mt-auto grid gap-3 border-t pt-4">
                <div>
                  <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Withdrawal reason
                  </label>
                  <Textarea
                    value={withdrawReason}
                    onChange={(event) => setWithdrawReason(event.target.value)}
                    placeholder="Enter why you need to withdraw this claim"
                    className={`mt-2 ${withdrawError ? "border-destructive text-destructive" : ""}`}
                    rows={3}
                    disabled={!canWithdraw || withdrawMutation.isPending}
                    aria-invalid={Boolean(withdrawError)}
                  />
                  {withdrawError && (
                    <p className="mt-2 text-sm text-destructive">
                      {withdrawError}
                    </p>
                  )}
                </div>

                <div className="flex items-center gap-3">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={
                      !canWithdraw ||
                      withdrawMutation.isPending ||
                      !withdrawReason.trim()
                    }
                    onClick={handleWithdraw}
                    className="text-destructive hover:text-destructive"
                  >
                    <Undo2 />
                    {withdrawMutation.isPending
                      ? "Withdrawing..."
                      : "Withdraw claim"}
                  </Button>
                </div>
              </div>
            )}
          </div>

          {selectedItem && (
            <div className="flex min-w-0 flex-col gap-1.5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Receipt
              </p>
              <ReceiptViewer
                key={selectedItem.id}
                fileUrl={selectedItem.receiptUrl}
                alt={`Receipt for ${selectedItem.merchantVendor}`}
                className="h-[70vh]"
              />
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
