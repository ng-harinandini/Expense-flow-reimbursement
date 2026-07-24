import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'ExpenseFlow AI - Next.js Enterprise Expense Platform',
  description: 'Enterprise expense reimbursement platform built with Next.js App Router, Gemini AI OCR, multi-tier policy engine, fraud screening, and AWS serverless architecture.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#020617] text-slate-100 antialiased selection:bg-orange-500/30 selection:text-orange-200">
        {children}
      </body>
    </html>
  );
}
