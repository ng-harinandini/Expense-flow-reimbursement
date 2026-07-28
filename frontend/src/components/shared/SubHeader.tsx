"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  PlusCircle,
  FileText,
  ShieldCheck,
  ClipboardCheck,
  Wallet,
  ScrollText,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { UserRole } from "@/types";

interface NavItem {
  label: string;
  icon: LucideIcon;
  href: string;
  roles: UserRole[];
}

// TODO: replace with real auth-derived role once auth is wired up
const role: UserRole = "employee";

const NAV_ITEMS: NavItem[] = [
  {
    label: "Submit expense",
    icon: PlusCircle,
    href: "/submit-expense",
    roles: ["employee", "manager"],
  },
  {
    label: "My expense claims",
    icon: FileText,
    href: "/my-claims",
    roles: ["employee", "manager"],
  },
  {
    label: "Approvals",
    icon: ClipboardCheck,
    href: "/approvals",
    roles: ["manager", "finance"],
  },
  {
    label: "Disbursement",
    icon: Wallet,
    href: "/disbursement",
    roles: ["finance"],
  },
  {
    label: "Policy guidelines",
    icon: ShieldCheck,
    href: "/policy-guidelines",
    roles: ["admin"],
  },
  {
    label: "Organisation",
    icon: ShieldCheck,
    href: "/organisation",
    roles: ["admin"],
  },
  {
    label: "Auditor logs",
    icon: ScrollText,
    href: "/audit-logs",
    roles: ["auditor"],
  },
];

export function SubHeader({ className }: { className?: string }) {
  const pathname = usePathname();
  const visibleNavItems = NAV_ITEMS.filter((item) => item.roles.includes(role));

  return (
    <div className={cn("border-b bg-background", className)}>
      <nav className="mx-auto flex max-w-7xl items-center gap-4 overflow-x-auto px-4 sm:gap-6 sm:px-6 lg:px-8 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {visibleNavItems.map(({ label, icon: Icon, href }) => {
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
