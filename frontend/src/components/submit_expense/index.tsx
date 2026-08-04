"use client";

import * as React from "react";
import Image from "next/image";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";
import { FileText } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { createClaim } from "@/api/claims";
import { getErrorMessage } from "@/lib/apiError";
import { AddExpenseItemDialog } from "@/components/submit_expense/AddExpenseItemDialog";
import { ExpenseItemsSection } from "@/components/submit_expense/ExpenseItemsSection";
import { ClaimDetailsCard } from "@/components/submit_expense/ClaimDetailsCard";
import {
  CLAIM_FORM_DEFAULTS,
  claimSchema,
  type ClaimFormValues,
} from "@/components/submit_expense/claimSchema";
import type { ExpenseItemDraft } from "@/components/submit_expense/helpers";

export default function SubmitExpense() {
  const claimForm = useForm<ClaimFormValues>({
    resolver: yupResolver(claimSchema),
    defaultValues: CLAIM_FORM_DEFAULTS,
    mode: "onChange",
  });

  const { watch, trigger, getValues } = claimForm;
  const [items, setItems] = React.useState<ExpenseItemDraft[]>([]);
  const [isDialogOpen, setIsDialogOpen] = React.useState(false);
  const [editingItem, setEditingItem] = React.useState<ExpenseItemDraft | null>(null);
  const [raiseClaimError, setRaiseClaimError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [submittedClaimNumber, setSubmittedClaimNumber] = React.useState<string | null>(null);

  const handleAddClick = async () => {
    // Claim fields must be valid before an item can be tied to this claim.
    // const valid = await trigger(["claimName", "fromDate", "toDate"]);
    // if (!valid) return;
    // setEditingItem(null);
    setIsDialogOpen(true);
  };

  const handleEditItem = (item: ExpenseItemDraft) => {
    setEditingItem(item);
    setIsDialogOpen(true);
  };

  const handleDeleteItem = (item: ExpenseItemDraft) => {
    setItems((current) => current.filter((i) => i.id !== item.id));
  };

  const handleConfirmItem = (item: ExpenseItemDraft) => {
    setItems((current) => {
      const exists = current.some((i) => i.id === item.id);
      return exists ? current.map((i) => (i.id === item.id ? item : i)) : [...current, item];
    });
    setRaiseClaimError(null);
  };

  const handleRaiseClaim = async () => {
    const claimValid = await trigger();
    if (!claimValid) {
      setRaiseClaimError("Fix the claim details above before raising the claim.");
      return;
    }
    if (items.length === 0) {
      setRaiseClaimError("Add at least one expense item before raising a claim.");
      return;
    }

    setRaiseClaimError(null);
    setIsSubmitting(true);

    try {
      const result = await createClaim(getValues(), items);
      setSubmittedClaimNumber(result.claimNumber);
      claimForm.reset();
      setItems([]);
    } catch (error) {
      setRaiseClaimError(getErrorMessage(error, "Failed to submit the claim. Please try again."));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      {submittedClaimNumber && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-primary">
          <FileText className="mt-0.5 size-4 shrink-0" />
          <span>
            Claim <span className="font-semibold">{submittedClaimNumber}</span> submitted
            successfully.{" "}
            <button
              type="button"
              className="underline underline-offset-2 hover:opacity-70"
              onClick={() => setSubmittedClaimNumber(null)}
            >
              Submit another
            </button>
          </span>
        </div>
      )}
      <Card className="gap-4 py-4">
        {/* CardHeader defaults to a two-row grid (title row / description row)
            for the case where they're separate children. Title and
            description are nested together in one block here, so the header
            is overridden to a single-row flex instead of leaving the grid's
            unused second row in place. Padding-bottom is zeroed (same
            [.border-b]: variant as the base class, so tailwind-merge
            deterministically replaces it) so the taller illustration's
            bottom edge — pinned via self-end below — sits flush against the
            border instead of floating above it. */}
        <CardHeader className="flex flex-row items-start justify-between gap-4 border-b border-border [.border-b]:pb-0">
          <div className="flex items-center gap-4">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
              <FileText className="size-6 text-primary" />
            </div>
            <div className="min-w-0">
              <CardTitle className="text-lg font-bold">Submit expense</CardTitle>
              <CardDescription className="mt-1">
                Upload your receipts, let AI pre-fill the details, review, and submit
                your expense claim.
              </CardDescription>
            </div>
          </div>
          <Image
            src="/assets/submit_expense.png"
            alt=""
            aria-hidden
            // Intrinsic size of the cropped PNG (content fills ~95%).
            width={681}
            height={510}
            className="hidden h-20 w-auto shrink-0 self-end object-contain sm:block"
          />
        </CardHeader>
        <CardContent className="space-y-4">
          <ClaimDetailsCard form={claimForm} />

          <ExpenseItemsSection
            items={items}
            onAddClick={handleAddClick}
            onEdit={handleEditItem}
            onDelete={handleDeleteItem}
            onRaiseClaim={handleRaiseClaim}
            raiseClaimError={raiseClaimError}
            isSubmitting={isSubmitting}
          />
        </CardContent>
      </Card>

      <AddExpenseItemDialog
        open={isDialogOpen}
        onOpenChange={setIsDialogOpen}
        editingItem={editingItem}
        onConfirm={handleConfirmItem}
      />
    </>
  );
}
