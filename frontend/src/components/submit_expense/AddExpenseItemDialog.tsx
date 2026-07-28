"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";
import {
  Calendar,
  CreditCard,
  DollarSign,
  FileText,
  Percent,
  Sparkles,
  Store,
  Tag,
} from "lucide-react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/Label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/Button";
import { ReceiptDropzone } from "@/components/submit_expense/ReceiptDropzone";
import {
  expenseItemSchema,
  EXPENSE_ITEM_FORM_DEFAULTS,
  type ExpenseItemFormValues,
} from "@/components/submit_expense/expenseItemSchema";
import {
  EXPENSE_CATEGORIES,
  generateItemId,
  readFileAsDataUrl,
  type ExpenseItemDraft,
} from "@/components/submit_expense/helpers";

const SELECT_CLASSES =
  "flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent py-1 pr-3 pl-9 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30";

interface AddExpenseItemDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` puts the dialog in "add expense item" mode. */
  editingItem: ExpenseItemDraft | null;
  onConfirm: (item: ExpenseItemDraft) => void;
}

export function AddExpenseItemDialog({
  open,
  onOpenChange,
  editingItem,
  onConfirm,
}: AddExpenseItemDialogProps) {
  const isEditing = Boolean(editingItem);

  const [receipt, setReceipt] = React.useState<File | null>(null);
  const [isExtracting, setIsExtracting] = React.useState(false);
  const [isExtracted, setIsExtracted] = React.useState(false);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<ExpenseItemFormValues>({
    resolver: yupResolver(expenseItemSchema),
    defaultValues: EXPENSE_ITEM_FORM_DEFAULTS,
  });

  // Reload the form each time the dialog opens so add and edit never leak state.
  React.useEffect(() => {
    if (!open) return;

    if (editingItem) {
      const { id, receiptFile, receiptFileName, receiptPreviewUrl, isPdf, ...values } =
        editingItem;
      reset(values);
      setReceipt(receiptFile);
      setIsExtracting(false);
      setIsExtracted(true);
    } else {
      reset(EXPENSE_ITEM_FORM_DEFAULTS);
      setReceipt(null);
      setIsExtracting(false);
      setIsExtracted(false);
    }
  }, [open, editingItem, reset]);

  const fieldsEnabled = Boolean(receipt) && !isExtracting;
  const isPdf = receipt?.type === "application/pdf";

  const handleFileAccepted = (file: File) => {
    setReceipt(file);
    setIsExtracted(false);
    setIsExtracting(true);
    // TODO: replace with the real receipt-extraction API call.
    // While that request is pending, ReceiptDropzone shows the scan-sweep
    // overlay on top of the uploaded image preview.
    setTimeout(() => {
      setIsExtracting(false);
      setIsExtracted(true);
    }, 2200);
  };

  const handleFileRemoved = () => {
    setReceipt(null);
    setIsExtracting(false);
    setIsExtracted(false);
    reset(EXPENSE_ITEM_FORM_DEFAULTS);
  };

  const onSubmit = async (values: ExpenseItemFormValues) => {
    if (!receipt) return;

    const receiptPreviewUrl = isPdf ? null : await readFileAsDataUrl(receipt);

    onConfirm({
      ...values,
      id: editingItem?.id ?? generateItemId(),
      receiptFile: receipt,
      receiptFileName: receipt.name,
      receiptPreviewUrl,
      isPdf: Boolean(isPdf),
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-5xl">
        <DialogHeader className="pr-8">
          <DialogTitle>{isEditing ? "Edit expense item" : "Add expense item"}</DialogTitle>
          <DialogDescription>
            Upload a receipt to auto-fill the details, review them, then confirm to add this
            item to the claim.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[35fr_65fr]">
            <div className="flex flex-col gap-3">
              {receipt && isExtracted && (
                <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-primary">
                  <Sparkles className="size-3.5 shrink-0" />
                  Receipt scanned — review the auto-filled details before confirming.
                </div>
              )}
              <ReceiptDropzone
                file={receipt}
                onFileAccepted={handleFileAccepted}
                onFileRemoved={handleFileRemoved}
                isScanning={isExtracting}
                className="flex-1"
              />
            </div>

            <div className="space-y-5">
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="merchantVendor">Merchant / vendor</Label>
                  <div className="relative">
                    <Store className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="merchantVendor"
                      disabled={!fieldsEnabled}
                      placeholder="e.g. Sweetgreen #104"
                      aria-invalid={!!errors.merchantVendor}
                      className="pl-9"
                      {...register("merchantVendor")}
                    />
                  </div>
                  {errors.merchantVendor && (
                    <p className="text-xs text-destructive">
                      {errors.merchantVendor.message}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="expenseDate">Expense date</Label>
                  <div className="relative">
                    <Calendar className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="expenseDate"
                      type="date"
                      disabled={!fieldsEnabled}
                      aria-invalid={!!errors.expenseDate}
                      className="pl-9"
                      {...register("expenseDate")}
                    />
                  </div>
                  {errors.expenseDate && (
                    <p className="text-xs text-destructive">
                      {errors.expenseDate.message}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="category">Category</Label>
                  <div className="relative">
                    <Tag className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <select
                      id="category"
                      disabled={!fieldsEnabled}
                      aria-invalid={!!errors.category}
                      className={SELECT_CLASSES}
                      {...register("category")}
                    >
                      <option value="">Select a category</option>
                      {EXPENSE_CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </div>
                  {errors.category && (
                    <p className="text-xs text-destructive">{errors.category.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="amount">Amount</Label>
                  <div className="relative">
                    <DollarSign className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="amount"
                      type="number"
                      step="0.01"
                      disabled={!fieldsEnabled}
                      placeholder="0.00"
                      aria-invalid={!!errors.amount}
                      className="pl-9"
                      {...register("amount")}
                    />
                  </div>
                  {errors.amount && (
                    <p className="text-xs text-destructive">{errors.amount.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="taxAmount">Tax / GST</Label>
                  <div className="relative">
                    <Percent className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="taxAmount"
                      type="number"
                      step="0.01"
                      disabled={!fieldsEnabled}
                      placeholder="0.00"
                      aria-invalid={!!errors.taxAmount}
                      className="pl-9"
                      {...register("taxAmount")}
                    />
                  </div>
                  {errors.taxAmount && (
                    <p className="text-xs text-destructive">{errors.taxAmount.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="paymentMethod">Payment method</Label>
                  <div className="relative">
                    <CreditCard className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="paymentMethod"
                      disabled={!fieldsEnabled}
                      placeholder="e.g. Corporate card"
                      aria-invalid={!!errors.paymentMethod}
                      className="pl-9"
                      {...register("paymentMethod")}
                    />
                  </div>
                  {errors.paymentMethod && (
                    <p className="text-xs text-destructive">
                      {errors.paymentMethod.message}
                    </p>
                  )}
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="description">Business purpose &amp; description</Label>
                <Textarea
                  id="description"
                  rows={3}
                  disabled={!fieldsEnabled}
                  placeholder="Why was this expense necessary?"
                  aria-invalid={!!errors.description}
                  {...register("description")}
                />
                {errors.description && (
                  <p className="text-xs text-destructive">{errors.description.message}</p>
                )}
              </div>
            </div>
          </div>

          <Separator className="my-6" />

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={!fieldsEnabled || isSubmitting}>
              {isEditing ? "Save changes" : "Confirm"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
