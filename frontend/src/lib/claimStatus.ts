import type { ClaimStatus, ExpenseItemStatus } from "@/types";

/**
 * Single source of truth for claim + expense item status presentation.
 *
 * The status values mirror the `check (status in (...))` constraints on the
 * claims and expense_items tables — do not add a value here that the database
 * would reject.
 */

export const CLAIM_STATUSES: ClaimStatus[] = [
  "submitted",
  "auto_approved",
  "manager_review",
  "finance_review",
  "approved",
  "rejected",
  "disbursed",
];

export const EXPENSE_ITEM_STATUSES: ExpenseItemStatus[] = [
  "submitted",
  "auto_approved",
  "policy_hold",
  "fraud_flag",
  "manager_approved",
  "rejected",
];

export const CLAIM_STATUS_LABELS: Record<ClaimStatus, string> = {
  submitted: "Submitted",
  auto_approved: "Auto approved",
  manager_review: "Manager review",
  fraud_review: "Fraud review",
  finance_review: "Finance review",
  approved: "Approved",
  rejected: "Rejected",
  disbursed: "Disbursed",
};

export const EXPENSE_ITEM_STATUS_LABELS: Record<ExpenseItemStatus, string> = {
  submitted: "Submitted",
  auto_approved: "Cleared",
  policy_hold: "Policy hold",
  fraud_flag: "Fraud flag",
  manager_approved: "Manager approved",
  rejected: "Rejected",
};

export const CLAIM_STATUS_BADGE_CLASSES: Record<ClaimStatus, string> = {
  submitted: "bg-blue-500/15 text-blue-600 border-transparent",
  auto_approved: "bg-emerald-500/15 text-emerald-600 border-transparent",
  manager_review: "bg-amber-500/15 text-amber-600 border-transparent",
  fraud_review: "bg-destructive/15 text-destructive border-transparent",
  finance_review: "bg-amber-500/15 text-amber-600 border-transparent",
  approved: "bg-emerald-500/15 text-emerald-600 border-transparent",
  rejected: "bg-destructive/15 text-destructive border-transparent",
  disbursed: "bg-secondary/15 text-secondary border-transparent",
};

export const EXPENSE_ITEM_STATUS_BADGE_CLASSES: Record<ExpenseItemStatus, string> = {
  submitted: "bg-blue-500/15 text-blue-600 border-transparent",
  auto_approved: "bg-emerald-500/15 text-emerald-600 border-transparent",
  policy_hold: "bg-amber-500/15 text-amber-600 border-transparent",
  fraud_flag: "bg-destructive/15 text-destructive border-transparent",
  manager_approved: "bg-emerald-500/15 text-emerald-600 border-transparent",
  rejected: "bg-destructive/15 text-destructive border-transparent",
};

/** Dot color for the leading indicator on an expense item row. */
export const EXPENSE_ITEM_STATUS_DOT_CLASSES: Record<ExpenseItemStatus, string> = {
  submitted: "bg-blue-500",
  auto_approved: "bg-emerald-500",
  policy_hold: "bg-amber-500",
  fraud_flag: "bg-destructive",
  manager_approved: "bg-emerald-500",
  rejected: "bg-destructive",
};

/** Items in these states still need a human decision. */
export function itemNeedsReview(status: ExpenseItemStatus): boolean {
  return status === "policy_hold" || status === "fraud_flag";
}

/**
 * Explains, in the employee's words, why the claim landed on its current
 * status — rendered above the expense item breakdown when a row is expanded.
 */
export function getClaimStatusExplanation(
  status: ClaimStatus,
  items: { status: ExpenseItemStatus }[]
): string {
  const holds = items.filter((item) => item.status === "policy_hold").length;
  const frauds = items.filter((item) => item.status === "fraud_flag").length;

  switch (status) {
   case "submitted":
      return "Every item has been raised and nothing needs review yet.";
    case "auto_approved":
      return "Every item cleared automatically, so no manual review was needed.";
    case "manager_review":
      return `${holds} ${holds === 1 ? "item is" : "items are"} on policy hold and none are flagged for fraud, so the claim sits with the manager.`;
    case "fraud_review":
      return `${frauds} ${frauds === 1 ? "item is" : "items are"} flagged for fraud, which takes priority over any policy hold.`;
    case "finance_review":
      return "Your manager cleared this claim — finance is verifying it before payout.";
    case "approved":
      return "Every item has been resolved and the claim is approved.";
    case "rejected":
      return "This claim was rejected. Check the item breakdown for the reason.";
    case "disbursed":
      return "This claim was approved and the payment has been paid out.";
  }
}
