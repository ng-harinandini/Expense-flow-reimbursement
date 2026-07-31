"use client";

import * as React from "react";
import type { UseFormReturn } from "react-hook-form";
import { Calendar, FileText, X } from "lucide-react";

import { Label } from "@/components/ui/Label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import type { ClaimFormValues } from "./claimSchema";

// Mirrors the max() on `purpose` in claimSchema.ts
const PURPOSE_MAX_LENGTH = 300;

interface ClaimDetailsCardProps {
  form: UseFormReturn<ClaimFormValues>;
}

export function ClaimDetailsCard({ form }: ClaimDetailsCardProps) {
  const {
    register,
    setValue,
    watch,
    formState: { errors },
  } = form;

  const fromDate = watch("fromDate");
  const purposeLength = watch("purpose")?.length ?? 0;
  const [toDateCopied, setToDateCopied] = React.useState(false);

  const handleSameAsFromDate = () => {
    if (!fromDate) return;
    setValue("toDate", fromDate, { shouldValidate: true, shouldDirty: true });
    setToDateCopied(true);
  };

  const handleClearToDate = () => {
    setValue("toDate", "", { shouldValidate: true, shouldDirty: true });
    setToDateCopied(false);
  };

  return (
    <div className="space-y-0.5">
      {/* Column gap stays generous; only the vertical rhythm is tightened. */}
      <div className="grid grid-cols-1 items-start gap-x-5 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="claimName">
            Claim Title <span className="text-destructive">*</span>
          </Label>
          <div className="relative">
            <FileText className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="claimName"
              placeholder="e.g. Business trip-mumbai"
              aria-invalid={!!errors.claimName}
              className="pl-9 font-semibold"
              {...register("claimName")}
            />
          </div>
          {errors.claimName && (
            <p className="text-xs text-destructive">{errors.claimName.message}</p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="fromDate">
            From Date <span className="text-destructive">*</span>
          </Label>
          <div className="relative">
            <Calendar className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="fromDate"
              type="date"
              aria-invalid={!!errors.fromDate}
              className="pl-9"
              {...register("fromDate")}
            />
          </div>
          {errors.fromDate && (
            <p className="text-xs text-destructive">{errors.fromDate.message}</p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="toDate">
            To Date <span className="text-destructive">*</span>
          </Label>
          <div className="relative">
            <Calendar className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="toDate"
              type="date"
              aria-invalid={!!errors.toDate}
              className="pl-9"
              {...register("toDate", { onChange: () => setToDateCopied(false) })}
            />
            {toDateCopied && (
              <button
                type="button"
                aria-label="Clear prefilled date"
                title="Clear prefilled date"
                onClick={handleClearToDate}
                className="absolute -top-1.5 -right-1.5 flex size-4 items-center justify-center rounded-full border border-background bg-secondary text-secondary-foreground shadow-sm"
              >
                <X className="size-2.5" strokeWidth={3} />
              </button>
            )}
          </div>
          <button
            type="button"
            onClick={handleSameAsFromDate}
            disabled={!fromDate}
            className="text-xs font-medium text-secondary underline decoration-dotted underline-offset-2 hover:text-secondary/80 disabled:pointer-events-none disabled:opacity-50"
          >
            Same as From Date
          </button>
          {errors.toDate && (
            <p className="text-xs text-destructive">{errors.toDate.message}</p>
          )}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="purpose">Claim Purpose</Label>
        <div className="relative">
          <Textarea
            id="purpose"
            rows={2}
            maxLength={PURPOSE_MAX_LENGTH}
            placeholder="What was the overall purpose of this claim?"
            aria-invalid={!!errors.purpose}
            className="min-h-0 resize-none pb-6"
            {...register("purpose")}
          />
          <span className="pointer-events-none absolute right-3 bottom-2 text-xs text-muted-foreground">
            {purposeLength}/{PURPOSE_MAX_LENGTH}
          </span>
        </div>
        {errors.purpose && (
          <p className="text-xs text-destructive">{errors.purpose.message}</p>
        )}
      </div>
    </div>
  );
}
