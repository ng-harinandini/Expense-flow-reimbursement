import type { Metadata } from "next";

import MyClaims from "@/components/my_claims";

export const metadata: Metadata = {
  title: "My expense claims - ExpenseFlow AI",
  description:
    "View and manage your submitted expense claims, track their status, and access detailed information for each claim.",
};

export default function MyClaimsPage() {
  return <MyClaims />;
}
