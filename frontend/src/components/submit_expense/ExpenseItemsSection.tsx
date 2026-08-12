"use client";

import { ArrowRight, DollarSign, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/Button";

import { ExpenseItemsTable } from "./ExpenseItemsTable";
import { MAX_EXPENSE_ITEMS, formatUsd, type ExpenseItemDraft } from "./helpers";

interface ExpenseItemsSectionProps {
  items: ExpenseItemDraft[];
  onAddClick: () => void;
  onEdit: (item: ExpenseItemDraft) => void;
  onDelete: (item: ExpenseItemDraft) => void;
  onRaiseClaim: () => void;
  isSubmitting: boolean;
}

export function ExpenseItemsSection({
  items,
  onAddClick,
  onEdit,
  onDelete,
  onRaiseClaim,
  isSubmitting,
}: ExpenseItemsSectionProps) {
  const totalAmount = items.reduce((sum, item) => sum + (item.amount || 0), 0);
  const atMaxItems = items.length >= MAX_EXPENSE_ITEMS;

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border p-3">
        <div className="flex flex-col gap-4 border-b border-border pb-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-secondary/20">
              <DollarSign className="size-4 text-secondary" />
            </div>
            <h3 className="text-base font-semibold text-foreground">
              Expense items
            </h3>
          </div>

          <Button
            type="button"
            variant="outline"
            onClick={onAddClick}
            disabled={atMaxItems}
            className="border-secondary/40 text-secondary hover:bg-secondary/5 hover:text-secondary"
            title={atMaxItems ? "Maximum of 10 items reached" : undefined}
          >
            <Plus />
            Add Expense Item
            <Badge className="border-transparent bg-secondary/15 text-secondary">
              {items.length}/{MAX_EXPENSE_ITEMS}
            </Badge>
          </Button>
        </div>

        <div className="mt-1.5">
          <ExpenseItemsTable
            items={items}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        </div>
      </div>

      <div className="flex flex-col items-end gap-1.5">
        <Button
          type="button"
          size="lg"
          className="w-full sm:w-auto"
          onClick={onRaiseClaim}
          disabled={isSubmitting}
        >
          Raise Claim ({items.length} {items.length === 1 ? "Item" : "Items"} -{" "}
          {formatUsd(totalAmount)})
          <ArrowRight />
        </Button>
      </div>
    </div>
  );
}
