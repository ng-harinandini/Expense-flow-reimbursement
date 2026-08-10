import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiRequest } from "./client";
import type { CategoryFieldDefinition, ExpenseCategoryAdmin } from "@/types";

export interface CategoryFieldDefinitionApiShape {
  name: string;
  label: string;
  description?: string | null;
  data_type: CategoryFieldDefinition["dataType"];
  options?: string[] | null;
  required?: boolean;
}

export interface CategoryApiShape {
  id?: string;
  code: string;
  name: string;
  description?: string | null;
  displayOrder?: number;
  isActive?: boolean;
  isCommon?: boolean;
  customFields?: CategoryFieldDefinitionApiShape[];
}

export const CATEGORIES_QUERY_KEY = ["categories"] as const;

function mapField(raw: CategoryFieldDefinitionApiShape): CategoryFieldDefinition {
  return {
    name: raw.name,
    label: raw.label,
    description: raw.description ?? undefined,
    dataType: raw.data_type,
    options: raw.options ?? undefined,
    required: Boolean(raw.required),
  };
}

function fieldToApiShape(field: CategoryFieldDefinition): CategoryFieldDefinitionApiShape {
  return {
    name: field.name,
    label: field.label,
    description: field.description || undefined,
    data_type: field.dataType,
    options: field.dataType === "enum" ? field.options ?? [] : undefined,
    required: field.required,
  };
}

function mapCategory(raw: CategoryApiShape): ExpenseCategoryAdmin {
  return {
    id: raw.id,
    code: raw.code,
    name: raw.name,
    description: raw.description ?? undefined,
    displayOrder: raw.displayOrder ?? 100,
    isActive: raw.isActive ?? true,
    isCommon: raw.isCommon ?? false,
    customFields: (raw.customFields ?? []).map(mapField),
  };
}

export function categoryToApiShape(category: ExpenseCategoryAdmin): CategoryApiShape {
  return {
    code: category.code,
    name: category.name,
    description: category.description || undefined,
    displayOrder: category.displayOrder,
    isActive: category.isActive,
    customFields: category.customFields.map(fieldToApiShape),
  };
}

export function getCategories(): Promise<ExpenseCategoryAdmin[]> {
  return apiRequest<CategoryApiShape[]>("/categories").then((list) => list.map(mapCategory));
}

export function useCategoriesQuery() {
  return useQuery({
    queryKey: CATEGORIES_QUERY_KEY,
    queryFn: getCategories,
  });
}

export function createCategory(category: ExpenseCategoryAdmin): Promise<ExpenseCategoryAdmin> {
  return apiRequest<CategoryApiShape>("/categories", {
    method: "POST",
    body: JSON.stringify(categoryToApiShape(category)),
    headers: { "Content-Type": "application/json" },
  }).then(mapCategory);
}

export function useCreateCategoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createCategory,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CATEGORIES_QUERY_KEY }),
  });
}

export function updateCategory(category: ExpenseCategoryAdmin): Promise<ExpenseCategoryAdmin> {
  return apiRequest<CategoryApiShape>(`/categories/${encodeURIComponent(category.code)}`, {
    method: "PUT",
    body: JSON.stringify(categoryToApiShape(category)),
    headers: { "Content-Type": "application/json" },
  }).then(mapCategory);
}

export function useUpdateCategoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateCategory,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CATEGORIES_QUERY_KEY }),
  });
}

export function deleteCategory(code: string): Promise<ExpenseCategoryAdmin> {
  return apiRequest<CategoryApiShape>(`/categories/${encodeURIComponent(code)}`, {
    method: "DELETE",
  }).then(mapCategory);
}

export function useDeleteCategoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteCategory,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CATEGORIES_QUERY_KEY }),
  });
}
