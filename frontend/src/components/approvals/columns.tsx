import type { ColDef, ICellRendererParams } from "ag-grid-community";

import type { Claim } from "@/types";
import { formatDate } from "@/components/my_claims/columns";

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
      flex: 1.6,
      minWidth: 200,
      cellClass: "font-medium",
    },
    {
      headerName: "From - To Date",
      colId: "dateRange",
      flex: 1.3,
      minWidth: 190,
      valueGetter: (params) =>
        params.data
          ? `${formatDate(params.data.fromDate)} – ${formatDate(params.data.toDate)}`
          : "",
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
