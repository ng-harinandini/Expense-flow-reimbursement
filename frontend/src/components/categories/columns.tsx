import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { Lock, Pencil, Trash2 } from "lucide-react";

import type { ExpenseCategoryAdmin } from "@/types";

export function buildColumnDefs(
  onEdit: (category: ExpenseCategoryAdmin) => void,
  onDelete: (category: ExpenseCategoryAdmin) => void
): ColDef<ExpenseCategoryAdmin>[] {
  return [
    {
      headerName: "Code",
      field: "code",
      flex: 1,
      minWidth: 150,
      cellClass: "font-medium",
      cellRenderer: (params: ICellRendererParams<ExpenseCategoryAdmin>) => (
        <div className="flex h-full items-center gap-1.5">
          {params.data?.isCommon && (
            <Lock className="size-3.5 shrink-0 text-muted-foreground" aria-label="Common fields" />
          )}
          <span>{params.value}</span>
        </div>
      ),
    },
    {
      headerName: "Name",
      field: "name",
      flex: 1.3,
      minWidth: 200,
    },
    {
      headerName: "Description",
      field: "description",
      flex: 1.6,
      minWidth: 220,
      valueGetter: (params) => params.data?.description ?? "",
    },
    {
      headerName: "Fields",
      colId: "fieldCount",
      flex: 0.6,
      minWidth: 90,
      valueGetter: (params) => params.data?.customFields.length ?? 0,
    },
    {
      headerName: "Active",
      colId: "isActive",
      flex: 0.6,
      minWidth: 90,
      valueGetter: (params) => (params.data?.isActive ? "Yes" : "No"),
    },
    {
      headerName: "Actions",
      colId: "actions",
      flex: 0.8,
      minWidth: 100,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<ExpenseCategoryAdmin>) => (
        <div className="flex h-full items-center gap-1">
          <button
            type="button"
            aria-label={`Edit ${params.data?.name ?? "category"}`}
            title="Edit category"
            className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-secondary"
            onClick={() => params.data && onEdit(params.data)}
          >
            <Pencil className="size-4" />
          </button>
          <button
            type="button"
            aria-label={`Deactivate ${params.data?.name ?? "category"}`}
            title={params.data?.isCommon ? "The common-fields category can't be deactivated" : "Deactivate category"}
            disabled={params.data?.isCommon}
            className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
            onClick={() => params.data && !params.data.isCommon && onDelete(params.data)}
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      ),
    },
  ];
}
