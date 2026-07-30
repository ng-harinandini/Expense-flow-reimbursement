"use client";

import * as React from "react";

import { Sidebar } from "@/components/shared/Sidebar";
import { TopHeader } from "@/components/shared/TopHeader";
import { useUser } from "@/context/UserContext";

const ROLE_LABELS: Record<string, string> = {
  employee: "Employee",
  manager: "Manager",
  finance: "Finance",
  admin: "Administrator",
  auditor: "Auditor",
};


export function AppShell({ children }: { children: React.ReactNode }) {
  const { user } = useUser();

  const email = user?.email ?? "";
  const role = user?.role ?? null;
  const name = user?.name ?? "";

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <TopHeader
        userName={name}
        userEmail={email}
        userRole={role ? (ROLE_LABELS[role] ?? role) : ""}
      />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar role={role} />
        <main className="flex-1 overflow-y-auto p-4">{children}</main>
      </div>
    </div>
  );
}
