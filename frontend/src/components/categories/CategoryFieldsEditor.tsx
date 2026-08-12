"use client";

import * as React from "react";
import {
  Controller,
  useFieldArray,
  type Control,
  type FieldErrors,
  type UseFormRegister,
} from "react-hook-form";
import { GripVertical, Plus, Trash2 } from "lucide-react";

import { Label } from "@/components/ui/Label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/Button";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";

import { CATEGORY_FIELD_DATA_TYPES, EMPTY_CATEGORY_FIELD, type CategoryFormValues } from "./categorySchema";

const SELECT_CLASSES =
  "flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30";

interface CategoryFieldsEditorProps {
  control: Control<CategoryFormValues>;
  register: UseFormRegister<CategoryFormValues>;
  errors: FieldErrors<CategoryFormValues>;
  idPrefix: string;
}

/**
 * The extraction-field schema editor: one accordion item per `customFields` entry, add/remove
 * rows freely. Modeled on the extracted-policy-rule accordion pattern in
 * `policy_guidelines/AddPolicyRuleWithAIDialog.tsx`, adapted for inline editing of a
 * `useFieldArray` instead of a one-shot AI-extraction review queue.
 */
export function CategoryFieldsEditor({ control, register, errors, idPrefix }: CategoryFieldsEditorProps) {
  const { fields, append, remove } = useFieldArray({ control, name: "customFields" });
  const fieldId = (index: number, name: string) => `${idPrefix}-field-${index}-${name}`;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Label className="text-sm font-semibold">Extraction fields</Label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append({ ...EMPTY_CATEGORY_FIELD })}
        >
          <Plus />
          Add field
        </Button>
      </div>

      {fields.length === 0 && (
        <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
          No custom fields yet. Add the fields extraction should pull off this category&apos;s
          invoices.
        </p>
      )}

      {fields.length > 0 && (
        <Accordion type="multiple" className="rounded-lg border px-4">
          {fields.map((field, index) => {
            const fieldErrors = errors.customFields?.[index];
            const dataType = field.dataType;

            return (
              <AccordionItem key={field.id} value={field.id}>
                <AccordionTrigger>
                  <div className="flex min-w-0 items-center gap-2 text-left">
                    <GripVertical className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate font-medium">
                      {field.label || field.name || `Field ${index + 1}`}
                    </span>
                    <span className="shrink-0 text-xs font-normal text-muted-foreground">
                      {dataType}
                    </span>
                  </div>
                </AccordionTrigger>
                <AccordionContent>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor={fieldId(index, "name")}>Field name (key)</Label>
                      <Input
                        id={fieldId(index, "name")}
                        placeholder="e.g. travel_route"
                        aria-invalid={!!fieldErrors?.name}
                        {...register(`customFields.${index}.name`)}
                      />
                      {fieldErrors?.name && (
                        <p className="text-xs text-destructive">{fieldErrors.name.message}</p>
                      )}
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor={fieldId(index, "label")}>Label</Label>
                      <Input
                        id={fieldId(index, "label")}
                        placeholder="e.g. Travel Route"
                        aria-invalid={!!fieldErrors?.label}
                        {...register(`customFields.${index}.label`)}
                      />
                      {fieldErrors?.label && (
                        <p className="text-xs text-destructive">{fieldErrors.label.message}</p>
                      )}
                    </div>

                    <div className="space-y-1.5 sm:col-span-2">
                      <Label htmlFor={fieldId(index, "description")}>
                        Description / extraction purpose
                      </Label>
                      <Input
                        id={fieldId(index, "description")}
                        placeholder="What this field is for and why it's extracted"
                        {...register(`customFields.${index}.description`)}
                      />
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor={fieldId(index, "dataType")}>Data type</Label>
                      <select
                        id={fieldId(index, "dataType")}
                        className={SELECT_CLASSES}
                        {...register(`customFields.${index}.dataType`)}
                      >
                        {CATEGORY_FIELD_DATA_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {type}
                          </option>
                        ))}
                      </select>
                    </div>

                    {dataType === "enum" && (
                      <div className="space-y-1.5">
                        <Label htmlFor={fieldId(index, "options")}>Options (comma-separated)</Label>
                        <Controller
                          control={control}
                          name={`customFields.${index}.options`}
                          render={({ field: optionsField }) => (
                            <Input
                              id={fieldId(index, "options")}
                              placeholder="Economy, Premium Economy, Business, First"
                              defaultValue={(optionsField.value ?? []).join(", ")}
                              onChange={(event) =>
                                optionsField.onChange(
                                  event.target.value
                                    .split(",")
                                    .map((option) => option.trim())
                                    .filter(Boolean)
                                )
                              }
                            />
                          )}
                        />
                        {fieldErrors?.options && (
                          <p className="text-xs text-destructive">
                            {(fieldErrors.options as { message?: string }).message}
                          </p>
                        )}
                      </div>
                    )}

                    <div className="flex items-center gap-2 sm:col-span-2">
                      <input
                        id={fieldId(index, "required")}
                        type="checkbox"
                        className="size-4 rounded border-input"
                        {...register(`customFields.${index}.required`)}
                      />
                      <Label htmlFor={fieldId(index, "required")} className="font-normal">
                        Required — extraction/review must populate this before submission
                      </Label>
                    </div>
                  </div>

                  <div className="mt-4 flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => remove(index)}
                    >
                      <Trash2 />
                      Remove field
                    </Button>
                  </div>
                </AccordionContent>
              </AccordionItem>
            );
          })}
        </Accordion>
      )}
    </div>
  );
}
