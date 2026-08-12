import type { Metadata } from "next";

import Categories from "@/components/categories";

export const metadata: Metadata = {
  title: "Invoice Categories - ExpenseFlow AI",
  description: "Manage expense categories and their invoice extraction fields.",
};

export default function CategoriesPage() {
  return <Categories />;
}
