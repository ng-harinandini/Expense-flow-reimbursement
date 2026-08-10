import type { ExpenseCategoryAdmin } from "@/types";

import { CATEGORY_FORM_DEFAULTS, type CategoryFormValues } from "./categorySchema";

/** Pre-fills the add/edit form from an existing category row. */
export function categoryToFormValues(category: ExpenseCategoryAdmin): CategoryFormValues {
  return {
    code: category.code,
    name: category.name,
    description: category.description ?? "",
    displayOrder: category.displayOrder,
    isActive: category.isActive,
    customFields: category.customFields.map((field) => ({
      name: field.name,
      label: field.label,
      description: field.description ?? "",
      dataType: field.dataType,
      options: field.options ?? [],
      required: field.required,
    })),
  };
}

/** Builds the row this admin screen (and the API) expects from a submitted form. */
export function formValuesToCategory(
  values: CategoryFormValues,
  existing?: ExpenseCategoryAdmin | null
): ExpenseCategoryAdmin {
  return {
    id: existing?.id,
    code: values.code.trim().toUpperCase(),
    name: values.name.trim(),
    description: values.description?.trim() || undefined,
    displayOrder: values.displayOrder,
    isActive: values.isActive ?? true,
    isCommon: existing?.isCommon ?? false,
    customFields: (values.customFields ?? []).map((field) => ({
      name: field.name.trim(),
      label: field.label.trim(),
      description: field.description?.trim() || undefined,
      dataType: field.dataType,
      options: field.dataType === "enum" ? (field.options ?? []).filter(Boolean) : undefined,
      required: field.required ?? false,
    })),
  };
}

export { CATEGORY_FORM_DEFAULTS };
