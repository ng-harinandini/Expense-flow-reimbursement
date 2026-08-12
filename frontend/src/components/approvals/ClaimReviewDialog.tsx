"use client";

import * as React from "react";
import { Check, RotateCcw, X } from "lucide-react";

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
  formatDateTime,
} from "@/components/my_claims/columns";
import { ClaimStatusStepper } from "@/components/my_claims/ClaimStatusStepper";

import { isManagerActionable } from "./columns";

type PendingAction = "reject" | "send_back" | null;

const REASON_COPY: Record<Exclude<PendingAction, null>, { label: string; placeholder: string; confirmLabel: string }> = {
  reject: {
    label: "Reason for rejecting this claim",
    placeholder: "Explain why this claim is being rejected...",
    confirmLabel: "Confirm rejection",
  },
  send_back: {
    label: "Reason for sending this back to the employee",
    placeholder: "Let the employee know what needs to change...",
    confirmLabel: "Confirm send back",
  },
};

interface ClaimReviewDialogProps {
  claim: Claim | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApprove: (claim: Claim) => void | Promise<void>;
  onReject: (claim: Claim, reason: string) => void | Promise<void>;
  onSendBack: (claim: Claim, reason: string) => void | Promise<void>;
}

export function ClaimReviewDialog({
  claim,
  open,
  onOpenChange,
  onApprove,
  onReject,
  onSendBack,
}: ClaimReviewDialogProps) {
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

  const selectedItem =
    claim.items.find((item) => item.id === selectedItemId) ?? claim.items[0];
  const isWithdrawn = claim.status === "Withdrawn";
  const actionable = isManagerActionable(claim);
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

  const handleConfirmReason = async () => {
    if (!pendingAction || !reason.trim()) return;
    setIsSubmitting(true);
    try {
      if (pendingAction === "reject") await onReject(claim, reason.trim());
      if (pendingAction === "send_back") await onSendBack(claim, reason.trim());
      onOpenChange(false);
    } finally {
      setIsSubmitting(false);
    }
  };

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
            {formatCurrency(claim.totalAmount)}
          </p>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[auto_22rem_1fr]">
          {/* Column 1 — progress stepper */}
          <ClaimStatusStepper claim={claim} className="lg:border-r lg:pr-6" />

          {/* Column 2 — selected expense item */}
          <div className="flex flex-col gap-4 lg:border-r lg:pr-6">
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
                    {/* The item's own status, not the claim's — one item can be on a policy hold
                        while a sibling is what pushed the whole claim into fraud review. */}
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

            {isWithdrawn && (
              <div className="mt-auto rounded-lg border border-amber-500/30 bg-amber-500/10 p-4">
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
                  {REASON_COPY[pendingAction].label}
                </label>
                <Textarea
                  autoFocus
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder={REASON_COPY[pendingAction].placeholder}
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
                  onClick={handleConfirmReason}
                >
                  {isSubmitting ? "Submitting..." : REASON_COPY[pendingAction].confirmLabel}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-end gap-2">
              {!actionable && (
                <p className="mr-auto text-sm text-muted-foreground">
                  {isWithdrawn
                    ? "No further action is possible on a withdrawn claim."
                    : "This claim has moved past manager review — no further action is possible here."}
                </p>
              )}
              <Button
                variant="outline"
                disabled={actionsDisabled}
                onClick={() => setPendingAction("send_back")}
              >
                <RotateCcw />
                Send to Employee
              </Button>
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
