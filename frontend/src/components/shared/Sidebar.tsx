"use client";

import * as React from "react";
import Image from "next/image";
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
const role: UserRole = "manager";

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

// Fades the labels and the bottom illustration in as the rail widens. Kept on a
// shared constant so everything inside the sidebar animates on the same beat.
const REVEAL_ON_EXPAND =
  "opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100";

export function Sidebar({ className }: { className?: string }) {
  const pathname = usePathname();
  const visibleNavItems = NAV_ITEMS.filter((item) => item.roles.includes(role));

  return (
    <aside
      className={cn(
        // Collapsed to an icon rail by default; widens on hover (or when a link
        // inside takes focus, so keyboard users get the labels too). Touch
        // devices have no hover, so the rail simply stays collapsed there.
        "group flex w-16 shrink-0 flex-col overflow-hidden border-r border-border bg-card transition-[width] duration-200 ease-out hover:w-64 focus-within:w-64",
        className
      )}
    >
      <nav className="flex-1 overflow-y-auto p-3">
        <ul className="space-y-2">
          {visibleNavItems.map(({ label, icon: Icon, href }) => {
            const isActive = pathname === href;
            return (
              <li key={href}>
                <Link
                  href={href}
                  title={label}
                  aria-label={label}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "relative flex items-center gap-3.5 rounded-lg px-2.5 py-3.5 text-sm whitespace-nowrap transition-colors",
                    isActive
                      ? "bg-primary/10 font-semibold text-primary"
                      : "font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
                  )}
                >
                  {isActive && (
                    <span className="absolute top-1 bottom-1 left-0 w-1 rounded-r-full bg-primary" />
                  )}
                  <Icon className="size-5 shrink-0" />
                  <span className={cn("truncate", REVEAL_ON_EXPAND)}>{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Decorative footer: two layered lavender waves running to the bottom
          edge, with the artwork sitting on top of them. */}
      <div className={cn("relative shrink-0 pb-8", REVEAL_ON_EXPAND)}>
        <svg
          aria-hidden
          viewBox="0 0 256 96"
          preserveAspectRatio="none"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-24 w-64 max-w-none"
        >
          <path
            d="M0 44C42 18 78 58 122 38s70-24 134-14v72H0V44Z"
            className="fill-secondary/10"
          />
          <path
            d="M0 64C48 40 92 76 140 56s78-18 116-8v48H0V64Z"
            className="fill-secondary/20"
          />
        </svg>
        <Image
          src="/assets/sidebar.png"
          alt=""
          aria-hidden
          // Intrinsic size of the cropped PNG (tight to its visible art).
          width={698}
          height={595}
          className="relative mx-auto h-44 w-auto object-contain"
        />
      </div>
    </aside>
  );
}
