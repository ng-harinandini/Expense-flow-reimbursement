"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";
import {
  AlertTriangle,
  Calendar,
  CreditCard,
  DollarSign,
  Percent,
  Sparkles,
  Store,
  Tag,
} from "lucide-react";

import { uploadReceipt, type ReceiptExtraction } from "@/api/expenseItems";
import { getErrorMessage } from "@/lib/apiError";

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
  const [extraction, setExtraction] = React.useState<ReceiptExtraction | null>(null);
  const [extractionError, setExtractionError] = React.useState<string | null>(null);

  // Aborts the in-flight upload when the receipt is swapped or removed, so a slow
  // earlier response can't land after a newer one and overwrite the form.
  const uploadRef = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => uploadRef.current?.abort(), []);

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<ExpenseItemFormValues>({
    resolver: yupResolver(expenseItemSchema),
    defaultValues: EXPENSE_ITEM_FORM_DEFAULTS,
  });

  // Reload the form each time the dialog opens so add and edit never leak state.
  React.useEffect(() => {
    if (!open) return;

    if (editingItem) {
      const {
        id,
        receiptFile,
        receiptFileName,
        receiptPreviewUrl,
        isPdf,
        extraction: savedExtraction,
        ...values
      } = editingItem;
      reset(values);
      setReceipt(receiptFile);
      setIsExtracting(false);
      setIsExtracted(true);
      // Carried over so re-confirming an edited item keeps the provenance the
      // submit call needs; the receipt is not re-uploaded just to edit a field.
      setExtraction(savedExtraction);
      setExtractionError(null);
    } else {
      reset(EXPENSE_ITEM_FORM_DEFAULTS);
      setReceipt(null);
      setIsExtracting(false);
      setIsExtracted(false);
      setExtraction(null);
      setExtractionError(null);
    }
  }, [open, editingItem, reset]);

  const fieldsEnabled = Boolean(receipt) && !isExtracting;
  const isPdf = receipt?.type === "application/pdf";

  const handleFileAccepted = async (file: File) => {
    uploadRef.current?.abort();
    const controller = new AbortController();
    uploadRef.current = controller;

    setReceipt(file);
    setIsExtracted(false);
    setExtraction(null);
    setExtractionError(null);
    setIsExtracting(true);

    try {
      // While this is pending, ReceiptDropzone shows the scan-sweep overlay on
      // top of the uploaded image preview.
      const result = await uploadReceipt(file, { signal: controller.signal });
      if (controller.signal.aborted) return;

      setExtraction(result);
      applyPrefill(result);
      // A failed extraction still returns 200 with the stored file — the user
      // fills the fields in by hand rather than losing the upload.
      setExtractionError(result.errorMessage ?? null);
      setIsExtracted(true);
    } catch (error) {
      if (controller.signal.aborted) return;
      setExtractionError(
        getErrorMessage(error, "Could not scan the receipt. Enter the details manually.")
      );
      // Fields are unlocked anyway so a scan failure never blocks the claim.
      setIsExtracted(true);
    } finally {
      if (!controller.signal.aborted) setIsExtracting(false);
    }
  };

  /** Writes the server's suggestions into the form as editable defaults. */
  const applyPrefill = (result: ReceiptExtraction) => {
    if (result.suggestedVendor) {
      setValue("merchantVendor", result.suggestedVendor, { shouldValidate: true });
    }
    if (result.suggestedDate) {
      // The date input needs yyyy-MM-dd; anything else is left for the user.
      const date = result.suggestedDate.slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        setValue("expenseFromDate", date, { shouldValidate: true });
        setValue("expenseToDate", date, { shouldValidate: true });
      }
    }
    if (typeof result.suggestedAmount === "number") {
      setValue("amount", result.suggestedAmount, { shouldValidate: true });
    }
    // Parse tax from extraction fields (e.g. { fieldType: "TAX", fieldValue: "$24.96" })
    const fields = (result.extraction?.fields as Array<{ fieldType: string; fieldValue: string }> | undefined) ?? [];
    const taxField = fields.find((f) => f.fieldType === "TAX");
    if (taxField) {
      const taxValue = parseFloat(taxField.fieldValue.replace(/[^\d.]/g, ""));
      if (!Number.isNaN(taxValue)) {
        setValue("taxAmount", taxValue, { shouldValidate: true });
      }
    }
    // No category prefill: the endpoint only echoes back a hint we don't send,
    // so the user always picks it. See ReceiptExtraction.suggestedCategory.
  };

  const handleFileRemoved = () => {
    uploadRef.current?.abort();
    setReceipt(null);
    setIsExtracting(false);
    setIsExtracted(false);
    setExtraction(null);
    setExtractionError(null);
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
      extraction,
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
              {receipt && isExtracted && !extractionError && (
                <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-primary">
                  <Sparkles className="size-3.5 shrink-0" />
                  Receipt scanned — review the auto-filled details before confirming.
                </div>
              )}

              {extractionError && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="mt-px size-3.5 shrink-0" />
                  <span>{extractionError}</span>
                </div>
              )}

              {/* Advisory only — a repeat receipt is legitimate after a rejection,
                  so this never blocks confirming the item. */}
              {extraction?.duplicateOfClaimNumber && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="mt-px size-3.5 shrink-0" />
                  <span>
                    This receipt was already uploaded on claim{" "}
                    {extraction.duplicateOfClaimNumber}.
                  </span>
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
                  <Label htmlFor="expenseFromDate">Expense from date</Label>
                  <div className="relative">
                    <Calendar className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="expenseFromDate"
                      type="date"
                      disabled={!fieldsEnabled}
                      aria-invalid={!!errors.expenseFromDate}
                      className="pl-9"
                      {...register("expenseFromDate")}
                    />
                  </div>
                  {errors.expenseFromDate && (
                    <p className="text-xs text-destructive">
                      {errors.expenseFromDate.message}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="expenseToDate">Expense to date</Label>
                  <div className="relative">
                    <Calendar className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="expenseToDate"
                      type="date"
                      disabled={!fieldsEnabled}
                      aria-invalid={!!errors.expenseToDate}
                      className="pl-9"
                      {...register("expenseToDate")}
                    />
                  </div>
                  {errors.expenseToDate && (
                    <p className="text-xs text-destructive">
                      {errors.expenseToDate.message}
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
