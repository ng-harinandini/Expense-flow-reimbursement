import type { Metadata } from "next";

import Approvals from "@/components/approvals";

export const metadata: Metadata = {
  title: "Approvals - ExpenseFlow AI",
  description: "Review expense claims raised by your team and view their details.",
};

export default function ApprovalsPage() {
  return <Approvals />;
}
