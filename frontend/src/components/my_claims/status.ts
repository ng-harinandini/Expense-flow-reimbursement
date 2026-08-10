import type { ClaimStatus, ExpenseItemStatus } from "@/types";

export const STATUS_LABELS: Record<ClaimStatus, string> = {
  Draft: "Draft",
  Submitted: "Submitted",
  Processing_AI: "Processing AI",
  Auto_Approved: "Auto Approved",
  Manager_Review: "Manager Review",
  Finance_Review: "Finance Review",
  Approved: "Approved",
  Rejected: "Rejected",
  Disbursed: "Disbursed",
  Flagged_Fraud: "Flagged Fraud",
  Withdrawn: "Withdrawn",
};

export const STATUS_BADGE_CLASSES: Record<ClaimStatus, string> = {
  Draft: "bg-muted text-muted-foreground border-transparent",
  Submitted: "bg-blue-500/15 text-blue-500 border-transparent",
  Processing_AI: "bg-blue-500/15 text-blue-500 border-transparent",
  Auto_Approved: "bg-emerald-500/15 text-emerald-500 border-transparent",
  Manager_Review: "bg-amber-500/15 text-amber-500 border-transparent",
  Finance_Review: "bg-amber-500/15 text-amber-500 border-transparent",
  Approved: "bg-emerald-500/15 text-emerald-500 border-transparent",
  Rejected: "bg-destructive/15 text-destructive border-transparent",
  Disbursed: "bg-secondary/15 text-secondary border-transparent",
  Flagged_Fraud: "bg-destructive/15 text-destructive border-transparent",
  Withdrawn: "bg-amber-500/15 text-amber-600 border-transparent",
};

/**
 * Per-item statuses are a separate enum from `ClaimStatus`, with overlapping member names but a
 * different set, so they get their own maps rather than being folded into the ones above.
 */
export const ITEM_STATUS_LABELS: Record<ExpenseItemStatus, string> = {
  Submitted: "Submitted",
  Auto_Approved: "Auto Approved",
  Policy_Hold: "Policy Hold",
  Fraud_Flag: "Fraud Flag",
  Manager_Approved: "Manager Approved",
  Rejected: "Rejected",
};

export const ITEM_STATUS_BADGE_CLASSES: Record<ExpenseItemStatus, string> = {
  Submitted: "bg-blue-500/15 text-blue-500 border-transparent",
  Auto_Approved: "bg-emerald-500/15 text-emerald-500 border-transparent",
  Policy_Hold: "bg-amber-500/15 text-amber-600 border-transparent",
  Fraud_Flag: "bg-destructive/15 text-destructive border-transparent",
  Manager_Approved: "bg-emerald-500/15 text-emerald-500 border-transparent",
  Rejected: "bg-destructive/15 text-destructive border-transparent",
};
