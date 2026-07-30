import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "../providers/QueryProvider";
import { ToastProvider } from "@/components/ui/toast";
import { UserProvider } from "@/context/UserContext";

export const metadata: Metadata = {
  title: "ExpenseFlow AI - Next.js Enterprise Expense Platform",
  description:
    "Enterprise expense reimbursement platform built with Next.js App Router, Gemini AI OCR, multi-tier policy engine, fraud screening, and AWS serverless architecture.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-background text-foreground antialiased">
        <QueryProvider>
          <ToastProvider>
            <UserProvider>{children}</UserProvider>
          </ToastProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
