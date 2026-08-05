import type { ColDef, ICellRendererParams } from "ag-grid-community";

import type { Claim } from "@/types";
import {
  ClaimTitleCell,
  ItemCountCell,
  formatCurrency,
} from "@/components/my_claims/columns";
import { INITIAL_EMPLOYEES } from "@/data/initialClaims";

export function managerNameFor(claim: Claim) {
  return INITIAL_EMPLOYEES.find((e) => e.id === claim.employeeId)?.managerName ?? "—";
}

export function buildColumnDefs(onView: (claim: Claim) => void): ColDef<Claim>[] {
  return [
    {
      headerName: "Claim Ref",
      field: "claimNumber",
      flex: 1,
      minWidth: 140,
      cellClass: "font-medium",
    },
    {
      headerName: "Employee",
      field: "employeeName",
      flex: 1.1,
      minWidth: 150,
    },
    {
      headerName: "Manager",
      colId: "managerName",
      flex: 1.1,
      minWidth: 150,
      valueGetter: (params) => (params.data ? managerNameFor(params.data) : ""),
    },
    {
      headerName: "Claim Title",
      // Widest track: it carries two stacked lines, and the date range must not wrap.
      field: "claimTitle",
      flex: 2,
      minWidth: 230,
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
      // valueFormatter: (params) =>
      //   formatCurrency(params.value as number, params.data?.currency),
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
