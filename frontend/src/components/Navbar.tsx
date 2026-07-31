"use client";

import * as React from "react";
import {
  PlusCircle,
  FileText,
  ShieldCheck,
  ClipboardCheck,
  BarChart2,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";

interface NavItem {
  label: string;
  icon: LucideIcon;
  href: string;
}

const NAV_ITEMS: NavItem[] = [
  { label: "My expense claims", icon: FileText, href: "/claims" },
  { label: "Policy guidelines", icon: ShieldCheck, href: "/policy" },
  { label: "Approvals", icon: ClipboardCheck, href: "/approvals" },
  { label: "Reports", icon: BarChart2, href: "/reports" },
];

export function Navbar({ className }: { className?: string }) {
  const [active, setActive] = React.useState<string>(NAV_ITEMS[0].href);

  return (
    <nav
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-2xl border bg-card p-2 shadow-sm sm:gap-3 sm:rounded-full",
        className
      )}
    >
      <Button size="sm" className="rounded-full sm:h-9 sm:px-4 sm:text-sm">
        <PlusCircle />
        Submit expense
      </Button>

      {NAV_ITEMS.map(({ label, icon: Icon, href }) => (
        <Button
          key={href}
          size="sm"
          variant="outline"
          className={cn(
            "rounded-full sm:h-9 sm:px-4 sm:text-sm",
            active === href && "bg-accent text-accent-foreground"
          )}
          onClick={() => setActive(href)}
        >
          <Icon />
          {label}
        </Button>
      ))}
    </nav>
  );
}
