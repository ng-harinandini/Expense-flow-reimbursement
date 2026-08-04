import type { ColDef, ICellRendererParams } from "ag-grid-community";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Claim, ClaimStatus } from "@/types";

export const STATUS_LABELS: Record<ClaimStatus, string> = {
  Draft: "Draft",
  Submitted: "Submitted",
  Processing_AI: "Processing AI",
  Auto_Approved: "Auto Approved",
  Manager_Review: "Manager Review",
  Finance_Review: "Finance Review",
  Approved: "Approved",
  Rejected: "Rejected",
  Disbursed: "Disbursed",
  Flagged_Fraud: "Flagged Fraud",
};

const STATUS_BADGE_CLASSES: Record<ClaimStatus, string> = {
  Draft: "bg-muted text-muted-foreground border-transparent",
  Submitted: "bg-blue-500/15 text-blue-500 border-transparent",
  Processing_AI: "bg-blue-500/15 text-blue-500 border-transparent",
  Auto_Approved: "bg-emerald-500/15 text-emerald-500 border-transparent",
  Manager_Review: "bg-amber-500/15 text-amber-500 border-transparent",
  Finance_Review: "bg-amber-500/15 text-amber-500 border-transparent",
  Approved: "bg-emerald-500/15 text-emerald-500 border-transparent",
  Rejected: "bg-destructive/15 text-destructive border-transparent",
  Disbursed: "bg-secondary/15 text-secondary border-transparent",
  Flagged_Fraud: "bg-destructive/15 text-destructive border-transparent",
};

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
