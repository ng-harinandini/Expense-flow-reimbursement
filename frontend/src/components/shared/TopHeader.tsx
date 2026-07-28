"use client";

import * as React from "react";
import { ChevronDown, LogOut, ReceiptText } from "lucide-react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface TopHeaderProps {
  userName: string;
  userEmail: string;
  userRole: string;
  avatarUrl?: string;
}

export function TopHeader({
  userName,
  userEmail,
  userRole,
  avatarUrl,
}: TopHeaderProps) {
  const initial = userName.charAt(0).toUpperCase();

  return (
    <header className="shrink-0 border-b border-border bg-card">
      <div className="flex w-full items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-orange-500">
            <ReceiptText className="size-5 text-primary-foreground" />
          </div>
          <div className="min-w-0">
            <span className="block truncate text-xl font-extrabold tracking-tight">
              <span className="text-primary">Expense</span>
              <span className="text-secondary">Flow</span>
            </span>
            <p className="hidden truncate text-xs font-medium text-muted-foreground sm:block">
              Expense Reimbursement Automation
            </p>
          </div>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex shrink-0 items-center gap-1.5 rounded-full transition-opacity hover:opacity-80">
              <Avatar className="size-10">
                {avatarUrl && <AvatarImage src={avatarUrl} alt={userName} />}
                <AvatarFallback className="bg-primary text-xl text-primary-foreground font-semibold">
                  {initial}
                </AvatarFallback>
              </Avatar>
              <ChevronDown className="size-4 text-muted-foreground" />
            </button>
          </DropdownMenuTrigger>

          <DropdownMenuContent align="end" className="w-56">
            <div className="px-2 py-1.5">
              <p className="text-sm font-semibold text-foreground">{userName}</p>
              <p className="truncate text-xs text-muted-foreground">{userEmail}</p>
              <p className="mt-1 text-xs text-muted-foreground">{userRole}</p>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive">
              <LogOut />
              Logout
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
