import type { ColDef, ICellRendererParams } from "ag-grid-community";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ClaimStatus, ExpenseClaim } from "@/types";

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

function riskScoreClasses(score: number) {
  if (score >= 70) return "text-destructive";
  if (score >= 35) return "text-amber-500";
  return "text-emerald-500";
}

export function RiskScoreCell({ params }: { params: ICellRendererParams<ExpenseClaim, number> }) {
  const score = params.data?.fraudScreening?.riskScore;

  if (score === undefined) {
    return <span className="text-muted-foreground">—</span>;
  }

  return <span className={cn("font-semibold", riskScoreClasses(score))}>{score}</span>;
}

export function formatCurrency(amount: number, currency: string) {
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

export function buildColumnDefs(onView: (claim: ExpenseClaim) => void): ColDef<ExpenseClaim>[] {
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
      minWidth: 150,
    },
    {
      headerName: "Vendor / Category",
      field: "merchantVendor",
      flex: 1.6,
      minWidth: 200,
      cellRenderer: (params: ICellRendererParams<ExpenseClaim>) => (
        <div className="flex flex-col py-1 leading-tight">
          <span className="font-medium text-foreground">{params.data?.merchantVendor}</span>
          <span className="text-xs text-muted-foreground">{params.data?.category}</span>
        </div>
      ),
      autoHeight: true,
    },
    {
      headerName: "Amount",
      field: "amount",
      flex: 1,
      minWidth: 110,
      valueFormatter: (params) =>
        params.data ? formatCurrency(params.data.amount, params.data.currency) : "",
    },
    {
      headerName: "Date",
      field: "expenseDate",
      flex: 1,
      minWidth: 120,
      valueFormatter: (params) => (params.value ? formatDate(params.value as string) : ""),
    },
    {
      headerName: "Risk Score",
      field: "fraudScreening.riskScore",
      flex: 1,
      minWidth: 110,
      cellRenderer: (params: ICellRendererParams<ExpenseClaim, number>) => (
        <RiskScoreCell params={params} />
      ),
    },
    {
      headerName: "Status",
      field: "status",
      flex: 1.2,
      minWidth: 150,
      cellRenderer: (params: ICellRendererParams<ExpenseClaim>) =>
        params.data ? <StatusBadge status={params.data.status} /> : null,
    },
    {
      headerName: "Action",
      field: "id",
      flex: 0.8,
      minWidth: 100,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<ExpenseClaim>) => (
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
