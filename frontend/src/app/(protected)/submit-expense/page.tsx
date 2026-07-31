import type { Metadata } from "next";

import SubmitExpense from "@/components/submit_expense";

export const metadata: Metadata = {
  title: "Submit Expense - ExpenseFlow AI",
  description:
    "Upload a receipt to auto-fill your claim details, then review and submit your expense for reimbursement.",
};

export default function SubmitExpensePage() {
  return <SubmitExpense />;
}
