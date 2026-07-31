"use client";

import * as React from "react";
import type { FieldErrors, UseFormRegister } from "react-hook-form";
import { Calendar, DollarSign, Percent, Ruler, ShieldCheck, Tag } from "lucide-react";

import { Label } from "@/components/ui/Label";
import { Input } from "@/components/ui/input";
import { EXPENSE_CATEGORIES } from "@/components/submit_expense/helpers";

import type { PolicyRuleFormValues } from "./policyRuleSchema";

const SELECT_CLASSES =
  "flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent py-1 pr-3 pl-9 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30";

interface PolicyRuleFormFieldsProps {
  register: UseFormRegister<PolicyRuleFormValues>;
  errors: FieldErrors<PolicyRuleFormValues>;
  /** Keeps element ids unique when several of these forms render at once (accordion). */
  idPrefix: string;
}

export function PolicyRuleFormFields({ register, errors, idPrefix }: PolicyRuleFormFieldsProps) {
  const fieldId = (name: string) => `${idPrefix}-${name}`;

  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
      <div className="space-y-1.5">
        <Label htmlFor={fieldId("category")}>Category</Label>
        <div className="relative">
          <Tag className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <select
            id={fieldId("category")}
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
        <Label htmlFor={fieldId("gradeApplicable")}>Grade applicable</Label>
        <Input
          id={fieldId("gradeApplicable")}
          placeholder="e.g. All, L1-L3, L4+, Manager+"
          aria-invalid={!!errors.gradeApplicable}
          {...register("gradeApplicable")}
        />
        {errors.gradeApplicable && (
          <p className="text-xs text-destructive">{errors.gradeApplicable.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={fieldId("maxAmount")}>Max amount</Label>
        <div className="relative">
          <DollarSign className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id={fieldId("maxAmount")}
            type="number"
            step="0.01"
            placeholder="0.00"
            aria-invalid={!!errors.maxAmount}
            className="pl-9"
            {...register("maxAmount")}
          />
        </div>
        {errors.maxAmount && (
          <p className="text-xs text-destructive">{errors.maxAmount.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={fieldId("maxAmountUnit")}>Unit</Label>
        <div className="relative">
          <Ruler className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id={fieldId("maxAmountUnit")}
            placeholder="e.g. day, trip, night, event"
            aria-invalid={!!errors.maxAmountUnit}
            className="pl-9"
            {...register("maxAmountUnit")}
          />
        </div>
        {errors.maxAmountUnit && (
          <p className="text-xs text-destructive">{errors.maxAmountUnit.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={fieldId("autoApproveLimit")}>Auto approve limit</Label>
        <div className="relative">
          <ShieldCheck className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id={fieldId("autoApproveLimit")}
            type="number"
            step="0.01"
            placeholder="Leave blank = always manual review"
            aria-invalid={!!errors.autoApproveLimit}
            className="pl-9"
            {...register("autoApproveLimit")}
          />
        </div>
        {errors.autoApproveLimit && (
          <p className="text-xs text-destructive">{errors.autoApproveLimit.message}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={fieldId("requiresReceiptAbove")}>Requires receipt above</Label>
        <div className="relative">
          <Percent className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id={fieldId("requiresReceiptAbove")}
            type="number"
            step="0.01"
            placeholder="0.00"
            aria-invalid={!!errors.requiresReceiptAbove}
            className="pl-9"
            {...register("requiresReceiptAbove")}
          />
        </div>
        {errors.requiresReceiptAbove && (
          <p className="text-xs text-destructive">{errors.requiresReceiptAbove.message}</p>
        )}
      </div>

      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor={fieldId("effectiveFrom")}>Effective from</Label>
        <div className="relative">
          <Calendar className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id={fieldId("effectiveFrom")}
            type="date"
            aria-invalid={!!errors.effectiveFrom}
            className="pl-9"
            {...register("effectiveFrom")}
          />
        </div>
        {errors.effectiveFrom && (
          <p className="text-xs text-destructive">{errors.effectiveFrom.message}</p>
        )}
      </div>
    </div>
  );
}
