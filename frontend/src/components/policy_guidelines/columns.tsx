import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { Pencil, Trash2 } from "lucide-react";

import type { AdminPolicyRule } from "@/types";

import {
  formatAutoApproveLimit,
  formatEffectiveFrom,
  formatMaxAmount,
  formatReceiptThreshold,
} from "./helpers";

export function buildColumnDefs(
  onEdit: (rule: AdminPolicyRule) => void,
  onDelete: (rule: AdminPolicyRule) => void
): ColDef<AdminPolicyRule>[] {
  return [
    {
      headerName: "ID",
      field: "id",
      flex: 0.5,
      minWidth: 70,
      cellClass: "font-medium",
    },
    {
      headerName: "Category",
      field: "category",
      flex: 1.3,
      minWidth: 170,
      cellClass: "font-medium",
    },
    {
      headerName: "Grade Applicable",
      field: "gradeApplicable",
      flex: 1.1,
      minWidth: 150,
    },
    {
      headerName: "Max Amount",
      colId: "maxAmount",
      flex: 1,
      minWidth: 130,
      valueGetter: (params) => (params.data ? formatMaxAmount(params.data) : ""),
    },
    {
      headerName: "Auto Approve Limit",
      colId: "autoApproveLimit",
      flex: 1,
      minWidth: 140,
      valueGetter: (params) =>
        params.data ? formatAutoApproveLimit(params.data.autoApproveLimit) : "",
    },
    {
      headerName: "Requires Receipt Above",
      colId: "requiresReceiptAbove",
      flex: 1,
      minWidth: 160,
      valueGetter: (params) =>
        params.data ? formatReceiptThreshold(params.data.requiresReceiptAbove) : "",
    },
    {
      headerName: "Effective From",
      colId: "effectiveFrom",
      flex: 1,
      minWidth: 130,
      valueGetter: (params) =>
        params.data ? formatEffectiveFrom(params.data.effectiveFrom) : "",
    },
    {
      headerName: "Actions",
      colId: "actions",
      flex: 0.8,
      minWidth: 100,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<AdminPolicyRule>) => (
        <div className="flex h-full items-center gap-1">
          <button
            type="button"
            aria-label={`Edit ${params.data?.category ?? "policy rule"}`}
            title="Edit rule"
            className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-secondary"
            onClick={() => params.data && onEdit(params.data)}
          >
            <Pencil className="size-4" />
          </button>
          <button
            type="button"
            aria-label={`Delete ${params.data?.category ?? "policy rule"}`}
            title="Delete rule"
            className="cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive"
            onClick={() => params.data && onDelete(params.data)}
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      ),
    },
  ];
}
