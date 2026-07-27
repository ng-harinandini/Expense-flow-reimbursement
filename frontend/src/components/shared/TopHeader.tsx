"use client";

import * as React from "react";
import { LogOut, Wallet } from "lucide-react";

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
    <header className="border-b bg-background">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex min-w-0 items-center gap-2">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-gradient-to-r from-primary to-secondary">
            <Wallet className="size-4.5 text-primary-foreground" />
          </div>
          <div className="min-w-0">
            <span className="block truncate bg-gradient-to-r from-primary to-secondary bg-clip-text text-lg font-extrabold tracking-tight text-transparent sm:text-xl">
              ExpenseFlow
            </span>
            <p className="hidden truncate text-[11px] font-medium uppercase tracking-wider text-muted-foreground sm:block">
              Expense Reimbursement Automation
            </p>
          </div>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="shrink-0 rounded-full transition-opacity hover:opacity-80">
              <Avatar className="size-10">
                {avatarUrl && <AvatarImage src={avatarUrl} alt={userName} />}
                <AvatarFallback className="bg-primary text-base text-primary-foreground font-semibold">
                  {initial}
                </AvatarFallback>
              </Avatar>
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
