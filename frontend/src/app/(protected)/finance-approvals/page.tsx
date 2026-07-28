import type { Metadata } from "next";

import FinanceApprovals from "@/components/finance_approvals";

export const metadata: Metadata = {
  title: "Finance Approvals - ExpenseFlow AI",
  description: "Review manager-approved expense claims awaiting finance sign-off.",
};

export default function FinanceApprovalsPage() {
  return <FinanceApprovals />;
}
