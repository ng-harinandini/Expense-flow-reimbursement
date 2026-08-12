"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Label";
import { Input } from "@/components/ui/input";
import { getErrorMessage } from "@/lib/apiError";
import type { ExpenseCategoryAdmin } from "@/types";

import { CategoryFieldsEditor } from "./CategoryFieldsEditor";
import { categoryToFormValues } from "./helpers";
import { categorySchema, CATEGORY_FORM_DEFAULTS, type CategoryFormValues } from "./categorySchema";

interface AddEditCategoryDialogProps {
  /** Non-null puts the dialog in edit mode, pre-filled from this category. */
  category?: ExpenseCategoryAdmin | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (values: CategoryFormValues) => void | Promise<void>;
}

export function AddEditCategoryDialog({
  category,
  open,
  onOpenChange,
  onSave,
}: AddEditCategoryDialogProps) {
  const isEditing = Boolean(category);
  const [submitError, setSubmitError] = React.useState<string | null>(null);

  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CategoryFormValues>({
    resolver: yupResolver(categorySchema),
    defaultValues: CATEGORY_FORM_DEFAULTS,
  });

  React.useEffect(() => {
    if (!open) return;
    setSubmitError(null);
    reset(category ? categoryToFormValues(category) : CATEGORY_FORM_DEFAULTS);
  }, [open, category, reset]);

  const onSubmit = async (values: CategoryFormValues) => {
    setSubmitError(null);
    try {
      await onSave(values);
      onOpenChange(false);
    } catch (error) {
      setSubmitError(getErrorMessage(error, "Something went wrong. Try again."));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader className="pr-8">
          <DialogTitle>{isEditing ? "Edit category" : "Add expense category"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? "Update the category and its extraction fields, then save to publish the change."
              : "Define the category and the fields extraction should pull from its invoices."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="category-code">Code</Label>
              <Input
                id="category-code"
                placeholder="e.g. AIR_TRAVEL"
                disabled={isEditing}
                aria-invalid={!!errors.code}
                {...register("code")}
              />
              {errors.code && <p className="text-xs text-destructive">{errors.code.message}</p>}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="category-name">Name</Label>
              <Input
                id="category-name"
                placeholder="e.g. Air Travel"
                aria-invalid={!!errors.name}
                {...register("name")}
              />
              {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="category-description">Description</Label>
              <Input
                id="category-description"
                placeholder="Short description shown to reviewers"
                {...register("description")}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="category-displayOrder">Display order</Label>
              <Input
                id="category-displayOrder"
                type="number"
                aria-invalid={!!errors.displayOrder}
                {...register("displayOrder")}
              />
              {errors.displayOrder && (
                <p className="text-xs text-destructive">{errors.displayOrder.message}</p>
              )}
            </div>

            <div className="flex items-center gap-2 pt-6">
              <input
                id="category-isActive"
                type="checkbox"
                className="size-4 rounded border-input"
                {...register("isActive")}
              />
              <Label htmlFor="category-isActive" className="font-normal">
                Active (selectable on new claims)
              </Label>
            </div>
          </div>

          <Separator />

          <CategoryFieldsEditor
            control={control}
            register={register}
            errors={errors}
            idPrefix="category"
          />

          {submitError && (
            <p className="text-sm text-destructive" role="alert">
              {submitError}
            </p>
          )}

          <Separator />

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={isSubmitting}>
              {isEditing ? "Save changes" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
