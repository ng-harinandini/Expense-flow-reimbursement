import type { Metadata } from "next";

import AuditLogs from "@/components/audit_logs";

export const metadata: Metadata = {
  title: "Audit Logs - ExpenseFlow AI",
  description: "Full audit trail of every claim event across employees, approvers, and the AI engine.",
};

export default function AuditLogsPage() {
  return <AuditLogs />;
}
