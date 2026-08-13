"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";
import {
  AlertTriangle,
  Calendar,
  CalendarDays,
  DollarSign,
  Hash,
  Plane,
  Route,
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
  TRAVEL_TYPES,
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
      // Carried over so re-confirming an edited item keeps the provenance the
      // submit call needs; the receipt is not re-uploaded just to edit a field.
      setExtraction(savedExtraction);
      setExtractionError(null);
    } else {
      reset(EXPENSE_ITEM_FORM_DEFAULTS);
      setReceipt(null);
      setIsExtracting(false);
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
    } catch (error) {
      if (controller.signal.aborted) return;
      setExtractionError(
        getErrorMessage(error, "Could not scan the receipt. Enter the details manually.")
      );
      // Fields are unlocked anyway so a scan failure never blocks the claim.
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

    const category = matchOption(categoryOptions, fields.expense_category ?? result.suggestedCategory ?? undefined);
    const currency = matchOption(
      CURRENCY_CODES,
      (fields.currency ?? result.suggestedCurrency ?? undefined)?.toUpperCase()
    );
    const invoiceDate = asIsoDate(result.suggestedDate ?? undefined);

    reset(
      (current) => ({
        ...current,
        ...(category && { category }),
        ...(currency && { currency }),
        amount: fields.total_amount ?? result.suggestedAmount ?? current.amount,
        ...(invoiceDate && { invoiceDate }),
        merchantVendor: fields.vendor_name ?? result.suggestedVendor ?? current.merchantVendor,
        invoiceNumber: fields.invoice_number ?? current.invoiceNumber,
        travelRoute: fields.travel_route ?? current.travelRoute,
        travelType: fields.travel_type ?? current.travelType,
        numberOfAttendees: fields.number_of_attendees ?? current.numberOfAttendees,
        // numberOfDays is deliberately never touched here — it is always a manual entry.
      }),
      { keepDefaultValues: true }
    );
  };

  const handleFileRemoved = () => {
    uploadRef.current?.abort();
    setReceipt(null);
    setIsExtracting(false);
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
              {extractionError && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="mt-px size-3.5 shrink-0" />
                  <span>{extractionError}</span>
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
                  {errors.category && (
                    <p className="text-xs text-destructive">{errors.category.message}</p>
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
                  <Label htmlFor="invoiceDate">
                    Invoice Date <span className="text-destructive">*</span>
                  </Label>
                  <div className="relative">
                    <Calendar className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="invoiceDate"
                      type="date"
                      disabled={!fieldsEnabled}
                      aria-invalid={!!errors.invoiceDate}
                      className="pl-9"
                      {...register("invoiceDate")}
                    />
                  </div>
                  {errors.invoiceDate && (
                    <p className="text-xs text-destructive">{errors.invoiceDate.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="numberOfDays">
                    Number of Days <span className="text-destructive">*</span>
                  </Label>
                  <div className="relative">
                    <CalendarDays className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="numberOfDays"
                      type="number"
                      step="1"
                      min="1"
                      disabled={!fieldsEnabled}
                      placeholder="1"
                      aria-invalid={!!errors.numberOfDays}
                      className="pl-9"
                      {...register("numberOfDays")}
                    />
                  </div>
                  {errors.numberOfDays && (
                    <p className="text-xs text-destructive">{errors.numberOfDays.message}</p>
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
                  <Label htmlFor="travelRoute">Travel Route (optional)</Label>
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
                  <Label htmlFor="travelType">Travel Type (optional)</Label>
                  <div className="relative">
                    <Plane className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <select
                      id="travelType"
                      disabled={!fieldsEnabled}
                      aria-invalid={!!errors.travelType}
                      className={SELECT_CLASSES}
                      {...register("travelType")}
                    >
                      <option value="">Select travel type</option>
                      {TRAVEL_TYPES.map((type) => (
                        <option key={type} value={type}>
                          {type}
                        </option>
                      ))}
                    </select>
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
