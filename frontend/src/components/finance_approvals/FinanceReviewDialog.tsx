"use client";

import * as React from "react";
import { Check, X } from "lucide-react";

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
import { Textarea } from "@/components/ui/textarea";
import { ReceiptViewer } from "@/components/ReceiptViewer";
import type { Claim } from "@/types";
import {
  ItemStatusBadge,
  StatusBadge,
  formatCurrency,
  formatDate,
} from "@/components/my_claims/columns";
import { ClaimStatusStepper } from "@/components/my_claims/ClaimStatusStepper";

import { isFinanceActionable, managerNameFor } from "./columns";

type PendingAction = "reject" | null;

interface FinanceReviewDialogProps {
  claim: Claim | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApprove: (claim: Claim) => void | Promise<void>;
  onReject: (claim: Claim, reason: string) => void | Promise<void>;
}

export function FinanceReviewDialog({
  claim,
  open,
  onOpenChange,
  onApprove,
  onReject,
}: FinanceReviewDialogProps) {
  const [selectedItemId, setSelectedItemId] = React.useState<string>("");
  const [pendingAction, setPendingAction] = React.useState<PendingAction>(null);
  const [reason, setReason] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  // Reset to the first expense item and clear any in-progress reason whenever
  // a (new) claim is opened.
  React.useEffect(() => {
    if (open && claim && claim.items.length > 0) {
      setSelectedItemId(claim.items[0].id);
    }
    setPendingAction(null);
    setReason("");
    setIsSubmitting(false);
  }, [open, claim]);

  if (!claim) return null;

  const manager = managerNameFor(claim);
  const selectedItem =
    claim.items.find((item) => item.id === selectedItemId) ?? claim.items[0];
  const actionable = isFinanceActionable(claim);
  const actionsDisabled = isSubmitting || !actionable;

  const handleApprove = async () => {
    setIsSubmitting(true);
    try {
      await onApprove(claim);
      onOpenChange(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleConfirmReject = async () => {
    if (!reason.trim()) return;
    setIsSubmitting(true);
    try {
      await onReject(claim, reason.trim());
      onOpenChange(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-[calc(100%-10rem)] overflow-y-auto sm:max-w-[70rem]">
        <DialogHeader className="pr-8">
          <div className="flex flex-wrap items-center gap-2.5">
            <DialogTitle className="text-xl">{claim.employeeName}</DialogTitle>
            <StatusBadge status={claim.status} />
          </div>
          <p className="text-sm text-muted-foreground">
            {claim.claimTitle} · {claim.claimNumber} ·{" "}
            {formatCurrency(claim.totalAmount)}
          </p>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[auto_22rem_1fr]">
          {/* Column 1 — progress stepper */}
          <ClaimStatusStepper claim={claim} manager={manager} className="lg:border-r lg:pr-6" />

          {/* Column 2 — selected expense item */}
          <div className="flex flex-col gap-4 lg:border-r lg:pr-6">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Approved by (Manager)
              </p>
              <p className="text-sm font-medium text-foreground">{manager}</p>
            </div>

            {selectedItem && (
              <>
                <div className="space-y-1.5">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Expense item
                  </p>
                  <Select value={selectedItem.id} onValueChange={setSelectedItemId}>
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
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium text-foreground">
                        {selectedItem.category}
                      </p>
                      <ItemStatusBadge status={selectedItem.status} className="text-[10px]" />
                    </div>
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
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Description
                    </p>
                    <p className="text-sm text-foreground">{selectedItem.description}</p>
                  </div>
                </div>
              </>
            )}
          </div>

          {/* Column 3 — receipt */}
          {selectedItem && (
            <div className="flex min-w-0 flex-col gap-1.5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Receipt
              </p>
              <ReceiptViewer
                key={selectedItem.id}
                fileUrl={selectedItem.receiptUrl}
                alt={`Receipt for ${selectedItem.merchantVendor}`}
                className="h-[60vh]"
              />
            </div>
          )}
        </div>

        <div className="mt-2 border-t pt-4">
          {pendingAction ? (
            <div className="space-y-3">
              <div>
                <label className="text-sm font-medium text-foreground">
                  Reason for rejecting this claim
                </label>
                <Textarea
                  autoFocus
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Explain why this claim is being rejected..."
                  className="mt-1.5 text-foreground"
                  disabled={isSubmitting}
                />
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  disabled={isSubmitting}
                  onClick={() => {
                    setPendingAction(null);
                    setReason("");
                  }}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  disabled={!reason.trim() || isSubmitting}
                  onClick={handleConfirmReject}
                >
                  {isSubmitting ? "Submitting..." : "Confirm rejection"}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-end gap-2">
              {!actionable && (
                <p className="mr-auto text-sm text-muted-foreground">
                  {claim.status === "Approved" || claim.status === "Disbursed"
                    ? "This claim has already been approved — no further action is possible."
                    : "This claim is closed — no further action is possible."}
                </p>
              )}
              <Button
                variant="outline"
                className="text-destructive hover:text-destructive"
                disabled={actionsDisabled}
                onClick={() => setPendingAction("reject")}
              >
                <X />
                Reject
              </Button>
              <Button disabled={actionsDisabled} onClick={handleApprove}>
                <Check />
                {isSubmitting ? "Submitting..." : "Approve"}
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
