import type { ClaimStatus } from "@/types";

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
