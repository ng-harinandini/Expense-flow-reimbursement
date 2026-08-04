import type { ColDef } from "ag-grid-community";

import type { AdminPolicyRule } from "@/types";

import {
  formatAutoApproveLimit,
  formatEffectiveFrom,
  formatMaxAmount,
  formatReceiptThreshold,
} from "./helpers";

export function buildColumnDefs(): ColDef<AdminPolicyRule>[] {
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
  ];
}
