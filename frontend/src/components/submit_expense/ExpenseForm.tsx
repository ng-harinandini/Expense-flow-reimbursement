"use client";

import * as React from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";
import {
  ArrowRight,
  Calendar,
  CreditCard,
  DollarSign,
  FileText,
  Plus,
  Percent,
  Sparkles,
  Store,
  Tag,
  Trash2,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/Label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/Button";
import { ReceiptDropzone } from "@/components/submit_expense/ReceiptDropzone";
import { claimSchema, CLAIM_FORM_DEFAULTS, type ClaimFormValues } from "@/components/submit_expense/claimSchema";
import { EXPENSE_CATEGORIES } from "@/components/submit_expense/helpers";

export function ExpenseForm() {
  const [receipt, setReceipt] = React.useState<File | null>(null);
  const [isExtracting, setIsExtracting] = React.useState(false);
  const [isExtracted, setIsExtracted] = React.useState(false);

  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<ClaimFormValues>({
    resolver: yupResolver(claimSchema),
    defaultValues: CLAIM_FORM_DEFAULTS,
  });

  const {
    fields: lineItemFields,
    append: appendLineItem,
    remove: removeLineItem,
  } = useFieldArray({
    control,
    name: "lineItems",
  });

  const fieldsEnabled = Boolean(receipt) && !isExtracting;

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
    reset(CLAIM_FORM_DEFAULTS);
  };

  const onSubmit = (values: ClaimFormValues) => {
    console.log("Submit claim", { ...values, receipt });
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <Card>
        <CardHeader className="flex-row items-start justify-between">
          <div className="w-full">
            <div className="flex items-center gap-2.5">
              <FileText className="size-7 shrink-0 text-primary" />
              <CardTitle className="text-lg font-bold">Submit expense</CardTitle>
              {isExtracted && (
                <Badge variant="secondary" className="shrink-0">
                  <span className="size-1.5 rounded-full bg-secondary-foreground" />
                  AI-extracted
                </Badge>
              )}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Upload a receipt to auto-fill your claim details, then review and submit.
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="space-y-6">
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
                      className="flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent py-1 pr-3 pl-9 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30"
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

              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label>Additional expenses</Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!fieldsEnabled}
                    onClick={() => appendLineItem({ name: "", amount: undefined as unknown as number })}
                  >
                    <Plus />
                    Add expense
                  </Button>
                </div>

                {lineItemFields.length > 0 && (
                  <div className="space-y-3">
                    {lineItemFields.map((field, index) => (
                      <div key={field.id} className="flex items-start gap-2">
                        <div className="flex-1 space-y-1.5">
                          <Input
                            disabled={!fieldsEnabled}
                            placeholder="Expense name"
                            aria-invalid={!!errors.lineItems?.[index]?.name}
                            {...register(`lineItems.${index}.name` as const)}
                          />
                          {errors.lineItems?.[index]?.name && (
                            <p className="text-xs text-destructive">
                              {errors.lineItems[index]?.name?.message}
                            </p>
                          )}
                        </div>
                        <div className="w-32 space-y-1.5 sm:w-40">
                          <div className="relative">
                            <DollarSign className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                              type="number"
                              step="0.01"
                              disabled={!fieldsEnabled}
                              placeholder="0.00"
                              aria-invalid={!!errors.lineItems?.[index]?.amount}
                              className="pl-9"
                              {...register(`lineItems.${index}.amount` as const)}
                            />
                          </div>
                          {errors.lineItems?.[index]?.amount && (
                            <p className="text-xs text-destructive">
                              {errors.lineItems[index]?.amount?.message}
                            </p>
                          )}
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={!fieldsEnabled}
                          aria-label="Remove expense"
                          onClick={() => removeLineItem(index)}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-3 self-start lg:sticky lg:top-6">
              {receipt && isExtracted && (
                <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-primary">
                  <Sparkles className="size-3.5 shrink-0" />
                  Receipt scanned — review the auto-filled details before submitting.
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
          </div>

          <Separator className="my-6" />

          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">Total reimbursement</p>
              <p className="text-2xl font-bold tracking-tight text-foreground">
                $0.00
              </p>
            </div>
            <Button type="submit" disabled={!fieldsEnabled || isSubmitting}>
              Submit claim
              <ArrowRight />
            </Button>
          </div>
        </CardContent>
      </Card>
    </form>
  );
}
