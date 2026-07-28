import type { Metadata } from "next";

import PolicyGuidelines from "@/components/policy_guidelines";

export const metadata: Metadata = {
  title: "Policy Guidelines - ExpenseFlow AI",
  description: "Manage the expense policy rules enforced during claim review.",
};

export default function PolicyGuidelinesPage() {
  return <PolicyGuidelines />;
}
