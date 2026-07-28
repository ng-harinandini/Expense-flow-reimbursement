"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  PlusCircle,
  FileText,
  ShieldCheck,
  ClipboardCheck,
  BarChart2,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

interface NavItem {
  label: string;
  icon: LucideIcon;
  href: string;
}

const NAV_ITEMS: NavItem[] = [
  { label: "Submit expense", icon: PlusCircle, href: "/submit-expense" },
  { label: "My expense claims", icon: FileText, href: "/claims" },
  { label: "Policy guidelines", icon: ShieldCheck, href: "/policy" },
  { label: "Approvals", icon: ClipboardCheck, href: "/approvals" },
  { label: "Reports", icon: BarChart2, href: "/reports" },
];

export function SubHeader({ className }: { className?: string }) {
  const pathname = usePathname();

  return (
    <div className={cn("border-b bg-background", className)}>
      <nav className="mx-auto flex max-w-7xl items-center gap-4 overflow-x-auto px-4 sm:gap-6 sm:px-6 lg:px-8 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {NAV_ITEMS.map(({ label, icon: Icon, href }) => {
          const isActive = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "relative flex shrink-0 items-center gap-1.5 py-3 text-sm whitespace-nowrap transition-colors",
                isActive
                  ? "font-semibold text-secondary"
                  : "font-medium text-muted-foreground hover:text-foreground"
              )}
            >
              <Icon className="size-4 shrink-0" />
              {label}
              {isActive && (
                <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-secondary" />
              )}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
