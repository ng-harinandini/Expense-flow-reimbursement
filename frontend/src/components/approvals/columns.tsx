import type { ColDef, ICellRendererParams } from "ag-grid-community";

import type { Claim } from "@/types";
import {
  ClaimTitleCell,
  ItemCountCell,
  formatCurrency,
} from "@/components/my_claims/columns";

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
          View Details
        </button>
      ),
    },
  ];
}
