import type { ColDef, ICellRendererParams } from "ag-grid-community";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Claim, ClaimStatus } from "@/types";

import { STATUS_BADGE_CLASSES, STATUS_LABELS } from "./status";

export function StatusBadge({ status }: { status: ClaimStatus }) {
  return (
    <Badge className={cn("font-medium", STATUS_BADGE_CLASSES[status])}>
      {STATUS_LABELS[status]}
    </Badge>
  );
}

export function formatCurrency(amount: number, currency: string = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency || "USD",
  }).format(amount);
}

export function formatDate(dateStr: string) {
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

export function getClaimTotal(claim: Claim) {
  return claim.items.reduce((sum, item) => sum + item.amount, 0);
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
      flex: 1.1,
      minWidth: 140,
    },
    {
      headerName: "Claim Title",
      field: "claimTitle",
      flex: 1.4,
      minWidth: 180,
      cellClass: "font-medium",
    },
    {
      headerName: "From - To Date",
      colId: "dateRange",
      flex: 1.3,
      minWidth: 190,
      valueGetter: (params) =>
        params.data ? `${formatDate(params.data.fromDate)} – ${formatDate(params.data.toDate)}` : "",
    },
    {
      headerName: "Total Amount",
      colId: "totalAmount",
      flex: 1,
      minWidth: 120,
      valueGetter: (params) => (params.data ? getClaimTotal(params.data) : 0),
      valueFormatter: (params) => formatCurrency(params.value as number),
    },
    {
      headerName: "Status",
      field: "status",
      flex: 1.1,
      minWidth: 150,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <StatusBadge status={params.data.status} /> : null,
    },
    {
      headerName: "Action",
      colId: "action",
      flex: 0.7,
      minWidth: 100,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<Claim>) => (
        <button
          type="button"
          className="text-sm font-medium text-secondary hover:underline"
          onClick={() => params.data && onView(params.data)}
        >
          View
        </button>
      ),
    },
  ];
}
