"use client";

import * as React from "react";
import type { ColDef } from "ag-grid-community";
import { FolderKanban, Plus } from "lucide-react";

import { DataGrid } from "@/components/shared/DataGrid";
import { Button } from "@/components/ui/Button";
import {
  useCategoriesQuery,
  useCreateCategoryMutation,
  useDeleteCategoryMutation,
  useUpdateCategoryMutation,
} from "@/api/categories";
import type { ExpenseCategoryAdmin } from "@/types";

import { buildColumnDefs } from "./columns";
import { formValuesToCategory } from "./helpers";
import { AddEditCategoryDialog } from "./AddEditCategoryDialog";
import { DeleteCategoryDialog } from "./DeleteCategoryDialog";
import type { CategoryFormValues } from "./categorySchema";

function Categories() {
  const { data: categories = [], isLoading } = useCategoriesQuery();
  const createCategory = useCreateCategoryMutation();
  const updateCategory = useUpdateCategoryMutation();
  const deleteCategory = useDeleteCategoryMutation();

  const [isFormOpen, setIsFormOpen] = React.useState(false);
  const [editingCategory, setEditingCategory] = React.useState<ExpenseCategoryAdmin | null>(null);
  const [deletingCategory, setDeletingCategory] = React.useState<ExpenseCategoryAdmin | null>(null);
  const [isDeleteOpen, setIsDeleteOpen] = React.useState(false);

  const handleAdd = React.useCallback(() => {
    setEditingCategory(null);
    setIsFormOpen(true);
  }, []);

  const handleEdit = React.useCallback((category: ExpenseCategoryAdmin) => {
    setEditingCategory(category);
    setIsFormOpen(true);
  }, []);

  const handleDelete = React.useCallback((category: ExpenseCategoryAdmin) => {
    setDeletingCategory(category);
    setIsDeleteOpen(true);
  }, []);

  const handleSave = React.useCallback(
    async (values: CategoryFormValues) => {
      const category = formValuesToCategory(values, editingCategory);
      if (editingCategory) {
        await updateCategory.mutateAsync(category);
      } else {
        await createCategory.mutateAsync(category);
      }
    },
    [editingCategory, createCategory, updateCategory]
  );

  const handleConfirmDelete = React.useCallback(
    async (category: ExpenseCategoryAdmin) => {
      await deleteCategory.mutateAsync(category.code);
    },
    [deleteCategory]
  );

  const columnDefs = React.useMemo<ColDef<ExpenseCategoryAdmin>[]>(
    () => buildColumnDefs(handleEdit, handleDelete),
    [handleEdit, handleDelete]
  );

  const defaultColDef = React.useMemo<ColDef>(
    () => ({
      resizable: true,
      sortable: true,
      filter: false,
    }),
    []
  );

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <FolderKanban className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">Invoice Categories</h2>
            <p className="text-sm text-muted-foreground">
              Manage expense categories and the fields extraction pulls from each one&apos;s
              invoices
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row">
          <Button onClick={handleAdd}>
            <Plus />
            Add category
          </Button>
        </div>
      </div>

      <div className="h-[560px] px-6 pb-6">
        <DataGrid<ExpenseCategoryAdmin>
          rowData={categories}
          loading={isLoading}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          getRowId={(params) => params.data.code}
          overlayNoRowsTemplate="No categories yet."
          domLayout="normal"
          rowHeight={56}
          headerHeight={44}
          suppressCellFocus
        />
      </div>

      <AddEditCategoryDialog
        category={editingCategory}
        open={isFormOpen}
        onOpenChange={setIsFormOpen}
        onSave={handleSave}
      />

      <DeleteCategoryDialog
        category={deletingCategory}
        open={isDeleteOpen}
        onOpenChange={setIsDeleteOpen}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}

export default Categories;
