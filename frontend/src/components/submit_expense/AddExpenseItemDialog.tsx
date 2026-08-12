"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";
import {
  AlertTriangle,
  Calendar,
  DollarSign,
  Hash,
  Percent,
  Plane,
  Route,
  Sparkles,
  Store,
  Tag,
  Users,
} from "lucide-react";

import { useCategoriesQuery } from "@/api/categories";
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
import { Button } from "@/components/ui/Button";
import { ReceiptDropzone } from "@/components/submit_expense/ReceiptDropzone";
import {
  expenseItemSchema,
  EXPENSE_ITEM_FORM_DEFAULTS,
  type ExpenseItemFormValues,
} from "@/components/submit_expense/expenseItemSchema";
import {
  CURRENCY_CODES,
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
    watch,
    formState: { errors, isSubmitting },
  } = useForm<ExpenseItemFormValues>({
    resolver: yupResolver(expenseItemSchema),
    defaultValues: EXPENSE_ITEM_FORM_DEFAULTS,
  });

  // The server answers with a category *name* from the database, validated against exactly this
  // set. Rendering the same source is what keeps a suggestion selectable — with the hardcoded list
  // a category an admin added would arrive with no matching <option>, and the select would go
  // blank. EXPENSE_CATEGORIES stays as the fallback for when the query hasn't landed or failed.
  const { data: liveCategories } = useCategoriesQuery();
  const categoryOptions = React.useMemo(() => {
    const live = (liveCategories ?? [])
      // The COMMON row is a bucket of shared extraction fields, not a category anyone can pick.
      .filter((category) => !category.isCommon && category.isActive)
      .sort((a, b) => a.displayOrder - b.displayOrder)
      .map((category) => category.name);
    return live.length ? live : EXPENSE_CATEGORIES;
  }, [liveCategories]);

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

  // Comparing against the live value rather than tracking "was prefilled" means the badge
  // disappears the moment the user picks something else, and reappears on an edit-mode reopen if
  // they kept the suggestion — the reopen path restores both the saved category and `extraction`.
  const selectedCategory = watch("category");
  const categoryWasSuggested =
    Boolean(extraction?.suggestedCategory) &&
    selectedCategory === extraction?.suggestedCategory;

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

  /** `yyyy-MM-dd` prefix of an ISO date string, or `""` if `value` isn't one. */
  const asIsoDate = (value: string | undefined) => value?.slice(0, 10) ?? "";


  const matchOption = (options: readonly string[], value: string | undefined) =>
    options.find((option) => option.toLowerCase() === value?.toLowerCase());

  /** Writes the server's suggestions into the form as editable defaults, in one `reset()` call. */
  const applyPrefill = (result: ReceiptExtraction) => {
    const fields = result.categoryFields ?? {};

    const rawFields =
      (result.extraction?.fields as Array<{ fieldType: string; fieldValue: string }> | undefined) ?? [];
    const taxText = rawFields.find((f) => f.fieldType === "TAX")?.fieldValue;
    const taxValue = taxText ? parseFloat(taxText.replace(/[^\d.]/g, "")) : NaN;

    const category = matchOption(categoryOptions, fields.expense_category ?? result.suggestedCategory ?? undefined);
    const currency = matchOption(
      CURRENCY_CODES,
      (fields.currency ?? result.suggestedCurrency ?? undefined)?.toUpperCase()
    );
    const fromDate = asIsoDate(fields.invoice_from_date) || asIsoDate(result.suggestedDate ?? undefined);
    const toDate = asIsoDate(fields.invoice_to_date) || fromDate;

    reset(
      (current) => ({
        ...current,
        ...(category && { category }),
        ...(currency && { currency }),
        amount: fields.total_amount ?? result.suggestedAmount ?? current.amount,
        ...(!Number.isNaN(taxValue) && { taxAmount: taxValue }),
        ...(fromDate && { expenseFromDate: fromDate }),
        ...(toDate && { expenseToDate: toDate }),
        merchantVendor: fields.vendor_name ?? result.suggestedVendor ?? current.merchantVendor,
        invoiceNumber: fields.invoice_number ?? current.invoiceNumber,
        travelRoute: fields.travel_route ?? current.travelRoute,
        travelType: fields.travel_type ?? current.travelType,
        numberOfAttendees: fields.number_of_attendees ?? current.numberOfAttendees,
      }),
      { keepDefaultValues: true }
    );
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
      <DialogContent className="flex max-h-[calc(100vh-4rem)] flex-col sm:max-w-5xl">
        <DialogHeader className="pr-8">
          <DialogTitle>{isEditing ? "Edit expense item" : "Add expense item"}</DialogTitle>
          <DialogDescription>
            Upload a receipt to auto-fill the details, review them, then confirm to add this
            item to the claim.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="flex min-h-0 flex-1 flex-col">
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-6 lg:grid-cols-[35fr_65fr] lg:grid-rows-1">
            <div className="flex flex-col gap-3 self-start">
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
              />
            </div>

            <div className="scrollbar-subtle min-h-0 space-y-5 overflow-y-auto px-1">
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="category">
                    Expense Category <span className="text-destructive">*</span>
                  </Label>
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
                      {categoryOptions.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </div>
                  {errors.category ? (
                    <p className="text-xs text-destructive">{errors.category.message}</p>
                  ) : (
                    categoryWasSuggested && (
                      <p className="flex items-center gap-1.5 text-xs text-primary">
                        <Sparkles className="size-3 shrink-0" />
                        Read from the receipt
                        {extraction?.suggestedCategoryConfidence != null &&
                          ` (${Math.round(extraction.suggestedCategoryConfidence * 100)}% confident)`}
                        {" — change it if it's wrong."}
                      </p>
                    )
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="amount">
                    Total Amount <span className="text-destructive">*</span>
                  </Label>
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
                  <Label htmlFor="currency">
                    Currency <span className="text-destructive">*</span>
                  </Label>
                  <div className="relative">
                    <Hash className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <select
                      id="currency"
                      disabled={!fieldsEnabled}
                      aria-invalid={!!errors.currency}
                      className={SELECT_CLASSES}
                      {...register("currency")}
                    >
                      <option value="">Select a currency</option>
                      {CURRENCY_CODES.map((code) => (
                        <option key={code} value={code}>
                          {code}
                        </option>
                      ))}
                    </select>
                  </div>
                  {errors.currency && (
                    <p className="text-xs text-destructive">{errors.currency.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="taxAmount">Tax / GST (optional)</Label>
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
                  <Label htmlFor="merchantVendor">
                    Vendor Name <span className="text-destructive">*</span>
                  </Label>
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
                  <Label htmlFor="expenseFromDate">
                    Invoice From Date <span className="text-destructive">*</span>
                  </Label>
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
                  <Label htmlFor="expenseToDate">
                    Invoice To Date <span className="text-destructive">*</span>
                  </Label>
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
                  <Label htmlFor="invoiceNumber">
                    Invoice Number <span className="text-destructive">*</span>
                  </Label>
                  <div className="relative">
                    <Hash className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="invoiceNumber"
                      disabled={!fieldsEnabled}
                      placeholder="e.g. INV-10293"
                      aria-invalid={!!errors.invoiceNumber}
                      className="pl-9"
                      {...register("invoiceNumber")}
                    />
                  </div>
                  {errors.invoiceNumber && (
                    <p className="text-xs text-destructive">{errors.invoiceNumber.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="travelRoute">
                    Travel Route <span className="text-destructive">*</span>
                  </Label>
                  <div className="relative">
                    <Route className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="travelRoute"
                      disabled={!fieldsEnabled}
                      placeholder="e.g. Bengaluru - Mumbai"
                      aria-invalid={!!errors.travelRoute}
                      className="pl-9"
                      {...register("travelRoute")}
                    />
                  </div>
                  {errors.travelRoute && (
                    <p className="text-xs text-destructive">{errors.travelRoute.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="travelType">
                    Travel Type <span className="text-destructive">*</span>
                  </Label>
                  <div className="relative">
                    <Plane className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="travelType"
                      disabled={!fieldsEnabled}
                      placeholder="e.g. Flight, Train, Cab"
                      aria-invalid={!!errors.travelType}
                      className="pl-9"
                      {...register("travelType")}
                    />
                  </div>
                  {errors.travelType && (
                    <p className="text-xs text-destructive">{errors.travelType.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="numberOfAttendees">No. of Attendees (optional)</Label>
                  <div className="relative">
                    <Users className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="numberOfAttendees"
                      type="number"
                      step="1"
                      disabled={!fieldsEnabled}
                      placeholder="0"
                      aria-invalid={!!errors.numberOfAttendees}
                      className="pl-9"
                      {...register("numberOfAttendees")}
                    />
                  </div>
                  {errors.numberOfAttendees && (
                    <p className="text-xs text-destructive">
                      {errors.numberOfAttendees.message}
                    </p>
                  )}
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="description">
                  Business purpose &amp; description <span className="text-destructive">*</span>
                </Label>
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

          <DialogFooter className="mt-6 shrink-0">
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
