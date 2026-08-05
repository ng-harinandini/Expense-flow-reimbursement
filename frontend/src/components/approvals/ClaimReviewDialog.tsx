"use client";

import * as React from "react";
import { Check, ExternalLink, RotateCcw, X } from "lucide-react";

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
import type { Claim } from "@/types";
import {
  formatCurrency,
  formatDate,
  StatusBadge,
} from "@/components/my_claims/columns";

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
  onApprove: (claim: Claim) => void;
  onReject: (claim: Claim, reason: string) => void;
  onSendBack: (claim: Claim, reason: string) => void;
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

  // Reset to the first expense item and clear any in-progress reason whenever
  // a (new) claim is opened.
  React.useEffect(() => {
    if (open && claim && claim.items.length > 0) {
      setSelectedItemId(claim.items[0].id);
    }
    setPendingAction(null);
    setReason("");
  }, [open, claim]);

  if (!claim) return null;

  const totalAmount = claim.totalAmount;
  const selectedItem =
    claim.items.find((item) => item.id === selectedItemId) ?? claim.items[0];

  const handleApprove = () => {
    onApprove(claim);
    onOpenChange(false);
  };

  const handleConfirmReason = () => {
    if (!pendingAction || !reason.trim()) return;
    if (pendingAction === "reject") onReject(claim, reason.trim());
    if (pendingAction === "send_back") onSendBack(claim, reason.trim());
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader className="pr-8">
          <DialogTitle className="text-xl">{claim.employeeName}</DialogTitle>
          <p className="text-sm text-muted-foreground">
            {claim.claimTitle} · {claim.claimNumber} · {formatCurrency(totalAmount)}
          </p>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
          <div className="flex flex-col gap-3">
            {claim.items.length > 1 && (
              <Select value={selectedItem?.id} onValueChange={setSelectedItemId}>
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
            )}

            {selectedItem && (
              <div className="relative">
                <img
                  src={selectedItem.receiptUrl}
                  alt={`Receipt for ${selectedItem.merchantVendor}`}
                  className="h-72 w-full rounded-lg border object-cover"
                />
                <a
                  href={selectedItem.receiptUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="absolute top-2 right-2 inline-flex items-center gap-1.5 rounded-md bg-secondary px-2.5 py-1.5 text-xs font-medium text-secondary-foreground shadow-xs hover:bg-secondary/90"
                >
                  <ExternalLink className="size-3.5" />
                  Open
                </a>
              </div>
            )}
          </div>

          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border bg-muted/30 p-3">
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Claim period
                </p>
                <p className="text-sm font-medium text-foreground">
                  {formatDate(claim.fromDate)} – {formatDate(claim.toDate)}
                </p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Status
                </p>
                <StatusBadge status={claim.status} />
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Total amount
                </p>
                <p className="text-sm font-semibold text-foreground">
                  {formatCurrency(totalAmount)}
                </p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Expense items
                </p>
                <p className="text-sm font-medium text-foreground">{claim.items.length}</p>
              </div>
            </div>

            {selectedItem && (
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
            )}
          </div>
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
                />
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => {
                    setPendingAction(null);
                    setReason("");
                  }}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  disabled={!reason.trim()}
                  onClick={handleConfirmReason}
                >
                  {REASON_COPY[pendingAction].confirmLabel}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="outline" onClick={() => setPendingAction("send_back")}>
                <RotateCcw />
                Send to Employee
              </Button>
              <Button
                variant="outline"
                className="text-destructive hover:text-destructive"
                onClick={() => setPendingAction("reject")}
              >
                <X />
                Reject
              </Button>
              <Button onClick={handleApprove}>
                <Check />
                Approve
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
