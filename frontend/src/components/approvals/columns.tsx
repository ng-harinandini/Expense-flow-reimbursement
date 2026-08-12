import type { ColDef, ICellRendererParams } from "ag-grid-community";

import type { Claim } from "@/types";
import {
  ClaimTitleCell,
  ItemCountCell,
  StatusBadge,
} from "@/components/my_claims/columns";

/**
 * Whether the *manager* review step is still the one waiting on this claim.
 *
 * The table lists every claim filed by the manager's team regardless of status (so the manager can
 * see where things ended up), but once a claim leaves `Manager_Review` — escalated to finance,
 * approved, rejected, or flagged for fraud — it is no longer this reviewer's turn. Mirrors
 * `isFinanceActionable` in `finance_approvals/columns.tsx`.
 */
export function isManagerActionable(claim: Claim): boolean {
  return claim.status === "Manager_Review";
}

export function buildColumnDefs(onView: (claim: Claim) => void): ColDef<Claim>[] {
  return [
    {
      headerName: "Claim Ref",
      field: "claimNumber",
      flex: 1.1,
      minWidth: 140,
      cellClass: "font-medium",
    },
    {
      headerName: "Employee",
      field: "employeeName",
      flex: 1.2,
      minWidth: 160,
    },
    {
      headerName: "Claim Title",
      field: "claimTitle",
      flex: 2.2,
      minWidth: 220,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <ClaimTitleCell claim={params.data} /> : null,
    },
    {
      headerName: "Items",
      colId: "itemCount",
      flex: 0.7,
      minWidth: 90,
      valueGetter: (params) => (params.data ? params.data.items.length : 0),
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <ItemCountCell claim={params.data} /> : null,
    },
    {
      headerName: "Total Amount",
      field: "totalAmount",
      flex: 1,
      minWidth: 130,
      cellClass: "font-semibold",
    },
    {
      headerName: "Claim Status",
      field: "status",
      flex: 1.1,
      minWidth: 150,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <StatusBadge status={params.data.status} /> : null,
    },
    {
      headerName: "Action",
      colId: "action",
      flex: 0.9,
      minWidth: 130,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<Claim>) => (
        <button
          type="button"
          className="text-sm font-medium text-secondary hover:underline"
          onClick={() => params.data && onView(params.data)}
        >
          {params.data && isManagerActionable(params.data) ? "Take Action" : "View Details"}
        </button>
      ),
    },
  ];
}
