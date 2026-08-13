"use client";

import Image from "next/image";
import { Paperclip, Pencil, Trash2 } from "lucide-react";

import { cn } from "@/lib/utils";

import {
  CATEGORY_ICONS,
  CATEGORY_PILL_COLORS,
  formatDdMmYyyy,
  formatUsd,
  type ExpenseItemDraft,
} from "./helpers";

const COLUMN_COUNT = 8;

interface ExpenseItemsTableProps {
  items: ExpenseItemDraft[];
  onEdit: (item: ExpenseItemDraft) => void;
  onDelete: (item: ExpenseItemDraft) => void;
}

export function ExpenseItemsTable({ items, onEdit, onDelete }: ExpenseItemsTableProps) {
  const hasItems = items.length > 0;

  return (
    <div className={cn(hasItems && "overflow-x-auto")}>
      <table
        className={cn(
          "w-full border-separate [border-spacing:0_0.5rem] text-left text-sm",
          // Only reserve the wide layout once there are rows to lay out —
          // otherwise the empty state alone would force a horizontal scrollbar.
          hasItems && "min-w-[860px]"
        )}
      >
        <thead>
          {/* border-spacing collapses a <tr> border, so the rule lives on the cells */}
          <tr className="bg-card text-xs font-semibold tracking-wide text-muted-foreground uppercase [&>th]:border-b [&>th]:border-border">
            <th className="px-3 py-2 font-semibold">#</th>
            <th className="px-3 py-2 font-semibold">Expense Category</th>
            <th className="px-3 py-2 font-semibold">Vendor / Merchant</th>
            <th className="px-3 py-2 font-semibold">Date</th>
            <th className="px-3 py-2 font-semibold">Description</th>
            <th className="px-3 py-2 font-semibold">Receipt</th>
            <th className="px-3 py-2 text-right font-semibold">Amount ($)</th>
            <th className="px-3 py-2 font-semibold">Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={COLUMN_COUNT} className="rounded-lg px-3 py-5 text-center">
                <Image
                  src="/assets/no_expense_items.png"
                  alt=""
                  aria-hidden
                  // Intrinsic size of the cropped PNG (content fills ~70%).
                  width={200}
                  height={135}
                  className="mx-auto h-32 w-auto object-contain"
                />
                <p className="mt-2 text-sm font-medium text-foreground">
                  No expense items yet
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Click &quot;Add Expense Item&quot; to scan a receipt and add your first item.
                </p>
              </td>
            </tr>
          ) : (
            items.map((item, index) => {
              const Icon = CATEGORY_ICONS[item.category] ?? Paperclip;
              const pillColor = CATEGORY_PILL_COLORS[item.category] ?? "bg-muted text-muted-foreground";

              return (
                <tr
                  key={item.id}
                  className="bg-muted/40 align-middle [&>td:first-child]:rounded-l-lg [&>td:last-child]:rounded-r-lg"
                >
                  <td className="px-3 py-3 text-muted-foreground">{index + 1}</td>
                  <td className="px-3 py-3">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
                        pillColor
                      )}
                    >
                      <Icon className="size-3.5 shrink-0" />
                      {item.category}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-semibold text-foreground">
                      {item.merchantVendor}
                    </span>
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap text-muted-foreground">
                    {formatDdMmYyyy(item.invoiceDate)}
                  </td>
                  <td className="px-3 py-3">
                    <span className="block max-w-56 truncate text-muted-foreground">
                      {item.description}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="inline-flex items-center gap-1.5 text-sm font-medium text-primary">
                      <Paperclip className="size-3.5 shrink-0" />
                      Attached
                    </span>
                  </td>
                  <td className="px-3 py-3 text-right font-semibold text-foreground">
                    {formatUsd(item.amount)}
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        aria-label={`Edit item ${index + 1}`}
                        title="Edit item"
                        className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-secondary"
                        onClick={() => onEdit(item)}
                      >
                        <Pencil className="size-4" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete item ${index + 1}`}
                        title="Delete item"
                        className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive"
                        onClick={() => onDelete(item)}
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
